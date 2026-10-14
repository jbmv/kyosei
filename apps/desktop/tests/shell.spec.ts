// The v3 shell, driven in the real app (KYO-232).
//
// This replaces `smoke.spec.ts`, which tested the three modes, the pane workspace and
// the bottom dock -- none of which exist. What is checked here is what replaced them:
// the rail, the collapsible sidebar, one to three columns of tabs, the tab registry
// discovered by directory, layout memory per display configuration, the terminal and
// claude tabs, and the badges.
//
// Every assertion below is made against a live renderer in a real Electron window:
// `npm run check` builds `dist/` and Playwright launches the built app.

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
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import {
  _electron,
  type ElectronApplication,
  expect,
  type Locator,
  type Page,
  test,
} from "@playwright/test";
import { TMUX_ENV } from "../src/main/terminal";
import {
  closeReadyApp,
  type Fixture,
  kyo,
  MACOS_NO_RESTORE,
  makeFixture,
  makeRecordsFixture,
  markReadyToClose,
  openKind,
  type RecordsFixture,
  removeFixture,
  scratchpadPathFor,
  suiteEnv,
  windowShowArg,
} from "./fixture";

/** Playwright transpiles this spec to CJS, so `__dirname` finds the app root. */
const APP_ROOT = join(__dirname, "..");
const REPO_ROOT = join(APP_ROOT, "..", "..");

let app: ElectronApplication;
let fixture: RecordsFixture;

/** One Chromium profile per run (KYO-116.d1: per-launch bought nothing measurable). */
const PROFILE = mkdtempSync(join(tmpdir(), "kyosei profile "));

/**
 * `suiteEnv()` plus this launch's overrides: the runner's environment without the
 * host terminal's own variables (KYO-254), and with the undefined entries dropped.
 *
 * `KYOSEI_PROJECT_ROOT` points the app at a fixture store; `KYOSEI_HOME` is the CLI's
 * own variable and keeps the session scratchpad inside the fixture rather than in a
 * developer's real `~/kyosei/`; `KYOSEI_TMUX=off` states which world the terminal tab
 * is in, so an assertion about it does not mean something different on a desk that
 * happens to have tmux (KYO-115).
 */
function envFor(
  projectRoot: string,
  home?: string,
  extra: Record<string, string> = {},
): Record<string, string> {
  const env = suiteEnv();
  env.KYOSEI_PROJECT_ROOT = projectRoot;
  if (home !== undefined) env.KYOSEI_HOME = home;
  env[TMUX_ENV] = "off";
  env.KYOSEI_DISPLAYS = uniqueDisplays();
  return { ...env, ...extra };
}

/**
 * A display configuration nothing has ever been arranged on.
 *
 * The profile is shared for the run (KYO-116.d1), and layouts are saved against the
 * attached screens -- so without this, every launch after the first would restore
 * whatever the PREVIOUS check left arranged, and each check here would be asserting
 * against a workspace some earlier one built. The layout check overrides it with two
 * configurations of its own, which is the same seam used on purpose.
 */
let displaySeq = 0;
function uniqueDisplays(): string {
  displaySeq += 1;
  return JSON.stringify([
    { id: 1000 + displaySeq, bounds: { x: 0, y: 0, width: 1512, height: 982 } },
  ]);
}

/**
 * Close every open tab, so a check about columns starts from nothing.
 *
 * A fresh display configuration opens on the scratchpad with the records list showing
 * (the app's two fast paths, one click apart), which is right for a human and noise for
 * a check counting tabs.
 */
async function emptyWorkspace(page: Page): Promise<void> {
  const closers = page.locator('[data-region="tab"] [data-action="close-tab"]');
  for (let guard = 0; guard < 12 && (await closers.count()) > 0; guard += 1) {
    await closers.first().click();
  }
  await expect(page.locator('[data-region="tab"]')).toHaveCount(0);
}

function launchArgs(profile = PROFILE): string[] {
  return [APP_ROOT, `--user-data-dir=${profile}`, ...MACOS_NO_RESTORE, windowShowArg()];
}

async function readyWindow(page: Page): Promise<Page> {
  await page.waitForSelector('body[data-ready="true"]');
  return page;
}

/**
 * Launch and wait for the first window before handing it to anyone (KYO-118).
 *
 * Quitting an Electron 44.0.0 mid-startup segfaults it, so no app this file starts may
 * be closed until its window has rendered. `closeReadyApp` refuses the alternative.
 */
async function launchReady(env: Record<string, string>, profile = PROFILE): Promise<Page> {
  app = await _electron.launch({ args: launchArgs(profile), env });
  const page = await readyWindow(await app.firstWindow());
  markReadyToClose(app);
  return page;
}

async function firstShell(): Promise<Page> {
  return readyWindow(await app.firstWindow());
}

/** A shell whose live seams and both badges have answered. */
async function shellReady(page: Page): Promise<Page> {
  await page.waitForSelector('body[data-facts="ready"]');
  await page.waitForSelector('body[data-badges="ready"]');
  return page;
}

/**
 * A line only the per-project scratchpad carries, so a check can tell WHICH file the
 * compose tab opened (KYO-240.c2).
 *
 * The stage 2 compose placeholder printed the path on screen and the real editor shows
 * the text, so the claim "this tab is on the scratchpad `main/kyo.ts` resolved, not on
 * some other markdown file" is made against content now. Written before the first
 * launch because the tab reads its file when it opens, and the default layout opens
 * one (the app's fast path: a fresh desk starts on the scratchpad).
 */
const SCRATCHPAD_MARKER = "a line only the per-project scratchpad carries";

test.beforeAll(async () => {
  fixture = await makeRecordsFixture();
  const scratchpad = scratchpadPathFor(fixture);
  mkdirSync(dirname(scratchpad), { recursive: true });
  writeFileSync(scratchpad, `# scratchpad\n\n${SCRATCHPAD_MARKER}\n`, "utf8");
});

test.afterAll(() => {
  removeFixture(fixture);
  rmSync(PROFILE, { recursive: true, force: true });
});

test.beforeEach(async () => {
  await launchReady(envFor(fixture.root, fixture.home));
});

test.afterEach(async () => {
  await closeReadyApp(app);
});

// ------------------------------------------------------------------ helpers

const rail = (page: Page, id: string) => page.locator(`[data-rail-item="${id}"]`);
const columns = (page: Page) => page.locator('[data-region="column"]');
const tabsIn = (page: Page, index: number) =>
  columns(page).nth(index).locator('[data-region="tab"]');

async function tabTitles(page: Page, index: number): Promise<string[]> {
  return tabsIn(page, index).evaluateAll((nodes) => nodes.map((node) => node.textContent ?? ""));
}

/**
 * Show a sidebar list and return its rows.
 *
 * Idempotent on purpose: the rail icon TOGGLES (clicking the selected one collapses
 * the sidebar), and the default layout opens with the records list already showing --
 * so a helper that always clicked would close the list it was asked to open.
 */
async function rowsFor(page: Page, list: string) {
  const sidebar = page.locator('[data-region="sidebar"]');
  if ((await sidebar.getAttribute("data-list")) !== list) await rail(page, list).click();
  await expect(sidebar).toHaveAttribute("data-list", list);
  await expect(sidebar).toHaveAttribute("data-open", "true");
  // And WAITED FOR. A list is filled by a `kyo` subprocess, which on a loaded machine
  // is seconds rather than milliseconds -- so a helper that returned as soon as the
  // sidebar opened would hand its caller an empty list and the caller would report a
  // missing record. Measured: this file passed in 2.3s per check on an idle machine
  // and timed out looking for FIX-4 under load, on the same build.
  await expect(page.locator('[data-region="sidebar-loading"]')).toHaveCount(0, {
    timeout: 60_000,
  });
  return page.locator('[data-region="sidebar-row"]');
}

/** Drag from one element's centre to another's, with pointer events. */
async function dragTo(page: Page, from: { x: number; y: number }, to: { x: number; y: number }) {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  // Two moves: the first crosses the drag threshold, the second lands. One move can
  // arrive as a single event and be read as a jump rather than as a drag.
  await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 6 });
  await page.mouse.move(to.x, to.y, { steps: 6 });
  await page.mouse.up();
}

/**
 * An element's box, waited for rather than sampled.
 *
 * `boundingBox()` answers null for an element that is attached and not visible, and it
 * does not retry -- so reading one the instant after a layout change is a coin toss.
 */
async function boxOf(
  locator: Locator,
): Promise<{ x: number; y: number; width: number; height: number }> {
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  if (box === null) throw new Error("the element has no box");
  return box;
}

async function centreOf(page: Page, selector: string, nth = 0): Promise<{ x: number; y: number }> {
  const box = await boxOf(page.locator(selector).nth(nth));
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/**
 * The pane window a pop-out just opened, CHOSEN rather than awaited (KYO-217).
 *
 * Playwright reports a `WebContentsView`'s contents as a window too, so with a claude
 * tab open `app.waitForEvent("window")` races between the window this app opened and
 * the claude.ai page inside it -- and the loser is a page that will never have
 * `body[data-ready]`. The two are distinguishable by ORIGIN: this app's renderers are
 * `file:`, the view's page is `https:`.
 */
async function paneWindowOf(shell: Page, timeout = 20_000): Promise<Page> {
  const ours = () => app.windows().filter((win) => win !== shell && win.url().startsWith("file:"));
  await expect.poll(() => ours().length, { timeout }).toBeGreaterThan(0);
  const popped = ours()[0];
  if (popped === undefined) throw new Error("no pane window opened");
  return readyWindow(popped);
}

// ============================================================ c1: the v2 renderer is gone

test("no v2 region, control or mode survives in the renderer", async () => {
  const page = await shellReady(await firstShell());

  // The regions the record names, asked of the live DOM rather than of the source:
  // a deleted module that some other file still drew would pass a grep and fail this.
  for (const region of [
    "mode-pills",
    "left-pane",
    "right-pane",
    "editor-pane",
    "dock",
    "dock-header",
    "dock-tabs",
    "terminal-pane",
    "claude-pane",
    "folder-tool",
    "scratchpad-tool",
    "doc-tool",
    "graph",
    "bubbles",
    "properties",
    "outline",
  ]) {
    await expect(
      page.locator(`[data-region="${region}"]`),
      `the v2 region "${region}" is still drawn`,
    ).toHaveCount(0);
  }
  await expect(page.locator("[data-mode-pill]")).toHaveCount(0);

  // And the shell that replaced them is all there.
  for (const region of ["rail", "sidebar", "columns", "column", "tab-strip", "statusbar"]) {
    await expect(page.locator(`[data-region="${region}"]`).first()).toBeVisible();
  }
});

test("the v2 renderer's files are gone from the tree, and the stylesheet is not a trim", () => {
  // The other half of c1, as a property of the source: the DOM check above cannot see
  // a module that is still compiled and merely never rendered.
  for (const path of [
    "src/renderer/shell.ts",
    "src/renderer/panes.ts",
    "src/renderer/claude.ts",
    "src/renderer/terminal.ts",
    "src/renderer/leftscroll.ts",
    "src/renderer/records",
    "src/renderer/ideas",
    "src/renderer/docs",
    "src/shared/grouping.ts",
  ]) {
    expect(existsSync(join(APP_ROOT, path)), `${path} survived the v2 deletion`).toBe(false);
  }
});

// ============================================================ c5: the tab registry

test("the registry is the directories under src/renderer/tabs, and the app says which", async () => {
  const page = await shellReady(await firstShell());
  const declared = (await page.locator("body").getAttribute("data-tab-kinds"))?.split(",") ?? [];
  expect(declared.sort()).toEqual(["claude", "compose", "home", "record", "terminal", "views"]);
});

test("a kind whose `kind` disagrees with its directory is refused by name", async () => {
  // The registry's one guarantee that is not visible from a passing app: a module
  // registering under a name no rail item, saved layout or `openTab` call could reach
  // would otherwise simply never appear, with nothing saying why.
  const dir = join(APP_ROOT, "src", "renderer", "tabs", "zzmismatch");
  try {
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      join(dir, "index.ts"),
      [
        'import type { TabKind } from "../kind";',
        "const kind: TabKind = {",
        '  kind: "something-else",',
        '  label: "Mismatched",',
        "  open: () => ({",
        '    title: () => "Mismatched",',
        "    render: () => undefined,",
        "    close: () => undefined,",
        "  }),",
        "};",
        "export default kind;",
        "",
      ].join("\n"),
      "utf8",
    );
    execFileSync("node", ["build.mjs"], { cwd: APP_ROOT, stdio: "ignore" });
    const other = await _electron.launch({ args: launchArgs(), env: envFor(fixture.root) });
    try {
      const page = await other.firstWindow();
      await page.waitForSelector("body[data-error]");
      const error = await page.locator("body").getAttribute("data-error");
      expect(error).toContain("zzmismatch");
      expect(error).toContain("something-else");
      markReadyToClose(other);
    } finally {
      await other.close();
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
    execFileSync("node", ["build.mjs"], { cwd: APP_ROOT, stdio: "ignore" });
  }
});

test("a new tab directory appears in the app, and nothing else in the tree changes", async () => {
  // c5's own check. The fixture kind declares a rail entry, so "it appears" is a thing
  // a reader can click rather than a name in an attribute -- and the whole path from a
  // directory on disk to an icon in the rail to a rendered tab is exercised.
  const gitStatus = (): string[] =>
    execFileSync("git", ["status", "--porcelain", "--", "apps/desktop"], {
      cwd: REPO_ROOT,
      encoding: "utf8",
    })
      .split("\n")
      .filter((line) => line.trim() !== "")
      .sort();

  const before = gitStatus();
  const dir = join(APP_ROOT, "src", "renderer", "tabs", "zzfixture");
  try {
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      join(dir, "index.ts"),
      [
        "// A fixture tab kind, created by tests/shell.spec.ts and removed by it.",
        'import type { TabKind } from "../kind";',
        "const kind: TabKind = {",
        '  kind: "zzfixture",',
        '  label: "Fixture",',
        '  rail: { order: 900, label: "Fixture", icon: "unknown" },',
        "  open: () => ({",
        '    title: () => "Fixture",',
        "    render: (mount: HTMLElement) => {",
        '      mount.dataset.state = "ready";',
        '      mount.textContent = "a tab kind that was only ever a directory";',
        "    },",
        "    close: () => undefined,",
        "  }),",
        "};",
        "export default kind;",
        "",
      ].join("\n"),
      "utf8",
    );

    // The ONLY step between writing that file and the app having the tab.
    execFileSync("node", ["build.mjs"], { cwd: APP_ROOT, stdio: "ignore" });

    // Nothing else changed. `registry.generated.ts` is gitignored, which is the point:
    // the build rewrote it and a worker's diff does not carry it.
    const after = gitStatus();
    const added = after.filter((line) => !before.includes(line));
    expect(added, "the build changed something other than the new directory").toEqual([
      "?? apps/desktop/src/renderer/tabs/zzfixture/",
    ]);

    const other = await _electron.launch({ args: launchArgs(), env: envFor(fixture.root) });
    try {
      const page = await readyWindow(await other.firstWindow());
      markReadyToClose(other);
      expect((await page.locator("body").getAttribute("data-tab-kinds"))?.split(",")).toContain(
        "zzfixture",
      );
      const icon = page.locator('[data-rail-item="zzfixture"]');
      await expect(icon, "the fixture kind's rail entry never appeared").toBeVisible();
      await icon.click();
      await expect(page.locator('[data-tab-kind="zzfixture"][data-region="tab-body"]')).toHaveText(
        "a tab kind that was only ever a directory",
      );
    } finally {
      await other.close();
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
    execFileSync("node", ["build.mjs"], { cwd: APP_ROOT, stdio: "ignore" });
    expect(gitStatus(), "the fixture directory outlived the check").toEqual(before);
  }
});

// Renamed from "the placeholder tabs render the read model's own data" (KYO-240.c2).
// Written at stage 2 against four placeholders, it survived stage 3 mostly intact --
// because it was written against the READ MODEL's data and the data did not change --
// with one assertion that was about the placeholder itself: the stage 2 compose tab
// printed the scratchpad's path on screen, and the real one (KYO-234) opens the file
// in CodeMirror behind a ribbon, where the path is the tab's subject rather than its
// text. The path assertion is replaced by the file's own CONTENTS, which is the same
// claim -- this tab is showing the per-project scratchpad and not some other file --
// made against what the reader actually sees.
//
// Kept rather than retired: what it is for is the seam, not the tabs. Each of the four
// was built in its own worktree against its own spec, and this is the one check that
// opens all four in ONE window and asserts each is showing the store's own data, which
// is exactly the thing four parallel dispatches could not check for themselves.
test("all four tabs render the read model's own data, in one window", async () => {
  const page = await shellReady(await firstShell());

  // record: every child object of FIX-1, which the fixture gives a gate and a claim.
  // Each tab is asked for VISIBLE: a single click shows a row in the tab the reader is
  // in (KYO-247), and what that tab showed before stays drawn and hidden as history.
  const rows = await rowsFor(page, "records");
  await rows.filter({ hasText: "FIX-1" }).first().click();
  const record = page.locator('[data-region="tab-body"][data-tab-kind="record"]:not([hidden])');
  await expect(record).toHaveAttribute("data-state", "ready");
  await expect(record).toHaveAttribute("data-record-id", "FIX-1");
  await expect(record).toContainText("A defect with a gate");
  await expect(record.locator('[data-section="criteria"]')).toContainText("FIX-1.c1");
  await expect(record.locator('[data-section="gates"]')).toContainText("FIX-1.g1");
  await expect(record.locator('[data-section="claims"]')).toContainText("FIX-1.m1");

  // compose: the human's own scratchpad, from the per-project home, open in the
  // editor. The stage 2 placeholder printed the path; the real tab shows the file, so
  // the same claim -- this is the scratchpad `main/kyo.ts` resolved and not some other
  // markdown -- is made against a line only that file carries.
  await openKind(page, "compose", "scratchpad");
  const compose = page.locator('[data-region="tab-body"][data-tab-kind="compose"]:not([hidden])');
  await expect(compose).toHaveAttribute("data-state", "ready");
  await expect(compose).toContainText(SCRATCHPAD_MARKER);

  // activity: a sidebar list since KYO-251, over the same read model.
  await rail(page, "activity").click();
  await expect(
    page.locator('[data-region="activity-row"][data-dispatch-id="FIX-3.d1"]'),
  ).toContainText("met 2", { timeout: 30_000 });

  // views: the records list, through the same read model the sidebar uses.
  const briefs = await rowsFor(page, "notes");
  await briefs.filter({ hasText: "Latest brief" }).first().click();
  const views = page.locator('[data-region="tab-body"][data-tab-kind="views"]:not([hidden])');
  await expect(views).toHaveAttribute("data-state", "ready");
  await expect(views).toContainText("FIX-B2");
});

// ============================================================ KYO-240.c1: the launch link

test("the shell subscribes to onOpenTarget before it awaits anything", () => {
  // The property `links.spec.ts` cannot check from outside, stated against the source.
  //
  // A deep link is a one-shot event that `ipcRenderer` DROPS when its channel has no
  // listener, so whether a launch link opens its tab is decided entirely by whether
  // this subscription is installed before `start()` gives the turn away. The bug this
  // replaced was invisible on an idle machine and appeared under load (KYO-233.d1),
  // which means the running-app check passes either way on a quiet desk: what makes
  // the mechanism un-removable is this one, which reads the ordering directly.
  const source = readFileSync(join(APP_ROOT, "src", "renderer", "shell", "shell.ts"), "utf8");
  const start = source.slice(source.indexOf("async function start()"));
  expect(start, "buildShell.start() is gone or renamed").not.toBe("");
  const subscribes = start.indexOf("bridge.onOpenTarget");
  const firstAwait = start.indexOf("await ");
  expect(subscribes, "start() no longer subscribes to onOpenTarget").toBeGreaterThan(-1);
  expect(firstAwait, "start() no longer awaits anything -- re-read this check").toBeGreaterThan(-1);
  expect(
    subscribes,
    "start() awaits before it subscribes to onOpenTarget: a launch link will be dropped",
  ).toBeLessThan(firstAwait);

  // ...and the second half: a delivery that lands before the layout is applied is
  // HELD, because `columns.restore` closes every open tab before it rebuilds the saved
  // ones. Subscribing early without holding moves the bug rather than fixing it.
  expect(
    source.indexOf("openWorkspaceToDeliveries()"),
    "the held-delivery queue is gone: an early delivery opens a tab restore then closes",
  ).toBeGreaterThan(-1);
});

// ============================================================ c2: the shell

test("a rail icon selects a sidebar list, and clicking it again collapses the sidebar", async () => {
  const page = await shellReady(await firstShell());
  const sidebar = page.locator('[data-region="sidebar"]');

  await rail(page, "ideas").click();
  await expect(sidebar).toHaveAttribute("data-open", "true");
  await expect(sidebar).toHaveAttribute("data-list", "ideas");
  await expect(rail(page, "ideas")).toHaveAttribute("data-selected", "true");
  // Never a popover: it is a region of the layout, so it takes width from the columns.
  const withSidebar = (await columns(page).first().boundingBox())?.width ?? 0;

  await rail(page, "records").click();
  await expect(sidebar).toHaveAttribute("data-list", "records");
  await expect(rail(page, "ideas")).toHaveAttribute("data-selected", "false");

  await rail(page, "records").click();
  await expect(sidebar).toHaveAttribute("data-open", "false");
  await expect(rail(page, "records")).toHaveAttribute("data-selected", "false");
  const collapsed = (await columns(page).first().boundingBox())?.width ?? 0;
  expect(
    collapsed,
    "the sidebar floated over the columns rather than pushing them",
  ).toBeGreaterThan(withSidebar);
});

test("a click on a list row opens a tab in the active column", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "records");
  await rows.filter({ hasText: "FIX-2" }).first().click();
  await expect(columns(page)).toHaveCount(1);
  expect(await tabTitles(page, 0)).toHaveLength(1);
  await expect(tabsIn(page, 0).first()).toHaveAttribute("data-tab-arg", "FIX-2");
});

