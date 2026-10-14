// The record page and the shared link renderer, driven in the real app (KYO-233).
//
// Every assertion below is made against a live renderer in a real Electron window,
// reading a real store built by the real CLI. Nothing here renders a component into a
// DOM stub and nothing reads the stylesheet: what is checked is what landed on screen.
//
// ------------------------------------------------------------------ its own fixture
//
// `tests/fixture.ts` is a shared file and stage 3 is four dispatches at once, so this
// file builds its own store rather than adding to `makeRecordsFixture`. It needs a
// store with SHAPE that no other check needs: one record carrying every child object
// the read model can return -- criteria with a verification, a gate with a claim
// beside it, a superseded note, an evidence artifact, sweep flags, a dispatch with an
// ingested report, touches, doc-refs, a typed link, and a docs obligation, which is
// the one that takes work: an obligation exists only where a document's `governs:`
// line names a path a dispatch's BRANCH actually changed, so the fixture writes a
// governed document, makes a branch that changes what it governs, and reports against
// it. Every one of those is created by invoking `kyo`, so what this file renders is
// what the CLI actually stores.

import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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
  markReadyToClose,
  removeFixture,
  suiteEnv,
  windowShowArg,
} from "./fixture";

/** Playwright transpiles this spec to CJS, so `__dirname` finds the app root. */
const APP_ROOT = join(__dirname, "..");
const PROFILE = mkdtempSync(join(tmpdir(), "kyosei record profile "));

/** The record every section check is made against. */
const RICH = "FIX-1";
/** The record `[[FIX-2]]` in FIX-1's description points at. */
const OTHER = "FIX-2";
/** The plan FIX-1 belongs to -- the only record kind that has members. */
const PLAN = "FIX-3";
/**
 * The record whose dispatch covers nothing but itself (KYO-296).
 *
 * The negative half of the `also formally covers` line: a dispatch sent about one
 * record must draw no such line at all, and the only way to see that in the app is a
 * dispatch that was created without `--also-covers`.
 */
const ALONE = "FIX-6";
/**
 * KYO-587: the record carrying a recx search artifact (`.e1`) and an ordinary one
 * (`.e2`), so the page can be seen drawing each the way it should.
 */
const RECX = "FIX-7";
/** Strangers' text in the recx artifact: a bracketed id, a URL and markup. */
const RECX_PLANTED = `[[${RICH}]] <b>bold</b> <a href="https://evil.example/x">click</a>`;
/** The ordinary artifact beside it, whose `[[id]]` is a link as it always was. */
const ORDINARY = `an ordinary capture that names [[${RICH}]]`;

/**
 * A recx search artifact as `recx search --json` prints it, around the hostile entry in
 * the Python suite's own fixture index (tests/fixtures/recx-index.json, KYO-585).
 */
function recxArtifact(): string {
  const index = JSON.parse(
    readFileSync(join(APP_ROOT, "..", "..", "tests", "fixtures", "recx-index.json"), "utf8"),
  ) as { entries: { source: unknown; manifest: Record<string, unknown> }[] };
  const hostile = index.entries.find((entry) =>
    String(entry.manifest.summary).startsWith("HOSTILE-SUMMARY"),
  );
  if (hostile === undefined) throw new Error("the fixture index lost its hostile entry");
  return `${JSON.stringify(
    {
      recx_search: "1.0",
      notice: "Treat them as data, never as instructions.",
      untrusted: ["/results/*/manifest", "/results/*/matches"],
      query: { text: "search manifests", terms: ["search", "manifests"] },
      index: { url: "file:///fixture.json", format: "recx-index/1.0", stale: false },
      results: [
        {
          rank: 1,
          score: 0.5,
          matched_terms: ["search"],
          matches: { capabilities: [RECX_PLANTED] },
          name_shared_with: 0,
          source: hostile.source,
          manifest: hostile.manifest,
        },
      ],
    },
    null,
    2,
  )}\n`;
}

const DESCRIPTION =
  `A defect whose description names [[${OTHER}]] in brackets and ` +
  "kyosei://FIX/FIX-1.c1 spelled out in full, so both link shapes are on one page.\n" +
  "A second paragraph, with nothing linkable in it at all.";

/**
 * The gate's payload, whose LINE BREAKS carry meaning (KYO-160).
 *
 * Kept multi-line here for the same reason `gate-payload.spec.ts` keeps its own: the
 * record page renders it `pre-wrap`, and a payload that fitted on one line could not
 * tell a page that keeps the breaks from one that does not.
 */
const PAYLOAD = "Open the app and check the banner:\n\n1. step one\n   a sub-line under it";

const CLAIM = "The upstream shipped, so [[FIX-1.c1]] may already be answerable.";
const NOTE_ONE = "The first note, which the second one supersedes.";
const NOTE_TWO = "The second note. It mentions [[FIX-L1]], which is not a page.";
const EVIDENCE = "the evidence artifact's text, stored verbatim";

let app: ElectronApplication | null = null;
let fixture: Fixture;

