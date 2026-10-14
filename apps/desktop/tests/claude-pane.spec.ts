// KYO-186: where the claude.ai pane points, and how it degrades when it cannot tell.
//
// These launch nothing, but for one. The resolver is an Electron-free module for
// exactly this reason (see `src/main/sessions.ts`), so every one of its outcomes can
// be driven directly and fail with a line number. The one launch is KYO-382's: the
// pane FOLLOWING a session that starts after it was shown is a property of the main
// process's wiring rather than of the resolver, so it is asserted in the real app.
//
// THE FILE UNDER TEST IS WRITTEN BY THE REAL CLI wherever the point is the contract
// between the two halves. A hand-written fixture would let this reader and
// `kyo sessions --hook` drift apart while both stayed green -- which is the one
// failure a check on a cross-language file format exists to prevent. Only the
// checks about MALFORMED documents write their own bytes, because the CLI cannot be
// asked to produce a document it would refuse to write.

import { execFileSync } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron, type ElectronApplication, expect, test } from "@playwright/test";
import { clientFor } from "../src/main/kyoserve";
import {
  CLAUDE_INDEX_URL,
  isClaudeUrl,
  RESOLVED_ENDED,
  RESOLVED_NO_LINK,
  RESOLVED_NO_STORE,
  RESOLVED_NOT_WRITTEN,
  RESOLVED_RUNNING,
  RESOLVED_THIS_PROJECT,
  RESOLVED_UNUSABLE,
  readSessionsOf,
  resolvePaneTarget,
  SESSIONS_SCHEMA,
  sessionsPathFor,
} from "../src/main/sessions";
import { TMUX_ENV } from "../src/main/terminal";
import type { ClaudePane, StoreChangeKind } from "../src/shared/protocol";
import {
  closeReadyApp,
  type Fixture,
  MACOS_NO_RESTORE,
  makeFixture,
  markReadyToClose,
  removeFixture,
  suiteEnv,
  windowShowArg,
} from "./fixture";

/** Playwright transpiles this spec to CJS, so `__dirname` finds the app root. */
const APP_ROOT = join(__dirname, "..");

const ULID = "01AbCdEfGhJkMnPqRsTvWxYz";
const OTHER_ULID = "01ZyXwVtSrQpNmKjHgFeDcBa";

/** A transcript carrying the harness's own `bridge-session` record shape. */
function writeTranscript(fixture: Fixture, name: string, ulid: string | null): string {
  const path = join(fixture.base, name);
  const lines = [JSON.stringify({ type: "user", message: { content: "hello" } })];
  if (ulid !== null) {
    lines.push(
      JSON.stringify({
        type: "bridge-session",
        sessionId: "local",
        bridgeSessionId: `cse_${ulid}`,
        lastSequenceNum: 0,
        // Present because a real record carries them. Their presence here is what
        // makes the "nothing reaches the export" check below a real one.
        ownerAccountUuid: "acc0acc0-0000-4000-8000-000000000001",
        ownerOrganizationUuid: "0a90a90a-0000-4000-8000-000000000002",
      }),
    );
  }
  writeFileSync(path, `${lines.join("\n")}\n`, "utf8");
  return path;
}

/**
 * Fire one session hook at the fixture's store, through the real CLI.
 *
 * `execFileSync` with `input` rather than `runKyo`, because the payload arrives on
 * STDIN and the app's own runner has no stdin: the app never invokes this command,
 * the hooks do. Driving the real one is the point -- it is what makes the document
 * this reader parses the document the writer writes.
 */
function fireHook(
  fixture: Fixture,
  payload: { session_id: string; hook_event_name: string; cwd?: string; transcript_path?: string },
): void {
  const { program, baseArgs } = fixture.resolution.source;
  if (program === null) throw new Error("no runnable kyo for this check");
  execFileSync(program, [...baseArgs, "sessions", "--hook"], {
    cwd: fixture.root,
    input: JSON.stringify(payload),
    encoding: "utf8",
  });
}

