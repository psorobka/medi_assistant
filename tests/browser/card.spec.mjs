import { test as base, expect } from "@playwright/test";

const test = base.extend({
  fixture: async ({ page, context, request, baseURL }, use) => {
    const externalRequests = [];
    await context.route("**/*", async (route) => {
      if (new URL(route.request().url()).origin === baseURL) return route.continue();
      externalRequests.push(route.request().url());
      return route.abort("blockedbyclient");
    });
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    let session;
    await use({
      async open(scenario = "normal", admin = true) {
        const response = await request.post("/__test/session", { data: { scenario } });
        expect(response.ok()).toBeTruthy();
        session = (await response.json()).id;
        await page.goto(`/?session=${session}&admin=${admin ? "1" : "0"}`);
        await expect(page.getByRole("button", { name: "Odśwież", exact: true })).toBeEnabled();
        await expect(page.getByText("Pobieranie wizyt…", { exact: true })).toHaveCount(0);
      },
      async snapshot() {
        return (await request.get(`/__test/snapshot?session=${session}`)).json();
      },
      async failReads(readFailure) {
        const response = await request.post(`/__test/configure?session=${session}`, { data: { readFailure } });
        expect(response.ok()).toBeTruthy();
      },
    });
    expect(externalRequests, "The browser must never contact external services").toEqual([]);
    expect(pageErrors).toEqual([]);
  },
});

const bookButtons = (page) => page.getByRole("button", { name: "Zarezerwuj", exact: true });
const bookPanel = (page) => page.getByRole("region", { name: "Zarezerwować tę wizytę?", exact: true });
const cancelPanel = (page) => page.getByRole("region", { name: "Odwołać tę wizytę?", exact: true });
const offerRow = (page, doctor) => page.locator("article.offer").filter({ hasText: doctor });
const plannedRow = (page, doctor) => page.locator("article.visit:not(.offer)").filter({ hasText: doctor });
const attempts = (snapshot, name) => snapshot.requests.filter((call) => call.service === name);

test("shows both accounts, their planned visits and named searches without writing", async ({ page, fixture }) => {
  await fixture.open();
  await expect(page.getByRole("heading", { name: "Konto Alfa", exact: true })).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "Konto Beta", exact: true })).toHaveCount(2);
  await expect(plannedRow(page, "Lekarz Wizyty Alfa")).toBeVisible();
  await expect(plannedRow(page, "Lekarz Wizyty Beta")).toBeVisible();
  await expect(page.locator("summary").filter({ hasText: "Szukajka Alfa" })).toBeVisible();
  await expect(bookButtons(page)).toHaveCount(3);
  const state = await fixture.snapshot();
  expect(state.bookWrites).toBe(0);
  expect(state.cancelWrites).toBe(0);
  expect(attempts(state, "book_appointment")).toHaveLength(0);
});

test("booking confirmation matches the chosen account and can be dismissed", async ({ page, fixture }) => {
  await fixture.open();
  await offerRow(page, "Lekarz Oferty Beta").getByRole("button", { name: "Zarezerwuj", exact: true }).click();
  await expect(bookPanel(page)).toContainText("Konto Beta");
  await expect(bookPanel(page)).toContainText("Szukajka Beta");
  await expect(bookPanel(page)).toContainText("Lekarz Oferty Beta");
  await expect(bookPanel(page)).toBeFocused();
  await page.getByRole("button", { name: "Wróć do terminów", exact: true }).click();
  await expect(bookPanel(page)).toHaveCount(0);
  expect(attempts(await fixture.snapshot(), "book_appointment")).toHaveLength(0);
});

