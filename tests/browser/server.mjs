import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";

const root = new URL("../../", import.meta.url);
const sessions = new Map();
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const person = (id, name) => ({ account_id: id, account_name: name, success: true, appointments: [] });
const visit = (id, doctor) => ({ cancellation_id: id, date: "2099-02-03T10:00:00", doctor, clinic: "Placówka przykładowa", specialty: "Konsultacja przykładowa", can_cancel: true, late_cancellation: false });
const offer = (id, doctor, date = "2099-02-04T11:00:00") => ({ booking_id: id, date, doctor, clinic: "Placówka przykładowa", specialty: "Konsultacja przykładowa" });

function seed(scenario) {
  const accounts = [person("account-a", "Konto Alfa"), person("account-b", "Konto Beta")];
  accounts[0].appointments.push(visit("cancel-a", "Lekarz Wizyty Alfa"));
  accounts[1].appointments.push(visit("cancel-b", "Lekarz Wizyty Beta"));
  const searches = accounts.map(({ account_id, account_name }) => ({ account_id, account_name, searches: [] }));
  searches[0].searches.push({ search_id: "search-a", name: "Szukajka Alfa", available: true, count: 2, truncated: false, appointments: [offer("offer-a1", "Lekarz Oferty Pierwszy"), offer("offer-a2", "Lekarz Oferty Drugi")] });
  searches[1].searches.push({ search_id: "search-b", name: "Szukajka Beta", available: true, count: 1, truncated: false, appointments: [offer("offer-b", "Lekarz Oferty Beta")] });
  if (scenario === "late") accounts[0].appointments[0].late_cancellation = true;
  if (scenario === "empty") {
    accounts.forEach((account) => { account.appointments = []; });
    searches.forEach((account) => { account.searches = []; });
  }
  if (scenario === "partial") {
    accounts[1] = { ...accounts[1], success: false, status: "auth_required", message: "Konto Beta wymaga zalogowania.", appointments: [] };
    searches[0].searches.push({ search_id: "empty", name: "Pusta szukajka", available: true, count: 0, appointments: [] });
    searches[1].searches[0] = { search_id: "failed", name: "Niedostępna szukajka", available: false, count: null, message: "Dane szukajki są niedostępne.", appointments: [] };
  }
  if (scenario === "truncated") {
    searches[0].searches[0].appointments = Array.from({ length: 20 }, (_, index) => offer(`many-${index}`, `Lekarz Wyniku ${index + 1}`));
    searches[0].searches[0].count = 30;
    searches[0].searches[0].truncated = true;
  }
  if (scenario === "html") {
    accounts[0].account_name = '<img src="x" onerror="window.patientHtmlExecuted=true">';
    searches[0].searches[0].appointments[0].doctor = '<img src="x" onerror="window.patientHtmlExecuted=true">';
  }
  return { scenario, accounts, searches, requests: [], bookWrites: 0, cancelWrites: 0, readFailure: null };
}

function json(response, data, status = 200) {
  response.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" });
  response.end(JSON.stringify(data));
}

async function body(request) {
  let text = "";
  for await (const chunk of request) {
    text += chunk;
    if (text.length > 64000) throw new Error("Test request too large");
  }
  return text ? JSON.parse(text) : {};
}

