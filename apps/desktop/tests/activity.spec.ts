// The activity tab, driven in the real app (KYO-236).
//
// This is the tab's OWN spec, not an addition to `shell.spec.ts` -- the four stage 3
// dispatches each own one tab directory and one spec file, and none of them may touch
// `tests/fixture.ts` (KYO-236's brief). So the fixture this file needs -- a running
// dispatch on a REAL worktree and branch, one reported-and-ingested dispatch, one
// terminated-no-report, and one whose worktree is gone -- is built here, from the
// exported pieces `tests/fixture.ts` already offers (`makeFixture`, `kyo`), the same
// way `tests/seams.spec.ts` builds `makeRecordsFixture` beside it rather than folding
// every store shape into one.
//
// KYO-236.c3 in particular asks for a worktree that GAINS a commit and a report file
// WHILE the check is running, so the poll (`main.ts`'s `ACTIVITY_POLL_MS`, 5s) has
// something real to notice -- not a fixture already in its end state.
//
// KYO-239: `kyo dispatch --worktree` does not store the path as given --
// `store_worktree_path`/`store_path` (src/kyosei/config.py, src/kyosei/contracts.py
// -- schema_version 9+) always store it as EITHER the KYO-70 worktree-home token or
// a path relative to the PROJECT ROOT -- and since KYO-459 `kyo activity` resolves
// either form (`ctx.resolve_worktree`) before it reads git there, on the machine that
// holds the project; the app renames its answer. So this fixture no longer needs to work around the gap: FIX-1
// below dispatches with no `--worktree` at all, letting `kyo dispatch --branch`
// derive one under the project's own worktree home, which is exactly what stores
// the KYO-70 token this check now proves the app resolves; FIX-4 keeps declaring an
// explicit `--worktree` outside that home, which stores the KYO-52 relative form.
// Between the two, every stored form `resolve_worktree_path` accepts is exercised
// against a real git worktree -- FIX-1's gains a commit and a report file while the
// check runs, the same way it always has.

import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron, type ElectronApplication, expect, type Page, test } from "@playwright/test";
import { dispatchEndOf, workerTokensOf } from "../src/main/activity";
import { TMUX_ENV } from "../src/main/terminal";
import {
  elapsedFor,
  endedAtMs,
  finishedWhen,
  formatDuration,
  roundedTokens,
  tokensLabel,
  tokensTip,
  whenTip,
} from "../src/renderer/shell/activity-format";
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

/** The worker's report file, as `kyo activity`'s git facts look for it (`cmd_worktrees`). */
const REPORT_FILE = ".kyosei-report.json";

/** Playwright transpiles this spec to CJS, so `__dirname` finds the app root. */
const APP_ROOT = join(__dirname, "..");

let app: ElectronApplication;

/** One Chromium profile for this file's one launch (KYO-116.d1). */
const PROFILE = mkdtempSync(join(tmpdir(), "kyosei activity profile "));

function envFor(fixture: Fixture): Record<string, string> {
  const env = suiteEnv();
  env.KYOSEI_PROJECT_ROOT = fixture.root;
  env.KYOSEI_HOME = fixture.home;
  env[TMUX_ENV] = "off";
  // A display configuration nothing has ever arranged a layout against, so this
  // launch opens on the shell's own default rather than restoring whatever some
  // other spec file's run last left arranged (the same seam `shell.spec.ts` uses).
  env.KYOSEI_DISPLAYS = JSON.stringify([
    { id: 424_242, bounds: { x: 0, y: 0, width: 1512, height: 982 } },
  ]);
  return env;
}

function launchArgs(): string[] {
  return [APP_ROOT, `--user-data-dir=${PROFILE}`, ...MACOS_NO_RESTORE, windowShowArg()];
}

async function readyWindow(page: Page): Promise<Page> {
  await page.waitForSelector('body[data-ready="true"]');
  return page;
}

/** Quitting mid-startup segfaults Electron 44; no app this file starts may close
 * until its window has rendered (KYO-118). */
async function launchReady(env: Record<string, string>): Promise<Page> {
  app = await _electron.launch({ args: launchArgs(), env });
  const page = await readyWindow(await app.firstWindow());
  markReadyToClose(app);
  return page;
}

async function shellReady(page: Page): Promise<Page> {
  await page.waitForSelector('body[data-facts="ready"]');
  await page.waitForSelector('body[data-badges="ready"]');
  return page;
}

