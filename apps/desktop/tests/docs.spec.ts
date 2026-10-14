// The docs tab (KYO-235.c3), against the real app: v2's docs mode ported to the v3
// shell's `{kind: "views", arg: "doc:<path>"}` (`shell/sidebar.ts`'s "Docs" list already
// opens a document row this way -- see the note at the top of
// `src/renderer/tabs/views/index.ts` for why the rendering lives there rather than in a
// registered `docs` tab kind). Recovered from v2's `tests/docs.spec.ts` (KYO-139) and
// rewritten on this suite's v3 launch pattern; the list below says which of the old
// checks came back.
//
// CAME BACK, against the `doc:` view this record owns:
//   KYO-139.c2   a section's `governs:` targets show, and whether each resolves
//   KYO-139.c3   the four staleness states, and `cannot-tell` visibly not `current`
//   KYO-139.c4   a mermaid diagram renders as a diagram; an unreadable dialect shows
//                its source and names the dialect; the flowchart parser is all-or-
//                nothing (unit-level, ported verbatim)
//
// DID NOT COME BACK, because the surface they were about is the shell's sidebar
// (`shell/sidebar.ts`'s `renderDocs`, KYO-232) and not this record's touches:
//   KYO-139.c1   "docs mode renders the project's documents rather than a placeholder"
//                -- the DOCUMENT LIST is the sidebar's "Docs" list, built and tested
//                under KYO-232; this file does not re-test the sidebar for its own sake
//                (the same call `views.spec.ts` makes about the records sidebar).
//   KYO-139.c5   "the left pane keeps the reader's place across a rebuild" -- there is
//                no left pane in v3; the analogous surface is the sidebar's document
//                list, again the shell's.
//   KYO-139.c6   "a project that declares no documents says so" -- `sidebar.ts`'s
//                `renderDocs` already branches on `tree.state === "no-documents"` and
//                shows `tree.says` (read directly in that file); this record adds
//                nothing there to test.
// The two bridge-shape checks ("no method on the bridge changes a document", "docs mode
// refuses the one `kyo docs` action that writes") are `src/main/docs.ts`'s, which this
// dispatch touches but did not need to change -- the v3 read model already carries them
// forward unchanged from v2, and they are not re-asserted here for the same reason.

import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { _electron, type ElectronApplication, expect, type Page, test } from "@playwright/test";
import { TMUX_ENV } from "../src/main/terminal";
import { parseFlowchart } from "../src/renderer/tabs/docs/mermaid";
import {
  closeReadyApp,
  type Fixture,
  MACOS_NO_RESTORE,
  makeFixture,
  markReadyToClose,
  removeFixture,
  suiteEnv,
  windowShowArg,
  writeStoreFile,
} from "./fixture";

const APP_ROOT = join(__dirname, "..");

function envFor(fixture: Fixture): Record<string, string> {
  const env = suiteEnv();
  env.KYOSEI_PROJECT_ROOT = fixture.root;
  env.KYOSEI_HOME = fixture.home;
  env[TMUX_ENV] = "off";
  env.KYOSEI_LOGIN_SHELL = "off";
  return env;
}

let app: ElectronApplication | null = null;
let profile: string | null = null;
let fixture: Fixture | null = null;

/**
 * Each test gets its OWN fixture and its own Chromium profile (not shared, unlike
 * `views.spec.ts`'s serial store): layout memory (KYO-232.c3) persists per profile per
 * display signature, and this file opens a different document in nearly every test, so
 * a shared profile would let one test's restored layout leak the previous test's open
 * tab into the next.
 */
async function launch(built: Fixture): Promise<Page> {
  fixture = built;
  profile = mkdtempSync(join(tmpdir(), "kyosei docs profile "));
  app = await _electron.launch({
    args: [APP_ROOT, `--user-data-dir=${profile}`, ...MACOS_NO_RESTORE, windowShowArg()],
    env: envFor(built),
  });
  const page = await app.firstWindow();
  await page.waitForSelector('body[data-ready="true"]');
  markReadyToClose(app);
  return page;
}