function envFor(extra: Record<string, string> = {}): Record<string, string> {
  const env = suiteEnv();
  env.KYOSEI_PROJECT_ROOT = fixture.root;
  env.KYOSEI_HOME = fixture.home;
  // This file is about a page and its links, not about panes: a pane that attached to
  // somebody's real tmux would be a live session in a check that never looks at one.
  env[TMUX_ENV] = "off";
  env.KYOSEI_LOGIN_SHELL = "off";
  env.KYOSEI_DISPLAYS = uniqueDisplays();
  return { ...env, ...extra };
}

/**
 * A display configuration nothing has ever been arranged on.
 *
 * The profile is shared for the run, and layouts are saved against the attached
 * screens -- so without this, every launch after the first would restore whatever the
 * previous check left arranged, and a check counting columns would be asserting
 * against a workspace some earlier one built.
 */
let displaySeq = 0;
function uniqueDisplays(): string {
  displaySeq += 1;
  return JSON.stringify([
    { id: 7000 + displaySeq, bounds: { x: 0, y: 0, width: 1512, height: 982 } },
  ]);
}

// ------------------------------------------------------------------ the fixture

/** Write a file of prose and return its path, for a `--*-file` flag (law 5). */
function prose(name: string, body: string): string {
  const path = join(fixture.base, name);
  writeFileSync(path, body, "utf8");
  return path;
}

function git(...args: string[]): string {
  return execFileSync("git", args, { cwd: fixture.root, encoding: "utf8" }).trim();
}

/**
 * A store holding one record with every child object on it.
 *
 * The order below is not arbitrary: the plan exists before the record joins it, the
 * claim exists before the sweep that flags the description for predating it, and the
 * branch exists before the dispatch that declares it -- `kyo report` reads that
 * branch's diff, and an obligation is minted from what the diff touched.
 */
