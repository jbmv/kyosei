// Every read docs mode makes. There is no write here and there is not meant to be.
//
// KYO-136 decided that docs mode sits past the seam and is pure view-only: a document
// changes when an idea reaches an orchestrator session, which reads and edits it. So
// this file is the whole capability surface of the mode, and it is two reads. Ideas
// mode needed `assertWritable` because it has writes to constrain; the equivalent
// guarantee here is that there is no third function.
//
// WHERE THE ROWS COME FROM, and why not from a Base. The governance layer -- what a
// document governs, which claims no longer resolve, how current it is -- is the KYO-109
// query source, and that source is reachable through `kyo base run` only when the
// project has declared a Base with `source: docs`. Requiring that would make this mode
// work on some projects and render an explanation on others, including this one, whose
// `kyosei/bases.yml` declares three Bases and none over docs. `kyo docs check --json`
// carries the same rows off the same enumerator (KYO-139, `docs_check.document_row`),
// runs on every project, and opens no database -- so it answers here.
//
// The verdict that command exists to give is not discarded on the way past: a broken
// `governs:` target makes it exit non-zero, and the rows ride on that payload too. A
// viewer that went blank on a failing check would go blank on exactly the projects
// whose documents most need looking at.

import { statSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { isAbsolute, join, posix, resolve, sep } from "node:path";
import type {
  CurrencyFacts,
  DocDocument,
  DocEntry,
  DocObligation,
  DocSection,
  DocStaleness,
  DocsState,
  DocTree,
  GovernsProblem,
} from "../shared/protocol";
import { DOC_STALENESS_STATES } from "../shared/protocol";
import { currencyFailure, locateProject, readCurrency } from "./facts";
import type { KyoJson, ServedProject } from "./kyoserve";

/** The command behind the tree, named on the surface rather than left to be inferred. */
export const DOCS_TREE_COMMAND = "kyo docs check --json";
/**
 * The `kyo` subcommands docs mode may construct, on records mode's allowlist pattern
 * (KYO-112.c6) and for the same reason: "docs mode is view-only" has to be a claim
 * about what this process CAN run, not about what its author remembered.
 *
 * One entry. `kyo docs` has three actions and only two of them are reads, so the
 * action is checked as well as the command -- `settle` writes a verdict row, and it is
 * the one action a viewer of this data would most plausibly be tempted to offer.
 */
export const DOCS_READ_COMMANDS: readonly string[] = ["docs"];
/** The `kyo docs` actions that read. `settle` is absent because it writes (KYO-137). */
export const DOCS_READ_ACTIONS: readonly string[] = ["check", "obligations"];

/** Refuse, by name, anything docs mode is not allowed to build. */
export function assertDocsRead(args: readonly string[]): void {
  const command = args[0] ?? "";
  if (!DOCS_READ_COMMANDS.includes(command)) {
    throw new Error(
      `kyosei: docs mode may not run \`kyo ${command}\`. Docs mode is pure view-only, ` +
        "past the runtime seam (KYO-136 decision 6): a document changes through a " +
        "orchestrator session, never through this app. The commands it may run are: " +
        `${DOCS_READ_COMMANDS.join(", ")}.`,
    );
  }
  const action = args[1] ?? "";
  if (!DOCS_READ_ACTIONS.includes(action)) {
    throw new Error(
      `kyosei: docs mode may not run \`kyo docs ${action}\`. \`settle\` records a ` +
        "orchestrator's asserted verdict against a docs obligation (KYO-137) and is a " +
        `write. The actions it may run are: ${DOCS_READ_ACTIONS.join(", ")}.`,
    );
  }
}

async function readJson(project: ServedProject, args: readonly string[]): Promise<KyoJson> {
  assertDocsRead(args);
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

function strings(value: unknown): string[] {
  return list(value).map((item) => String(item));
}

function rows(value: unknown): Record<string, unknown>[] {
  return list(value).filter(
    (item): item is Record<string, unknown> =>
      item !== null && typeof item === "object" && !Array.isArray(item),
  );
}

/**
 * The CLI's staleness word, or `unrecognised`.
 *
 * The default matters more than the mapping. A state word this build does not know --
 * a fifth one, or a rename -- becomes `unrecognised` and is drawn as its own thing.
 * Anything else here would be choosing a known state to absorb it into, and the
 * tempting choice is always the benign one, which is how a viewer comes to paint a
 * clean docs tree over a state it could not read.
 */
function stalenessOf(value: unknown): DocStaleness {
  const word = text(value);
  return (DOC_STALENESS_STATES as readonly string[]).includes(word)
    ? (word as DocStaleness)
    : "unrecognised";
}

/** The folder half of a root-relative posix path; the empty string at the top. */
function folderOf(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut === -1 ? "" : path.slice(0, cut);
}

/** The file's own name, extension included. */
function nameOf(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

function problemsFrom(value: unknown): GovernsProblem[] {
  return rows(value).map((row) => ({
    docPath: text(row.doc_path),
    section: text(row.section),
    target: text(row.target),
    reason: text(row.reason),
  }));
}

/**
 * A section's own broken targets, intersected from the document's.
 *
 * `governs_broken` is a document-level list and `governs` is per section, so which
 * section owns a broken claim has to be recovered by matching the target -- the same
 * intersection `kyo docs check`'s own problem rows carry, and this agrees with them
 * because both sides are the CLI's verdict rather than a second resolution attempt
 * here. The app never decides whether a target resolves.
 */
function sectionsFrom(value: unknown, governsBroken: readonly string[]): DocSection[] {
  const broken = new Set(governsBroken);
  return rows(value).map((row) => {
    const governs = strings(row.governs);
    return {
      heading: text(row.heading),
      level: int(row.level),
      governs,
      governsBroken: governs.filter((target) => broken.has(target)),
      status: str(row.status),
      amendedBy: strings(row.amended_by),
    };
  });
}

function obligationsFrom(value: unknown): Map<string, DocObligation[]> {
  const byDoc = new Map<string, DocObligation[]>();
  for (const row of rows(value)) {
    // Only the open ones reach here: `kyo docs obligations` lists open by default,
    // and a settled obligation is evidence somebody looked rather than work owed.
    const docPath = text(row.doc_path);
    const owed: DocObligation = {
      id: text(row.id),
      recordId: text(row.record_id),
      dispatchId: str(row.dispatch_id),
      section: text(row.section),
      cause: text(row.cause),
      detail: text(row.detail),
    };
    const standing = byDoc.get(docPath);
    if (standing === undefined) byDoc.set(docPath, [owed]);
    else standing.push(owed);
  }
  return byDoc;
}

function entriesFrom(value: unknown, obligations: Map<string, DocObligation[]>): DocEntry[] {
  return rows(value).map((row) => {
    const path = text(row.id);
    const governsBroken = strings(row.governs_broken);
    return {
      path,
      name: nameOf(path),
      folder: folderOf(path),
      title: str(row.title),
      sectionCount: int(row.section_count),
      governs: strings(row.governs),
      governsBroken,
      amended: row.amended === true,
      staleness: stalenessOf(row.staleness),
      modifiedAt: null,
      stalenessSays: text(row.staleness_says),
      sections: sectionsFrom(row.sections, governsBroken),
      obligations: obligations.get(path) ?? [],
    };
  });
}

function unavailable(problem: string, currency: CurrencyFacts): DocTree {
  return {
    ok: false,
    state: "unavailable",
    root: null,
    documents: [],
    folders: [],
    scannedPaths: [],
    pathsDeclared: false,
    says: "",
    from: DOCS_TREE_COMMAND,
    problems: [],
    refusal: problem,
    currency,
  };
}

// ------------------------------------------------------------------- the reads

/**
 * This project's documents, with the governance layer over them.
 *
 * The obligations read is best-effort and deliberately so: it is the only one of the
 * two that opens the database, so a store whose `kyosei.db` has not been rebuilt fails
 * it while the document tree itself is perfectly readable. A tree withheld because a
 * secondary read failed would be this app deciding a reader may not see their
 * documents over a fact about a cache.
 */
export async function readDocTree(project: ServedProject): Promise<DocTree> {
  let currency: CurrencyFacts;
  try {
    currency = await readCurrency(project);
  } catch (error) {
    currency = currencyFailure((error as Error).message);
  }

  const located = await locateProject(project);
  const root = located.repoRoot;

  let result: Awaited<ReturnType<typeof readJson>>;
  try {
    result = await readJson(project, ["docs", "check"]);
  } catch (error) {
    return unavailable(
      `could not run \`${DOCS_TREE_COMMAND}\`: ${(error as Error).message}`,
      currency,
    );
  }
  const { data, stderr, code } = result;
  if (data === null) {
    return unavailable(
      `\`${DOCS_TREE_COMMAND}\` produced no JSON (exit ${code}): ${stderr.trim() || "(no output)"}`,
      currency,
    );
  }
  // A `documents` key this build does not get back means a CLI older than KYO-139:
  // said so, rather than rendered as a project with no documents, because those are
  // the two things c6 exists to keep apart.
  if (!Array.isArray(data.documents)) {
    return unavailable(
      `\`${DOCS_TREE_COMMAND}\` answered without a \`documents\` array. This build reads the ` +
        "docs query source off that key (KYO-139); a `kyo` from before it carries only the " +
        "check's verdict. Point the app at a newer `kyo`, or run `kyo base run` over a Base " +
        "declaring `source: docs`.",
      currency,
    );
  }

  let obligations = new Map<string, DocObligation[]>();
  try {
    const owed = await readJson(project, ["docs", "obligations"]);
    if (owed.data !== null) obligations = obligationsFrom(owed.data.obligations);
  } catch {
    // Left empty. See the note above: this read is not what the tree is for.
  }

  const documents = entriesFrom(data.documents, obligations).map((entry) => {
    // The file's own stamp, read here because the CLI's envelope carries none: a
    // sort by "modified" over documents is a sort over the tree on disk (KYO-250).
    // Only on this machine's server, whose roots are paths on this disk; kyo serve
    // serves no document stamp, so another machine's documents carry none (KYO-459).
    if (!project.local) return entry;
    try {
      return {
        ...entry,
        modifiedAt: root === null ? null : statSync(join(root, entry.path)).mtime.toISOString(),
      };
    } catch {
      return entry;
    }
  });
  const folders = [...new Set(documents.map((entry) => entry.folder))].sort();
  const state: DocsState = text(data.state) === "no-documents" ? "no-documents" : "documents";
  return {
    ok: true,
    state,
    root,
    documents,
    folders,
    scannedPaths: strings(data.document_paths),
    pathsDeclared: data.document_paths_declared === true,
    says: text(data.says),
    from: DOCS_TREE_COMMAND,
    problems: problemsFrom(data.problems),
    refusal: null,
    currency,
  };
}

/**
 * One document's text.
 *
 * Read on its own rather than shipped in the tree payload: a docs tree is small and a
 * docs tree with every file's bytes inlined is not, and the reader opens one at a time.
 * This machine's documents are read from disk; another machine's through its kyo
 * serve's document route (KYO-467).
 *
 * The path is resolved against the code root and checked to still be under it. Docs
 * mode never writes, so this is not a write guard -- it is the read equivalent, and the
 * reason to have it is that `path` arrives from a renderer. `..` in it would otherwise
 * make this method a general-purpose file reader wearing a docs-mode label.
 */
export async function readDoc(project: ServedProject, path: string): Promise<DocDocument> {
  const missing = (problem: string): DocDocument => ({
    ok: false,
    path,
    absolutePath: null,
    text: "",
    problem,
  });
  if (path === "" || isAbsolute(path) || path.includes("\0")) {
    return missing(`kyosei: ${path || "(empty)"} is not a document path relative to the project.`);
  }
  if (!project.local) {
    // Another machine's document is read through its kyo serve's document route
    // (KYO-467), which answers only for a file `kyo docs check` lists there and refuses
    // anything else by its own sentence. There is no path on THIS disk to name, so
    // `absolutePath` stays null. A `.` or `..` segment is refused here, before it is
    // sent: `fetch` would resolve it away inside the URL and ask for some other route.
    if (path.split("/").some((segment) => segment === "." || segment === "..")) {
      return missing(
        `kyosei: ${path} has a "." or ".." segment; name the document by its path in the tree.`,
      );
    }
    try {
      const read = await project.readDocument(path);
      if (read.ok) return { ok: true, path, absolutePath: null, text: read.text, problem: null };
      return missing(
        `kyosei: could not read ${path} from ${project.client.label}: ${read.problem}`,
      );
    } catch (error) {
      return missing(`kyosei: could not read ${path}: ${(error as Error).message}`);
    }
  }
  const located = await locateProject(project);
  const root = located.repoRoot;
  if (root === null) {
    return missing(
      located.problem ?? "kyosei: could not locate this project's code root, so nothing was read.",
    );
  }
  const absolute = resolve(join(root, ...path.split(posix.sep)));
  if (absolute !== root && !absolute.startsWith(root.endsWith(sep) ? root : root + sep)) {
    return missing(
      `kyosei: ${path} resolves outside the project root (${root}); docs mode reads the ` +
        "documents this project declares and nothing else.",
    );
  }
  try {
    return {
      ok: true,
      path,
      absolutePath: absolute,
      text: await readFile(absolute, "utf8"),
      problem: null,
    };
  } catch (error) {
    return missing(`kyosei: could not read ${path}: ${(error as Error).message}`);
  }
}
