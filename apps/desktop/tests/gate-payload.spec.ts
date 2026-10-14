// A multi-line gate payload keeps its line breaks in the app (KYO-160).
//
// A separate spec file from `smoke.spec.ts` deliberately, on the same reasoning
// `docs.spec.ts` states at the top of itself: KYO-161 holds that file for the
// duration of its own dispatch, and a criterion of this record is not worth a merge
// conflict in somebody else's. Nothing here duplicates it -- the gate-panel checks
// (gates lead the properties, claims render under the open gate) live there and are
// not repeated. This file asks one question those do not: whether the payload's
// newlines survive as far as the screen.
//
// ------------------------------------------------------------------ what it measures
//
// The obvious guard for a CSS rule is to search the stylesheet for it, and it is
// worthless: `white-space: pre-wrap` sits in `shell.css` reading perfectly while the
// element it names has been renamed, or while the payload has moved into a different
// element the rule never reaches. Nothing below reads the stylesheet. Every assertion
// is a measurement of where the glyphs actually landed, taken from the real app
// rendering a real store built by the real CLI:
//
//   * `renders taller than the same text with its breaks collapsed` -- lays the
//     payload out twice in the live pane, once as the app styles it and once with
//     `white-space: normal` forced on, and requires the first to be strictly taller.
//     Delete the rule and the two measurements become the same number.
//   * `a step's sub-line is indented under its step` -- compares the rendered x of an
//     indented sub-line against the x of the step above it. This is the one that
//     separates `pre-wrap` from `pre-line`, which is the choice KYO-160.c2 is about:
//     `pre-line` keeps the newline, collapses the leading spaces, and slides every
//     sub-line back to the margin.
//   * `the payload reaches the DOM unflattened` -- the layer underneath: no rule can
//     restore a break that was turned into a space before the browser saw it.

import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
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
import { runKyo } from "../src/main/kyo";
import { TMUX_ENV } from "../src/main/terminal";
import {
  closeReadyApp,
  type Fixture,
  MACOS_NO_RESTORE,
  makeRecordsFixture,
  markReadyToClose,
  removeFixture,
  suiteEnv,
  windowShowArg,
} from "./fixture";

const APP_ROOT = join(__dirname, "..");
const PROFILE = mkdtempSync(join(tmpdir(), "kyosei gate payload profile "));

/**
 * What a `ui-review` payload actually looks like, taken verbatim from KYO-148's own
 * test rather than invented here: a sentence, a blank line, numbered steps, and
 * sub-lines indented under the step they belong to.
 *
 * Three of its properties are load-bearing and none of them is decoration. The blank
 * line is what guarantees the height comparison has something to find. The
 * indentation is what the `pre-line` check reads. The long unspaced URLs are the
 * token `overflow-wrap: anywhere` exists for, and the reason the original incident
 * happened at all.
 */
const PROCEDURE = `Open the staging app and check the read-only banner in both modes:

1. http://localhost:5173/records?previewBanner=readonly
   the banner must say "read only" and the Save button must be absent
2. http://localhost:5173/ideas?previewBanner=readonly
   the same banner, and the composer must be disabled`;

function envFor(projectRoot: string, home: string): Record<string, string> {
  const env = suiteEnv();
  env.KYOSEI_PROJECT_ROOT = projectRoot;
  env.KYOSEI_HOME = home;
  env[TMUX_ENV] = "off";
  return env;
}

let app: ElectronApplication | null = null;
let fixture: Fixture | null = null;

/**
 * The record the payload is hung on.
 *
 * FIX-2 rather than FIX-1, which the shared fixture already gates: a second gate on
 * FIX-1 would make `[data-gate-open]` ambiguous, and FIX-2 carries none of its own.
 */
const GATED = "FIX-2";

/**
 * The suite's own records store, with `PROCEDURE` added as a gate on FIX-2.
 *
 * `makeRecordsFixture` rather than a store built from scratch here, because the
 * records tree needs the `scopes.yml` and `bases.yml` that helper writes -- without
 * them the folder tool settles with no rows and no record can be opened at all.
 * The gate is then added through `runKyo` against the real CLI, so what this file
 * renders is what `kyo gate --do-file` actually stored rather than a literal that
 * only ever existed in a test. `fixture.ts` itself is left alone: every call to
 * `makeRecordsFixture` builds a fresh temp store, so this addition is confined to
 * this file's own copy and no other spec sees a gate it did not ask for.
 */
