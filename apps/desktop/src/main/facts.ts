// The two live reads the shell shows: where the store is and what it holds, and
// which side of the db/export seam moved.
//
// Records mode's own reads used to live at the bottom of this file; KYO-113 moved
// them to `records.ts` unchanged, so that this file has one subject again. The two
// things they still ask of it are `locateProject` (which store, for a direct read)
// and `currencyFrom`/`readCurrency` (one lookup table for every surface, so four
// renderings cannot disagree about what the CLI said).
//
// The division of labour is KYO-72's, and it is not a style choice:
//
//   the store's *contents*   read straight out of SQLite, read-only (store.ts).
//   where the store *is*     asked of `kyo`, never re-derived here.
//   the currency direction   asked of `kyo`, never recomputed here.
//
// The middle line is the one worth stating twice. `kyosei.config.resolve_layout`
// is a real rule -- walk up for `kyosei/config.yml`, or follow a `.kyosei` link to
// a store in its own repository, deepest match wins -- and a TypeScript copy of it
// would be a second implementation of where a project lives, in a codebase whose
// law 3 exists to prevent exactly that. So the app asks.
//
// What it asks with used to be the weak point of this file, and KYO-184 removed it.
// The command is `kyo where --json`, whose *subject* is these two questions and
// nothing else: where the project is (`layout`, `store_root`, `code_root`,
// `store_link`, `branch`) and whether the build behind that `kyo` can open the
// store it just located (`can_open`, `store_problem`, and KYO-178's
// `schema_mismatch` payload carried as stored when that is why). One invocation,
// one envelope, exit 0 whatever the answer -- a store this build cannot open is a
// question answered, not a command that failed.
//
// What it replaced, since the shape of this file is the record of it: `kyo
// check-branch --json` was mined for the location with its exit code deliberately
// ignored (exit 2 there means "this branch diffs the store", true and irrelevant
// here), `kyo docs check --json` stood behind it as a second locator because
// `check-branch` asks git for the branch before it reports anything and so failed
// outright outside a repository, and `kyo list --json` was run as an openability
// probe because opening the store is what makes the schema refusal arrive. Three
// borrowings, none of which was about the question, each doing real work nobody
// asked for -- a store diff, a documents walk, and a render of every record -- on
// the path between a human opening a window and the window saying anything.
// `kyo where` does none of it. `kyo list --json` stays, for the one question it IS
// about: the db/export currency direction.

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  type CurrencyFacts,
  type CurrencyState,
  type ProjectEntry,
  type ProjectSource,
  type RemoteFacts,
  type SchemaMismatchFacts,
  type SchemaStanding,
  STORE_CURRENCY_STATES,
  type StoreFacts,
} from "../shared/protocol";
import { type KyoJson, type ServedEntry, type ServedProject, sameServer } from "./kyoserve";
import type { KnownProject } from "./links";

/**
 * The one command this file locates with, and the one it asks about openability.
 *
 * They are the same command since KYO-184, which is the whole of that record: `kyo
 * where`'s subject is "where is this project, and can this build open its store",
 * so both answers arrive in one envelope from one interpreter start. It opens no
 * git repository to place a project and renders no record to decide whether the
 * store opens.
 */
export const LOCATE_COMMAND = "kyo where --json";
/** The command the currency direction comes out of. */
export const CURRENCY_COMMAND = "kyo list --json";

export interface ProjectLocation {
  layout: string | null;
  storeRoot: string | null;
  repoRoot: string | null;
  /** Null outside a git repository, and on a detached HEAD -- never invented. */
  branch: string | null;
  /** Why nothing could be located here. Null when something was. */
  problem: string | null;
  // ------------------------------------------------------------ KYO-184
  // The rest of the same envelope. Before this record these three came from a
  // SECOND invocation (`kyo list --json`, run as a probe); they arrive here now
  // because the command that places the project is the command that tries to open
  // its store, and one answer to two questions cannot disagree with itself.
  /** The record-id prefix out of the store's own `config.yml`. */
  project: string | null;
  /** Where the CLI says the database is -- not re-derived from `storeRoot`. */
  dbPath: string | null;
  /** Did the build behind that `kyo` open this store? */
  canOpen: boolean;
  /** The CLI's own sentence about why it did not. Null when it did. */
  storeProblem: string | null;
  /** KYO-178's payload, when a schema mismatch is why. */
  mismatch: SchemaMismatchFacts | null;
  /**
   * The schema this build of the CLI speaks (`build_schema_version`). When `canOpen`
   * it is the store's own too: the CLI opened the store under it.
   */
  buildSchemaVersion: number | null;
}