test.afterEach(async () => {
  if (app !== null) {
    await closeReadyApp(app);
    app = null;
  }
  if (profile !== null) {
    rmSync(profile, { recursive: true, force: true });
    profile = null;
  }
  if (fixture !== null) {
    removeFixture(fixture);
    fixture = null;
  }
});

// --------------------------------------------------------------- the fixtures

function write(root: string, rel: string, body: string): void {
  const path = join(root, rel);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, body, "utf8");
}

function git(root: string, args: string[], env: Record<string, string> = {}): void {
  // `suiteEnv` rather than `process.env` (KYO-254), and for the scan as much as for
  // git: no spec that launches the app may copy the whole environment, whatever it is
  // copying it for -- see "no spec that launches the app copies the whole environment".
  execFileSync("git", args, { cwd: root, stdio: "ignore", env: suiteEnv(env) });
}

/**
 * Commit at an explicit committer date (ported from v2's `tests/docs.spec.ts`).
 *
 * `git_queries.last_commit_at` reads `%cI`, which has ONE-SECOND granularity, and
 * `governs_staleness` asks whether a governed path's date is strictly greater than the
 * document's. Two commits in the same wall-clock second are therefore equal, and a
 * fixture that meant to be "stale" reads "current" intermittently. The dates are stated
 * rather than raced for.
 */
function commit(root: string, message: string, when: string): void {
  git(root, ["add", "-A"]);
  git(root, ["commit", "-m", message, "--allow-empty"], {
    GIT_AUTHOR_DATE: when,
    GIT_COMMITTER_DATE: when,
  });
}

const FIRST_COMMIT = "2026-01-01T00:00:00+00:00";
const SECOND_COMMIT = "2026-06-01T00:00:00+00:00";

/**
 * A project whose documents sit in the four staleness states, one each, plus one with a
 * resolving and a broken `governs:` target beside each other -- ported from
 * `governedFixture` in v2's `tests/docs.spec.ts`. See that file's own comment,
 * carried here: the commit sequence IS the fixture, and each step is load-bearing.
 */
async function governedFixture(): Promise<Fixture> {
  const fixture = await makeFixture({ records: 1 });
  const root = fixture.root;
  writeStoreFile(
    fixture,
    "documents.yml",
    "documents:\n  design:\n    summary: The fixture's governed documents.\n    globs:\n      - docs/**/*.md\n",
  );
  write(root, "src/settled.py", "def settled():\n    return 1\n");
  write(root, "src/moved.py", "def moved():\n    return 1\n");
  write(root, "src/present.py", "def present():\n    return 1\n");
  write(root, "docs/current.md", "# Current\n\n## Settled\n\ngoverns: src/settled.py\n\nProse.\n");
  write(root, "docs/stale.md", "# Stale\n\n## Moving\n\ngoverns: src/moved.py\n\nProse.\n");
  write(
    root,
    "docs/cannot.md",
    "# Cannot tell\n\n## Undated\n\ngoverns: src/untracked.py\n\nProse.\n",
  );
  write(root, "docs/ungoverned.md", "# Ungoverned\n\n## Prose\n\nNo claim about any code.\n");
  write(
    root,
    "docs/broken.md",
    "# Broken\n\n## Claims\n\ngoverns: src/present.py, src/vanished.py\n\nProse.\n",
  );
  commit(root, "documents and the code they govern", FIRST_COMMIT);

  write(root, "src/moved.py", "def moved():\n    return 2\n");
  commit(root, "the code moves out from under stale.md", SECOND_COMMIT);

  write(root, "src/untracked.py", "def untracked():\n    return 1\n");
  return fixture;
}

