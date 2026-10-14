// KYO-459.c2: when nothing answers on the kyo serve port, the app shows ONE state --
// "kyo serve is not running", naming `kyo serve --install --apply` and `kyo serve` --
// with Check again, which clears it once a kyo serve answers.
//
// Launches the app pointed (`KYOSEI_SERVE_URL`) at a loopback port nothing listens on,
// then starts the check's own `kyo serve` on exactly that port, as a person following
// the state's second command would. Never the real server on 47370.

import { mkdtempSync, rmSync } from "node:fs";
import { createServer as createHttpServer, request as httpRequest } from "node:http";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron, type ElectronApplication, expect, test } from "@playwright/test";
import { SERVE_URL_ENV, START_SERVE_COMMANDS } from "../src/main/kyoserve";
import { TMUX_ENV } from "../src/main/terminal";
import { SERVE_REPROBE_MS, SERVE_RESTART_COMMAND } from "../src/shared/protocol";
import {
  closeReadyApp,
  type Fixture,
  type KyoServeFixture,
  MACOS_NO_RESTORE,
  makeFixture,
  markReadyToClose,
  removeFixture,
  startKyoServe,
  stopKyoServe,
  suiteEnv,
  windowShowArg,
} from "./fixture";

const APP_ROOT = join(__dirname, "..");

async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.on("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      probe.close(() =>
        resolve(typeof address === "object" && address !== null ? address.port : 0),
      );
    });
  });
}

let fixture: Fixture;
let app: ElectronApplication | null = null;
let started: KyoServeFixture | null = null;
const profile = mkdtempSync(join(tmpdir(), "kyosei serve-down profile "));

test.beforeAll(async () => {
  fixture = await makeFixture(1);
});

test.afterAll(async () => {
  if (app !== null) await closeReadyApp(app);
  if (started !== null) await stopKyoServe(started);
  removeFixture(fixture);
  rmSync(profile, { recursive: true, force: true });
});

test("nothing on the kyo serve port is one state naming both commands, and Check again clears it once kyo serve answers (KYO-459.c2)", async () => {
  test.setTimeout(120_000);
  const port = await freePort();
  const env = suiteEnv({
    [SERVE_URL_ENV]: `http://127.0.0.1:${port}`,
    KYOSEI_PROJECT_ROOT: fixture.root,
    KYOSEI_HOME: fixture.home,
    [TMUX_ENV]: "off",
    KYOSEI_LOGIN_SHELL: "off",
    KYOSEI_DISPLAYS: JSON.stringify([
      { id: 459_459, bounds: { x: 0, y: 0, width: 1400, height: 900 } },
    ]),
  });
  app = await _electron.launch({
    args: [APP_ROOT, `--user-data-dir=${profile}`, ...MACOS_NO_RESTORE, windowShowArg()],
    env,
  });
  const page = await app.firstWindow();
  await page.waitForSelector('body[data-ready="true"]');
  markReadyToClose(app);
  await page.waitForSelector('body[data-facts="ready"]');

  // One state, with exact text.
  const state = page.locator('[data-region="serve-down"]');
  await expect(state).toHaveCount(1);
  await expect(state.locator('[data-region="serve-down-title"]')).toHaveText(
    "kyo serve is not running",
  );
  await expect(state.locator('[data-region="serve-down-why"]')).toContainText(
    `http://127.0.0.1:${port}`,
  );
  await expect(state.locator('[data-region="serve-down-command"]')).toHaveText([
    ...START_SERVE_COMMANDS,
  ]);
  expect(START_SERVE_COMMANDS).toEqual(["kyo serve --install --apply", "kyo serve"]);
  const facts = await page.evaluate(() => window.kyosei.getFacts());
  expect(facts.serve.running).toBe(false);
  expect(facts.serve.problem ?? "").toContain("kyo serve is not running");

  // Check again while it is still down: the state stays, and says it asked.
  await page.locator('[data-region="serve-check-again"]').click();
  await expect(page.locator('[data-region="serve-down-state"]')).toContainText("Still nothing at");
  await expect(state).toHaveCount(1);

  // The remedy the state names: `kyo serve`, on that port, serving this project.
  started = await startKyoServe(fixture.home, {}, port);
  await page.locator('[data-region="serve-check-again"]').click();
  await expect(state).toHaveCount(0, { timeout: 20_000 });
  const after = await page.evaluate(() => window.kyosei.getFacts());
  expect(after.serve.running).toBe(true);
  expect(after.store.ok, after.store.problem ?? "").toBe(true);
  expect(after.store.records).toBe(1);
  // And the window reads as a first load reads it: the records list has the record.
  const sidebar = page.locator('[data-region="sidebar"]');
  if ((await sidebar.getAttribute("data-list")) !== "records") {
    await page.locator('[data-rail-item="records"]').click();
  }
  await expect(
    page.locator('[data-region="sidebar-row"]').filter({ hasText: "Fixture record 1" }),
  ).toHaveCount(1, {
    timeout: 20_000,
  });
});