/**
 * Where the pane points for `f`'s project, the way the app asks since KYO-459: the
 * sessions through the project's kyo serve (`kyo sessions --json`), then the pure
 * resolution. `projectRoot` is the directory a session must be in to be this pane's.
 */
async function paneTarget(f: Fixture, projectRoot: string | null = f.root) {
  return resolvePaneTarget(f.root, projectRoot, await readSessionsOf(f.project));
}

let fixture: Fixture;

test.beforeAll(async () => {
  fixture = await makeFixture(1);
});

test.afterAll(() => {
  removeFixture(fixture);
});

// ---------------------------------------------------------------- the good case

test("the pane opens on the session running in this project's own directory", async () => {
  const transcript = writeTranscript(fixture, "here.jsonl", ULID);
  fireHook(fixture, {
    session_id: "session-here",
    hook_event_name: "SessionStart",
    cwd: fixture.root,
    transcript_path: transcript,
  });

  const target = await paneTarget(fixture);
  expect(target.outcome).toBe(RESOLVED_THIS_PROJECT);
  expect(target.url).toBe(`https://claude.ai/code/session_${ULID}`);
  expect(target.deepLinked).toBe(true);
  // Law 6: the verdict names its subject on every resolution, not only on failure.
  expect(target.why).toContain(fixture.root);
});

test("a session in another directory still resolves, and says which", async () => {
  const transcript = writeTranscript(fixture, "elsewhere.jsonl", OTHER_ULID);
  fireHook(fixture, {
    session_id: "session-elsewhere",
    hook_event_name: "SessionStart",
    cwd: "/somewhere/else",
    transcript_path: transcript,
  });

  // Asked as though the app had no project directory to match against -- the
  // cross-host case, where Claude Code runs on a machine this app is not on.
  const target = await paneTarget(fixture, null);
  expect(target.outcome).toBe(RESOLVED_RUNNING);
  expect(target.deepLinked).toBe(true);
  expect(target.why).toContain("/somewhere/else");
});

test("a session that has ended is used only when none is running", async () => {
  fireHook(fixture, {
    session_id: "session-here",
    hook_event_name: "SessionEnd",
    cwd: fixture.root,
  });
  fireHook(fixture, {
    session_id: "session-elsewhere",
    hook_event_name: "SessionEnd",
    cwd: "/somewhere/else",
  });

  const target = await paneTarget(fixture);
  expect(target.outcome).toBe(RESOLVED_ENDED);
  expect(target.deepLinked).toBe(true);
  // KYO-186.c7 from the app's side: an ended session is still *readable* as ended.
  // If `SessionEnd` did not mark its own entry, this would still say "running".
  expect(target.why).toContain("no session is running here");
});

test("no account or organization identifier reaches the file the app reads", async () => {
  // KYO-186.c5 checked where the file actually is, against a transcript that
  // really does carry both identifiers.
  const written = await readSessionsOf(fixture.project);
  expect(written.entries.length).toBeGreaterThan(0);
  const raw = JSON.stringify(written);
  for (const forbidden of ["ownerAccountUuid", "ownerOrganizationUuid", "acc0acc0", "0a90a90a"]) {
    expect(raw).not.toContain(forbidden);
  }
});

test("nothing this feature writes lands in the git-canonical export", () => {
  // The trap KYO-186 names as most likely to be skipped, because nothing fails
  // when you skip it: `kyosei/export/` is what this project publishes.
  //
  // Asserted as "no export file mentions anything this feature touched" rather than
  // "the export directory is clean" -- the fixture's own `kyo new` writes records
  // there, so a cleanliness check would be about the fixture and would pass whether
  // or not this feature leaked. Several hooks have fired against this store by now,
  // one of them carrying both owner identifiers.
  const exportDir = join(fixture.root, "kyosei", "export");
  const leaked: string[] = [];
  // Recursive since KYO-268: the events ledger is a directory of shards
  // (`_events/000001.jsonl`, ...), and a shard is exactly where a hook's payload
  // would land, so a scan that stopped at the top level would read nothing of it.
  for (const name of readdirSync(exportDir, { recursive: true }) as string[]) {
    const path = join(exportDir, name);
    if (statSync(path).isDirectory()) continue;
    const body = readFileSync(path, "utf8");
    for (const forbidden of [
      ULID,
      OTHER_ULID,
      "cse_",
      "bridgeSessionId",
      "ownerAccountUuid",
      "acc0acc0",
      "claude.ai/code/session_",
    ]) {
      if (body.includes(forbidden)) leaked.push(`${name}: ${forbidden}`);
    }
  }
  expect(leaked).toEqual([]);
  // And the file itself is beside the database, not under the export.
  expect(sessionsPathFor(fixture.root)).toBe(join(fixture.root, "kyosei", "sessions.json"));
  expect(sessionsPathFor(fixture.root).startsWith(exportDir)).toBe(false);
});