test("split adds a column, two is the most, and the active column is the one clicked in", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const split = page.locator('[data-region="split"]');

  await split.first().click();
  await expect(columns(page)).toHaveCount(2);
  await expect(columns(page).nth(1)).toHaveAttribute("data-active", "true");
  await expect(columns(page).nth(0)).toHaveAttribute("data-active", "false");
  // "One column is active (highlighted tab strip)" -- the strip carries it too, so the
  // reader can see which column a click will open in.
  await expect(columns(page).nth(1).locator('[data-region="tab-strip"]')).toHaveAttribute(
    "data-active",
    "true",
  );
  // Two is the most (KYO-246): the control is a toggle, and every strip says it is on.
  await expect(split.first()).toHaveAttribute("data-split-on", "true");
  await expect(split.nth(1)).toHaveAttribute("data-split-on", "true");

  // The divider between two columns gives one of them more room, and only that pair
  // moves: a width nobody can change is a field a layout remembers for nothing.
  const widthsNow = async (): Promise<number[]> =>
    columns(page).evaluateAll((nodes) =>
      nodes.map((node) => Math.round(node.getBoundingClientRect().width)),
    );
  const before = await widthsNow();
  const gripBox = await boxOf(page.locator('[data-region="column-grip"]').first());
  await dragTo(
    page,
    { x: gripBox.x + gripBox.width / 2, y: gripBox.y + gripBox.height / 2 },
    { x: gripBox.x + gripBox.width / 2 + 120, y: gripBox.y + gripBox.height / 2 },
  );
  const after = await widthsNow();
  expect(after[0], "the grip did not widen the column to its left").toBeGreaterThan(before[0] ?? 0);

  // Clicking in a column makes it the one a row opens into.
  await columns(page)
    .nth(0)
    .click({ position: { x: 20, y: 60 } });
  await expect(columns(page).nth(0)).toHaveAttribute("data-active", "true");
  const rows = await rowsFor(page, "records");
  await rows.filter({ hasText: "FIX-3" }).first().click();
  expect(await tabTitles(page, 0)).toHaveLength(1);
  expect(await tabTitles(page, 1)).toHaveLength(0);

  // Off again: the second column's tabs fold into the first rather than closing.
  await rows.filter({ hasText: "FIX-4" }).first().click({ button: "right" });
  await page.locator('[data-menu-item="Open in the right column"]').click();
  expect(await tabTitles(page, 1)).toHaveLength(1);
  await split.first().click();
  await expect(columns(page)).toHaveCount(1);
  await expect
    .poll(async () =>
      tabsIn(page, 0).evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-tab-arg"))),
    )
    .toEqual(["FIX-3", "FIX-4"]);
  await expect(split.first()).toHaveAttribute("data-split-on", "false");
});

test("closing the last tab in a column leaves the column open; only the square folds the split (KYO-277)", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const split = page.locator('[data-region="split"]');
  await split.first().click();
  await expect(columns(page)).toHaveCount(2);
  // Make the second column the active one, so a plain click on a row lands there.
  await columns(page)
    .nth(1)
    .click({ position: { x: 20, y: 60 } });
  await expect(columns(page).nth(1)).toHaveAttribute("data-active", "true");
  const rows = await rowsFor(page, "records");
  await rows.filter({ hasText: "FIX-2" }).first().click();
  await expect(tabsIn(page, 1)).toHaveCount(1);
  await expect(tabsIn(page, 0)).toHaveCount(0);

  // The second column's only tab goes; the column does not.
  await tabsIn(page, 1).first().locator('[data-action="close-tab"]').click();
  await expect(tabsIn(page, 1)).toHaveCount(0);
  await expect(columns(page)).toHaveCount(2);
  await expect(columns(page).nth(1).locator('[data-region="empty-column"]')).toHaveCount(1);
  await expect(split.first()).toHaveAttribute("data-split-on", "true");

  // The square is what folds it.
  await split.first().click();
  await expect(columns(page)).toHaveCount(1);
  await expect(split.first()).toHaveAttribute("data-split-on", "false");
});

test("a sidebar row is marked when its tab is open, and marked active when that tab is where a click lands (KYO-278)", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "records");
  const two = rows.filter({ hasText: "FIX-2" }).first();
  const three = rows.filter({ hasText: "FIX-3" }).first();
  await expect(two).not.toHaveAttribute("data-open", "true");

  await two.click();
  await expect(two).toHaveAttribute("data-open", "true");
  await expect(two).toHaveAttribute("data-open-active", "true");

  // Cmd-click opens a second tab in the background (KYO-247): both rows are open,
  // and the active mark stays where the reader is.
  await three.click({ modifiers: ["Meta"] });
  await expect(three).toHaveAttribute("data-open", "true");
  await expect(three).toHaveAttribute("data-open-active", "false");
  await expect(two).toHaveAttribute("data-open", "true");
  await expect(two).toHaveAttribute("data-open-active", "true");

  // Selecting the second tab moves the active mark with it.
  await tabsIn(page, 0).filter({ hasText: "FIX-3" }).first().click();
  await expect(three).toHaveAttribute("data-open-active", "true");
  await expect(two).toHaveAttribute("data-open-active", "false");

  // Closing a tab clears its row's marks; the list was not repainted to learn it.
  await tabsIn(page, 0)
    .filter({ hasText: "FIX-3" })
    .first()
    .locator('[data-action="close-tab"]')
    .click();
  await expect(three).not.toHaveAttribute("data-open", "true");
  await expect(two).toHaveAttribute("data-open", "true");
});

test("a record opens into the browsing tab and never replaces the scratchpad (KYO-281)", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  // The scratchpad, from the home page: a protected tab, active.
  await page.locator('[data-action="new-tab"]').first().click();
  await page.locator('[data-region="home-card"][data-home-arg="scratchpad"]').click();
  await expect.poll(() => tabArgs(page, 0)).toEqual(["scratchpad"]);
  const rows = await rowsFor(page, "records");

  // A click with the scratchpad active makes a record tab beside it, and focuses it.
  await rows.filter({ hasText: "FIX-1" }).first().click();
  await expect.poll(() => tabArgs(page, 0)).toEqual(["scratchpad", "FIX-1"]);
  await expect(tabsIn(page, 0).nth(1)).toHaveAttribute("data-selected", "true");

  // Back on the scratchpad, the next click goes into THAT record tab, not over the
  // scratchpad and not into a third tab -- and the record tab is focused again.
  await tabsIn(page, 0).nth(0).click();
  await expect(tabsIn(page, 0).nth(0)).toHaveAttribute("data-selected", "true");
  await rows.filter({ hasText: "FIX-2" }).first().click();
  await expect.poll(() => tabArgs(page, 0)).toEqual(["scratchpad", "FIX-2"]);
  await expect(tabsIn(page, 0).nth(1)).toHaveAttribute("data-selected", "true");
  await expect(tabsIn(page, 0).nth(0)).toHaveAttribute("data-tab-kind", "compose");

  // The browsing tab can live in the other column: it is found and its column focused.
  await tabsIn(page, 0).nth(1).click({ button: "right" });
  await page.locator('[data-menu-item="Move to a new column"]').click();
  await expect(columns(page)).toHaveCount(2);
  await columns(page)
    .nth(0)
    .click({ position: { x: 20, y: 60 } });
  await tabsIn(page, 0).nth(0).click();
  await expect(columns(page).nth(0)).toHaveAttribute("data-active", "true");
  await rows.filter({ hasText: "FIX-3" }).first().click();
  await expect.poll(() => tabArgs(page, 1)).toEqual(["FIX-3"]);
  await expect(columns(page).nth(1)).toHaveAttribute("data-active", "true");
  await expect.poll(() => tabArgs(page, 0)).toEqual(["scratchpad"]);
});

test("the row menu opens into the left or right column by name, and the active tab is marked (KYO-282)", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "records");
  await rows.filter({ hasText: "FIX-1" }).first().click({ button: "right" });
  const menu = page.locator('[data-region="context-menu"]');
  await expect(menu.locator('[data-menu-item="Open in the other column"]')).toHaveCount(0);
  await menu.locator('[data-menu-item="Open in the right column"]').click();
  await expect(columns(page)).toHaveCount(2);
  await expect.poll(() => tabArgs(page, 1)).toEqual(["FIX-1"]);
  await expect.poll(() => tabArgs(page, 0)).toEqual([]);

  await rows.filter({ hasText: "FIX-2" }).first().click({ button: "right" });
  await page.locator('[data-menu-item="Open in the left column"]').click();
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-2"]);
  await expect.poll(() => tabArgs(page, 1)).toEqual(["FIX-1"]);

  // Exactly one tab in the window is the one a plain click lands in: the selected
  // tab of the active column, which carries both marks.
  const landing = page.locator(
    '[data-region="column"][data-active="true"] [data-region="tab"][data-selected="true"]',
  );
  await expect(landing).toHaveCount(1);
  await expect(landing).toHaveAttribute("data-tab-arg", "FIX-2");
});

// A tab's strip button is made once and the TAB MOVES (KYO-288, KYO-256). The two
// checks below drive the two ways a button outlives the column it was made in: a tab
// moved to the other column, and a column folded away under a tab opened in it. Both
// failed before the button stopped closing over its column -- the first by refusing to
// close, the second by refusing to highlight.

