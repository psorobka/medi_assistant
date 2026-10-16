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

async function create(accounts = [account], cancel = async () => ({ success: true, status: "cancelled" }), searchAccounts = [], book = async () => ({ success: true, status: "booked" }), deleteSearch = async () => ({ success: true })) {
  const calls = [];
  let listing = accounts;
  let searchListing = searchAccounts;
  const card = document.createElement("medi-assistant-appointments");
  card.setConfig({});
  card.hass = { language: "pl", connection: {}, user: { is_admin: true }, callWS: async (message) => {
    calls.push(message);
    if (message.service === "delete_search") return { response: await deleteSearch(message) };
    if (message.service === "book_appointment") return { response: await book(message) };
    if (message.service === "list_booking_offers") return { response: { accounts: structuredClone(searchListing) } };
    if (message.service === "cancel_appointment") return { response: await cancel(message) };
    return { response: { accounts: structuredClone(listing) } };
  }};
  document.body.append(card);
  await tick();
  return { card, calls, setListing: (value) => { listing = value; }, setSearchListing: (value) => { searchListing = value; } };
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
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0].service_data, {});
});

test("confirmation can be dismissed without sending a cancellation", async () => {
  const { card, calls } = await create();
  button(card, "Odwołaj").click();
  assert.match(card.shadowRoot.querySelector(".confirmation").textContent, /Example Clinic/);
  button(card, "Zostaw wizytę").click();
  assert.equal(card.shadowRoot.querySelector(".confirmation"), null);
  assert.equal(calls.length, 2);
});

test("confirmed cancellation sends the exact handle once and reloads the list", async () => {
  let complete;
  const { card, calls, setListing } = await create([account], () => new Promise((resolve) => { complete = resolve; }));
  button(card, "Odwołaj").click();
  button(card, "Odwołaj wizytę").click();
  button(card, "Odwoływanie…").click();
  assert.equal(calls.filter((call) => call.service === "cancel_appointment").length, 1);
  assert.deepEqual(calls.find((call) => call.service === "cancel_appointment").service_data, { cancellation_id: appointment.cancellation_id, confirm_late_cancellation: false });
  setListing([{ ...account, appointments: [] }]);
  complete({ success: true, status: "cancelled" });
  await tick();
  assert.match(card.shadowRoot.textContent, /Wizyta została odwołana/);
  assert.equal(card.shadowRoot.querySelectorAll(".visit").length, 0);
  assert.equal(calls.length, 5);
});

