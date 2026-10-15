"""Provision only the loopback-bound local HA; never read or copy stored sessions."""

import json
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8123"
CLIENT = "http://localhost:8123/"


def request(path, data=None, token=None, form=False):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        body = (urlencode(data) if form else json.dumps(data)).encode()
        headers["Content-Type"] = (
            "application/x-www-form-urlencoded" if form else "application/json"
        )
    with urlopen(Request(BASE + path, data=body, headers=headers), timeout=30) as response:
        content = response.read()
        return json.loads(content) if content else None


def main():
    for _ in range(45):
        try:
            steps = request("/api/onboarding")
            break
        except HTTPError as error:
            if error.code == 404:
                print("Lokalny HA jest już przygotowany.")
                return
            raise RuntimeError(f"HA zwrócił HTTP {error.code}") from None
        except (URLError, ConnectionError):
            time.sleep(1)
    else:
        raise RuntimeError("HA nie odpowiada po restarcie.")

    done = {step["step"] for step in steps if step["done"]}
    if "user" not in done:
        created = request(
            "/api/onboarding/users",
            {
                "name": "Tester lokalny",
                "username": "local_test",
                "password": secrets.token_urlsafe(48),
                "client_id": CLIENT,
                "language": "pl",
            },
        )
        code = created["auth_code"]
    else:
        # Existing accounts remain untouched. Trusted auth is limited by config
        # to loopback and the Docker bridge, with a loopback-only published port.
        flow = request(
            "/auth/login_flow",
            {
                "client_id": CLIENT,
                "handler": ["trusted_networks", None],
                "redirect_uri": CLIENT,
            },
        )
        if flow["type"] != "create_entry":
            users = next(
                field["options"] for field in flow["data_schema"] if field["name"] == "user"
            )
            if len(users) != 1:
                print("Jest kilku użytkowników. Dokończ pierwszą konfigurację w HA.")
                return
            user_id = next(iter(users)) if isinstance(users, dict) else users[0][0]
            flow = request("/auth/login_flow/" + flow["flow_id"], {"user": user_id})
        code = flow["result"]

    tokens = request(
        "/auth/token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT,
        },
        form=True,
    )
    token = tokens["access_token"]
    for step in ("core_config", "analytics", "integration"):
        if step not in done:
            payload = {"client_id": CLIENT, "redirect_uri": CLIENT} if step == "integration" else {}
            request("/api/onboarding/" + step, payload, token)
    # This temporary provisioning session is not retained.
    request("/auth/token", {"action": "revoke", "token": tokens["refresh_token"]}, form=True)
    print("Lokalny HA przygotowany. Wejście przez localhost pomija logowanie.")


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        raise SystemExit(f"Przygotowanie lokalnego HA: HTTP {error.code}") from None