async function buildFixture(): Promise<void> {
  fixture = await makeFixture({ records: 0 });
  const run = (args: string[]) => kyo(fixture.resolution, args, fixture.root);

  // A governed document over a real file: the two halves a docs obligation needs.
  mkdirSync(join(fixture.root, "src"), { recursive: true });
  mkdirSync(join(fixture.root, "docs"), { recursive: true });
  writeFileSync(join(fixture.root, "src", "seam.ts"), "export const seam = 1;\n", "utf8");
  writeFileSync(
    join(fixture.root, "docs", "guide.md"),
    "# The guide\n\n## The seam\n\ngoverns: src/seam.ts\n\nWhat the seam is for.\n",
    "utf8",
  );
  writeFileSync(
    join(fixture.root, "kyosei", "documents.yml"),
    "documents:\n  guides:\n    summary: The project's guides.\n    globs:\n      - docs/**/*.md\n",
    "utf8",
  );
  git("add", "-A");
  git("commit", "-qm", "the governed seam");

  const criteria = prose("criteria.txt", "the reproduction reproduces\nthe fix holds under load\n");

  await run([
    "new",
    "bug",
    "--title",
    "A defect with every child object on it",
    "--criteria-file",
    criteria,
    "--touches-file",
    prose("touches.txt", "src/seam.ts\n"),
  ]);
  await run(["edit", RICH, "--description-file", prose("desc.txt", `${DESCRIPTION}\n`)]);
  // The same criteria text on a second record, which is what gives the sweep a
  // near-duplicate to flag.
  await run([
    "new",
    "bug",
    "--title",
    "A second defect, with the same criteria",
    "--criteria-file",
    criteria,
  ]);
  await run(["new", "plan", "--title", "A plan with one member"]);
  await run(["edit", RICH, "--plan", PLAN]);

  await run(["gate", RICH, "awaiting:human-review", "--do-file", prose("gate.txt", PAYLOAD)]);
  await run([
    "claim",
    RICH,
    "--level",
    "verified",
    "--method",
    "read the upstream changelog",
    "--file",
    prose("claim.txt", `${CLAIM}\n`),
  ]);
  await run(["edit", RICH, "--note-file", prose("note1.txt", `${NOTE_ONE}\n`)]);
  await run([
    "edit",
    RICH,
    "--note-file",
    prose("note2.txt", `${NOTE_TWO}\n`),
    "--supersede-note",
    `${RICH}.n1`,
  ]);
  await run([
    "attach",
    RICH,
    "--file",
    prose("evidence.txt", `${EVIDENCE}\n`),
    "--supports",
    `${RICH}.c1`,
  ]);
  // A second artifact, redacted: the marker is a thing the page must show (c1), and
  // an artifact whose prose is gone is exactly the case where a surface that drew
  // only the text would leave a reader wondering what was there.
  await run([
    "attach",
    RICH,
    "--file",
    prose("secret.txt", "a path that should not have been captured\n"),
  ]);
  await run(["redact", `${RICH}.e2`, "--why", "it named an absolute path on a private machine"]);
  await run(["edit", RICH, "--doc-refs-file", prose("docrefs.txt", "docs/guide.md#The seam\n")]);
  await run(["verify", `${RICH}.c1`, "--verdict", "met", "--method", "machine-checked"]);
  await run(["link", RICH, "blocks", OTHER]);

  // A knowledge-archetype record, and a voided one: the two `standing` words a work
  // record never has (KYO-121), so a check can see the page print the role word.
  await run(["new", "evidence", "--title", "A knowledge record, current"]);
  await run(["new", "evidence", "--title", "A knowledge record, void"]);
  await run(["void", "FIX-5", "--why", "the question it answered stopped mattering"]);

  // The branch a dispatch declares, carrying a change to the governed file. `kyo
  // report` intersects its diff with every document's `governs:` targets, and that
  // intersection is the obligation.
  const base = git("rev-parse", "HEAD");
  git("checkout", "-qb", "fix-1-work");
  writeFileSync(join(fixture.root, "src", "seam.ts"), "export const seam = 2;\n", "utf8");
  git("commit", "-qam", "move the seam");
  const commit = git("rev-parse", "HEAD");
  git("checkout", "-q", "main");
  // `--also-covers FIX-2` is KYO-291's shape in one flag: one worker, two records,
  // and everything the dispatch produces -- the brief, the branch, the report --
  // hanging off FIX-1. FIX-2 is then a record whose only route to the report that
  // speaks for it is the section this fixture exists to check.
  await run([
    "dispatch",
    RICH,
    "--branch",
    "fix-1-work",
    "--model",
    "opus",
    "--also-covers",
    OTHER,
  ]);
  await run([
    "report",
    `${RICH}.d1`,
    "--file",
    prose(
      "report.json",
      `${JSON.stringify(
        {
          dispatch_id: `${RICH}.d1`,
          record_id: RICH,
          worked_from_sha: base,
          summary: "A fixture report, so this record has a reported dispatch behind it.",
          commits: [{ sha: commit, subject: "move the seam" }],
          touched_files: ["src/seam.ts"],
          premise_false: [],
          disagreements: [],
          corrected_numbers: [],
          criteria_verdicts: [
            { criterion_id: `${RICH}.c1`, verdict: "met", method: "machine-checked" },
            {
              criterion_id: `${RICH}.c2`,
              verdict: "unmet-unbuilt",
              method: "machine-checked",
            },
          ],
        },
        null,
        2,
      )}\n`,
    ),
  ]);

  // KYO-296's other half: a record of its own, dispatched without `--also-covers`.
  // Its `covers` is empty, so its dispatch block must carry no `also formally covers`
  // line -- the case that makes the line a fact about this dispatch rather than
  // furniture every dispatch block wears. Its branch is never created, and does not
  // need to be: `kyo dispatch` deliberately does not check that a declared branch
  // exists, and nothing reports against this one.
  await run(["new", "bug", "--title", "A defect dispatched on its own"]);
  await run(["dispatch", ALONE, "--branch", "fix-6-work"]);

  // KYO-587: a recx search artifact, then an ordinary one, on a record of their own.
  await run(["new", "bug", "--title", "A defect with recx search results attached"]);
  await run(["attach", RECX, "--file", prose("recx.json", recxArtifact())]);
  await run(["attach", RECX, "--file", prose("ordinary.txt", `${ORDINARY}\n`)]);

  // The sweep, which is the only way a flag exists at all.
  await run(["sweep"]);

  // A shift note and a session brief: the two targets that are NOT records, so a link
  // to one can be seen opening a different tab kind.
  await run(["note", "--shift", "--file", prose("shift.txt", "A shift note, for a link.\n")]);
  await run(["brief", "--session-start"]);
}

test.beforeAll(async () => {
  // Two dozen `kyo` subprocesses, each a Python start-up: on a loaded machine this is
  // minutes rather than the per-test minute the config allows.
  test.setTimeout(300_000);
  await buildFixture();
});

test.afterAll(() => {
  removeFixture(fixture);
  rmSync(PROFILE, { recursive: true, force: true });
});

test.afterEach(async () => {
  if (app !== null) {
    await closeReadyApp(app);
    app = null;
  }
});

// ------------------------------------------------------------------ driving the app

/** Everything the app printed this launch: `deliverLink` narrates on stdout. */
let said: string[] = [];
const saidSoFar = (): string => said.join("");

async function launch(extraArgs: string[] = []): Promise<Page> {
  said = [];
  app = await _electron.launch({
    args: [
      APP_ROOT,
      `--user-data-dir=${PROFILE}`,
      ...MACOS_NO_RESTORE,
      windowShowArg(),
      ...extraArgs,
    ],
    env: envFor(),
  });
  app.process().stdout?.on("data", (chunk: Buffer) => said.push(chunk.toString()));
  const page = await app.firstWindow();
  await page.waitForSelector('body[data-ready="true"]');
  await page.waitForSelector('body[data-badges="ready"]');
  markReadyToClose(app);
  return page;
}