test("late cancellation needs an explicit checkbox", async () => {
  const { card, calls } = await create([{ ...account, appointments: [{ ...appointment, late_cancellation: true }] }]);
  button(card, "Odwołaj").click();
  assert.equal(button(card, "Odwołaj wizytę").disabled, true);
  button(card, "Odwołaj wizytę").click();
  assert.equal(calls.length, 2);
  const checkbox = card.shadowRoot.querySelector("input");
  checkbox.checked = true;
  checkbox.dispatchEvent(new window.Event("change"));
  button(card, "Odwołaj wizytę").click();
  await tick();
  assert.equal(calls.find((call) => call.service === "cancel_appointment").service_data.confirm_late_cancellation, true);
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


const offered = { date: "2099-02-03T11:00:00", doctor: "Offered Doctor", clinic: "Offered Clinic", specialty: "Offered Specialty", booking_id: "synthetic-exact-offer" };
const searchAccount = { account_id: "synthetic-account", account_name: "Example Account", searches: [{ search_id: "search-a", name: "My search", available: true, count: 1, truncated: false, appointments: [offered] }] };

test("searches show offers and booking buttons without submitting bookings", async () => {
  const { card, calls } = await create([], undefined, [searchAccount]);
  assert.match(card.shadowRoot.textContent, /My search/);
  assert.match(card.shadowRoot.textContent, /Offered Doctor/);
  assert.ok(button(card, "Zarezerwuj"));
  assert.equal(calls.filter((call) => call.service === "book_appointment").length, 0);
  button(card, "Zarezerwuj").click();
  assert.match(card.shadowRoot.querySelector(".confirmation").textContent, /Offered Clinic/);
  button(card, "Wróć do terminów").click();
  assert.equal(calls.filter((call) => call.service === "book_appointment").length, 0);
});

test("booking sends only the selected handle once and refreshes both lists", async () => {
  let complete;
  const { card, calls, setListing, setSearchListing } = await create([], undefined, [searchAccount], () => new Promise((resolve) => { complete = resolve; }));
  button(card, "Zarezerwuj").click();
  button(card, "Zarezerwuj wizytę").click();
  button(card, "Rezerwowanie…").click();
  assert.equal(calls.filter((call) => call.service === "book_appointment").length, 1);
  assert.deepEqual(calls.find((call) => call.service === "book_appointment").service_data, { booking_id: offered.booking_id });
  setListing([account]);
  setSearchListing([{ ...searchAccount, searches: [{ ...searchAccount.searches[0], count: 0, appointments: [] }] }]);
  complete({ success: true, status: "booked" });
  await tick();
  assert.match(card.shadowRoot.textContent, /Wizyta została zarezerwowana/);
  assert.match(card.shadowRoot.textContent, /Example Doctor/);
  assert.match(card.shadowRoot.textContent, /Brak dostępnych terminów/);
  assert.equal(calls.filter((call) => call.service === "list_booking_offers").length, 2);
  assert.equal(calls.filter((call) => call.service === "list_booked_appointments").length, 2);
  assert.equal(calls.filter((call) => call.service === "list_booking_offers").at(-1).service_data.refresh, true);
});

test("expired booking is reported without substituting another appointment", async () => {
  const { card, calls } = await create([], undefined, [searchAccount], async () => ({ success: false, status: "expired", message: "Offer expired" }));
  button(card, "Zarezerwuj").click();
  button(card, "Zarezerwuj wizytę").click();
  await tick();
  assert.match(card.shadowRoot.textContent, /Offer expired/);
  assert.equal(calls.filter((call) => call.service === "book_appointment").length, 1);
});

test("ambiguous booking response is not retried", async () => {
  const { card, calls } = await create([], undefined, [searchAccount], async () => { throw new Error("lost response"); });
  button(card, "Zarezerwuj").click();
  button(card, "Zarezerwuj wizytę").click();
  await tick();
  assert.match(card.shadowRoot.textContent, /Nie udało się potwierdzić wyniku/);
  assert.equal(calls.filter((call) => call.service === "book_appointment").length, 1);
});

test("empty, failed and truncated searches have distinct messages", async () => {
  const searches = [
    { search_id: "empty", name: "Empty search", available: true, count: 0, appointments: [] },
    { search_id: "failed", name: "Failed search", available: false, message: "Search unavailable", appointments: [] },
    { search_id: "many", name: "Many results", available: true, count: 50, truncated: true, appointments: [offered] },
  ];
  const { card } = await create([], undefined, [{ ...searchAccount, searches }]);
  assert.match(card.shadowRoot.textContent, /Brak dostępnych terminów/);
  assert.match(card.shadowRoot.textContent, /Search unavailable/);
  assert.match(card.shadowRoot.textContent, /Pokazano 1 z 50/);
});

test("booked-list failure does not hide available search results", async () => {
  const { card } = await create([], undefined, [searchAccount]);
  card._hass.callWS = async (message) => {
    if (message.service === "list_booked_appointments") throw new Error("unavailable");
    return { response: { accounts: [searchAccount] } };
  };
  await card.refresh();
  assert.match(card.shadowRoot.textContent, /Nie udało się pobrać wizyt/);
  assert.equal(button(card, "Zarezerwuj").disabled, false);
});

test("manual refresh requests current search results", async () => {
  const { card, calls } = await create([], undefined, [searchAccount]);
  button(card, "Odśwież").click();
  await tick();
  assert.equal(calls.filter((call) => call.service === "list_booking_offers").at(-1).service_data.refresh, true);
});


test("add search link is present even without searches and uses native HA configuration", async () => {
  const { card, calls } = await create([]);
  const link = card.shadowRoot.querySelector('a[aria-label="Dodaj szukajkę"]');
  assert.ok(link);
  assert.equal(link.getAttribute("href"), "/config/integrations/integration/medi_assistant");
  assert.equal(calls.filter((call) => call.service === "book_appointment").length, 0);
});

test("configuration link is hidden for non-admin users", async () => {
  const { card } = await create([]);
  card._hass.user = { is_admin: false };
  card._render();
  assert.equal(card.shadowRoot.querySelector(".add-search"), null);
});


test("search deletion shows account and name and can be dismissed", async () => {
  const { card, calls } = await create([], undefined, [searchAccount]);
  card.shadowRoot.querySelector(".delete-search").click();
  const panel = card.shadowRoot.querySelector(".confirmation");
  assert.match(panel.textContent, /Example Account/);
  assert.match(panel.textContent, /My search/);
  assert.equal(panel, card.shadowRoot.activeElement);
  button(card, "Zostaw szukajkę").click();
  assert.equal(card.shadowRoot.querySelector(".confirmation"), null);
  assert.equal(calls.filter((call) => call.service === "delete_search").length, 0);
});

test("confirmed search deletion sends exact account/search once and refreshes", async () => {
  let complete;
  const { card, calls, setSearchListing } = await create([account], undefined, [searchAccount], undefined, () => new Promise((resolve) => { complete = resolve; }));
  card.shadowRoot.querySelector(".delete-search").click();
  button(card, "Usuń szukajkę").click();
  button(card, "Usuwanie…").click();
  assert.deepEqual(calls.find((call) => call.service === "delete_search").service_data, { account_id: "synthetic-account", search_id: "search-a" });
  setSearchListing([{ ...searchAccount, searches: [] }]);
  complete({ success: true, deleted: true });
  await tick();
  assert.equal(calls.filter((call) => call.service === "delete_search").length, 1);
  assert.equal(card.shadowRoot.querySelectorAll("details.search").length, 0);
  assert.match(card.shadowRoot.textContent, /Szukajka została usunięta/);
  assert.match(card.shadowRoot.textContent, /Example Doctor/);
  assert.equal(calls.filter((call) => call.service === "cancel_appointment").length, 0);
});

test("lost deletion response is not repeated and the list is re-read", async () => {
  const { card, calls } = await create([], undefined, [searchAccount], undefined, async () => { throw new Error("lost response"); });
  card.shadowRoot.querySelector(".delete-search").click();
  button(card, "Usuń szukajkę").click();
  await tick();
  assert.match(card.shadowRoot.textContent, /Nie udało się potwierdzić usunięcia/);
  assert.equal(calls.filter((call) => call.service === "delete_search").length, 1);
  assert.equal(calls.filter((call) => call.service === "list_booking_offers").length, 2);
});

test("non-admin cannot select or invoke search deletion", async () => {
  const { card, calls } = await create([], undefined, [searchAccount]);
  card._hass.user.is_admin = false;
  card._render();
  assert.equal(card.shadowRoot.querySelector(".delete-search"), null);
  card._selectDelete(searchAccount, searchAccount.searches[0]);
  assert.equal(card._selected, undefined);
  assert.equal(calls.filter((call) => call.service === "delete_search").length, 0);
});