async function gatedFixture(): Promise<Fixture> {
  const made = await makeRecordsFixture();
  const doFile = join(made.base, "gate-do.txt");
  writeFileSync(doFile, PROCEDURE, "utf8");
  const result = await runKyo(
    made.resolution,
    ["gate", GATED, "awaiting:human-review", "--do-file", doFile],
    { cwd: made.root, timeoutMs: 60_000 },
  );
  if (result.code !== 0) {
    throw new Error(`kyo gate exited ${result.code}: ${result.stderr || result.stdout}`);
  }
  return made;
}

/** The app, open on the gated record, with the gate's payload element located. */
async function payloadElement(): Promise<{ page: Page; detail: Locator }> {
  fixture = await gatedFixture();
  app = await _electron.launch({
    args: [APP_ROOT, `--user-data-dir=${PROFILE}`, ...MACOS_NO_RESTORE, windowShowArg()],
    env: envFor(fixture.root, fixture.home),
  });
  const page = await app.firstWindow();
  await page.waitForSelector('body[data-ready="true"]');
  markReadyToClose(app);

  // KYO-232: records mode is gone. The same record opens as a TAB, from the records
  // list in the sidebar -- which the shell shows by default on a display configuration
  // it has not seen before, and this profile is fresh per run.
  await page.waitForSelector('body[data-badges="ready"]');
  await page.locator('[data-region="sidebar-row"]').filter({ hasText: GATED }).first().click();
  const body = page.locator('[data-region="tab-body"][data-tab-kind="record"]');
  await expect(body).toHaveAttribute("data-state", "ready");

  const gate = body.locator('[data-gate-open="true"]');
  await expect(gate).toContainText("Open the staging app");

  // Located from the gate the payload is IN, and by the region it declares -- not by a
  // class name this file already believes in. Rename the element and this resolves to
  // nothing, which is the failure the record asked for.
  const detail = gate.locator('[data-region="gate-payload"]');
  await expect(detail).toHaveCount(1);
  return { page, detail };
}

test.afterEach(async () => {
  if (app !== null) {
    await closeReadyApp(app);
    app = null;
  }
  if (fixture !== null) {
    removeFixture(fixture);
    fixture = null;
  }
});

test.afterAll(() => {
  rmSync(PROFILE, { recursive: true, force: true });
});

// ------------------------------------------------------------------------ KYO-160.c1

test("the payload reaches the DOM with its newlines intact", async () => {
  const { detail } = await payloadElement();
  expect(await detail.evaluate((node) => node.textContent)).toBe(PROCEDURE);
});

test("the payload renders taller than the same text with its breaks collapsed", async () => {
  const { detail } = await payloadElement();

  const { styled, collapsed } = await detail.evaluate((node) => {
    const element = node as HTMLElement;
    const measured = element.getBoundingClientRect().height;
    // Force the pre-KYO-160 rendering on the very same element, in the very same
    // pane at the very same width, and measure again. Nothing about the comparison
    // depends on knowing what the rule is or what it is called.
    const before = element.style.whiteSpace;
    element.style.whiteSpace = "normal";
    const flattened = element.getBoundingClientRect().height;
    element.style.whiteSpace = before;
    return { styled: measured, collapsed: flattened };
  });

  expect(styled).toBeGreaterThan(collapsed);
});

// ------------------------------------------------------------------------ KYO-160.c2

test("a step's indented sub-line is rendered indented under its step", async () => {
  const { detail } = await payloadElement();

  // The rendered x of the first glyph of a step, and of the sub-line beneath it,
  // taken from real line boxes via a Range over the text node.
  const { step, subLine } = await detail.evaluate((node) => {
    const text = node.firstChild as Text;
    const body = text.data;
    const leftOf = (needle: string): number => {
      const at = body.indexOf(needle);
      if (at < 0) throw new Error(`not in the payload: ${needle}`);
      const range = document.createRange();
      range.setStart(text, at);
      range.setEnd(text, at + needle.length);
      return range.getBoundingClientRect().left;
    };
    return { step: leftOf("2. http"), subLine: leftOf("the same banner") };
  });

  // `pre-line` would put these at the same x; `normal` would not reliably start
  // either at a line box at all. Only a value that keeps leading spaces indents one
  // under the other.
  expect(subLine).toBeGreaterThan(step);
});