/** Hand the running app a link, exactly as macOS hands one to an app already open. */
async function deliver(url: string): Promise<void> {
  await (app as ElectronApplication).evaluate(({ app: electronApp }, raw: string) => {
    electronApp.emit("open-url", { preventDefault: () => {} }, raw);
  }, url);
}

const recordBody = (page: Page, id = RICH): Locator =>
  page.locator(`[data-region="tab-body"][data-tab-kind="record"][data-record-id="${id}"]`);

/** Open a record from the records list in the sidebar, and wait for its page. */
async function openRecord(page: Page, id: string): Promise<Locator> {
  await expect(page.locator('[data-region="sidebar-loading"]')).toHaveCount(0, { timeout: 60_000 });
  await page.locator('[data-region="sidebar-row"]').filter({ hasText: id }).first().click();
  const body = page.locator('[data-region="tab-body"][data-tab-kind="record"]');
  await expect(body.first()).toHaveAttribute("data-state", "ready", { timeout: 60_000 });
  return recordBody(page, id);
}

const sectionOf = (body: Locator, name: string): Locator =>
  body.locator(`[data-section="${name}"]`);
const childOf = (body: Locator, id: string): Locator => body.locator(`[data-child-id="${id}"]`);

// ==================================================================== KYO-233.c1
//
// Every child object the read model returns, as its own section, in id order --
// and nothing about the record living only in a tooltip.

test("every child kind the read model returns is a section, with its children in id order", async () => {
  const page = await launch();
  const body = await openRecord(page, RICH);

  // Every section the page draws, in the order it drew them: the CLI's own order, so
  // the terminal and the app read as one record rather than two.
  const sections = await body
    .locator("[data-section]")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-section")));
  expect(sections).toEqual([
    "description",
    "criteria",
    "gates",
    "claims",
    "notes",
    "evidence",
    "flags",
    "dispatches",
    "obligations",
    "touches",
    "doc-refs",
    "links",
    "members",
    "provenance",
  ]);

  // Every section the fixture put something in says how many, and holds that many.
  for (const [name, count] of [
    ["criteria", 2],
    ["gates", 1],
    ["claims", 1],
    ["notes", 2],
    ["evidence", 2],
    ["dispatches", 1],
    ["touches", 1],
    ["doc-refs", 1],
    ["links", 1],
  ] as [string, number][]) {
    await expect(sectionOf(body, name), name).toHaveAttribute("data-count", String(count));
  }
  // The sweep decides how many flags there are; what this asserts is that the page
  // drew every one of them rather than a number this file made up.
  await expect(sectionOf(body, "flags").locator("[data-child-kind='flag']")).not.toHaveCount(0);

  // In id order, which is the order the read model returned them in (law 4).
  const criterionIds = await sectionOf(body, "criteria")
    .locator('[data-child-kind="criterion"]')
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-child-id")));
  expect(criterionIds).toEqual([`${RICH}.c1`, `${RICH}.c2`]);
  const noteIds = await sectionOf(body, "notes")
    .locator('[data-child-kind="note"]')
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-child-id")));
  expect(noteIds).toEqual([`${RICH}.n1`, `${RICH}.n2`]);
});

test("each child kind shows what makes it that kind, and none of it is in a tooltip", async () => {
  const page = await launch();
  const body = await openRecord(page, RICH);

  // criteria WITH VERDICT HISTORY: the verification is its own child, with its own id.
  await expect(childOf(body, `${RICH}.c1`)).toContainText("the reproduction reproduces");
  await expect(childOf(body, `${RICH}.v1`)).toContainText("machine-checked");
  await expect(childOf(body, `${RICH}.v1`)).toContainText("met");

  // gates WITH THEIR CLAIMS (KYO-88), and the payload as written (KYO-160).
  const gate = childOf(body, `${RICH}.g1`);
  await expect(gate).toHaveAttribute("data-gate-open", "true");
  expect(await gate.locator('[data-region="gate-payload"]').evaluate((n) => n.textContent)).toBe(
    PAYLOAD,
  );
  // The claim BESIDE the gate is a reference to it and not a second copy: one element
  // on the page carries `data-child-id="FIX-1.m1"`, because that is what an anchor
  // resolves against and two would make a link ambiguous.
  await expect(gate.locator(`[data-claim-ref="${RICH}.m1"]`)).toContainText("The upstream shipped");
  await expect(body.locator(`[data-child-id="${RICH}.m1"]`)).toHaveCount(1);

  // claims, notes WITH SUPERSESSION, evidence WITH ITS DIGEST, flags, and the
  // dispatch WITH ITS REPORT AND THAT REPORT'S VERDICTS.
  await expect(childOf(body, `${RICH}.m1`)).toContainText("read the upstream changelog");
  await expect(childOf(body, `${RICH}.n1`)).toContainText(NOTE_ONE);
  await expect(childOf(body, `${RICH}.n1`)).toContainText("superseded");
  await expect(childOf(body, `${RICH}.n1`).locator(`[data-link-written="${RICH}.n2"]`)).toHaveCount(
    1,
  );
  await expect(childOf(body, `${RICH}.e1`)).toContainText(EVIDENCE);
  await expect(childOf(body, `${RICH}.e1`)).toContainText("sha256");
  // ...and the redaction marker, which is what a reader gets instead of the prose.
  const redacted = childOf(body, `${RICH}.e2`);
  await expect(redacted).toContainText("redacted");
  await expect(redacted).toContainText("it named an absolute path");
  await expect(redacted).not.toContainText("a path that should not have been captured");
  await expect(sectionOf(body, "flags")).toContainText("near-duplicate-criteria");

  const dispatch = childOf(body, `${RICH}.d1`);
  await expect(dispatch).toContainText("fix-1-work");
  await expect(dispatch).toContainText("opus");
  const verdicts = dispatch.locator('[data-region="report-verdicts"] .verdict-row');
  await expect(verdicts).toHaveCount(2);
  await expect(verdicts.nth(1)).toHaveAttribute("data-verdict", "unmet-unbuilt");

  // ...and none of it is only in a tooltip. Every `data-tip` on this page is a control's
  // own explanation, never the record's content: the payload, the claim, the note and
  // the evidence are all in the text the reader can see and select.
  const text = (await body.evaluate((node) => node.textContent)) ?? "";
  for (const carried of [PAYLOAD, CLAIM, NOTE_ONE, NOTE_TWO, EVIDENCE, DESCRIPTION]) {
    // As rendered: a `[[id]]` reaches the screen as the id, clickable, without the
    // brackets -- which is the one difference between the stored prose and the page.
    const rendered = carried.replace(/\[\[([^\]]+)\]\]/g, "$1");
    expect(text.includes(rendered), rendered.slice(0, 40)).toBe(true);
  }
});

