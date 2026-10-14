// Switching projects in the app, driven in the real app.
//
// KYO-175/KYO-186 already tear down what the main process owns on a switch -- the
// terminal's pty and the claude pane's webview both re-point themselves
// (`projectMoved` in main.ts). Nothing did the equivalent for tab CONTENT: a `record`
// tab fetches its document once, in `render()`, and a `compose` tab (the scratchpad,
// or an idea file) loads its text once the same way -- neither ever learns the project
// moved. Left open across a switch, a record tab keeps showing the OLD project's
// record under an id that, because Kyosei ids are small per-project sequences, very
// often still names something in the NEW project -- so the reader sees confidently
// wrong content with nothing on screen saying so. A compose tab is worse: its
// debounced autosave resolves the project fresh on every write
// (`saveScratchpad`/`saveIdea`, both via `projectCwd()` in main.ts), so it goes on
// writing the OLD project's text into the NEW project's `ideas/` after the switch.
//
// This file is the coverage for that fix: `shell.ts`'s `onProjectChanged` handler now
// closes every open `record`/`compose`/`views` tab before anything else runs.
//
// The switch has to be a REAL in-app one -- `window.kyosei.openProject`, the same call
// the rail's picker makes -- not `KYOSEI_PROJECT_ROOT`, which outranks it by design
// (KYO-175.c8) and would make the switch a refusal rather than a change.
//
// It also covers a second, additive change: `main.ts` has tracked a `recents` list
// since KYO-175, but nothing in the renderer ever showed it -- the rail's project
// button went straight to `chooseProject()`'s native folder dialog. It now opens a
// menu (the same `showMenu` right-click uses elsewhere) with the account's recents,
// excluding whichever project is already open, plus the native picker as a fallback.
//
// And KYO-379.c1 as KYO-459 left it: the machine's project registry,
// `$KYOSEI_HOME/projects.json`, is what this machine's kyo serve serves; the app offers
// it in that menu after the recents, and opening a directory it does not list runs
// `kyo projects add` -- the CLI is the registry's one writer -- while the chosen project
// and the recents order stay in `project.json`, this installation's own.

import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron, type ElectronApplication, expect, type Page, test } from "@playwright/test";
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
  serveAlso,
  suiteEnv,
  useServe,
  windowShowArg,
} from "./fixture";

/** The registry file in a store home, as `kyo projects` writes it. */
const REGISTRY_FILE = "projects.json";

function registeredRoots(home: string): string[] {
  const raw = JSON.parse(readFileSync(join(home, REGISTRY_FILE), "utf8")) as {
    projects: { root: string }[];
  };
  return raw.projects.map((entry) => entry.root);
}

const APP_ROOT = join(__dirname, "..");
const PROFILE = mkdtempSync(join(tmpdir(), "kyosei project-switch profile "));

let app: ElectronApplication | null = null;
let fixtureA: Fixture;
let fixtureB: Fixture;

/** A display configuration nothing else has ever arranged a layout on. */
function uniqueDisplays(): string {
  return JSON.stringify([{ id: 9001, bounds: { x: 0, y: 0, width: 1400, height: 900 } }]);
}

/**
 * A second configuration, for a check that ARRANGES a project rather than only reading
 * it. Layouts are keyed by project AND display configuration, and this file shares one
 * profile across its checks -- so a check that leaves project A split in two columns
 * changes what the next check's `adopt(A)` restores. Arranging on a configuration of
 * its own keeps that inside the check that did it.
 */
function arrangingDisplays(): string {
  return JSON.stringify([{ id: 9002, bounds: { x: 0, y: 0, width: 1400, height: 900 } }]);
}

/** One more, for each of KYO-334's checks: all three arrange, for the same reason. */
function debounceDisplays(): string {
  return JSON.stringify([{ id: 9003, bounds: { x: 0, y: 0, width: 1400, height: 900 } }]);
}

function flushDisplays(): string {
  return JSON.stringify([{ id: 9004, bounds: { x: 0, y: 0, width: 1400, height: 900 } }]);
}

function pushDisplays(): string {
  return JSON.stringify([{ id: 9005, bounds: { x: 0, y: 0, width: 1400, height: 900 } }]);
}

function envUnpinned(displays = uniqueDisplays()): Record<string, string> {
  const env = suiteEnv();
  delete env.KYOSEI_PROJECT_ROOT;
  // A bare shell, not a tmux one: this file is about tabs, not about a real tmux
  // session existing on the machine that runs it.
  env[TMUX_ENV] = "off";
  env.KYOSEI_LOGIN_SHELL = "off";
  env.KYOSEI_DISPLAYS = displays;
  return env;
}

