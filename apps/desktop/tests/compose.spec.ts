// Compose, driven in the real app (KYO-234): the scratchpad and idea files as
// editable tabs, autosave with KYO-150's keystroke-versus-programmatic distinction,
// `[[`/`#`/child-id autocomplete (KYO-55, KYO-234.c5), the scratchpad's promotion to
// an idea FILE (KYO-252; KYO-439 removed the idea tab's record promotion that
// KYO-234.c2 built), and the ideas sidebar (KYO-234.c3).
//
// Own spec file per this dispatch's brief -- `tests/fixture.ts` and `shell.spec.ts`
// are not touched; every helper this file needs is below. Patterns (launch, ready
// window, rail/column locators) are copied from `shell.spec.ts`'s own, which the
// brief names as the model to follow.
//
// The editor is CodeMirror over a contenteditable surface, not a `<textarea>`, so a
// check that needs a precise selection drives it through `root.kyoseiEditor` --
// the same seam `tabs/compose/editor.ts` exposes for exactly this, carried over from
// v2, so the checks write through the same door the app does.

import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { _electron, type ElectronApplication, expect, type Page, test } from "@playwright/test";
import { TMUX_ENV } from "../src/main/terminal";
import {
  closeReadyApp,
  FIXTURE_BASES,
  type Fixture,
  kyo,
  MACOS_NO_RESTORE,
  makeFixture,
  markReadyToClose,
  openKind,
  removeFixture,
  scratchpadPathFor,
  seedIdeas,
  suiteEnv,
  windowShowArg,
  writeStoreFile,
} from "./fixture";

const APP_ROOT = join(__dirname, "..");

interface ComposeFixture extends Fixture {
  /** A bug with one real criterion, for the child-id popup (KYO-234.c5). */
  recordId: string;
  criterionId: string;
}

async function makeComposeFixture(): Promise<ComposeFixture> {
  const fixture = await makeFixture({ records: 0 });
  seedIdeas(fixture);
  // The `everything` base is what `getCompletions()`'s record half runs
  // (`readRecordTree` -> `kyo base run everything --json`); a bare fixture
  // declares no bases at all, and the read would otherwise come back `ok: true`
  // with an empty record list and the refusal buried in `problem`.
  writeStoreFile(fixture, "bases.yml", FIXTURE_BASES);
  const run = (args: string[]) => kyo(fixture.resolution, args, fixture.root);
  const criteriaFile = join(fixture.base, "criteria.txt");
  writeFileSync(criteriaFile, "reproduces the reported failure\n", "utf8");
  const touchesFile = join(fixture.base, "touches.txt");
  writeFileSync(touchesFile, "+src/x.ts\n", "utf8");
  const created = await run([
    "new",
    "bug",
    "--title",
    "A record with a criterion",
    "--criteria-file",
    criteriaFile,
    "--touches-file",
    touchesFile,
    "--json",
  ]);
  const record = (JSON.parse(created.stdout) as { record: { id: string } }).record;
  const show = await run(["show", record.id, "--json"]);
  const detail = JSON.parse(show.stdout) as { criteria: { id: string }[] };
  const criterion = detail.criteria[0];
  if (criterion === undefined) throw new Error("fixture record was created with no criterion");
  return { ...fixture, recordId: record.id, criterionId: criterion.id };
}

let app: ElectronApplication;
let fixture: ComposeFixture;

const PROFILE = mktempProfile();
function mktempProfile(): string {
  return mkdtempSync(join(tmpdir(), "kyosei compose profile "));
}

/**
 * A display configuration nothing has ever been arranged on (`shell.spec.ts`'s own
 * pattern, copied here rather than imported: this file may not touch that one).
 *
 * Layouts are saved per display configuration and this suite's launches share one
 * profile, so without a fresh signature every launch after the first would restore
 * whatever the PREVIOUS test left open -- which is exactly what broke this spec's
 * first draft: `composePane`'s `.first()` picked up a stale scratchpad tab left over
 * from an earlier test instead of the tab the test at hand had just opened.
 */
