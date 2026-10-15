const TEXT = {
  pl: {
    title: "Moje wizyty", refresh: "Odśwież", loading: "Pobieranie wizyt…",
    empty: "Nie masz zaplanowanych wizyt.", noAccounts: "Dodaj konto Medi Assistant, aby zobaczyć wizyty.",
    cancel: "Odwołaj", unavailable: "Odwołanie online niedostępne", late: "Późne odwołanie",
    question: "Odwołać tę wizytę?", back: "Zostaw wizytę", confirm: "Odwołaj wizytę",
    lateInfo: "Medicover oznaczył tę wizytę jako późne odwołanie. Sprawdź warunki odwołania w Medicover.",
    agree: "Potwierdzam późne odwołanie", working: "Odwoływanie…", error: "Nie udało się pobrać wizyt. Spróbuj odświeżyć listę.",
    unknown: "Nie udało się potwierdzić wyniku. Sprawdź listę wizyt przed kolejną próbą.",
    cancelled: "Wizyta została odwołana.", count: "Zaplanowane wizyty", retryLate: "Wizyta wymaga potwierdzenia późnego odwołania.",
  },
  en: {
    title: "My appointments", refresh: "Refresh", loading: "Loading appointments…",
    empty: "You have no planned appointments.", noAccounts: "Add a Medi Assistant account to see appointments.",
    cancel: "Cancel", unavailable: "Online cancellation unavailable", late: "Late cancellation",
    question: "Cancel this appointment?", back: "Keep appointment", confirm: "Cancel appointment",
    lateInfo: "Medicover marked this as a late cancellation. Review the cancellation terms in Medicover.",
    agree: "I confirm late cancellation", working: "Cancelling…", error: "Unable to load appointments. Try refreshing the list.",
    unknown: "Unable to confirm the outcome. Check your appointments before trying again.",
    cancelled: "Appointment cancelled.", count: "Planned appointments", retryLate: "This appointment requires late cancellation confirmation.",
  },
};