function writeJson(path: string, value: unknown): void {
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function reportBodyFor(dispatchId: string, recordId: string, workedFromSha: string) {
  return {
    dispatch_id: dispatchId,
    record_id: recordId,
    worked_from_sha: workedFromSha,
    summary: `A fixture report for ${dispatchId}, so the activity tab has an ingested dispatch to render.`,
    commits: [],
    touched_files: [],
    premise_false: [],
    disagreements: [],
    corrected_numbers: [],
    criteria_verdicts: [
      { criterion_id: `${recordId}.c1`, verdict: "met", method: "machine-checked" },
    ],
  };
}

// ---------------------------------------------------------------------- KYO-284
//
// The token figure on an activity row is `kyo`'s answer, and `kyo` gets it from the
// transcript its stop-audit resolution matched to the dispatch (KYO-128/154/134).
// So a fixture that wants a row with a real number has to produce a real stop: a
// transcript shaped the way the harness writes one, and the hook run the way Claude
// Code runs it. Nothing below writes to the store directly -- the CLI's own
// resolution decides what the row says, which is the only thing worth checking.

/** Tokens per assistant message in the fixture transcripts, and their four names. */
const FIXTURE_USAGE = {
  input_tokens: 7,
  cache_creation_input_tokens: 1_100,
  cache_read_input_tokens: 90_000,
  output_tokens: 640,
};

/** The fixture's LATEST-TURN figure and what the row prints for it (KYO-292.c2):
 * all four counts of one message, cache reads included, and NOT a function of how
 * many turns the transcript holds -- which is the point. Every turn of a fixture
 * transcript carries the same usage, so this is the same number for a two-turn
 * transcript and a four-turn one, and a row that showed a running sum would differ
 * between them. The rounding rule stays in `roundedTokens`, which this calls. */
const LATEST_TURN_SUM =
  FIXTURE_USAGE.input_tokens +
  FIXTURE_USAGE.cache_creation_input_tokens +
  FIXTURE_USAGE.cache_read_input_tokens +
  FIXTURE_USAGE.output_tokens;
const latestTurnLabel = (): string => roundedTokens(LATEST_TURN_SUM);

/**
 * Write one subagent transcript and run the `SubagentStop` guard over it.
 *
 * The first user message carries a generated brief's own `- **Dispatch id**: <id>`
 * header, which is what `resolve_stop_target` prefers over a bare mention (KYO-201)
 * -- the same shape `kyo dispatch`'s briefs carry, so this fixture exercises the
 * real matching rather than a weaker one.
 */
function recordStopFor(fixture: Fixture, dispatchId: string, agentId: string, messages: number) {
  const sessionDir = join(fixture.base, "transcripts", dispatchId.replace(".", "-"));
  mkdirSync(join(sessionDir, "subagents"), { recursive: true });
  const parent = `${sessionDir}.jsonl`;
  writeFileSync(parent, "", "utf8");
  const lines = [
    JSON.stringify({
      type: "user",
      isSidechain: true,
      agentId,
      message: {
        role: "user",
        content: `# Dispatch brief\n\n- **Dispatch id**: ${dispatchId}\n\nDo the work.`,
      },
    }),
  ];
  for (let index = 0; index < messages; index += 1) {
    lines.push(
      JSON.stringify({
        type: "assistant",
        agentId,
        message: {
          role: "assistant",
          model: "claude-opus-5",
          content: [{ type: "text", text: `turn ${index}` }],
          usage: { ...FIXTURE_USAGE, service_tier: "standard" },
        },
      }),
    );
  }
  writeFileSync(
    join(sessionDir, "subagents", `agent-${agentId}.jsonl`),
    `${lines.join("\n")}\n`,
    "utf8",
  );
  const { program, baseArgs } = fixture.resolution.source;
  if (program === null) throw new Error("kyo: no runnable source for the stop fixture");
  // The guard blocks (exit 2) on a dirty worker tree, which is not this fixture's
  // subject: what is wanted is the RECORDED resolution, which is written either way.
  const result = spawnSync(program, [...baseArgs, "check-clean", "--subagent-stop"], {
    cwd: fixture.root,
    encoding: "utf8",
    input: JSON.stringify({
      session_id: dispatchId,
      transcript_path: parent,
      cwd: fixture.root,
      hook_event_name: "SubagentStop",
      stop_hook_active: false,
      agent_id: agentId,
    }),
  });
  expect(
    `${result.stdout ?? ""}${result.stderr ?? ""}`,
    `the stop for ${dispatchId} should have resolved to it`,
  ).toContain(dispatchId);
}

interface ActivityFixture extends Fixture {
  /** `FIX-1`'s real, linked worktree -- gains a commit and a report file mid-check. */
  runningWorktree: string;
  /** `FIX-4`'s worktree path, declared and then removed before the app ever reads it. */
  goneWorktree: string;
}

/**
 * The four dispatch shapes KYO-236.c3 asks a check to cover, each on its own record:
 *
 *   FIX-1  running, a REAL git worktree on a real branch (linked to the fixture's own
 *          repository, so `main` resolves and `mergedInto` is a real answer) -- and
 *          gated, so the "gated, with conditions" group has a row to show.
 *   FIX-2  dispatched, verified, reported and INGESTED (`kyo report`) -- and never
 *          moved off `open`, which is KYO-87's `finished-but-not-moved` by the store's
 *          own rule, not this tab's.
 *   FIX-3  dispatched and closed `--terminated-no-report`.
 *   FIX-4  dispatched against a real worktree that is then deleted out from under it --
 *          `directory-gone`, the ordinary end state of a finished dispatch's worktree.
 */
async function makeActivityFixture(): Promise<ActivityFixture> {
  const fixture = await makeFixture({ records: 0 });
  const run = (args: string[]) => kyo(fixture.resolution, args, fixture.root);
  const fileIn = (name: string, body: string): string => {
    const path = join(fixture.base, name);
    writeFileSync(path, body, "utf8");
    return path;
  };
  const git = (cwd: string, args: string[]) =>
    execFileSync("git", ["-C", cwd, ...args], { stdio: "ignore" });

  // Every record below declares its own disjoint touch, so `kyo dispatch` can compute
  // the touch-disjointness comparison (KYO-76) instead of refusing an unknown scope --
  // the same reason a real dispatch brief always carries one.
  const touchesFor = (name: string): string =>
    fileIn(`${name}-touches.txt`, `+activity-fixture/${name}.txt\n`);

  // FIX-1: running, on a real linked worktree, and gated. No `--worktree` is given,
  // so `kyo dispatch --branch` derives one under this project's own worktree home
  // (KYO-70) and stores the `<worktrees>/...` token for it -- the stored form this
  // check most wants to prove `main/activity.ts` resolves (KYO-239). `kyo dispatch`
  // creates the worktree's PARENT directory only (KYO-70.c5); the worktree itself is
  // still the controller's own `git worktree add`, same as ever.
  await run([
    "new",
    "bug",
    "--title",
    "A running dispatch to watch",
    "--touches-file",
    touchesFor("fix1"),
  ]);
  await run(["gate", "FIX-1", "awaiting:event", "--do", "the nightly build goes green"]);
  const fix1Dispatch = JSON.parse(
    // KYO-485: this fixture makes the worktree itself below, so the dispatch must not.
    (
      await run([
        "dispatch",
        "FIX-1",
        "--branch",
        "kyo-fix1-running",
        "--no-create-worktree",
        "--json",
      ])
    ).stdout,
  ) as { worktree: string; worktree_resolved: string };
  expect(fix1Dispatch.worktree.startsWith("<worktrees>/")).toBe(true);
  const runningWorktree = fix1Dispatch.worktree_resolved;
  git(fixture.root, ["worktree", "add", "-b", "kyo-fix1-running", runningWorktree]);

  // FIX-2: dispatched, verified, reported, ingested -- and left `open`.
  const criteria = fileIn("criteria.txt", "the reproduction reproduces\n");
  await run([
    "new",
    "bug",
    "--title",
    "A finished, ingested dispatch",
    "--criteria-file",
    criteria,
    "--touches-file",
    touchesFor("fix2"),
  ]);
  const head = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: fixture.root,
    encoding: "utf8",
  }).trim();
  // KYO-284.c2 needs ONE finished row that carries a run time AND +added −removed at
  // the same time, because the criterion is about their ORDER and a row missing
  // either would pass any assertion made about the one it has. So this dispatch gets
  // a real branch with a real commit on it, in a real linked worktree that STAYS on
  // disk -- which it has to, because a dispatch whose worktree is gone is not a
  // candidate the stop resolution can match a transcript to (KYO-201), and c2 also
  // wants a finished row showing a real token total rather than only the unknown.
  const fix2Tree = join(fixture.base, "fix2-work");
  git(fixture.root, ["worktree", "add", "-b", "kyo-fix2-done", fix2Tree]);
  writeFileSync(join(fix2Tree, "fix2-work.txt"), "one line of finished work\n", "utf8");
  git(fix2Tree, ["add", "-A"]);
  git(fix2Tree, ["commit", "-m", "fix2 work"]);
  await run(["dispatch", "FIX-2", "--worktree", fix2Tree, "--branch", "kyo-fix2-done"]);
  await run(["verify", "FIX-2.c1", "--verdict", "met", "--method", "machine-checked"]);
  const fix2Report = fileIn(
    "fix2-report.json",
    `${JSON.stringify(reportBodyFor("FIX-2.d1", "FIX-2", head), null, 2)}\n`,
  );
  await run(["report", "FIX-2.d1", "--file", fix2Report]);

  // FIX-3: dispatched, then closed with no report at all.
  await run([
    "new",
    "bug",
    "--title",
    "A dispatch that never reported",
    "--touches-file",
    touchesFor("fix3"),
  ]);
  await run(["dispatch", "FIX-3"]);
  await run([
    "dispatch",
    "close",
    "FIX-3.d1",
    "--terminated-no-report",
    "--why",
    "the worker's machine died mid-session",
  ]);

  // FIX-4: dispatched against a real worktree, which is then removed -- the ordinary
  // end state `kyo worktrees` leaves behind once a dispatch is done. `--worktree`
  // names a path OUTSIDE this project's worktree home, so `kyo dispatch` stores the
  // OTHER live form (KYO-52's project-root-relative path, a `..` chain here) rather
  // than the token FIX-1 exercises above -- between the two, this fixture covers
  // both stored forms `resolve_worktree_path` accepts, plus this dispatch's own
  // subject: a directory that is genuinely gone.
  await run([
    "new",
    "bug",
    "--title",
    "A dispatch whose worktree is gone",
    "--touches-file",
    touchesFor("fix4"),
  ]);
  const goneWorktree = join(fixture.base, "gone-worktree");
  git(fixture.root, ["worktree", "add", "-b", "kyo-fix4-gone", goneWorktree]);
  const fix4DispatchResult = await run([
    "dispatch",
    "FIX-4",
    "--worktree",
    goneWorktree,
    "--branch",
    "kyo-fix4-gone",
    "--json",
  ]);
  const fix4Dispatch = JSON.parse(fix4DispatchResult.stdout) as { worktree: string };
  expect(fix4Dispatch.worktree.startsWith("<worktrees>")).toBe(false);
  rmSync(goneWorktree, { recursive: true, force: true });

  // KYO-284: two dispatches that a `SubagentStop` was really resolved against, so
  // two rows carry a REAL token total -- the running one and the finished one, which
  // is c1 and c2. Nothing is hand-written into the store: the hook is run exactly as
  // Claude Code runs it, over a transcript shaped as the harness writes one, and the
  // CLI's own resolution decides which dispatch it belongs to and records the row.
  recordStopFor(fixture, "FIX-1.d1", "a1000000000000001", 4);
  recordStopFor(fixture, "FIX-2.d1", "a1000000000000002", 2);

  return { ...fixture, runningWorktree, goneWorktree };
}

