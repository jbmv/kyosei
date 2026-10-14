// KYO-468: a kyo serve that stops mid-connection is a state, never an uncaught exception.
//
// Electron's "Uncaught Exception" dialog (`setTypeOfService EINVAL`, thrown from
// undici's writeH1) appeared while desktop checks were stopping their kyo serves.
// The trigger was not reproduced, so `kyoserve.ts` no longer uses undici at all
// (`tests/seams.spec.ts`, KYO-468.c1); what THIS file holds is the case the fix must
// survive whatever the trigger was: a real kyo serve stopped under an open events stream
// and requests in flight -- in this process against the Electron-free client, and in
// the app's own main process, which must still be running and answering afterwards.
//
// Every kyo serve here is the check's own, on an ephemeral loopback port. Never 47370.

import { mkdtempSync, rmSync } from "node:fs";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron, type ElectronApplication, expect, test } from "@playwright/test";
import {
  drainEvents,
  SERVE_URL_ENV,
  ServeClient,
  ServeUnreachable,
  serveFactsFrom,
} from "../src/main/kyoserve";
import { TMUX_ENV } from "../src/main/terminal";
import type { StoreChangeKind } from "../src/shared/protocol";
import {
  closeReadyApp,
  type Fixture,
  type KyoServeFixture,
  MACOS_NO_RESTORE,
  makeFixture,
  markReadyToClose,
  removeFixture,
  startKyoServe,
  suiteEnv,
  windowShowArg,
} from "./fixture";

const APP_ROOT = join(__dirname, "..");

let fixture: Fixture;

test.beforeAll(async () => {
  fixture = await makeFixture(2);
});

test.afterAll(async () => {
  if (fixture !== undefined) removeFixture(fixture);
});

/** Stop a check's kyo serve with `signal`, and wait for it to have gone. */
async function stopWith(serve: KyoServeFixture, signal: NodeJS.Signals): Promise<void> {
  if (serve.child.exitCode !== null || serve.child.signalCode !== null) return;
  const gone = new Promise<void>((resolve) => serve.child.once("exit", () => resolve()));
  serve.child.kill(signal);
  await gone;
}

/** Everything this process would have thrown uncaught, heard without changing what happens. */
function hearUncaught(): { heard: string[]; stop: () => void } {
  const heard: string[] = [];
  const listener = (error: Error) => heard.push(String(error?.stack ?? error));
  process.on("uncaughtExceptionMonitor", listener);
  return { heard, stop: () => process.off("uncaughtExceptionMonitor", listener) };
}

// ------------------------------------------------------------- the framing (pure)

test("the events reader frames text/event-stream across chunk boundaries (KYO-468)", () => {
  const heard: StoreChangeKind[] = [];
  const hear = (kind: StoreChangeKind) => heard.push(kind);
  let rest = drainEvents('event: changed\ndata: {"kind": "ide', hear);
  expect(heard).toEqual([]);
  rest = drainEvents(`${rest}as"}\n\n: comment\n\nevent: changed\r\n`, hear);
  expect(heard).toEqual(["ideas"]);
  rest = drainEvents(`${rest}data: {"kind": "store"}\r\n\r\nevent: other\ndata: {}\n\n`, hear);
  expect(heard).toEqual(["ideas", "store"]);
  // A kind this build does not know, and an event that is not `changed`, are not heard.
  drainEvents(`${rest}event: changed\ndata: {"kind": "weather"}\n\n`, hear);
  expect(heard).toEqual(["ideas", "store"]);
});

// ----------------------------------------------- a server that dies mid-answer (fake)