let displaySeq = 0;
function uniqueDisplays(): string {
  displaySeq += 1;
  return JSON.stringify([
    { id: 5200 + displaySeq, bounds: { x: 0, y: 0, width: 1512, height: 982 } },
  ]);
}

function envFor(): Record<string, string> {
  const env = suiteEnv();
  env.KYOSEI_PROJECT_ROOT = fixture.root;
  env.KYOSEI_HOME = fixture.home;
  env[TMUX_ENV] = "off";
  env.KYOSEI_DISPLAYS = uniqueDisplays();
  return env;
}

function launchArgs(): string[] {
  return [APP_ROOT, `--user-data-dir=${PROFILE}`, ...MACOS_NO_RESTORE, windowShowArg()];
}

async function readyWindow(page: Page): Promise<Page> {
  await page.waitForSelector('body[data-ready="true"]');
  return page;
}

async function launchReady(): Promise<Page> {
  app = await _electron.launch({ args: launchArgs(), env: envFor() });
  const page = await readyWindow(await app.firstWindow());
  markReadyToClose(app);
  return page;
}

/**
 * Close every open tab (`shell.spec.ts`'s own helper, copied): a fresh display
 * configuration still opens on the scratchpad with the records list showing (the
 * app's two fast paths, one click apart), which is right for a human and noise for
 * a check that wants to open exactly one tab and reason about it.
 */
async function emptyWorkspace(page: Page): Promise<void> {
  const closers = page.locator('[data-region="tab"] [data-action="close-tab"]');
  for (let guard = 0; guard < 12 && (await closers.count()) > 0; guard += 1) {
    await closers.first().click();
  }
  await expect(page.locator('[data-region="tab"]')).toHaveCount(0);
}

test.beforeAll(async () => {
  fixture = await makeComposeFixture();
});

test.afterAll(() => {
  removeFixture(fixture);
  rmSync(PROFILE, { recursive: true, force: true });
});

test.beforeEach(async () => {
  const page = await launchReady();
  await emptyWorkspace(page);
});

test.afterEach(async () => {
  await closeReadyApp(app);
});

// ------------------------------------------------------------------ helpers

const rail = (page: Page, id: string) => page.locator(`[data-rail-item="${id}"]`);
const composeTabs = (page: Page) =>
  page.locator('[data-region="tab-body"][data-tab-kind="compose"]');

async function composePane(page: Page, nth = 0): Promise<import("@playwright/test").Locator> {
  const pane = composeTabs(page).nth(nth).locator('[data-region="compose-pane"]');
  await expect(pane).toHaveAttribute("data-state", "ready", { timeout: 30_000 });
  return pane;
}

/** The CodeMirror handle exposed on the editor's own root (`editor.ts`'s own seam). */
async function editorText(pane: import("@playwright/test").Locator): Promise<string> {
  return pane.locator('[data-region="idea-editor-pane"]').evaluate((el) => {
    return (el as HTMLElement & { kyoseiEditor: { text(): string } }).kyoseiEditor.text();
  });
}

/** Select the first occurrence of `needle` inside the editor, through its own handle. */
async function selectText(pane: import("@playwright/test").Locator, needle: string): Promise<void> {
  await pane.locator('[data-region="idea-editor-pane"]').evaluate((el, target) => {
    const handle = (
      el as HTMLElement & {
        kyoseiEditor: { text(): string; view: { dispatch(spec: unknown): void } };
      }
    ).kyoseiEditor;
    const text = handle.text();
    const from = text.indexOf(target);
    if (from < 0) throw new Error(`"${target}" is not in the editor's text`);
    handle.view.dispatch({ selection: { anchor: from, head: from + target.length } });
  }, needle);
}

// ============================================================ c1: editable tabs, autosave