test.beforeAll(async () => {
  test.setTimeout(120_000);
  fixtureA = await makeFixture({ records: 0 });
  fixtureB = await makeFixture({ records: 0, dir: "b project" });
  // One kyo serve serves both, as this machine's does every project it holds (KYO-459):
  // B's, with A added to its registry by the CLI's own writer.
  await serveAlso(fixtureB, fixtureA);
  useServe(fixtureB);
  // Both fixtures init with the same `--prefix FIX` (`makeFixture`'s own choice), so
  // each store's first record is `FIX-1` -- the exact id collision that makes the
  // record-tab defect dangerous rather than merely stale: reopening the same id in the
  // new project must show the NEW project's own record, never leftover text.
  await kyo(fixtureA.resolution, ["new", "bug", "--title", "Only in project A"], fixtureA.root);
  await kyo(fixtureB.resolution, ["new", "bug", "--title", "Only in project B"], fixtureB.root);
});

test.afterAll(() => {
  removeFixture(fixtureA);
  removeFixture(fixtureB);
  rmSync(PROFILE, { recursive: true, force: true });
});

test.afterEach(async () => {
  if (app !== null) {
    await closeReadyApp(app);
    app = null;
  }
});

async function launchUnpinned(displays?: string): Promise<Page> {
  app = await _electron.launch({
    args: [APP_ROOT, `--user-data-dir=${PROFILE}`, ...MACOS_NO_RESTORE, windowShowArg()],
    env: envUnpinned(displays),
  });
  const page = await app.firstWindow();
  await page.waitForSelector('body[data-ready="true"]');
  markReadyToClose(app);
  return page;
}

/** A fixture root used inside a `RegExp`, mainly for the space `makeFixture` puts in it. */
function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function adopt(page: Page, root: string): Promise<void> {
  const result = await page.evaluate((r) => window.kyosei.openProject(r), root);
  expect(result.refused, JSON.stringify(result.refused)).toBeNull();
}

/**
 * Show the records list, tolerating that the rail icon TOGGLES and a fresh window
 * opens with the records list already selected (`sidebar.ts`'s own default) -- an
 * unconditional click here would collapse it rather than open it.
 */
async function showRecords(page: Page): Promise<void> {
  const sidebar = page.locator('[data-region="sidebar"]');
  if ((await sidebar.getAttribute("data-list")) !== "records") {
    await page.locator('[data-region="rail-item"][data-rail-item="records"]').click();
  }
  await expect(sidebar).toHaveAttribute("data-list", "records");
  await expect(page.locator('[data-region="sidebar-loading"]')).toHaveCount(0, {
    timeout: 30_000,
  });
}

const recordTabs = (page: Page) => page.locator('[data-region="tab-body"][data-tab-kind="record"]');
const composeTabs = (page: Page) =>
  page.locator('[data-region="tab-body"][data-tab-kind="compose"]');

const columnsOf = (page: Page) => page.locator('[data-region="column"]');
const tabsInColumn = (page: Page, index: number) =>
  columnsOf(page).nth(index).locator('[data-region="tab"]');
const tabArgsIn = (page: Page, index: number) =>
  tabsInColumn(page, index).evaluateAll((nodes) =>
    nodes.map((node) => (node as HTMLElement).dataset.tabArg ?? ""),
  );

/**
 * Close every open tab, so a check that counts them starts from nothing.
 *
 * This file shares one profile across its checks and layouts are saved PER PROJECT
 * (KYO-249), so a window adopted onto project A restores whatever an earlier check in
 * this file left arranged there -- tabs a check counting tabs would be asserting
 * against.
 */
async function emptyWorkspace(page: Page): Promise<void> {
  const tabs = page.locator('[data-region="tab"]');
  for (let guard = 0; guard < 12 && (await tabs.count()) > 0; guard += 1) {
    // HOVER, then click the X. A tab's close button is `visibility: hidden` until its
    // tab is hovered or selected (`shell.css`), and only one tab per column is
    // selected -- so going straight for the first X stalls for thirty seconds on a
    // restored column holding two tabs, waiting for an element that will never become
    // visible on its own. Playwright hovers before it clicks, but it checks the
    // target's visibility BEFORE moving the mouse, so the hover has to be its own act.
    const tab = tabs.first();
    await tab.hover();
    await tab.locator('[data-action="close-tab"]').click();
  }
  await expect(tabs).toHaveCount(0);
}