test("an answer or a stream cut off mid-connection is not-running, never thrown (KYO-468.c2)", async () => {
  test.setTimeout(60_000);
  const uncaught = hearUncaught();
  let streams = 0;
  const fake = createServer((request: IncomingMessage, response: ServerResponse) => {
    if (request.url?.endsWith("/events")) {
      streams += 1;
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      response.write('event: changed\ndata: {"kind": "store"}\n\n');
      // Mid-stream, reset: an RST, not an orderly close.
      setTimeout(() => request.socket.resetAndDestroy(), 100);
      return;
    }
    if (request.url === "/v1/projects") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ projects: [{ name: "p", root: "/p" }] }));
      return;
    }
    // Headers and half a body promised by Content-Length, then reset.
    response.writeHead(200, { "Content-Type": "application/json", "Content-Length": "100" });
    response.write('{"ok": tr');
    setTimeout(() => request.socket.resetAndDestroy(), 50);
  });
  await new Promise<void>((resolve) => fake.listen(0, "127.0.0.1", () => resolve()));
  const port = (fake.address() as { port: number }).port;
  const client = new ServeClient({ host: "127.0.0.1", port }, true);
  const project = client.project("/p");
  const heard: StoreChangeKind[] = [];
  const stop = project.subscribe((kind) => heard.push(kind));
  try {
    const cut = await project.json(["list"]).then(
      () => null,
      (error: unknown) => error,
    );
    expect(cut).toBeInstanceOf(ServeUnreachable);
    expect((cut as ServeUnreachable).slow).toBe(false);
    // The stream heard its event, was reset, and was opened again.
    await expect.poll(() => streams, { timeout: 15_000 }).toBeGreaterThanOrEqual(2);
    expect(heard).toContain("store");
    expect(heard).toContain("server");
  } finally {
    stop();
    fake.closeAllConnections();
    await new Promise<void>((resolve) => fake.close(() => resolve()));
    uncaught.stop();
  }
  expect(uncaught.heard).toEqual([]);
});

// ----------------------------------------------- a real kyo serve, stopped (client)

for (const signal of ["SIGTERM", "SIGKILL"] as const) {
  test(`a real kyo serve stopped by ${signal} under an open stream and requests in flight is not running, and nothing is thrown (KYO-468.c2)`, async () => {
    test.setTimeout(90_000);
    const uncaught = hearUncaught();
    const serve = await startKyoServe(fixture.home);
    const client = new ServeClient(serve.address, true);
    const project = client.project(fixture.root);
    const heard: StoreChangeKind[] = [];
    const stop = project.subscribe((kind) => heard.push(kind));
    let restarted: KyoServeFixture | null = null;
    try {
      // The stream is open once it hears a change made after it opened.
      await expect
        .poll(
          async () => {
            await project.writeFile(`poke ${Date.now()}.md`, "x\n", "create");
            return heard.includes("ideas");
          },
          { timeout: 20_000, intervals: [500] },
        )
        .toBe(true);

      // A burst of served commands, then the stop while they are still being answered:
      // kyo serve runs each under one lock, so most are queued or mid-answer.
      const inFlight = Array.from({ length: 12 }, (_, index) =>
        project.json(index % 2 === 0 ? ["list"] : ["show", "FIX-1"]).then(
          (answer) => ({ ok: true as const, code: answer.code }),
          (error: unknown) => ({ ok: false as const, error }),
        ),
      );
      await new Promise((resolve) => setTimeout(resolve, 60));
      await stopWith(serve, signal);
      const settled = await Promise.race([
        Promise.all(inFlight),
        new Promise<null>((resolve) => setTimeout(() => resolve(null), 30_000)),
      ]);
      expect(settled, "every in-flight request settled").not.toBeNull();
      const failures = (settled ?? []).filter((outcome) => !outcome.ok);
      // SIGTERM lets kyo serve finish what it holds (measured: all twelve answered);
      // SIGKILL cuts them off mid-connection, which is the case in question.
      if (signal === "SIGKILL") {
        expect(failures.length, "some requests were cut off by the stop").toBeGreaterThan(0);
      }
      for (const failure of failures) {
        if (failure.ok) continue;
        expect(failure.error).toBeInstanceOf(ServeUnreachable);
        expect((failure.error as ServeUnreachable).slow).toBe(false);
      }

      // The window's question, asked now: not running -- not slow, not a crash.
      const facts = serveFactsFrom(client, await client.version(5_000));
      expect(facts.running).toBe(false);
      expect(facts.slow).toBe(false);
      expect(facts.problem ?? "").toContain("kyo serve is not running");
      // The stream told its drop as `server`.
      await expect.poll(() => heard.filter((kind) => kind === "server").length).toBe(1);

      // Started again on the same port: the stream comes back by itself, and says so.
      restarted = await startKyoServe(fixture.home, {}, serve.address.port);
      await expect
        .poll(() => heard.filter((kind) => kind === "server").length, { timeout: 20_000 })
        .toBe(2);
      expect((await client.version()).ok).toBe(true);
    } finally {
      stop();
      await stopWith(serve, "SIGKILL");
      if (restarted !== null) await stopWith(restarted, "SIGTERM");
      uncaught.stop();
    }
    expect(uncaught.heard).toEqual([]);
  });
}

// ------------------------------------------------ a real kyo serve, stopped (the app)