/** Open the docs sidebar list and wait for it to settle -- the shell's own surface. */
async function docsList(page: Page) {
  const sidebar = page.locator('[data-region="sidebar"]');
  if ((await sidebar.getAttribute("data-list")) !== "docs") {
    await page.locator('[data-rail-item="docs"]').click();
  }
  await expect(sidebar).toHaveAttribute("data-list", "docs");
  await expect(page.locator('[data-region="sidebar-loading"]')).toHaveCount(0, {
    timeout: 30_000,
  });
  return page.locator('[data-region="sidebar-row"]');
}

/**
 * Each fixture document's first heading, which is what `sidebar.ts`'s `renderDocs` row
 * actually shows (`doc.title ?? doc.name`) -- not the filename, so a row is found by it.
 */
async function openDoc(page: Page, path: string): Promise<void> {
  await docsList(page);
  // Each row carries its own open target as `data-row-arg` (`sidebar.ts`'s `row()`),
  // so a doc is found by exactly what it opens rather than by its rendered label --
  // which is the document's own first HEADING (`doc.title ?? doc.name`), not its
  // filename, and can coincide with another row's staleness badge text (`current`) on
  // a case-insensitive substring match.
  await page.locator(`[data-region="sidebar-row"][data-row-arg="doc:${path}"]`).first().click();
  await expect(
    page.locator(
      `[data-region="tab-body"][data-tab-kind="views"][data-tab-arg="doc:${path}"]:not([hidden])`,
    ),
  ).toHaveAttribute("data-state", "ready");
}

// ------------------------------------------------------------------- KYO-139.c2

test("a section's governs: targets show, and whether each still resolves", async () => {
  const fixture = await governedFixture();
  const page = await launch(fixture);
  await openDoc(page, "docs/broken.md");

  const body = page.locator(
    '[data-region="tab-body"][data-tab-kind="views"][data-tab-arg="doc:docs/broken.md"]:not([hidden])',
  );

  // The banner carries both targets, each marked with the CLI's own verdict.
  const banner = body.locator('[data-region="doc-banner-governs"]');
  await expect(banner.locator('[data-target="src/present.py"]')).toHaveAttribute(
    "data-resolves",
    "true",
  );
  await expect(banner.locator('[data-target="src/vanished.py"]')).toHaveAttribute(
    "data-resolves",
    "false",
  );

  // And the rendered body's own inline `governs:` metadata (KYO-139's Layer 3 line,
  // ported verbatim in `tabs/docs/markdown.ts`) carries the same verdict, attributed to
  // the section that declared it -- there is exactly one such section in this document.
  const inline = body.locator('[data-region="doc-metadata"][data-key="governs"]');
  await expect(inline).toHaveCount(1);
  await expect(inline.locator('[data-meta-value="src/present.py"]')).toHaveAttribute(
    "data-resolves",
    "true",
  );
  await expect(inline.locator('[data-meta-value="src/vanished.py"]')).toHaveAttribute(
    "data-resolves",
    "false",
  );
});

// ------------------------------------------------------------------- KYO-139.c3