test("switching projects closes an open record tab rather than leaving it stale", async () => {
  const page = await launchUnpinned();
  await adopt(page, fixtureA.root);

  await showRecords(page);
  await page.locator('[data-region="sidebar-row"]').filter({ hasText: "FIX-1" }).first().click();
  await expect(recordTabs(page)).toHaveCount(1);
  await expect(recordTabs(page)).toContainText("Only in project A");

  await adopt(page, fixtureB.root);

  // The stale tab is gone -- not repainted with new content under the old title, gone.
  await expect(recordTabs(page)).toHaveCount(0);

  // And FIX-1 opened fresh, in project B, is project B's own record -- proving nothing
  // about the old project's FIX-1 survived anywhere a later fetch could pick it up.
  await showRecords(page);
  await page.locator('[data-region="sidebar-row"]').filter({ hasText: "FIX-1" }).first().click();
  await expect(recordTabs(page)).toHaveCount(1);
  await expect(recordTabs(page)).toContainText("Only in project B");
  await expect(recordTabs(page)).not.toContainText("Only in project A");
});

test("switching projects closes BOTH copies of a record open in two columns (KYO-319)", async () => {
  // On a display configuration of its own: this is the one check here that ARRANGES a
  // project, and an arrangement is remembered per project AND configuration, so this
  // is what keeps the two columns it builds out of every other check's `adopt`.
  const page = await launchUnpinned(arrangingDisplays());
  await adopt(page, fixtureA.root);
  await emptyWorkspace(page);
  await expect(columnsOf(page)).toHaveCount(1);

  // Two tabs on one record, the state KYO-288's check builds: a click, then a
  // cmd-click (which forces a second tab rather than raising the first), then the
  // second copy moved to a column of its own.
  await showRecords(page);
  const row = page.locator('[data-region="sidebar-row"]').filter({ hasText: "FIX-1" }).first();
  await row.click();
  await row.click({ modifiers: ["Meta"] });
  await expect.poll(() => tabArgsIn(page, 0)).toEqual(["FIX-1", "FIX-1"]);
  await tabsInColumn(page, 0).nth(1).click({ button: "right" });
  await page.locator('[data-menu-item="Move to a new column"]').click();
  await expect(columnsOf(page)).toHaveCount(2);
  await expect.poll(() => tabArgsIn(page, 0)).toEqual(["FIX-1"]);
  await expect.poll(() => tabArgsIn(page, 1)).toEqual(["FIX-1"]);

  // WHICH tabs are project A's, marked on the DOM. Counting record tabs afterwards
  // cannot answer this check's question: the new project restores ITS OWN remembered
  // arrangement on the switch, which may hold a record tab of its own, and every
  // selector this file has reads that one exactly like a survivor -- both are a record
  // tab on FIX-1, because ids collide between projects. A tab's body element is created
  // once and moved, never rebuilt (`columns.ts`'s own rule), so a mark put on it here
  // is on it for as long as the tab lives and on nothing the restore makes.
  for (const index of [0, 1]) {
    await columnsOf(page)
      .nth(index)
      .locator('[data-region="tab-body"]')
      .first()
      .evaluate((node) => {
        (node as HTMLElement).dataset.projectA = "yes";
      });
  }
  await expect(page.locator("[data-project-a]")).toHaveCount(2);

  // Let the layout save land BEFORE the switch, as this file's layout check does. The
  // save is debounced by 250ms, and one still in flight when the project changes is
  // written under the project the window has moved TO -- so the switch would restore
  // this arrangement in the new project and this check would be reading the restore
  // rather than the close (KYO-319, measured: the check fails 3 of 3 without this wait
  // and passes 3 of 3 with it, on the same build).
  await page.waitForTimeout(800);

  await adopt(page, fixtureB.root);

  // Neither copy may survive: a tab left open across a switch shows the OLD project's
  // record, and because ids collide between projects it reads as the new project's own
  // with nothing on screen saying otherwise.
  await expect(page.locator("[data-project-a]")).toHaveCount(0);
  // ...and whatever the new project restored of its own is the NEW project's record.
  // As a FILTERED COUNT, not `not.toContainText`, which fails outright when the locator
  // matches nothing -- and matching nothing is the ordinary outcome here.
  await expect(recordTabs(page).filter({ hasText: "Only in project A" })).toHaveCount(0);
});