const NOWHERE: ProjectLocation = {
  layout: null,
  storeRoot: null,
  repoRoot: null,
  branch: null,
  problem: null,
  project: null,
  dbPath: null,
  canOpen: false,
  storeProblem: null,
  mismatch: null,
  buildSchemaVersion: null,
};

function stringField(data: Record<string, unknown>, key: string): string | null {
  const value = data[key];
  return typeof value === "string" && value !== "" ? value : null;
}

/**
 * Ask the CLI where this project is, and whether the build behind it can open the
 * store it found.
 *
 * One invocation, whatever the answer. `kyo where` exits 0 for "there is no project
 * here" and for "there is one and I cannot open it" alike -- both are its subject --
 * so there is no exit code to interpret and no second command to fall back to. What
 * is still checked is that a payload arrived at all: a `kyo` that could not be run,
 * or that printed nothing, is a fact about this app's own runtime rather than about
 * any project, and it is reported as such.
 */
export async function locateProject(project: ServedProject): Promise<ProjectLocation> {
  const cwd = project.root;
  const { data, stderr } = await project.json(["where"]);
  if (data === null || (data.ok === false && data.refused_by === "kyo serve")) {
    return {
      ...NOWHERE,
      problem:
        data === null
          ? `\`${LOCATE_COMMAND}\` produced no JSON for ${cwd}: ${stderr.trim() || "(no output)"}`
          : stderr,
    };
  }
  const layout = stringField(data, "layout");
  const storeRoot = stringField(data, "store_root");
  const storeProblem = stringField(data, "store_problem");
  if (storeRoot === null) {
    return {
      ...NOWHERE,
      layout,
      // The CLI's own sentence, which names what `resolve_layout` looked for --
      // `kyosei/config.yml`, and a `.kyosei` link to a store elsewhere. A paraphrase
      // composed here would be a second statement of a rule this app deliberately
      // does not implement (law 3).
      problem: storeProblem ?? `no Kyosei project is configured at or above ${cwd}`,
      storeProblem,
    };
  }
  return {
    layout,
    storeRoot,
    repoRoot: stringField(data, "code_root"),
    branch: stringField(data, "branch"),
    problem: null,
    project: stringField(data, "project_prefix"),
    dbPath: stringField(data, "db_path"),
    canOpen: data.can_open === true,
    storeProblem,
    // Carried through `schemaMismatchFrom` for the mapping only; every field in it
    // is the CLI's, and `kyo where` in turn carries `SchemaMismatch`'s own payload
    // rather than a re-derived one (KYO-184.c2), so nothing between the comparison
    // in `store.open_store` and the screen recomputes any of it.
    mismatch: schemaMismatchFrom(data.schema_mismatch, storeProblem ?? "", LOCATE_COMMAND),
    buildSchemaVersion: intField(data, "build_schema_version"),
  };
}

function failed(problem: string, partial: Partial<StoreFacts> = {}): StoreFacts {
  return {
    ok: false,
    project: null,
    branch: null,
    records: null,
    schemaVersion: null,
    layout: null,
    storeRoot: null,
    dbPath: null,
    ...partial,
    problem,
  };
}

/**
 * The title bar's identity line and the status bar's schema version.
 *
 * Every number here is the CLI's, asked of `kyo serve` (KYO-459): the record count is
 * `kyo list --json`'s total, and the schema is the one the CLI opened the store under
 * (`kyo where`'s `build_schema_version`, which is the store's own whenever `can_open`).
 * When the store could not be opened this returns the reason rather than a zero,
 * because "0 records" and "I cannot see the records" are different claims and only one
 * of them is true.
 */