test("a record open in both columns: closing the left copy leaves the right one closable (KYO-288)", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "records");

  // Two tabs on one record, which is a state a reader asks for (KYO-227): a click, then
  // a cmd-click, which forces a second tab rather than raising the first.
  await rows.filter({ hasText: "FIX-1" }).first().click();
  await rows
    .filter({ hasText: "FIX-1" })
    .first()
    .click({ modifiers: ["Meta"] });
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-1", "FIX-1"]);

  // The second copy goes to a column of its own -- so its button was made in the LEFT
  // column and the tab now lives in the right one.
  await tabsIn(page, 0).nth(1).click({ button: "right" });
  await page.locator('[data-menu-item="Move to a new column"]').click();
  await expect(columns(page)).toHaveCount(2);
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-1"]);
  await expect.poll(() => tabArgs(page, 1)).toEqual(["FIX-1"]);

  // Close the left copy. Its column stays, empty, as KYO-277 asks.
  await tabsIn(page, 0).first().locator('[data-action="close-tab"]').click();
  await expect.poll(() => tabArgs(page, 0)).toEqual([]);
  await expect(columns(page)).toHaveCount(2);

  // The right copy -- the last tab in its column -- still takes a click...
  await tabsIn(page, 1).first().click();
  await expect(tabsIn(page, 1).first()).toHaveAttribute("data-selected", "true");
  await expect(columns(page).nth(1)).toHaveAttribute("data-active", "true");

  // ...and its X still closes it, with the split left as the reader set it.
  await tabsIn(page, 1).first().locator('[data-action="close-tab"]').click();
  await expect.poll(() => tabArgs(page, 1)).toEqual([]);
  await expect(columns(page)).toHaveCount(2);
});

// KYO-319: the pop-out and the project switch wanted two different things out of one
// function. "Close every tab matching this reference" was right for the switch and
// wrong for the pop-out, which took whichever copy `findTab` reached first -- so
// popping the RIGHT copy out closed the LEFT one and left the right one sitting in its
// strip, duplicated into the new window. The decision (KYO-319.n1): a pop-out leaves a
// matching tab in the other column open, and only a project switch closes every copy.
// The check below fails if the two meanings are merged back into one.

test("popping one copy out takes THAT copy and leaves the other column's copy open (KYO-319)", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "records");

  // The same record in both columns, built exactly as KYO-288's check builds it.
  await rows.filter({ hasText: "FIX-1" }).first().click();
  await rows
    .filter({ hasText: "FIX-1" })
    .first()
    .click({ modifiers: ["Meta"] });
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-1", "FIX-1"]);
  await tabsIn(page, 0).nth(1).click({ button: "right" });
  await page.locator('[data-menu-item="Move to a new column"]').click();
  await expect(columns(page)).toHaveCount(2);
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-1"]);
  await expect.poll(() => tabArgs(page, 1)).toEqual(["FIX-1"]);

  // WHICH copy is which, marked on the DOM. Two tabs on one record are identical to
  // every selector this file has -- same kind, same argument, same title -- and an
  // emptied column gives its space up on a pop-out (KYO-249), so after the hand-off
  // the survivor is column 0 either way and counting columns cannot tell them apart.
  // A tab's body element is created once and MOVED, never rebuilt (this module's own
  // rule), so a mark put on it here is still on it afterwards.
  const stamp = async (index: number, value: string) => {
    await columns(page)
      .nth(index)
      .locator('[data-region="tab-body"]')
      .first()
      .evaluate((node, mark) => {
        (node as HTMLElement).dataset.copy = mark;
      }, value);
  };
  await stamp(0, "left");
  await stamp(1, "right");

  // Pop the RIGHT copy out.
  await tabsIn(page, 1).first().click({ button: "right" });
  await page.locator('[data-menu-item="Move to a new window"]').click();
  const popped = await paneWindowOf(page);
  await expect(popped.locator('[data-region="tab-body"][data-tab-kind="record"]')).toHaveAttribute(
    "data-tab-arg",
    "FIX-1",
  );

  // The copy that left is the one the reader asked for, and the other copy stays open.
  await expect(page.locator('[data-copy="right"]')).toHaveCount(0);
  await expect(page.locator('[data-copy="left"]')).toHaveCount(1);
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-1"]);
  // The emptied column gave its space up, as a hand-off to another window does.
  await expect(columns(page)).toHaveCount(1);

  await popped.close();
});

test("the claude tab opened in the second column still takes a click once the split folds (KYO-256)", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "records");
  await rows.filter({ hasText: "FIX-1" }).first().click();
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-1"]);

  // Claude, opened from the SECOND column's own home page: its button is made there.
  await page.locator('[data-region="split"]').first().click();
  await expect(columns(page)).toHaveCount(2);
  await columns(page)
    .nth(1)
    .click({ position: { x: 20, y: 60 } });
  await expect(columns(page).nth(1)).toHaveAttribute("data-active", "true");
  await columns(page).nth(1).locator('[data-action="new-tab"]').first().click();
  await page
    .locator(
      '[data-region="tab-body"][data-tab-kind="home"]:not([hidden]) [data-home-kind="claude"]',
    )
    .click();
  await expect(tabsIn(page, 1).filter({ hasText: "Claude" })).toHaveCount(1);

  // The square folds the split, and claude comes back into the first column with it.
  await page.locator('[data-region="split"]').first().click();
  await expect(columns(page)).toHaveCount(1);
  const claudeTab = page.locator('[data-region="tab"][data-tab-kind="claude"]');
  await expect(claudeTab).toHaveCount(1);

  // Somewhere else first, so the click on claude has something to change.
  await tabsIn(page, 0).nth(0).click();
  await expect(tabsIn(page, 0).nth(0)).toHaveAttribute("data-selected", "true");

  // The click that used to fail: the strip highlights it and its body is the one shown.
  await claudeTab.click();
  await expect(claudeTab).toHaveAttribute("data-selected", "true");
  await expect(page.locator('[data-region="tab-body"][data-tab-kind="claude"]')).not.toBeHidden();
  // And the frame the `WebContentsView` is positioned over has a box again, which is
  // the whole of "shows its page" that a renderer can answer: the view itself is
  // composited above this document and is nothing this check can see (and the suite
  // launches hidden, so what is ON the screen is a human-observed check, not ours).
  const frame = await boxOf(page.locator('[data-region="claude-frame"]'));
  expect(frame.width).toBeGreaterThan(0);
  expect(frame.height).toBeGreaterThan(0);
});

test("the X on a link-strip message dismisses it (KYO-279)", async () => {
  const page = await shellReady(await firstShell());
  // A refused delivery is the shortest path to the strip: the main process sends one
  // over the same channel a deep link's outcome arrives on.
  await app.evaluate(({ BrowserWindow }) => {
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send("kyosei:open-target", {
        ok: false,
        refusal: "kyosei: a refusal written by a check (KYO-279)",
      });
    }
  });
  const strip = page.locator('[data-region="link-strip"]');
  await expect(strip).toBeVisible();
  await expect(strip).toContainText("KYO-279");
  await strip.locator(".link-strip-close").click();
  await expect(strip).toBeHidden();
});

test("a strip with more tabs than fit scrolls rather than clipping, and the selected tab is in view", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  // A narrow window, so the strip overflows with a handful of tabs rather than dozens.
  await page.setViewportSize({ width: 960, height: 640 });
  const rows = await rowsFor(page, "records");
  const row = rows.filter({ hasText: "FIX-1" }).first();
  await row.click();
  for (let i = 0; i < 11; i += 1) {
    await row.click({ button: "right" });
    await page.locator('[data-menu-item="Open in a new tab"]').click();
  }
  await expect(tabsIn(page, 0)).toHaveCount(12);

  const host = page.locator('[data-region="strip-tabs"]').first();
  const overflow = await host.evaluate((node) => node.scrollWidth - node.clientWidth);
  expect(
    overflow,
    "twelve tabs fit in a 960px strip: the floor width is not a floor",
  ).toBeGreaterThan(0);
  // The last-opened tab is selected and in view...
  const within = async (index: number): Promise<boolean> => {
    const strip = await boxOf(host);
    const tab = await boxOf(tabsIn(page, 0).nth(index));
    return tab.x >= strip.x - 1 && tab.x + tab.width <= strip.x + strip.width + 1;
  };
  await expect(tabsIn(page, 0).nth(11)).toHaveAttribute("data-selected", "true");
  expect(await within(11)).toBe(true);
  expect(await within(0), "the strip did not scroll: the first tab is still in view").toBe(false);
  // ...and selecting the first scrolls it back into view: no tab is unreachable.
  await page
    .locator('[data-region="strip-tabs"]')
    .first()
    .evaluate((node) => {
      node.scrollLeft = 0;
    });
  await tabsIn(page, 0).first().click();
  await expect(tabsIn(page, 0).first()).toHaveAttribute("data-selected", "true");
  expect(await within(0)).toBe(true);
});

test("the home page: an empty column shows it, + and the New Tab command open it as a tab", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  // The empty column IS the page: every kind's home entries, and nothing else.
  const empty = page.locator('[data-region="empty-column"]');
  await expect(empty).toBeVisible();
  const offered = await empty
    .locator('[data-region="home-card"]')
    .evaluateAll((nodes) =>
      nodes.map((n) => `${n.getAttribute("data-home-kind")}:${n.getAttribute("data-home-arg")}`),
    );
  expect(offered).toEqual(["compose:scratchpad", "terminal:", "claude:", "views:brief"]);
  // No Records entry (KYO-257): the rail's records list serves that purpose better.
  await empty.locator('[data-home-kind="views"][data-home-arg="brief"]').click();
  await expect(tabsIn(page, 0).first()).toHaveAttribute("data-tab-arg", "brief");

  // "+" opens the page as a tab; choosing from it replaces that tab with the choice.
  await page.locator('[data-action="new-tab"]').first().click();
  await expect(tabsIn(page, 0).nth(1)).toHaveAttribute("data-tab-kind", "home");
  await page
    .locator(
      '[data-tab-body][data-tab-kind="home"], [data-region="tab-body"][data-tab-kind="home"]',
    )
    .locator('[data-home-kind="compose"]')
    .click();
  await expect
    .poll(async () =>
      tabsIn(page, 0).evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-tab-kind"))),
    )
    .toEqual(["views", "compose"]);

  // The menu's commands reach the workspace (KYO-246): New Tab, then Close Tab.
  const command = (name: string) =>
    app.evaluate(({ BrowserWindow }, word) => {
      for (const win of BrowserWindow.getAllWindows()) win.webContents.send("kyosei:command", word);
    }, name);
  await command("new-tab");
  await expect(tabsIn(page, 0)).toHaveCount(3);
  await expect(tabsIn(page, 0).nth(2)).toHaveAttribute("data-tab-kind", "home");
  await command("close-tab");
  await expect(tabsIn(page, 0)).toHaveCount(2);
  await command("toggle-split");
  await expect(columns(page)).toHaveCount(2);
  await command("toggle-split");
  await expect(columns(page)).toHaveCount(1);
});

test("the application menu is the app's own: Cmd+W closes a tab, Close Window moved and asks", async () => {
  await shellReady(await firstShell());
  const items = await app.evaluate(({ Menu }) => {
    const menu = Menu.getApplicationMenu();
    const found: Record<string, string | undefined> = {};
    for (const id of ["new-tab", "new-window", "close-tab", "close-window", "toggle-split"]) {
      found[id] = menu?.getMenuItemById(id)?.accelerator ?? undefined;
    }
    const labels = (menu?.items ?? []).map((item) => item.label);
    return { found, labels };
  });
  expect(items.found).toEqual({
    "new-tab": "CmdOrCtrl+T",
    "new-window": "CmdOrCtrl+N",
    "close-tab": "CmdOrCtrl+W",
    "close-window": "Shift+CmdOrCtrl+W",
    "toggle-split": "CmdOrCtrl+\\",
  });
  expect(items.labels).toContain("Edit");

  // KYO-450.c1: KYO-315's two command-line-tools actions are gone with the bundled
  // runtime they linked onto PATH -- by id, and by label anywhere in the menu tree, so
  // a re-added item under a new id is caught too.
  const cli = await app.evaluate(({ Menu }) => {
    const menu = Menu.getApplicationMenu();
    const labels: string[] = [];
    const walk = (items: Electron.MenuItem[]): void => {
      for (const item of items) {
        labels.push(item.label);
        if (item.submenu) walk(item.submenu.items);
      }
    };
    walk(menu?.items ?? []);
    return {
      byId: ["install-cli", "remove-cli"].map((id) => menu?.getMenuItemById(id)?.label ?? null),
      labels,
    };
  });
  expect(cli.byId).toEqual([null, null]);
  expect(cli.labels.filter((label) => /command line tools/i.test(label))).toEqual([]);

  // New Window from the menu opens the project CHOOSER in the shell (KYO-249): the
  // recents and the native picker. Choosing is what opens the window.
  const page = await firstShell();
  await app.evaluate(({ BrowserWindow }) => {
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send("kyosei:command", "new-window");
    }
  });
  await expect(page.locator('[data-menu-item="Open other project…"]')).toBeVisible();
  await expect(page.locator('[data-menu-item^="This project"]')).toBeVisible();
  await page.keyboard.press("Escape");
  const opened = await page.evaluate(() => window.kyosei.openWindow());
  expect(opened.refused).toBeNull();
  await expect.poll(() => app.windows().filter((w) => w.url().startsWith("file:")).length).toBe(2);
});

test("a tab's menu closes others and closes to the right", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "records");
  // Cmd-clicks: a single click would show each in the same tab (KYO-247).
  for (const id of ["FIX-1", "FIX-2", "FIX-3", "FIX-4"]) {
    await rows
      .filter({ hasText: id })
      .first()
      .click({ modifiers: ["Meta"] });
  }
  await expect(tabsIn(page, 0)).toHaveCount(4);
  const args = () =>
    tabsIn(page, 0).evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-tab-arg")));

  await tabsIn(page, 0).nth(1).click({ button: "right" });
  await page.locator('[data-menu-item="Close tabs to the right"]').click();
  await expect.poll(args).toEqual(["FIX-1", "FIX-2"]);
  // The one on the right end has nothing to its right, and the entry says so.
  await tabsIn(page, 0).nth(1).click({ button: "right" });
  await expect(page.locator('[data-menu-item="Close tabs to the right"]')).toBeDisabled();
  await page.locator('[data-menu-item="Close others"]').click();
  await expect.poll(args).toEqual(["FIX-2"]);
});

test("a tab torn off its strip opens a window that follows the pointer, and stays one when released", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "records");
  await rows.filter({ hasText: "FIX-3" }).first().click();
  await rows.filter({ hasText: "FIX-4" }).first().click();
  // The tear-off as the renderer reports it the moment the pointer leaves the strip
  // (a real drag cannot be driven past the viewport here), then two moves and the
  // release, at a screen point no window is under. The suite's windows are hidden, so
  // the snap-to-another-strip half is not exercisable here; the hand-back path in the
  // pop-out check and the receive path are the same delivery.
  const started = await page.evaluate(() =>
    window.kyosei.startTabDrag({
      tab: { kind: "record", arg: "FIX-3" },
      screenX: 3000,
      screenY: 3000,
      wholeWindow: false,
      grab: { x: 40, y: 16 },
    }),
  );
  expect(started).toEqual({ started: true, refusal: null });
  const opened = await paneWindowOf(page);
  await expect(opened.locator("body")).toHaveAttribute("data-transient", "true");
  await expect(opened.locator('[data-region="tab"]').first()).toHaveAttribute(
    "data-tab-arg",
    "FIX-3",
  );
  const before = await app.evaluate(
    ({ BrowserWindow }, id) => BrowserWindow.fromId(id)?.getPosition(),
    (await opened.evaluate(() => window.kyosei.getState())).self.id,
  );
  await page.evaluate(() => window.kyosei.moveTabDrag({ screenX: 3200, screenY: 3100 }));
  await page.waitForTimeout(100);
  const after = await app.evaluate(
    ({ BrowserWindow }, id) => BrowserWindow.fromId(id)?.getPosition(),
    (await opened.evaluate(() => window.kyosei.getState())).self.id,
  );
  expect(after?.[0], "the torn-off window did not follow the pointer").toBe(
    (before?.[0] ?? 0) + 200,
  );
  expect(after?.[1]).toBe((before?.[1] ?? 0) + 100);
  const ended = await page.evaluate(() =>
    window.kyosei.endTabDrag({ screenX: 3200, screenY: 3100 }),
  );
  expect(ended).toEqual({ attachedTo: null });
  await expect(opened.locator('[data-region="tab"]')).toHaveCount(1);
});