test("switching projects closes an open scratchpad tab rather than risk writing its text into the new project", async () => {
  const page = await launchUnpinned();
  await adopt(page, fixtureA.root);

  // The default layout opens on the scratchpad already; this test's own switch is the
  // only thing that matters, not how the tab got there, so ensure it explicitly.
  await openKind(page, "compose", "scratchpad");
  await expect(composeTabs(page)).toHaveCount(1);
  await composeTabs(page)
    .first()
    .evaluate((node) => {
      (node as HTMLElement).dataset.stamp = "project-a-scratchpad";
    });

  await adopt(page, fixtureB.root);

  // Gone -- the tab that could have written A's text into B. What B's own layout
  // opens (a fresh scratchpad, on B's file) is a different tab (KYO-249).
  await expect(page.locator('[data-stamp="project-a-scratchpad"]')).toHaveCount(0);
});

// ============================================================ KYO-249: one project per window

test("two windows show two projects at once, each reading and writing its own", async () => {
  const page = await launchUnpinned();
  await adopt(page, fixtureA.root);
  const opened = await page.evaluate((root) => window.kyosei.openWindow(root), fixtureB.root);
  expect(opened.refused, JSON.stringify(opened.refused)).toBeNull();
  const windows = () =>
    (app as ElectronApplication).windows().filter((w) => w.url().startsWith("file:"));
  await expect.poll(() => windows().length).toBe(2);
  const other = windows().find((w) => w !== page);
  if (other === undefined) throw new Error("no second window");
  await other.waitForSelector('body[data-facts="ready"]');

  // Each window is bound to its own project, and says so.
  const rootOf = (p: Page) => p.evaluate(async () => (await window.kyosei.getFacts()).project.root);
  expect(await rootOf(page)).toBe(fixtureA.root);
  expect(await rootOf(other)).toBe(fixtureB.root);
  const self = (await other.evaluate(() => window.kyosei.getState())).self;
  expect(self.kind === "shell" ? self.projectRoot : null).toBe(fixtureB.root);

  // ...and reads from it: FIX-1 is a different record in each.
  await showRecords(page);
  await showRecords(other);
  await page.locator('[data-region="sidebar-row"]').filter({ hasText: "FIX-1" }).first().click();
  await other.locator('[data-region="sidebar-row"]').filter({ hasText: "FIX-1" }).first().click();
  await expect(recordTabs(page).locator(":visible").first()).toContainText("Only in project A");
  await expect(recordTabs(other).locator(":visible").first()).toContainText("Only in project B");

  // The switcher moves ONE window: A's window goes to B, B's window is untouched.
  await adopt(page, fixtureB.root);
  expect(await rootOf(page)).toBe(fixtureB.root);
  expect(await rootOf(other)).toBe(fixtureB.root);
  await other.close();
});

test("a switch restores the project's own layout, remembered per project", async () => {
  const page = await launchUnpinned();
  await adopt(page, fixtureA.root);
  const columns = (p: Page) => p.locator('[data-region="column"]');
  await page.locator('[data-region="split"]').first().click();
  await expect(columns(page)).toHaveCount(2);
  await page.waitForTimeout(800); // the save is debounced

  // B has never been arranged: its default, one column.
  await adopt(page, fixtureB.root);
  await expect(columns(page)).toHaveCount(1);
  await page.waitForTimeout(800);

  // Back to A: A's two columns, not B's one.
  await adopt(page, fixtureA.root);
  await expect(columns(page)).toHaveCount(2);
});

test("a link opens in a window already on its project, not in whichever window is focused", async () => {
  const page = await launchUnpinned();
  await adopt(page, fixtureA.root);
  await page.evaluate((root) => window.kyosei.openWindow(root), fixtureB.root);
  const windows = () =>
    (app as ElectronApplication).windows().filter((w) => w.url().startsWith("file:"));
  await expect.poll(() => windows().length).toBe(2);
  const other = windows().find((w) => w !== page);
  if (other === undefined) throw new Error("no second window");
  await other.waitForSelector('body[data-facts="ready"]');
  // Window A may have restored a layout an earlier test saved for project A (this
  // file reuses one display signature, and layouts are per project now): what is
  // asserted is that the link ADDS nothing to A, whatever A opened on.
  const before = await recordTabs(page).count();
  // Both stores carry the prefix FIX, so the link resolves to the project of the
  // window it was followed FROM -- and lands there.
  await other.evaluate(() => window.kyosei.followLink("kyosei://FIX/FIX-1"));
  await expect(recordTabs(other).locator(":visible").first()).toContainText("Only in project B", {
    timeout: 20_000,
  });
  await expect(recordTabs(page)).toHaveCount(before);
  await other.close();
});