// ------------------------------------------------------- every degradation (c4)

test("no store open falls through to the account's own session list", () => {
  const target = resolvePaneTarget(null, "/anywhere", null);
  expect(target.outcome).toBe(RESOLVED_NO_STORE);
  expect(target.url).toBe(CLAUDE_INDEX_URL);
  expect(target.deepLinked).toBe(false);
});

test("a store no session hook has run in falls through, and names the remedy", async () => {
  const fresh = await makeFixture(1);
  try {
    const target = await paneTarget(fresh);
    expect(target.outcome).toBe(RESOLVED_NOT_WRITTEN);
    expect(target.url).toBe(CLAUDE_INDEX_URL);
    // The project's standing rule: an error naming a remedy is worth nothing if the
    // remedy is not named. This one names `kyo install-hooks`.
    expect(target.why).toContain("kyo install-hooks");
  } finally {
    removeFixture(fresh);
  }
});

test("an unreadable file is a NAMED outcome, never a guess", async () => {
  const broken = await makeFixture(1);
  try {
    const path = sessionsPathFor(broken.root);
    mkdirSync(join(broken.root, "kyosei"), { recursive: true });
    writeFileSync(path, "{ this is not json", "utf8");
    const target = await paneTarget(broken);
    // The whole discipline in one assertion. `file-unusable` and `not-written` have
    // the same consequence for the pane and completely different causes, and a
    // user whose pane will not deep-link needs to know which one they have.
    expect(target.outcome).toBe(RESOLVED_UNUSABLE);
    expect(target.outcome).not.toBe(RESOLVED_NOT_WRITTEN);
    expect(target.url).toBe(CLAUDE_INDEX_URL);
    expect(target.why).toContain(path);
  } finally {
    removeFixture(broken);
  }
});

test("a document from a schema this build does not read is refused, not guessed at", async () => {
  const future = await makeFixture(1);
  try {
    mkdirSync(join(future.root, "kyosei"), { recursive: true });
    writeFileSync(
      sessionsPathFor(future.root),
      JSON.stringify({
        schema: SESSIONS_SCHEMA + 1,
        sessions: [
          {
            session_id: "s",
            state: "running",
            last_seen: "2026-01-01T00:00:00.000Z",
            url: `https://claude.ai/code/session_${ULID}`,
          },
        ],
      }),
      "utf8",
    );
    const target = await paneTarget(future);
    // The entry LOOKS usable. Reading it anyway is how a pane comes to open
    // confidently on the wrong page when the field meanings have moved under it.
    expect(target.outcome).toBe(RESOLVED_UNUSABLE);
    expect(target.deepLinked).toBe(false);
    expect(target.why).toContain(String(SESSIONS_SCHEMA));
  } finally {
    removeFixture(future);
  }
});

test("a file naming only sessions with no Remote Control page falls through", async () => {
  const plain = await makeFixture(1);
  try {
    const transcript = writeTranscript(plain, "no-bridge.jsonl", null);
    fireHook(plain, {
      session_id: "not-connected",
      hook_event_name: "SessionStart",
      cwd: plain.root,
      transcript_path: transcript,
    });
    const target = await paneTarget(plain);
    expect(target.outcome).toBe(RESOLVED_NO_LINK);
    expect(target.url).toBe(CLAUDE_INDEX_URL);
  } finally {
    removeFixture(plain);
  }
});