export async function readStoreFacts(
  project: ServedProject,
  located?: ProjectLocation,
  listed?: KyoJson,
): Promise<StoreFacts> {
  let location: ProjectLocation;
  try {
    // KYO-184: the caller may already hold this answer. `main.ts`'s `facts` handler
    // does -- it asks `kyo where` once and hands the same envelope to this and to
    // the project probe. Asking again would be a second read of a store that can
    // change between them, reported side by side as one moment.
    location = located ?? (await locateProject(project));
  } catch (error) {
    return failed(`could not ask ${LOCATE_COMMAND}: ${(error as Error).message}`);
  }
  if (location.storeRoot === null) {
    return failed(location.problem ?? "no Kyosei project found", { layout: location.layout });
  }
  const partial = {
    layout: location.layout,
    storeRoot: location.storeRoot,
    dbPath: location.dbPath,
    branch: location.branch,
    project: location.project,
  };
  if (!location.canOpen) {
    return failed(location.storeProblem ?? "the CLI could not open this store", partial);
  }
  let list: KyoJson;
  try {
    list = listed ?? (await project.json(["list"]));
  } catch (error) {
    return failed(`could not ask ${CURRENCY_COMMAND}: ${(error as Error).message}`, partial);
  }
  const summary = list.data?.summary as Record<string, unknown> | undefined;
  const total = typeof summary?.total === "number" ? summary.total : null;
  if (list.code !== 0 || total === null) {
    const said = typeof list.data?.error === "string" ? list.data.error : list.stderr.trim();
    return failed(
      `\`${CURRENCY_COMMAND}\` did not count the records: ${said || `exit ${list.code}`}`,
      partial,
    );
  }
  return {
    ok: true,
    ...partial,
    records: total,
    schemaVersion: location.buildSchemaVersion,
    problem: null,
  };
}

// ------------------------------------------------------------------- currency

/**
 * The CLI's `direction` string mapped onto the state the status bar renders.
 *
 * A lookup table and nothing else. There is no comparison of id counters or record
 * sets anywhere in this app: `kyosei.currency` performs it, `kyo list --json`
 * carries the verdict in `db_export_currency`, and this turns that verdict into a
 * label. A JavaScript reimplementation would be a second answer to a question the
 * tool already answers -- the exact failure KYO-74 was filed about, one layer out.
 */
export const DIRECTION_TO_STATE: Readonly<Record<string, CurrencyState>> = {
  "in-step": "current",
  "db-ahead": "database-ahead",
  "export-ahead": "export-ahead",
  "cannot-tell": "cannot-tell",
};

/** What the status bar prints for each state. */
export const STATE_LABEL: Readonly<Record<CurrencyState, string>> = {
  current: "store current",
  "database-ahead": "database ahead",
  "export-ahead": "export ahead",
  "cannot-tell": "currency: cannot tell",
  unrecognised: "currency: unrecognised",
  unavailable: "currency: unavailable",
};

/** The four states that are verdicts about the store rather than about the app. */
export function isStoreVerdict(state: CurrencyState): boolean {
  return STORE_CURRENCY_STATES.includes(state);
}

/** A currency verdict this app never got to ask for, carrying why. */
export function currencyFailure(problem: string): CurrencyFacts {
  return {
    state: "unavailable",
    direction: null,
    says: "",
    remedy: "",
    from: CURRENCY_COMMAND,
    problem,
  };
}

/**
 * Poll the store's currency direction through the CLI.
 *
 * `kyo list --json` because its envelope carries `db_export_currency` and it is on
 * the read path the tool already budgets for -- one small JSON read, one directory
 * listing and two indexed selects behind it (`kyosei.currency`'s own constraint).
 * `kyo base run --json` carries the same field, but this store saves no bases and
 * `kyosei/bases.yml` does not exist here, so a Base-backed poll would need a config
 * file written into a store this app must not write to.
 */