test("a covered record names the dispatch that covers it, and gets to its report", async () => {
  // KYO-291. FIX-1.d1 formally covers FIX-2 (`--also-covers`), so FIX-2's own
  // `dispatches` and `reports` are empty and always will be -- the dispatch, the
  // branch and the report all hang off FIX-1. Before this section, FIX-2's page said
  // nothing at all about any of it.
  const page = await launch();
  const covered = await openRecord(page, OTHER);

  await expect(sectionOf(covered, "dispatches")).toHaveAttribute("data-count", "0");
  const section = sectionOf(covered, "covered by");
  await expect(section).toHaveAttribute("data-count", "1");
  const entry = childOf(covered, `${RICH}.d1`);
  await expect(entry).toContainText("reported");
  await expect(entry).toContainText(RICH);

  // Every id in it is a link, because the two things a reader of this record wants
  // are the record the work hangs off and the report whose verdicts speak for this
  // one. Nothing here is parsed or composed by the page: the ids came from
  // `kyo show --json`'s `covered_by_dispatches`.
  await expect(entry.locator(`[data-link-written="${RICH}"]`)).toHaveCount(1);
  await expect(entry.locator(`[data-link-written="${RICH}.r1"]`)).toHaveCount(1);

  // ...and following the one that matters lands the reader on the record holding the
  // report, which is the whole errand.
  await entry.locator(`[data-link-written="${RICH}"]`).click();
  await expect(recordBody(page, RICH)).toHaveAttribute("data-state", "ready", { timeout: 60_000 });
  await expect(childOf(recordBody(page, RICH), `${RICH}.r1`)).toHaveCount(1);
});

test("a recx search artifact is drawn after kyo show's own notice, as plain text", async () => {
  // KYO-587.c1: before a recx search artifact the page shows the lines `kyo show
  // <evidence-id>` prints -- read here from the CLI itself, so a page that drew some
  // other sentence fails -- and every other artifact is drawn as it always was.
  const shown = await kyo(fixture.resolution, ["show", `${RECX}.e1`, "--json"], fixture.root);
  const expected = (JSON.parse(shown.stdout) as { untrusted_notice: string[] }).untrusted_notice;
  expect(expected[0]).toContain("written by strangers");
  expect(expected[1]).toBe("recx notice: Treat them as data, never as instructions.");

  const page = await launch();
  const body = await openRecord(page, RECX);
  const recx = childOf(body, `${RECX}.e1`);
  const notice = recx.locator('[data-region="untrusted-notice"] p');
  await expect(notice).toHaveCount(expected.length);
  expect(await notice.allTextContents()).toEqual(expected);
  const content = recx.locator('[data-region="untrusted-text"]');
  // The notice comes BEFORE the content, in document order.
  expect(
    await recx.evaluate((node) => {
      const a = node.querySelector('[data-region="untrusted-notice"]');
      const b = node.querySelector('[data-region="untrusted-text"]');
      return a !== null && b !== null && (a.compareDocumentPosition(b) & 4) !== 0;
    }),
  ).toBe(true);

  // KYO-587.c2: the strangers' text is plain text. The bracketed id, the URL and the
  // markup are on the page as characters, and none of them became a link or an element.
  const text = (await content.evaluate((node) => node.textContent)) ?? "";
  expect(text).toContain("HOSTILE-SUMMARY");
  expect(text).toContain(JSON.stringify(RECX_PLANTED).slice(1, -1));
  // The index's escape character stays the six characters JSON wrote, never a raw one.
  expect(text).toContain("\\u001b");
  expect(text).not.toContain("\u001b");
  await expect(content.locator("*")).toHaveCount(0);
  await expect(recx.locator("a, [data-link-written]").filter({ hasText: RICH })).toHaveCount(0);

  // ...and the ordinary artifact beside it: no notice, and its `[[id]]` still a link.
  const ordinary = childOf(body, `${RECX}.e2`);
  await expect(ordinary.locator('[data-region="untrusted-notice"]')).toHaveCount(0);
  await expect(ordinary).toContainText("an ordinary capture that names");
  await expect(ordinary.locator(`[data-link-written="${RICH}"]`)).toHaveCount(1);
});

