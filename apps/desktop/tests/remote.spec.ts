// Connect to a dev box (KYO-370, KYO-374, KYO-459), against two real kyo serves.
//
// Since KYO-459 another machine's projects are read through ITS kyo serve, and a
// connection is an address and nothing else: no password, no token, no certificate to
// trust (the tailnet's access rule is the boundary, and Tailscale Serve's certificate is
// one the system already trusts). So the DEV BOX here is a second `kyo serve`, over fixture
// A's store home, on its own loopback port -- the app treats any server that is not its
// own as another machine's, whatever the address -- and it also serves fixture C. The
// CLIENT is the app, whose own kyo serve (this machine's) serves fixture B, and which
// connects to the dev box through `window.kyosei.connectRemote` -- the same call the
// rail's "Connect to a dev box…" dialog makes -- and from then on reads and writes
// fixture A's store through it, never touching it on disk itself.
//
//   KYO-370.c1  the switcher connects; records, ideas, scratchpad, docs and activity
//               come from the dev box's store
//   KYO-370.c2  a scratchpad or idea edit lands in the dev box's files
//   KYO-370.c3  the claude tab resolves against the dev box's `sessions.json`
//   KYO-370.c4  (reversed by KYO-474) the terminal reaches the dev box over ssh, and
//               says in ssh's own words when it cannot
//   KYO-370.c5  an unreachable server or an address that is not one is refused where
//               the connection was made, naming the address
//   KYO-374     remembered projects, the caption, windows on the dev box, layouts apart
//   KYO-379     the dev box's list is its registry's, read afresh; kyo serve adds none
//   KYO-387/389 a dev box that went away is said by one sentence, never an IPC frame
//   KYO-463     a link followed in a window on the dev box opens the dev box's project,
//               never this machine's that shares its prefix
//
// Every fixture path contains a space (see `fixture.ts`).

import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron, type ElectronApplication, expect, type Page, test } from "@playwright/test";
import { clientFor } from "../src/main/kyoserve";
import { REMOTES_FILE } from "../src/main/remote";
import { RESOLVED_THIS_PROJECT, readSessionsOf, resolvePaneTarget } from "../src/main/sessions";
import { TMUX_ENV } from "../src/main/terminal";
import {
  closeReadyApp,
  type Fixture,
  kyo,
  MACOS_NO_RESTORE,
  makeFixture,
  markReadyToClose,
  openKind,
  removeFixture,
  scratchpadPathFor,
  seedIdeas,
  serveAlso,
  startKyoServe,
  stopKyoServe,
  suiteEnv,
  useServe,
  windowShowArg,
} from "./fixture";

const APP_ROOT = join(__dirname, "..");
const LOCAL_HOST = "127.0.0.1";

test.describe.configure({ mode: "serial" });

let client: ElectronApplication | null = null;
let clientPage: Page;
/** The dev box's project, and the second one it serves (KYO-374.c4). */
let fixtureA: Fixture;
let fixtureC: Fixture;
/** This machine's project, served by the app's own kyo serve. */
let fixtureB: Fixture;
let clientProfile: string;
/** The dev box's kyo serve: its port, and the address the dialog takes. */
let devBox: { port: number; address: string };

/** A ULID-shaped bridge session id, so the dev box's `sessions.json` deep-links. */
const ULID = "01AbCdEfGhJkMnPqRsTvWxYz";

function writeTranscript(fixture: Fixture, name: string): string {
  const path = join(fixture.base, name);
  const lines = [
    JSON.stringify({ type: "user", message: { content: "hello" } }),
    JSON.stringify({
      type: "bridge-session",
      sessionId: "local",
      bridgeSessionId: `cse_${ULID}`,
      lastSequenceNum: 0,
    }),
  ];
  writeFileSync(path, `${lines.join("\n")}\n`, "utf8");
  return path;
}

/** Fire one session hook at a fixture's store, through the real CLI. */
function fireHook(
  fixture: Fixture,
  payload: { session_id: string; hook_event_name: string; cwd: string; transcript_path: string },
): void {
  const { program, baseArgs } = fixture.resolution.source;
  if (program === null) throw new Error("no runnable kyo for this check");
  execFileSync(program, [...baseArgs, "sessions", "--hook"], {
    cwd: fixture.root,
    input: JSON.stringify(payload),
    encoding: "utf8",
    // The fixture's own home, so the hook writes where that fixture's server reads.
    env: suiteEnv({ KYOSEI_HOME: fixture.home }),
  });
}