export async function readCurrency(
  project: ServedProject,
  listed?: KyoJson,
): Promise<CurrencyFacts> {
  let result: KyoJson;
  try {
    result = listed ?? (await project.json(["list"]));
  } catch (error) {
    return currencyFailure(`could not ask ${CURRENCY_COMMAND}: ${(error as Error).message}`);
  }
  return currencyFrom(result.data?.db_export_currency, CURRENCY_COMMAND, {
    detail: result.stderr.trim() || `exit ${result.code}`,
  });
}

/**
 * Turn one `db_export_currency` envelope into the verdict a surface renders.
 *
 * Split out of `readCurrency` for KYO-112: every records-mode surface that paints
 * store facts owes the reader all three directions plus "cannot tell" (KYO-74),
 * and several of the commands behind those surfaces carry the envelope in their
 * own payload. Mapping it here means one lookup table serves the status bar, the
 * Base view, the folder tree and the record editor -- rather than four, which is
 * where they would first disagree about what the CLI said.
 */
export function currencyFrom(
  seam: unknown,
  from: string,
  options: { detail?: string } = {},
): CurrencyFacts {
  const failure = (problem: string): CurrencyFacts => ({
    state: "unavailable",
    direction: null,
    says: "",
    remedy: "",
    from,
    problem,
  });
  if (seam === undefined || seam === null || typeof seam !== "object") {
    const detail = options.detail ?? "no payload";
    return failure(`\`${from}\` carried no db_export_currency envelope (${detail})`);
  }
  const payload = seam as Record<string, unknown>;
  const direction = typeof payload.direction === "string" ? payload.direction : null;
  const says = typeof payload.says === "string" ? payload.says : "";
  const remedy = typeof payload.remedy === "string" ? payload.remedy : "";
  if (direction === null) return failure(`\`${from}\` reported a seam with no direction`);
  const state = DIRECTION_TO_STATE[direction];
  if (state === undefined) {
    // Not folded into `cannot-tell`: that is the CLI's own verdict for "both sides
    // moved", and claiming it because this build does not recognise a word would be
    // this app asserting a store state nothing observed.
    return {
      state: "unrecognised",
      direction,
      says,
      remedy,
      from,
      problem:
        `\`${from}\` reported direction ${JSON.stringify(direction)}, which this ` +
        "build of the app does not know; the CLI is newer than the app",
    };
  }
  return { state, direction, says, remedy, from, problem: null };
}

// ============================================== which project (KYO-175)
//
// Three things, and they are here rather than in `main/main.ts` for the reason the
// header of this file gives for everything else in it: none of them needs Electron,
// so all three are exercisable from a plain Node check without launching an app.
// `main.ts` supplies the two facts only it holds -- the environment, and where
// `app.getPath("userData")` is -- and this file decides what follows from them.
//
//   the precedence      which of three candidate roots wins, and why.
//   the probe           does a candidate hold a project, and does its store open?
//   the app-local state the chosen project and the recents, remembered between
//                       launches. App-local and never in any store: KYO-79
//                       decision 4 is untouched -- nothing about the chosen project
//                       is written into the project.

/**
 * Decide which project the app is looking at, from the three places a root can
 * come from.
 *
 * Fixed order and stated: environment, then the in-app choice, then the checkout.
 * `KYOSEI_PROJECT_ROOT` is first and stays first (KYO-175.c8). It is what the
 * Playwright specs drive to open a fixture store, and it is the same capability a
 * person launching the app has, not a test-only back door -- a remembered
 * last-opened project that outranked it would break every check and would do it by
 * making the app ignore an explicit instruction from whoever launched it.
 *
 * A pure function of its inputs, and it must stay one: `projectRoot()` in `main.ts`
 * calls this on EVERY invocation rather than once, because that is what makes the
 * schema-mismatch state re-checkable in place rather than a restart (KYO-79
 * decision 5, KYO-175.c3). Hoisting the answer into a constant is the one change
 * that would quietly kill that criterion while every check still passed.
 */
