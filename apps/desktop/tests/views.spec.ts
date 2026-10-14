// The views tab, against the real app (KYO-235): the records sidebar's search and base
// switching, the awaiting-human view over a fixture with a `done` record carrying an
// `awaiting:human-review` gate, the badge count, and brief links.
//
// The records SIDEBAR is the shell's own (`shell/sidebar.ts`, KYO-232) and is exercised
// here rather than re-tested for its own sake: KYO-235.c4 asks this file to cover it
// because it is the surface a reader actually uses to reach the views this record owns
// (search and base switching feed the same read model the "Awaiting you" pinned group
// and this file's dedicated "Awaiting you" rail item read). `tests/shell.spec.ts` is
// held for KYO-232's own dispatch and is not duplicated here.
//
// A click is checked end to end: `shell.ts`'s `onDelivery` (KYO-232) already turns a
// resolved `kyosei://` link into an opened tab and the "you were sent here" strip, so
// clicking one of this tab's rendered ids is checked the same way `tests/shell.spec.ts`
// checks a terminal-pane link -- against the strip and the tab it opened, not against a
// log line. What this record's brief tab owns is finding the id and handing it to
// `context.followLink`; scrolling the opened record page to the anchor is the record
// page's own job (KYO-233, `shell.ts`'s comment on `onDelivery`).
//
// One store, mutated a step further before each later test and read by all of them in
// file order (`mode: "serial"`, and the suite is `workers: 1` project-wide already) --
// the same shape `tests/links.spec.ts` uses for its multi-step scenarios. Each test
// says what it adds to the store before it launches the app.

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
  removeFixture,
  suiteEnv,
  windowShowArg,
  writeStoreFile,
} from "./fixture";

test.describe.configure({ mode: "serial" });

const APP_ROOT = join(__dirname, "..");
const PROFILE = mkdtempSync(join(tmpdir(), "kyosei views profile "));

const BASES = `bases:
  needs-review:
    summary: Everything titled for review.
    source: records
    filters:
      status: done
    columns: [id, kind, status, title]
    sort: [seq]
`;

let fixture: Fixture;
let app: ElectronApplication | null = null;

/** Write a file of prose into the fixture's own temp directory, for a `--*-file` flag. */
function proseFile(name: string, body: string): string {
  const path = join(fixture.base, name);
  writeFileSync(path, body, "utf8");
  return path;
}

function run(args: string[]): Promise<{ code: number; stdout: string; stderr: string }> {
  return kyo(fixture.resolution, args, fixture.root);
}

/**
 * FIX-1: a `holding-for` gate, open but NOT an awaiting-human type -- the negative case
 * the awaiting view has to filter out. FIX-2: a criterion met and moved to `done`, so
 * the base-switching check has a status to filter on and the awaiting-view check has a
 * `done` record ready to gate. Neither carries the awaiting gate yet -- each later test
 * adds exactly the store change it is about, in order, against this shared fixture.
 */
test.beforeAll(async () => {
  fixture = await makeFixture({ records: 0 });
  writeStoreFile(fixture, "bases.yml", BASES);

  await run([
    "new",
    "bug",
    "--title",
    "Alpha holding thing",
    "--criteria-file",
    proseFile("crit1.txt", "the upstream release ships\n"),
  ]);
  await run(["gate", "FIX-1", "holding-for", "--do", "wait for the upstream release"]);

  await run([
    "new",
    "bug",
    "--title",
    "Beta needs a human",
    "--criteria-file",
    proseFile("crit.txt", "somebody signs off on this\n"),
  ]);
  await run(["verify", "FIX-2.c1", "--verdict", "met", "--method", "machine-checked"]);
  await run(["move", "FIX-2", "done"]);
});

test.afterEach(async () => {
  if (app !== null) {
    await closeReadyApp(app);
    app = null;
  }
});

test.afterAll(() => {
  removeFixture(fixture);
  rmSync(PROFILE, { recursive: true, force: true });
});

function envFor(): Record<string, string> {
  const env = suiteEnv();
  env.KYOSEI_PROJECT_ROOT = fixture.root;
  env.KYOSEI_HOME = fixture.home;
  env[TMUX_ENV] = "off";
  env.KYOSEI_LOGIN_SHELL = "off";
  return env;
}