let fixture: ActivityFixture;

test.beforeAll(async () => {
  fixture = await makeActivityFixture();
});

test.afterAll(() => {
  removeFixture(fixture);
  rmSync(PROFILE, { recursive: true, force: true });
});

test.afterEach(async () => {
  if (app !== undefined) await closeReadyApp(app);
});

// ==================================================================== c1, c2: the tab

const rail = (page: Page, id: string) => page.locator(`[data-rail-item="${id}"]`);

test("the activity list: running above the divider, finished below, two lines and the records, live", async () => {
  test.setTimeout(120_000);
  const page = await shellReady(await launchReady(envFor(fixture)));

  await rail(page, "activity").click();
  const list = page.locator('[data-region="sidebar"][data-list="activity"]');
  const block = (dispatchId: string) =>
    list.locator(`[data-region="activity-row"][data-dispatch-id="${dispatchId}"]`);
  // Generous: this first read runs `kyo`, four git reads and `kyo worktrees --json`.
  await expect(block("FIX-1.d1")).toBeVisible({ timeout: 30_000 });

  // The rail badge counts the running dispatches -- FIX-1 and FIX-4, which stays
  // `running` even though its worktree is gone (KYO-236.c2, now on the Activity item).
  await expect(page.locator('[data-badge-for="activity"]')).toHaveAttribute("data-count", "2", {
    timeout: 30_000,
  });

  // Running first, a divider, then finished -- each dispatch under its own heading.
  await expect(list.locator('[data-group="running"] [data-region="activity-row"]')).toHaveCount(2);
  await expect(list.locator('[data-region="activity-divider"]')).toHaveCount(1);
  await expect(list.locator('[data-group="finished"] [data-region="activity-row"]')).toHaveCount(2);
  await expect(list.locator('[data-group="running"] [data-dispatch-id="FIX-1.d1"]')).toHaveCount(1);
  await expect(list.locator('[data-group="finished"] [data-dispatch-id="FIX-2.d1"]')).toHaveCount(
    1,
  );

  // ---- FIX-1: running, on its own real worktree. Line one: the dispatch link, the
  // model, the elapsed time; line three: what the store can observe; then its record.
  const fix1 = block("FIX-1.d1");
  await expect(fix1).toHaveAttribute("data-dispatch-state", "running");
  await expect(fix1.locator('[data-link-id="FIX-1.d1"]')).toBeVisible();
  // KYO-284.c3: the running clock has no " so far" suffix any more -- it reads as a
  // duration and nothing else. Asserted as an absence, because "the suffix is gone"
  // is the criterion and a positive match on the duration would pass with it still there.
  await expect(fix1.locator('[data-region="dispatch-elapsed"]')).not.toContainText("so far");
  await expect(fix1.locator('[data-region="dispatch-elapsed"]')).toHaveText(/\d/);
  // KYO-284.c1: and it carries the token figures `kyo` summed from the transcript its
  // own stop resolution matched to this dispatch -- four assistant turns of the
  // fixture's usage, rounded for the row and exact on the tip.
  //
  // KYO-292.c2: TWO figures, each labelled. "in" is input plus cache creation -- what
  // this worker newly put in front of the model -- and "out" is output. The row used
  // to show output alone under the bare word "tokens", which is ambiguous: a token
  // figure has to say whether it counts output or input.
  await expect(fix1.locator('[data-region="dispatch-tokens"]')).toHaveText(
    `${latestTurnLabel()} tokens`,
  );
  await expect(fix1.locator('[data-region="dispatch-tokens"]')).toHaveAttribute(
    "data-tip",
    new RegExp(`${LATEST_TURN_SUM} tokens on claude-opus-5 in this worker's latest turn`),
  );
  // And what the worker used across all four of its turns stays on the tip beside it,
  // as a different quantity and said to be one -- 360k of cache reads here against a
  // 91k turn, which is the ratio that makes a running total unreadable as a headline.
  await expect(fix1.locator('[data-region="dispatch-tokens"]')).toHaveAttribute(
    "data-tip",
    new RegExp(`${4 * FIXTURE_USAGE.cache_read_input_tokens} cache read`),
  );
  // A running row keeps its run time on line one and does not repeat it on line two.
  await expect(fix1.locator('[data-region="dispatch-runtime"]')).toHaveCount(0);
  await expect(fix1.locator('[data-region="dispatch-doing"]')).toContainText(
    "no commits past base yet",
  );
  await expect(fix1.locator('[data-region="dispatch-doing"]')).toContainText("no report file yet");
  await expect(
    fix1.locator('[data-region="dispatch-records"] [data-row-arg="FIX-1"]'),
  ).toContainText("A running dispatch to watch");

  // ---- FIX-2: reported and ingested -- the report is a link, with its verdicts.
  const fix2 = block("FIX-2.d1");
  await expect(fix2.locator('[data-link-id="FIX-2.r1"]')).toBeVisible();
  await expect(fix2).toContainText("met 1");
  await expect(fix2.locator('[data-marker="finished-but-not-moved"]')).toHaveCount(1);
  // KYO-284.c2: a finished row carries a token figure on exactly the same terms --
  // and, under KYO-292.c2, the same figure as the running row above, because it is
  // one TURN's size and every turn in these fixtures carries the same usage. That
  // this row has two turns against the other's four is what the summed counts on the
  // tip still tell apart, and the tip is where that assertion is...
  await expect(fix2.locator('[data-region="dispatch-tokens"]')).toHaveText(
    `${latestTurnLabel()} tokens`,
  );
  await expect(fix2.locator('[data-region="dispatch-tokens"]')).toHaveAttribute(
    "data-tip",
    new RegExp(`across all 2 of its messages: ${2 * FIXTURE_USAGE.input_tokens} input`),
  );
  // ...and its run time on line TWO, which is where "below the date it ran, to left
  // of lines added/deleted" puts it. This dispatch closed seconds ago, so line one
  // still shows the duration (c3's younger case) and line two says "ran ..." -- the
  // date takes line one over once the row is a day old, which `finishedWhen` decides
  // and the helper check below pins at both sides of the boundary.
  await expect(fix2.locator('[data-region="dispatch-runtime"]')).toHaveText(/^ran /);
  await expect(fix2.locator('[data-region="dispatch-lines"]')).toHaveText("+1 −0");
  // And the run time is to the LEFT of the counts, which is the half of c2 that a
  // row carrying only one of them could not have shown.
  await expect(
    fix2.locator('[data-region="dispatch-runtime"] ~ [data-region="dispatch-lines"]'),
  ).toHaveCount(1);

  // ---- KYO-286: and that run time is END MINUS START, not "now minus created".
  //
  // FIX-2.d1 is the exact shape the defect was seen on: reported, ingested, and never
  // closed -- `kyo dispatch close` is the only thing that writes `closed_at`, and no
  // controller runs it on a dispatch that simply reported. So this row had no end time
  // at all and the list ran its clock from `created_at` to now, which grew every paint.
  //
  // Asserted against the two timestamps `kyo worktrees --json` carries rather than
  // against a tolerance: the expected string is a FIXED duration computed from the
  // store, so this fails on the old behaviour (which would print however long this
  // check has been running) and keeps failing however fast the machine is.
  const listed = JSON.parse(
    (await kyo(fixture.resolution, ["worktrees", "--json"], fixture.root)).stdout,
  ) as {
    worktrees: {
      dispatch_id: string;
      created_at: string;
      closed_at: string | null;
      finished_at: string | null;
      finished_at_source: string | null;
    }[];
  };
  const fix2Row = listed.worktrees.find((row) => row.dispatch_id === "FIX-2.d1");
  expect(fix2Row, "kyo worktrees should list FIX-2.d1").toBeDefined();
  expect(fix2Row?.closed_at, "the defect's precondition: nothing ever closed it").toBeNull();
  // The worker's own stop, which is what the hook recorded for it above -- not the
  // ingest, which happened first here and which a lesser precedence would have taken.
  expect(fix2Row?.finished_at_source).toBe("worker-stop");
  const fix2Ran = formatDuration(
    Date.parse(String(fix2Row?.finished_at)) - Date.parse(String(fix2Row?.created_at)),
  );
  await expect(fix2.locator('[data-region="dispatch-runtime"]')).toHaveText(`ran ${fix2Ran}`);
  // And the tip says which of kyo's three sources the end time is, so the figure is
  // one a reader can check rather than one they have to take.
  await expect(fix2.locator('[data-region="dispatch-runtime"]')).toHaveAttribute(
    "data-tip",
    /finished .*the latest stop the stop-audit resolution matched to this dispatch/,
  );
  // The running row is the other half: its clock is still going, so kyo gives it no
  // end time at all -- the ~32s stop tick records a matched stop while a worker runs,
  // and freezing on one of those would stop the clock on every running row.
  const fix1Row = listed.worktrees.find((row) => row.dispatch_id === "FIX-1.d1");
  expect(fix1Row?.finished_at).toBeNull();
  expect(fix1Row?.finished_at_source).toBeNull();
  await expect(fix1.locator('[data-region="dispatch-elapsed"]')).toHaveAttribute(
    "data-tip",
    /still running/,
  );

  // ---- KYO-292.c2: the row still fits the line at the sidebar's NARROWEST width.
  //
  // `.dispatch-line > *` pins every child at `flex: 0 0 auto`, so nothing on it wraps
  // and an overflow is SILENT -- the clock simply leaves the sidebar's right edge,
  // which is how KYO-284 found the same problem by eye and not by a check. So it is
  // measured here, at the 180px minimum (`MIN_WIDTH` in sidebar.ts), on the real row.
  //
  // This is the guard the reworded c2 needs and the earlier draft of it did not
  // survive: a two-figure label ("665k in \u00b7 69k out") measured 74px against a
  // 157px content box and overflowed by 7px with the model already shrunk to nothing.
  // The agent-card sum is one figure, the same shape as the "69k tokens" that was
  // there before, so the fit is the fit KYO-284 left -- and now it is checked.
  const restoreWidth = await page.evaluate(() => {
    const sidebar = document.querySelector('[data-region="sidebar"]') as HTMLElement | null;
    const was = sidebar?.style.width ?? "";
    if (sidebar !== null) sidebar.style.width = "180px";
    return was;
  });
  const overflow = await fix1
    .locator(".dispatch-line")
    .first()
    .evaluate((line) => ({
      scrollWidth: (line as HTMLElement).scrollWidth,
      clientWidth: (line as HTMLElement).clientWidth,
    }));
  // Put the sidebar back before anything else is clicked: the width is an INLINE
  // style the shell sets, so clearing it collapses the panel rather than restoring
  // the default, and every later locator in this test then misses.
  await page.evaluate((was) => {
    const sidebar = document.querySelector('[data-region="sidebar"]') as HTMLElement | null;
    if (sidebar !== null) sidebar.style.width = was;
  }, restoreWidth);
  expect(
    overflow.scrollWidth,
    `line one overflows a ${overflow.clientWidth}px sidebar by ${overflow.scrollWidth - overflow.clientWidth}px`,
  ).toBeLessThanOrEqual(overflow.clientWidth);

  // ---- FIX-3: terminated, no report -- marked as such, with the reason on hover.
  const fix3 = block("FIX-3.d1");
  await expect(fix3.locator('[data-marker="terminated-no-report"]')).toHaveCount(1);
  await expect(fix3.locator('[data-marker="terminated-no-report"]')).toHaveAttribute(
    "data-tip",
    /the worker's machine died mid-session/,
  );
  // KYO-284.c1/c2's other half, and the one that matters: nothing measured this
  // dispatch, so the row says so in words. A zero here would be a claim that a worker
  // whose machine died mid-session cost nothing. Since KYO-459 the row is `kyo
  // activity`'s, which answers the token question for every row it shows -- this one
  // declared no worktree, and the CLI says why it has nothing to sum.
  await expect(fix3.locator('[data-region="dispatch-tokens"]')).toHaveText("tokens unknown");
  await expect(fix3.locator('[data-region="dispatch-tokens"]')).toHaveAttribute(
    "data-tip",
    /no stop of any kind was recorded while this dispatch was running/,
  );

  // ---- FIX-4: still running, and its worktree is gone -- said, not invented.
  await expect(block("FIX-4.d1").locator('[data-region="dispatch-doing"]')).toContainText(
    "worktree is gone",
  );

  // ---- Now mutate FIX-1's REAL worktree while the list shows: a commit, and a report
  // file dropped as a worker would before `kyo report` -- the poll (5s) is what has to
  // notice both and repaint the list, not this spec re-reading anything itself.
  writeFileSync(join(fixture.runningWorktree, "progress.txt"), "made some progress\n", "utf8");
  execFileSync("git", ["-C", fixture.runningWorktree, "add", "-A"], { stdio: "ignore" });
  execFileSync("git", ["-C", fixture.runningWorktree, "commit", "-m", "first commit of work"], {
    stdio: "ignore",
  });
  writeJson(join(fixture.runningWorktree, REPORT_FILE), {
    dispatch_id: "FIX-1.d1",
    note: "not ingested yet -- the file existing is the fact this list reads",
  });
  await expect(
    block("FIX-1.d1").locator('[data-region="dispatch-doing"]'),
    "the poll should notice the new commit",
  ).toContainText("latest commit: first commit of work", { timeout: 30_000 });
  await expect(block("FIX-1.d1").locator('[data-region="dispatch-doing"]')).toContainText(
    "report file present",
  );
  // Lines added and removed, from the diff past the base: the one new file's one line.
  await expect(block("FIX-1.d1").locator('[data-region="dispatch-lines"]')).toHaveText("+1 −0");

  // ---- The record rows under a dispatch open the record.
  await block("FIX-2.d1").locator('[data-row-arg="FIX-2"]').click();
  await expect(
    page.locator('[data-region="tab"][data-tab-kind="record"][data-tab-arg="FIX-2"]'),
  ).toHaveCount(1);

  // ---- And the block's own white space opens its dispatch, as its id link does, so a
  // click anywhere on the block that is not a control opens the record.
  // Clicked in the block's top-left padding, clear of every link, badge and row -- and
  // of the sidebar's resize grip, which overlaps the block's last few pixels on the right.
  await block("FIX-3.d1").click({ position: { x: 5, y: 2 } });
  await expect(
    page.locator('[data-region="tab"][data-tab-kind="record"][data-tab-arg^="FIX-3"]'),
  ).toHaveCount(1);
});