test("a URL that is not a claude.ai page is dropped rather than navigated to", async () => {
  const hostile = await makeFixture(1);
  try {
    mkdirSync(join(hostile.root, "kyosei"), { recursive: true });
    writeFileSync(
      sessionsPathFor(hostile.root),
      JSON.stringify({
        schema: SESSIONS_SCHEMA,
        sessions: [
          {
            session_id: "s",
            state: "running",
            last_seen: "2026-01-01T00:00:00.000Z",
            url: "https://claude.ai.example.invalid/code/session_x",
          },
        ],
      }),
      "utf8",
    );
    // The view carries a persistent logged-in session and the file is written by a
    // hook reading an undocumented format, possibly on another machine. A page this
    // build does not recognise costs the deep link and nothing else.
    const target = await paneTarget(hostile);
    expect(target.outcome).toBe(RESOLVED_NO_LINK);
    expect(target.url).toBe(CLAUDE_INDEX_URL);
  } finally {
    removeFixture(hostile);
  }
});

test("the claude.ai guard accepts the real page and refuses the near misses", () => {
  expect(isClaudeUrl(`https://claude.ai/code/session_${ULID}`)).toBe(true);
  expect(isClaudeUrl(CLAUDE_INDEX_URL)).toBe(true);
  for (const bad of [
    "http://claude.ai/code",
    "https://claude.ai.example.invalid/code",
    "https://notclaude.ai/code",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "not a url at all",
  ]) {
    expect(isClaudeUrl(bad), bad).toBe(false);
  }
});

// ------------------------------------------------------ following a switch (c9)

test("the pane re-resolves against whichever project it is asked about", async () => {
  // KYO-186.c9's mechanism, at the level where it is decided. `resolvePaneTarget`
  // is pure and takes the store root as an argument, so "the pane follows a project
  // switch" reduces to "the app asks again with the other store root" -- which
  // `projectMoved` does, beside `endSessions`, on every switch.
  //
  // A cached answer is the one change that would kill c9 while every other check
  // still passed, and this is what fails if somebody makes one.
  //
  // Two FRESH projects rather than the shared fixture: this check is about two
  // stores disagreeing, so it must not be able to pass or fail on what some earlier
  // check happened to leave in a store they share.
  const alpha = await makeFixture(1);
  const beta = await makeFixture(1);
  try {
    fireHook(alpha, {
      session_id: "alpha-controller",
      hook_event_name: "SessionStart",
      cwd: alpha.root,
      transcript_path: writeTranscript(alpha, "alpha.jsonl", ULID),
    });
    fireHook(beta, {
      session_id: "beta-controller",
      hook_event_name: "SessionStart",
      cwd: beta.root,
      transcript_path: writeTranscript(beta, "beta.jsonl", OTHER_ULID),
    });

    const first = await paneTarget(alpha);
    const second = await paneTarget(beta);

    expect(first.url).toBe(`https://claude.ai/code/session_${ULID}`);
    expect(second.url).toBe(`https://claude.ai/code/session_${OTHER_ULID}`);
    expect(second.outcome).toBe(RESOLVED_THIS_PROJECT);
    expect(second.url).not.toBe(first.url);
    expect(second.storeRoot).toBe(beta.root);

    // And back again in the same process, with no state to reset: the reader holds
    // nothing between calls, which is what makes the switch a re-read rather than
    // an invalidation somebody has to remember to perform.
    expect((await paneTarget(alpha)).url).toBe(first.url);
  } finally {
    removeFixture(alpha);
    removeFixture(beta);
  }
});

// ------------------------------------------ following a new session (KYO-382)
//
// A common daily loop: open the app, start `claude` in the terminal, open the
// claude tab -- and find it showing YESTERDAY's session, because the pane resolved its
// target when it was first shown and nothing re-asked when the new session's
// SessionStart hook wrote `sessions.json` a moment later. The ranking was never the
// defect (`resolvePaneTarget` above ranks the new one first); the missing re-resolve
// was. These two checks are the re-resolve, locally and through a dev box.

