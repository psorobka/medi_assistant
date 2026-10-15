import { after, afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";

const window = new Window();
Object.assign(globalThis, { window, document: window.document, HTMLElement: window.HTMLElement, customElements: window.customElements });
await import("../../custom_components/medi_assistant/www/medi-assistant-card.js");
afterEach(() => document.body.replaceChildren());
after(() => window.happyDOM.abort());
const tick = () => new Promise((resolve) => setImmediate(resolve));

const appointment = {
  cancellation_id: "random-synthetic-handle", date: "2099-02-03T10:00:00",
  doctor: "Example Doctor", clinic: "Example Clinic", specialty: "Example Specialty",
  can_cancel: true, late_cancellation: false,
};
const account = { account_id: "synthetic-account", account_name: "Example Account", success: true, appointments: [appointment] };

async function create(accounts = [account], cancel = async () => ({ success: true, status: "cancelled" })) {
  const calls = [];
  let listing = accounts;
  const card = document.createElement("medi-assistant-appointments");
  card.setConfig({});
  card.hass = { language: "pl", connection: {}, callWS: async (message) => {
    calls.push(message);
    if (message.service === "cancel_appointment") return { response: await cancel(message) };
    return { response: { accounts: structuredClone(listing) } };
  }};
  document.body.append(card);
  await tick();
  return { card, calls, setListing: (value) => { listing = value; } };
}
function button(card, text) {
  const found = [...card.shadowRoot.querySelectorAll("button")].find((node) => node.textContent === text);
  assert.ok(found, `Missing button ${text}`);
  return found;
}

test("shows all accounts, empty accounts and failures without cancelling", async () => {
  const { card, calls } = await create([account, { account_name: "Empty", success: true, appointments: [] }, { account_name: "Offline", success: false, message: "Reconnect account", appointments: [] }]);
  assert.match(card.shadowRoot.textContent, /Example Doctor/);
  assert.match(card.shadowRoot.textContent, /Nie masz zaplanowanych wizyt/);
  assert.match(card.shadowRoot.textContent, /Reconnect account/);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].service_data, {});
});

test("confirmation can be dismissed without sending a cancellation", async () => {
  const { card, calls } = await create();
  button(card, "Odwołaj").click();
  assert.match(card.shadowRoot.querySelector(".confirmation").textContent, /Example Clinic/);
  button(card, "Zostaw wizytę").click();
  assert.equal(card.shadowRoot.querySelector(".confirmation"), null);
  assert.equal(calls.length, 1);
});

test("confirmed cancellation sends the exact handle once and reloads the list", async () => {
  let complete;
  const { card, calls, setListing } = await create([account], () => new Promise((resolve) => { complete = resolve; }));
  button(card, "Odwołaj").click();
  button(card, "Odwołaj wizytę").click();
  button(card, "Odwoływanie…").click();
  assert.equal(calls.filter((call) => call.service === "cancel_appointment").length, 1);
  assert.deepEqual(calls[1].service_data, { cancellation_id: appointment.cancellation_id, confirm_late_cancellation: false });
  setListing([{ ...account, appointments: [] }]);
  complete({ success: true, status: "cancelled" });
  await tick();
  assert.match(card.shadowRoot.textContent, /Wizyta została odwołana/);
  assert.equal(card.shadowRoot.querySelectorAll(".visit").length, 0);
  assert.equal(calls.length, 3);
});

test("late cancellation needs an explicit checkbox", async () => {
  const { card, calls } = await create([{ ...account, appointments: [{ ...appointment, late_cancellation: true }] }]);
  button(card, "Odwołaj").click();
  assert.equal(button(card, "Odwołaj wizytę").disabled, true);
  button(card, "Odwołaj wizytę").click();
  assert.equal(calls.length, 1);
  const checkbox = card.shadowRoot.querySelector("input");
  checkbox.checked = true;
  checkbox.dispatchEvent(new window.Event("change"));
  button(card, "Odwołaj wizytę").click();
  await tick();
  assert.equal(calls[1].service_data.confirm_late_cancellation, true);
});

test("a newly late visit requests confirmation without silently retrying", async () => {
  const { card, calls } = await create([account], async () => ({ success: false, status: "confirmation_required", message: "Confirm late cancellation" }));
  button(card, "Odwołaj").click();
  button(card, "Odwołaj wizytę").click();
  await tick();
  assert.ok(card.shadowRoot.querySelector("input"));
  assert.equal(button(card, "Odwołaj wizytę").disabled, true);
  assert.equal(calls.filter((call) => call.service === "cancel_appointment").length, 1);
});

test("lost cancellation response is not retried and reloads appointments", async () => {
  const { card, calls } = await create([account], async () => { throw new Error("disconnected"); });
  button(card, "Odwołaj").click();
  button(card, "Odwołaj wizytę").click();
  await tick();
  assert.equal(calls.filter((call) => call.service === "cancel_appointment").length, 1);
  assert.equal(calls.filter((call) => call.service === "list_booked_appointments").length, 2);
  assert.match(card.shadowRoot.textContent, /Nie udało się potwierdzić wyniku/);
});

test("patient-provided values are rendered as text", async () => {
  const injected = '<img src=x onerror="window.injected=true">';
  const { card } = await create([{ ...account, account_name: injected, appointments: [{ ...appointment, doctor: injected }] }]);
  assert.equal(card.shadowRoot.querySelector("img"), null);
  assert.match(card.shadowRoot.textContent, /<img/);
});

test("failed refresh preserves data and disables cancellation until recovery", async () => {
  const { card } = await create();
  card._hass.callWS = async () => { throw new Error("offline"); };
  await card.refresh();
  assert.match(card.shadowRoot.textContent, /Example Doctor/);
  assert.equal(button(card, "Odwołaj").disabled, true);
  assert.ok(card.shadowRoot.querySelector('[role="alert"]'));
});