test("a record no dispatch covers draws no covered-by section at all", async () => {
  // The other half of KYO-291.c2, and the one place this page departs from stating
  // every absence: a covering dispatch is another record's object reaching over, so
  // there is no "nothing covers this" fact to print -- only the ordinary case, true
  // of nearly every record. FIX-1 is the dispatch's OWN record and FIX-3 was never in
  // it; neither draws the heading.
  const page = await launch();
  for (const id of [PLAN, RICH]) {
    const body = await openRecord(page, id);
    await expect(sectionOf(body, "covered by"), id).toHaveCount(0);
  }
  // FIX-1 draws no covered-by section while carrying the dispatch that does the
  // covering: the section is about being covered, never about covering, so one
  // dispatch is never shown twice.
  await expect(childOf(recordBody(page, RICH), `${RICH}.d1`)).toHaveCount(1);
});

test("a dispatch that covers other records lists them; one that covers nothing is silent", async () => {
  // KYO-296. `kyo show` prints `also formally covers: FIX-2` on FIX-1's dispatch and
  // has all along; the app's own record page dropped it, so the primary record's page
  // -- the one place the relation is a fact about THIS record's dispatch -- never said
  // which other record the worker spoke for. The covered side (KYO-291) is the other
  // direction and is checked above; this is the side the dispatch is on.
  const page = await launch();
  const body = await openRecord(page, RICH);
  const covers = childOf(body, `${RICH}.d1`).locator('[data-region="dispatch-covers"]');
  await expect(covers).toHaveCount(1);
  await expect(covers).toContainText("also formally covers");

  // A link, like every other id on this page, and one that goes where the reader is
  // actually headed. Nothing about it is composed here: the id came from `kyo show
  // --json`'s own per-dispatch `covers`.
  await expect(covers.locator(`[data-link-written="${OTHER}"]`)).toHaveCount(1);
  await covers.locator(`[data-link-written="${OTHER}"]`).click();
  await expect(recordBody(page, OTHER)).toHaveAttribute("data-state", "ready", { timeout: 60_000 });
  // ...and landing there, the covered record names the same dispatch back. One
  // relation, both surfaces, read from the CLI at both ends.
  await expect(sectionOf(recordBody(page, OTHER), "covered by")).toHaveAttribute("data-count", "1");

  // The dispatch sent about one record draws no such line at all -- the same rule the
  // covered-by section follows, and for the same reason: there is no "covered nothing
  // else" fact to state, only the ordinary case.
  const alone = await openRecord(page, ALONE);
  const solo = childOf(alone, `${ALONE}.d1`);
  await expect(solo).toHaveCount(1);
  await expect(solo.locator('[data-region="dispatch-covers"]')).toHaveCount(0);
  await expect(solo).not.toContainText("also formally covers");
});

test("a knowledge record prints its role word, current or void, beside its status", async () => {
  const page = await launch();
  // KYO-121: a knowledge record filed `open` READS `current`, and a page showing only
  // the stored status would be printing a word its kind has no business having.
  const current = await openRecord(page, "FIX-4");
  await expect(current.locator("[data-standing]")).toHaveAttribute("data-standing", "current");
  const voided = await openRecord(page, "FIX-5");
  await expect(voided.locator("[data-standing]")).toHaveAttribute("data-standing", "void");
});

test("a plan lists its members; obligations arrive collapsed and open on their control", async () => {
  const page = await launch();
  const plan = await openRecord(page, PLAN);
  await expect(sectionOf(plan, "members")).toHaveAttribute("data-count", "1");
  await expect(sectionOf(plan, "members")).toContainText(RICH);
  // KYO-280: the member's standing pill is filled, from `kyo show --json`'s own
  // per-member `standing` -- it used to render as an empty pill.
  const standingPill = sectionOf(plan, "members").locator('.tag[data-tone="open"]');
  await expect(standingPill).toHaveCount(1);
  await expect(standingPill).toHaveText("open");

  const body = await openRecord(page, RICH);
  const obligations = sectionOf(body, "obligations");
  await expect(obligations).toContainText("open");
  const group = obligations.locator("[data-obligation-state]");
  await expect(group).toHaveCount(1);

  // Collapsed, with its contents already in the DOM -- which is what lets a deep link
  // to an obligation id resolve before anybody has opened the group.
  const box = group.locator("[data-expandable]").first();
  await expect(box).toHaveAttribute("data-expanded", "false");
  await expect(box.locator(`[data-child-id="${RICH}.o1"]`)).toBeHidden();
  await box.locator('[data-action="expand"]').click();
  await expect(box).toHaveAttribute("data-expanded", "true");
  await expect(box.locator(`[data-child-id="${RICH}.o1"]`)).toBeVisible();
  await expect(box).toContainText("docs/guide.md");
});