async function launch(): Promise<Page> {
  app = await _electron.launch({
    args: [APP_ROOT, `--user-data-dir=${PROFILE}`, ...MACOS_NO_RESTORE, windowShowArg()],
    env: envFor(),
  });
  const page = await app.firstWindow();
  await page.waitForSelector('body[data-ready="true"]');
  await page.waitForSelector('body[data-facts="ready"]');
  await page.waitForSelector('body[data-badges="ready"]');
  markReadyToClose(app);
  return page;
}

const rail = (page: Page, id: string) => page.locator(`[data-rail-item="${id}"]`);

async function rowsFor(page: Page, list: string) {
  const sidebar = page.locator('[data-region="sidebar"]');
  if ((await sidebar.getAttribute("data-list")) !== list) await rail(page, list).click();
  await expect(sidebar).toHaveAttribute("data-list", list);
  await expect(page.locator('[data-region="sidebar-loading"]')).toHaveCount(0, {
    timeout: 60_000,
  });
  return page.locator('[data-region="sidebar-row"]');
}

// ============================================================ KYO-235.c1: sidebar

test("the records sidebar searches by id or title", async () => {
  const page = await launch();
  const rows = await rowsFor(page, "records");
  await expect(rows.filter({ hasText: "FIX-1" })).toHaveCount(1);
  await expect(rows.filter({ hasText: "FIX-2" })).toHaveCount(1);

  const search = page.locator('[data-region="records-search"]');
  await search.fill("Beta");
  await expect
    .poll(async () => page.locator('[data-region="sidebar-row"]').count())
    .toBeLessThanOrEqual(1);
  const filtered = page.locator('[data-region="sidebar-row"]');
  await expect(filtered.filter({ hasText: "FIX-2" })).toBeVisible();
  await expect(filtered.filter({ hasText: "FIX-1" })).toHaveCount(0);

  // And by id.
  await search.fill("FIX-1");
  await expect(
    page.locator('[data-region="sidebar-row"]').filter({ hasText: "FIX-1" }),
  ).toBeVisible();
  await expect(
    page.locator('[data-region="sidebar-row"]').filter({ hasText: "FIX-2" }),
  ).toHaveCount(0);
});

test("a sidebar list never just goes blank: what it is holding is stated, and stamped (KYO-306)", async () => {
  // KYO-306: three checks in one run died waiting thirty seconds for a row, each
  // against a list that had drawn nothing, and the page snapshot each one kept said
  // nothing about the list at all. Two things close that, and this asserts both: an
  // empty list SAYS which emptiness it is, and the sidebar STAMPS what its body holds
  // so a kept snapshot carries the answer whether or not anyone thought to look.
  const page = await launch();
  const sidebar = page.locator('[data-region="sidebar"]');
  const rows = await rowsFor(page, "records");
  await expect(rows).not.toHaveCount(0);
  await expect(sidebar).toHaveAttribute("data-load", /^rows:[1-9]/);

  // Narrowed to nothing by a search: rows go, and a sentence naming the search
  // arrives in their place. `silent-empty` -- rows gone, nothing said -- is the state
  // this record is about, and no list may ever be in it.
  await page.locator('[data-region="records-search"]').fill("no record is called this");
  await expect(rows).toHaveCount(0);
  await expect(page.locator('[data-region="sidebar-problem"]')).toContainText(
    'No record matches "no record is called this"',
  );
  await expect(sidebar).toHaveAttribute("data-load", "stated");

  // And back: the stamp follows the list rather than latching.
  await page.locator('[data-region="records-search"]').fill("");
  await expect(sidebar).toHaveAttribute("data-load", /^rows:[1-9]/);
});