// ==================================================================== the helpers

test("elapsed and duration formatting, as the list draws them", () => {
  expect(formatDuration(4_000)).toBe("4s");
  expect(formatDuration(65_000)).toBe("1m 5s");
  expect(formatDuration(3_600_000 + 60_000)).toBe("1h 1m");
  expect(formatDuration(90_000_000)).toBe("1d 1h 0m");
  expect(elapsedFor({ createdAt: "2026-01-01T00:00:00Z", closedAt: "2026-01-01T00:02:00Z" })).toBe(
    "2m 0s",
  );
  expect(elapsedFor({ createdAt: "not a date", closedAt: null })).toBe("");
});

// ======================================================================== KYO-284

test("a finished row shows its date once it is more than a day old", () => {
  // A finished row more than a day old shows its date instead of a duration. Both
  // sides of the boundary, because a threshold checked on one side is a threshold that
  // could be anywhere.
  const ran = { createdAt: "2026-09-14T10:00:00Z", closedAt: "2026-09-14T12:30:00Z" };
  const justUnder = () => Date.parse(ran.closedAt) + 86_400_000 - 1_000;
  const justOver = () => Date.parse(ran.closedAt) + 86_400_000 + 1_000;

  // Younger than a day: what it shows today, which is how long it ran.
  expect(finishedWhen(ran, justUnder)).toMatch(/^23h 59m ago$|^1d 0h 0m ago$/);
  // Older: the date it ran, in the app's own ISO idiom and in local time, so the
  // check is the same statement whatever timezone it runs in -- noon UTC on the
  // 14th is the 14th everywhere from UTC-11 to UTC+12.
  expect(finishedWhen(ran, justOver)).toBe("2026-09-14");
  // A row with no `closedAt` at all falls through to the duration, and an
  // unparseable one to `elapsedFor`'s empty string rather than to "NaN".
  expect(finishedWhen({ createdAt: "2026-09-14T10:00:00Z", closedAt: null }, justOver)).toBe(
    "1d 2h 30m",
  );
  expect(finishedWhen({ createdAt: "2026-09-14T10:00:00Z", closedAt: "nope" }, justOver)).toBe("");
});

