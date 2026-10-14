// Every read records mode makes, and the guard that keeps them reads.
//
// Split out of `facts.ts` at the start of KYO-113 with no behaviour change: that
// file's subject is the *shell's* two live seams (where the store is, which side of
// the db/export seam moved), and records mode had grown a second subject inside it
// -- the Base engine, the board classes, the folder tree, one record, the session
// briefs. They are separated here so that each file states one thing, and so that a
// renderer added by a later record has an obvious place to ask from.
//
// The division of labour is unchanged and is KYO-72's:
//
//   the query          `kyo base run <name> [--group-by FIELD] --json` -- the CLI's
//                      own Base engine, which KYO-45 makes the single primitive
//                      behind every rendering. The app never selects rows itself,
//                      and since KYO-120 it does not arrange the tree's either.
//   the board classes  `kyo next --json` -- ready, blocked (KYO-570), scope-unknown,
//                      and KYO-87's "finished but not moved", all off one computation
//                      in the CLI.
//   past session briefs  read straight out of the store's own tables, because the
//                      command that would answer this *writes* (see below).
//
// What is NOT here is as load-bearing: no filtering rule, no readiness rule, no
// currency comparison, no status vocabulary and -- since KYO-120 gave `kyo base run`
// a `--group-by` -- no grouping rule either. Law 3: a rule with two implementations
// gets to disagree with itself.

import type {
  AwaitingGate,
  AwaitingHuman,
  BaseCatalogue,
  BaseCounts,
  BaseDefinition,
  BaseGroup,
  BaseResult,
  BoardClass,
  BriefCatalogue,
  BriefDocument,
  BriefDocumentView,
  BriefRun,
  ClaimView,
  CoveringDispatchView,
  CriterionView,
  CurrencyFacts,
  DispatchView,
  DocRefView,
  EventView,
  EvidenceView,
  FlagView,
  GateDocument,
  GateView,
  LatestShiftNote,
  LinkView,
  MemberView,
  NoteView,
  ObligationCollapse,
  RecordDetail,
  RecordDocument,
  RecordsList,
  RecordsListRow,
  RecordTree,
  ReportView,
  ShiftNotesCatalogue,
  ShiftNoteView,
  TouchView,
  TreeGrouping,
  TreeRecord,
  TreeScope,
  VerificationView,
} from "../shared/protocol";
import { groupingField } from "../shared/protocol";
import { currencyFailure, currencyFrom, readCurrency } from "./facts";
import type { KyoJson, ServedProject } from "./kyoserve";

/**
 * The `kyo` subcommands records mode is allowed to construct (KYO-112.c6).
 *
 * An allowlist rather than a denylist, and checked at the one place arguments are
 * assembled, because "records mode is read-only to a human" (KYO-72's amendment) is
 * a claim about what the app *can* do, and a denylist makes it a claim about what
 * this author remembered. A new command added to the CLI is refused here until
 * somebody puts it on this list on purpose.
 */
export const RECORDS_READ_COMMANDS: readonly string[] = [
  "base",
  "show",
  "next",
  "list",
  // KYO-454/KYO-459: the ledgers this mode read by SQL until the app became a client
  // of kyo serve. Each is read-only on the CLI's side (no transaction, no row).
  "briefs",
  "shift-notes",
  "gates",
];

/**
 * Commands that mutate the store, listed so the refusal can say *why*.
 *
 * `brief` is on this list and that is the entry worth reading twice. `kyo brief`
 * reads like a report and is offered to controllers as the thing to read instead of
 * the records -- and every run of it INSERTs a row into `brief_runs`. It was the
 * obvious command to build the calendar tool on, and it is the one command records
 * mode most needs to not call. `kyo next --json` computes the same board classes
 * (`cmd_review._ready_records`, shared with `cmd_brief`) and writes nothing, so that
 * is what this app asks, and the past briefs are read out of the table directly.
 *
 * KYO-140: this list is hand-kept and it drifted -- `retag` (KYO-130), `flag` and
 * `docs` were all added to the CLI as mutating commands and none reached this list,
 * so their refusals said "is not known to be read-only" instead of naming them as
 * writes. `tests/test_kyo140_desktop_command_drift.py` now reads this array and fails
 * if any member of `kyosei.cli.MUTATING_COMMANDS` is missing from it, or if any name
 * on either list is not a real `kyo` subcommand. It is a SUPERSET of
 * `MUTATING_COMMANDS` on purpose: `init`, `ideas`, `rebuild`, `export` and the
 * two installers write files without writing an events row, so they are writes to a
 * human and not mutations to the store's own definition. The test checks the direction
 * that can rot silently -- everything the CLI calls a mutation is named here -- and
 * leaves the extras alone beyond requiring that each one exists.
 */
export const KYO_WRITING_COMMANDS: readonly string[] = [
  "init",
  "new",
  "promote",
  "spawn",
  "edit",
  "restate",
  "move",
  "wont-do",
  "void",
  "link",
  "unlink",
  "gate",
  "retag",
  "redo",
  "ungate",
  "claim",
  "verify",
  "attach",
  "prior-art",
  "redact",
  "ideas",
  "dispatch",
  "report",
  // KYO-331: `kyo accept` applies an ingested report — verdicts, docs verdicts and the
  // touch-drift correction — through the commands above it, so it writes to the store.
  "accept",
  // KYO-494: `kyo confirm` verifies, attaches, ungates and moves through the commands above.
  "confirm",
  // KYO-536: `kyo close-wave` moves records through `kyo move`, then commits.
  "close-wave",
  "split",
  "sweep",
  "flag",
  "docs",
  "note",
  "rebuild",
  "export",
  "install-hooks",
  "install-skill",
  "brief",
];