/** Launch the app on this machine's project, in a data directory of its own. */
async function launch(
  fixture: Fixture,
  profile: string,
  fresh = true,
): Promise<{ app: ElectronApplication; page: Page }> {
  // `fresh: false` launches on whatever the profile's last session remembered (KYO-465).
  if (fresh) {
    writeFileSync(
      join(profile, "project.json"),
      JSON.stringify({ chosen: fixture.root, recents: [fixture.root] }),
      "utf8",
    );
  }
  const env = suiteEnv();
  delete env.KYOSEI_PROJECT_ROOT;
  env.KYOSEI_HOME = fixture.home;
  env[TMUX_ENV] = "off";
  env.KYOSEI_LOGIN_SHELL = "off";
  const app = await _electron.launch({
    args: [APP_ROOT, `--user-data-dir=${profile}`, ...MACOS_NO_RESTORE, windowShowArg()],
    env,
  });
  const page = await app.firstWindow();
  await page.waitForSelector('body[data-ready="true"]');
  await page.waitForSelector('body[data-facts="ready"]');
  markReadyToClose(app);
  return { app, page };
}

/** Every file under `dir`, recursively, whose text contains `needle`. */
function filesContaining(dir: string, needle: string, found: string[] = []): string[] {
  if (!existsSync(dir)) return found;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === ".git") continue;
      filesContaining(path, needle, found);
    } else if (entry.isFile()) {
      let text = "";
      try {
        text = readFileSync(path, "utf8");
      } catch {
        continue;
      }
      if (text.includes(needle)) found.push(path);
    }
  }
  return found;
}