test("the records sidebar has no base dropdown; its filter box narrows by status instead (KYO-259)", async () => {
  // The saved-bases dropdown went with KYO-259, since the filter box now does its job.
  // What `needs-review` did (status "done") is the filter box's Status group, and Clear
  // filters is that box's first row.
  const page = await launch();
  await rowsFor(page, "records");
  await expect(page.locator('[data-region="records-base"]')).toHaveCount(0);
  await expect(page.locator('[data-region="sidebar"] select')).toHaveCount(0);

  await page.locator('[data-region="filter-button"]').click();
  const box = page.locator('[data-region="sidebar-box"]');
  await expect(box.locator(":scope > *").first()).toContainText("Clear filters");
  await box
    .locator('[data-region="filter-group"][data-filter="status"] [data-filter-value="done"]')
    .click();
  const rows = page.locator('[data-region="sidebar-row"]');
  await expect(rows.filter({ hasText: "FIX-2" })).toBeVisible();
  await expect(rows.filter({ hasText: "FIX-1" })).toHaveCount(0);
  await page.locator('[data-region="filter-clear"]').click();
  await expect(rows.filter({ hasText: "FIX-1" })).toBeVisible();
});

// ============================================================ KYO-235.c1/.c4: awaiting

test("the awaiting-human view lists a done record's open awaiting:human-review gate, and only that gate type", async () => {
  // The shape KYO-235.c4 names by name: `done`, then gated for human review -- status
  // and an open gate are orthogonal (KYO-227's "regardless of record status").
  await run(["brief", "--session-start"]);
  await run(["gate", "FIX-2", "awaiting:human-review", "--do", "somebody looks at this"]);

  const page = await launch();
  // The rail's Awaiting LIST is the one place for this query since KYO-257.c8, which
  // took the full page off the home page; the gate's instruction rides on each row.
  const rowsList = await rowsFor(page, "awaiting");
  await expect(page.locator('[data-region="home-card"][data-home-arg="awaiting"]')).toHaveCount(0);

  // FIX-2 is `done` and still shows here: the query is over gate TYPE across every
  // record status, not over open work (KYO-229.r1 premise_false[0]).
  const fix2 = rowsList.filter({ hasText: "FIX-2" });
  await expect(fix2).toBeVisible();
  await expect(fix2.locator(".row-badge")).toHaveText("awaiting:human-review");
  await expect(fix2).toHaveAttribute("data-tip", /somebody looks at this/);
  // FIX-1's `holding-for` gate is open too, and is NOT an awaiting-human type: it must
  // not appear here even though it is an open gate.
  await expect(rowsList.filter({ hasText: "FIX-1" })).toHaveCount(0);
  await expect(rowsList).toHaveCount(1);

  // The badge on the shell's own "Awaiting you" rail item counts the same query.
  const badge = page.locator('[data-badge-for="awaiting"]');
  await expect(badge).toHaveAttribute("data-count", "1");
});

// ============================================================ KYO-235.c2: brief links