const SESSION_URL = `https://claude.ai/code/session_${ULID}`;
const OTHER_SESSION_URL = `https://claude.ai/code/session_${OTHER_ULID}`;

/** What the main process pushes on `claudeState`, collected in the page. */
interface Collected {
  __kyo382States: ClaudePane[];
}

test("a controller session started while the pane is open moves the pane to it, and a write that leaves the answer alone does not (KYO-382.c1, c2)", async () => {
  test.setTimeout(150_000);
  const live = await makeFixture(1);
  const profile = mkdtempSync(join(tmpdir(), "kyosei claude-pane profile "));
  let app: ElectronApplication | null = null;
  try {
    // Yesterday's controller, the session the pane opens on.
    fireHook(live, {
      session_id: "yesterday",
      hook_event_name: "SessionStart",
      cwd: live.root,
      transcript_path: writeTranscript(live, "yesterday.jsonl", ULID),
    });

    const env = suiteEnv({
      KYOSEI_PROJECT_ROOT: live.root,
      KYOSEI_HOME: live.home,
      [TMUX_ENV]: "off",
      // A display configuration nothing has arranged a layout on.
      KYOSEI_DISPLAYS: JSON.stringify([
        { id: 382_382, bounds: { x: 0, y: 0, width: 1400, height: 900 } },
      ]),
    });
    app = await _electron.launch({
      args: [APP_ROOT, `--user-data-dir=${profile}`, ...MACOS_NO_RESTORE, windowShowArg()],
      env,
    });
    const page = await app.firstWindow();
    await page.waitForSelector('body[data-ready="true"]');
    markReadyToClose(app);
    // The facts read is what arms this project's store watch (KYO-310), and the
    // watch is what hears the hook -- so nothing is written before it is armed.
    await page.waitForSelector('body[data-facts="ready"]');

    await page.evaluate(() => {
      const collected = window as unknown as Collected;
      collected.__kyo382States = [];
      window.kyosei.onClaudeState((state) => collected.__kyo382States.push(state));
    });
    const pushed = () =>
      page.evaluate(() => (window as unknown as Collected).__kyo382States.slice());

    const first = await page.evaluate(() =>
      window.kyosei.showClaude({ x: 0, y: 0, width: 200, height: 120 }),
    );
    expect(first.outcome).toBe(RESOLVED_THIS_PROJECT);
    expect(first.url).toBe(SESSION_URL);

    // Every navigation the APP asks of the pane from here on. `loadURL` is the only
    // way the main process moves the view, so counting its calls counts exactly the
    // thing c2 is about -- whereas the page's own navigations (claude.ai redirecting a
    // logged-out view to its sign-in page) are the site's, and would make a count of
    // `did-start-navigation` a count of somebody else's behaviour.
    await app.evaluate(({ BrowserWindow, webContents }) => {
      const windowContents = new Set(BrowserWindow.getAllWindows().map((w) => w.webContents.id));
      const views = webContents.getAllWebContents().filter((wc) => !windowContents.has(wc.id));
      const view = views[0];
      if (views.length !== 1 || view === undefined) {
        throw new Error(`expected one claude view, found ${views.length}`);
      }
      const loads: string[] = [];
      (globalThis as unknown as { __kyo382Loads: string[] }).__kyo382Loads = loads;
      const original = view.loadURL.bind(view);
      view.loadURL = (url, options) => {
        loads.push(url);
        return original(url, options);
      };
    });
    const loads = () =>
      app?.evaluate(() => (globalThis as unknown as { __kyo382Loads: string[] }).__kyo382Loads);

    // c1: today's controller starts -- its SessionStart hook writes the file while the
    // pane is open on this project. No reload, no project switch.
    fireHook(live, {
      session_id: "today",
      hook_event_name: "SessionStart",
      cwd: live.root,
      transcript_path: writeTranscript(live, "today.jsonl", OTHER_ULID),
    });
    await expect
      .poll(async () => (await pushed()).at(-1)?.url, { timeout: 15_000 })
      .toBe(OTHER_SESSION_URL);
    const moved = (await pushed()).at(-1);
    expect(moved?.outcome).toBe(RESOLVED_THIS_PROJECT);
    expect(moved?.why).toContain("today");
    // ...and the pane itself was sent there, once.
    await expect.poll(loads, { timeout: 5_000 }).toEqual([OTHER_SESSION_URL]);

    // c2: a write that leaves the answer where it was. `Stop` fires at the end of every
    // turn and rewrites today's entry with a newer `last_seen` -- the file moves, the
    // resolved page does not. Quiet first, so a late push from c1 is not taken for this.
    await new Promise((resolve) => setTimeout(resolve, 1_000));
    const before = (await pushed()).length;
    const fileBefore = readFileSync(sessionsPathFor(live.root), "utf8");
    fireHook(live, {
      session_id: "today",
      hook_event_name: "Stop",
      cwd: live.root,
      transcript_path: join(live.base, "today.jsonl"),
    });
    expect(readFileSync(sessionsPathFor(live.root), "utf8")).not.toBe(fileBefore);
    // The write WAS heard -- the pane re-resolved and says so -- so the absence of a
    // navigation below is a decision, not a watch that never fired.
    await expect
      .poll(async () => (await pushed()).length, { timeout: 15_000 })
      .toBeGreaterThan(before);
    const after = (await pushed()).at(-1);
    expect(after?.url).toBe(OTHER_SESSION_URL);
    expect(await loads()).toEqual([OTHER_SESSION_URL]);
  } finally {
    if (app !== null) await closeReadyApp(app);
    removeFixture(live);
    rmSync(profile, { recursive: true, force: true });
  }
});

