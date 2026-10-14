// The activity rows: every running dispatch, and the most recent finished ones.
//
// KYO-231's third query, and the only one of the four whose facts come from two
// places at once: the store's half changes when a controller does something; git's
// half changes whenever a worker commits, and nothing tells anybody when. So the view
// is POLLED while anything is running (`main.ts`), because there is no event a
// worker's `git commit` fires that a viewer could subscribe to.
//
// SINCE KYO-459 THE CLI JOINS IT. `kyo activity --json` (KYO-454) is the dispatch rows
// with their record, their current report's verdict counts, their record's open gates
// and classes, the worker's tokens and end time, the unaudited stops, and what git says
// about each worktree now -- read on the machine that holds the project, asked of its
// kyo serve. This file renames that envelope into this codebase's casing and derives
// nothing: every class is the CLI's word, every number the CLI's count.
//
// Electron-free, like every other seam module.

import type {
  ActivityClass,
  ActivityRow,
  ActivityView,
  DispatchEnd,
  GitFacts,
  GitFactsOutcome,
  WorkerTokens,
} from "../shared/protocol";
import { ACTIVITY_CLASSES, GIT_FACTS_OUTCOMES } from "../shared/protocol";
import type { ServedProject } from "./kyoserve";
import { ACTIVITY_FINISHED_DEFAULT } from "./records";

/** The dispatch state a running dispatch is in. The store's word, not ours. */
export const RUNNING = "running";

/** The state a dispatch closed without a report lands in. Also the store's word. */
export const TERMINATED_NO_REPORT = "terminated-no-report";

/** The command every activity view is read from. */
export const ACTIVITY_COMMAND = "kyo activity --json";

