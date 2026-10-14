import { defineConfig } from "@playwright/test";

// Playwright's Electron support launches the real Electron binary and drives the
// real renderer over CDP, which is what the record asks for: a smoke test that
// launches the app, not one that renders a component tree into a DOM stub. It
// also exposes `app.windows()` and `waitForEvent("window")`, which is exactly
// what the multi-window and pop-out criteria need. No browsers are downloaded --
// the Electron path uses the app's own binary.
export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // Electron windows are real OS windows; running specs in parallel would have
  // several apps fighting over focus on the same desktop.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: "list",
});