test("the rail's project button offers recents instead of only the native picker", async () => {
  const page = await launchUnpinned();
  // Visit B then land on A, so A is "current" and B is the one recent worth offering
  // -- the switcher must not offer the project already open.
  await adopt(page, fixtureB.root);
  await adopt(page, fixtureA.root);

  // The switcher's menu is built from `shell.ts`'s own cache of the last `getFacts`
  // push, so the menu opens instantly rather than waiting on a `kyo` subprocess --
  // and that push is a separate, unordered round trip from the two `openProject`
  // calls above returning. `rail.setProject`'s own `data-tip` is written by that same
  // `renderFacts` call, synchronously before the cache this menu reads from, so
  // waiting for it to say "A" first rules the race out rather than racing it too.
  const projectButton = page.locator('[data-region="project-switcher"]');
  await expect(projectButton).toHaveAttribute("data-tip", new RegExp(escapeRegExp(fixtureA.root)));

  await projectButton.click();
  const menu = page.locator('[data-region="context-menu"]');
  await expect(menu).toBeVisible();
  const items = menu.locator(".menu-item");
  // The one recent (B), labelled with where it is served from (KYO-465.c2), "Clear
  // recent projects" (KYO-465.c3), "Open other project…", "New project…" (KYO-460),
  // and -- since KYO-370 -- "Connect to a dev box…" last, below a separator; no dev box
  // has been connected to, so no remembered one sits between them.
  await expect(items).toHaveCount(5);
  await expect(items.first()).toHaveAttribute("data-tip", fixtureB.root);
  await expect(items.first()).toHaveText("FIX @ this machine");
  await expect(items.nth(1)).toHaveText("Clear recent projects");
  await expect(items.nth(2)).toHaveText("Open other project…");
  await expect(items.nth(3)).toHaveText("New project…");
  await expect(items.last()).toHaveText("Connect to a dev box…");

  // Clicking the recent switches straight there -- no native dialog involved.
  await items.first().click();
  await showRecords(page);
  await expect(
    page.locator('[data-region="sidebar-row"]').filter({ hasText: "Only in project B" }),
  ).toBeVisible();
});

// ============================================================ KYO-334: a save in flight
//
// The shell debounces its layout saves by 250ms (`SAVE_AFTER_MS`), and both halves of
// the key a layout is filed under -- the project and the desk -- can move inside that
// window. KYO-319.r1 measured the first half: a save still pending when the project
// switched was written under the project the window had just moved TO, so the tabs the
// switch deliberately closed came back and the new project's own remembered arrangement
// was overwritten with the old one's. The three checks below are the three ways that
// goes wrong, and each one fails on a build without the fix (measured; see KYO-334.r1).

test("a layout save made while the project is switching is not filed under the new project (KYO-334.c1/c2)", async () => {
  const page = await launchUnpinned(debounceDisplays());

  // What B remembers on this configuration: an empty workspace, no tabs at all.
  await adopt(page, fixtureB.root);
  await emptyWorkspace(page);
  await page.waitForTimeout(800); // deliberate: B's own arrangement goes to disk

  // A, with a record tab open. The TAB is the discriminator and the column count is
  // not: B's own arrangement has no tabs, and both projects' records are `FIX-1`, so
  // any record tab showing in B after this is one A's workspace put there.
  await adopt(page, fixtureA.root);
  await emptyWorkspace(page);
  await showRecords(page);
  await page.locator('[data-region="sidebar-row"]').filter({ hasText: "FIX-1" }).first().click();
  await expect.poll(() => tabArgsIn(page, 0)).toEqual(["FIX-1"]);
  await page.waitForTimeout(800);

  // THE WINDOW, and why this rearranges in a loop rather than once before the switch.
  // `adoptProject` probes the candidate with a `kyo` subprocess BEFORE it moves
  // `entry.projectRoot`, and only then awaits `projectMoved` and `projectFacts` before
  // telling the renderer. So a save landing in the first phase is filed under A and is
  // perfectly correct, and only one landing in the second -- between the window moving
  // and the renderer hearing about it -- is the defect. Measured on this machine, on a
  // build with none of KYO-334 in it: the move at 608ms and `projectChanged` at 916ms,
  // a window 307ms wide that a single save armed before the switch (it fires at 250ms)
  // sails straight past. So the workspace is rearranged every 280ms until the renderer
  // is told the project moved: each rearrangement arms a save 250ms out and the next
  // one is 30ms later than that, so saves fire 280ms apart across the whole switch and
  // one of them is inside any window at least that wide. Nothing is rearranged after
  // `projectChanged` -- past that point a save is B's own business and proves nothing.
  const choice = await page.evaluate(async (root) => {
    let switching = true;
    const off = window.kyosei.onProjectChanged(() => {
      switching = false;
    });
    const done = window.kyosei.openProject(root);
    void done.then(() => {
      switching = false;
    });
    for (let guard = 0; guard < 40 && switching; guard += 1) {
      (document.querySelector('[data-region="split"]') as HTMLElement).click();
      await new Promise((resolve) => setTimeout(resolve, 280));
    }
    off();
    return done;
  }, fixtureB.root);
  expect(choice.refused, JSON.stringify(choice.refused)).toBeNull();

  // B opens on B's OWN remembered arrangement (c2): no tabs, because B has none.
  await expect(recordTabs(page)).toHaveCount(0);

  // And nothing of A's was written down under B either -- which is a separate question,
  // because the push that ends a switch reads the layouts file and a save landing after
  // that read shows up only the next time B is opened. So settle, leave, come back.
  await page.waitForTimeout(800);
  await adopt(page, fixtureA.root);
  await page.waitForTimeout(800);
  await adopt(page, fixtureB.root);
  await expect(recordTabs(page)).toHaveCount(0);
});