test("the scratchpad opens as an editable tab; a keystroke saves, a programmatic load does not", async () => {
  const page = await readyWindow(await app.firstWindow());
  await openKind(page, "compose", "scratchpad");
  const pane = await composePane(page);
  const chip = pane.locator('[data-region="save-state"]');

  // KYO-150: the app's own load never arms the save -- the chip stays clean.
  await expect(chip).toHaveAttribute("data-state", "clean");

  await pane.locator('[data-region="idea-editor"]').click();
  await page.keyboard.type("a fresh thought, typed by a human");

  // The keystroke armed it immediately, before the debounce fires.
  await expect(chip).toHaveAttribute("data-state", "dirty");
  // ...and the debounce flushes it to the state the app reports back as "saved".
  await expect(chip).toHaveAttribute("data-state", "saved", { timeout: 5_000 });

  const onDisk = await editorText(pane);
  expect(onDisk).toContain("a fresh thought, typed by a human");
});

test("an idea file opens as an editable tab, titled by its own name", async () => {
  const page = await readyWindow(await app.firstWindow());
  await rail(page, "ideas").click();
  await page.locator('[data-region="sidebar-row"][data-row-arg="idea:bare.md"]').click();
  const pane = await composePane(page);
  expect(await editorText(pane)).toContain("No frontmatter here at all.");
  // The title lives on the tab STRIP button, a sibling of the tab-body content div.
  await expect(page.locator('[data-region="tab"][data-tab-kind="compose"]').first()).toContainText(
    "bare.md",
  );
});

test("two compose tabs are open side by side in two columns", async () => {
  const page = await readyWindow(await app.firstWindow());
  await openKind(page, "compose", "scratchpad");
  await page.locator('[data-action="split"]').first().click();
  await expect(page.locator('[data-region="column"]')).toHaveCount(2);

  // Splitting makes the NEW column (1) the active one, so a plain click on a
  // sidebar row -- which opens in the active column -- lands the idea beside the
  // scratchpad rather than on top of it.
  await rail(page, "ideas").click();
  await page.locator('[data-region="sidebar-row"][data-row-arg="idea:bare.md"]').click();

  const col0 = page.locator('[data-region="column"]').nth(0);
  const col1 = page.locator('[data-region="column"]').nth(1);
  await expect(
    col0.locator('[data-region="tab-body"][data-tab-kind="compose"][data-tab-arg="scratchpad"]'),
  ).toHaveCount(1);
  await expect(
    col1.locator('[data-region="tab-body"][data-tab-kind="compose"][data-tab-arg="idea:bare.md"]'),
  ).toHaveCount(1);
  // Both editable: each has its own editor pane in the "ready" state.
  await expect(col0.locator('[data-region="compose-pane"][data-state="ready"]')).toHaveCount(1);
  await expect(col1.locator('[data-region="compose-pane"][data-state="ready"]')).toHaveCount(1);
});

// ============================================================ c1 + c5: autocomplete

test("`[[` offers real records, `#` offers real tags, and `[[<id>.` offers only that record's real children", async () => {
  const page = await readyWindow(await app.firstWindow());
  await openKind(page, "compose", "scratchpad");
  const pane = await composePane(page);
  const editorEl = pane.locator('[data-region="idea-editor-pane"]');
  await expect(editorEl).toHaveAttribute("data-completions", "ready", { timeout: 20_000 });
  // The actual contenteditable, not the pane wrapper (which also spans the ribbon
  // header above it): clicking the wrapper's centre can land on a ribbon button
  // instead of the text for a short document.
  await pane.locator('[data-region="idea-editor"]').click();

  await page.keyboard.type("[[");
  const popup = page.locator('[data-region="completion-popup"][data-kind="record"]');
  await expect(popup).toBeVisible();
  await expect(popup).toContainText(fixture.recordId);

  // Close and try the child-id trigger for the real record.
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  await selectTextAll(page);
  await page.keyboard.press("Delete");
  await page.keyboard.type(`[[${fixture.recordId}.`);
  const childPopup = page.locator('[data-region="completion-popup"]');
  await expect(childPopup).toContainText(fixture.criterionId, { timeout: 10_000 });

  // A child id that does not exist on this record offers nothing (KYO-55's closed
  // set, extended to child shapes): the popup never lists it.
  await page.keyboard.type("zzzz");
  await expect(page.locator('[data-region="completion-popup"]')).toHaveCount(0, { timeout: 3_000 });

  await selectTextAll(page);
  await page.keyboard.press("Delete");
  await page.keyboard.type("#");
  const tagPopup = page.locator('[data-region="completion-popup"][data-kind="tag"]');
  await expect(tagPopup).toBeVisible();
  await expect(tagPopup).toContainText("loose");
});