test("the four staleness states are four, and cannot-tell is visibly not current", async () => {
  const fixture = await governedFixture();
  const page = await launch(fixture);

  const stateOf = async (doc: string): Promise<string> => {
    await openDoc(page, doc);
    return (
      (await page
        .locator(
          `[data-region="tab-body"][data-tab-arg="doc:${doc}"]:not([hidden]) [data-region="doc-staleness"]`,
        )
        .getAttribute("data-staleness")) ?? ""
    );
  };

  expect(await stateOf("docs/current.md")).toBe("current");
  expect(await stateOf("docs/stale.md")).toBe("stale");
  expect(await stateOf("docs/cannot.md")).toBe("cannot-tell");
  expect(await stateOf("docs/ungoverned.md")).toBe("ungoverned");

  // `answered` agrees with `docs_check.staleness_answered`: current and stale are
  // decided, cannot-tell and ungoverned are not -- the distinction KYO-139.c3 exists to
  // keep visible rather than have every non-"stale" state read as a clean bill.
  const answeredOf = async (doc: string): Promise<string> =>
    (await page
      .locator(
        `[data-region="tab-body"][data-tab-arg="doc:${doc}"]:not([hidden]) [data-region="doc-banner"]`,
      )
      .getAttribute("data-answered")) ?? "";
  await openDoc(page, "docs/current.md");
  expect(await answeredOf("docs/current.md")).toBe("true");
  await openDoc(page, "docs/stale.md");
  expect(await answeredOf("docs/stale.md")).toBe("true");
  await openDoc(page, "docs/cannot.md");
  expect(await answeredOf("docs/cannot.md")).toBe("false");
  await openDoc(page, "docs/ungoverned.md");
  expect(await answeredOf("docs/ungoverned.md")).toBe("false");

  // The CLI's own sentence reaches the banner in every state, `current` included --
  // never withheld because the state looked clean.
  await openDoc(page, "docs/cannot.md");
  const says = page.locator(
    '[data-region="tab-body"][data-tab-arg="doc:docs/cannot.md"]:not([hidden]) .doc-banner-says',
  );
  await expect(says).toContainText("src/untracked.py");
  await openDoc(page, "docs/current.md");
  await expect(
    page.locator(
      '[data-region="tab-body"][data-tab-arg="doc:docs/current.md"]:not([hidden]) .doc-banner-says',
    ),
  ).not.toHaveText("");
});

// ------------------------------------------------------------------- KYO-139.c4

test("a mermaid diagram renders as a diagram, not as its source", async () => {
  const fixture = await governedFixture();
  write(
    fixture.root,
    "docs/architecture.md",
    [
      "# Architecture",
      "",
      "## The shape",
      "",
      "governs: src/present.py",
      "",
      "```mermaid",
      "flowchart LR",
      "  R[Renderer] -->|invoke| M[Main]",
      "  M --> K[(kyo)]",
      "  M --> S[Store]",
      "```",
      "",
      "Prose after the diagram.",
    ].join("\n"),
  );
  commit(fixture.root, "an architecture document with a diagram", SECOND_COMMIT);

  const page = await launch(fixture);
  await openDoc(page, "docs/architecture.md");
  const body = page.locator(
    '[data-region="tab-body"][data-tab-arg="doc:docs/architecture.md"]:not([hidden])',
  );

  const diagram = body.locator('[data-region="doc-diagram"]');
  await expect(diagram).toHaveAttribute("data-diagram", "drawn");
  await expect(diagram.locator("svg.mmd")).toBeVisible();
  // The renderer's own sheet arrived with it (KYO-253): `docs/` is not a tab kind, so
  // `renderDocument` installs its rules itself, once, through the one helper.
  await expect(page.locator('style[data-styles="docs"]')).toHaveCount(1);

  const labels = await diagram
    .locator("text.mmd-label")
    .evaluateAll((nodes) => nodes.map((node) => node.textContent).sort());
  expect(labels).toEqual(["Main", "Renderer", "Store", "kyo"]);
  await expect(diagram.locator(".md-pre")).toHaveCount(0);
  await expect(body.locator('[data-region="doc-body"]')).not.toContainText("flowchart LR");
  await expect(diagram.locator("text.mmd-edge-label")).toHaveText("invoke");
  await expect(body.locator('[data-region="doc-body"]')).toContainText("Prose after the diagram.");
});