export interface ProjectRootInputs {
  /** `process.env.KYOSEI_PROJECT_ROOT`, whatever it is -- unset, empty or a path. */
  environment: string | undefined;
  /**
   * The in-app choice for this session, or the remembered one, or null -- with the kyo
   * serve it was read through (KYO-465), since a root alone names a different project
   * on every machine that has a directory at that path.
   */
  chosen: RememberedProject | null;
  /** The checkout the app was loaded from (KYO-79 decision 3). */
  checkout: string;
}

export function resolveProjectRoot(inputs: ProjectRootInputs): {
  root: string;
  source: ProjectSource;
  /** The kyo serve the root is a path on; null for this machine's own (KYO-465). */
  connection: RemoteFacts | null;
} {
  // Blank-but-set is treated as unset, exactly as it was before this record: an
  // empty `KYOSEI_PROJECT_ROOT` would otherwise run `kyo` in the process's own
  // working directory, which is not a project anybody named.
  const environment = inputs.environment;
  if (environment !== undefined && environment.trim() !== "") {
    return { root: environment, source: "environment", connection: null };
  }
  const chosen = inputs.chosen;
  if (chosen !== null && chosen.root.trim() !== "") {
    // KYO-465: a choice made through another machine's kyo serve comes back THROUGH it.
    // Dropping the connection here is exactly the defect: the same path on this disk is
    // an older local copy of the project, opened with nothing saying so.
    return chosen.connection === null
      ? { root: chosen.root, source: "chosen", connection: null }
      : { root: chosen.root, source: "remote", connection: { ...chosen.connection } };
  }
  return { root: inputs.checkout, source: "checkout", connection: null };
}

/** Is `KYOSEI_PROJECT_ROOT` pinning the app, whatever else has been chosen? */
export function environmentRootOf(environment: string | undefined): string | null {
  return environment !== undefined && environment.trim() !== "" ? environment : null;
}

// ------------------------------------------------------- the schema refusal

/**
 * `errors.SchemaMismatch`'s `direction` mapped onto the standing this app renders.
 *
 * A lookup table and nothing else, deliberately the same shape as
 * `DIRECTION_TO_STATE` above -- KYO-175's brief asks that the app not answer two
 * questions of the same kind in two different ways, and this is the second of the
 * two `direction` fields the CLI hands this file. Neither is recomputed here: the
 * comparison of three schema_versions happens in `store.open_store`, and this turns
 * its verdict into a word.
 */
export const SCHEMA_DIRECTION_TO_STANDING: Readonly<Record<string, SchemaStanding>> = {
  "db-ahead-of-build": "store-ahead-of-app",
  "build-ahead-of-db": "app-ahead-of-store",
  "config-drift": "config-drift",
};