async function selectTextAll(page: Page): Promise<void> {
  await page.keyboard.press(process.platform === "darwin" ? "Meta+a" : "Control+a");
}

test("a typed [[id]] is Mod-clickable and opens the record through followLink", async () => {
  const page = await readyWindow(await app.firstWindow());
  await openKind(page, "compose", "scratchpad");
  const pane = await composePane(page);

  // KYO-287: the scratchpad is ONE file for the whole spec -- `KYOSEI_HOME` is the
  // fixture's and `beforeAll` makes one fixture -- so this tab opens on whatever the
  // test before it left there, and the `[[<id>.` check above leaves `[[FIX-1.`: a
  // half-typed trigger with no closing brackets. Typing into that produced
  // `[[FIX-1.see [[FIX-1]] for context`, and BOTH halves of this check then read the
  // wrong thing. `indexOf` found the id in the leftover rather than in the typed
  // link, so the click landed there; and `linkAt`'s `/\[\[([^\]\n]+)\]\]/` matched
  // from the leftover's `[[` all the way to the TYPED link's `]]`, so even a click on
  // the right token resolved to `FIX-1.see [[FIX-1`, which is no record and opens no
  // tab. Three failures in three in the file, none in three alone. So the document
  // this check reasons about is one it writes itself, start to end.
  await pane.locator('[data-region="idea-editor"]').click();
  await selectTextAll(page);
  await page.keyboard.press("Delete");
  await expect.poll(() => editorText(pane)).toBe("");
  const line = `see [[${fixture.recordId}]] for context`;
  await page.keyboard.type(line);
  // Asserted, not assumed: if the carry-over ever comes back this says so here, by
  // name, instead of ten seconds later as a record tab that never opened.
  expect(await editorText(pane)).toBe(line);
  // Give the popup a moment to close on its own (typing "for context" after the
  // closing ]] leaves the trigger) before clicking inside the token.
  await page.waitForTimeout(200);

  // Screen coordinates for a position INSIDE the id text, from CodeMirror's own
  // `coordsAtPos` through the exposed `kyoseiEditor` handle -- not guessed from the
  // pane's pixel width, which is fragile against font metrics and zoom. The point is
  // the midpoint BETWEEN the two ends of the id rather than two pixels off one end of
  // it, and it is then put back through `posAtCoords` -- the same call the editor's
  // own click handler makes -- so a point that could not have followed the link is
  // named here rather than clicked and waited on.
  const point = await pane.locator('[data-region="idea-editor-pane"]').evaluate((el, needle) => {
    const handle = (
      el as HTMLElement & {
        kyoseiEditor: {
          text(): string;
          view: {
            coordsAtPos(pos: number): { left: number; top: number; bottom: number } | null;
            posAtCoords(coords: { x: number; y: number }): number | null;
          };
        };
      }
    ).kyoseiEditor;
    const text = handle.text();
    const token = `[[${needle}]]`;
    const at = text.indexOf(token);
    if (at < 0) throw new Error(`"${token}" is not in the editor's text: ${JSON.stringify(text)}`);
    const idFrom = at + 2;
    const idTo = idFrom + needle.length;
    const first = handle.view.coordsAtPos(idFrom);
    const last = handle.view.coordsAtPos(idTo);
    if (first === null || last === null) {
      throw new Error(`coordsAtPos(${idFrom}) or coordsAtPos(${idTo}) returned null`);
    }
    const spot = { x: (first.left + last.left) / 2, y: (first.top + first.bottom) / 2 };
    const back = handle.view.posAtCoords(spot);
    if (back === null || back < at || back > at + token.length) {
      throw new Error(
        `posAtCoords(${spot.x}, ${spot.y}) came back ${back}, outside the ` +
          `${token} at ${at}..${at + token.length} in ${JSON.stringify(text)}`,
      );
    }
    return spot;
  }, fixture.recordId);

  // `Mouse.click` has no `modifiers` option (unlike `Locator.click`), so the
  // modifier key is held down around a plain click instead.
  const modifierKey = process.platform === "darwin" ? "Meta" : "Control";
  await page.keyboard.down(modifierKey);
  await page.mouse.click(point.x, point.y);
  await page.keyboard.up(modifierKey);

  await expect(
    page.locator(
      `[data-region="tab-body"][data-tab-kind="record"][data-tab-arg="${fixture.recordId}"]`,
    ),
  ).toHaveCount(1, { timeout: 10_000 });
});