async function service(state, message, response) {
  const { service: name, service_data: data = {} } = message;
  state.requests.push({ service: name, data });
  if (name === "list_booked_appointments") {
    return state.readFailure === "booked" ? json(response, {}, 503) : json(response, { accounts: state.accounts });
  }
  if (name === "list_booking_offers") {
    return state.readFailure === "searches" ? json(response, {}, 503) : json(response, { accounts: state.searches });
  }
  if (name === "delete_search") {
    await delay(250);
    if (state.scenario === "delete-failed") return json(response, { success: false, message: "Nie udało się usunąć szukajki." });
    const account = state.searches.find((item) => item.account_id === data.account_id);
    if (!account) return json(response, {}, 400);
    account.searches = account.searches.filter((search) => search.search_id !== data.search_id);
    if (state.scenario === "delete-response-lost") return json(response, {}, 503);
    return json(response, { success: true, deleted: true });
  }
  if (name === "book_appointment") {
    await delay(250);
    if (state.scenario === "expired") return json(response, { success: false, status: "expired", message: "Wybrany termin wygasł." });
    for (const account of state.searches) {
      for (const search of account.searches) {
        const selected = search.appointments.find((slot) => slot.booking_id === data.booking_id);
        if (!selected) continue;
        const planned = state.accounts.find((item) => item.account_id === account.account_id);
        planned.appointments.push({ ...visit(`cancel-${selected.booking_id}`, selected.doctor), date: selected.date });
        search.appointments = search.appointments.filter((slot) => slot !== selected);
        search.count -= 1;
        state.bookWrites += 1;
        if (state.scenario === "book-response-lost") return json(response, {}, 503);
        return json(response, { success: true, status: "booked", message: "Wizyta została zarezerwowana." });
      }
    }
    return json(response, { success: false, status: "expired", message: "Wybrany termin wygasł." });
  }
  if (name === "cancel_appointment") {
    await delay(250);
    for (const account of state.accounts) {
      const selected = account.appointments.find((slot) => slot.cancellation_id === data.cancellation_id);
      if (!selected) continue;
      const late = selected.late_cancellation || state.scenario === "late-changed";
      if (late && data.confirm_late_cancellation !== true) return json(response, { success: false, status: "confirmation_required", message: "Potwierdź późne odwołanie." });
      account.appointments = account.appointments.filter((slot) => slot !== selected);
      state.cancelWrites += 1;
      if (state.scenario === "cancel-response-lost") return json(response, {}, 503);
      return json(response, { success: true, status: "cancelled", message: "Wizyta została odwołana." });
    }
    return json(response, { success: false, status: "expired", message: "Identyfikator wygasł." });
  }
  return json(response, { error: "Unknown synthetic service" }, 400);
}

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url, "http://127.0.0.1");
    if (url.pathname === "/health") return json(response, { fixture: "medi-assistant-browser-tests" });
    if (url.pathname === "/__test/session" && request.method === "POST") {
      const { scenario = "normal" } = await body(request);
      const id = randomUUID();
      sessions.set(id, seed(scenario));
      return json(response, { id });
    }
    if (url.pathname.startsWith("/__test/")) {
      const state = sessions.get(url.searchParams.get("session"));
      if (!state) return json(response, { error: "Unknown synthetic session" }, 404);
      if (url.pathname === "/__test/service" && request.method === "POST") return await service(state, await body(request), response);
      if (url.pathname === "/__test/configure" && request.method === "POST") {
        const { readFailure } = await body(request);
        if (![null, "booked", "searches"].includes(readFailure)) return json(response, {}, 400);
        state.readFailure = readFailure;
        return json(response, { ok: true });
      }
      if (url.pathname === "/__test/snapshot") return json(response, { requests: state.requests, bookWrites: state.bookWrites, cancelWrites: state.cancelWrites, planned: state.accounts.map((account) => ({ account: account.account_id, visits: account.appointments.map((slot) => slot.cancellation_id) })) });
    }
    if (url.pathname === "/medi-assistant-card.js") {
      response.writeHead(200, { "content-type": "text/javascript", "cache-control": "no-store" });
      return response.end(await readFile(new URL("custom_components/medi_assistant/www/medi-assistant-card.js", root)));
    }
    if (url.pathname === "/") {
      response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      return response.end(await readFile(new URL("tests/browser/fixture.html", root)));
    }
    if (url.pathname === "/config/integrations/integration/medi_assistant") {
      response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      return response.end("<!doctype html><title>Test destination</title><p>Native HA configuration route reached. The real configuration form is outside this card fixture.</p>");
    }
    response.writeHead(404).end();
  } catch {
    json(response, { error: "Synthetic test server error" }, 500);
  }
});

const port = Number(process.env.MEDI_BROWSER_TEST_PORT || 4178);
server.listen(port, "127.0.0.1", () => console.log(`Card fixture listening on 127.0.0.1:${port}`));
for (const signal of ["SIGTERM", "SIGINT"]) process.on(signal, () => server.close(() => process.exit(0)));