test("token figures are rounded, and an unknown one is words rather than a zero", () => {
  expect(roundedTokens(0)).toBe("0");
  expect(roundedTokens(940)).toBe("940");
  expect(roundedTokens(160_400)).toBe("160k");
  expect(roundedTokens(1_440_000)).toBe("1.4M");

  // KYO-284.c1/c2. `known` is the only thing branched on: a dispatch nothing could
  // be matched to and one that genuinely cost nothing are different facts, and a
  // label derived from `totalTokens === 0` would render them identically.
  //
  // KYO-292.c2: the figure is the one claude.ai's agent card shows for the same
  // worker -- kyo's `latest_turn_tokens`, the size of that worker's LAST turn -- so
  // the two can be read against each other. 187,726 is what the last message read on
  // KYO-289.d1's transcript when its card said 189,568; every cumulative sum was
  // already past both (190,141 for input + cache creation + output counted once per
  // message), which is how a running total was ruled out.
  expect(tokensLabel({ known: true, latestTurnTokens: 187_726 })).toBe("188k tokens");
  expect(tokensLabel({ known: true, latestTurnTokens: 0 })).toBe("0 tokens");
  expect(tokensLabel({ known: false, latestTurnTokens: 187_726 })).toBe("tokens unknown");
  expect(tokensLabel(null)).toBe("tokens unknown");

  // And the reason a row says "unknown" is the CLI's own sentence, carried through
  // untouched -- the app never writes its own account of why kyo could not answer.
  const unknown = {
    known: false,
    why: "no stop has been recorded against this dispatch",
    model: null,
    inputTokens: 0,
    cacheCreationInputTokens: 0,
    cacheReadInputTokens: 0,
    outputTokens: 0,
    latestTurnTokens: 0,
    totalTokens: 0,
    messages: 0,
  };
  expect(tokensTip(unknown)).toBe("no stop has been recorded against this dispatch");
  const known = {
    ...unknown,
    known: true,
    model: "claude-opus-5",
    inputTokens: 2,
    cacheCreationInputTokens: 3,
    cacheReadInputTokens: 900,
    outputTokens: 5,
    latestTurnTokens: 880,
    totalTokens: 910,
    messages: 4,
  };
  // The headline, said to be the latest TURN and not a total, with the reason it can
  // read under the web card; then all four summed counts and what the worker used
  // altogether -- so the one rounded figure in the row never has to be trusted alone,
  // and the two quantities are never presented as one (KYO-292.c2).
  expect(tokensTip(known)).toContain("880 tokens on claude-opus-5 in this worker's latest turn");
  expect(tokensTip(known)).toContain("about a percent under the card");
  expect(tokensTip(known)).toContain("across all 4 of its messages: 2 input");
  expect(tokensTip(known)).toContain("3 cache creation");
  expect(tokensTip(known)).toContain("900 cache read");
  expect(tokensTip(known)).toContain("5 output");
  expect(tokensTip(known)).toContain("910 in all, which is what it used altogether");
});