test("a switch started in this window writes down the arrangement being left first (KYO-334.c1)", async () => {
  const page = await launchUnpinned(flushDisplays());
  // B first, so it is the recent the switcher offers; then A, the project to leave.
  await adopt(page, fixtureB.root);
  await adopt(page, fixtureA.root);
  await emptyWorkspace(page);
  await page.waitForTimeout(800);
  const projectButton = page.locator('[data-region="project-switcher"]');
  await expect(projectButton).toHaveAttribute("data-tip", new RegExp(escapeRegExp(fixtureA.root)));

  // Split the workspace, open the switcher and choose B -- all in one renderer task, so
  // the reader's last arrangement of A is still inside its 250ms debounce when the
  // switch is asked for. This is the reader's OWN way to switch, and the difference
  // between it and the check above is the whole point: a switch this window starts can
  // write A's arrangement down before it asks, and does.
  await page.evaluate((root) => {
    const click = (node: Element | null | undefined): void => {
      if (node === null || node === undefined) throw new Error("nothing to click");
      (node as HTMLElement).click();
    };
    click(document.querySelector('[data-region="split"]'));
    click(document.querySelector('[data-region="project-switcher"]'));
    click(
      [...document.querySelectorAll('[data-region="context-menu"] .menu-item')].find(
        (item) => (item as HTMLElement).dataset.tip === root,
      ),
    );
  }, fixtureB.root);

  await expect(projectButton).toHaveAttribute("data-tip", new RegExp(escapeRegExp(fixtureB.root)));
  await page.waitForTimeout(800);

  // Back to A: the two columns the reader left it in. Dropping the pending save would
  // leave one -- correct in the sense that nothing is misfiled, and still a loss of the
  // last thing the reader did.
  await adopt(page, fixtureA.root);
  await expect(columnsOf(page)).toHaveCount(2);
});

test("a layout push carrying nothing new does not undo a tab just closed (KYO-334.c3)", async () => {
  const page = await launchUnpinned(pushDisplays());
  await adopt(page, fixtureA.root);
  await emptyWorkspace(page);
  await openKind(page, "compose", "scratchpad");
  await showRecords(page);
  await page.locator('[data-region="sidebar-row"]').filter({ hasText: "FIX-1" }).first().click();
  await expect.poll(() => tabArgsIn(page, 0)).toEqual(["scratchpad", "FIX-1"]);
  await page.waitForTimeout(800); // both tabs are on disk now

  // Close FIX-1, then push a layout at the window before the close's own 250ms save can
  // fire -- the ordering KYO-319.r1 named and could not force. BOTH HALVES RUN IN THE
  // MAIN PROCESS, one statement after the other with no test-runner round trip between
  // them: `executeJavaScript` is a main<->renderer hop, and `screen.emit` drives the
  // very listener `main.ts` registers for a real screen change (`announceDisplays`),
  // which is why this is the ordering and not an imitation of it.
  //
  // POINTERDOWN, not `click()`. `columns.ts` closes a tab from `onTabPointerDown` (the
  // tab is selected on press, and the X is read off `event.target` there), so a
  // programmatic `.click()` on the X does nothing at all and does not say so: the first
  // run of this check spent its ten seconds waiting for a tab that had never been
  // closed. Dispatching it on the X is also what lets this skip the hover `emptyWorkspace`
  // needs -- an event dispatched by hand does not care that `shell.css` keeps the X
  // `visibility: hidden` until its tab is hovered.
  await (app as ElectronApplication).evaluate(async ({ BrowserWindow, screen }) => {
    const win = BrowserWindow.getAllWindows().find((candidate) =>
      candidate.webContents.getURL().startsWith("file:"),
    );
    if (win === undefined) throw new Error("no shell window");
    await win.webContents.executeJavaScript(
      `(() => {
         const x = document.querySelector('[data-region="tab"][data-tab-arg="FIX-1"] [data-action="close-tab"]');
         if (x === null) throw new Error("no close button on the FIX-1 tab");
         x.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 0 }));
       })()`,
    );
    screen.emit("display-metrics-changed");
  });

  // The closed tab does not come back ...
  await expect.poll(() => tabArgsIn(page, 0)).toEqual(["scratchpad"]);
  // ... and is still gone once the close's save has fired: a restore that resurrected
  // it would have been written down by the save the restore itself schedules, which is
  // what makes this defect outlive the moment it happens.
  await page.waitForTimeout(800);
  await expect.poll(() => tabArgsIn(page, 0)).toEqual(["scratchpad"]);
});