async function launchOn(port: number, dir: string): Promise<ElectronApplication> {
  const env = suiteEnv({
    [SERVE_URL_ENV]: `http://127.0.0.1:${port}`,
    KYOSEI_PROJECT_ROOT: fixture.root,
    KYOSEI_HOME: fixture.home,
    [TMUX_ENV]: "off",
    KYOSEI_LOGIN_SHELL: "off",
    KYOSEI_DISPLAYS: JSON.stringify([
      { id: 449_449, bounds: { x: 0, y: 0, width: 1400, height: 900 } },
    ]),
  });
  const launched = await _electron.launch({
    args: [APP_ROOT, `--user-data-dir=${dir}`, ...MACOS_NO_RESTORE, windowShowArg()],
    env,
  });
  const page = await launched.firstWindow();
  await page.waitForSelector('body[data-ready="true"]');
  markReadyToClose(launched);
  await page.waitForSelector('body[data-facts="ready"]');
  return launched;
}

test("the not-running state asks again by itself every few seconds and clears once kyo serve answers, with no click (KYO-449.c8)", async () => {
  test.setTimeout(120_000);
  const port = await freePort();
  const dir = mkdtempSync(join(tmpdir(), "kyosei reprobe profile "));
  let launched: ElectronApplication | null = null;
  let serve: KyoServeFixture | null = null;
  try {
    launched = await launchOn(port, dir);
    const page = await launched.firstWindow();
    const state = page.locator('[data-region="serve-down"]');
    await expect(state).toHaveCount(1);
    await expect(page.locator('[data-region="serve-down-reprobe"]')).toContainText(
      `every ${SERVE_REPROBE_MS / 1000} s`,
    );
    // Asked again without a click: the state line says when.
    await expect(page.locator('[data-region="serve-down-state"]')).toContainText(
      "Still nothing at",
      { timeout: SERVE_REPROBE_MS * 3 },
    );
    serve = await startKyoServe(fixture.home, {}, port);
    await expect(state).toHaveCount(0, { timeout: SERVE_REPROBE_MS * 3 + 20_000 });
  } finally {
    if (launched !== null) await closeReadyApp(launched);
    if (serve !== null) await stopKyoServe(serve);
    rmSync(dir, { recursive: true, force: true });
  }
});