test("a dev box's kyo serve pushes its sessions.json change to a connected laptop, which resolves the new session there (KYO-382, KYO-459)", async () => {
  test.setTimeout(120_000);
  // Electron-free: the dev box's half is a kyo serve over its store, and the laptop's
  // half is the client every window reads through -- the same stream a remote window's
  // `armStoreWatch` follows and the same read its `resolveClaudeTarget` makes.
  const box = await makeFixture(1);
  const laptop = clientFor(box.serve.address).project(box.root);
  const kinds: StoreChangeKind[] = [];
  const stop = laptop.subscribe((kind) => kinds.push(kind));
  const target = async () => resolvePaneTarget(box.root, box.root, await readSessionsOf(laptop));
  try {
    // The stream opens with the server's first fingerprint; a change after that is an
    // event. Each `Stop` rewrites the file, so the first change is repeated until heard.
    fireHook(box, {
      session_id: "yesterday",
      hook_event_name: "SessionStart",
      cwd: box.root,
      transcript_path: writeTranscript(box, "box-yesterday.jsonl", ULID),
    });
    const until = Date.now() + 30_000;
    while (!kinds.includes("sessions") && Date.now() < until) {
      fireHook(box, {
        session_id: "yesterday",
        hook_event_name: "Stop",
        cwd: box.root,
        transcript_path: join(box.base, "box-yesterday.jsonl"),
      });
      await new Promise((resolve) => setTimeout(resolve, 750));
    }
    expect(kinds).toContain("sessions");
    // A session write is not a store write: the sidebar's lists do not reload for it.
    expect(kinds).not.toContain("store");
    expect((await target()).url).toBe(SESSION_URL);

    await new Promise((resolve) => setTimeout(resolve, 1_500));
    const heard = kinds.length;
    fireHook(box, {
      session_id: "today",
      hook_event_name: "SessionStart",
      cwd: box.root,
      transcript_path: writeTranscript(box, "box-today.jsonl", OTHER_ULID),
    });
    await expect.poll(() => kinds.length, { timeout: 10_000 }).toBeGreaterThan(heard);
    expect(kinds.at(-1)).toBe("sessions");
    const there = await target();
    expect(there.url).toBe(OTHER_SESSION_URL);
    expect(there.outcome).toBe(RESOLVED_THIS_PROJECT);
  } finally {
    stop();
    removeFixture(box);
  }
});