test("the recents menu stays inside the window instead of spilling past the bottom edge", async () => {
  const page = await launchUnpinned();
  await adopt(page, fixtureB.root);
  await adopt(page, fixtureA.root);

  const projectButton = page.locator('[data-region="project-switcher"]');
  await expect(projectButton).toHaveAttribute("data-tip", new RegExp(escapeRegExp(fixtureA.root)));

  // The project button sits flush against the window's bottom edge by construction
  // (it is the rail's last item), so a short window is what makes the menu overflow
  // if `showMenu` positioned it on the raw click point alone -- a real, if small,
  // window a reader would actually use, not an edge case manufactured for the check.
  await (app as ElectronApplication).evaluate(({ BrowserWindow }) => {
    for (const win of BrowserWindow.getAllWindows()) win.setSize(1000, 500);
  });

  await projectButton.click();
  const items = page.locator('[data-region="context-menu"] .menu-item');
  const last = items.last();
  await expect(last).toHaveText("Connect to a dev box…");
  const box = await last.boundingBox();
  const viewportHeight = await page.evaluate(() => window.innerHeight);
  expect(box).not.toBeNull();
  expect(
    (box as { y: number; height: number }).y + (box as { height: number }).height,
  ).toBeLessThanOrEqual(viewportHeight);
});

// ============================================================ KYO-379.c1: the registry

test("opening a directory this machine's kyo serve does not serve adds it with `kyo projects add`, the switcher offers what it serves, and an unparseable registry is refused by name (KYO-379.c1, KYO-459)", async () => {
  test.setTimeout(180_000);
  // A third project, on this machine and in no registry the app's server reads.
  const fixtureC = await makeFixture({ records: 0, git: false, dir: "c project" });
  useServe(fixtureB);
  const registry = join(fixtureB.home, REGISTRY_FILE);
  const kept = readFileSync(registry, "utf8");
  const nowhere = mkdtempSync(join(tmpdir(), "kyosei no project here "));
  try {
    expect(registeredRoots(fixtureB.home)).not.toContain(fixtureC.root);
    const page = await launchUnpinned();
    await page.waitForSelector('body[data-facts="ready"]');
    // The switcher offers what this machine's kyo serve serves: A and B, whichever
    // this installation opened before.
    const facts = await page.evaluate(() => window.kyosei.getFacts());
    const offered = facts.project.recents.map((entry) => entry.root);
    expect(offered).toEqual(expect.arrayContaining([fixtureA.root, fixtureB.root]));

    // Opening C adds it to the registry -- by the CLI, the registry's one writer -- and
    // opens it, read through the server that now serves it.
    const opened = await page.evaluate((root) => window.kyosei.openProject(root), fixtureC.root);
    expect(opened.refused, JSON.stringify(opened.refused)).toBeNull();
    expect(registeredRoots(fixtureB.home)).toContain(fixtureC.root);
    expect(opened.project.root).toBe(fixtureC.root);
    expect(opened.project.project).toBe("FIX");

    // A directory holding no project is refused with the CLI's own sentence, and the
    // window stays where it was.
    const refused = await page.evaluate((root) => window.kyosei.openProject(root), nowhere);
    expect(refused.refused?.problem ?? "").toContain("no initialised Kyosei project");
    expect(refused.project.root).toBe(fixtureC.root);

    // Unparseable: kyo serve refuses it by name rather than reading it as empty, and the
    // app says so where the choice was made -- the person's file is left as it is.
    const broken = '{ "projects": [ { "root": ';
    writeFileSync(registry, broken);
    const unreadable = await page.evaluate(
      (root) => window.kyosei.openProject(root),
      fixtureA.root,
    );
    expect(unreadable.refused?.problem ?? "").toContain("cannot read the project registry");
    expect(readFileSync(registry, "utf8")).toBe(broken);
  } finally {
    writeFileSync(registry, kept);
    removeFixture(fixtureC);
    useServe(fixtureB);
    rmSync(nowhere, { recursive: true, force: true });
  }
});