test("the app renames kyo's token answer and derives nothing from it", () => {
  // KYO-284.c4 at the app's mapper. Every number below is the CLI's; the mapper's
  // whole job is the casing. A `total_tokens` that disagreed with the four counts
  // would come through as kyo sent it, because recomputing it here would be the app
  // holding a second opinion about what a dispatch cost.
  const mapped = workerTokensOf({
    outcome: "matched",
    known: true,
    why: "summed from 1 transcript(s)",
    model: "claude-opus-5",
    transcripts: 1,
    messages: 4,
    input_tokens: 28,
    cache_creation_input_tokens: 4_400,
    cache_read_input_tokens: 360_000,
    output_tokens: 2_560,
    latest_turn_tokens: 91_747,
    total_tokens: 366_988,
  });
  expect(mapped).toEqual({
    outcome: "matched",
    known: true,
    why: "summed from 1 transcript(s)",
    model: "claude-opus-5",
    transcripts: 1,
    messages: 4,
    inputTokens: 28,
    cacheCreationInputTokens: 4_400,
    cacheReadInputTokens: 360_000,
    outputTokens: 2_560,
    // KYO-292.c2: the latest turn's size comes across as kyo sent it, and could not
    // be derived here from any of the four counts above -- it is the LAST message's
    // usage and this object carries only their sums. One place decides what "tokens"
    // means on a row, and a mapper holding a second opinion would disagree with
    // `kyo show` in front of a user comparing the two.
    latestTurnTokens: 91_747,
    totalTokens: 366_988,
  });

  // A row from a `kyo` that predates KYO-284 carries no `worker_tokens` at all, and
  // that is null rather than a zeroed object: the list says "tokens unknown" for it,
  // which is true, where a zeroed object would have said this dispatch was free.
  expect(workerTokensOf(undefined)).toBeNull();
  expect(workerTokensOf(null)).toBeNull();
  expect(workerTokensOf({ known: true })).toBeNull();
  expect(workerTokensOf({ outcome: "no-transcript-recorded" })?.known).toBe(false);
});