test("a diagram this build cannot draw shows its source and says which dialect", async () => {
  const fixture = await governedFixture();
  write(
    fixture.root,
    "docs/sequence.md",
    [
      "# Sequence",
      "",
      "## A conversation",
      "",
      "```mermaid",
      "sequenceDiagram",
      "  Renderer->>Main: invoke",
      "  Main-->>Renderer: reply",
      "```",
    ].join("\n"),
  );
  commit(fixture.root, "a dialect this build does not draw", SECOND_COMMIT);

  const page = await launch(fixture);
  await openDoc(page, "docs/sequence.md");
  const body = page.locator(
    '[data-region="tab-body"][data-tab-arg="doc:docs/sequence.md"]:not([hidden])',
  );

  const diagram = body.locator('[data-region="doc-diagram"]');
  await expect(diagram).toHaveAttribute("data-diagram", "source");
  await expect(diagram).toHaveAttribute("data-diagram-kind", "sequenceDiagram");
  await expect(diagram.locator("svg")).toHaveCount(0);
  await expect(diagram.locator(".md-diagram-note")).toContainText("flowcharts only");
  await expect(diagram.locator(".md-pre")).toContainText("Renderer->>Main: invoke");
});

test("the flowchart parser refuses a whole diagram rather than dropping an edge", () => {
  // Unit-level, ported verbatim (KYO-139): the tempting design is to skip unreadable
  // lines and draw the rest, which produces a diagram missing an edge -- a false
  // statement about the architecture that looks exactly like a true one.
  const drawn = parseFlowchart("flowchart TD\n  A[Start] --> B{Choice}\n  B -->|yes| C[Done]\n");
  expect(drawn).not.toBeNull();
  expect(drawn?.nodes.map((node) => node.id)).toEqual(["A", "B", "C"]);
  expect(drawn?.nodes.find((node) => node.id === "B")?.shape).toBe("diamond");
  expect(drawn?.edges).toHaveLength(2);
  expect(drawn?.edges[1]?.label).toBe("yes");

  expect(parseFlowchart("flowchart TD\n  subgraph one\n  A --> B\n  end\n")).toBeNull();
  expect(parseFlowchart("sequenceDiagram\n  A->>B: hi\n")).toBeNull();
});

// --------------------------------------------------------- the markdown body itself

test("headings, lists and code render as themselves, not as one monospace block", async () => {
  const fixture = await governedFixture();
  write(
    fixture.root,
    "docs/shapes.md",
    [
      "# Shapes",
      "",
      "## A list",
      "",
      "- one",
      "- two",
      "",
      "A paragraph with `inline code`.",
      "",
      "```text",
      "plain fenced code",
      "```",
    ].join("\n"),
  );
  commit(fixture.root, "a document exercising the markdown subset", SECOND_COMMIT);

  const page = await launch(fixture);
  await openDoc(page, "docs/shapes.md");
  const body = page.locator(
    '[data-region="tab-body"][data-tab-arg="doc:docs/shapes.md"]:not([hidden])',
  );
  const doc = body.locator('[data-region="doc-body"]');

  await expect(doc.locator("h1.md-h1")).toHaveText("Shapes");
  await expect(doc.locator("h2.md-h2")).toHaveText("A list");
  await expect(doc.locator("ul.md-list li")).toHaveCount(2);
  await expect(doc.locator("code.md-code")).toHaveText("inline code");
  await expect(doc.locator('[data-region="doc-code"][data-language="text"]')).toContainText(
    "plain fenced code",
  );
});

// ------------------------------------------------------------------- KYO-250

test("the docs list sorts and filters, by staleness among others", async () => {
  const fixture = await governedFixture();
  const page = await launch(fixture);
  await docsList(page);
  const rows = page.locator('[data-region="sidebar-row"][data-row-kind="views"]');
  const all = await rows.count();
  expect(all).toBeGreaterThan(1);
  await page.locator('[data-region="filter-button"]').click();
  await page
    .locator('[data-region="filter-group"][data-filter="staleness"] [data-filter-value="stale"]')
    .click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toHaveAttribute("data-row-arg", "doc:docs/stale.md");
  await page.locator('[data-region="filter-clear"]').click();
  await expect(rows).toHaveCount(all);
  await page.locator('[data-region="sort-button"]').click();
  await expect(page.locator('[data-region="sort-field"] option[value="modified"]')).toHaveCount(1);
});