test("double click books only the selected offer and refreshes both lists", async ({ page, fixture }) => {
  await fixture.open();
  await offerRow(page, "Lekarz Oferty Drugi").getByRole("button", { name: "Zarezerwuj", exact: true }).click();
  await page.getByRole("button", { name: "Zarezerwuj wizytę", exact: true }).dblclick({ delay: 15 });
  await expect(page.getByRole("status")).toHaveText("Wizyta została zarezerwowana.");
  await expect(plannedRow(page, "Lekarz Oferty Drugi")).toBeVisible();
  await expect(offerRow(page, "Lekarz Oferty Drugi")).toHaveCount(0);
  const state = await fixture.snapshot();
  expect(state.bookWrites).toBe(1);
  expect(attempts(state, "book_appointment")).toEqual([{ service: "book_appointment", data: { booking_id: "offer-a2" } }]);
  expect(state.planned.find((account) => account.account === "account-a").visits).toContain("cancel-offer-a2");
  expect(state.planned.find((account) => account.account === "account-b").visits).not.toContain("cancel-offer-a2");
  expect(attempts(state, "list_booked_appointments").length).toBeGreaterThanOrEqual(2);
  expect(attempts(state, "list_booking_offers").at(-1).data.refresh).toBe(true);
});

test("an expired booking is reported without selecting another offer", async ({ page, fixture }) => {
  await fixture.open("expired");
  await offerRow(page, "Lekarz Oferty Drugi").getByRole("button", { name: "Zarezerwuj", exact: true }).click();
  await page.getByRole("button", { name: "Zarezerwuj wizytę", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("Wybrany termin wygasł.");
  await expect(bookPanel(page)).toHaveCount(0);
  const state = await fixture.snapshot();
  expect(state.bookWrites).toBe(0);
  expect(attempts(state, "book_appointment")).toHaveLength(1);
  expect(attempts(state, "book_appointment")[0].data.booking_id).toBe("offer-a2");
});

test("a lost booking response is not replayed and a fresh list reveals the visit", async ({ page, fixture }) => {
  await fixture.open("book-response-lost");
  await offerRow(page, "Lekarz Oferty Beta").getByRole("button", { name: "Zarezerwuj", exact: true }).click();
  await page.getByRole("button", { name: "Zarezerwuj wizytę", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Nie udało się potwierdzić wyniku");
  await expect(plannedRow(page, "Lekarz Oferty Beta")).toBeVisible();
  const state = await fixture.snapshot();
  expect(state.bookWrites).toBe(1);
  expect(attempts(state, "book_appointment")).toHaveLength(1);
});

test("cancellation can be dismissed without changing the visit", async ({ page, fixture }) => {
  await fixture.open();
  await plannedRow(page, "Lekarz Wizyty Alfa").getByRole("button", { name: "Odwołaj", exact: true }).click();
  await expect(cancelPanel(page)).toContainText("Konto Alfa");
  await expect(cancelPanel(page)).toContainText("Lekarz Wizyty Alfa");
  await page.getByRole("button", { name: "Zostaw wizytę", exact: true }).click();
  await expect(cancelPanel(page)).toHaveCount(0);
  expect(attempts(await fixture.snapshot(), "cancel_appointment")).toHaveLength(0);
});

test("confirmed cancellation removes only the selected account visit", async ({ page, fixture }) => {
  await fixture.open();
  await plannedRow(page, "Lekarz Wizyty Alfa").getByRole("button", { name: "Odwołaj", exact: true }).click();
  await page.getByRole("button", { name: "Odwołaj wizytę", exact: true }).dblclick({ delay: 15 });
  await expect(page.getByRole("status")).toHaveText("Wizyta została odwołana.");
  await expect(plannedRow(page, "Lekarz Wizyty Alfa")).toHaveCount(0);
  await expect(plannedRow(page, "Lekarz Wizyty Beta")).toBeVisible();
  const state = await fixture.snapshot();
  expect(state.cancelWrites).toBe(1);
  expect(attempts(state, "cancel_appointment")).toEqual([{ service: "cancel_appointment", data: { cancellation_id: "cancel-a", confirm_late_cancellation: false } }]);
});

test("late cancellation requires a checked confirmation before it can be sent", async ({ page, fixture }) => {
  await fixture.open("late");
  await plannedRow(page, "Lekarz Wizyty Alfa").getByRole("button", { name: "Odwołaj", exact: true }).click();
  const confirm = page.getByRole("button", { name: "Odwołaj wizytę", exact: true });
  await expect(confirm).toBeDisabled();
  expect(attempts(await fixture.snapshot(), "cancel_appointment")).toHaveLength(0);
  await page.getByRole("checkbox", { name: "Potwierdzam późne odwołanie", exact: true }).check();
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(page.getByRole("status")).toHaveText("Wizyta została odwołana.");
  expect(attempts(await fixture.snapshot(), "cancel_appointment")[0].data.confirm_late_cancellation).toBe(true);
});

test("a visit that became late asks again rather than confirming automatically", async ({ page, fixture }) => {
  await fixture.open("late-changed");
  await plannedRow(page, "Lekarz Wizyty Alfa").getByRole("button", { name: "Odwołaj", exact: true }).click();
  await page.getByRole("button", { name: "Odwołaj wizytę", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "Potwierdzam późne odwołanie", exact: true })).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Odwołaj wizytę", exact: true })).toBeDisabled();
  const state = await fixture.snapshot();
  expect(state.cancelWrites).toBe(0);
  expect(attempts(state, "cancel_appointment")).toHaveLength(1);
});

test("a lost cancellation response is not replayed and the list refreshes", async ({ page, fixture }) => {
  await fixture.open("cancel-response-lost");
  await plannedRow(page, "Lekarz Wizyty Alfa").getByRole("button", { name: "Odwołaj", exact: true }).click();
  await page.getByRole("button", { name: "Odwołaj wizytę", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Nie udało się potwierdzić wyniku");
  await expect(plannedRow(page, "Lekarz Wizyty Alfa")).toHaveCount(0);
  expect(attempts(await fixture.snapshot(), "cancel_appointment")).toHaveLength(1);
});

test("empty lists retain an add-search shortcut and navigate to the HA route", async ({ page, fixture }) => {
  await fixture.open("empty");
  await expect(page.getByText("Nie masz zaplanowanych wizyt.", { exact: true })).toHaveCount(2);
  await expect(page.getByText("Nie masz jeszcze szukajek.", { exact: true })).toHaveCount(2);
  await page.getByRole("link", { name: "Dodaj szukajkę", exact: true }).click();
  await expect(page).toHaveURL(/\/config\/integrations\/integration\/medi_assistant$/);
  expect((await fixture.snapshot()).bookWrites).toBe(0);
});

test("non-admin users do not see the configuration shortcut", async ({ page, fixture }) => {
  await fixture.open("normal", false);
  await expect(page.getByRole("link", { name: "Dodaj szukajkę", exact: true })).toHaveCount(0);
  await expect(bookButtons(page)).toHaveCount(3);
});

test("unavailable accounts and searches are distinct from empty results", async ({ page, fixture }) => {
  await fixture.open("partial");
  await expect(plannedRow(page, "Lekarz Wizyty Alfa")).toBeVisible();
  await expect(page.getByText("Konto Beta wymaga zalogowania.", { exact: true })).toBeVisible();
  await expect(page.getByText("Dane szukajki są niedostępne.", { exact: true })).toBeVisible();
  await expect(page.getByText("Brak dostępnych terminów.", { exact: true })).toBeVisible();
});

test("manual refresh fetches new results and preserves a collapsed search", async ({ page, fixture }) => {
  await fixture.open();
  const summary = page.locator("summary").filter({ hasText: "Szukajka Alfa" });
  await summary.click();
  await expect(offerRow(page, "Lekarz Oferty Pierwszy")).toBeHidden();
  await page.getByRole("button", { name: "Odśwież", exact: true }).click();
  await expect(page.getByRole("button", { name: "Odśwież", exact: true })).toBeEnabled();
  await expect(offerRow(page, "Lekarz Oferty Pierwszy")).toBeHidden();
  expect(attempts(await fixture.snapshot(), "list_booking_offers").at(-1).data.refresh).toBe(true);
  await summary.click();
  await expect(offerRow(page, "Lekarz Oferty Pierwszy")).toBeVisible();
});

test("failed search refresh keeps planned visits available and disables stale offers", async ({ page, fixture }) => {
  await fixture.open();
  await fixture.failReads("searches");
  await page.getByRole("button", { name: "Odśwież", exact: true }).click();
  await expect(page.getByText("Nie udało się pobrać wyników szukajek. Odśwież listę.", { exact: true })).toBeVisible();
  await expect(bookButtons(page).first()).toBeDisabled();
  await expect(plannedRow(page, "Lekarz Wizyty Alfa").getByRole("button", { name: "Odwołaj", exact: true })).toBeEnabled();
  await fixture.failReads(null);
  await page.getByRole("button", { name: "Odśwież", exact: true }).click();
  await expect(bookButtons(page).first()).toBeEnabled();
});

test("failed planned-visit refresh does not hide successful search results", async ({ page, fixture }) => {
  await fixture.open();
  await fixture.failReads("booked");
  await page.getByRole("button", { name: "Odśwież", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Nie udało się pobrać wizyt");
  await expect(plannedRow(page, "Lekarz Wizyty Alfa").getByRole("button", { name: "Odwołaj", exact: true })).toBeDisabled();
  await expect(bookButtons(page).first()).toBeEnabled();
});

test("reports the bounded search list and fits the browser viewport", async ({ page, fixture }) => {
  await fixture.open("truncated");
  await expect(page.getByText("Pokazano 20 z 30", { exact: true })).toBeVisible();
  await expect(bookButtons(page)).toHaveCount(21);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});

test("renders account and doctor HTML as inert text", async ({ page, fixture }) => {
  await fixture.open("html");
  await expect(page.locator("medi-assistant-appointments").locator("img")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: '<img src="x" onerror="window.patientHtmlExecuted=true">', exact: true })).toBeVisible();
  expect(await page.evaluate(() => window.patientHtmlExecuted)).toBeUndefined();
});


test("search deletion confirmation identifies account and can be dismissed", async ({ page, fixture }) => {
  await fixture.open();
  await page.locator("details.search").filter({ hasText: "Szukajka Beta" }).getByRole("button", { name: "Usuń szukajkę", exact: true }).click();
  const panel = page.getByRole("region", { name: "Usunąć tę szukajkę?", exact: true });
  await expect(panel).toContainText("Konto Beta");
  await expect(panel).toContainText("Szukajka Beta");
  await expect(panel).toBeFocused();
  await panel.getByRole("button", { name: "Zostaw szukajkę" }).click();
  await expect(panel).toHaveCount(0);
  expect(attempts(await fixture.snapshot(), "delete_search")).toHaveLength(0);
});

for (const scenario of ["normal", "delete-response-lost"]) {
  test(`search deletion removes only selected search without cancelling appointments: ${scenario}`, async ({ page, fixture }) => {
    await fixture.open(scenario);
    await page.locator("details.search").filter({ hasText: "Szukajka Beta" }).getByRole("button", { name: "Usuń szukajkę", exact: true }).click();
    const panel = page.getByRole("region", { name: "Usunąć tę szukajkę?", exact: true });
    await panel.getByRole("button", { name: "Usuń szukajkę", exact: true }).dblclick();
    await expect(page.locator("details.search").filter({ hasText: "Szukajka Beta" })).toHaveCount(0);
    await expect(page.locator("details.search").filter({ hasText: "Szukajka Alfa" })).toBeVisible();
    await expect(plannedRow(page, "Lekarz Wizyty Beta")).toBeVisible();
    const state = await fixture.snapshot();
    expect(attempts(state, "delete_search")).toEqual([{ service: "delete_search", data: { account_id: "account-b", search_id: "search-b" } }]);
    expect(state.cancelWrites).toBe(0);
    expect(state.bookWrites).toBe(0);
  });
}

test("failed deletion preserves the search", async ({ page, fixture }) => {
  await fixture.open("delete-failed");
  await page.locator("details.search").first().getByRole("button", { name: "Usuń szukajkę", exact: true }).click();
  await page.getByRole("region", { name: "Usunąć tę szukajkę?" }).getByRole("button", { name: "Usuń szukajkę", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Nie udało się usunąć szukajki.");
  await expect(page.locator("details.search")).toHaveCount(2);
});

test("non-admin has no search deletion buttons", async ({ page, fixture }) => {
  await fixture.open("normal", false);
  await expect(page.getByRole("button", { name: "Usuń szukajkę", exact: true })).toHaveCount(0);
});