// ============================================================ KYO-465.c3: editing the recents

/** The recents `project.json` holds, as roots -- what the next launch reads. */
function rememberedRecents(): string[] {
  try {
    const state = JSON.parse(readFileSync(join(PROFILE, "project.json"), "utf8")) as {
      recents?: { root: string }[];
    };
    return (state.recents ?? []).map((entry) => entry.root);
  } catch {
    return [];
  }
}

/** Quit the app this check launched and launch it again on the same profile. */
async function relaunchUnpinned(): Promise<Page> {
  if (app !== null) await closeReadyApp(app);
  app = null;
  return launchUnpinned();
}

test("a recent is removed from the switcher, all are cleared, and neither comes back after a restart (KYO-465.c3)", async () => {
  test.setTimeout(180_000);
  let page = await launchUnpinned();
  await adopt(page, fixtureB.root);
  await adopt(page, fixtureA.root);
  const projectButton = () => page.locator('[data-region="project-switcher"]');
  await expect(projectButton()).toHaveAttribute(
    "data-tip",
    new RegExp(escapeRegExp(fixtureA.root)),
  );
  // An earlier check in this file may have left another project further down the list.
  await expect.poll(() => rememberedRecents().slice(0, 2)).toEqual([fixtureA.root, fixtureB.root]);

  // Remove B from where the recents are listed: its row goes, the menu stays open.
  await projectButton().click();
  // Functions, not locators: a locator is bound to the page it was made on, and this
  // check relaunches the app twice.
  const menu = () => page.locator('[data-region="context-menu"]');
  const rowOf = (root: string) =>
    menu().locator(".menu-row", { has: page.locator(`.menu-item[data-tip="${root}"]`) });
  await expect(rowOf(fixtureB.root)).toHaveCount(1);
  await rowOf(fixtureB.root).locator('[data-action="remove-recent"]').click();
  await expect(menu()).toBeVisible();
  await expect(rowOf(fixtureB.root)).toHaveCount(0);
  await expect.poll(rememberedRecents).not.toContain(fixtureB.root);
  expect(rememberedRecents()[0]).toBe(fixtureA.root);
  await page.keyboard.press("Escape");

  // A restart reads the removal back: B is no recent (this machine's registry may still
  // offer it, as a row with no remove control -- the CLI edits that list).
  page = await relaunchUnpinned();
  await expect(projectButton()).toHaveAttribute(
    "data-tip",
    new RegExp(escapeRegExp(fixtureA.root)),
  );
  await projectButton().click();
  await expect(menu()).toBeVisible();
  await expect(menu().locator('[data-menu-item="Open other project…"]')).toBeVisible();
  await expect(rowOf(fixtureB.root)).toHaveCount(0);
  expect(rememberedRecents()).not.toContain(fixtureB.root);
  await page.keyboard.press("Escape");

  // Clear all: B again, then Clear -- nothing remembered, and still nothing after a restart.
  await adopt(page, fixtureB.root);
  await expect.poll(() => rememberedRecents().slice(0, 2)).toEqual([fixtureB.root, fixtureA.root]);
  await expect(projectButton()).toHaveAttribute(
    "data-tip",
    new RegExp(escapeRegExp(fixtureB.root)),
  );
  await projectButton().click();
  await menu().locator('[data-menu-item="Clear recent projects"]').click();
  await expect.poll(rememberedRecents).toEqual([]);
  page = await relaunchUnpinned();
  // The window the last session showed is still what reopens: clearing forgot a list.
  await expect(projectButton()).toHaveAttribute(
    "data-tip",
    new RegExp(escapeRegExp(fixtureB.root)),
  );
  await projectButton().click();
  await expect(menu()).toBeVisible();
  await expect(menu().locator(".menu-row")).toHaveCount(0);
  await expect(menu().locator('[data-menu-item="Clear recent projects"]')).toHaveCount(0);
  expect(rememberedRecents()).toEqual([]);
  await page.keyboard.press("Escape");
});