test("the app's main process keeps running when its kyo serve stops under an open stream and requests in flight (KYO-468.c2)", async () => {
  test.setTimeout(150_000);
  const profile = mkdtempSync(join(tmpdir(), "kyosei transport profile "));
  const serve = await startKyoServe(fixture.home);
  let restarted: KyoServeFixture | null = null;
  let app: ElectronApplication | null = null;
  try {
    app = await _electron.launch({
      args: [APP_ROOT, `--user-data-dir=${profile}`, ...MACOS_NO_RESTORE, windowShowArg()],
      env: suiteEnv({
        [SERVE_URL_ENV]: serve.url,
        KYOSEI_PROJECT_ROOT: fixture.root,
        KYOSEI_HOME: fixture.home,
        [TMUX_ENV]: "off",
        KYOSEI_LOGIN_SHELL: "off",
        KYOSEI_DISPLAYS: JSON.stringify([
          { id: 468_468, bounds: { x: 0, y: 0, width: 1400, height: 900 } },
        ]),
      }),
    });
    const page = await app.firstWindow();
    await page.waitForSelector('body[data-ready="true"]');
    markReadyToClose(app);
    await page.waitForSelector('body[data-facts="ready"]');
    await expect(page.locator('[data-region="serve-down"]')).toHaveCount(0);
    // Heard in the main process without changing what an uncaught exception does there
    // (no handler is installed; the monitor only listens).
    await app.evaluate(() => {
      const held = globalThis as { __kyo468Uncaught?: string[] };
      held.__kyo468Uncaught = [];
      process.on("uncaughtExceptionMonitor", (error) => {
        held.__kyo468Uncaught?.push(String(error?.stack ?? error));
      });
    });
    const facts = await page.evaluate(() => window.kyosei.getFacts());
    expect(facts.serve.running).toBe(true);

    // Requests in flight from the window, then the stop, under them.
    await page.evaluate(() => {
      const held = globalThis as { __kyo468Calls?: Promise<unknown>[] };
      held.__kyo468Calls = [];
      for (let index = 0; index < 8; index += 1) {
        held.__kyo468Calls.push(window.kyosei.getRecordsList().catch((error) => error));
        held.__kyo468Calls.push(window.kyosei.showRecord("FIX-1").catch((error) => error));
      }
    });
    await new Promise((resolve) => setTimeout(resolve, 60));
    await stopWith(serve, "SIGKILL");

    // Every call the window made settles: the main process answered them all.
    const settled = await page.evaluate(async () => {
      const held = globalThis as { __kyo468Calls?: Promise<unknown>[] };
      await Promise.all(held.__kyo468Calls ?? []);
      return (held.__kyo468Calls ?? []).length;
    });
    expect(settled).toBe(16);

    // What the window is told when it asks -- a facts read, as launch, a project switch
    // and Check again make one: not running, naming its address; not slow, not a crash.
    const down = await page.evaluate(() => window.kyosei.getFacts());
    expect(down.serve.running).toBe(false);
    expect(down.serve.slow).toBe(false);
    expect(down.serve.problem ?? "").toContain("kyo serve is not running");
    expect(down.serve.problem ?? "").toContain(serve.url);
    // The main process is still running and answering, and threw nothing.
    const alive = await Promise.race([
      app.evaluate(({ app: electronApp }) => ({
        pid: process.pid,
        ready: electronApp.isReady(),
        uncaught: (globalThis as { __kyo468Uncaught?: string[] }).__kyo468Uncaught ?? null,
      })),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), 10_000)),
    ]);
    expect(alive, "the main process answered").not.toBeNull();
    expect(alive?.ready).toBe(true);
    expect(alive?.uncaught).toEqual([]);
    expect(app.process().exitCode).toBeNull();

    // And with kyo serve started again, the same question is answered: running.
    restarted = await startKyoServe(fixture.home, {}, serve.address.port);
    await expect
      .poll(async () => (await page.evaluate(() => window.kyosei.getFacts())).serve.running, {
        timeout: 30_000,
      })
      .toBe(true);
    await expect(page.locator('[data-region="serve-down"]')).toHaveCount(0);
    const after = await app.evaluate(
      () => (globalThis as { __kyo468Uncaught?: string[] }).__kyo468Uncaught ?? null,
    );
    expect(after).toEqual([]);
  } finally {
    if (app !== null) await closeReadyApp(app);
    await stopWith(serve, "SIGKILL");
    if (restarted !== null) await stopWith(restarted, "SIGTERM");
    rmSync(profile, { recursive: true, force: true });
  }
});