test("with no kyo serve here, the not-running state connects to another machine's server and opens its project in a window of its own, and each status bar names where it is served from (KYO-473.c1, c5)", async () => {
  test.setTimeout(120_000);
  const port = await freePort();
  const dir = mkdtempSync(join(tmpdir(), "kyosei connect-from-down profile "));
  let launched: ElectronApplication | null = null;
  // The "other machine": a kyo serve on a port this window was not pointed at.
  let other: KyoServeFixture | null = null;
  try {
    other = await startKyoServe(fixture.home);
    launched = await launchOn(port, dir);
    const app = launched;
    const page = await app.firstWindow();
    const windows = () => app.windows().filter((w) => w.url().startsWith("file:"));
    const state = page.locator('[data-region="serve-down"]');
    await expect(state).toHaveCount(1);
    // This window's own project is on this machine, and its status bar says so.
    await expect(page.locator('[data-region="served-from"]')).toHaveText(/^.+ @ this machine$/);

    // The third choice, beside the two commands.
    const connect = state.locator('[data-region="serve-connect"]');
    await expect(connect).toHaveText("Connect to a server…");
    await connect.click();
    await expect(page.locator('[data-region="remote-dialog"]')).toBeVisible();
    await page.locator('[data-region="remote-address"]').fill(other.url);
    await page.locator('[data-region="remote-connect"]').click();
    await expect(page.locator('[data-region="remote-connect"]')).toHaveText("Open", {
      timeout: 20_000,
    });
    const before = windows().length;
    await page.locator('[data-region="remote-connect"]').click();
    await expect(page.locator('[data-region="remote-dialog"]')).toHaveCount(0, {
      timeout: 20_000,
    });
    await expect.poll(() => windows().length, { timeout: 20_000 }).toBe(before + 1);
    const opened = windows().find((w) => w !== page);
    if (opened === undefined) throw new Error("no window on the other server");
    await opened.waitForSelector('body[data-facts="ready"]');
    const facts = await opened.evaluate(() => window.kyosei.getFacts());
    expect(facts.project.root).toBe(fixture.root);
    expect(facts.project.remote).toEqual({ host: "127.0.0.1", port: other.address.port });
    expect(facts.serve.running).toBe(true);
    await expect(opened.locator('[data-region="serve-down"]')).toHaveCount(0);
    await expect(opened.locator('[data-region="served-from"]')).toHaveText(
      `${facts.project.project ?? "?"} @ 127.0.0.1:${other.address.port}`,
    );
    // It reached a store: the other server's record is listed.
    expect(facts.store.ok, facts.store.problem ?? "").toBe(true);
    await opened.close();
  } finally {
    if (launched !== null) await closeReadyApp(launched);
    if (other !== null) await stopKyoServe(other);
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a server answering as a kyo this app does not read is the compatibility state, naming its address (KYO-449.c5)", async () => {
  test.setTimeout(120_000);
  const fake = createHttpServer((request, response) => {
    if (request.url === "/v1/version") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(
        JSON.stringify({
          ok: true,
          kyosei: {
            command: "version",
            distribution: "kyosei",
            version: "0.0.1",
            kind: "uv-tool",
            release: true,
            commit_short: "0ld0ld0",
          },
          schema_version: 25,
        }),
      );
      return;
    }
    response.writeHead(404, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ ok: false, error: "not here", refused_by: "kyo serve" }));
  });
  await new Promise<void>((resolve) => fake.listen(0, "127.0.0.1", () => resolve()));
  const port = (fake.address() as { port: number }).port;
  const dir = mkdtempSync(join(tmpdir(), "kyosei compat profile "));
  let launched: ElectronApplication | null = null;
  try {
    launched = await launchOn(port, dir);
    const page = await launched.firstWindow();
    const state = page.locator('[data-region="serve-compat"]');
    await expect(state).toHaveCount(1);
    await expect(state).toHaveAttribute("data-state", "too-old");
    await expect(page.locator('[data-region="serve-compat-title"]')).toHaveText(
      "This `kyo` is too old for this app.",
    );
    await expect(page.locator('[data-region="serve-compat-says"]')).toContainText(
      `The kyo serve at \`http://127.0.0.1:${port}\` is kyosei \`0.0.1\``,
    );
    await expect(page.locator('[data-region="serve-compat-command"]')).toHaveText(
      "uv tool upgrade kyosei",
    );
    await expect(page.locator('[data-region="serve-down"]')).toHaveCount(0);
    // The kyo this app runs (the checkout's, unpacked) is one it reads: no chip.
    await expect(page.locator('[data-region="kyo-compat"]')).toBeHidden();
    const facts = await page.evaluate(() => window.kyosei.getFacts());
    expect(facts.kyoCompat.state, facts.kyoCompat.says).toBe("accepted");
  } finally {
    if (launched !== null) await closeReadyApp(launched);
    await new Promise<void>((resolve) => fake.close(() => resolve()));
    rmSync(dir, { recursive: true, force: true });
  }
});

test("kyo serve stopping under an open window shows the not-running state within a few seconds with no click, and it clears when the server returns (KYO-471.c1)", async () => {
  test.setTimeout(120_000);
  const port = await freePort();
  const dir = mkdtempSync(join(tmpdir(), "kyosei stream-close profile "));
  let launched: ElectronApplication | null = null;
  let serve: KyoServeFixture | null = await startKyoServe(fixture.home, {}, port);
  try {
    launched = await launchOn(port, dir);
    const page = await launched.firstWindow();
    const state = page.locator('[data-region="serve-down"]');
    await expect(state).toHaveCount(0);
    // The events stream is open once the store is heard; give it a moment to be.
    await expect
      .poll(() => serve?.stderr().includes("/events") ?? false, { timeout: 20_000 })
      .toBe(true);

    // Kill it under the open window. Nothing else asks: the reprobe loop runs only
    // while the window already knows it is down, so the stream closing is the signal.
    await stopKyoServe(serve);
    serve = null;
    const stopped = Date.now();
    await expect(state).toHaveCount(1, { timeout: 5_000 });
    expect(Date.now() - stopped).toBeLessThan(5_000);
    await expect(state.locator('[data-region="serve-down-title"]')).toHaveText(
      "kyo serve is not running",
    );

    // Back on the same port: the re-probe loop (KYO-449.c8) clears it, again no click.
    serve = await startKyoServe(fixture.home, {}, port);
    await expect(state).toHaveCount(0, { timeout: SERVE_REPROBE_MS * 3 + 20_000 });
  } finally {
    if (launched !== null) await closeReadyApp(launched);
    if (serve !== null) await stopKyoServe(serve);
    rmSync(dir, { recursive: true, force: true });
  }
});