function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function int(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function rows(value: unknown): Record<string, unknown>[] {
  return (Array.isArray(value) ? value : []).filter(
    (item): item is Record<string, unknown> =>
      item !== null && typeof item === "object" && !Array.isArray(item),
  );
}

/** One row's `git`, renamed: `kyo activity`'s `git_facts`, read where the project is. */
export function gitFactsOf(raw: unknown): GitFacts {
  const row = raw !== null && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const outcome = (GIT_FACTS_OUTCOMES as readonly string[]).includes(String(row.outcome))
    ? (row.outcome as GitFactsOutcome)
    : "not-a-git-worktree";
  return {
    worktree: str(row.worktree),
    present: row.present === true,
    dirty: typeof row.dirty === "boolean" ? row.dirty : null,
    dirtyPaths: Array.isArray(row.dirty_paths) ? row.dirty_paths.map(String) : [],
    branch: str(row.branch),
    commitsPastBase: int(row.commits_past_base),
    latestCommitSha: str(row.latest_commit_sha),
    latestCommitMessage: str(row.latest_commit_message),
    linesAdded: int(row.lines_added),
    linesRemoved: int(row.lines_removed),
    mergedInto: typeof row.merged_into === "boolean" ? row.merged_into : null,
    reportFilePresent: row.report_file_present === true,
    outcome,
    why: typeof row.why === "string" ? row.why : "",
  };
}

/**
 * One row's `worker_tokens`, renamed and not otherwise touched (KYO-284.c4).
 *
 * A pure rename: every number here is `kyo`'s, and nothing is computed, defaulted
 * or repaired. A row from an older `kyo` that carries no `worker_tokens` at all
 * answers `null`, and the list says "tokens unknown" for it -- which is true, and
 * is what a derived number would have hidden.
 */
export function workerTokensOf(raw: unknown): WorkerTokens | null {
  if (raw === null || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const outcome = row.outcome;
  if (typeof outcome !== "string") return null;
  const count = (key: string): number => (typeof row[key] === "number" ? (row[key] as number) : 0);
  return {
    outcome,
    known: row.known === true,
    why: typeof row.why === "string" ? row.why : "",
    model: typeof row.model === "string" && row.model !== "" ? row.model : null,
    transcripts: count("transcripts"),
    messages: count("messages"),
    inputTokens: count("input_tokens"),
    cacheCreationInputTokens: count("cache_creation_input_tokens"),
    cacheReadInputTokens: count("cache_read_input_tokens"),
    outputTokens: count("output_tokens"),
    latestTurnTokens: count("latest_turn_tokens"),
    totalTokens: count("total_tokens"),
  };
}

/**
 * One row's end time, renamed and not otherwise touched (KYO-286).
 *
 * A pure rename, on exactly `workerTokensOf`'s terms: the three fields are `kyo`'s
 * and nothing here computes, defaults or repairs one. The presence of
 * `finished_at_why` is what is tested, because it is the one field the CLI always
 * writes -- `finished_at` is legitimately null on a running dispatch, so branching
 * on IT would read a row from a `kyo` that predates this as "still running" and a
 * running one as "no answer", which are the two things that must stay apart. A row
 * from an older `kyo` answers null and the list falls back to `closedAt`.
 */
export function dispatchEndOf(raw: unknown): DispatchEnd | null {
  if (raw === null || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  if (typeof row.finished_at_why !== "string") return null;
  return {
    at: typeof row.finished_at === "string" && row.finished_at !== "" ? row.finished_at : null,
    source:
      typeof row.finished_at_source === "string" && row.finished_at_source !== ""
        ? row.finished_at_source
        : null,
    why: row.finished_at_why,
  };
}

export interface ActivityOptions {
  finished?: number;
  /** The branch a finished dispatch lands on. The store does not record it; `main`. */
  against?: string;
}

/** One `kyo activity` row, renamed into an activity row. */
export function activityRowOf(row: Record<string, unknown>): ActivityRow {
  const covers = rows(row.covers).map((entry) => ({
    id: String(entry.id ?? ""),
    title: typeof entry.title === "string" ? entry.title : "",
  }));
  const classes = (Array.isArray(row.classes) ? row.classes : []).filter(
    (word): word is ActivityClass => (ACTIVITY_CLASSES as readonly string[]).includes(String(word)),
  );
  return {
    dispatchId: String(row.id ?? ""),
    recordId: String(row.record_id ?? ""),
    recordTitle: typeof row.record_title === "string" ? row.record_title : "",
    recordKind: typeof row.record_kind === "string" ? row.record_kind : "",
    recordStatus: typeof row.record_status === "string" ? row.record_status : "",
    // The role word is not on the dispatch row; the record document carries it for
    // the page that needs it, and nothing here guesses one.
    recordStanding: "",
    state: String(row.state ?? ""),
    workerModel: str(row.worker_model),
    workedFromSha: typeof row.worked_from_sha === "string" ? row.worked_from_sha : "",
    branch: str(row.branch),
    createdAt: typeof row.created_at === "string" ? row.created_at : "",
    closedAt: str(row.closed_at),
    terminatedWhy: str(row.terminated_why),
    reportId: str(row.report_id),
    verdictCounts: rows(row.verdict_counts).map((entry) => ({
      verdict: String(entry.verdict ?? ""),
      count: int(entry.count) ?? 0,
    })),
    unauditedStops: Array.isArray(row.unaudited_stops) ? row.unaudited_stops.length : 0,
    // KYO-284: `kyo`'s answer, or null. Never a zero -- a worker that cost nothing and
    // one nothing measured are different, and only the CLI can tell them apart.
    workerTokens: workerTokensOf(row.worker_tokens),
    // KYO-286: `kyo`'s answer, or null; the one place that renders a time falls back
    // to `closedAt`.
    finished: dispatchEndOf(row),
    openGates: int(row.open_gates) ?? 0,
    covers,
    git: gitFactsOf(row.git),
    classes,
  };
}

/**
 * Read the activity rows: `kyo activity --finished N --against REF --json`, asked of
 * the project's kyo serve. Not cached between calls: caching is the one change that
 * would quietly break the poll, whose whole reason is that a worker's commit changes
 * git without changing anything a viewer can see.
 */
export async function readActivity(
  project: ServedProject,
  options: ActivityOptions = {},
): Promise<ActivityView> {
  const finished = options.finished ?? ACTIVITY_FINISHED_DEFAULT;
  const against = options.against ?? "main";
  const readAt = new Date().toISOString();
  const empty = (problem: string): ActivityView => ({
    ok: false,
    rows: [],
    running: 0,
    finishedShown: 0,
    readAt,
    problem,
  });
  let answer: Awaited<ReturnType<ServedProject["json"]>>;
  try {
    answer = await project.json(["activity", "--finished", String(finished), "--against", against]);
  } catch (error) {
    return empty((error as Error).message);
  }
  const data = answer.data;
  if (answer.code !== 0 || data === null || data.ok === false) {
    const said = typeof data?.error === "string" && data.error !== "" ? data.error : answer.stderr;
    return empty(said.trim() || `\`${ACTIVITY_COMMAND}\` exited ${answer.code} with no message`);
  }
  const listed = rows(data.rows).map(activityRowOf);
  const running = listed.filter((row) => row.state === RUNNING).length;
  return {
    ok: true,
    rows: listed,
    running,
    finishedShown: listed.length - running,
    readAt,
    problem: null,
  };
}

/**
 * The shape of one activity view, for the poll to compare two answers by (KYO-264).
 *
 * Everything but `readAt`, which is the one field that moves on every tick whether or
 * not anything did. Two views with the same fingerprint would paint the same rows, so
 * the poll pushes neither the second nor the redraw it would cause -- the same terms
 * the awaiting-human poll has always pushed on. A view that could not be read prints
 * as its problem, so a store that goes away and comes back is two changes, not none.
 */
export function activityFingerprint(view: ActivityView): string {
  return JSON.stringify({ ...view, readAt: "" });
}