// ============================================================ promotion: idea files only

test("an idea tab offers no record promotion, with or without a selection (KYO-439)", async () => {
  // The idea tab once carried "Promote selection" and "Promote file", which ran `kyo
  // promote` from the app (KYO-234.c2). A record's structure is the controller's to
  // write, so the ribbon is empty and no promote dialog can open.
  const page = await readyWindow(await app.firstWindow());
  await rail(page, "ideas").click();
  await page.locator('[data-region="sidebar-row"][data-row-arg="idea:bare.md"]').click();
  const pane = await composePane(page);
  const ribbon = pane.locator('[data-region="editor-ribbon"]');
  await expect(ribbon).toHaveAttribute("data-buttons", "0");
  await expect(pane.locator('[data-region="idea-editor"]')).toContainText("No frontmatter");
  await selectText(pane, "No frontmatter");
  await expect(ribbon).toHaveAttribute("data-buttons", "0");
  await expect(ribbon.locator("[data-ribbon-button]")).toHaveCount(0);
  await expect(page.locator('[data-region="promote-dialog"]')).toHaveCount(0);
});

test("the scratchpad promotes a selection to an IDEA FILE: name, folder, remove-or-keep, path copied", async () => {
  const page = await readyWindow(await app.firstWindow());
  await openKind(page, "compose", "scratchpad");
  const pane = await composePane(page);
  // One button, and only when something is selected (KYO-252).
  await expect(pane.locator('[data-region="editor-ribbon"] [data-ribbon-button]')).toHaveCount(1);
  await expect(pane.locator('[data-ribbon-button="promote-file"]')).toHaveCount(0);
  await pane.locator('[data-region="idea-editor"]').click();
  await page.keyboard.type("Line one stays.\nCapture this exact thought.\nLine three stays.");
  await expect(pane.locator('[data-region="save-state"]')).toHaveAttribute("data-state", "saved", {
    timeout: 5_000,
  });

  await selectText(pane, "Capture this exact thought.");
  await pane.locator('[data-ribbon-button="promote-selection"]').click();
  const dialog = page.locator('[data-region="idea-dialog"]');
  await expect(dialog).toBeVisible();
  // The name is suggested from the text; the folders are the vault's own; the
  // checkbox is on by default.
  await expect(dialog.locator('[data-region="idea-name"]')).toHaveValue(
    "capture-this-exact-thought",
  );
  await dialog.locator('[data-region="idea-folder"]').selectOption("in a folder");
  await expect(dialog.locator('[data-region="idea-remove"]')).toBeChecked();
  await dialog.locator('[data-region="idea-save"]').click();
  await expect(dialog).toHaveCount(0, { timeout: 15_000 });

  // The file, where it was asked for, holding the text -- and no record.
  const written = join(fixture.root, "ideas", "in a folder", "capture-this-exact-thought.md");
  await expect.poll(() => existsSync(written)).toBe(true);
  expect(readFileSync(written, "utf8")).toContain("Capture this exact thought.");
  await expect(page.locator('[data-region="tab-body"][data-tab-kind="record"]')).toHaveCount(0);
  // Removed from the scratchpad, the rest kept.
  const after = await editorText(pane);
  expect(after).not.toContain("Capture this exact thought.");
  expect(after).toContain("Line one stays.");
  expect(after).toContain("Line three stays.");
  // The path, relative to the project root, is on the clipboard and the tab says so.
  const copied = await (app as ElectronApplication).evaluate(({ clipboard }) =>
    clipboard.readText(),
  );
  expect(copied).toBe("ideas/in a folder/capture-this-exact-thought.md");
  await expect(pane.locator('[data-region="compose-notice"]')).toContainText("path copied");

  // Unchecked: the text stays where it was.
  await selectText(pane, "Line three stays.");
  await pane.locator('[data-ribbon-button="promote-selection"]').click();
  await page.locator('[data-region="idea-name"]').fill("kept in place");
  await page.locator('[data-region="idea-remove"]').uncheck();
  await page.locator('[data-region="idea-save"]').click();
  await expect(page.locator('[data-region="idea-dialog"]')).toHaveCount(0, { timeout: 15_000 });
  await expect.poll(() => existsSync(join(fixture.root, "ideas", "kept in place.md"))).toBe(true);
  expect(await editorText(pane)).toContain("Line three stays.");
});