test("the brief tab renders record, shift-note ids -- and ids inside prose -- as links that resolve", async () => {
  // A shift note whose PROSE names another record: KYO-235.c2 asks for ids "rendered as
  // a link" wherever they occur, and a note's own id is a different case from an id a
  // human happened to type inside it.
  await run([
    "note",
    "--shift",
    "--file",
    proseFile("note.txt", "Please also check FIX-1 while you are in there.\n"),
  ]);
  // Closes this brief over the gate-open event from the previous test and this note.
  await run(["brief", "--session-start"]);

  const page = await launch();
  const briefs = await rowsFor(page, "notes");
  await briefs.filter({ hasText: "Latest brief" }).first().click();

  const body = page.locator(
    '[data-region="tab-body"][data-tab-kind="views"][data-tab-arg="brief"]',
  );
  await expect(body).toHaveAttribute("data-state", "ready");
  const briefTabButton = page.locator(
    '[data-region="tab"][data-tab-kind="views"][data-tab-arg="brief"]',
  );
  const strip = page.locator('[data-region="link-strip"]');

  // The event section carries the gate command, with FIX-2 as its record id. Clicking
  // it hands `kyosei://FIX/FIX-2` to `context.followLink`; `shell.ts`'s `onDelivery`
  // (KYO-232) resolves it, opens the record tab, and says so in the strip.
  const events = body.locator('[data-section="events"]');
  await expect(events).toBeVisible();
  const recordLink = events.locator('[data-id-link="FIX-2"]').first();
  await expect(recordLink).toBeVisible();
  await recordLink.click();
  await expect(strip).toContainText("Opened FIX-2 from kyosei://FIX/FIX-2");
  await expect(
    page.locator('[data-region="tab-body"][data-tab-kind="record"][data-tab-arg="FIX-2"]'),
  ).toHaveCount(1);

  // The link navigated THIS tab (KYO-247): the brief is a step back, not another tab.
  await expect(briefTabButton).toHaveCount(0);
  const back = page.locator('[data-region="column"][data-active="true"] [data-action="back"]');
  await back.click();
  await expect(body).toBeVisible();

  // The shift note's own id is a link (a shift-note id, not a record id): it resolves
  // to this tab's own `shift-note:<id>` view (KYO-231 has no by-id query, so it shows
  // the latest and names what was asked for -- see this file's `arg.startsWith` case).
  const notes = body.locator('[data-section="shift-notes"]');
  const noteIdLink = notes.locator('[data-id-link^="FIX-S"]').first();
  await expect(noteIdLink).toBeVisible();
  const noteId = (await noteIdLink.getAttribute("data-id-link")) ?? "";
  await noteIdLink.click();
  await expect(strip).toContainText(`Opened ${noteId} from kyosei://FIX/${noteId}`);
  await expect(
    page.locator(
      `[data-region="tab-body"][data-tab-kind="views"][data-tab-arg="shift-note:${noteId}"]`,
    ),
  ).toHaveCount(1);

  await back.click();
  await expect(body).toBeVisible();

  // And an id INSIDE the note's own prose -- not the note's own id -- is a link too.
  const inProseLink = notes.locator('[data-id-link="FIX-1"]').first();
  await expect(inProseLink).toBeVisible();
  await inProseLink.click();
  await expect(strip).toContainText("Opened FIX-1 from kyosei://FIX/FIX-1");
});

test("a criterion id embedded in a ledger command is a link, and resolves to the record at that anchor", async () => {
  await run(["brief", "--session-start"]);
  await run(["verify", "FIX-1.c1", "--verdict", "met", "--method", "machine-checked"]);
  await run(["brief", "--session-start"]);

  const page = await launch();
  const briefs = await rowsFor(page, "notes");
  await briefs.filter({ hasText: "Latest brief" }).first().click();
  const body = page.locator(
    '[data-region="tab-body"][data-tab-kind="views"][data-tab-arg="brief"]',
  );
  await expect(body).toHaveAttribute("data-state", "ready");

  const criterionLink = body.locator('[data-id-link="FIX-1.c1"]').first();
  await expect(criterionLink).toBeVisible();
  await criterionLink.click();
  // The anchor: KYO-235's own job is naming it; scrolling the record page to it is
  // KYO-233's (`shell.ts`'s comment on `onDelivery`).
  await expect(page.locator('[data-region="link-strip"]')).toContainText(
    "Opened FIX-1 at criterion FIX-1.c1, from kyosei://FIX/FIX-1.c1",
  );
  await expect(
    page.locator('[data-region="tab-body"][data-tab-kind="record"][data-tab-arg="FIX-1"]'),
  ).toHaveCount(1);
});

// ============================================== KYO-290: the records filter's readiness
//
// The open-work filter listed records that were built but not verified, or had any
// criterion outstanding, and nothing narrowed the list to dispatchable work alone.
// This is that filter, against the real box in the real app.
//
// The oracle is `kyo next --json` itself rather than a list written out here, because
// the criterion is a claim about agreement between two surfaces: "the list holds
// exactly the records in `kyo next --json`'s `ready` and nothing else". A check that
// re-derived the expected set from statuses and criteria would be a third answer, and
// would pass on the day the app and the CLI drifted together away from it.