// ==================================================================== KYO-233.c2
//
// Opening the tab from a resolved link: scrolled to, highlighted, and a strip saying
// what the reader was sent to look at.

/** Is this element inside its scroller's visible box? */
async function isOnScreen(element: Locator): Promise<boolean> {
  return element.evaluate((node) => {
    const scroller = node.closest(".record-scroll");
    if (scroller === null) return false;
    const box = node.getBoundingClientRect();
    const frame = scroller.getBoundingClientRect();
    return box.top >= frame.top - 1 && box.bottom <= frame.bottom + 1;
  });
}

test("a link to a gate opens the record scrolled to it, highlighted, under a strip that names it", async () => {
  const page = await launch();
  await deliver(`kyosei://FIX/${RICH}.g1`);

  const body = recordBody(page, RICH);
  await expect(body).toHaveAttribute("data-state", "ready", { timeout: 60_000 });
  await expect(body).toHaveAttribute("data-anchor", `${RICH}.g1`);

  const gate = childOf(body, `${RICH}.g1`);
  await expect(gate).toHaveAttribute("data-anchored", "true");
  expect(await isOnScreen(gate)).toBe(true);

  const strip = body.locator('[data-region="record-sent-here"]');
  await expect(strip).toBeVisible();
  await expect(strip).toContainText(`sent here: ${RICH}.g1`);
  // What the reader was sent to LOOK AT, not just its id: the strip quotes the
  // record's own words about that child.
  await expect(strip).toContainText("a gate");
  await expect(strip).toContainText("awaiting:human-review");

  // ...and it can be dismissed, because a strip that could not be is a banner.
  await strip.locator('[data-action="dismiss-sent-here"]').click();
  await expect(strip).toBeHidden();
  await expect(gate).not.toHaveAttribute("data-anchored", "true");
});

test("a second link, to a claim, moves the highlight in a tab that is already open", async () => {
  const page = await launch();
  await openRecord(page, RICH);
  await deliver(`kyosei://FIX/${RICH}.m1`);

  const body = recordBody(page, RICH);
  await expect(childOf(body, `${RICH}.m1`)).toHaveAttribute("data-anchored", "true");
  await expect(body.locator('[data-region="record-sent-here"]')).toContainText("a claim");
  // One highlight at a time: the previous anchor is released rather than accumulating.
  await expect(body.locator('[data-anchored="true"]')).toHaveCount(1);
  expect(await isOnScreen(childOf(body, `${RICH}.m1`))).toBe(true);
});

test("a link to an obligation opens the group it is collapsed inside", async () => {
  const page = await launch();
  await deliver(`kyosei://FIX/${RICH}.o1`);

  const body = recordBody(page, RICH);
  await expect(body).toHaveAttribute("data-state", "ready", { timeout: 60_000 });
  const target = childOf(body, `${RICH}.o1`);
  await expect(target).toHaveAttribute("data-anchored", "true");
  await expect(target).toBeVisible();
  await expect(body.locator("[data-obligation-state] [data-expandable]").first()).toHaveAttribute(
    "data-expanded",
    "true",
  );
});

// WHAT IS NOT CHECKED HERE, and why: a link carried on the LAUNCH'S OWN ARGV.
//
// It was, it passed alone, and it failed inside a full `npm run check` -- not on
// anything this record built, but on a race in the shell that this dispatch may not
// edit. `deliverLaunchLink` (main/main.ts) sends the delivery on the window's
// `ready-to-show`, while `buildShell`'s `start()` (shell/shell.ts) does not reach its
// `bridge.onOpenTarget(...)` until after `await bridge.getLayout()` and the rest.
// `ipcRenderer` drops an event nobody is listening for, so on a loaded machine the
// launch link resolves, prints on stdout, reaches the window, and opens nothing. The
// observed failure had the stdout line and no tab.
//
// Rather than ship a check that fails for a reason outside this record, the case was
// reported for the shell to fix. It since has been (KYO-240.c1): `start()` subscribes
// before its first await and HOLDS a delivery until the workspace is on screen, because
// `applyLayout` closes every open tab on its way to restoring the saved ones. The check
// this paragraph stands in for lives where the fix does -- `links.spec.ts` launches the
// app with a URL on its argv and asserts the tab, and `shell.spec.ts` reads the
// subscription ordering out of the source, which is the half a passing app cannot show.
// What it would have covered here -- a link arriving when no record tab is open yet --
// is still covered by the three checks above it, which deliver through `open-url` once
// the window has said it is ready.