// ============================================================ c3: the ideas sidebar

test("the ideas sidebar lists files under ideas/ and opens one in the active column, a new tab, and the other column", async () => {
  const page = await readyWindow(await app.firstWindow());
  await rail(page, "ideas").click();
  const rows = page.locator('[data-region="sidebar-row"]');
  await expect(rows).not.toHaveCount(0);
  // Every fixture markdown file is listed; the non-markdown one is not.
  await expect(
    page.locator('[data-region="sidebar-row"][data-row-arg="idea:bare.md"]'),
  ).toHaveCount(1);
  await expect(
    page.locator('[data-region="sidebar-row"][data-row-arg^="idea:"][data-row-arg$=".png"]'),
  ).toHaveCount(0);

  // Active column.
  await page.locator('[data-region="sidebar-row"][data-row-arg="idea:bare.md"]').click();
  await expect(composeTabs(page)).toHaveCount(1);

  // Open in a new tab: the row's own menu (KYO-234.c3), same row.
  await page.locator('[data-region="sidebar-row"][data-row-arg="idea:bare.md"]').click({
    button: "right",
  });
  await page.getByText("Open in a new tab").click();
  await expect(composeTabs(page)).toHaveCount(2);

  // Open in the right column (KYO-282: named, so which column no longer depends on
  // which one is active). Column 0 is reactivated first only so the assertion below
  // about column 0's tabs still reads from where the reader was.
  await page.locator('[data-action="split"]').first().click();
  await page
    .locator('[data-region="column"]')
    .nth(0)
    .locator('[data-region="tab"][data-tab-kind="compose"]')
    .first()
    .click();
  await page
    .locator('[data-region="sidebar-row"][data-row-arg="idea:a first idea.md"]')
    .click({ button: "right" });
  await page.getByText("Open in the right column").click();
  await expect(
    page
      .locator('[data-region="column"]')
      .nth(1)
      .locator('[data-region="tab-body"][data-tab-kind="compose"]'),
  ).toHaveCount(1);
});

// ======================== KYO-575: two editors of one file, and a file changed elsewhere