test("the records filter's Readiness group keeps exactly `kyo next`'s dispatchable work", async () => {
  // Four records that put every class this filter has to tell apart into the store.
  // FIX-1 (gated) and FIX-2 (done) are already here and are neither dispatchable nor
  // anything else the board lists.
  //
  // The touches are declared and disjoint on purpose: with FIX-6's dispatch running,
  // a candidate that declared none would be `scope-unknown` rather than `ready`
  // (`commands.touch_standing` -- an undeclared scope is unknown, not empty), and this
  // check would then be asserting over an empty ready list.
  const criteria = proseFile("ready-crit.txt", "the thing works\n");
  await run(["new", "plan", "--title", "A plan with two members"]);
  await run([
    "new",
    "bug",
    "--title",
    "Ready to hand out",
    "--criteria-file",
    criteria,
    "--touches-file",
    proseFile("touch-a.txt", "+src/a.py\n"),
  ]);
  await run(["new", "bug", "--title", "Finished, nobody moved it", "--criteria-file", criteria]);
  await run([
    "new",
    "bug",
    "--title",
    "Already being worked",
    "--criteria-file",
    criteria,
    "--touches-file",
    proseFile("touch-b.txt", "+src/b.py\n"),
  ]);
  await run(["edit", "FIX-4", "--plan", "FIX-3"]);
  await run(["edit", "FIX-5", "--plan", "FIX-3"]);
  // FIX-5's only criterion is met and nobody moved it: KYO-87's class, and the exact
  // thing that kept turning up under "open work".
  await run(["verify", "FIX-5.c1", "--verdict", "met", "--method", "machine-checked"]);
  // FIX-6 is already being worked, which is not the same as being available.
  await run(["dispatch", "FIX-6"]);

  const board = JSON.parse((await run(["next", "--json"])).stdout) as {
    ready: { id: string }[];
    settled_not_moved: { id: string }[];
  };
  const dispatchable = board.ready.map((row) => row.id).sort();
  expect(dispatchable, "the fixture offers no dispatchable record").toEqual(["FIX-4"]);
  expect(board.settled_not_moved.map((row) => row.id)).toEqual(["FIX-5"]);

  const page = await launch();
  const rows = await rowsFor(page, "records");
  const shown = async (): Promise<string[]> =>
    (
      await page
        .locator('[data-region="sidebar-row"]')
        .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-row-arg") ?? ""))
    ).sort();
  await expect(rows).toHaveCount(6);

  await page.locator('[data-region="filter-button"]').click();
  const box = page.locator('[data-region="sidebar-box"]');
  const readiness = box.locator('[data-region="filter-group"][data-filter="readiness"]');
  await expect(readiness).toHaveCount(1);
  // The box says nothing about board classes: the chip carries the plain word.
  await expect(readiness.locator('[data-filter-value="ready"]')).toHaveText("dispatchable");
  // `kyo next` was read, so the group offers chips rather than an explanation.
  await expect(box.locator('[data-region="filter-unavailable"]')).toHaveCount(0);

  await readiness.locator('[data-filter-value="ready"]').click();
  await expect.poll(shown).toEqual(dispatchable);
  // Said the other way, which is the half KYO-290 was filed about: the
  // finished-but-unmoved record, the one already being worked, the gated one and the
  // plan are all gone, and the criterion names each of them.
  for (const absent of ["FIX-1", "FIX-2", "FIX-3", "FIX-5", "FIX-6"]) {
    await expect(rows.filter({ hasText: absent }), absent).toHaveCount(0);
  }
  await expect(page.locator('[data-region="filter-button"]')).toHaveAttribute(
    "data-filtering",
    "true",
  );

  // It composes with the groups already in the box, as an AND and not an either:
  // nothing here is both dispatchable and `done`.
  await box
    .locator('[data-region="filter-group"][data-filter="status"] [data-filter-value="done"]')
    .click();
  await expect(rows).toHaveCount(0);
  // And Clear filters clears it with the rest, from the same one control.
  await page.locator('[data-region="filter-clear"]').click();
  await expect(rows).toHaveCount(6);
  await expect(page.locator('[data-region="filter-button"]')).toHaveAttribute(
    "data-filtering",
    "false",
  );

  // The other classes are values of the same group, so "then why is that one not in
  // here?" is one click away -- and `settled-not-moved` stays its own answer rather
  // than a flavour of dispatchable (KYO-87).
  await readiness.locator('[data-filter-value="settled-not-moved"]').click();
  await expect.poll(shown).toEqual(["FIX-5"]);
  await page.locator('[data-region="filter-clear"]').click();

  // KYO-280: the filter narrows THIS LIST and nothing else. With only the dispatchable
  // record in the sidebar, the plan it belongs to still lists both its members with
  // their standing pills filled -- those are the record's own facts, read from
  // `kyo show --json`, and a filter that reached them would be emptying a record page
  // because of a choice made in a sidebar.
  await readiness.locator('[data-filter-value="ready"]').click();
  await expect.poll(shown).toEqual(dispatchable);
  await rows.filter({ hasText: "FIX-4" }).first().click();
  const member = page.locator(
    '[data-region="tab-body"][data-tab-kind="record"][data-tab-arg="FIX-4"]',
  );
  await expect(member).toHaveAttribute("data-state", "ready", { timeout: 60_000 });
  await member.locator('[data-link-written="FIX-3"]').first().click();
  const plan = page.locator(
    '[data-region="tab-body"][data-tab-kind="record"][data-tab-arg="FIX-3"]',
  );
  await expect(plan).toHaveAttribute("data-state", "ready", { timeout: 60_000 });
  const members = plan.locator('[data-section="members"]');
  await expect(members).toHaveAttribute("data-count", "2");
  await expect(members.locator('.tag[data-tone="open"]')).toHaveCount(2);
  await expect(members.locator('.tag[data-tone="open"]').first()).toHaveText("open");
  // The sidebar is still filtered while that page shows both: one is a list, the
  // other is a record.
  await expect(shown()).resolves.toEqual(dispatchable);
});