// No patient values are interpolated into HTML, saved to storage or logged.
const STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; color: var(--primary-text-color); }
  header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 22px 20px 16px; }
  h2 { margin: 0; font-size: 22px; font-weight: 500; letter-spacing: -.3px; }
  .subtitle { margin-top: 5px; color: var(--secondary-text-color); font-size: 13px; }
  button { font: inherit; cursor: pointer; border-radius: 10px; padding: 9px 13px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-color); min-height: 42px; }
  button:hover:not(:disabled) { background: var(--secondary-background-color); }
  button:focus-visible, input:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 3px; }
  button:disabled { opacity: .5; cursor: default; }
  .body { padding: 0 20px 20px; }
  .account { margin: 8px 0 0; }
  h3 { margin: 0; padding: 10px 0 6px; font-size: 13px; font-weight: 600; color: var(--secondary-text-color); }
  .visit { padding: 16px 0; border-bottom: 1px solid var(--divider-color); }
  .visit:last-child { border-bottom: 0; }
  .date { font-weight: 600; font-size: 15px; margin-bottom: 9px; }
  .specialty { font-size: 16px; font-weight: 500; margin-bottom: 5px; }
  .detail { color: var(--secondary-text-color); font-size: 14px; line-height: 1.55; }
  .actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 13px; flex-wrap: wrap; }
  .danger { color: var(--error-color, #db4437); }
  .primary { background: var(--error-color, #db4437); color: white; border-color: transparent; }
  .primary:hover:not(:disabled) { background: var(--error-color, #db4437); filter: brightness(.92); }
  .badge { color: var(--warning-color, #b77400); font-size: 12px; font-weight: 500; }
  .empty { padding: 20px 0 12px; color: var(--secondary-text-color); line-height: 1.6; }
  .notice { padding: 12px 14px; margin: 0 0 12px; border-radius: 10px; background: var(--secondary-background-color); font-size: 14px; line-height: 1.5; }
  .success { color: var(--success-color, #22844b); }
  .confirmation { border: 1px solid var(--divider-color); border-radius: 12px; padding: 16px; margin: 6px 0 16px; }
  h4 { margin: 0 0 14px; font-size: 17px; }
  .confirmation p { font-size: 14px; line-height: 1.5; }
  label { display: flex; align-items: center; gap: 9px; padding: 8px 0; font-size: 14px; }
  input { width: 20px; height: 20px; accent-color: var(--primary-color); }
  @media (max-width: 400px) { header { padding: 18px 16px 14px; } .body { padding: 0 16px 16px; } }
`;

export class MediAssistantAppointments extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._accounts = [];
    this._loaded = false;
    this._loading = false;
    this._busy = false;
    this._visibility = () => {
      if (!document.hidden && Date.now() - (this._updated || 0) > 60000) this.refresh();
    };
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    const changed = this._hass?.connection !== hass.connection;
    this._hass = hass;
    if ((!this._loaded || changed) && this.isConnected) this.refresh();
  }

  connectedCallback() {
    this._timer = window.setInterval(() => {
      if (!document.hidden) this.refresh();
    }, 5 * 60 * 1000);
    document.addEventListener("visibilitychange", this._visibility);
    this._render();
    if (this._hass) this.refresh();
  }

  disconnectedCallback() {
    window.clearInterval(this._timer);
    document.removeEventListener("visibilitychange", this._visibility);
  }

  static getStubConfig() { return {}; }
  getCardSize() { return Math.max(3, 2 + this._accounts.reduce((sum, a) => sum + a.appointments.length * 3, 0)); }
  getGridOptions() { return { columns: 12, min_columns: 6 }; }
  get _text() { return TEXT[this._hass?.language?.startsWith("en") ? "en" : "pl"]; }

  async _service(service, data = {}) {
    const response = await this._hass.callWS({ type: "call_service", domain: "medi_assistant", service, service_data: data, return_response: true });
    return response.response;
  }

  async refresh(force = false) {
    if (!this._hass || this._loading || this._busy || (this._selected && !force)) return;
    this._loading = true;
    this._render();
    try {
      const listing = await this._service("list_booked_appointments");
      if (!Array.isArray(listing?.accounts)) throw new Error("Invalid appointment list");
      this._accounts = listing.accounts;
      this._loadError = false;
      this._loaded = true;
      this._updated = Date.now();
    } catch {
      this._loadError = true;
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _select(account, appointment) {
    if (this._busy) return;
    this._selected = { account, appointment };
    this._lateConfirmed = false;
    this._notice = null;
    this._render();
    this.shadowRoot.querySelector(".confirmation")?.focus();
  }

  async _cancelSelected() {
    const selected = this._selected;
    if (!selected || this._busy || (selected.appointment.late_cancellation && !this._lateConfirmed)) return;
    this._busy = true;
    this._render();
    let outcome;
    try {
      outcome = await this._service("cancel_appointment", {
        cancellation_id: selected.appointment.cancellation_id,
        confirm_late_cancellation: this._lateConfirmed === true,
      });
      if (!outcome || typeof outcome.success !== "boolean") throw new Error("Invalid cancellation result");
    } catch {
      outcome = { success: false, status: "unknown" };
    }
    this._busy = false;
    this._notice = { success: outcome.success, message: outcome.success ? this._text.cancelled : (outcome.message || this._text.unknown) };
    if (outcome.status === "confirmation_required") {
      selected.appointment = { ...selected.appointment, late_cancellation: true };
      this._lateConfirmed = false;
      this._render();
      return;
    }
    this._selected = null;
    // Re-read even an ambiguous result. Never retry the cancellation itself.
    await this.refresh(true);
  }

  _node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  _button(text, className, action, disabled = false) {
    const button = this._node("button", className, text);
    button.type = "button";
    button.disabled = disabled;
    button.addEventListener("click", action);
    return button;
  }

  _date(value) {
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return value;
    return new Intl.DateTimeFormat(this._hass?.language || "pl", { dateStyle: "full", timeStyle: "short" }).format(date);
  }

  _details(appointment, parent) {
    parent.append(this._node("div", "date", this._date(appointment.date)));
    parent.append(this._node("div", "specialty", (appointment.specialty || "").trim()));
    parent.append(this._node("div", "detail", appointment.doctor));
    parent.append(this._node("div", "detail", appointment.clinic));
  }

  _render() {
    if (!this.shadowRoot) return;
    const t = this._text;
    const style = this._node("style", "", STYLE);
    const card = this._node("ha-card");
    const header = this._node("header");
    const heading = this._node("div");
    heading.append(this._node("h2", "", this._config?.title || t.title));
    if (this._loaded) heading.append(this._node("div", "subtitle", `${t.count}: ${this._accounts.reduce((sum, a) => sum + a.appointments.length, 0)}`));
    header.append(heading, this._button(t.refresh, "", () => this.refresh(), this._loading || this._busy || !!this._selected));
    const body = this._node("div", "body");
    if (this._notice) {
      const notice = this._node("div", `notice ${this._notice.success ? "success" : "danger"}`, this._notice.message);
      notice.setAttribute("role", "status");
      body.append(notice);
    }
    if (this._loadError) {
      const error = this._node("div", "notice danger", t.error);
      error.setAttribute("role", "alert");
      body.append(error);
    }
    if (this._selected) {
      const { account, appointment } = this._selected;
      const panel = this._node("section", "confirmation");
      panel.tabIndex = -1;
      panel.setAttribute("aria-label", t.question);
      panel.append(this._node("h4", "", t.question), this._node("p", "detail", account.account_name));
      this._details(appointment, panel);
      if (appointment.late_cancellation) {
        panel.append(this._node("p", "", t.lateInfo));
        const label = this._node("label");
        const check = this._node("input");
        check.type = "checkbox";
        check.checked = this._lateConfirmed;
        check.disabled = this._busy;
        check.addEventListener("change", () => {
          this._lateConfirmed = check.checked;
          this.shadowRoot.querySelector("[data-confirm]").disabled = this._busy || !check.checked;
        });
        label.append(check, this._node("span", "", t.agree));
        panel.append(label);
      }
      const actions = this._node("div", "actions");
      const confirm = this._button(this._busy ? t.working : t.confirm, "primary", () => this._cancelSelected(), this._busy || (appointment.late_cancellation && !this._lateConfirmed));
      confirm.dataset.confirm = "true";
      actions.append(this._button(t.back, "", () => { this._selected = null; this._render(); }, this._busy), confirm);
      panel.append(actions);
      body.append(panel);
    }
    if (!this._loaded && !this._loadError) body.append(this._node("div", "empty", t.loading));
    if (this._loaded && !this._accounts.length) body.append(this._node("div", "empty", t.noAccounts));
    for (const account of this._accounts) {
      const section = this._node("section", "account");
      section.append(this._node("h3", "", account.account_name));
      if (!account.success) {
        section.append(this._node("div", "notice danger", account.message));
      } else if (!account.appointments.length) {
        section.append(this._node("div", "empty", t.empty));
      }
      for (const appointment of account.appointments) {
        const row = this._node("article", "visit");
        this._details(appointment, row);
        const actions = this._node("div", "actions");
        if (appointment.late_cancellation) actions.append(this._node("span", "badge", t.late));
        actions.append(appointment.can_cancel
          ? this._button(t.cancel, "danger", () => this._select(account, appointment), this._busy || !!this._selected || this._loadError)
          : this._node("span", "detail", t.unavailable));
        row.append(actions);
        section.append(row);
      }
      body.append(section);
    }
    card.append(header, body);
    this.shadowRoot.replaceChildren(style, card);
  }
}

if (!customElements.get("medi-assistant-appointments")) customElements.define("medi-assistant-appointments", MediAssistantAppointments);
window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "medi-assistant-appointments")) {
  window.customCards.push({ type: "medi-assistant-appointments", name: "Medi Assistant — Moje wizyty", description: "Zarezerwowane wizyty wszystkich kont i ich odwoływanie.", preview: true });
}