// ======================================================================== KYO-286

test("a finished dispatch's run time is end minus start and does not grow", () => {
  // The whole defect in one pair of assertions. `closedAt` is written by `kyo
  // dispatch close` and by nothing else, so a dispatch that reported and was never
  // closed had no end at all and every helper here fell through to `now()` -- which
  // is a different answer every time it is asked. Two very different `now`s below,
  // one answer, which is the property a growing clock cannot have.
  const reported = {
    createdAt: "2026-09-16T10:00:00Z",
    closedAt: null,
    finished: {
      at: "2026-09-16T10:17:00Z",
      source: "worker-stop",
      why: "the latest stop the stop-audit resolution matched to this dispatch (KYO-E9)",
    },
  };
  const soon = () => Date.parse("2026-09-16T10:20:00Z");
  const muchLater = () => Date.parse("2026-09-16T18:00:00Z");

  expect(elapsedFor(reported, soon)).toBe("17m 0s");
  expect(elapsedFor(reported, muchLater)).toBe("17m 0s");
  // And the same row without kyo's answer is the old behaviour, still: the clock runs
  // from `created_at` to now and every read of it is a bigger number. Asserted so the
  // fix cannot be mistaken for a rendering change that was always going to happen.
  const withoutKyo = { createdAt: reported.createdAt, closedAt: null };
  expect(elapsedFor(withoutKyo, soon)).toBe("20m 0s");
  expect(elapsedFor(withoutKyo, muchLater)).toBe("8h 0m");

  // A still-running dispatch keeps its clock: kyo gives it no `at`, because the ~32s
  // stop tick would otherwise freeze every running row within half a minute.
  const running = {
    createdAt: "2026-09-16T10:00:00Z",
    closedAt: null,
    finished: { at: null, source: null, why: "this dispatch is still running" },
  };
  expect(elapsedFor(running, soon)).toBe("20m 0s");
  expect(elapsedFor(running, muchLater)).toBe("8h 0m");

  // `finishedWhen` reads the same end, so line one stops moving too -- and crosses
  // the one-day boundary against the END, not against the close it does not have.
  expect(finishedWhen(reported, soon)).toBe("3m 0s ago");
  expect(finishedWhen(reported, () => Date.parse("2026-09-18T10:00:00Z"))).toBe("2026-09-16");
  expect(finishedWhen(running, soon)).toBe("20m 0s");

  // `closedAt` still answers for a row kyo said nothing about, and an end that will
  // not parse is still the empty string rather than "NaN" anything.
  expect(endedAtMs({ createdAt: "x", closedAt: "2026-09-16T10:05:00Z" })).toBe(
    Date.parse("2026-09-16T10:05:00Z"),
  );
  expect(endedAtMs({ createdAt: "x", closedAt: null })).toBeNull();
  expect(elapsedFor({ createdAt: reported.createdAt, closedAt: "nope" }, soon)).toBe("");
  // kyo's answer outranks `closedAt`: a close can land a day after the worker stopped.
  expect(
    elapsedFor(
      {
        createdAt: reported.createdAt,
        closedAt: "2026-09-17T10:00:00Z",
        finished: reported.finished,
      },
      soon,
    ),
  ).toBe("17m 0s");
});