/** Refuse, by name, any command records mode is not allowed to build. */
export function assertRecordsRead(args: readonly string[]): void {
  const command = args[0] ?? "";
  if (RECORDS_READ_COMMANDS.includes(command)) return;
  const writes = KYO_WRITING_COMMANDS.includes(command)
    ? ` \`kyo ${command}\` writes to the store`
    : ` \`kyo ${command}\` is not known to be read-only`;
  throw new Error(
    `kyosei: records mode may not run \`kyo ${command}\`.${writes}, and records mode is ` +
      "read-only to a human (KYO-72's amendment, KYO-112.c6). The commands it may run are: " +
      `${RECORDS_READ_COMMANDS.join(", ")}.`,
  );
}

/** Every records-mode invocation. There is no other route from this mode to `kyo`. */
async function readJson(project: ServedProject, args: readonly string[]): Promise<KyoJson> {
  assertRecordsRead(args);
  return project.json(args);
}

// ------------------------------------------------------------ payload readers

function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function int(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function rows(value: unknown): Record<string, unknown>[] {
  return list(value).filter(
    (item): item is Record<string, unknown> =>
      item !== null && typeof item === "object" && !Array.isArray(item),
  );
}

function strings(value: unknown): string[] {
  return list(value).map((item) => String(item));
}

function record(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

/**
 * The CLI's refusal, verbatim.
 *
 * A base the CLI refuses is surfaced with the CLI's own message and never an empty
 * view (KYO-112.c4): the message names the declared bases and the `bases.yml` entry
 * that would make the query legal, and a paraphrase would lose the remedy. The
 * fallbacks below exist so this can never return the empty string -- an empty
 * refusal renders as an empty pane, which is the failure being avoided.
 */
function refusalFrom(
  data: Record<string, unknown> | null,
  stderr: string,
  code: number,
  command: string,
): string {
  return (
    str(data?.error) ??
    (stderr.trim() !== "" ? stderr.trim() : null) ??
    `\`${command}\` exited ${code} with no message`
  );
}

function commandOf(args: readonly string[]): string {
  return `kyo ${args.join(" ")} --json`;
}

// ------------------------------------------------------------------ the bases

export const BASE_LIST_COMMAND = "kyo base list --json";

/** The saved bases in `kyosei/bases.yml`, or the CLI's refusal to read them. */
export async function readBases(project: ServedProject): Promise<BaseCatalogue> {
  const args = ["base", "list"];
  const command = commandOf(args);
  let result: Awaited<ReturnType<typeof readJson>>;
  try {
    result = await readJson(project, args);
  } catch (error) {
    return {
      ok: false,
      path: null,
      exists: false,
      bases: [],
      refusal: (error as Error).message,
      command,
    };
  }
  const { data, stderr, code } = result;
  if (data === null || data.ok !== true) {
    return {
      ok: false,
      path: str(data?.path),
      exists: data?.exists === true,
      bases: [],
      refusal: refusalFrom(data, stderr, code, command),
      command,
    };
  }
  return {
    ok: true,
    path: str(data.path),
    exists: data.exists === true,
    bases: rows(data.bases).map(baseDefinition),
    refusal: null,
    command,
  };
}

function baseDefinition(entry: Record<string, unknown>): BaseDefinition {
  const filters: Record<string, string> = {};
  for (const [key, value] of Object.entries(record(entry.filters) ?? {})) {
    filters[key] = String(value);
  }
  return {
    name: text(entry.name),
    summary: text(entry.summary),
    source: text(entry.source),
    columns: strings(entry.columns),
    sort: strings(entry.sort),
    groupBy: str(entry["group-by"]),
    filters,
  };
}

function countsFrom(value: unknown): BaseCounts | null {
  const summary = record(value);
  if (summary === null) return null;
  return {
    counted: int(summary.counted),
    notCounted: int(summary.not_counted),
    total: int(summary.total),
  };
}

function groupsFrom(value: unknown): BaseGroup[] | null {
  if (value === null || value === undefined) return null;
  return rows(value).map((group) => ({
    value: group.value === null || group.value === undefined ? null : String(group.value),
    count: int(group.count),
    ids: strings(group.ids),
  }));
}

/**
 * Run one saved base through the CLI's own engine.
 *
 * Reads may shell out; only writes must not (KYO-72), and this mode has no writes.
 * The rows, the projection, the grouping and the counts all come back from
 * `kyo base run --json` exactly as the engine produced them -- the app re-sorts and
 * re-filters *on top* for the reader's own refinement, and that refinement is
 * separate and visibly so (see `refineRows`).
 *
 * The rows carry `links` beside their columns, which `query._attach_links` puts
 * there for exactly one consumer: KYO-45's amendment says the typed-edge graph is
 * the fourth rendering *of this run*, so the graph draws the edges this document
 * already carried rather than issuing a query per node.
 *
 * `groupBy` is `kyo base run --group-by` (KYO-120), and it is how a caller asks the
 * engine for a grouping the saved base does not declare. Omitted, the run is exactly
 * what the file says; supplied, the engine rearranges the same rows and reports the
 * field it used in `applied.group-by`. It reaches nothing else -- which rows exist is
 * still the reviewable file's answer -- and it is on the `command` string this result
 * carries, so a surface quoting its provenance quotes the grouping too.
 */
export async function runBase(
  project: ServedProject,
  name: string,
  groupBy?: string,
): Promise<BaseResult> {
  const args =
    groupBy === undefined ? ["base", "run", name] : ["base", "run", name, "--group-by", groupBy];
  const command = commandOf(args);
  const empty = (refusal: string, currency: CurrencyFacts): BaseResult => ({
    ok: false,
    name,
    summary: "",
    columns: [],
    rows: [],
    groups: null,
    counts: null,
    currency,
    refusal,
    command,
  });
  let result: Awaited<ReturnType<typeof readJson>>;
  try {
    result = await readJson(project, args);
  } catch (error) {
    const message = (error as Error).message;
    return empty(message, currencyFrom(null, command, { detail: message }));
  }
  const { data, stderr, code } = result;
  const currency = currencyFrom(data?.db_export_currency, command, {
    detail: stderr.trim() || `exit ${code}`,
  });
  if (data === null || data.ok !== true) {
    return empty(refusalFrom(data, stderr, code, command), currency);
  }
  const applied = record(data.applied);
  const base = record(data.base);
  return {
    ok: true,
    name,
    summary: text(base?.summary),
    columns: strings(applied?.columns ?? base?.columns),
    rows: rows(data.rows),
    groups: groupsFrom(data.groups),
    counts: countsFrom(data.summary),
    currency,
    refusal: null,
    command,
  };
}

// --------------------------------------------------------- the board's classes

/**
 * Ready, blocked, scope-unknown and finished-but-not-moved, as the CLI computes them.
 *
 * `kyo next --json`, not `kyo brief --json`: the two share `_ready_records`, so the
 * classes are identical, and only one of them leaves the store as it found it.
 */
export const BOARD_COMMAND = "kyo next --json";

export interface BoardClasses {
  ready: Set<string>;
  settledNotMoved: Set<string>;
  scopeUnknown: Set<string>;
  /**
   * Records `kyo next` withholds under `blocked` (KYO-570): an unsettled `blocks`
   * source holds them back, so they are never offered as ready (KYO-571).
   */
  blocked: Set<string>;
  /** The CLI's own remedy for a settled-not-moved record, keyed by record id. */
  needs: Map<string, string>;
  problem: string | null;
}

function emptyBoard(problem: string | null): BoardClasses {
  return {
    ready: new Set(),
    settledNotMoved: new Set(),
    scopeUnknown: new Set(),
    blocked: new Set(),
    needs: new Map(),
    problem,
  };
}

export async function readBoardClasses(project: ServedProject): Promise<BoardClasses> {
  const args = ["next"];
  const command = commandOf(args);
  let result: Awaited<ReturnType<typeof readJson>>;
  try {
    result = await readJson(project, args);
  } catch (error) {
    return emptyBoard((error as Error).message);
  }
  const { data, stderr, code } = result;
  if (data === null || data.ok !== true) {
    return emptyBoard(refusalFrom(data, stderr, code, command));
  }
  const board = emptyBoard(null);
  const idsOf = (value: unknown): Record<string, unknown>[] => rows(value);
  for (const item of idsOf(data.ready)) board.ready.add(text(item.id));
  for (const item of idsOf(data.scope_unknown)) board.scopeUnknown.add(text(item.id));
  for (const item of idsOf(data.blocked)) board.blocked.add(text(item.id));
  for (const item of idsOf(data.settled_not_moved)) {
    const id = text(item.id);
    board.settledNotMoved.add(id);
    const needs = str(item.needs);
    if (needs !== null) board.needs.set(id, needs);
  }
  return board;
}

/**
 * Which class a record is in.
 *
 * `settled-not-moved` is checked first for the reason KYO-87 exists: the defect was
 * finished work appearing in the list a controller reads as "what to hand out next",
 * and the CLI resolves the overlap the same way -- `_ready_records` `continue`s past
 * a settled record before it ever reaches the ready list.
 */
export function classOf(board: BoardClasses, id: string): BoardClass {
  if (board.settledNotMoved.has(id)) return "settled-not-moved";
  if (board.ready.has(id)) return "ready";
  if (board.blocked.has(id)) return "blocked";
  if (board.scopeUnknown.has(id)) return "scope-unknown";
  return "none";
}

// -------------------------------------------------------------- the folder tree

/**
 * The base the folder tree runs. Its summary in `kyosei/bases.yml` names it as
 * exactly that, and running a saved base rather than an ad-hoc query is what keeps
 * the tree and the Base view two renderings of one query (KYO-45).
 */
/** The saved base behind each scope pill. The names are the store's, not this app's. */
export const TREE_BASES: Record<TreeScope, string> = {
  everything: "everything",
  "open-work": "open-work",
};

export const TREE_BASE = TREE_BASES.everything;

/**
 * Every record in the store, grouped the way the ribbon asked -- by the engine.
 *
 * All three of the ribbon's groupings are `kyo base run --group-by` (KYO-120). Before
 * that flag existed this function could delegate only the one grouping the saved base
 * happened to declare and ported `bases.grouping()` for the other two, which meant the
 * tree's answer changed *implementation* depending on which pill the reader pressed --
 * and the surface had to say so, because a rendering that implied the engine had
 * answered would be claiming a provenance it did not have. It now always has it: one
 * subprocess, one answer, and the `--group-by` on `base.command` is the receipt.
 *
 * That the app's port survives with the bubbles view as its caller is deliberate and is
 * not this function's business: bubbles regroups a `BaseResult` the renderer already
 * holds as the human clicks, and reaching this process for it would spend an IPC round
 * trip and a subprocess to call a function already in memory.
 */
export async function readRecordTree(
  project: ServedProject,
  grouping: TreeGrouping,
  scope: TreeScope = "everything",
): Promise<RecordTree> {
  const [base, board] = await Promise.all([
    // `groupingField`, not `grouping`: the ribbon's status pill is answered from
    // `standing`, the role word, and never from the stored `status` column. KYO-121,
    // and the reasoning is on `TREE_GROUPING_FIELDS` in `shared/protocol.ts`.
    //
    // `TREE_BASES[scope]`: the scope pill switches which saved base answers, so
    // "open work" means whatever `kyosei/bases.yml` says it means (KYO-122.c9).
    runBase(project, TREE_BASES[scope], groupingField(grouping)),
    readBoardClasses(project),
  ]);
  if (!base.ok) {
    return {
      ok: false,
      grouping,
      scope,
      groups: [],
      records: [],
      currency: base.currency,
      refusal: base.refusal,
      command: base.command,
    };
  }
  // `--group-by` was supplied, so the engine grouped: `groups` is null only for a run
  // with no binding at all, which this one cannot be. The fallback is an empty tree
  // rather than a re-grouping in the app -- there is one grouping rule here now, and it
  // is the engine's.
  const groups: BaseGroup[] = base.groups ?? [];
  return {
    ok: true,
    grouping,
    scope,
    groups,
    records: base.rows.map((row) => treeRecord(row, board)),
    currency: base.currency,
    refusal: null,
    command: base.command,
  };
}

function treeRecord(row: Record<string, unknown>, board: BoardClasses): TreeRecord {
  const id = text(row.id);
  return {
    id,
    kind: text(row.kind),
    status: text(row.status),
    standing: text(row.standing),
    scope: str(row.scope),
    title: text(row.title),
    gatesOpen: int(row.gates_open),
    gateTags: strings(row.gate_tags),
    gateTypes: strings(row.gate_types),
    boardClass: classOf(board, id),
  };
}

// ----------------------------------------------------------- one record, read-only

export async function readRecord(project: ServedProject, id: string): Promise<RecordDetail> {
  const args = ["show", id];
  const command = commandOf(args);
  const [result, board] = await Promise.all([
    readJson(project, args).catch((error: unknown) => ({
      code: 1,
      data: null,
      stderr: (error as Error).message,
    })),
    readBoardClasses(project),
  ]);
  // `kyo show --json` carries no `db_export_currency` envelope of its own, so the
  // currency for this surface is asked for separately rather than omitted: a record
  // page is a surface painting store facts, and KYO-74's obligation does not lapse
  // because the command that supplied the facts did not carry the verdict.
  const currency = await readCurrency(project);
  const { data, stderr, code } = result;
  if (data === null || str(data.id) === null) {
    return {
      ...blankRecord(id),
      currency,
      refusal: refusalFrom(data, stderr, code, command),
      command,
    };
  }
  return {
    ok: true,
    id: text(data.id),
    kind: text(data.kind),
    title: text(data.title),
    status: text(data.status),
    standing: text(data.standing),
    scope: str(data.scope),
    plan: str(data.plan),
    createdAt: text(data.created_at),
    updatedAt: text(data.updated_at),
    boardClass: classOf(board, text(data.id)),
    needs: board.needs.get(text(data.id)) ?? null,
    description: text(data.description),
    gates: rows(data.gates).map(gateView),
    claims: rows(data.claims).map(claimView),
    criteria: rows(data.criteria).map(criterionView),
    touches: rows(data.touches).map(touchView),
    links: rows(data.links).map(linkView),
    dispatches: dispatchViews(rows(data.dispatches), rows(data.reports)),
    coveredBy: coveringDispatchViews(rows(data.covered_by_dispatches)),
    currency,
    refusal: null,
    command,
  };
}

function blankRecord(id: string): RecordDetail {
  return {
    ok: false,
    id,
    kind: "",
    title: "",
    status: "",
    standing: "",
    scope: null,
    plan: null,
    createdAt: "",
    updatedAt: "",
    boardClass: "none",
    needs: null,
    description: "",
    gates: [],
    claims: [],
    criteria: [],
    touches: [],
    links: [],
    dispatches: [],
    coveredBy: [],
    currency: currencyFailure("not asked: the record could not be read"),
    refusal: null,
    command: "",
  };
}

function gateView(gate: Record<string, unknown>): GateView {
  return {
    id: text(gate.id),
    type: text(gate.type),
    detail: str(gate.do) ?? str(gate.detail),
    tags: strings(gate.tags),
    open: str(gate.cleared_at) === null,
    createdAt: text(gate.created_at),
    clearedAt: str(gate.cleared_at),
    clearedWhy: str(gate.cleared_why),
  };
}

function claimView(claim: Record<string, unknown>): ClaimView {
  return {
    id: text(claim.id),
    level: text(claim.level),
    method: text(claim.method),
    author: text(claim.author),
    statement: text(claim.statement),
    createdAt: text(claim.created_at),
    supersedes: str(claim.supersedes),
    supersededBy: str(claim.superseded_by),
  };
}

function criterionView(criterion: Record<string, unknown>): CriterionView {
  return {
    id: text(criterion.id),
    text: text(criterion.text),
    state: text(criterion.state),
    reason: str(criterion.reason),
    verifications: rows(criterion.verifications).map(
      (entry): VerificationView => ({
        id: text(entry.id),
        verdict: text(entry.verdict),
        method: text(entry.method),
        author: text(entry.author),
        createdAt: text(entry.created_at),
        reason: str(entry.reason),
      }),
    ),
  };
}

function touchView(touch: Record<string, unknown>): TouchView {
  return { id: text(touch.id), path: text(touch.path), symbol: str(touch.symbol) };
}

function linkView(link: Record<string, unknown>): LinkView {
  return {
    type: text(link.type),
    direction: text(link.direction),
    other: text(link.other),
  };
}

function reportView(report: Record<string, unknown>, current: boolean): ReportView {
  return {
    id: text(report.id),
    seq: int(report.seq),
    author: text(report.author),
    createdAt: text(report.created_at),
    workedFromSha: text(report.worked_from_sha),
    current,
    body: record(report.body) ?? {},
  };
}

/**
 * A dispatch and the report that counts (KYO-90).
 *
 * A dispatch can carry several reports -- an amended one supersedes its predecessor
 * and the earlier one stays readable -- and exactly one of them is current: the one
 * the dispatch's own `report_id` names. Picking "the newest" instead would be this
 * app deciding which account of a dispatch is authoritative, which is the store's
 * decision and is already recorded.
 *
 * KYO-296: `covers` rides here too, and this is the seam that decides what the record
 * page's `also formally covers` line says -- the one the terminal has printed all
 * along. It is a READ of `kyo show --json`'s own per-dispatch `covers`, which is the
 * CLI's `covers_json`: the relation is recorded on the dispatch at dispatch time, and
 * deriving it here would mean this page reading every other record looking for a way
 * back, over a document that holds exactly one. An absent key yields `[]` -- what an
 * older CLI than this build produces, and what the page draws no line for.
 */
export function dispatchViews(
  dispatches: Record<string, unknown>[],
  reports: Record<string, unknown>[],
): DispatchView[] {
  return dispatches.map((dispatch) => {
    const id = text(dispatch.id);
    const currentId = str(dispatch.report_id);
    const mine = reports.filter((report) => text(report.dispatch_id) === id);
    const current = mine.find((report) => text(report.id) === currentId) ?? null;
    return {
      id,
      seq: int(dispatch.seq),
      state: text(dispatch.state),
      branch: str(dispatch.branch),
      worktree: str(dispatch.worktree),
      workerModel: str(dispatch.worker_model),
      workedFromSha: text(dispatch.worked_from_sha),
      createdAt: text(dispatch.created_at),
      closedAt: str(dispatch.closed_at),
      terminatedWhy: str(dispatch.terminated_why),
      covers: strings(dispatch.covers),
      currentReport: current === null ? null : reportView(current, true),
      superseded: mine
        .filter((report) => text(report.id) !== currentId)
        .map((report) => reportView(report, false)),
    };
  });
}

/**
 * The dispatches on other records that formally cover this one (KYO-291).
 *
 * The CLI walked `covers_json` backwards and put the answer in `kyo show --json`'s
 * `covered_by_dispatches`; this shapes those rows and decides nothing. In particular
 * it does not go looking through `dispatches` for a `covers` array -- the covering
 * dispatch belongs to a record this page never read, so its row is not in this
 * document and could not be found here even if this file wanted to derive it.
 *
 * An absent key yields an empty list, which is what an older CLI than this build
 * produces: the page then draws no covered-by section, exactly as it does for the
 * overwhelming majority of records, rather than failing to render.
 */
export function coveringDispatchViews(covering: Record<string, unknown>[]): CoveringDispatchView[] {
  return covering.map((entry) => ({
    id: text(entry.id),
    state: text(entry.state),
    primaryRecordId: text(entry.primary_record_id),
    reportId: str(entry.report_id),
  }));
}

// --------------------------------------------------------------- session briefs

/** One `brief_runs` row as `kyo briefs --json` prints it. */
function briefRun(row: Record<string, unknown>): BriefRun {
  return {
    id: text(row.id),
    seq: int(row.seq),
    at: text(row.at),
    actor: text(row.actor),
    // `session_start` is null on every row written before the column existed, which
    // is "not recorded" and reads here as false rather than as a guess.
    sessionStart: row.session_start === true,
    lastEventSeq: int(row.last_event_seq),
    lastShiftNoteSeq: int(row.last_shift_note_seq),
  };
}

/** One shift note as `kyo shift-notes`/`kyo briefs` print it. */
function shiftNote(row: Record<string, unknown>): ShiftNoteView {
  return {
    id: text(row.id),
    seq: int(row.seq),
    author: text(row.author),
    createdAt: text(row.created_at),
    text: text(row.text),
    redactedAt: str(row.redacted_at),
    redactedWhy: str(row.redacted_why),
  };
}

/** One ledger entry as `kyo briefs <id> --json` prints it. */
function eventView(row: Record<string, unknown>): EventView {
  return {
    id: text(row.id),
    seq: int(row.seq),
    at: text(row.at),
    actor: text(row.actor),
    command: text(row.command),
    recordId: str(row.record_id),
  };
}

/** The CLI's sentence for a read that did not answer, or null when it did. */
function failureOf(result: KyoJson, command: string): string | null {
  if (result.code === 0 && result.data !== null && result.data.ok !== false) return null;
  return refusalFrom(result.data, result.stderr, result.code, command);
}

/**
 * The session briefs, newest first, as `kyo briefs --json` lists them (KYO-454).
 *
 * Not `kyo brief`: see `KYO_WRITING_COMMANDS`. `kyo briefs` is the read-only
 * listing the CLI grew so no viewer reads `brief_runs` by SQL.
 */
export async function readBriefs(project: ServedProject): Promise<BriefCatalogue> {
  const currency = await readCurrency(project);
  const args = ["briefs"];
  try {
    const result = await readJson(project, args);
    const problem = failureOf(result, commandOf(args));
    if (problem !== null) return { ok: false, briefs: [], currency, problem };
    return { ok: true, briefs: rows(result.data?.briefs).map(briefRun), currency, problem: null };
  } catch (error) {
    return { ok: false, briefs: [], currency, problem: (error as Error).message };
  }
}

/**
 * The newest shift note in the store (KYO-122.c12): `kyo shift-notes --latest`.
 *
 * Not a brief's window, and that is the point: a note written after the last brief --
 * exactly the case the daily sheet's split view is for, the handoff from the session
 * that has just ended -- appears in no brief's window.
 */
export async function readLatestShiftNote(project: ServedProject): Promise<LatestShiftNote> {
  const args = ["shift-notes", "--latest"];
  try {
    const result = await readJson(project, args);
    const problem = failureOf(result, commandOf(args));
    if (problem !== null) return { ok: false, note: null, problem };
    const row = record(result.data?.note);
    return { ok: true, note: row === null ? null : shiftNote(row), problem: null };
  } catch (error) {
    return { ok: false, note: null, problem: (error as Error).message };
  }
}

/**
 * Every shift note, newest first, with the handoffs marked (KYO-248): `kyo
 * shift-notes --json`, whose `handoff_for` is the session-start brief that stood at
 * each note -- the CLI's join, carried rather than re-derived here.
 */
export async function readShiftNotes(project: ServedProject): Promise<ShiftNotesCatalogue> {
  const args = ["shift-notes"];
  try {
    const result = await readJson(project, args);
    const problem = failureOf(result, commandOf(args));
    if (problem !== null) return { ok: false, notes: [], problem };
    const notes = rows(result.data?.notes).map((row) => ({
      ...shiftNote(row),
      handoffFor: str(row.handoff_for),
    }));
    return { ok: true, notes, problem: null };
  } catch (error) {
    return { ok: false, notes: [], problem: (error as Error).message };
  }
}

// ================================================ the v3 read model (KYO-231)
//
// Four queries the v3 tabs consume, and the sentence every one of them is written to
// keep true: **the tab renders what it is given and derives nothing.** The obligation
// collapse below is the CLI's own (KYO-219's `collapse_obligations`, read out of the
// envelope rather than ported); the awaiting-human types are the CLI's gate
// vocabulary; the activity classes are the brief's. Nothing here decides what any of
// them mean.

/**
 * The gate types that are waiting on a person.
 *
 * These are TYPES, not tags. KYO-231's own description calls them "gates whose tag is
 * `awaiting:human-run` or `awaiting:human-review`", and the store disagrees: both are
 * members of `contracts.GATE_TYPES`, and `tags_json` holds the per-project vocabulary
 * from `kyosei/gate-tags.yml` (`ui-review`, `deploy-risk`) which is a different axis --
 * the type says what KIND of waiting this is, the tag says which thing is being
 * waited on (KYO-102/KYO-46). Matching on tags would have returned nothing in this
 * store, where no gate carries a tag by either name.
 */
export const AWAITING_HUMAN_TYPES: readonly string[] = [
  "awaiting:human-run",
  "awaiting:human-review",
];

/**
 * How many finished dispatches the activity view shows beside the running ones.
 *
 * Measured against this store rather than picked: on 2026-09-11 it held 139 reported
 * and 5 terminated dispatches, of which 11 closed in the previous fourteen days. So a
 * dozen is "roughly the last fortnight of this project's work", which is the window a
 * controller coming back to a session actually reads, and 144 rows is not.
 */
export const ACTIVITY_FINISHED_DEFAULT = 12;

function note(row: Record<string, unknown>): NoteView {
  return {
    id: text(row.id),
    seq: int(row.seq),
    text: text(row.text),
    createdAt: text(row.created_at),
    supersededBy: str(row.superseded_by),
    supersededAt: str(row.superseded_at),
    redactedAt: str(row.redacted_at),
    redactedWhy: str(row.redacted_why),
  };
}

function evidence(row: Record<string, unknown>): EvidenceView {
  return {
    id: text(row.id),
    seq: int(row.seq),
    sha256: text(row.sha256),
    author: text(row.author),
    createdAt: text(row.created_at),
    supportsId: str(row.supports_id),
    text: text(row.text),
    redactedAt: str(row.redacted_at),
    redactedWhy: str(row.redacted_why),
    // KYO-587: present only on a recx search artifact; the CLI recognised it.
    untrustedNotice: Array.isArray(row.untrusted_notice) ? strings(row.untrusted_notice) : null,
    untrustedText: str(row.untrusted_text),
  };
}

function flag(row: Record<string, unknown>): FlagView {
  return {
    id: text(row.id),
    seq: int(row.seq),
    flagType: text(row.flag_type),
    detail: text(row.detail),
    createdAt: text(row.created_at),
    resolvedAt: str(row.resolved_at),
    resolvedWhy: str(row.resolved_why),
    otherRecordId: str(row.other_record_id),
    otherCriterionId: str(row.other_criterion_id),
  };
}

function docRef(row: Record<string, unknown>): DocRefView {
  return {
    id: text(row.id),
    seq: int(row.seq),
    docPath: text(row.doc_path),
    section: str(row.section),
    note: str(row.note),
  };
}

function member(row: Record<string, unknown>): MemberView {
  return {
    id: text(row.id),
    kind: text(row.kind),
    status: text(row.status),
    standing: text(row.standing),
    title: text(row.title),
  };
}

/**
 * The obligation collapse, re-typed from the envelope `kyo show --json` already carries.
 *
 * REUSE and not a port, which is what KYO-231.c1 offers as the first of its two
 * options and what law 3 argues for: `docs_check.collapse_obligations` computes this
 * (KYO-219.c3 built it so the desktop viewer could read the same grouping rather than
 * deriving a second one), the CLI puts the result in `docs_obligation_groups`, and
 * this maps snake_case onto camelCase and does nothing else. There is no second
 * implementation of the grouping in this codebase, so there is nothing for the two to
 * disagree about.
 */
export function obligationsFrom(value: unknown): ObligationCollapse {
  const data = record(value);
  if (data === null) return { total: 0, open: 0, countsByState: [], groups: [] };
  return {
    total: int(data.total),
    open: int(data.open),
    countsByState: rows(data.counts_by_state).map((entry) => ({
      state: text(entry.state),
      count: int(entry.count),
    })),
    groups: rows(data.groups).map((group) => ({
      state: text(group.state),
      method: str(group.method),
      author: str(group.author),
      settledAt: str(group.settled_at),
      settledWhy: str(group.settled_why),
      count: int(group.count),
      obligationIds: strings(group.obligation_ids),
      causes: strings(group.causes),
      dispatchIds: strings(group.dispatch_ids),
      details: rows(group.details).map((detail) => ({
        detail: text(detail.detail),
        count: int(detail.count),
        obligationIds: strings(detail.obligation_ids),
        docPaths: strings(detail.doc_paths),
        sections: strings(detail.sections),
      })),
    })),
  };
}

/** By seq, which for a child object is id order -- ids are allocation counters (law 4). */
function bySeq<T extends { seq: number }>(items: T[]): T[] {
  return [...items].sort((a, b) => a.seq - b.seq);
}

/**
 * Every child object of one record, in id order, with the description and the links.
 *
 * `kyo show <id> --json` and nothing else, because that envelope already carries every
 * one of them AND the obligation collapse. A direct SQLite read would be a dozen
 * SELECTs plus a TypeScript re-derivation of the collapse and of the role word
 * (`standing`), which is two rules with two implementations.
 *
 * The gates carry their record's claims beside them (KYO-88): a gate and the claim that
 * says it may already be answered are the pair a reader is looking for, and putting
 * them together here is what keeps every tab that draws a gate from doing the join
 * itself.
 */
export async function readRecordDocument(
  project: ServedProject,
  id: string,
): Promise<RecordDocument> {
  const args = ["show", id];
  const command = commandOf(args);
  const [result, currency] = await Promise.all([
    readJson(project, args).catch((error: unknown) => ({
      code: 1,
      data: null,
      stderr: (error as Error).message,
    })),
    readCurrency(project),
  ]);
  const { data, stderr, code } = result;
  const empty: RecordDocument = {
    ok: false,
    id,
    kind: "",
    title: "",
    status: "",
    standing: "",
    scope: null,
    plan: null,
    createdAt: "",
    updatedAt: "",
    description: "",
    wontDoReason: null,
    criteria: [],
    gates: [],
    claims: [],
    notes: [],
    evidence: [],
    flags: [],
    dispatches: [],
    coveredBy: [],
    touches: [],
    links: [],
    docRefs: [],
    obligations: { total: 0, open: 0, countsByState: [], groups: [] },
    members: [],
    currency,
    refusal: null,
    command,
  };
  if (data === null || str(data.id) === null) {
    return { ...empty, refusal: refusalFrom(data, stderr, code, command) };
  }
  const claims = rows(data.claims).map(claimView);
  return {
    ...empty,
    ok: true,
    id: text(data.id),
    kind: text(data.kind),
    title: text(data.title),
    status: text(data.status),
    // The role word, which for a knowledge-kind record is `current` or `void` rather
    // than a work status. The CLI computes it (`contracts`), this carries it (KYO-121).
    standing: text(data.standing),
    scope: str(data.scope),
    plan: str(data.plan),
    createdAt: text(data.created_at),
    updatedAt: text(data.updated_at),
    description: text(data.description),
    wontDoReason: str(data.wont_do_reason),
    criteria: rows(data.criteria).map(criterionView),
    // The claims stand beside the gate (KYO-88): a gate and the claim saying it may
    // already be answered are the pair a reader is looking for, and joining them here
    // is what keeps every tab that draws a gate from doing the join itself.
    gates: bySeq(
      rows(data.gates).map(
        (gate): GateDocument => ({
          ...gateView(gate),
          seq: int(gate.seq),
          claims,
        }),
      ),
    ),
    claims,
    notes: bySeq(rows(data.notes).map(note)),
    evidence: bySeq(rows(data.evidence_artifacts).map(evidence)),
    flags: bySeq(rows(data.flags).map(flag)),
    dispatches: dispatchViews(rows(data.dispatches), rows(data.reports)),
    // KYO-291. Read, never derived: the covering dispatch hangs off a record this
    // page has not read, so the CLI's backwards walk over `covers_json` is the only
    // thing that can know it exists.
    coveredBy: coveringDispatchViews(rows(data.covered_by_dispatches)),
    touches: rows(data.touches).map(touchView),
    links: rows(data.links).map(linkView),
    docRefs: bySeq(rows(data.doc_refs).map(docRef)),
    obligations: obligationsFrom(data.docs_obligation_groups),
    members: rows(data.members).map(member),
  };
}

/**
 * Every open gate waiting on a human, across every record status, and the count.
 *
 * Across every status is the whole point: a gate on a `working` record and a gate on
 * an `open` one are the same claim on a person's attention, and a view that only
 * looked at open work would hide half of them. There is no read-only command that
 * asks this, so it is a direct read -- one SELECT, joined to the record for its title.
 */
export async function readAwaitingHuman(project: ServedProject): Promise<AwaitingHuman> {
  const empty = (problem: string): AwaitingHuman => ({
    ok: false,
    gates: [],
    count: 0,
    types: [...AWAITING_HUMAN_TYPES],
    problem,
  });
  // `kyo gates --type …` (KYO-454): the census of open gates across every record
  // status, the types passed in rather than written into a query here.
  const args = ["gates", ...AWAITING_HUMAN_TYPES.flatMap((type) => ["--type", type])];
  try {
    const result = await readJson(project, args);
    const problem = failureOf(result, commandOf(args));
    if (problem !== null) return empty(problem);
    const gates = rows(result.data?.gates).map(
      (row): AwaitingGate => ({
        recordId: text(row.record_id),
        recordTitle: text(row.record_title),
        recordKind: text(row.record_kind),
        recordStatus: text(row.record_status),
        gate: {
          id: text(row.id),
          seq: int(row.seq),
          type: text(row.type),
          detail: str(row.do),
          tags: strings(row.tags),
          open: true,
          createdAt: text(row.created_at),
          clearedAt: null,
          clearedWhy: null,
          // Claims are attached by the record document, not here: this view's job is
          // "what is waiting", and one `kyo show` per gate to decorate a badge count
          // would be a request per row.
          claims: [],
        },
      }),
    );
    return {
      ok: true,
      gates,
      count: gates.length,
      types: [...AWAITING_HUMAN_TYPES],
      problem: null,
    };
  } catch (error) {
    return empty((error as Error).message);
  }
}

/**
 * The records list: every record, searched by id or title, or run through a saved base.
 *
 * Two sources and the choice is the caller's. With a base named, the CLI's own engine
 * answers and the rows carry `standing`; with none, the store is read directly. The
 * search is applied on top either way and is a plain case-insensitive substring over
 * the id and the title -- deliberately not a query language, because a second query
 * language beside `kyosei/bases.yml`'s is the thing KYO-45 exists to prevent.
 *
 * Every row also carries the class `kyo next --json` puts it in (KYO-290), stamped
 * here from `readBoardClasses` rather than in the renderer: "dispatchable" is the
 * CLI's verdict, and a records browser that re-derived it from status and criteria
 * would be a second board disagreeing with the first one the day either changed.
 */
export async function readRecordsList(
  project: ServedProject,
  options: { query?: string; base?: string } = {},
): Promise<RecordsList> {
  const query = (options.query ?? "").trim();
  const base = options.base ?? null;
  const matches = (row: RecordsListRow): boolean =>
    query === "" ||
    row.id.toLowerCase().includes(query.toLowerCase()) ||
    row.title.toLowerCase().includes(query.toLowerCase());
  // In parallel with whichever source answers below: the board is a second subprocess
  // and there is no reason for it to wait on the first. An unreadable board is not a
  // failed list -- `board.problem` travels beside the rows and the filter says so.
  const board = readBoardClasses(project);
  const catalogue = await readBases(project);
  if (base !== null && base !== "") {
    const run = await runBase(project, base);
    const classes = await board;
    const listed = run.rows.map(
      (row): RecordsListRow => ({
        id: text(row.id),
        kind: text(row.kind),
        title: text(row.title),
        status: text(row.status),
        scope: str(row.scope),
        standing: str(row.standing),
        createdAt: str(row.created_at) ?? "",
        updatedAt: str(row.updated_at) ?? "",
        boardClass: classOf(classes, text(row.id)),
      }),
    );
    return {
      ok: run.ok,
      rows: listed.filter(matches),
      query,
      base,
      bases: catalogue.bases,
      currency: run.currency,
      problem: run.refusal,
      boardProblem: classes.problem,
    };
  }
  // With no base, `kyo list --json` is the list (KYO-459: the app reads no table of
  // its own). It carries every record with its role word (`standing`), and it is the
  // same envelope the currency verdict comes out of, so one request answers both.
  const args = ["list"];
  let listed: KyoJson;
  try {
    listed = await readJson(project, args);
  } catch (error) {
    listed = { code: 1, data: null, stderr: (error as Error).message };
  }
  const currency = await readCurrency(project, listed);
  const classes = await board;
  const problem = failureOf(listed, commandOf(args));
  if (problem !== null) {
    return {
      ok: false,
      rows: [],
      query,
      base: null,
      bases: catalogue.bases,
      currency,
      problem,
      boardProblem: classes.problem,
    };
  }
  const listedRows = rows(listed.data?.records).map(
    (row): RecordsListRow => ({
      id: text(row.id),
      kind: text(row.kind),
      title: text(row.title),
      status: text(row.status),
      scope: str(row.scope),
      // The CLI's role word, carried and never derived here (KYO-365).
      standing: str(row.standing),
      // The record's own stamps, which `kyo list --json` rows carry since KYO-467, so
      // the Created and Modified sorts work with no base selected. A CLI from before
      // it sends none, and the sort falls back to id order (allocation order).
      createdAt: str(row.created_at) ?? "",
      updatedAt: str(row.updated_at) ?? "",
      boardClass: classOf(classes, text(row.id)),
    }),
  );
  return {
    ok: true,
    rows: listedRows.filter(matches),
    query,
    base: null,
    bases: catalogue.bases,
    currency,
    problem: null,
    boardProblem: classes.problem,
  };
}

/**
 * The latest session brief, as sections, with every id preserved.
 *
 * Read out of the store rather than generated, and that is forced rather than chosen:
 * `kyo brief` INSERTs a row in `brief_runs` on every run, so the one command whose
 * subject is briefs is the one this app must never call (`KYO_WRITING_COMMANDS`). What
 * the store keeps about a past brief is a marker plus two ledger positions, and the
 * shift notes and events standing in that window; this is those, arranged as named
 * sections with the ids intact so a renderer can link every row.
 *
 * Nothing here regenerates the brief's prose. The store never kept it, and rendering a
 * freshly-computed board under a date three days ago would be this app inventing a
 * document and dating it.
 */
export async function readLatestBrief(project: ServedProject): Promise<BriefDocumentView> {
  const currency = await readCurrency(project);
  const empty = (problem: string | null): BriefDocumentView => ({
    ok: problem === null,
    run: null,
    previousBriefId: null,
    sections: [],
    currency,
    problem,
  });
  const args = ["briefs", "--latest"];
  try {
    const result = await readJson(project, args);
    const problem = failureOf(result, commandOf(args));
    if (problem !== null) return empty(problem);
    const runRow = record(result.data?.run);
    if (runRow === null) {
      return empty("this store holds no session brief yet; `kyo brief` writes the first");
    }
    const notes = rows(result.data?.shift_notes).map(shiftNote);
    const events = rows(result.data?.events).map(eventView);
    return {
      ok: true,
      run: briefRun(runRow),
      previousBriefId: str(result.data?.previous_brief_id),
      sections: [
        {
          key: "shift-notes",
          title: "Shift notes since the previous brief",
          items: notes.map((entry) => ({
            id: entry.id,
            kind: "shift-note",
            recordId: null,
            at: entry.createdAt,
            actor: entry.author,
            // A redacted note keeps its id and its marker and loses its prose, which
            // is what redaction IS -- a row that vanished would hide that it happened.
            text: entry.redactedAt === null ? entry.text : `[redacted: ${entry.redactedWhy ?? ""}]`,
          })),
        },
        {
          key: "events",
          title: "What the ledger recorded in that window",
          items: events.map((entry) => ({
            id: entry.id,
            kind: "event",
            recordId: entry.recordId,
            at: entry.at,
            actor: entry.actor,
            text: entry.command,
          })),
        },
      ],
      currency,
      problem: null,
    };
  } catch (error) {
    return empty((error as Error).message);
  }
}

/**
 * One session brief, as the store kept it: `kyo briefs <id> --json` (KYO-454).
 *
 * A brief run is a marker plus two ledger positions: the last event and the last
 * shift note it closed over. That -- with the shift notes standing at the time,
 * verbatim, and the ledger entries since the previous brief -- is what the store
 * holds about a past brief. Nothing here regenerates the brief's prose: the store
 * never kept it, and rendering a freshly-computed board under a date three days ago
 * would be this app inventing a document and dating it.
 */
export async function readBrief(project: ServedProject, id: string): Promise<BriefDocument> {
  const empty = (problem: string): BriefDocument => ({
    ok: false,
    run: null,
    previousBriefId: null,
    shiftNotes: [],
    events: [],
    problem,
  });
  if (id === "" || id.startsWith("-")) return empty(`${id || "(empty)"} is not a session brief id`);
  const args = ["briefs", id];
  try {
    const result = await readJson(project, args);
    const problem = failureOf(result, commandOf(args));
    if (problem !== null) return empty(problem);
    const runRow = record(result.data?.run);
    if (runRow === null) return empty(`${id} is not a session brief in this store`);
    return {
      ok: true,
      run: briefRun(runRow),
      previousBriefId: str(result.data?.previous_brief_id),
      shiftNotes: rows(result.data?.shift_notes).map(shiftNote),
      events: rows(result.data?.events).map(eventView),
      problem: null,
    };
  } catch (error) {
    return empty((error as Error).message);
  }
}