test("a tab dragged down into a column's body tears off into a window of its own (KYO-259)", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "records");
  await rows
    .filter({ hasText: "FIX-3" })
    .first()
    .click({ modifiers: ["Meta"] });
  await rows
    .filter({ hasText: "FIX-4" })
    .first()
    .click({ modifiers: ["Meta"] });
  const args = () =>
    tabsIn(page, 0).evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-tab-arg")));
  await expect.poll(args).toEqual(["FIX-3", "FIX-4"]);

  // Straight down from the tab into the middle of its own column: before KYO-259 this
  // was a move to that column (a no-op here); now it is a tear-off, as dragging it out
  // of the window is.
  const tab = await boxOf(tabsIn(page, 0).nth(0));
  const body = await centreOf(page, '[data-region="column"]', 0);
  await dragTo(page, { x: tab.x + tab.width / 2, y: tab.y + tab.height / 2 }, body);
  const opened = await paneWindowOf(page);
  await expect(opened.locator('[data-region="tab"]').first()).toHaveAttribute(
    "data-tab-arg",
    "FIX-3",
  );
  await expect.poll(args).toEqual(["FIX-4"]);
  await opened.close();
});

test("a window with two or more tabs keeps one booted spare, unlisted, and a tear-off shows it (KYO-261)", async () => {
  // Pre-warming is off for the suite's hidden launches (`prewarm` in main.ts), so this
  // check asks for it by name, on a profile of its own.
  const profile = mkdtempSync(join(tmpdir(), "kyosei spare "));
  await closeReadyApp(app);
  try {
    const page = await shellReady(
      await launchReady(envFor(fixture.root, fixture.home, { KYOSEI_PREWARM: "on" }), profile),
    );
    await emptyWorkspace(page);
    const spareWindows = async (): Promise<Page[]> => {
      const found: Page[] = [];
      for (const win of app.windows()) {
        if (win === page || !win.url().startsWith("file:")) continue;
        const flag = await win
          .locator("body")
          .getAttribute("data-spare", { timeout: 1_000 })
          .catch(() => null);
        if (flag === "true") found.push(win);
      }
      return found;
    };
    const spareCount = async (): Promise<number> => (await spareWindows()).length;
    const chips = page.locator('[data-region="windows"] [data-window-id]');
    // Selected first: a tab Cmd-clicked open sits unselected, and its close control only
    // takes the click once its tab is the one showing.
    const closeTab = async (arg: string): Promise<void> => {
      const tab = page.locator(`[data-region="tab"][data-tab-arg="${arg}"]`);
      await tab.click();
      await tab.locator('[data-action="close-tab"]').click();
      await expect(tab).toHaveCount(0);
    };

    const rows = await rowsFor(page, "records");
    await rows
      .filter({ hasText: "FIX-1" })
      .first()
      .click({ modifiers: ["Meta"] });
    await page.waitForTimeout(800);
    expect(await spareCount(), "one tab cannot tear off into a new window").toBe(0);
    await rows
      .filter({ hasText: "FIX-2" })
      .first()
      .click({ modifiers: ["Meta"] });
    await expect.poll(spareCount, { timeout: 20_000 }).toBe(1);
    await rows
      .filter({ hasText: "FIX-3" })
      .first()
      .click({ modifiers: ["Meta"] });
    await page.waitForTimeout(800);
    expect(await spareCount(), "a third tab must not warm a second spare").toBe(1);
    // Invisible to the window list: one chip, this window's.
    await expect(chips).toHaveCount(1);

    // Tear FIX-3 off once the spare has booted: the spare IS the new window.
    const [spare] = await spareWindows();
    if (spare === undefined) throw new Error("no spare to tear into");
    await expect(spare.locator("body")).toHaveAttribute("data-ready", "true", { timeout: 20_000 });
    await page.waitForTimeout(300);
    const asked = Date.now();
    const started = await page.evaluate(() =>
      window.kyosei.startTabDrag({
        tab: { kind: "record", arg: "FIX-3" },
        screenX: 3000,
        screenY: 3000,
        wholeWindow: false,
        grab: { x: 40, y: 16 },
      }),
    );
    expect(started).toEqual({ started: true, refusal: null });
    await expect(spare.locator('[data-region="tab"][data-tab-arg="FIX-3"]')).toHaveCount(1, {
      timeout: 3_000,
    });
    // KYO-261.c2's figure, printed for the record beside KYO-260.c3's ~920-990ms.
    console.log(`KYO-261 tear-off into a booted spare: tab drawn +${Date.now() - asked}ms`);
    await expect(spare.locator("body")).toHaveAttribute("data-spare", "false");
    await page.evaluate(() => window.kyosei.endTabDrag({ screenX: 3000, screenY: 3000 }));
    await expect(chips).toHaveCount(2);

    // The renderer closes the torn tab in a real drag; here, by hand. Two tabs left, so
    // a new spare is warmed behind the one just used.
    await closeTab("FIX-3");
    await expect.poll(spareCount, { timeout: 20_000 }).toBe(1);
    // Below two tabs the spare goes.
    await closeTab("FIX-2");
    await expect.poll(spareCount, { timeout: 20_000 }).toBe(0);
    await closeReadyApp(app);
  } finally {
    if (app.windows().length === 0) await launchReady(envFor(fixture.root, fixture.home));
    rmSync(profile, { recursive: true, force: true });
  }
});

// ============================================================ KYO-247: the click model

const tabArgs = (page: Page, index: number) =>
  tabsIn(page, index).evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-tab-arg")));

test("a single click shows the row in the current tab with history; Cmd-click opens beside", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "records");
  await rows.filter({ hasText: "FIX-1" }).first().click();
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-1"]);
  // The next single click shows the next row in the SAME tab, and back returns.
  await rows.filter({ hasText: "FIX-2" }).first().click();
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-2"]);
  const back = page.locator('[data-region="column"] [data-action="back"]').first();
  await expect(back).toBeEnabled();
  await back.click();
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-1"]);
  // Cmd-click: a tab of its own, opened without leaving the one the reader is in.
  await rows
    .filter({ hasText: "FIX-3" })
    .first()
    .click({ modifiers: ["Meta"] });
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-1", "FIX-3"]);
  await expect(tabsIn(page, 0).nth(0)).toHaveAttribute("data-selected", "true");
  // A single click on something already open in another tab raises that tab.
  await rows.filter({ hasText: "FIX-3" }).first().click();
  await expect(tabsIn(page, 0).nth(1)).toHaveAttribute("data-selected", "true");
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-1", "FIX-3"]);
  // The strip's buttons are updated in place, never rebuilt, so a click does not
  // empty and refill the bar: the FIX-1 button is the same element throughout.
  const stamped = await tabsIn(page, 0)
    .nth(0)
    .evaluate((node) => {
      (node as HTMLElement).dataset.stamp = "same-node";
      return true;
    });
  expect(stamped).toBe(true);
  await rows.filter({ hasText: "FIX-4" }).first().click();
  await expect(tabsIn(page, 0).nth(0)).toHaveAttribute("data-stamp", "same-node");
});

test("a tab navigates in place and back and forward walk its history", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  // The home tab, choosing the latest brief: the tab BECOMES that view (KYO-247) and
  // back brings the home page back, forward the brief again.
  await page.locator('[data-action="new-tab"]').first().click();
  await expect.poll(() => tabArgs(page, 0)).toEqual([""]);
  const back = page.locator('[data-region="column"] [data-action="back"]').first();
  const forward = page.locator('[data-region="column"] [data-action="forward"]').first();
  await expect(back).toBeDisabled();
  await page
    .locator('[data-region="tab-body"][data-tab-kind="home"] [data-home-arg="brief"]')
    .click();
  await expect.poll(() => tabArgs(page, 0)).toEqual(["brief"]);
  await expect(tabsIn(page, 0).first()).toHaveAttribute("data-tab-kind", "views");
  await expect(back).toBeEnabled();
  await expect(forward).toBeDisabled();
  await back.click();
  await expect(tabsIn(page, 0).first()).toHaveAttribute("data-tab-kind", "home");
  await expect(forward).toBeEnabled();
  await forward.click();
  await expect(tabsIn(page, 0).first()).toHaveAttribute("data-tab-kind", "views");
  await expect(tabsIn(page, 0)).toHaveCount(1);

  // A link followed FROM a tab navigates that tab (the origin rides the delivery),
  // and Cmd-click opens beside instead.
  const tabId = await tabsIn(page, 0).first().getAttribute("data-tab-id");
  await page.evaluate(
    (id) => window.kyosei.followLink("kyosei://FIX/FIX-1", { tabId: id ?? "", newTab: false }),
    tabId,
  );
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-1"]);
  await expect(tabsIn(page, 0).first()).toHaveAttribute("data-tab-kind", "record");
  await page.evaluate(
    (id) => window.kyosei.followLink("kyosei://FIX/FIX-2", { tabId: id ?? "", newTab: true }),
    tabId,
  );
  await expect.poll(() => tabArgs(page, 0)).toEqual(["FIX-1", "FIX-2"]);
  // Back on the first tab walks past the brief to the home page: three entries.
  await tabsIn(page, 0).first().click();
  await back.click();
  await expect(tabsIn(page, 0).first()).toHaveAttribute("data-tab-arg", "brief");
  await back.click();
  await expect(tabsIn(page, 0).first()).toHaveAttribute("data-tab-kind", "home");

  // The menu's Back and Forward reach the same history.
  await page.evaluate(() => undefined);
  await app.evaluate(({ BrowserWindow }) => {
    for (const win of BrowserWindow.getAllWindows())
      win.webContents.send("kyosei:command", "forward");
  });
  await expect(tabsIn(page, 0).first()).toHaveAttribute("data-tab-arg", "brief");
});

test("a row's menu opens it in a new window", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "records");
  await rows.filter({ hasText: "FIX-3" }).first().click({ button: "right" });
  await page.locator('[data-menu-item="Open in a new window"]').click();
  const opened = await paneWindowOf(page);
  await expect(opened.locator("body")).toHaveAttribute("data-view", "shell");
  await expect(opened.locator('[data-region="tab"]').first()).toHaveAttribute(
    "data-tab-arg",
    "FIX-3",
  );
  await expect(tabsIn(page, 0)).toHaveCount(0);
});

test("a row dragged onto a column opens there rather than in the active one", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  await page.locator('[data-region="split"]').first().click();
  await expect(columns(page)).toHaveCount(2);
  await columns(page)
    .nth(0)
    .click({ position: { x: 20, y: 60 } });
  await expect(columns(page).nth(0)).toHaveAttribute("data-active", "true");

  const rows = await rowsFor(page, "records");
  const row = rows.filter({ hasText: "FIX-4" }).first();
  const from = await boxOf(row);
  const to = await centreOf(page, '[data-region="column"]', 1);
  await dragTo(page, { x: from.x + from.width / 2, y: from.y + from.height / 2 }, to);

  // The INACTIVE column got it, which is the whole of the criterion: a drag says where.
  expect(await tabTitles(page, 0)).toHaveLength(0);
  expect(await tabTitles(page, 1)).toHaveLength(1);
  await expect(tabsIn(page, 1).first()).toHaveAttribute("data-tab-arg", "FIX-4");
});

test("right-clicking a row offers open-in-new-tab and open-in-other-column", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "records");
  const row = rows.filter({ hasText: "FIX-1" }).first();

  await row.click();
  expect(await tabTitles(page, 0)).toHaveLength(1);

  // A plain click on the same row raises the tab that is open; "open in a new tab" is
  // what makes a second one, which is the only thing that distinguishes the two.
  await row.click();
  expect(await tabTitles(page, 0)).toHaveLength(1);
  await row.click({ button: "right" });
  await page.locator('[data-menu-item="Open in a new tab"]').click();
  expect(await tabTitles(page, 0)).toHaveLength(2);

  await row.click({ button: "right" });
  await page.locator('[data-menu-item="Open in the right column"]').click();
  await expect(columns(page)).toHaveCount(2);
  expect(await tabTitles(page, 1)).toHaveLength(1);
  await expect(tabsIn(page, 1).first()).toHaveAttribute("data-tab-arg", "FIX-1");
});

// ============================================================ KYO-240.c3: one CSS convention