test("the tip names the end time and which of kyo's three sources it is", () => {
  // KYO-286.c1's last clause. The source word is never written here: `why` is the
  // CLI's own sentence, carried through the way `tokensTip` carries its one, so this
  // app holds no second account of a precedence it does not implement.
  const stopped = {
    createdAt: "2026-09-16T10:00:00Z",
    closedAt: null,
    finished: {
      at: "2026-09-16T10:17:00Z",
      source: "worker-stop",
      why: "the latest stop the stop-audit resolution matched to this dispatch (KYO-E9)",
    },
  };
  expect(whenTip(stopped)).toBe(
    "started 2026-09-16T10:00:00Z, finished 2026-09-16T10:17:00Z — the latest stop the " +
      "stop-audit resolution matched to this dispatch (KYO-E9)",
  );
  const ingested = {
    ...stopped,
    finished: {
      at: "2026-09-16T11:00:00Z",
      source: "report-ingest",
      why: "when KYO-1.r1 was ingested — no worker stop was matched to this dispatch",
    },
  };
  expect(whenTip(ingested)).toContain("when KYO-1.r1 was ingested");
  const closed = {
    createdAt: "2026-09-16T10:00:00Z",
    closedAt: "2026-09-16T10:30:00Z",
    finished: {
      at: "2026-09-16T10:30:00Z",
      source: "closed-at",
      why: "when `kyo dispatch close` closed it",
    },
  };
  expect(whenTip(closed)).toContain("when `kyo dispatch close` closed it");

  // A running row says so; a row kyo was never asked about says THAT, rather than
  // borrowing a source word for a fallback that is not one of the three.
  expect(
    whenTip({
      createdAt: "2026-09-16T10:00:00Z",
      closedAt: null,
      finished: { at: null, source: null, why: "this dispatch is still running" },
    }),
  ).toBe("started 2026-09-16T10:00:00Z — this dispatch is still running");
  expect(whenTip({ createdAt: "2026-09-16T10:00:00Z", closedAt: null })).toBe(
    "started 2026-09-16T10:00:00Z; still running",
  );
  expect(
    whenTip({ createdAt: "2026-09-16T10:00:00Z", closedAt: "2026-09-16T10:30:00Z" }),
  ).toContain("kyo said nothing about when this dispatch finished");
});

test("the app renames kyo's end time and derives nothing from it", () => {
  // KYO-286 at the app's mapper, on `workerTokensOf`'s terms: every field is the
  // CLI's and the mapper's whole job is the casing.
  expect(
    dispatchEndOf({
      dispatch_id: "KYO-1.d1",
      finished_at: "2026-09-16T10:17:00Z",
      finished_at_source: "worker-stop",
      finished_at_why: "the latest stop the stop-audit resolution matched to this dispatch",
    }),
  ).toEqual({
    at: "2026-09-16T10:17:00Z",
    source: "worker-stop",
    why: "the latest stop the stop-audit resolution matched to this dispatch",
  });

  // A running row: an answer, and its answer is that there is no end yet. This is the
  // case `finished_at` alone could not express, which is why the mapper branches on
  // `finished_at_why` -- the one field the CLI writes on every branch.
  expect(
    dispatchEndOf({
      finished_at: null,
      finished_at_source: null,
      finished_at_why: "this dispatch is still running",
    }),
  ).toEqual({ at: null, source: null, why: "this dispatch is still running" });

  // And a row from a `kyo` that predates KYO-286 carries none of the three, which is
  // null rather than a running dispatch -- the list then falls back to `closedAt`,
  // which is what it always did.
  expect(dispatchEndOf({ dispatch_id: "KYO-1.d1", closed_at: null })).toBeNull();
  expect(dispatchEndOf(null)).toBeNull();
  expect(dispatchEndOf(undefined)).toBeNull();
});