/** The editor's handle, for a caret and a text the check sets itself. */
async function caretAfter(pane: import("@playwright/test").Locator, needle: string): Promise<void> {
  await pane.locator('[data-region="idea-editor-pane"]').evaluate((el, target) => {
    const handle = (
      el as HTMLElement & {
        kyoseiEditor: { text(): string; setCaret(at: number): void; focus(): void };
      }
    ).kyoseiEditor;
    const at = handle.text().indexOf(target);
    if (at < 0) throw new Error(`"${target}" is not in the editor's text`);
    handle.focus();
    handle.setCaret(at + target.length);
  }, needle);
}

/** Where the caret is, as the text before it: survives a line inserted above it. */
async function textBeforeCaret(pane: import("@playwright/test").Locator): Promise<string> {
  return pane.locator('[data-region="idea-editor-pane"]').evaluate((el) => {
    const handle = (
      el as HTMLElement & {
        kyoseiEditor: {
          text(): string;
          view: { state: { selection: { main: { head: number } } } };
        };
      }
    ).kyoseiEditor;
    return handle.text().slice(0, handle.view.state.selection.main.head);
  });
}

test("an open scratchpad with no unsaved edits reloads when the file changes elsewhere, keeps its caret, and arms no save (KYO-575.c3)", async () => {
  const path = scratchpadPathFor(fixture);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, "a line\nthe caret's line\nlast line\n", "utf8");
  const page = await readyWindow(await app.firstWindow());
  await openKind(page, "compose", "scratchpad");
  const pane = await composePane(page);
  await expect.poll(() => editorText(pane)).toBe("a line\nthe caret's line\nlast line\n");
  await caretAfter(pane, "the caret's line");

  // Another window or machine saves it: a line above the caret, a line changed below.
  const elsewhere = "written elsewhere\na line\nthe caret's line\nlast line, edited elsewhere\n";
  writeFileSync(path, elsewhere, "utf8");
  await expect.poll(() => editorText(pane), { timeout: 15_000 }).toBe(elsewhere);
  const chip = pane.locator('[data-region="save-state"]');
  // The app put it on screen: nothing is owed (KYO-150), so nothing is written back.
  await expect(chip).toHaveAttribute("data-state", "clean");
  expect(await textBeforeCaret(pane)).toBe("written elsewhere\na line\nthe caret's line");
  const stamp = statSync(path).mtimeMs;
  await page.waitForTimeout(2_000);
  await expect(chip).toHaveAttribute("data-state", "clean");
  expect(statSync(path).mtimeMs).toBe(stamp);
  expect(readFileSync(path, "utf8")).toBe(elsewhere);
});

test("an open idea file with no unsaved edits reloads when it changes elsewhere, and arms no save (KYO-575.c3)", async () => {
  const path = join(fixture.root, "ideas", "followed.md");
  writeFileSync(path, "# Followed\n\nas it was\n", "utf8");
  const page = await readyWindow(await app.firstWindow());
  await rail(page, "ideas").click();
  const row = page.locator('[data-region="sidebar-row"][data-row-arg="idea:followed.md"]');
  await expect(row).toHaveCount(1, { timeout: 15_000 });
  await row.click();
  const pane = await composePane(page);
  await expect.poll(() => editorText(pane)).toContain("as it was");

  writeFileSync(path, "# Followed\n\nas it was\nand a line from elsewhere\n", "utf8");
  await expect
    .poll(() => editorText(pane), { timeout: 15_000 })
    .toContain("and a line from elsewhere");
  const chip = pane.locator('[data-region="save-state"]');
  await expect(chip).toHaveAttribute("data-state", "clean");
  const stamp = statSync(path).mtimeMs;
  await page.waitForTimeout(2_000);
  expect(statSync(path).mtimeMs).toBe(stamp);
});