test("with `kyo next` unreadable the Readiness group says so, and the filter stops narrowing", async () => {
  // KYO-290.c2, and the reason it is a criterion: with the board unread every record
  // falls to `none`, so a box that still drew its chips over a filter already set to
  // "dispatchable" would show an EMPTY list -- which a reader reads as "there is no
  // dispatchable work" rather than as "nobody could ask".
  //
  // Two launches, because the interesting state is a filter the reader set while the
  // board was readable and a registry that stopped being readable afterwards. Since
  // KYO-459 the records list is `kyo list` through kyo serve rather than a direct read
  // of the database, so a broken `kinds.yml` blinds the list as well as the board: the
  // "board blind, list whole" half of KYO-290.c2 is held Electron-free, by `a board that
  // cannot be read leaves the list whole` in `seams.spec.ts`, and what a launch can show
  // is that neither half turns into an empty list -- the list says the CLI's sentence.
  let page = await launch();
  const rows = await rowsFor(page, "records");
  await page.locator('[data-region="filter-button"]').click();
  // From a known state: this profile is the one the test above filtered.
  await page.locator('[data-region="filter-clear"]').click();
  await expect(rows).toHaveCount(6);
  await page
    .locator('[data-region="filter-group"][data-filter="readiness"] [data-filter-value="ready"]')
    .click();
  await expect(rows).toHaveCount(1);
  await page.waitForTimeout(800); // the layout save is debounced
  if (app !== null) await closeReadyApp(app);
  app = null;

  const kept = readFileSync(join(fixture.root, "kyosei", "kinds.yml"), "utf8");
  writeStoreFile(fixture, "kinds.yml", "kinds: [\n");
  try {
    // The CLI refuses outright, which is the state the app has to render.
    await expect(kyo(fixture.resolution, ["next", "--json"], fixture.root)).rejects.toThrow();

    page = await launch();
    const sidebar = page.locator('[data-region="sidebar"]');
    if ((await sidebar.getAttribute("data-list")) !== "records") {
      await page.locator('[data-rail-item="records"]').click();
    }
    await expect(page.locator('[data-region="sidebar-loading"]')).toHaveCount(0, {
      timeout: 60_000,
    });
    // Not an empty list: the CLI's own sentence, naming what it could not read.
    await expect(page.locator('[data-region="sidebar-row"]')).toHaveCount(0);
    const problem = page.locator('[data-region="sidebar-problem"]');
    await expect(problem.first()).toContainText("kinds.yml");
  } finally {
    writeStoreFile(fixture, "kinds.yml", kept);
  }
});