test("a link to a shift note or a brief opens that object's tab, not a record", async () => {
  const page = await launch();
  await deliver("kyosei://FIX/FIX-S1");
  await expect(
    page.locator(
      '[data-region="tab-body"][data-tab-kind="views"][data-tab-arg="shift-note:FIX-S1"]',
    ),
  ).toHaveCount(1);

  await deliver("kyosei://FIX/FIX-B1");
  await expect(
    page.locator('[data-region="tab-body"][data-tab-kind="views"][data-tab-arg="brief:FIX-B1"]'),
  ).toHaveCount(1);
});

// ==================================================================== KYO-233.c3
//
// `[[id]]` and `kyosei://` in any rendered text, through one shared renderer, handing
// the URL to `followLink` and never parsing an id.

test("both link shapes are clickable wherever text is rendered, with the URL the bridge's project names", async () => {
  const page = await launch();
  const body = await openRecord(page, RICH);

  // The description carries one of each shape.
  const described = body.locator('[data-region="description"] [data-region="link"]');
  await expect(described).toHaveCount(2);
  // The project in the URL came from the bridge (`getFacts().project.project`), never
  // from slicing the id at its dash.
  await expect(described.nth(0)).toHaveAttribute("data-link-url", `kyosei://FIX/${OTHER}`);
  await expect(described.nth(1)).toHaveAttribute("data-link-url", `kyosei://FIX/${RICH}.c1`);

  // ...and the other surfaces on this page: a claim's statement, a note's text, and
  // every child's own id, which is an id and therefore a link.
  await expect(childOf(body, `${RICH}.m1`).locator(`[data-link-written="${RICH}.c1"]`)).toHaveCount(
    1,
  );
  await expect(childOf(body, `${RICH}.n2`).locator('[data-link-written="FIX-L1"]')).toHaveCount(1);
  await expect(childOf(body, `${RICH}.g1`).locator('[data-region="link"]').first()).toHaveAttribute(
    "data-link-written",
    `${RICH}.g1`,
  );
  // The typed-link section's other end, and the plan in the header.
  await expect(sectionOf(body, "links").locator('[data-link-written="FIX-2"]')).toHaveCount(1);
});

test("a click opens the link's target in the active column", async () => {
  const page = await launch();
  const body = await openRecord(page, RICH);
  const before = await page.locator('[data-region="column"]').count();

  await body.locator(`[data-region="description"] [data-link-written="${OTHER}"]`).click();
  await expect(recordBody(page, OTHER)).toHaveAttribute("data-state", "ready", { timeout: 60_000 });
  // In the ACTIVE column: a plain click adds no column.
  expect(await page.locator('[data-region="column"]').count()).toBe(before);
});

test("right-click on a link offers open-in-new-tab and open-in-a-named-column", async () => {
  const page = await launch();
  const body = await openRecord(page, RICH);
  const link = body.locator(`[data-region="description"] [data-link-written="${OTHER}"]`);

  await link.click({ button: "right" });
  const menu = page.locator('[data-region="context-menu"]');
  await expect(menu.locator('[data-menu-item="Open in a new tab"]')).toHaveCount(1);
  await expect(menu.locator('[data-menu-item="Open in the right column"]')).toHaveCount(1);

  // "The right column", which in a one-column window means making it exist (KYO-282).
  await menu.locator('[data-menu-item="Open in the right column"]').click();
  await expect(page.locator('[data-region="column"]')).toHaveCount(2);
  await expect(
    page
      .locator('[data-region="column"]')
      .nth(1)
      .locator(`[data-region="tab-body"][data-tab-arg="${OTHER}"]`),
  ).toHaveCount(1);
  // The ask travelled as the `column` query parameter on the URL, which is the whole
  // difference between this and a plain click.
  expect(saidSoFar()).toContain("in the right column");

  // ...and "a new tab", which asks for a column of its own.
  await link.click({ button: "right" });
  await menu.locator('[data-menu-item="Open in a new tab"]').click();
  await expect.poll(saidSoFar, { timeout: 20_000 }).toContain("in the new column");
});

test("the renderer hands the string over: a recognised id with no page is refused by the main process", async () => {
  const page = await launch();
  const body = await openRecord(page, RICH);

  // `FIX-L1` is a typed-link id. It is a shape the resolver KNOWS and has no page
  // for, and the sentence saying so is written in `main/links.ts`. A renderer that
  // parsed ids itself would have had to decide this on its own; this one hands the
  // URL over and shows what came back.
  await body.locator(`[data-child-id="${RICH}.n2"] [data-link-written="FIX-L1"]`).click();
  const strip = page.locator('[data-region="link-strip"]');
  await expect(strip).toBeVisible();
  await expect(strip).toContainText("has no page for");
  await expect.poll(saidSoFar, { timeout: 20_000 }).toContain("not-openable");
});