/** A loopback port nothing listens on: bound once by the OS and released. */
async function closedPort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.on("error", reject);
    server.listen(0, LOCAL_HOST, () => {
      const address = server.address();
      const port = typeof address === "object" && address !== null ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

async function rows(page: Page, list: string) {
  const sidebar = page.locator('[data-region="sidebar"]');
  if ((await sidebar.getAttribute("data-list")) !== list) {
    await page.locator(`[data-rail-item="${list}"]`).click();
  }
  await expect(sidebar).toHaveAttribute("data-list", list);
  await expect(page.locator('[data-region="sidebar-loading"]')).toHaveCount(0, {
    timeout: 60_000,
  });
  return page.locator('[data-region="sidebar-row"]');
}

test.beforeAll(async () => {
  test.setTimeout(180_000);
  // The dev box: A, with C beside it in the same registry (so the one kyo serve over
  // A's home serves both). Distinct directory names, because a registry refuses two
  // projects with one name.
  fixtureA = await makeFixture({ records: 0 });
  fixtureC = await makeFixture({ records: 0, dir: "c project" });
  fixtureB = await makeFixture({ records: 0, dir: "b project" });
  await serveAlso(fixtureA, fixtureC);
  await kyo(fixtureA.resolution, ["new", "bug", "--title", "Only on the dev box"], fixtureA.root);
  await kyo(fixtureB.resolution, ["new", "bug", "--title", "Only on the laptop"], fixtureB.root);
  await kyo(
    fixtureC.resolution,
    ["new", "bug", "--title", "Only in the second dev box project"],
    fixtureC.root,
  );
  seedIdeas(fixtureA);
  // A controller session on the dev box, so its `sessions.json` names one (c3).
  fireHook(fixtureA, {
    session_id: "dev-box-controller",
    hook_event_name: "SessionStart",
    cwd: fixtureA.root,
    transcript_path: writeTranscript(fixtureA, "dev box.jsonl"),
  });
  devBox = { port: fixtureA.serve.address.port, address: fixtureA.serve.url };

  // The laptop: this machine's kyo serve is B's.
  useServe(fixtureB);
  clientProfile = mkdtempSync(join(tmpdir(), "kyosei client profile "));
  const laptop = await launch(fixtureB, clientProfile);
  client = laptop.app;
  clientPage = laptop.page;
});

test.afterAll(async () => {
  if (client !== null) await closeReadyApp(client);
  client = null;
  removeFixture(fixtureA);
  removeFixture(fixtureB);
  removeFixture(fixtureC);
  rmSync(clientProfile, { recursive: true, force: true });
});

// ------------------------------------------------------------------ KYO-370.c5

test("an address nothing answers at, and one that is not an address of a kyo serve, are refused by name where the connection was made (KYO-370.c5)", async () => {
  test.setTimeout(120_000);
  const page = clientPage;
  const before = await page.evaluate(() => window.kyosei.getFacts());
  expect(before.project.root).toBe(fixtureB.root);
  expect(before.project.remote).toBeNull();

  const nobody = await closedPort();
  const closed = await page.evaluate((input) => window.kyosei.connectRemote(input), {
    address: `${LOCAL_HOST}:${nobody}`,
  });
  expect(closed.refused, JSON.stringify(closed)).not.toBeNull();
  expect(closed.refused?.root).toBe(`http://${LOCAL_HOST}:${nobody}`);
  expect(closed.refused?.problem).toContain("connection refused");
  expect(closed.refused?.problem).toContain(`http://${LOCAL_HOST}:${nobody}`);
  expect(closed.changed).toBe(false);

  // A plain-HTTP LAN address is not a kyo serve's: refused before anything is sent.
  const lan = await page.evaluate((input) => window.kyosei.connectRemote(input), {
    address: "http://192.168.1.20:47370",
  });
  expect(lan.refused?.problem).toContain("Tailscale Serve");

  // Through the dialog itself: the refusal is shown IN the dialog, which stays open
  // with what was typed, rather than the window moving onto an empty store.
  await page.locator('[data-region="project-switcher"]').click();
  await page.locator('[data-menu-item="Connect to a dev box…"]').click();
  const dialog = page.locator('[data-region="remote-dialog"]');
  await expect(dialog).toBeVisible();
  // It asks for an address and the SSH host, and nothing else: no password, no token.
  await expect(page.locator('[data-region="remote-address"]')).toBeVisible();
  await expect(page.locator('[data-region="remote-ssh-host"]')).toBeVisible();
  await expect(dialog.locator('input[type="password"]')).toHaveCount(0);
  await page.locator('[data-region="remote-address"]').fill(`${LOCAL_HOST}:${nobody}`);
  await page.locator('[data-region="remote-connect"]').click();
  const problem = page.locator('[data-region="remote-problem"]');
  await expect(problem).toHaveAttribute("data-state", "refused");
  await expect(problem).toContainText("connection refused");
  // The sentence alone, with no IPC frame (KYO-387).
  expect((await problem.textContent()) ?? "").not.toMatch(/Error:|invoking remote method|Refusal:/);
  await expect(dialog).toBeVisible();
  await page.locator('[data-region="remote-cancel"]').click();
  await expect(dialog).toHaveCount(0);

  const after = await page.evaluate(() => window.kyosei.getFacts());
  expect(after.project.root).toBe(fixtureB.root);
  expect(after.project.remote).toBeNull();
});

// ------------------------------------------------------------------ KYO-370.c1

test("the switcher connects to the dev box's kyo serve and every list reads its store; remotes.json holds the address and no secret (KYO-370.c1)", async () => {
  test.setTimeout(180_000);
  const page = clientPage;
  const choice = await page.evaluate((input) => window.kyosei.connectRemote(input), {
    address: devBox.address,
  });
  expect(choice.refused, JSON.stringify(choice.refused)).toBeNull();
  expect(choice.changed).toBe(true);
  expect([fixtureA.root, fixtureC.root]).toContain(choice.project.root);
  // The first it serves unless one is picked; pick A for what follows.
  const onA = await page.evaluate((input) => window.kyosei.connectRemote(input), {
    address: devBox.address,
    root: fixtureA.root,
  });
  expect(onA.refused, JSON.stringify(onA.refused)).toBeNull();
  expect(onA.project.root).toBe(fixtureA.root);
  expect(onA.project.remote).toEqual({ host: LOCAL_HOST, port: devBox.port });
  expect(onA.project.source).toBe("remote");
  expect(onA.project.project).toBe("FIX");
  expect(onA.project.storeRoot).toBe(fixtureA.root);

  await expect(page.locator('[data-region="project-name"]')).toHaveText("FIX");
  await expect(page.locator('[data-region="project-host"]')).toHaveText(LOCAL_HOST);
  const facts = await page.evaluate(() => window.kyosei.getFacts());
  expect(facts.project.root).toBe(fixtureA.root);
  expect(facts.serve).toMatchObject({ running: true, local: false, address: devBox.address });
  expect(facts.store.ok).toBe(true);
  expect(facts.store.storeRoot).toBe(fixtureA.root);

  const records = await rows(page, "records");
  await expect(records.filter({ hasText: "Only on the dev box" })).toHaveCount(1);
  await expect(records.filter({ hasText: "Only on the laptop" })).toHaveCount(0);

  const ideas = await rows(page, "ideas");
  await expect(ideas.filter({ hasText: "a first idea" })).toHaveCount(1);
  const tree = await page.evaluate(() => window.kyosei.getIdeaTree());
  expect(tree.ok).toBe(true);
  expect(tree.root).toBe(join(fixtureA.root, "ideas"));

  const scratchpad = await page.evaluate(() => window.kyosei.getScratchpad());
  expect(scratchpad.ok, scratchpad.problem ?? "").toBe(true);
  expect(scratchpad.path).toBe(scratchpadPathFor(fixtureA));
  const docs = await page.evaluate(() => window.kyosei.getDocTree());
  expect(docs.ok).toBe(true);
  const activity = await page.evaluate(() => window.kyosei.getActivity());
  expect(activity.ok).toBe(true);
  const briefs = await page.evaluate(() => window.kyosei.getBriefs());
  expect(briefs.ok).toBe(true);

  // Remembered like a local project choice, in the client's own data directory, in no
  // project: every project the dev box serves, the opened one first (KYO-385) -- the
  // address and the project, and nothing else, since there is no secret to keep.
  const remembered = JSON.parse(readFileSync(join(clientProfile, REMOTES_FILE), "utf8")) as {
    host: string;
    port: number;
    root: string;
    project: string | null;
  }[];
  const entry = (root: string) => ({ host: LOCAL_HOST, port: devBox.port, root, project: "FIX" });
  expect(remembered).toEqual([entry(fixtureA.root), entry(fixtureC.root)]);
  for (const retired of ["remote-tokens.json", "known-hosts.json"]) {
    expect(existsSync(join(clientProfile, retired)), retired).toBe(false);
  }
});

// ------------------------------------------------------------------ KYO-370.c2

test("a scratchpad or idea edit lands in the dev box's files and nowhere on the client (KYO-370.c2)", async () => {
  test.setTimeout(120_000);
  const page = clientPage;
  const marker = "written from the laptop through the dev box's kyo serve";
  // The save names the version the read answered (KYO-575): a tab's own, never one a
  // process keeps for every editor.
  const pad = await page.evaluate(() => window.kyosei.getScratchpad());
  const saved = await page.evaluate(
    ({ text, pad }) => window.kyosei.saveScratchpad({ text, base: pad.text, version: pad.version }),
    { text: marker, pad },
  );
  expect(saved.ok, saved.problem ?? "").toBe(true);
  expect(saved.path).toBe(scratchpadPathFor(fixtureA));
  await expect.poll(() => readFileSync(scratchpadPathFor(fixtureA), "utf8")).toContain(marker);

  const idea = await page.evaluate(
    (text) => window.kyosei.createIdea({ folder: "", name: "from-the-laptop", text }),
    marker,
  );
  expect(idea.ok, idea.problem ?? "").toBe(true);
  expect(idea.path).toBe(join(fixtureA.root, "ideas", "from-the-laptop.md"));
  expect(readFileSync(idea.path ?? "", "utf8")).toContain(marker);

  expect(filesContaining(fixtureB.base, marker)).toEqual([]);
  expect(filesContaining(clientProfile, marker)).toEqual([]);
});

// ------------------------------------------------------------------ KYO-467.c1

test("a dev box's document opens in the docs tab, read through its kyo serve (KYO-467.c1)", async () => {
  test.setTimeout(120_000);
  const page = clientPage;
  const onA = await page.evaluate((input) => window.kyosei.connectRemote(input), {
    address: devBox.address,
    root: fixtureA.root,
  });
  expect(onA.refused, JSON.stringify(onA.refused)).toBeNull();
  const text = "# On the dev box\n\nRead through its kyo serve, never this disk.\n";
  mkdirSync(join(fixtureA.root, "docs"), { recursive: true });
  writeFileSync(join(fixtureA.root, "docs", "dev-box.md"), text, "utf8");
  const tree = await page.evaluate(() => window.kyosei.getDocTree());
  expect(tree.ok, tree.refusal ?? "").toBe(true);
  expect(tree.documents.map((entry) => entry.path)).toContain("docs/dev-box.md");
  const doc = await page.evaluate((path) => window.kyosei.openDoc(path), "docs/dev-box.md");
  expect(doc.ok, doc.problem ?? "").toBe(true);
  expect(doc.text).toBe(text);
  // And the tab shows it: the docs list's row (found by what it opens, as docs.spec
  // does) opens a views tab whose body is the dev box's text.
  await rows(page, "docs");
  await page
    .locator('[data-region="sidebar-row"][data-row-arg="doc:docs/dev-box.md"]')
    .first()
    .click();
  const body = page.locator(
    '[data-region="tab-body"][data-tab-kind="views"][data-tab-arg="doc:docs/dev-box.md"]:not([hidden])',
  );
  await expect(body).toHaveAttribute("data-state", "ready");
  await expect(body).toContainText("Read through its kyo serve, never this disk.");
});

// ------------------------------------------------------------------ KYO-370.c4

test("a remote project's terminal reaches the dev box over ssh, and says so in ssh's own words when it cannot (KYO-370.c4, KYO-474.c4)", async () => {
  test.setTimeout(120_000);
  // KYO-474 reversed KYO-370.c4's "no terminal here": the pane is a client of the dev
  // box's controller session over the person's own SSH. This suite's ssh is
  // `/usr/bin/false` (suiteEnv), so the one thing to see here is the refusal, in ssh's
  // own words, with Reconnect -- the working path is `remote-terminal.spec.ts`'s.
  const page = clientPage;
  await openKind(page, "terminal");
  const host = page.locator('[data-region="tab-body"][data-tab-kind="terminal"]:not([hidden])');
  await expect(host).toHaveAttribute("data-terminal", "live", { timeout: 30_000 });
  await expect(host).toHaveAttribute("data-terminal-where", "ssh");
  await expect(host).toHaveAttribute("data-terminal-ssh-host", LOCAL_HOST);
  const offer = page.locator('[data-region="terminal-offer"]');
  await expect(offer).toBeVisible({ timeout: 30_000 });
  await expect(offer).toHaveAttribute("data-terminal-offer", "reconnect");
  await expect(offer).toContainText(`could not reach ${LOCAL_HOST}`);
  await expect(page.locator('[data-region="terminal-said"]')).toContainText(
    `ssh to ${LOCAL_HOST} exited with status 1 and printed nothing on stderr`,
  );
  // The "No terminal here" prose is for a dev box with no SSH host, which this is not.
  await expect(page.locator('[data-region="terminal-remote"]')).toHaveCount(0);
});

// ------------------------------------------------------------------ KYO-370.c3

test("the claude tab resolves against the dev box's sessions.json (KYO-370.c3)", async () => {
  test.setTimeout(120_000);
  const page = clientPage;
  const pane = await page.evaluate(() =>
    window.kyosei.showClaude({ x: 0, y: 0, width: 200, height: 120 }),
  );
  await page.evaluate(() => window.kyosei.hideClaude());
  // The same resolution, asked of the dev box's kyo serve from here.
  const there = resolvePaneTarget(
    fixtureA.root,
    fixtureA.root,
    await readSessionsOf(clientFor(fixtureA.serve.address).project(fixtureA.root)),
  );
  expect(there.outcome).toBe(RESOLVED_THIS_PROJECT);
  expect(pane.outcome).toBe(there.outcome);
  expect(pane.url).toBe(`https://claude.ai/code/session_${ULID}`);
  expect(pane.storeRoot).toBe(fixtureA.root);
  expect(pane.why).toBe(there.why);
});

// ------------------------------------------------------------------ KYO-463

test("a link followed in a window on the dev box opens the dev box's project, never this machine's with the same prefix (KYO-463.c2)", async () => {
  test.setTimeout(120_000);
  const page = clientPage;
  const app = client as ElectronApplication;
  const windows = () => app.windows().filter((w) => w.url().startsWith("file:"));
  const before = windows().length;
  // Both A (on the dev box) and B (this machine's) answer to FIX; FIX-1 exists in both
  // with different titles. Followed from this window -- the Activity tab's own path.
  await page.evaluate(() => window.kyosei.followLink("kyosei://FIX/FIX-1"));
  const tab = page.locator('[data-region="tab-body"][data-tab-kind="record"]:not([hidden])');
  await expect(tab).toContainText("Only on the dev box", { timeout: 20_000 });
  await expect(tab).not.toContainText("Only on the laptop");
  // Delivered here: no window was opened on this machine's copy.
  await page.waitForTimeout(500);
  expect(windows().length).toBe(before);
  // And it says which connection it chose, since two serve FIX.
  await expect(page.locator('[data-region="link-strip"]')).toContainText(devBox.address);
});

// ---------------------------------------------------------- and back again

test("opening a local project again leaves the dev box behind", async () => {
  test.setTimeout(120_000);
  const page = clientPage;
  const back = await page.evaluate((root) => window.kyosei.openProject(root), fixtureB.root);
  expect(back.refused, JSON.stringify(back.refused)).toBeNull();
  expect(back.changed).toBe(true);
  expect(back.project.remote).toBeNull();
  expect(back.project.root).toBe(fixtureB.root);
  const records = await rows(page, "records");
  await expect(records.filter({ hasText: "Only on the laptop" })).toHaveCount(1);
  await expect(records.filter({ hasText: "Only on the dev box" })).toHaveCount(0);
  await page.locator('[data-region="project-switcher"]').click();
  const label = `[data-menu-item="FIX @ ${LOCAL_HOST}:${devBox.port}"]`;
  await expect(page.locator(label)).toHaveCount(2);
  await expect(page.locator(`${label}[data-tip^="${fixtureC.root}"]`)).toHaveCount(1);
  await page.keyboard.press("Escape");
});

// ------------------------------------------------------------------ KYO-465.c1

test("a relaunch reopens the window on the dev box's project it last showed, not this machine's project at the same root (KYO-465.c1)", async () => {
  test.setTimeout(180_000);
  // This machine's kyo serve serves the dev box's root too -- the laptop's old local
  // copy at the same path -- so the defect has a local project to fall back to.
  const profile = mkdtempSync(join(tmpdir(), "kyosei relaunch profile "));
  let run: { app: ElectronApplication; page: Page } | null = null;
  try {
    await serveAlso(fixtureB, fixtureA);
    run = await launch(fixtureB, profile);
    const moved = await run.page.evaluate((input) => window.kyosei.connectRemote(input), {
      address: devBox.address,
      root: fixtureA.root,
    });
    expect(moved.refused, JSON.stringify(moved.refused)).toBeNull();
    expect(moved.project.remote).toEqual({ host: LOCAL_HOST, port: devBox.port });
    await closeReadyApp(run.app);
    run = null;

    run = await launch(fixtureB, profile, false);
    const facts = await run.page.evaluate(() => window.kyosei.getFacts());
    expect(facts.project.root).toBe(fixtureA.root);
    expect(facts.project.remote).toEqual({ host: LOCAL_HOST, port: devBox.port });
    await expect(
      (await rows(run.page, "records")).filter({ hasText: "Only on the dev box" }),
    ).toHaveCount(1);
    // The recent it came from is labelled with that server, beside this machine's copy.
    await run.page.locator('[data-region="project-switcher"]').click();
    await expect(
      run.page.locator(`.menu-item[data-tip="${fixtureA.root}"]`),
      "this machine's copy at the same root is its own row",
    ).toHaveText("FIX @ this machine");
    await run.page.keyboard.press("Escape");
  } finally {
    if (run !== null) await closeReadyApp(run.app);
    rmSync(profile, { recursive: true, force: true });
    // This machine's registry as the checks after this one expect it: B's alone.
    const held = process.env.KYOSEI_HOME;
    process.env.KYOSEI_HOME = fixtureB.home;
    try {
      await kyo(fixtureB.resolution, ["projects", "remove", fixtureA.root], fixtureB.base);
    } finally {
      if (held === undefined) delete process.env.KYOSEI_HOME;
      else process.env.KYOSEI_HOME = held;
    }
  }
});

// ------------------------------------------------------------------ KYO-374.c4

test("the connect dialog lists the dev box's projects and opens the one picked, keeping the SSH host (KYO-374.c4, KYO-460)", async () => {
  test.setTimeout(120_000);
  const page = clientPage;
  const probe = await page.evaluate((input) => window.kyosei.probeRemote(input), {
    address: devBox.address,
  });
  expect(probe.ok, probe.problem ?? "").toBe(true);
  expect(probe.recents.map((entry) => entry.root)).toEqual([fixtureA.root, fixtureC.root]);
  expect(probe.recents.map((entry) => entry.project)).toEqual(["FIX", "FIX"]);
  expect((await page.evaluate(() => window.kyosei.getFacts())).project.root).toBe(fixtureB.root);

  await page.locator('[data-region="project-switcher"]').click();
  await page.locator('[data-menu-item="Connect to a dev box…"]').click();
  await page.locator('[data-region="remote-address"]').fill(devBox.address);
  await page.locator('[data-region="remote-ssh-host"]').fill("me@devbox");
  await page.locator('[data-region="remote-connect"]').click();
  const select = page.locator('[data-region="remote-project"]');
  await expect(select).toBeVisible();
  await expect(select.locator("option")).toHaveCount(2);
  await expect(page.locator('[data-region="remote-connect"]')).toHaveText("Open");
  await select.selectOption(fixtureC.root);
  await page.locator('[data-region="remote-connect"]').click();
  await expect(page.locator('[data-region="remote-dialog"]')).toHaveCount(0);

  const facts = await page.evaluate(() => window.kyosei.getFacts());
  expect(facts.project.root).toBe(fixtureC.root);
  expect(facts.project.remote).toEqual({ host: LOCAL_HOST, port: devBox.port });
  const records = await rows(page, "records");
  await expect(records.filter({ hasText: "Only in the second dev box project" })).toHaveCount(1);
  // The server entry carries the SSH host New project and Start controller use.
  const servers = await page.evaluate(() => window.kyosei.newProjectServers());
  expect(servers.find((entry) => entry.server?.port === devBox.port)?.sshHost).toBe("me@devbox");
});

// ------------------------------------------------------------------ KYO-374.c3

test("the rail caption shows the prefix and the dev box's host whole, the rest in the tooltip (KYO-374.c3)", async () => {
  const page = clientPage;
  await expect(page.locator('[data-region="project-name"]')).toHaveText("FIX");
  await expect(page.locator('[data-region="project-host"]')).toHaveText(LOCAL_HOST);
  const tip = await page.locator('[data-region="project-switcher"]').getAttribute("data-tip");
  expect(tip).toContain(fixtureC.root);
  expect(tip).toContain(`${LOCAL_HOST}:${devBox.port}`);
});

// ------------------------------------------------------------------ KYO-374.c1

test("New Window from a remote window opens on the same dev box, and a remembered dev box opens a window too (KYO-374.c1)", async () => {
  test.setTimeout(120_000);
  const page = clientPage;
  const app = client as ElectronApplication;
  const windows = () => app.windows().filter((w) => w.url().startsWith("file:"));
  const before = windows().length;

  const same = await page.evaluate(() => window.kyosei.openWindow());
  expect(same.refused, JSON.stringify(same.refused)).toBeNull();
  await expect.poll(() => windows().length).toBe(before + 1);
  const other = windows()
    .filter((w) => w !== page)
    .at(-1);
  if (other === undefined) throw new Error("no second window");
  await other.waitForSelector('body[data-facts="ready"]');
  const otherFacts = await other.evaluate(() => window.kyosei.getFacts());
  expect(otherFacts.project.root).toBe(fixtureC.root);
  expect(otherFacts.project.remote).toEqual({ host: LOCAL_HOST, port: devBox.port });

  const onA = await page.evaluate((input) => window.kyosei.openWindow(input.root, input), {
    host: LOCAL_HOST,
    port: devBox.port,
    root: fixtureA.root,
  });
  expect(onA.refused, JSON.stringify(onA.refused)).toBeNull();
  expect(onA.project.root).toBe(fixtureA.root);
  await expect.poll(() => windows().length).toBe(before + 2);
  const third = windows().filter((w) => w !== page && w !== other)[0];
  if (third === undefined) throw new Error("no third window");
  await third.waitForSelector('body[data-facts="ready"]');
  expect((await third.evaluate(() => window.kyosei.getFacts())).project.root).toBe(fixtureA.root);
  await third.close();
  await other.close();
  await expect.poll(() => windows().length).toBe(before);

  const fresh = await page.evaluate((input) => window.kyosei.connectRemote(input), {
    address: devBox.address,
    root: fixtureA.root,
    newWindow: true,
  });
  expect(fresh.refused, JSON.stringify(fresh.refused)).toBeNull();
  await expect.poll(() => windows().length).toBe(before + 1);
  const fourth = windows().find((w) => w !== page);
  if (fourth === undefined) throw new Error("no window from the chooser row");
  await fourth.waitForSelector('body[data-facts="ready"]');
  expect((await fourth.evaluate(() => window.kyosei.getFacts())).project.root).toBe(fixtureA.root);
  expect((await page.evaluate(() => window.kyosei.getFacts())).project.root).toBe(fixtureC.root);
  await fourth.close();
  await expect.poll(() => windows().length).toBe(before);
});

// ------------------------------------------------------------------ KYO-374.c2

test("a dev box project's layout is remembered apart from a local project at the same path (KYO-374.c2)", async () => {
  test.setTimeout(120_000);
  const page = clientPage;
  const layoutsFile = join(clientProfile, "layouts.json");
  const keysOf = () =>
    existsSync(layoutsFile)
      ? Object.keys(JSON.parse(readFileSync(layoutsFile, "utf8")) as Record<string, unknown>)
      : [];
  const localKey = (root: string) => keysOf().find((key) => key.startsWith(`${root}\n`));
  const remoteKey = (root: string) =>
    keysOf().find((key) => key.startsWith(`${LOCAL_HOST}:${devBox.port}:${root}\n`));

  // Fixture A is a directory on THIS machine too: opened locally, this machine's kyo
  // serve is made to serve it (`kyo projects add`), and it is arranged.
  const local = await page.evaluate((root) => window.kyosei.openProject(root), fixtureA.root);
  expect(local.refused, JSON.stringify(local.refused)).toBeNull();
  expect(local.project.remote).toBeNull();
  await openKind(page, "views", "brief");
  await page.waitForTimeout(900);
  expect(localKey(fixtureA.root)).toBeDefined();
  const localBefore = JSON.stringify(
    (JSON.parse(readFileSync(layoutsFile, "utf8")) as Record<string, unknown>)[
      localKey(fixtureA.root) ?? ""
    ],
  );

  const remote = await page.evaluate((input) => window.kyosei.openRemote(input), {
    host: LOCAL_HOST,
    port: devBox.port,
    root: fixtureA.root,
  });
  expect(remote.refused, JSON.stringify(remote.refused)).toBeNull();
  await openKind(page, "views", "brief");
  await page.waitForTimeout(900);
  expect(remoteKey(fixtureA.root)).toBeDefined();
  const localAfter = JSON.stringify(
    (JSON.parse(readFileSync(layoutsFile, "utf8")) as Record<string, unknown>)[
      localKey(fixtureA.root) ?? ""
    ],
  );
  expect(localAfter).toBe(localBefore);
});

// ------------------------------------------------------------------ KYO-379

test("the dev box's list is its registry's, read afresh; a path typed for it is refused, since kyo serve adds no project (KYO-379, KYO-452)", async () => {
  test.setTimeout(120_000);
  const page = clientPage;
  const probe = () =>
    page.evaluate((input) => window.kyosei.probeRemote(input), { address: devBox.address });
  expect((await probe()).recents.map((entry) => entry.root)).toEqual([
    fixtureA.root,
    fixtureC.root,
  ]);
  // Taken out of the dev box's registry by its own CLI: the next ask lists it no more.
  const held = process.env.KYOSEI_HOME;
  process.env.KYOSEI_HOME = fixtureA.home;
  try {
    await kyo(fixtureA.resolution, ["projects", "remove", fixtureC.root], fixtureA.base);
    expect((await probe()).recents.map((entry) => entry.root)).toEqual([fixtureA.root]);
  } finally {
    await kyo(fixtureA.resolution, ["projects", "add", fixtureC.root], fixtureA.base);
    process.env.KYOSEI_HOME = held;
  }
  expect((await probe()).recents.map((entry) => entry.root)).toEqual([
    fixtureA.root,
    fixtureC.root,
  ]);

  const typed = await page.evaluate((input) => window.kyosei.connectRemote(input), {
    address: devBox.address,
    root: fixtureB.root,
    add: true,
  });
  expect(typed.changed).toBe(false);
  expect(typed.refused?.problem).toContain(`kyo projects add ${fixtureB.root}`);
});

// ------------------------------------------------------------------ KYO-387, KYO-389

test("with the window's dev box stopped, the window and the connect dialog say so in one sentence, with no IPC frame (KYO-387.c1, KYO-389.c1, KYO-471)", async () => {
  test.setTimeout(180_000);
  const page = clientPage;
  // A second kyo serve over the dev box's home, on a port of its own, to stop.
  const doomed = await startKyoServe(fixtureA.home);
  const label = doomed.url;
  try {
    const moved = await page.evaluate((input) => window.kyosei.connectRemote(input), {
      address: label,
      root: fixtureA.root,
    });
    expect(moved.refused, JSON.stringify(moved.refused)).toBeNull();
    await expect(
      (await rows(page, "records")).filter({ hasText: "Only on the dev box" }),
    ).toHaveCount(1);

    await stopKyoServe(doomed);

    // KYO-471: the window notices on its own, when the events stream closes, and says so
    // on the serve-down screen -- which covers the lists, so they are no longer where a
    // stopped dev box is stated. The screen's own sentences carry no IPC frame either.
    const down = page.locator('[data-region="serve-down"]');
    await expect(down).toBeVisible({ timeout: 15_000 });
    await expect(down).toHaveAttribute("data-state", "not-running");
    const said0 = (await down.textContent()) ?? "";
    expect(said0).not.toMatch(
      /kyosei:|Error:|Error invoking remote method|invoking remote method|Refusal:/,
    );

    // Connecting again from that screen's own choice: the refusal alone, where it was typed.
    await down.locator('[data-region="serve-connect"]').click();
    await page.locator('[data-region="remote-address"]').fill(label);
    await page.locator('[data-region="remote-connect"]').click();
    const said = page.locator('[data-region="remote-problem"]');
    await expect(said).toHaveAttribute("data-state", "refused");
    const text = (await said.textContent()) ?? "";
    expect(text.startsWith(`Could not open ${label}: `), text).toBe(true);
    expect(text).not.toMatch(/Error:|invoking remote method|kyosei:|Refusal:/);
    await page.locator('[data-region="remote-cancel"]').click();
  } finally {
    await stopKyoServe(doomed);
  }
  const home = await page.evaluate((root) => window.kyosei.openProject(root), fixtureB.root);
  expect(home.refused, JSON.stringify(home.refused)).toBeNull();
});