function intField(data: Record<string, unknown>, key: string): number | null {
  const value = data[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Turn one `schema_mismatch` envelope into the refusal a surface renders.
 *
 * BRANCH ON THE PAYLOAD, NEVER ON THE MESSAGE. That is the whole point of KYO-178:
 * `store.open_store` gives each direction its own carefully-written sentence, and
 * those sentences are free to improve without moving any code here. `says` is
 * carried through verbatim because KYO-168 wrote the ahead-direction one to be read
 * off a screen by someone who is not at a shell -- its last clause tells a reader
 * who did not install this kyosei that it is bundled with the application they are
 * running -- but nothing in this app inspects a character of it.
 *
 * Returns null when there is no mismatch envelope, which is the ordinary case: a
 * store that opens has no payload to map.
 */
export function schemaMismatchFrom(
  payload: unknown,
  says: string,
  from: string,
): SchemaMismatchFacts | null {
  if (payload === undefined || payload === null || typeof payload !== "object") return null;
  if (Array.isArray(payload)) return null;
  const data = payload as Record<string, unknown>;
  const direction = typeof data.direction === "string" ? data.direction : null;
  const olderSide = typeof data.older_side === "string" ? data.older_side : null;
  const base = {
    direction,
    olderSide,
    buildSchemaVersion: intField(data, "build_schema_version"),
    dbSchemaVersion: intField(data, "db_schema_version"),
    configSchemaVersion: intField(data, "config_schema_version"),
    configPath: stringField(data, "config_path"),
    dbPath: stringField(data, "db_path"),
    says,
    from,
  };
  const standing = direction === null ? undefined : SCHEMA_DIRECTION_TO_STANDING[direction];
  if (standing === undefined) {
    // Not folded into either direction, on `currencyFrom`'s own argument one seam
    // out: guessing a direction is how a screen comes to print the remedy for the
    // OPPOSITE one, and one of these two remedies reconstructs a healthy store under
    // an older schema (KYO-168). An unknown word gets the CLI's sentence and no
    // advice of this app's own.
    return {
      ...base,
      standing: "unrecognised",
      problem:
        `\`${from}\` reported a schema mismatch in direction ` +
        `${JSON.stringify(direction)}, which this build of the app does not know; the ` +
        "CLI is newer than the app. The refusal above is the CLI's own and is shown " +
        "unchanged -- this app has no safe remedy to add to it",
    };
  }
  return { ...base, standing, problem: null };
}

// ------------------------------------------------------------- the probe

/** What one look at a candidate project directory establishes. */
export interface ProjectProbe {
  /** Is a Kyosei project configured at or above this directory at all? */
  located: boolean;
  storeRoot: string | null;
  /** The record-id prefix out of that store's `config.yml`. */
  project: string | null;
  /** Why this build cannot open that store, or null when it opens. */
  mismatch: SchemaMismatchFacts | null;
  /** Why nothing is located here, in the CLI's own words. Null when it is. */
  problem: string | null;
}

/**
 * Ask, in one place, the two questions the switcher has to answer about a directory.
 *
 * "Is there a project here" and "can this build open its store" are different
 * questions with different consequences -- the first refuses a choice (KYO-175.c6),
 * the second is adopted and becomes a screen the human can re-check (c2, c3) -- and
 * KYO-184 is the record that they are nonetheless ONE command's subject. Before it
 * they were answered by two, in parallel, because no single command answered both:
 * `kyo check-branch --json` located without opening the store, and `kyo list --json`
 * opened it so that a schema refusal would arrive. `kyo where --json` answers both,
 * so this function is now a reading of one envelope rather than a join of two.
 *
 * The refusal still reaches this app as data rather than as prose, and by the same
 * route as everything else here: `kyo where` catches `SchemaMismatch` and copies
 * that class's own payload into its envelope unchanged. So there is no "what schema
 * is this store" question anywhere in this app, and no place where its answer is
 * assembled from parts.
 */
export async function probeProject(
  project: ServedProject,
  located?: ProjectLocation,
): Promise<ProjectProbe> {
  const cwd = project.root;
  // `located` is the same sharing `readStoreFacts` takes, for the same reason: the
  // `facts` handler holds this answer already and a second `kyo where` would be an
  // interpreter start for a fact in hand. Every other caller (the switcher, the
  // pinned-root check) is asking about a directory nobody has asked about yet and
  // passes nothing.
  const location =
    located ??
    (await locateProject(project).catch(
      (error: unknown): ProjectLocation => ({
        ...NOWHERE,
        problem: `could not ask ${LOCATE_COMMAND}: ${(error as Error).message}`,
      }),
    ));
  if (location.storeRoot === null) {
    return {
      located: false,
      storeRoot: null,
      project: null,
      mismatch: null,
      problem: location.problem ?? `no Kyosei project at or above ${cwd}`,
    };
  }
  return {
    located: true,
    storeRoot: location.storeRoot,
    project: location.project,
    mismatch: location.mismatch,
    problem: null,
  };
}

// ------------------------------------------------- remembering the choice

/**
 * Where the app remembers which project it was last pointed at.
 *
 * App-local, under whatever directory the caller passes -- `main.ts` passes
 * `app.getPath("userData")`, which is where this app already keeps its `kyo` shims.
 * Never in any store: under KYO-79 a human reaches a project by writing an idea or by
 * telling the controller, and "which project this app had open" is
 * a fact about this installation of the app, not about any project it opened. A
 * consumer's repository gains nothing from this record.
 *
 * Since KYO-379 it holds only what is this INSTALLATION's: the chosen project and the
 * recents order. The list of projects this MACHINE holds and serves is its registry,
 * `$KYOSEI_HOME/projects.json` (`projects.ts`), which the app and the server share;
 * the recents here were folded into it once, on the first launch that had one.
 */
export const PROJECT_STATE_FILE = "project.json";

/** How many previously-opened projects the switcher offers. */
export const MAX_RECENTS = 8;

/** How many windows a launch reopens, at most (KYO-465.c1). */
export const MAX_WINDOWS = 16;

/**
 * A project as this installation remembers it (KYO-465): its root, and the kyo serve
 * it was read through -- `KnownProject`'s identity (`links.ts`, KYO-463) without the
 * labels. A root alone is not a project: two machines can each hold a
 * `~/code/kyosei`, and before this type the app remembered the path and
 * reopened it on THIS machine, whichever server it had last been read through.
 */
export type RememberedProject = Pick<KnownProject, "root" | "connection">;

/** The same project: the same root on the same kyo serve (null for this machine's). */
export function sameProject(a: RememberedProject, b: RememberedProject): boolean {
  return a.root === b.root && sameServer(a.connection, b.connection);
}

export interface ProjectState {
  /** The last project adopted in the app, or null if none ever was. */
  chosen: RememberedProject | null;
  /** Every project adopted, newest first, including `chosen`; keyed by (server, root). */
  recents: RememberedProject[];
  /**
   * The project each window was showing when the app last ran, in the order they
   * opened (KYO-465.c1): what the next launch reopens. Absent until a window has been
   * remembered; the last window's is kept when it closes, since that close is the quit.
   */
  windows?: RememberedProject[];
  /**
   * True once these recents have been folded into this machine's project registry
   * (`projects.ts`, KYO-379.c1), so a project a person later removes from the registry
   * by hand is not put back by this list. Absent until then, and never written false.
   */
  registryFolded?: boolean;
}

const EMPTY_STATE: ProjectState = { chosen: null, recents: [] };

export function projectStatePath(userDataDir: string): string {
  return join(userDataDir, PROJECT_STATE_FILE);
}

/**
 * One remembered project out of the file, in either of its two shapes: a bare path
 * (every file written before KYO-465, when a path was all the app kept -- and a path
 * it kept only for a project on this machine, so it reads as one), or `{root,
 * connection}`. Anything else is null, never a guess.
 */
function rememberedFrom(raw: unknown): RememberedProject | null {
  if (typeof raw === "string") return raw === "" ? null : { root: raw, connection: null };
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return null;
  const data = raw as Record<string, unknown>;
  if (typeof data.root !== "string" || data.root === "") return null;
  const connection = data.connection;
  if (connection === null || connection === undefined) return { root: data.root, connection: null };
  if (typeof connection !== "object" || Array.isArray(connection)) return null;
  const at = connection as Record<string, unknown>;
  // A connection that cannot be read is NOT this machine: reading it as local is the
  // defect this record is about, so the entry is dropped instead.
  if (typeof at.host !== "string" || at.host === "" || typeof at.port !== "number") return null;
  return { root: data.root, connection: { host: at.host, port: at.port } };
}

function rememberedList(raw: unknown): RememberedProject[] {
  if (!Array.isArray(raw)) return [];
  const list: RememberedProject[] = [];
  for (const item of raw) {
    const entry = rememberedFrom(item);
    if (entry !== null && !list.some((held) => sameProject(held, entry))) list.push(entry);
  }
  return list;
}

/**
 * Read the remembered choice, treating every failure as "nothing remembered".
 *
 * Total by design: a corrupt or half-written file must cost the reader the recents
 * list, never the app. What it must NOT do is invent a project -- an unreadable
 * file yields `chosen: null`, and the precedence order then falls through to the
 * checkout, which is where the app pointed before this record existed.
 */
export function readProjectState(userDataDir: string): ProjectState {
  try {
    const raw: unknown = JSON.parse(readFileSync(projectStatePath(userDataDir), "utf8"));
    if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return { ...EMPTY_STATE };
    const data = raw as Record<string, unknown>;
    const windows = rememberedList(data.windows).slice(0, MAX_WINDOWS);
    return {
      chosen: rememberedFrom(data.chosen),
      recents: rememberedList(data.recents).slice(0, MAX_RECENTS),
      ...(windows.length > 0 ? { windows } : {}),
      ...(data.registryFolded === true ? { registryFolded: true } : {}),
    };
  } catch {
    // Not written yet (the ordinary first launch), unreadable, or not JSON.
    return { ...EMPTY_STATE };
  }
}

/** A copy that shares no object with the caller's, so a later edit of one is not both. */
function copyOf(project: RememberedProject): RememberedProject {
  return {
    root: project.root,
    connection: project.connection === null ? null : { ...project.connection },
  };
}

/** Put `project` at the head of the recents, without duplicating it, and choose it. */
export function withRecent(state: ProjectState, project: RememberedProject): ProjectState {
  return {
    ...state,
    chosen: copyOf(project),
    recents: [
      copyOf(project),
      ...state.recents.filter((held) => !sameProject(held, project)),
    ].slice(0, MAX_RECENTS),
  };
}

/**
 * Forget one recent, or every one with `null` (KYO-465.c3: remove a stale or duplicate
 * entry, and clear all). Where the app reopens -- `chosen`, `windows` -- is left alone:
 * forgetting a row of a list is not closing a project, and the project open now is not
 * a row the switcher offers at all.
 */
export function withoutRecent(
  state: ProjectState,
  project: RememberedProject | null,
): ProjectState {
  return {
    ...state,
    recents: project === null ? [] : state.recents.filter((held) => !sameProject(held, project)),
  };
}

/** The windows the app now shows, remembered for the next launch (KYO-465.c1). */
export function withWindows(state: ProjectState, windows: RememberedProject[]): ProjectState {
  return { ...state, windows: windows.slice(0, MAX_WINDOWS).map(copyOf) };
}

/**
 * The windows a launch opens, each on the project it last showed and through the kyo
 * serve it was read through (KYO-465.c1). A pure function, so the property this record
 * exists for -- a project last used through a server never comes back as the local
 * project at the same path -- is checkable without an app.
 *
 * `KYOSEI_PROJECT_ROOT` still outranks everything (KYO-175.c8): one window, on it. Then
 * the windows the last session showed; then the one project it last chose; and with
 * nothing remembered at all, an empty list -- the caller's default, the checkout.
 */
export function windowsAtLaunch(
  state: ProjectState,
  environment: string | undefined,
): RememberedProject[] {
  const pinned = environmentRootOf(environment);
  if (pinned !== null) return [{ root: pinned, connection: null }];
  if (state.windows !== undefined && state.windows.length > 0) return state.windows.map(copyOf);
  return state.chosen === null ? [] : [copyOf(state.chosen)];
}

/**
 * Write the remembered choice, and never fail the switch because it could not be.
 *
 * The switch itself has already happened in memory by the time this runs; a
 * read-only or full `userData` should cost the human the memory of it across
 * launches, not the project they just opened. It returns the problem instead of
 * throwing so a caller that wants to say so can.
 */
export function writeProjectState(userDataDir: string, state: ProjectState): string | null {
  try {
    mkdirSync(userDataDir, { recursive: true });
    writeFileSync(projectStatePath(userDataDir), `${JSON.stringify(state, null, 2)}\n`, "utf8");
    return null;
  } catch (error) {
    return (error as Error).message;
  }
}

/**
 * The recents as the switcher lists them: a path, and the store's own prefix -- as
 * this machine's kyo serve lists it (`/v1/projects`, KYO-459), read once for the whole
 * list rather than once per entry. A recent the server does not serve, or a list that
 * could not be read, reads as null, which the panel renders as the path alone.
 */
export function recentEntries(
  state: ProjectState,
  served: readonly ServedEntry[] = [],
): ProjectEntry[] {
  // This machine's served list labels this machine's projects only (KYO-465).
  return state.recents
    .filter((entry) => entry.connection === null)
    .map(({ root }) => ({
      root,
      project: served.find((entry) => entry.root === root)?.prefix ?? null,
    }));
}