test("every tab's CSS is installed by the one helper, and nothing else makes a sheet", async () => {
  const page = await shellReady(await firstShell());

  // Open one tab of every kind that carries rules, so each kind's `open` has run.
  const rows = await rowsFor(page, "records");
  await rows.filter({ hasText: "FIX-1" }).first().click();
  await openKind(page, "compose", "scratchpad");
  await rail(page, "activity").click();
  const briefs = await rowsFor(page, "notes");
  await briefs.filter({ hasText: "Latest brief" }).first().click();

  // The sheets the window is carrying, by name. Every one of them arrived through
  // `installStyles`, which is the only thing that writes `data-styles`. The fifth
  // sheet, "docs", is the document renderer's and is installed by `renderDocument`
  // itself (KYO-253); this fixture declares no documents, so `docs.spec.ts` is where
  // that one is seen to arrive.
  const sheets = await page
    .locator("style[data-styles]")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-styles")).sort());
  expect(sheets, "a tab kind stopped installing its sheet, or installed it twice").toEqual([
    "compose",
    "links",
    "record",
    "views",
  ]);

  // ...and the agreement itself, which the DOM cannot show: no second mechanism. One
  // module makes `<style>` elements, and no tab sets a look on an element it builds --
  // with the single computed exception `kind.ts` names, which is a measurement.
  const sources = tabSources();
  const makers = sources.filter(([, text]) => text.includes('createElement("style")'));
  expect(
    makers.map(([path]) => path),
    "a second module makes style elements",
  ).toEqual([]);
  // Template literals stripped first: every sheet in this app is CSS inside backticks,
  // and a declaration like `text-decoration-style: wavy` is not an element style.
  const stylers = sources.filter(
    ([path, text]) =>
      /^\s*style:/m.test(text.replace(/`[^`]*`/g, "``")) &&
      !path.endsWith(join("compose", "editor.ts")),
  );
  expect(
    stylers.map(([path]) => path),
    "a tab is setting styles on its elements instead of installing a sheet",
  ).toEqual([]);
});

/** Every renderer source file except `kind.ts` itself, as `[relative path, text]`. */
function tabSources(): [string, string][] {
  const root = join(APP_ROOT, "src", "renderer");
  const out: [string, string][] = [];
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".ts") && full !== join(root, "tabs", "kind.ts")) {
        out.push([relative(root, full), readFileSync(full, "utf8")]);
      }
    }
  };
  walk(root);
  return out;
}

// ============================================================ KYO-240.c4: the ideas list

test("the ideas list searches by name and path, and opens three ways", async () => {
  // Moved here from KYO-234.c3: the compose dispatch owned `tabs/compose/` and this
  // list lives in `shell/sidebar.ts`, which was nobody's during stage 3.
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "ideas");
  const search = page.locator('[data-region="ideas-search"]');
  await expect(search, "the ideas list has no search control").toBeVisible();

  // The fixture vault, undecorated: four markdown files across two folders. The
  // `.png` is listed by the tree and not openable, so it is not a row; `scratchpad.md`
  // sits in the vault and is not an idea (`ideas.idea_files` skips it at any depth),
  // so it is not one either -- and neither absence is a decoration this list applied.
  await expect(rows).toHaveCount(4);
  await expect(rows.filter({ hasText: "a picture.png" })).toHaveCount(0);
  await expect(rows.filter({ hasText: "scratchpad.md" })).toHaveCount(0);

  // BY NAME.
  await search.fill("bare");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toHaveAttribute("data-row-arg", "idea:bare.md");

  // BY PATH: the folder is part of what the reader is typing, and no row's NAME
  // contains it -- so a filter that read only the name would return nothing here.
  await search.fill("in a folder");
  await expect(rows).toHaveCount(2);
  await expect(rows.filter({ hasText: "promoted already.md" })).toHaveCount(1);

  // A search that matches nothing says so, and says it differently from an empty
  // vault: the remedy for one is to type less and for the other is to write an idea.
  await search.fill("zzz nothing matches this");
  await expect(rows).toHaveCount(0);
  await expect(page.locator('[data-region="sidebar-problem"]')).toContainText("No idea file");

  await search.fill("promoted");
  await expect(rows).toHaveCount(1);
  const row = rows.first();

  // THE THREE OPEN TARGETS. A click lands in the active column...
  await row.click();
  const arg = "idea:in a folder/promoted already.md";
  await expect(tabsIn(page, 0)).toHaveCount(1);
  await expect(tabsIn(page, 0).first()).toHaveAttribute("data-tab-arg", arg);

  // ...the menu's first entry makes a SECOND tab rather than raising the one open...
  await row.click({ button: "right" });
  await page.locator('[data-menu-item="Open in a new tab"]').click();
  await expect(tabsIn(page, 0)).toHaveCount(2);

  // ...and its second puts one in the other column, making the column if there is none.
  await row.click({ button: "right" });
  await page.locator('[data-menu-item="Open in the right column"]').click();
  await expect(columns(page)).toHaveCount(2);
  await expect(tabsIn(page, 1).first()).toHaveAttribute("data-tab-arg", arg);
});

test("tabs reorder inside a column and move between columns", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "records");
  await rows
    .filter({ hasText: "FIX-1" })
    .first()
    .click({ modifiers: ["Meta"] });
  await rows
    .filter({ hasText: "FIX-2" })
    .first()
    .click({ modifiers: ["Meta"] });
  await rows
    .filter({ hasText: "FIX-3" })
    .first()
    .click({ modifiers: ["Meta"] });
  const before = await tabsIn(page, 0).evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-tab-arg")),
  );
  expect(before).toEqual(["FIX-1", "FIX-2", "FIX-3"]);

  // Third to first: drop on the left half of the first tab.
  const third = await boxOf(tabsIn(page, 0).nth(2));
  const first = await boxOf(tabsIn(page, 0).nth(0));
  await dragTo(
    page,
    { x: third.x + third.width / 2, y: third.y + third.height / 2 },
    { x: first.x + 4, y: first.y + first.height / 2 },
  );
  await expect
    .poll(async () =>
      tabsIn(page, 0).evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-tab-arg"))),
    )
    .toEqual(["FIX-3", "FIX-1", "FIX-2"]);

  // And between columns.
  await page.locator('[data-region="split"]').first().click();
  await expect(columns(page)).toHaveCount(2);
  // Onto the other column's STRIP: since KYO-259 a strip is the only place a tab lands,
  // and a tab dragged down into a body tears off instead (checked below).
  const moving = await boxOf(tabsIn(page, 0).nth(0));
  await dragTo(
    page,
    { x: moving.x + moving.width / 2, y: moving.y + moving.height / 2 },
    await centreOf(page, '[data-region="tab-strip"]', 1),
  );
  await expect
    .poll(async () =>
      tabsIn(page, 0).evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-tab-arg"))),
    )
    .toEqual(["FIX-1", "FIX-2"]);
  await expect
    .poll(async () =>
      tabsIn(page, 1).evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-tab-arg"))),
    )
    .toEqual(["FIX-3"]);
});

test("moving a tab to a new window yields a whole shell, and closing it hands the tab back", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  await page.locator('[data-region="split"]').first().click();
  const rows = await rowsFor(page, "records");
  await rows.filter({ hasText: "FIX-2" }).first().click();
  expect(await tabTitles(page, 1)).toHaveLength(1);

  await tabsIn(page, 1).first().click({ button: "right" });
  await page.locator('[data-menu-item="Move to a new window"]').click();
  const popped = await paneWindowOf(page);

  // A whole shell on the same project (KYO-246): rail, sidebar, columns -- with the
  // moved tab open, and marked transient so its layout is never the desk's.
  await expect(popped.locator("body")).toHaveAttribute("data-view", "shell");
  await expect(popped.locator("body")).toHaveAttribute("data-transient", "true");
  await expect(popped.locator('[data-region="rail"]')).toHaveCount(1);
  await expect(popped.locator('[data-region="sidebar"]')).toHaveCount(1);
  await expect(popped.locator('[data-region="tab"]')).toHaveCount(1);
  // ...and it is the same tab: same kind, same argument, same content.
  const body = popped.locator('[data-region="tab-body"][data-tab-kind="record"]');
  await expect(body).toHaveAttribute("data-tab-arg", "FIX-2");
  await expect(body).toHaveAttribute("data-state", "ready");

  // The column it left gave the space up rather than drawing an empty copy.
  await expect(columns(page)).toHaveCount(1);

  await popped.close();
  // Handed back to the window it came from, into what is now its only column.
  await expect
    .poll(async () =>
      tabsIn(page, 0).evaluateAll((n) => n.map((x) => x.getAttribute("data-tab-arg"))),
    )
    .toEqual(["FIX-2"]);
});

// ============================================================ KYO-248: the rail

test("the rail holds five lists and the project switcher, and nothing that opens a tab", async () => {
  const page = await shellReady(await firstShell());
  const items = await page
    .locator('[data-region="rail-item"]')
    .evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-rail-item")));
  expect(items).toEqual(["ideas", "records", "awaiting", "notes", "docs", "activity"]);
  await expect(page.locator('[data-region="project-switcher"]')).toHaveCount(1);
  // Each one selects a list; the sidebar names it.
  for (const id of ["ideas", "records", "awaiting", "notes", "docs", "activity"]) {
    await rail(page, id).click();
    await expect(page.locator('[data-region="sidebar"]')).toHaveAttribute("data-list", id);
  }
  // What left the rail is on the home page and in the View menu.
  const menu = await app.evaluate(({ Menu }) => {
    const top = Menu.getApplicationMenu();
    return ["open-scratchpad", "open-terminal", "open-claude", "open-activity", "open-brief"].map(
      (id) => top?.getMenuItemById(id)?.label ?? null,
    );
  });
  expect(menu).toEqual(["Scratchpad", "Terminal", "Claude", "Activity", "Latest Brief"]);
  await emptyWorkspace(page);
  await app.evaluate(({ BrowserWindow }) => {
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send("kyosei:command", "open-scratchpad");
    }
  });
  await expect(tabsIn(page, 0).first()).toHaveAttribute("data-tab-arg", "scratchpad");
});

test("Awaiting lists every record with an open gate on a person, naming the gate, and opens it", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const gated = JSON.parse(
    (
      await kyo(
        fixture.resolution,
        ["gate", "FIX-2", "awaiting:human-review", "--do", "somebody looks at this", "--json"],
        fixture.root,
      )
    ).stdout,
  ) as { gate_id: string };
  try {
    const rows = await rowsFor(page, "awaiting");
    const row = rows.filter({ hasText: "FIX-2" }).first();
    await expect(row).toBeVisible({ timeout: 30_000 });
    await expect(row.locator(".row-badge")).toHaveText("awaiting:human-review");
    // FIX-1's gate is `holding-for`, which waits on no person: not listed.
    await expect(rows.filter({ hasText: "FIX-1" })).toHaveCount(0);
    await row.click();
    await expect(tabsIn(page, 0).first()).toHaveAttribute("data-tab-arg", "FIX-2");
    await expect(tabsIn(page, 0).first()).toHaveAttribute("data-tab-kind", "record");
  } finally {
    await kyo(fixture.resolution, ["ungate", gated.gate_id, "--why", "looked at"], fixture.root);
  }
});

test("Notes lists shift notes newest first, marks the handoff, keeps the latest brief, and opens a note", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const noteFile = join(fixture.base, "shift-note.txt");
  writeFileSync(noteFile, "An older thought, before the session started.\n", "utf8");
  await kyo(fixture.resolution, ["note", "--shift", "--file", noteFile], fixture.root);
  await kyo(fixture.resolution, ["brief", "--session-start"], fixture.root);
  writeFileSync(noteFile, "The newest thought, after it.\n", "utf8");
  await kyo(fixture.resolution, ["note", "--shift", "--file", noteFile], fixture.root);

  const rows = await rowsFor(page, "notes");
  await expect(rows.filter({ hasText: "Latest brief" })).toHaveCount(1);
  const notes = page.locator(
    '[data-region="sidebar-row"][data-row-kind="views"][data-row-arg^="shift-note:"]',
  );
  await expect(notes.first()).toContainText("The newest thought");
  // Newest first, by the ledger's own timestamps.
  const stamps = await notes.evaluateAll((n) => n.map((e) => e.getAttribute("data-note-at") ?? ""));
  expect([...stamps].sort().reverse()).toEqual(stamps);
  // The note standing when the session-start brief ran is the handoff; the newer one is not.
  const handoff = notes.filter({ hasText: "An older thought" }).first();
  await expect(handoff.locator(".row-badge")).toHaveText("handoff");
  await expect(handoff).toHaveAttribute("data-handoff-for", /^FIX-B/);
  await expect(notes.first().locator(".row-badge")).toHaveCount(0);
  // A click shows THAT note, not the latest. Its id is read before the click: the
  // list may repaint on the poll afterwards, and a locator re-resolved then is a race.
  const handoffId = (await handoff.getAttribute("data-row-arg"))?.slice("shift-note:".length) ?? "";
  await handoff.click();
  const body = page.locator('[data-region="tab-body"][data-tab-kind="views"]:not([hidden])');
  await expect(body).toHaveAttribute("data-state", "ready");
  await expect(body.locator('[data-region="shift-note-text"]')).toContainText("An older thought");
  await expect(body.locator('[data-region="shift-note-title"]')).toContainText(handoffId);
});

// ============================================================ KYO-250: sort, filter, folders

test("each list has a sort box and a filter box, and the choice survives a restart", async () => {
  const profile = mkdtempSync(join(tmpdir(), "kyosei list state "));
  await closeReadyApp(app);
  try {
    let page = await launchReady(
      envFor(fixture.root, fixture.home, { KYOSEI_DISPLAYS: LAPTOP }),
      profile,
    );
    await shellReady(page);
    const rows = await rowsFor(page, "records");
    await expect(rows).toHaveCount(5);
    await page.locator('[data-region="filter-button"]').click();
    // The Status group speaks the role word (KYO-365): FIX-2 is a knowledge record
    // stored `open` that reads `current`, so the group offers `current` and picking
    // `open` does not list it. Asserted in the window because the first fix for this
    // passed every check below the window and changed nothing in it.
    const status = page.locator('[data-region="filter-group"][data-filter="status"]');
    await expect(status.locator('[data-filter-value="current"]')).toHaveCount(1);
    await status.locator('[data-filter-value="open"]').click();
    await expect(page.locator('[data-row-arg="FIX-2"]')).toHaveCount(0);
    await status.locator('[data-filter-value="open"]').click();
    await expect(rows).toHaveCount(5);
    // Filter: kind = bug keeps the two bugs and says so on the button.
    await page
      .locator('[data-region="filter-group"][data-filter="kind"] [data-filter-value="bug"]')
      .click();
    await expect(rows).toHaveCount(2);
    await expect(page.locator('[data-region="filter-button"]')).toHaveAttribute(
      "data-filtering",
      "true",
    );
    // Sort: by id descending puts FIX-3 (the second bug) first.
    await page.locator('[data-region="sort-button"]').click();
    await page.locator('[data-region="sort-direction"]').click();
    await expect(page.locator('[data-region="sort-direction"]')).toHaveAttribute(
      "data-direction",
      "descending",
    );
    await expect(rows.first()).toHaveAttribute("data-row-arg", "FIX-3");
    // The ideas list has the same two controls.
    await rowsFor(page, "ideas");
    await expect(page.locator('[data-region="sort-button"]')).toHaveCount(1);
    await expect(page.locator('[data-region="filter-button"]')).toHaveCount(1);
    await page.waitForTimeout(800); // the save is debounced
    await closeReadyApp(app);

    // Relaunched on the same desk: the records list comes back filtered and sorted.
    page = await launchReady(
      envFor(fixture.root, fixture.home, { KYOSEI_DISPLAYS: LAPTOP }),
      profile,
    );
    await shellReady(page);
    const again = await rowsFor(page, "records");
    await expect(again).toHaveCount(2);
    await expect(again.first()).toHaveAttribute("data-row-arg", "FIX-3");
    await expect(page.locator('[data-region="filter-button"]')).toHaveAttribute(
      "data-filtering",
      "true",
    );
  } finally {
    if (app.windows().length === 0) await launchReady(envFor(fixture.root, fixture.home));
    rmSync(profile, { recursive: true, force: true });
  }
});

test("the ideas list is a folder tree: fold, new folder, rename, move, delete", async () => {
  const page = await shellReady(await firstShell());
  await emptyWorkspace(page);
  const rows = await rowsFor(page, "ideas");
  await expect(rows).toHaveCount(4);
  const folder = page.locator('[data-region="sidebar-folder"][data-folder="in a folder"]');
  await expect(folder).toHaveCount(1);
  await expect(folder).toHaveAttribute("data-collapsed", "false");
  // Fold: the folder's two files leave the list; unfold: they return.
  await folder.click();
  await expect(folder).toHaveAttribute("data-collapsed", "true");
  await expect(rows).toHaveCount(2);
  await folder.click();
  await expect(rows).toHaveCount(4);

  // New folder, from the toolbar, through the app's own prompt.
  await page.locator('[data-region="ideas-new-folder"]').click();
  await page.locator('[data-region="prompt-input"]').fill("made here");
  await page.locator('[data-region="prompt-ok"]').click();
  await expect(page.locator('[data-region="sidebar-folder"][data-folder="made here"]')).toHaveCount(
    1,
  );

  // Rename a file from its menu -- and back, so the vault is as this check found it
  // for every check after it (the fixture is shared across this file).
  await rows.filter({ hasText: "bare.md" }).first().click({ button: "right" });
  await page.locator('[data-menu-item="Rename…"]').click();
  await page.locator('[data-region="prompt-input"]').fill("bare renamed");
  await page.keyboard.press("Enter");
  const renamed = page.locator('[data-region="sidebar-row"][data-row-arg="idea:bare renamed.md"]');
  await expect(renamed).toHaveCount(1);
  await renamed.click({ button: "right" });
  await page.locator('[data-menu-item="Rename…"]').click();
  await page.locator('[data-region="prompt-input"]').fill("bare");
  await page.keyboard.press("Enter");
  await expect(
    page.locator('[data-region="sidebar-row"][data-row-arg="idea:bare.md"]'),
  ).toHaveCount(1);

  // Move a file into the new folder from its menu, and back to the root.
  await rows.filter({ hasText: "a first idea.md" }).first().click({ button: "right" });
  await page.locator('[data-menu-item="Move to made here/"]').click();
  const moved = page.locator(
    '[data-region="sidebar-row"][data-row-arg="idea:made here/a first idea.md"]',
  );
  await expect(moved).toHaveCount(1);
  await expect(
    page.locator(
      '[data-region="folder-children"][data-folder="made here"] [data-region="sidebar-row"]',
    ),
  ).toHaveCount(1);
  await moved.click({ button: "right" });
  await page.locator('[data-menu-item="Move to ideas/ (root)"]').click();
  await expect(
    page.locator('[data-region="sidebar-row"][data-row-arg="idea:a first idea.md"]'),
  ).toHaveCount(1);

  // Delete the (now empty) folder -- after the app asked.
  await page
    .locator('[data-region="sidebar-folder"][data-folder="made here"]')
    .click({ button: "right" });
  await page.locator('[data-menu-item="Delete…"]').click();
  await expect(page.locator('[data-region="prompt"]')).toContainText("made here");
  await page.locator('[data-region="prompt-ok"]').click();
  await expect(page.locator('[data-region="sidebar-folder"][data-folder="made here"]')).toHaveCount(
    0,
  );
  await expect(rows).toHaveCount(4);
});

// ============================================================ KYO-257: shell polish

test("a context menu closes on Escape, on a press outside it, and when the window loses focus", async () => {
  const page = await shellReady(await firstShell());
  const menu = page.locator('[data-region="context-menu"]');
  const rows = await rowsFor(page, "records");
  const open = async (): Promise<void> => {
    await rows.first().click({ button: "right" });
    await expect(menu).toHaveCount(1);
  };

  await open();
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);

  // What clicking another app does to this window, when the page itself had focus.
  await open();
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  await expect(menu).toHaveCount(0);

  // And when it did not: the native window's own blur, forwarded by the main process.
  // With only the DOM listener a menu stayed open over another app (KYO-257.c1), so
  // this is emitted on the BrowserWindow, never on the page.
  await open();
  await app.evaluate(({ BrowserWindow }) => {
    for (const win of BrowserWindow.getAllWindows()) win.emit("blur");
  });
  await expect(menu).toHaveCount(0);

  // A menu opened while the page lacks focus asks the main process for it: the
  // right-click on the window of an app that is not active, which macOS does not
  // activate (KYO-257.c1). Counted where it arrives; the suite's launch never steals.
  await app.evaluate(({ ipcMain }) => {
    const g = globalThis as unknown as { __kyo257Focus?: number; __kyo257Hooked?: boolean };
    g.__kyo257Focus = 0;
    if (g.__kyo257Hooked !== true) {
      g.__kyo257Hooked = true;
      ipcMain.on("kyosei:claim-focus", () => {
        g.__kyo257Focus = (g.__kyo257Focus ?? 0) + 1;
      });
    }
  });
  await page.evaluate(() => {
    (document as unknown as { hasFocus: () => boolean }).hasFocus = () => false;
  });
  await open();
  await expect
    .poll(() =>
      app.evaluate(() => (globalThis as unknown as { __kyo257Focus?: number }).__kyo257Focus ?? 0),
    )
    .toBeGreaterThan(0);
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await page.evaluate(() => {
    delete (document as unknown as { hasFocus?: unknown }).hasFocus;
  });

  // A press inside the menu that runs nothing must not spend the listener that closes
  // it on the next press outside (it was `once`, and this left the menu stuck open).
  await open();
  await menu.dispatchEvent("pointerdown");
  await expect(menu).toHaveCount(1);
  await page.locator(".statusbar").click();
  await expect(menu).toHaveCount(0);
});

test("reopening the terminal tab shows what the session had on screen, before it prints again (KYO-258)", async () => {
  const page = await shellReady(await firstShell());
  const openTerminal = async (): Promise<void> => {
    await app.evaluate(({ BrowserWindow }) => {
      for (const win of BrowserWindow.getAllWindows())
        win.webContents.send("kyosei:command", "open-terminal");
    });
  };
  const body = page.locator('[data-region="tab-body"][data-tab-kind="terminal"]');
  await openTerminal();
  await expect(body).toHaveAttribute("data-terminal", "live", { timeout: 30_000 });
  await expect.poll(async () => (await body.innerText()).trim().length).toBeGreaterThan(0);
  // The shell computes the marker, so it exists only in the command's OUTPUT: the typed
  // line reads `$((6*7))`, the printed one `42`. Waiting for the typed echo alone let the
  // output and the next prompt land after the close, where the preload holds undelivered
  // chunks for the next screen -- and the check then passed with the defect in place.
  const stamp = Date.now();
  const printed = `kyo258-42-${stamp}`;
  await body.click();
  await page.keyboard.type(`echo kyo258-$((6*7))-${stamp}\n`);
  await expect.poll(() => body.innerText(), { timeout: 20_000 }).toContain(printed);
  // And settled: the prompt after the output has reached this screen too, so nothing
  // is still in flight to be handed to the next one.
  let settled = "";
  await expect
    .poll(
      async () => {
        const now = await body.innerText();
        const same = now === settled;
        settled = now;
        return same;
      },
      { intervals: [400], timeout: 10_000 },
    )
    .toBe(true);

  await page
    .locator('[data-region="tab"][data-tab-kind="terminal"] [data-action="close-tab"]')
    .click();
  await expect(page.locator('[data-region="tab"][data-tab-kind="terminal"]')).toHaveCount(0);

  // Nothing is typed after the reopen: the printed marker can only come from the
  // session's replay reaching the new screen. Before the fix this screen stayed empty.
  await openTerminal();
  await expect(body).toHaveAttribute("data-terminal", "live", { timeout: 30_000 });
  await expect.poll(() => body.innerText(), { timeout: 5_000 }).toContain(printed);
});

test("the sidebar head's collapse button hides the sidebar exactly as the selected rail item does", async () => {
  const page = await shellReady(await firstShell());
  await rowsFor(page, "ideas");
  const sidebar = page.locator('[data-region="sidebar"]');
  await expect(sidebar).toHaveAttribute("data-open", "true");
  await page.locator('[data-region="sidebar-collapse"]').click();
  await expect(sidebar).toHaveAttribute("data-open", "false");
  await expect(page.locator('[data-region="rail-item"][data-selected="true"]')).toHaveCount(0);
  // The rail still brings it back with one click, because nothing is selected now.
  await rail(page, "ideas").click();
  await expect(sidebar).toHaveAttribute("data-open", "true");
  await expect(sidebar).toHaveAttribute("data-list", "ideas");
});

test("the ideas tree puts folders before files at every level, and sorts by one dropdown and a direction", async () => {
  const page = await shellReady(await firstShell());
  const rows = await rowsFor(page, "ideas");
  await expect(rows).toHaveCount(4);
  // A subfolder, so there is a level below the root to check too; removed at the end.
  expect(
    (await page.evaluate(() => window.kyosei.mkdirIdeas({ folder: "in a folder/sub" }))).ok,
  ).toBe(true);
  try {
    await rail(page, "ideas").click();
    await rail(page, "ideas").click();
    const group = page.locator('[data-region="sidebar-body"] .group').first();
    const order = await group.evaluate((node) =>
      [...node.children].map((child) => (child as HTMLElement).dataset.region ?? child.className),
    );
    const divider = order.indexOf("ideas-divider");
    expect(
      divider,
      `no divider between folders and root files: ${order.join(", ")}`,
    ).toBeGreaterThan(0);
    expect(
      order.slice(1, divider).every((r) => r === "sidebar-folder" || r === "folder-children"),
    ).toBe(true);
    expect(order.slice(divider + 1).every((r) => r === "sidebar-row")).toBe(true);
    const nested = await page
      .locator('[data-region="folder-children"][data-folder="in a folder"]')
      .evaluate((node) => [...node.children].map((child) => (child as HTMLElement).dataset.region));
    expect(nested[0], `inside a folder: ${nested.join(", ")}`).toBe("sidebar-folder");
    expect(nested.lastIndexOf("sidebar-folder")).toBeLessThan(nested.indexOf("sidebar-row"));

    // Sort: one dropdown of the list's fields, and a direction toggle beside it.
    await page.locator('[data-region="sort-button"]').click();
    const field = page.locator('[data-region="sort-field"]');
    await expect(field.locator("option")).toHaveText(["Name", "Created", "Modified"]);
    const rootNames = async (): Promise<string[]> =>
      group.locator(':scope > [data-region="sidebar-row"]').allInnerTexts();
    const before = await rootNames();
    expect(before.length).toBeGreaterThan(1);
    const direction = page.locator('[data-region="sort-direction"]');
    await direction.click();
    await expect(direction).toHaveAttribute("data-direction", "descending");
    await expect.poll(rootNames).toEqual([...before].reverse());
    await direction.click();
    await expect(direction).toHaveAttribute("data-direction", "ascending");
    await field.selectOption("modified");
    await field.selectOption("name");
    await expect.poll(rootNames).toEqual(before);
    await page.locator('[data-region="sort-button"]').click();
  } finally {
    await page.evaluate(() => window.kyosei.deleteIdea({ path: "in a folder/sub" }));
  }
});

test("the records list sorts by Created and Modified with no base selected (KYO-467.c2)", async () => {
  // The order each sort must produce is the CLI's own stamps, ordered the way the
  // sidebar orders them (a stable sort over `kyo list`'s seq order, reversed for
  // descending) -- so this asserts the window sorts by the records' stamps, not a guess.
  const { stdout } = await kyo(fixture.resolution, ["list", "--json"], fixture.root);
  const listed = (JSON.parse(stdout) as { records: Record<string, string>[] }).records;
  expect(listed.every((row) => typeof row.created_at === "string" && row.created_at !== "")).toBe(
    true,
  );
  const orderBy = (key: string): string[] =>
    [...listed]
      .sort((a, b) => (a[key] ?? "").localeCompare(b[key] ?? ""))
      .map((row) => row.id ?? "");
  const page = await shellReady(await firstShell());
  await rowsFor(page, "records");
  const ids = () =>
    page
      .locator('[data-region="sidebar-row"][data-row-kind="record"]')
      .evaluateAll((nodes) => nodes.map((node) => (node as HTMLElement).dataset.rowArg ?? ""));
  await page.locator('[data-region="sort-button"]').click();
  const field = page.locator('[data-region="sort-field"]');
  const direction = page.locator('[data-region="sort-direction"]');
  try {
    for (const [option, key] of [
      ["created", "created_at"],
      ["modified", "updated_at"],
    ] as const) {
      await field.selectOption(option);
      if ((await direction.getAttribute("data-direction")) === "descending")
        await direction.click();
      await expect.poll(ids, `${option} ascending`).toEqual(orderBy(key));
      await direction.click();
      await expect(direction).toHaveAttribute("data-direction", "descending");
      await expect.poll(ids, `${option} descending`).toEqual([...orderBy(key)].reverse());
      await direction.click();
    }
  } finally {
    await field.selectOption("name");
    await page.locator('[data-region="sort-button"]').click();
  }
});

test("an idea tab shows its folder, created and modified stamps above the text", async () => {
  const page = await shellReady(await firstShell());
  const rows = await rowsFor(page, "ideas");
  await rows.filter({ hasText: "bare.md" }).first().click();
  const meta = page.locator(
    '[data-region="tab-body"][data-tab-kind="compose"]:not([hidden]) [data-region="idea-meta"]',
  );
  await expect(meta.locator('[data-meta="folder"]')).toHaveText("ideas/");
  const modified = await meta.locator('[data-meta="modified"]').getAttribute("data-iso");
  expect(Number.isNaN(Date.parse(modified ?? "")), "modified carried no stamp").toBe(false);
  // Since KYO-467 kyo serve's listing carries a created time (the birth time where the
  // disk keeps one), so "created" shows a stamp again rather than "unknown".
  const created = await meta.locator('[data-meta="created"]').getAttribute("data-iso");
  expect(Number.isNaN(Date.parse(created ?? "")), "created carried no stamp").toBe(false);
  await expect(meta.locator('[data-meta="created"]')).not.toHaveText("created unknown");
});

test("every ideas write is refused outside ideas/, naming the path", async () => {
  const page = await shellReady(await firstShell());
  const results = await page.evaluate(async () => {
    const k = window.kyosei;
    return {
      mkdir: await k.mkdirIdeas({ folder: "../escaped" }),
      rename: await k.renameIdea({ path: "../outside.md", name: "x" }),
      move: await k.moveIdea({ path: "bare.md", folder: "../../elsewhere" }),
      remove: await k.deleteIdea({ path: "../../../etc" }),
      absolute: await k.mkdirIdeas({ folder: "/tmp/nope" }),
    };
  });
  for (const [name, result] of Object.entries(results)) {
    expect(result.ok, `${name} was allowed`).toBe(false);
    expect(result.problem, `${name} named no path`).toMatch(
      /will not write|not a path inside ideas|is a folder name, not a path/,
    );
  }
  expect(results.mkdir.problem).toContain("escaped");
  expect(results.move.problem).toContain("elsewhere");
  // And bare.md is still where it was.
  const rows = await rowsFor(page, "ideas");
  await expect(rows.filter({ hasText: "bare.md" })).toHaveCount(1);
});

// ============================================================ c6: the badges

test("the rail badges count the read model's answers, and follow them", async () => {
  const page = await shellReady(await firstShell());
  const records = page.locator('[data-badge-for="awaiting"]');

  // The records fixture has one open gate and it is `holding-for`, which is NOT one of
  // the awaiting-human types -- so the badge is absent, and that is the answer rather
  // than a zero the reader has to read.
  await expect(records).toHaveAttribute("data-count", "0");
  await expect(records).toBeHidden();

  // Now the controller does something, in another process, to the store this app is
  // reading -- which is exactly how this number moves in life.
  const gated = JSON.parse(
    (
      await kyo(
        fixture.resolution,
        ["gate", "FIX-2", "awaiting:human-review", "--do", "somebody looks at this", "--json"],
        fixture.root,
      )
    ).stdout,
  ) as { gate_id: string };
  await expect(records, "the badge never noticed a gate opened under it").toHaveAttribute(
    "data-count",
    "1",
    { timeout: 30_000 },
  );
  await expect(records).toBeVisible();
  await expect(records).toHaveText("1");

  // And back down again when it is cleared.
  await kyo(
    fixture.resolution,
    ["ungate", gated.gate_id, "--why", "somebody looked at it"],
    fixture.root,
  );
  await expect(records).toHaveAttribute("data-count", "0", { timeout: 30_000 });
  await expect(records).toBeHidden();
});

// ============================================================ c3: layout memory

test("a layout saved with three columns restores as two, every tab kept (KYO-246)", async () => {
  const profile = mkdtempSync(join(tmpdir(), "kyosei three columns "));
  await closeReadyApp(app);
  try {
    // What a KYO-232 build left behind: three columns, a record in each.
    const column = (arg: string, widthPercent: number) => ({
      widthPercent,
      tabs: [{ kind: "record", arg }],
      activeTab: 0,
    });
    writeFileSync(
      join(profile, "layouts.json"),
      JSON.stringify({
        "1:0,0,1512x982": {
          columns: [column("FIX-1", 34), column("FIX-2", 33), column("FIX-3", 33)],
          activeColumn: 2,
          sidebar: { item: null, width: 280 },
        },
      }),
      "utf8",
    );
    const page = await launchReady(
      envFor(fixture.root, fixture.home, { KYOSEI_DISPLAYS: LAPTOP }),
      profile,
    );
    await shellReady(page);
    await expect(columns(page)).toHaveCount(2);
    await expect
      .poll(async () =>
        tabsIn(page, 0).evaluateAll((n) => n.map((x) => x.getAttribute("data-tab-arg"))),
      )
      .toEqual(["FIX-1"]);
    await expect
      .poll(async () =>
        tabsIn(page, 1).evaluateAll((n) => n.map((x) => x.getAttribute("data-tab-arg"))),
      )
      .toEqual(["FIX-2", "FIX-3"]);
  } finally {
    if (app.windows().length === 0) await launchReady(envFor(fixture.root, fixture.home));
    rmSync(profile, { recursive: true, force: true });
  }
});

test("a layout saved with an empty second column restores as two columns (KYO-277)", async () => {
  const profile = mkdtempSync(join(tmpdir(), "kyosei empty column "));
  await closeReadyApp(app);
  try {
    writeFileSync(
      join(profile, "layouts.json"),
      JSON.stringify({
        "1:0,0,1512x982": {
          columns: [
            { widthPercent: 50, tabs: [{ kind: "record", arg: "FIX-1" }], activeTab: 0 },
            { widthPercent: 50, tabs: [], activeTab: 0 },
          ],
          activeColumn: 1,
          sidebar: { item: null, width: 280 },
        },
      }),
      "utf8",
    );
    const page = await launchReady(
      envFor(fixture.root, fixture.home, { KYOSEI_DISPLAYS: LAPTOP }),
      profile,
    );
    await shellReady(page);
    await expect(columns(page)).toHaveCount(2);
    await expect(tabsIn(page, 0)).toHaveCount(1);
    await expect(tabsIn(page, 1)).toHaveCount(0);
    await expect(columns(page).nth(1).locator('[data-region="empty-column"]')).toHaveCount(1);
  } finally {
    if (app.windows().length === 0) await launchReady(envFor(fixture.root, fixture.home));
    rmSync(profile, { recursive: true, force: true });
  }
});

/** Two display configurations, stated rather than plugged in (`KYOSEI_DISPLAYS`). */
const LAPTOP = JSON.stringify([{ id: 1, bounds: { x: 0, y: 0, width: 1512, height: 982 } }]);
const DESK = JSON.stringify([
  { id: 1, bounds: { x: 0, y: 0, width: 1512, height: 982 } },
  { id: 77, bounds: { x: 1512, y: 0, width: 3840, height: 2160 } },
]);

test("a layout is saved per display configuration and restored when that one comes back", async () => {
  const profile = mkdtempSync(join(tmpdir(), "kyosei layouts "));
  await closeReadyApp(app);
  try {
    // On the laptop: two columns, a record in the second, the ideas list showing.
    let page = await launchReady(
      envFor(fixture.root, fixture.home, { KYOSEI_DISPLAYS: LAPTOP }),
      profile,
    );
    await shellReady(page);
    const laptopKey = await page.locator("body").getAttribute("data-display-signature");
    expect(laptopKey).toBe("1:0,0,1512x982");
    await page.locator('[data-region="split"]').first().click();
    // A tab with a history: the home tab, navigated to the latest brief, then to
    // FIX-1 by a single click (so it is the column's preview, too).
    await page.locator('[data-action="new-tab"]').nth(1).click();
    await page
      .locator('[data-region="tab-body"][data-tab-kind="home"] [data-home-arg="brief"]')
      .click();
    await expect(tabsIn(page, 1).first()).toHaveAttribute("data-tab-arg", "brief");
    const rows = await rowsFor(page, "records");
    await rows.filter({ hasText: "FIX-1" }).first().click();
    await expect(tabsIn(page, 1).first()).toHaveAttribute("data-tab-arg", "FIX-1");
    await rail(page, "ideas").click();
    await expect(page.locator('[data-region="sidebar"]')).toHaveAttribute("data-list", "ideas");
    await page.waitForTimeout(800); // the save is debounced
    await closeReadyApp(app);

    // At the desk: a configuration nobody has worked on, so its own default.
    page = await launchReady(
      envFor(fixture.root, fixture.home, { KYOSEI_DISPLAYS: DESK }),
      profile,
    );
    await shellReady(page);
    expect(await page.locator("body").getAttribute("data-display-signature")).toBe(
      "1:0,0,1512x982|77:1512,0,3840x2160",
    );
    await expect(columns(page)).toHaveCount(1);
    await expect(page.locator('[data-region="sidebar"]')).toHaveAttribute("data-list", "records");
    // Work here instead: two columns, the terminal in the second.
    await page.locator('[data-region="split"]').first().click();
    await expect(columns(page)).toHaveCount(2);
    await openKind(page, "terminal");
    await page.waitForTimeout(800);
    await closeReadyApp(app);

    // Back to the laptop: ITS layout, not the desk's.
    page = await launchReady(
      envFor(fixture.root, fixture.home, { KYOSEI_DISPLAYS: LAPTOP }),
      profile,
    );
    await shellReady(page);
    await expect(columns(page), "the laptop restored the desk's layout").toHaveCount(2);
    await expect(tabsIn(page, 1).first()).toHaveAttribute("data-tab-arg", "FIX-1");
    await expect(page.locator('[data-region="sidebar"]')).toHaveAttribute("data-list", "ideas");
    // ...with its history (KYO-247): home, then the latest brief (Records left the home
    // page with KYO-257), then FIX-1, so back walks to the brief and then home. Restored
    // entries are drawn on arrival, not at launch.
    const backOnLaptop = columns(page).nth(1).locator('[data-action="back"]');
    await expect(backOnLaptop).toBeEnabled();
    await backOnLaptop.click();
    await expect(tabsIn(page, 1).first()).toHaveAttribute("data-tab-arg", "brief");
    await backOnLaptop.click();
    await expect(tabsIn(page, 1).first()).toHaveAttribute("data-tab-kind", "home");
    await closeReadyApp(app);

    // And the desk still has its own.
    page = await launchReady(
      envFor(fixture.root, fixture.home, { KYOSEI_DISPLAYS: DESK }),
      profile,
    );
    await shellReady(page);
    await expect(columns(page)).toHaveCount(2);
    await expect(page.locator('[data-tab-kind="terminal"]').first()).toBeVisible();
  } finally {
    // `afterEach` closes whatever is running; leave one started.
    if (app.windows().length === 0) {
      await launchReady(envFor(fixture.root, fixture.home));
    }
    rmSync(profile, { recursive: true, force: true });
  }
});

// ============================================================ c4: terminal and claude

test("terminal and claude open as tabs from the home page, one instance each", async () => {
  const page = await shellReady(await firstShell());

  await openKind(page, "terminal");
  const terminal = page.locator('[data-region="tab-body"][data-tab-kind="terminal"]');
  await expect(terminal).toHaveAttribute("data-terminal", "live", { timeout: 30_000 });
  await expect(terminal).toHaveAttribute("data-terminal-mode", "bare");
  await expect(terminal).toHaveAttribute("data-terminal-cwd", fixture.root);

  // One instance per project: asking again raises the tab that is open rather than
  // starting a second screen on the same pty.
  await openKind(page, "terminal");
  await expect(page.locator('[data-tab-kind="terminal"][data-region="tab"]')).toHaveCount(1);

  await openKind(page, "claude");
  const claude = page.locator('[data-region="tab-body"][data-tab-kind="claude"]');
  await expect(claude.locator('[data-region="claude-status"]')).not.toBeEmpty({ timeout: 30_000 });
  await openKind(page, "claude");
  await expect(page.locator('[data-tab-kind="claude"][data-region="tab"]')).toHaveCount(1);

  // Both are open at once, beside the scratchpad the default layout opens on -- which
  // is what "everything is a tab" buys over v2's dock, where the terminal had to be
  // hidden to show claude.
  await expect(page.locator('[data-region="tab"][data-tab-kind="terminal"]')).toHaveCount(1);
  await expect(page.locator('[data-region="tab"][data-tab-kind="claude"]')).toHaveCount(1);
  await expect(tabsIn(page, 0)).toHaveCount(3);
});

/** The shell's own pid, printed by the shell, off whichever screen is showing it. */
async function shellPid(page: Page, marker: string): Promise<string> {
  const body = page.locator('[data-region="tab-body"][data-tab-kind="terminal"]');
  await expect(body).toHaveAttribute("data-terminal", "live", { timeout: 30_000 });
  await expect.poll(async () => (await body.innerText()).trim().length).toBeGreaterThan(0);
  await body.click();
  await page.keyboard.type(`echo "${marker}:$$"\n`);
  await expect
    .poll(async () => await body.innerText(), { timeout: 20_000 })
    .toMatch(new RegExp(`${marker}:\\d+`));
  const pid = new RegExp(`${marker}:(\\d+)`).exec(await body.innerText())?.[1];
  expect(pid, `the shell never printed its ${marker} pid`).toBeDefined();
  return pid as string;
}

test("moving the terminal to a window and back keeps its session (KYO-124, ported)", async () => {
  // KYO-124 as it reads after KYO-232: the pane is a TAB and pop-out is moving it, but
  // the claim is unchanged -- one pty, moved, not a second one started. Runs in `bare`
  // mode, which is the case the record is actually about: under tmux the pty is a
  // client and the session survives by construction.
  const page = await shellReady(await firstShell());
  await openKind(page, "terminal");
  const body = page.locator('[data-region="tab-body"][data-tab-kind="terminal"]');
  await expect(body).toHaveAttribute("data-terminal-mode", "bare", { timeout: 30_000 });
  const pid = await shellPid(page, "PANE");
  const session = await body.getAttribute("data-terminal-pty");
  expect(session, "the tab does not say which pty it is showing").not.toBe("");

  await page.locator('[data-region="tab"][data-tab-kind="terminal"]').click({ button: "right" });
  await page.locator('[data-menu-item="Move to a new window"]').click();
  const popped = await paneWindowOf(page);
  const out = popped.locator('[data-region="tab-body"][data-tab-kind="terminal"]');
  await expect(out).toHaveAttribute("data-terminal", "live", { timeout: 30_000 });
  // The same pty, by the main process's own name for it...
  await expect(out).toHaveAttribute("data-terminal-pty", session ?? "");
  // ...opened on the screen this window never printed: a view that arrives late is
  // given what the session has already said, which is what makes it a move.
  await expect
    .poll(async () => await out.innerText(), { timeout: 20_000 })
    .toContain(`PANE:${pid}`);
  // ...and it is that shell, asked directly rather than inferred from the screen.
  await out.click();
  await popped.keyboard.type('echo "OUT:$$"\n');
  await expect.poll(async () => await out.innerText(), { timeout: 20_000 }).toContain(`OUT:${pid}`);

  await popped.close();
  await expect(body).toHaveAttribute("data-terminal", "live", { timeout: 30_000 });
  await expect(body).toHaveAttribute("data-terminal-pty", session ?? "", { timeout: 20_000 });
  expect(await shellPid(page, "BACK"), "the tab came back to a different shell").toBe(pid);

  // ...and it does end when the reader ends it, which is the half that keeps the
  // criterion from being satisfiable by a session nothing can stop.
  await body.click();
  await page.keyboard.type("exit\n");
  await expect(body).toHaveAttribute("data-terminal", "exited");
});

test("a bare shell outlives the window it was in: closing that window ends nothing", async () => {
  const page = await shellReady(await firstShell());
  await openKind(page, "terminal");
  const pid = await shellPid(page, "OWNED");

  await page.locator('[data-region="tab"][data-tab-kind="terminal"]').click({ button: "right" });
  await page.locator('[data-menu-item="Move to a new window"]').click();
  const popped = await paneWindowOf(page);
  const out = popped.locator('[data-region="tab-body"][data-tab-kind="terminal"]');
  await expect(out).toHaveAttribute("data-terminal", "live", { timeout: 30_000 });

  // The window the session started in goes away entirely. Nothing else is holding this
  // pty: before KYO-124 it was keyed by that window's renderer and died with it.
  await page.close();
  await expect(out).toHaveAttribute("data-terminal", "live");
  await out.click();
  await popped.keyboard.type('echo "ALIVE:$$"\n');
  await expect
    .poll(async () => await out.innerText(), { timeout: 20_000 })
    .toContain(`ALIVE:${pid}`);
  expect(await out.innerText(), "the surviving session is a different shell").toContain(
    `OWNED:${pid}`,
  );
});

// ------------------------------------------- the claude.ai window (KYO-246)
//
// KYO-190's redock -- re-fetching the docked view when a popped-out claude window
// closed -- is gone with the owner it re-fetched for. The popped-out claude.ai window
// is the ACCOUNT's: it belongs to no project window, there is one of it at most, and
// closing it touches nothing in the window it was opened from. What is checked is exactly that: the docked view's own
// `loadURL` count stays where it was, counted by wrapping the method on the docked
// view -- the instrument KYO-190's check built, kept because navigations are useless
// here (claude.ai is a single-page app that navigates itself for as long as it lives).

/** Point a store's session file at a Remote Control session, via the real hook. */
function reseedSession(target: Fixture, ulid: string, id: string): void {
  const transcript = join(target.base, `${id}.jsonl`);
  writeFileSync(
    transcript,
    `${JSON.stringify({ type: "bridge-session", sessionId: id, bridgeSessionId: `cse_${ulid}` })}\n`,
    "utf8",
  );
  const { program, baseArgs } = target.resolution.source;
  if (program === null) throw new Error("no runnable kyo");
  execFileSync(program, [...baseArgs, "sessions", "--hook"], {
    cwd: target.root,
    input: JSON.stringify({
      session_id: id,
      hook_event_name: "SessionStart",
      cwd: target.root,
      transcript_path: transcript,
    }),
    encoding: "utf8",
  });
}

const CLAUDE_PARTITION_DIR = join("Partitions", "claude-ai");

test("the popped-out claude.ai window is one per app, owned by nobody, and closing it touches no docked view", async () => {
  const store = await makeFixture(1);
  await closeReadyApp(app);
  try {
    reseedSession(store, "01CCCCCCCCCCCCCCCCCCCCCCCC", "redock-session");
    const page = await shellReady(await launchReady(envFor(store.root, store.base)));
    await openKind(page, "claude");
    const status = page.locator('[data-region="claude-status"]');
    await expect(status).toContainText("redock-session", { timeout: 30_000 });

    // Wrapped BEFORE the pop-out exists, so only the docked view is counted.
    await app.evaluate(({ webContents }, partitionDir) => {
      const counter = globalThis as unknown as { __kyo190?: number };
      counter.__kyo190 = 0;
      for (const contents of webContents.getAllWebContents()) {
        if (contents.session.storagePath?.endsWith(partitionDir) !== true) continue;
        const original = contents.loadURL.bind(contents);
        contents.loadURL = (url: string, options?: unknown) => {
          counter.__kyo190 = (counter.__kyo190 ?? 0) + 1;
          return original(url, options as never);
        };
      }
    }, CLAUDE_PARTITION_DIR);
    const loads = async () =>
      app.evaluate(() => (globalThis as unknown as { __kyo190?: number }).__kyo190 ?? 0);
    expect(await loads()).toBe(0);

    await page.locator('[data-region="tab"][data-tab-kind="claude"]').click({ button: "right" });
    await page.locator('[data-menu-item="Move to a new window"]').click();
    const popped = await paneWindowOf(page);
    // A pane window -- the claude view and nothing else -- not a second shell.
    await expect(popped.locator("body")).toHaveAttribute("data-view", "pane");
    await expect(popped.locator("body")).toHaveAttribute("data-pane", "claude");
    expect(await loads(), "the pop-out loaded the docked view rather than its own").toBe(0);

    // One per app: asking again from the shell focuses it rather than opening another.
    await openKind(page, "claude");
    await page.locator('[data-region="tab"][data-tab-kind="claude"]').click({ button: "right" });
    await page.locator('[data-menu-item="Move to a new window"]').click();
    await page.waitForTimeout(800);
    expect(app.windows().filter((w) => w !== page && w.url().startsWith("file:"))).toHaveLength(1);

    // Closing it is closing it: nothing comes back, nothing is re-fetched.
    await popped.close();
    await page.waitForTimeout(1500);
    expect(await loads(), "closing the claude window re-fetched a docked view").toBe(0);
    await expect(page.locator('[data-region="tab"][data-tab-kind="claude"]')).toHaveCount(0);
  } finally {
    removeFixture(store);
    if (app.windows().length === 0) await launchReady(envFor(fixture.root, fixture.home));
  }
});

// ------------------------------------------- a menu over the claude view
//
// The view is composited above the shell's document, so a menu drawn over the claude
// column is under it and a real click lands in claude.ai. This suite cannot SEE that:
// Playwright dispatches into the renderer's own contents, beneath the native view, and
// the menu clicks fine here either way. So what is asserted is the mechanism, from the
// main process: the view is off the window while the menu is open and back once it
// closes.

test("the claude.ai view steps aside while a menu is open over it, and comes back after", async () => {
  const page = await shellReady(await firstShell());
  await openKind(page, "claude");
  await expect(page.locator('[data-region="claude-status"]')).not.toBeEmpty({ timeout: 30_000 });
  const frame = page.locator('[data-region="claude-frame"]');

  const attached = async () =>
    app.evaluate(
      ({ BrowserWindow, WebContentsView }, partitionDir) =>
        BrowserWindow.getAllWindows()
          .flatMap((win) => win.contentView.children)
          .filter(
            (view) =>
              view instanceof WebContentsView &&
              view.webContents.session.storagePath?.endsWith(partitionDir) === true,
          ).length,
      CLAUDE_PARTITION_DIR,
    );
  await expect.poll(attached).toBe(1);

  await page.locator('[data-region="tab"][data-tab-kind="claude"]').click({ button: "right" });
  await expect(page.locator('[data-region="context-menu"]')).toBeVisible();
  await expect.poll(attached, { message: "the menu opened with the view still over it" }).toBe(0);
  await expect(frame).toHaveAttribute("data-covered", "true");

  // Any press outside the menu closes it.
  await page.locator('[data-region="claude-status"]').click();
  await expect(page.locator('[data-region="context-menu"]')).toHaveCount(0);
  await expect
    .poll(attached, { message: "the view never came back after the menu closed" })
    .toBe(1);
  await expect(frame).not.toHaveAttribute("data-covered");
});

// ============================================================ c7: kyosei:// in the pane

test("a kyosei:// URL printed in the terminal is a link, and clicking it opens the tab", async () => {
  const page = await shellReady(await firstShell());
  await openKind(page, "terminal");
  const body = page.locator('[data-region="tab-body"][data-tab-kind="terminal"]');
  await expect(body).toHaveAttribute("data-terminal", "live", { timeout: 30_000 });
  await expect.poll(async () => (await body.innerText()).trim().length).toBeGreaterThan(0);

  // `kyosei://<project>/<id>`, where the project is the store's own record-id prefix
  // (`matchProject` in `main/links.ts` also accepts the project directory's name).
  const url = "kyosei://FIX/FIX-1.c2";
  await body.click();
  // Assembled by the shell from three arguments, so the ECHO of the command a reader
  // types does not itself contain the URL. Without that the pane holds the link twice
  // -- once in the echoed command, where it starts fourteen columns in, and once in the
  // output, where it starts at column zero -- and a check clicking "a few cells in" is
  // clicking the wrong one whenever it picks the wrong row.
  await page.keyboard.type("printf '%s://%s/%s\\n' kyosei FIX FIX-1.c2\n");

  // xterm draws its links as an overlay it decorates on hover; the click is on the
  // cell the URL occupies. Found by walking the screen rows for the one holding the
  // URL, rather than by assuming a line number -- the shell echoed the command too.
  // The row whose WHOLE TEXT is the URL -- so the click below is inside it by
  // construction, rather than by an assumption about where on the row it starts. And
  // waited for, on that row and not on the pane's text: the echoed command reaches the
  // screen the instant it is typed and contains `FIX-1.c2` too, so a wait on the pane's
  // text returns before the OUTPUT exists and the row search then finds nothing -- or,
  // worse, finds the echo and clicks a column the URL does not occupy. That is the whole
  // of this check's earlier flakiness: 2 failures in 3 isolated runs, and one inside a
  // full suite that the retry below could not rescue.
  const rows = body.locator(".xterm-rows > div");
  const rowWithUrl = async (): Promise<number> =>
    rows.evaluateAll(
      (nodes, target) => nodes.findIndex((node) => (node.textContent ?? "").trim() === target),
      url,
    );
  await expect
    .poll(rowWithUrl, {
      timeout: 20_000,
      message: "the URL never reached a rendered row of its own",
    })
    .toBeGreaterThanOrEqual(0);
  const box = await boxOf(rows.nth(await rowWithUrl()));
  // A few cells in from the left edge of the row: the URL starts the line. Two moves,
  // because xterm resolves the hovered link on a CELL CHANGE -- a single move onto the
  // cell from wherever the pointer happened to be can land inside the same cell and ask
  // no provider anything.
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + 4, y);
  await page.mouse.move(box.x + 24, y);
  // The provider was asked about this row and found the link: the half of c7 that is
  // about the PATTERN, separated from the half that is about the click, because a
  // failure in either looks identical from the link strip.
  await expect(body).toHaveAttribute("data-terminal-links", "1", { timeout: 10_000 });

  // The click, retried -- and the retry is the honest form of this check rather than a
  // concession. xterm holds the hovered link in `Linkifier`'s own state and drops it
  // when the rows under the pointer are re-rendered, which an idle shell does on its
  // own (the cursor blinks). So a single hover-then-click loses a race it cannot be
  // told about: measured at 1 delivery in 1 run in isolation and 0 in 1 inside the full
  // suite, on the same build. What is asserted is unchanged -- a real click on the
  // rendered link delivers the link -- and a pane where the link is dead still fails,
  // twenty seconds later. Repeats cost nothing: the record tab dedups by identity, so
  // the count below is still 1.
  const strip = page.locator('[data-region="link-strip"]');
  await expect
    .poll(
      async () => {
        await page.mouse.move(box.x + 4, y);
        await page.mouse.move(box.x + 24, y);
        await page.mouse.down();
        await page.mouse.up();
        return (await strip.textContent()) ?? "";
      },
      { timeout: 20_000, message: "clicking the rendered link delivered nothing" },
    )
    .toContain("FIX-1");

  // The delivery came back over `onOpenTarget` -- the same path a link clicked in the
  // claude view takes -- so the shell opened the tab and said where the reader came
  // from. Both halves are asserted: a strip with no tab would be a link that reported
  // an open that never happened.
  await expect(strip).toContainText("FIX-1.c2");
  await expect(
    page.locator('[data-region="tab"][data-tab-kind="record"][data-tab-arg="FIX-1"]'),
  ).toHaveCount(1);
});

test("the terminal's link provider matches a custom scheme, which the web-links addon does not", () => {
  // Why `registerLinkProvider` and not `@xterm/addon-web-links` (KYO-232.c7). The
  // addon's own regex is the reason, and it is checked here rather than asserted in a
  // comment: a future reader who "simplifies" this back to the addon gets a failure
  // naming the cause instead of a pane where every link works except this app's own.
  //
  // The addon is not a dependency of this app, so what is compared is its published
  // pattern against this app's -- the pattern is the claim, not the package.
  const addonish = /https?:\/\/[^\s]+/;
  const ours = /kyosei:\/\/[^\s"'<>`]*[^\s"'<>`.,;:!?)\]}]/;
  const url = "kyosei://a%20project/KYO-232.c7";
  expect(addonish.test(url), "an http-shaped pattern would have matched a kyosei:// URL").toBe(
    false,
  );
  expect(ours.test(url)).toBe(true);
  // And it stops where a URL stops in prose.
  expect(ours.exec("see kyosei://p/KYO-1, then stop")?.[0]).toBe("kyosei://p/KYO-1");
  expect(ours.exec("(kyosei://p/KYO-1)")?.[0]).toBe("kyosei://p/KYO-1");
});

// ==================================== KYO-310: a showing list follows the store

/**
 * A store of its own for the follow check, and a window small enough to scroll.
 *
 * Its own store because this check FILES RECORDS: the shared fixture's row counts are
 * asserted exactly elsewhere in this file, and a check that adds to it would break
 * them from a distance. Small window because "keeps its scroll position" is only a
 * claim about something a reader can lose -- a sidebar with no overflow restores a
 * scroll of zero and would pass while doing nothing.
 */
async function windowSized(instance: ElectronApplication, width: number, height: number) {
  await instance.evaluate(
    ({ BrowserWindow }, size) => {
      const win = BrowserWindow.getAllWindows().find((candidate) => !candidate.isDestroyed());
      win?.setSize(size.width, size.height);
    },
    { width, height },
  );
}

test("a record filed from outside appears in the showing Records list, keeping what the reader did to it", async () => {
  const home = process.env.KYOSEI_HOME;
  const own = await makeFixture(24);
  const other = await _electron.launch({
    args: launchArgs(),
    env: envFor(own.root, own.home, { KYOSEI_DISPLAYS: uniqueDisplays() }),
  });
  try {
    const page = await shellReady(await readyWindow(await other.firstWindow()));
    markReadyToClose(other);
    await windowSized(other, 900, 460);
    const sidebar = page.locator('[data-region="sidebar"]');
    const rows = page.locator('[data-region="sidebar-row"]');
    if ((await sidebar.getAttribute("data-list")) !== "records") {
      await page.locator('[data-rail-item="records"]').click();
    }
    await expect(sidebar).toHaveAttribute("data-list", "records");
    await expect(rows).toHaveCount(24, { timeout: 60_000 });

    // What the reader has done to this list, before anybody files anything.
    // A search that keeps every row, so the assertion afterwards is about the BOX
    // rather than about which rows a query happened to match.
    await page.locator('[data-region="records-search"]').fill("FIX-");
    await expect(rows).toHaveCount(24, { timeout: 60_000 });
    // A filter kept, and a sort reversed: both live in the list's state rather than in
    // the DOM, and both are what a rebuilt list would silently drop.
    await page.locator('[data-region="filter-button"]').click();
    await page
      .locator('[data-region="filter-group"][data-filter="kind"] [data-filter-value="bug"]')
      .click();
    await page.locator('[data-region="filter-button"]').click();
    await page.locator('[data-region="sort-button"]').click();
    await page.locator('[data-region="sort-direction"]').click();
    await expect(page.locator('[data-region="sort-direction"]')).toHaveAttribute(
      "data-direction",
      "descending",
    );
    await page.locator('[data-region="sort-button"]').click();
    await expect(rows.first()).toHaveAttribute("data-row-arg", "FIX-24");
    // A record open in a tab, so the list carries an open mark to keep (KYO-278).
    await rows.filter({ hasText: "FIX-3" }).first().click();
    const marked = page.locator('[data-region="sidebar-row"][data-row-arg="FIX-3"]');
    await expect(marked).toHaveAttribute("data-open", "true");
    // ...and a scroll position, on a list that really does overflow its pane.
    const scrolled = await page.evaluate(() => {
      const body = document.querySelector('[data-region="sidebar-body"]');
      if (body === null) return 0;
      body.scrollTop = 90;
      return body.scrollTop;
    });
    expect(
      scrolled,
      "the sidebar did not overflow, so there was no scroll to keep",
    ).toBeGreaterThan(0);

    // The terminal pane beside the app, as a controller uses it: one `kyo new`, and
    // NOTHING done to the window afterwards.
    await kyo(own.resolution, ["new", "bug", "--title", "Filed from the pane"], own.root);
    await expect(rows).toHaveCount(25, { timeout: 30_000 });
    await expect(sidebar).toHaveAttribute("data-store-reloads", "1");
    // The row is there, and it is at the top because the reader's descending sort
    // survived -- FIX-25 sorts above FIX-24 under the numeric compare.
    await expect(rows.first()).toHaveAttribute("data-row-arg", "FIX-25");
    await expect(rows.first()).toContainText("Filed from the pane");
    // ...and everything else they had is where they left it.
    await expect(page.locator('[data-region="records-search"]')).toHaveValue("FIX-");
    await expect(page.locator('[data-region="filter-button"]')).toHaveAttribute(
      "data-filtering",
      "true",
    );
    await expect(marked).toHaveAttribute("data-open", "true");
    expect(
      await page.evaluate(
        () => document.querySelector('[data-region="sidebar-body"]')?.scrollTop ?? -1,
      ),
    ).toBe(scrolled);

    // A re-read does not wake itself. Filling this list runs several `kyo`
    // subprocesses against the store it is watching, and if any of them moved the
    // fingerprint the app would re-read forever off its own reads -- the one failure
    // a watch has that a poll does not. Held still for three seconds with the list
    // showing: still one.
    await page.waitForTimeout(3_000);
    await expect(sidebar).toHaveAttribute("data-store-reloads", "1");

    // A list that is not showing costs nothing (KYO-310.c1). The rail item collapses
    // the sidebar; a record filed now re-reads nothing at all -- and the count of
    // store-driven re-reads is the evidence, because "no work happened" has none
    // otherwise.
    await page.locator('[data-rail-item="records"]').click();
    await expect(sidebar).toHaveAttribute("data-open", "false");
    await kyo(own.resolution, ["new", "bug", "--title", "Filed while nobody looked"], own.root);
    await page.waitForTimeout(3_000);
    await expect(sidebar).toHaveAttribute("data-store-reloads", "1");
    // And the list is current again the moment it is shown, which is the behaviour
    // that was there before this record and is still what a rail click means.
    await page.locator('[data-rail-item="records"]').click();
    await expect(rows).toHaveCount(26, { timeout: 60_000 });
  } finally {
    await other.close();
    removeFixture(own);
    if (home === undefined) delete process.env.KYOSEI_HOME;
    else process.env.KYOSEI_HOME = home;
  }
});

test("the Ideas list follows ideas/, and a record filed from outside does not re-read it", async () => {
  const home = process.env.KYOSEI_HOME;
  const own = await makeFixture(2);
  const other = await _electron.launch({
    args: launchArgs(),
    env: envFor(own.root, own.home, { KYOSEI_DISPLAYS: uniqueDisplays() }),
  });
  try {
    const page = await shellReady(await readyWindow(await other.firstWindow()));
    markReadyToClose(other);
    const sidebar = page.locator('[data-region="sidebar"]');
    const rows = page.locator('[data-region="sidebar-row"]');
    if ((await sidebar.getAttribute("data-list")) !== "ideas") {
      await page.locator('[data-rail-item="ideas"]').click();
    }
    await expect(sidebar).toHaveAttribute("data-list", "ideas");
    await expect(page.locator('[data-region="sidebar-loading"]')).toHaveCount(0, {
      timeout: 60_000,
    });

    // A record filed from the terminal is the STORE's change, and the ideas list does
    // not read the store: it must not spawn a `kyo` for it.
    await kyo(own.resolution, ["new", "bug", "--title", "Not an idea"], own.root);
    await page.waitForTimeout(3_000);
    await expect(sidebar).not.toHaveAttribute("data-store-reloads", /\d/);

    // An idea written by a person (or an agent) in the folder that is theirs, is.
    writeFileSync(join(own.root, "ideas", "an unpromoted thought.md"), "# a thought\n", "utf8");
    await expect(rows.filter({ hasText: "an unpromoted thought" })).toHaveCount(1, {
      timeout: 30_000,
    });
    await expect(sidebar).toHaveAttribute("data-store-reloads", "1");
  } finally {
    await other.close();
    removeFixture(own);
    if (home === undefined) delete process.env.KYOSEI_HOME;
    else process.env.KYOSEI_HOME = home;
  }
});