test("two tabs on one idea file: typing in one and then the other keeps both texts in the file and in both tabs (KYO-575.c1, c4)", async () => {
  const path = join(fixture.root, "ideas", "twice.md");
  writeFileSync(path, "# Twice\n\nthe start\n", "utf8");
  const page = await readyWindow(await app.firstWindow());
  await rail(page, "ideas").click();
  const row = page.locator('[data-region="sidebar-row"][data-row-arg="idea:twice.md"]');
  await expect(row).toHaveCount(1, { timeout: 15_000 });
  await row.click();
  await row.click({ button: "right" });
  await page.getByText("Open in the right column").click();
  const left = page
    .locator('[data-region="column"]')
    .nth(0)
    .locator('[data-region="tab-body"][data-tab-kind="compose"] [data-region="compose-pane"]');
  const right = page
    .locator('[data-region="column"]')
    .nth(1)
    .locator('[data-region="tab-body"][data-tab-kind="compose"] [data-region="compose-pane"]');
  await expect(left).toHaveAttribute("data-state", "ready", { timeout: 30_000 });
  await expect(right).toHaveAttribute("data-state", "ready", { timeout: 30_000 });

  await caretAfter(left, "the start\n");
  await page.keyboard.type("typed on the left");
  await expect(left.locator('[data-region="save-state"]')).toHaveAttribute("data-state", "saved", {
    timeout: 5_000,
  });
  // Straight on to the other tab, which loaded the file before the left one's save.
  await caretAfter(right, "the start\n");
  await page.keyboard.type("typed on the right");
  await expect(right.locator('[data-region="save-state"]')).toHaveAttribute("data-state", "saved", {
    timeout: 10_000,
  });
  await expect
    .poll(() => readFileSync(path, "utf8"), { timeout: 10_000 })
    .toMatch(
      /typed on the left[\s\S]*typed on the right|typed on the right[\s\S]*typed on the left/,
    );
  // Each tab ends showing both: the left one by reloading after the right one's save,
  // which arms nothing (KYO-150), the right one by the merge it was answered with.
  for (const pane of [left, right]) {
    await expect
      .poll(
        async () => {
          const text = await editorText(pane);
          return text.includes("typed on the left") && text.includes("typed on the right");
        },
        { timeout: 15_000 },
      )
      .toBe(true);
  }
  await expect(left.locator('[data-region="save-state"]')).not.toHaveAttribute(
    "data-state",
    "dirty",
  );
});

test("two windows on one scratchpad: typing in one and then the other leaves both texts in the file and in both windows (KYO-575.c5's check)", async () => {
  const path = scratchpadPathFor(fixture);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, "shared start\n", "utf8");
  const first = await readyWindow(await app.firstWindow());
  await openKind(first, "compose", "scratchpad");
  const one = await composePane(first);
  await expect.poll(() => editorText(one)).toBe("shared start\n");

  const opened = app.waitForEvent("window");
  await first.evaluate(() => window.kyosei.openWindow());
  const second = await readyWindow(await opened);
  await emptyWorkspace(second);
  await openKind(second, "compose", "scratchpad");
  const two = await composePane(second);
  await expect.poll(() => editorText(two)).toBe("shared start\n");

  await caretAfter(one, "shared start\n");
  await first.keyboard.type("from the first window");
  await expect(one.locator('[data-region="save-state"]')).toHaveAttribute("data-state", "saved", {
    timeout: 5_000,
  });
  await caretAfter(two, "shared start\n");
  await second.keyboard.type("from the second window");
  await expect(two.locator('[data-region="save-state"]')).toHaveAttribute("data-state", "saved", {
    timeout: 10_000,
  });
  await expect
    .poll(() => readFileSync(path, "utf8"), { timeout: 10_000 })
    .toContain("from the first window");
  expect(readFileSync(path, "utf8")).toContain("from the second window");
  for (const pane of [one, two]) {
    await expect
      .poll(
        async () => {
          const text = await editorText(pane);
          return text.includes("from the first window") && text.includes("from the second window");
        },
        { timeout: 15_000 },
      )
      .toBe(true);
  }
  await expect(one.locator('[data-region="save-state"]')).not.toHaveAttribute(
    "data-state",
    "dirty",
  );
});