test("a stale kyo serve is a status-bar line carrying stale_detail and the restart command, and the scratchpad and records views stay usable (KYO-550.c1, c2)", async () => {
  test.setTimeout(120_000);
  // A real kyo serve, behind a pass-through that answers `/v1/version` as a server
  // whose installed build moved on would (`serve.py` `_version`): every other request,
  // the events stream included, is the real server's answer.
  const real = await startKyoServe(fixture.home);
  const detail =
    "this server started as kyosei 0.0.0 (0ld0ld0), and the build installed on disk is " +
    "now kyosei 0.0.0 (n3wn3wn); stop kyo serve and start it again to serve the installed build";
  const proxy = createHttpServer((request, response) => {
    const upstream = httpRequest(
      {
        host: real.address.host,
        port: real.address.port,
        method: request.method,
        path: request.url,
        headers: request.headers,
        agent: false,
      },
      (answer) => {
        if (request.url === "/v1/version") {
          let text = "";
          answer.setEncoding("utf8");
          answer.on("data", (chunk: string) => {
            text += chunk;
          });
          answer.on("end", () => {
            const document = JSON.parse(text) as Record<string, unknown>;
            const body = JSON.stringify({ ...document, stale: true, stale_detail: detail });
            const headers = {
              ...answer.headers,
              "content-length": String(Buffer.byteLength(body)),
            };
            response.writeHead(answer.statusCode ?? 502, headers);
            response.end(body);
          });
          return;
        }
        response.writeHead(answer.statusCode ?? 502, answer.headers);
        answer.pipe(response);
      },
    );
    upstream.on("error", () => response.destroy());
    response.on("close", () => upstream.destroy());
    request.pipe(upstream);
  });
  await new Promise<void>((resolve) => proxy.listen(0, "127.0.0.1", () => resolve()));
  const port = (proxy.address() as { port: number }).port;
  const dir = mkdtempSync(join(tmpdir(), "kyosei stale profile "));
  let launched: ElectronApplication | null = null;
  try {
    launched = await launchOn(port, dir);
    const page = await launched.firstWindow();
    const note = page.locator('[data-region="serve-stale"]');
    await expect(note).toBeVisible();
    await expect(note).toHaveAttribute("data-state", "stale");
    await expect(note).toContainText(detail);
    await expect(note).toContainText(SERVE_RESTART_COMMAND);
    // c2: no overlay of any kind, and the views behind it are the stale server's answers.
    await expect(page.locator("[data-overlay]")).toHaveCount(0);
    await expect(page.locator('[data-region="serve-down"]')).toHaveCount(0);
    await expect(page.locator('[data-region="serve-compat"]')).toHaveCount(0);
    const facts = await page.evaluate(() => window.kyosei.getFacts());
    expect(facts.serve.stale).toBe(true);
    expect(facts.store.ok, facts.store.problem ?? "").toBe(true);
    const sidebar = page.locator('[data-region="sidebar"]');
    if ((await sidebar.getAttribute("data-list")) !== "records") {
      await page.locator('[data-rail-item="records"]').click();
    }
    await expect(
      page.locator('[data-region="sidebar-row"]').filter({ hasText: "Fixture record 1" }),
    ).toHaveCount(1, { timeout: 20_000 });
    // The scratchpad opens and takes typing.
    const pane = page.locator('[data-region="compose-pane"][data-state="ready"]').first();
    await expect(pane).toHaveCount(1, { timeout: 20_000 });
    const editor = pane.locator("textarea, [contenteditable='true']").first();
    await expect(editor).toBeEditable();
  } finally {
    if (launched !== null) await closeReadyApp(launched);
    await new Promise<void>((resolve) => proxy.close(() => resolve()));
    proxy.closeAllConnections();
    await stopKyoServe(real);
    rmSync(dir, { recursive: true, force: true });
  }
});
