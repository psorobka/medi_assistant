import { defineConfig } from "@playwright/test";

const port = Number(process.env.MEDI_BROWSER_TEST_PORT || 4178);

export default defineConfig({
  testDir: "./tests/browser",
  testMatch: "**/*.spec.mjs",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 2,
  timeout: 20000,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    browserName: "chromium",
    headless: true,
    locale: "pl-PL",
    timezoneId: "Europe/Warsaw",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium-desktop", use: { viewport: { width: 1280, height: 900 } } },
    { name: "chromium-phone", use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
  webServer: {
    command: "node tests/browser/server.mjs",
    url: `http://127.0.0.1:${port}/health`,
    timeout: 15000,
    reuseExistingServer: false,
  },
});
