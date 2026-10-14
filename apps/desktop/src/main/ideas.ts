// Ideas mode's reads, and its writes -- the only writes this app asks for.
//
// The app writes the human's own surfaces and nothing else. Two of them: the session
// scratchpad, at `<per-project home>/scratchpad.md`, and files under the project's
// `ideas/`. It never writes `kyosei/` -- promotion, records and everything past that
// seam stay with the CLI (KYO-39), and there is no promotion UI at all.
//
// SINCE KYO-459 IT WRITES NOTHING ITSELF. Every read and write here is a request to the
// project's `kyo serve` -- its file routes (`ideas/`, `ideas-move`, `ideas-mkdir`,
// `scratchpad`) -- and the boundary is `src/kyosei/serve_files.py`: one resolver, by
// real path, for every viewer. A renderer that asked to write
// `kyosei/export/KYO-1.json` gets kyo serve's refusal naming the seam, not a file --
// whether the project is on this machine or another.
//
// The division of labour with the CLI is KYO-72's, unchanged:
//
//   where the project is     asked of `kyo`, never re-derived (`facts.locateProject`).
//   where the HOME is        asked of `kyo` too -- see `readProjectHome` below.
//   what an idea file says   `kyo base run <an ideas base> --json` for files on disk,
//                            and the port in `shared/ideafile.ts` for the buffer the
//                            human is editing, which no command can answer for.
//   which records exist      the read seams records mode already uses. This mode
//                            adds no second route to a record.

import { hostname } from "node:os";
import { dirname, join } from "node:path";
import {
  composeIdeaFile,
  fileTags,
  IDEAS_DIR,
  readProperties,
  readSections,
  SCRATCHPAD_BASENAME,
  SCRATCHPAD_FILENAME,
  splitFrontmatter,
} from "../shared/ideafile";
import { merge3 } from "../shared/merge3";
import type {
  Completions,
  CurrencyFacts,
  IdeaDocument,
  IdeaEdit,
  IdeaFileEntry,
  IdeaTree,
  RecordCompletion,
  SaveResult,
  Scratchpad,
  ScratchpadSave,
  WriteResult,
} from "../shared/protocol";
import { currencyFailure, locateProject, readCurrency } from "./facts";
import type { IdeaEntry, KyoJson, ServedProject } from "./kyoserve";
import { readBases, readRecordTree, runBase } from "./records";

/**
 * The `kyo` subcommands ideas mode is allowed to construct.
 *
 * An allowlist, for the reason `RECORDS_READ_COMMANDS` is one: "this mode reaches
 * the CLI for these two things" is a claim about what the app CAN do, and a denylist
 * makes it a claim about what this author remembered.
 *
 * Two entries, and `worktrees` is the one that needs defending. It is on this list
 * because its `--json` envelope is the only place any command reports the
 * per-project home (see `readProjectHome`), and it is safe to be on it because law
 * 11 makes it dry-run by default: without `--prune` it removes nothing, opens no
 * transaction and writes no ledger row. `--prune` is never constructed here, and
 * could not be: this file passes a fixed argv.
 *
 * `map` was on this list from KYO-234 to KYO-439, for the record-promotion dialog's
 * kind picker; it left with that dialog. `promote` is not on it and never was:
 * promotion is a store write, the controller's act, and since KYO-439 the app does
 * not run it by any route -- `kyo.ts`'s `assertAppMayRun` refuses it at the one
 * place this app spawns the CLI.
 */
export const IDEAS_READ_COMMANDS: readonly string[] = ["base", "worktrees"];

export function assertIdeasRead(args: readonly string[]): void {
  const command = args[0] ?? "";
  if (IDEAS_READ_COMMANDS.includes(command)) return;
  throw new Error(
    `kyosei: ideas mode may not run \`kyo ${command}\`. Its route to the CLI is a read: ` +
      `${IDEAS_READ_COMMANDS.join(", ")}. Ideas mode writes the human's own files ` +
      "directly (the scratchpad and `ideas/`) and reaches the store only to read it -- " +
      "promotion and everything past that seam are the CLI's (KYO-39).",
  );
}

async function readJson(project: ServedProject, args: readonly string[]): Promise<KyoJson> {
  assertIdeasRead(args);
  return project.json(args);
}

// ------------------------------------------------------------ the per-project home

/** The command the per-project home comes out of. */
export const HOME_COMMAND = "kyo worktrees --json";

/**
 * Where `~/kyosei/<project>/` is, for this project.
 *
 * The same weak point `facts.ts` documents for the store locator, one directory
 * over, and written down for the same reason: **no `kyo` command's subject is "where
 * is this project's home"**. `config.store_home()` + `config.project_home_name()` is
 * a real rule -- `$KYOSEI_HOME` or `~/kyosei`, then the store root's directory name
 * under the split layout and the code root's under in-repo -- and a TypeScript copy
 * of it would be a second implementation of where a project's files live, which is
 * exactly what law 3 exists to prevent. So the app asks.
 *
 * `kyo worktrees --json` is the leanest command whose envelope answers it: it
 * reports `home` as `<per-project home>/dispatch worktrees` (`config.worktree_home`),
 * and the parent of that IS the per-project home. Taking a parent directory is a path
 * operation, not a re-derivation: none of the three things that could drift -- the
 * env var, the default base, which root supplies the name -- is decided here.
 *
 * It also means the FIXTURE controls this through the variable the CLI already
 * honours. The checks set `KYOSEI_HOME` into the app's environment, `kyo` reads it,
 * and the scratchpad lands inside the fixture. There is no app-only override and no
 * test-only branch: the seam the tests use is the seam the consumer uses.
 */
export async function readProjectHome(
  project: ServedProject,
): Promise<{ home: string | null; problem: string | null }> {
  let result: Awaited<ReturnType<typeof readJson>>;
  try {
    result = await readJson(project, ["worktrees"]);
  } catch (error) {
    return {
      home: null,
      problem: `could not run \`${HOME_COMMAND}\`: ${(error as Error).message}`,
    };
  }
  const home = result.data?.home;
  if (typeof home !== "string" || home === "") {
    return {
      home: null,
      problem:
        `\`${HOME_COMMAND}\` named no worktree home for ${project.root}` +
        (result.stderr.trim() === "" ? "" : `: ${result.stderr.trim()}`),
    };
  }
  // `home` is `<per-project home>/dispatch worktrees`; the scratchpad is a sibling of
  // that directory, not a thing inside it -- a worktree home holds checkouts, and a
  // file dropped among them would be swept up by `kyo worktrees --prune` reasoning
  // about directories it did not create.
  return { home: dirname(home), problem: null };
}

// ------------------------------------------------------- the writable surfaces
//
// KYO-459: THE BOUNDARY IS KYO SERVE'S. Every write below is a request to the
// project's kyo serve -- `PUT`/`DELETE` on `ideas/<relpath>` and the scratchpad,
// `ideas-move`, `ideas-mkdir` -- and `serve_files.py` resolves each one to its REAL
// path and refuses anything outside `ideas/` and the one scratchpad path: a `..`,
// an absolute path, a symlink planted under `ideas/` that points into `kyosei/`. The
// app's own TypeScript copy of that rule (`assertWritable`) is gone with the app's own
// write path; one module in Python holds the line for every viewer. What stays here is
// the shape a reader types -- one path segment for a name, no separator -- so a
// refusal names what they typed before a request is made.
//
// No write overwrites a copy it did not read, and the copy is the EDITOR's
// (KYO-575): an idea file and the scratchpad are each saved with the ETag the tab that
// holds them loaded or last saved, which the tab sends with its text. Nothing here
// keeps a version per process -- two windows' tabs on one file each name their own --
// and nothing re-reads the file at save time to borrow the version there now, which
// would make the check cover only the milliseconds between that read and the write. A
// save kyo serve refuses because the file changed since is merged three ways
// (`shared/merge3.ts`) and the merge written against the current version; a new idea
// is written only where no file is.

/** Where `ideas/` is for this project, on the server's machine, for the paths said. */
const ideasRoots = new Map<string, string>();

async function ideasRootOf(project: ServedProject): Promise<string | null> {
  const key = scratchKey(project);
  const held = ideasRoots.get(key);
  if (held !== undefined) return held;
  const location = await locateProject(project).catch(() => ({ storeRoot: null }));
  if (location.storeRoot === null) return null;
  const root = join(location.storeRoot, IDEAS_DIR);
  ideasRoots.set(key, root);
  return root;
}

/** The absolute path a relative one names, for a WriteResult to state; null when unknown. */
async function stated(project: ServedProject, relpath: string): Promise<string | null> {
  const root = await ideasRootOf(project);
  return root === null ? null : join(root, relpath);
}

/** A path relative to `ideas/` as the reader's request names it, checked as text. */
function relativeProblem(path: string): string | null {
  if (
    path === "" ||
    path.startsWith("/") ||
    path.includes("\0") ||
    path.split("/").some((segment) => segment === ".." || segment === ".")
  ) {
    return `kyosei: ${JSON.stringify(path)} is not a path inside ideas/`;
  }
  return null;
}

// ---------------------------------------------------------------- the scratchpad

/**
 * The session scratchpad, read.
 *
 * A file that is not there is not a failure: the scratchpad's whole premise is that
 * it costs nothing to start one, so a project that has never had one opens an empty
 * document that says so (`fresh`). It is created on the first save and not before --
 * opening ideas mode in a project should not leave a file behind in the human's home
 * directory.
 */
export async function readScratchpad(project: ServedProject): Promise<Scratchpad> {
  // Where it is, for the surface to state: `<per-project home>/scratchpad.md`, the home
  // as `kyo worktrees --json` names it. Its bytes come from kyo serve's scratchpad
  // route (KYO-459), which resolves that same path on the server's side.
  const home = await readProjectHome(project);
  const path = home.home === null ? null : join(home.home, SCRATCHPAD_FILENAME);
  let read: Awaited<ReturnType<ServedProject["readFile"]>>;
  try {
    read = await project.readFile(null);
  } catch (error) {
    return {
      ok: false,
      path,
      home: home.home,
      text: "",
      version: null,
      fresh: false,
      problem: (error as Error).message,
    };
  }
  if (read.ok) {
    return {
      ok: true,
      path,
      home: home.home,
      text: read.text,
      version: read.etag,
      fresh: false,
      problem: null,
    };
  }
  if (read.missing) {
    // Not there yet is not a failure: a project that has never had one opens an empty
    // document that says so, created on the first save and not before.
    return {
      ok: true,
      path,
      home: home.home,
      text: "",
      version: null,
      fresh: true,
      problem: null,
    };
  }
  return {
    ok: false,
    path,
    home: home.home,
    text: "",
    version: null,
    fresh: false,
    problem: read.problem,
  };
}

function scratchKey(project: ServedProject): string {
  return `${project.client.label}\u0000${project.root}`;
}

/**
 * How a merge names the side it wrote (`mergeMarkerMine`): the window that saved second.
 * The marker is read in EVERY window that shows the file, so it names the machine rather
 * than saying "this window", which would be wrong in all the others.
 */
export function mergeAuthor(): string {
  return `the window on ${hostname().replace(/\.local$/, "")}`;
}

/** How many times a merged save is retried when the file moves again under it. */
const MERGE_ATTEMPTS = 4;

type Written =
  | { ok: true; version: string | null; text: string; merged: { conflicts: number } | null }
  | { ok: false; problem: string; status: number | null };

/**
 * Write `mine` over the version the editor holds (`version`, whose text was `base`):
 * the one write path for both compose surfaces (KYO-575).
 *
 * kyo serve's If-Match is the check. When it refuses with 412 the file changed
 * since the tab's version, so the current text is read, merged three ways with the
 * tab's (base: what the tab loaded; mine: the editor; theirs: the file now) and the
 * merge is written against THAT version -- and if the file moved again in between, the
 * merge is merged again, from the text it was merged with. A file that is gone is
 * written as the tab has it: deleting a file is not an edit to any line of it.
 */
async function writeMerging(
  project: ServedProject,
  relpath: string | null,
  base: string,
  version: string | null,
  mine: string,
): Promise<Written> {
  let expect = version;
  let from = base;
  let text = mine;
  let conflicts: number | null = null;
  for (let attempt = 0; attempt < MERGE_ATTEMPTS; attempt += 1) {
    const written = await project.writeFile(relpath, text, expect ?? "create");
    if (written.ok) {
      return {
        ok: true,
        version: written.etag,
        text,
        merged: conflicts === null ? null : { conflicts },
      };
    }
    if (written.status !== 412) {
      return { ok: false, problem: written.problem, status: written.status };
    }
    const current = await project.readFile(relpath);
    if (!current.ok && !current.missing) {
      return { ok: false, problem: current.problem, status: null };
    }
    if (!current.ok) {
      // Gone since: nothing of theirs to keep, so the tab's text is written whole.
      expect = null;
      from = "";
      conflicts = conflicts ?? 0;
      continue;
    }
    const merged = merge3(from, text, current.text, mergeAuthor());
    conflicts = (conflicts ?? 0) + merged.conflicts;
    from = current.text;
    text = merged.text;
    expect = current.etag ?? "";
  }
  return {
    ok: false,
    status: 412,
    problem:
      `${relpath === null ? "the scratchpad" : `ideas/${relpath}`} kept changing while this ` +
      `window merged it (${MERGE_ATTEMPTS} times); nothing of this window's was written, and ` +
      "its text is still in the editor -- the next keystroke saves again.",
  };
}

/**
 * The session scratchpad, written through kyo serve's scratchpad route against the
 * version the TAB loaded or last saved (KYO-575) -- or created, when it was read as not
 * there yet. A scratchpad another window or machine changed since is merged, never
 * overwritten. The server makes its directory; nothing else is made.
 */
export async function writeScratchpad(
  project: ServedProject,
  save: ScratchpadSave,
): Promise<SaveResult> {
  const home = await readProjectHome(project);
  const path = home.home === null ? null : join(home.home, SCRATCHPAD_FILENAME);
  try {
    const written = await writeMerging(project, null, save.base, save.version, save.text);
    if (!written.ok) return refusedSave(path, written.problem);
    return {
      ok: true,
      path,
      problem: null,
      version: written.version,
      base: written.text,
      body: written.text,
      properties: null,
      merged: written.merged,
    };
  } catch (error) {
    return refusedSave(path, (error as Error).message);
  }
}

function refusedSave(path: string | null, problem: string): SaveResult {
  return {
    ok: false,
    path,
    problem,
    version: null,
    base: null,
    body: null,
    properties: null,
    merged: null,
  };
}

// ------------------------------------------------------------------ the ideas tree

/** How the tree said where its rows came from. */
export const TREE_SCAN = "the ideas/ directory itself";

interface ScannedFile {
  path: string;
  name: string;
  folder: string;
  /** Nanoseconds since the epoch, as kyo serve's listing reports the file's mtime. */
  mtimeNs: number | null;
  /** The listing's created time (birth time where the disk keeps one; KYO-467). */
  createdNs: number | null;
  etag: string | null;
}

/**
 * Every file under `ideas/`, and every folder, from kyo serve's listing (KYO-459).
 *
 * The listing is membership; the Base engine only ever DECORATES it, which is why no
 * base's filter can quietly make a file disappear from the tree. Dotfiles -- an
 * Obsidian vault's `.obsidian/`, a `.gitignore` -- are the editor's furniture, not the
 * human's writing, and are left out at any depth, as is `scratchpad.md`, which
 * `ideas.idea_files` skips: a leftover in-vault scratchpad is not an idea. A symlink
 * is left out too; its content is whatever the server's resolver says it is.
 */
function scanIdeas(entries: readonly IdeaEntry[]): { files: ScannedFile[]; folders: string[] } {
  const files: ScannedFile[] = [];
  const folders = new Set<string>([""]);
  for (const entry of entries) {
    const segments = entry.path.split("/");
    if (segments.some((segment) => segment.startsWith("."))) continue;
    if (entry.type === "dir") {
      folders.add(entry.path);
      continue;
    }
    if (entry.type !== "file") continue;
    const name = segments[segments.length - 1] ?? entry.path;
    if (name === SCRATCHPAD_BASENAME) continue;
    files.push({
      path: entry.path,
      name,
      folder: segments.slice(0, -1).join("/"),
      mtimeNs: entry.mtimeNs,
      createdNs: entry.createdNs,
      etag: entry.etag,
    });
  }
  files.sort((a, b) => a.path.localeCompare(b.path));
  return { files, folders: [...folders].sort() };
}

/** The texts of the `.md` files named, fetched a few at a time; an unreadable one is absent. */
async function markdownTexts(
  project: ServedProject,
  files: readonly ScannedFile[],
): Promise<Map<string, string>> {
  const texts = new Map<string, string>();
  const wanted = files.filter((file) => file.name.toLowerCase().endsWith(".md"));
  let next = 0;
  const worker = async (): Promise<void> => {
    for (;;) {
      const file = wanted[next];
      next += 1;
      if (file === undefined) return;
      try {
        const read = await project.readFile(file.path);
        if (read.ok) texts.set(file.path, read.text);
      } catch {
        // A file that went away, or a server that did: it still lists, carrying nothing.
      }
    }
  };
  await Promise.all(Array.from({ length: 8 }, () => worker()));
  return texts;
}

/** A listing's nanosecond stamp as an ISO instant; null stays null ("unknown"). */
function isoOf(stampNs: number | null): string | null {
  return stampNs === null ? null : new Date(Math.floor(stampNs / 1e6)).toISOString();
}

/**
 * The rows the CLI's own ideas source can supply, keyed by path relative to `ideas/`.
 *
 * Every base in `kyosei/bases.yml` whose `source` is `ideas` is run, and their rows
 * are unioned. Running all of them rather than one named base is the honest reading
 * of "prefer the engine where it answers the question": a store may declare an ideas
 * base with any filter it likes (this repository's `unpromoted` filters to
 * `stamped: "false"`), so no single base covers the vault, and a union covers more of
 * it than any one does. What the union can never do is change the tree's membership,
 * because the tree's membership is the scan.
 *
 * A store that declares no ideas base at all is not a failure -- the tree is then the
 * scan alone, and says so.
 */
async function engineRows(
  project: ServedProject,
): Promise<{ rows: Map<string, Record<string, unknown>>; from: string[] }> {
  const rows = new Map<string, Record<string, unknown>>();
  const from: string[] = [];
  const catalogue = await readBases(project);
  for (const base of catalogue.bases) {
    if (base.source !== "ideas") continue;
    const result = await runBase(project, base.name);
    if (!result.ok) continue;
    from.push(`kyo base run ${base.name} --json`);
    for (const row of result.rows) {
      const id = typeof row.id === "string" ? row.id : "";
      // The engine's `id` is the path relative to the STORE root (`ideas/x.md`); the
      // tree's is relative to `ideas/`. One prefix, stripped in one place.
      if (!id.startsWith(`${IDEAS_DIR}/`)) continue;
      rows.set(id.slice(IDEAS_DIR.length + 1), row);
    }
  }
  return { rows, from };
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

/** `ideas/` as a file view, decorated with whatever the engine could say about it. */
export async function readIdeaTree(project: ServedProject): Promise<IdeaTree> {
  let listed: Awaited<ReturnType<ServedProject["ideaEntries"]>>;
  try {
    listed = await project.ideaEntries();
  } catch (error) {
    listed = { ok: false, problem: (error as Error).message };
  }
  if (!listed.ok) {
    return {
      ok: false,
      root: null,
      folders: [],
      files: [],
      from: TREE_SCAN,
      refusal: listed.problem,
    };
  }
  const root = listed.root;
  if (!listed.exists) {
    return { ok: true, root, folders: [], files: [], from: TREE_SCAN, refusal: null };
  }
  const scanned = scanIdeas(listed.entries);
  const [engine, texts] = await Promise.all([
    engineRows(project).catch(() => ({
      rows: new Map<string, Record<string, unknown>>(),
      from: [] as string[],
    })),
    markdownTexts(project, scanned.files),
  ]);
  const files: IdeaFileEntry[] = scanned.files.map((file) => {
    const row = engine.rows.get(file.path);
    const openable = file.name.toLowerCase().endsWith(".md");
    // Read from the file only when the engine had nothing to say about it, so the
    // ordinary case is the CLI's own answer and the fallback is visibly a fallback.
    let promoted = stringList(row?.promoted);
    let tags: string[] = [];
    if (row !== undefined) {
      const properties = row.properties;
      if (properties !== null && typeof properties === "object" && !Array.isArray(properties)) {
        tags = stringList((properties as Record<string, unknown>).tags).map((tag) =>
          tag.replace(/^#/, ""),
        );
      }
    }
    const text = texts.get(file.path);
    if (openable && text !== undefined) {
      // Body `#tags` are not a frontmatter property, so the engine never carries
      // them; the vault's own tags are the union of both (see `fileTags`).
      tags = [...new Set([...tags, ...fileTags(text)])];
      if (row === undefined) {
        promoted =
          readProperties(text).find((property) => property.key === "promoted")?.values ?? [];
      }
    }
    return {
      path: file.path,
      name: file.name,
      folder: file.folder,
      openable,
      promoted,
      tags,
      // Both stamps are the listing's (KYO-467): created is the birth time where the
      // server's disk keeps one; a server from before KYO-467 sends none ("unknown").
      createdAt: isoOf(file.createdNs),
      modifiedAt: isoOf(file.mtimeNs),
    };
  });
  return {
    ok: true,
    root,
    folders: scanned.folders,
    files,
    from:
      engine.from.length === 0
        ? `${TREE_SCAN} (this store declares no base over the ideas source)`
        : `${TREE_SCAN}, with rows from ${engine.from.join(", ")}`,
    refusal: null,
  };
}

// -------------------------------------------------------------- one idea file

export async function openIdea(project: ServedProject, path: string): Promise<IdeaDocument> {
  const empty = (problem: string): IdeaDocument => ({
    ok: false,
    path,
    absolutePath: null,
    body: "",
    properties: [],
    sections: [],
    createdAt: null,
    modifiedAt: null,
    base: "",
    version: null,
    problem,
  });
  if (path === "" || path.startsWith("/")) {
    return empty(`kyosei: ${JSON.stringify(path)} is not a path inside ideas/`);
  }
  let listed: Awaited<ReturnType<ServedProject["ideaEntries"]>>;
  let read: Awaited<ReturnType<ServedProject["readFile"]>>;
  try {
    [listed, read] = await Promise.all([project.ideaEntries(), project.readFile(path)]);
  } catch (error) {
    return empty((error as Error).message);
  }
  const absolute = listed.ok && listed.root !== null ? join(listed.root, path) : null;
  if (!read.ok) return { ...empty(read.problem), absolutePath: absolute };
  // The whole file and its version go to the tab, which keeps them as its merge base
  // and sends both with its save (KYO-575): the version is the tab's, not this process's.
  // The stamps the tree lists, for the editor's metadata line (KYO-257): the file's
  // mtime and, since KYO-467, its created time -- "unknown" only from an older server.
  const entry = listed.ok ? listed.entries.find((each) => each.path === path) : undefined;
  return {
    ok: true,
    path,
    absolutePath: absolute,
    body: splitFrontmatter(read.text).body,
    properties: readProperties(read.text),
    sections: readSections(read.text),
    createdAt: isoOf(entry?.createdNs ?? null),
    modifiedAt: isoOf(entry?.mtimeNs ?? null),
    base: read.text,
    version: read.etag,
    problem: null,
  };
}

/**
 * Write one idea file back: the edited body, and the edited frontmatter around it.
 *
 * Composed against the file the TAB loaded (`edit.base`), not a copy re-read here, so
 * that a key the properties tool does not model -- a nested mapping, a comment, a
 * quoting style -- survives the round trip as the bytes it was: `composeIdeaFile`
 * splices; it does not re-render a file from a parse. The write names the version the
 * tab loaded or last saved (`edit.version`, KYO-575), so a file changed since -- by
 * another window, another machine, an editor on disk -- is merged three ways rather
 * than replaced. Re-reading here to take the version there NOW is what this replaced:
 * it made the check cover only the milliseconds between that read and the write.
 */
export async function saveIdea(project: ServedProject, edit: IdeaEdit): Promise<SaveResult> {
  const refused = relativeProblem(edit.path);
  if (refused !== null) return refusedSave(null, refused);
  const path = await stated(project, edit.path);
  try {
    const mine = composeIdeaFile(edit.base, edit.properties, edit.body);
    const written = await writeMerging(project, edit.path, edit.base, edit.version, mine);
    if (!written.ok) return refusedSave(path, written.problem);
    return {
      ok: true,
      path,
      problem: null,
      version: written.version,
      base: written.text,
      body: splitFrontmatter(written.text).body,
      properties: readProperties(written.text),
      merged: written.merged,
    };
  } catch (error) {
    return refusedSave(path, (error as Error).message);
  }
}

/**
 * Create a new idea file from the scratchpad's selection.
 *
 * The human's hand on the human's surface: this writes a markdown file into `ideas/`
 * and does nothing else. No `kyo` runs, no record is created, nothing is stamped --
 * the file is intake (KYO-37's zero-ceremony layer), and what becomes of it is
 * `kyo promote`'s decision and a human's, made later and elsewhere.
 *
 * An existing file is never overwritten (`If-None-Match: *`). The scratchpad's Idea
 * button exists to capture a thought, and silently replacing a file whose name it
 * collided with would destroy writing to make room for writing.
 */
export async function createIdea(
  project: ServedProject,
  request: { folder: string; name: string; text: string },
): Promise<WriteResult> {
  const base = request.name.trim();
  if (base === "") {
    return { ok: false, path: null, problem: "an idea needs a name; nothing was written" };
  }
  // A name is a file name, not a path: a `/` in it would put the file somewhere the
  // human did not choose in the folder field, which is the one control that decides
  // where this lands.
  if (/[/\\]/.test(base)) {
    return {
      ok: false,
      path: null,
      problem:
        `"${base}" is a file name, not a path — choose the folder in the destination ` +
        "field beside it. Nothing was written.",
    };
  }
  const fileName = base.toLowerCase().endsWith(".md") ? base : `${base}.md`;
  const relativePath = request.folder === "" ? fileName : `${request.folder}/${fileName}`;
  const refused = relativeProblem(relativePath);
  if (refused !== null) return { ok: false, path: null, problem: refused };
  const path = await stated(project, relativePath);
  const text = request.text.replace(/\s+$/, "");
  try {
    const written = await project.writeFile(relativePath, text === "" ? "" : `${text}\n`, "create");
    if (written.ok) return { ok: true, path, problem: null };
    return {
      ok: false,
      path,
      problem:
        written.status === 412
          ? `${relativePath} already exists in ideas/ — nothing was written. Choose another name.`
          : written.problem,
    };
  } catch (error) {
    return { ok: false, path, problem: (error as Error).message };
  }
}

// ------------------------------------------------ organising ideas/ (KYO-250)
//
// Four writes, each a request kyo serve checks against the one boundary: a `..` in
// a name, an absolute path, or a symlink out of the vault is refused there with the
// sentence that names what would have been written and why not. Making a folder and
// renaming, moving or deleting an idea are the whole of the surface; nothing here reads
// or writes anything but the human's own files.

/** A folder name or file name the reader typed: one path segment, no separators. */
function segmentOrProblem(name: string, what: string): { ok: string } | { problem: string } {
  const trimmed = name.trim();
  if (trimmed === "") return { problem: `a ${what} needs a name; nothing was changed` };
  if (/[/\\]/.test(trimmed) || trimmed === "." || trimmed === "..") {
    return {
      problem: `"${trimmed}" is a ${what} name, not a path -- it may not contain a separator. Nothing was changed.`,
    };
  }
  if (trimmed.startsWith(".")) {
    return {
      problem: `"${trimmed}" would be hidden (a dotfile is the editor's furniture, not writing). Nothing was changed.`,
    };
  }
  return { ok: trimmed };
}

/** What kyo serve lists at one path under `ideas/`: a file, a folder, or nothing. */
async function entryAt(
  project: ServedProject,
  path: string,
): Promise<{ entries: IdeaEntry[]; entry: IdeaEntry | null } | { problem: string }> {
  const listed = await project.ideaEntries();
  if (!listed.ok) return { problem: listed.problem };
  return {
    entries: listed.entries,
    entry: listed.entries.find((each) => each.path === path) ?? null,
  };
}

export async function mkdirIdeas(
  project: ServedProject,
  request: { folder: string },
): Promise<WriteResult> {
  // An absolute path is not a folder inside the vault and is not made one by
  // trimming its slash: it is refused as what it is.
  if (request.folder.startsWith("/")) {
    return {
      ok: false,
      path: null,
      problem: `kyosei: ${JSON.stringify(request.folder)} is not a path inside ideas/`,
    };
  }
  const relative = request.folder.replace(/\/+$/g, "");
  if (relative === "") return { ok: false, path: null, problem: "a folder needs a name" };
  for (const segment of relative.split("/")) {
    const checked = segmentOrProblem(segment, "folder");
    // The whole request is named, not only the bad segment: a refusal that says
    // `".."` alone leaves the reader guessing which of their folders it was about.
    if ("problem" in checked) {
      return { ok: false, path: null, problem: `${relative}: ${checked.problem}` };
    }
  }
  const path = await stated(project, relative);
  try {
    const made = await project.mkdir(relative);
    if (!made.ok) return { ok: false, path, problem: made.problem };
    if (!made.created) return { ok: false, path, problem: `${relative} already exists in ideas/` };
    return { ok: true, path, problem: null };
  } catch (error) {
    return { ok: false, path, problem: (error as Error).message };
  }
}

/** Rename a file or folder in place; a file keeps its `.md` when the new name lacks one. */
export async function renameIdea(
  project: ServedProject,
  request: { path: string; name: string },
): Promise<WriteResult> {
  const checked = segmentOrProblem(request.name, "file or folder");
  if ("problem" in checked) return { ok: false, path: null, problem: checked.problem };
  const refused = relativeProblem(request.path);
  if (refused !== null) return { ok: false, path: null, problem: refused };
  try {
    const found = await entryAt(project, request.path);
    if ("problem" in found) return { ok: false, path: null, problem: found.problem };
    if (found.entry === null) {
      return {
        ok: false,
        path: await stated(project, request.path),
        problem: `${request.path} is not in ideas/; nothing was renamed`,
      };
    }
    const isFile = found.entry.type === "file";
    const name =
      isFile &&
      request.path.toLowerCase().endsWith(".md") &&
      !checked.ok.toLowerCase().endsWith(".md")
        ? `${checked.ok}.md`
        : checked.ok;
    const folder = dirname(request.path);
    const target = folder === "." ? name : `${folder}/${name}`;
    const path = await stated(project, target);
    if (target === request.path) return { ok: true, path, problem: null };
    if (found.entries.some((each) => each.path === target)) {
      return {
        ok: false,
        path,
        problem: `${target} already exists in ideas/; nothing was renamed`,
      };
    }
    const moved = await project.move(request.path, target);
    return moved.ok
      ? { ok: true, path, problem: null }
      : { ok: false, path, problem: moved.problem };
  } catch (error) {
    return { ok: false, path: null, problem: (error as Error).message };
  }
}

/** Move a file or folder into another folder ("" is the vault's root). */
export async function moveIdea(
  project: ServedProject,
  request: { path: string; folder: string },
): Promise<WriteResult> {
  const refused = relativeProblem(request.path);
  if (refused !== null) return { ok: false, path: null, problem: refused };
  const from = await stated(project, request.path);
  if (request.folder.startsWith("/")) {
    return {
      ok: false,
      path: from,
      problem: `kyosei: ${JSON.stringify(request.folder)} is not a path inside ideas/`,
    };
  }
  const base = request.path.split("/").at(-1) ?? request.path;
  const folder = request.folder.replace(/\/+$/g, "");
  const folderProblem = folder === "" ? null : relativeProblem(folder);
  if (folderProblem !== null) return { ok: false, path: from, problem: folderProblem };
  const target = folder === "" ? base : `${folder}/${base}`;
  if (folder === request.path || folder.startsWith(`${request.path}/`)) {
    return { ok: false, path: from, problem: `${request.path} cannot be moved into itself` };
  }
  try {
    const found = await entryAt(project, request.path);
    if ("problem" in found) return { ok: false, path: from, problem: found.problem };
    if (found.entry === null) {
      return {
        ok: false,
        path: from,
        problem: `${request.path} is not in ideas/; nothing was moved`,
      };
    }
    const path = await stated(project, target);
    if (target === request.path) return { ok: true, path, problem: null };
    if (found.entries.some((each) => each.path === target)) {
      return { ok: false, path, problem: `${target} already exists in ideas/; nothing was moved` };
    }
    const moved = await project.move(request.path, target);
    return moved.ok
      ? { ok: true, path, problem: null }
      : { ok: false, path, problem: moved.problem };
  } catch (error) {
    return { ok: false, path: from, problem: (error as Error).message };
  }
}

/**
 * Delete a file, or a folder and everything in it. The renderer asks first.
 *
 * kyo serve deletes one file at a time, each with the ETag it was listed at, and a
 * folder only once it is empty (a file is never deleted unseen). So a folder goes
 * deepest first: every file under it with its own ETag, then each folder, then it.
 */
export async function deleteIdea(
  project: ServedProject,
  request: { path: string },
): Promise<WriteResult> {
  const refused = relativeProblem(request.path);
  if (refused !== null) return { ok: false, path: null, problem: refused };
  const path = await stated(project, request.path);
  try {
    const found = await entryAt(project, request.path);
    if ("problem" in found) return { ok: false, path, problem: found.problem };
    if (found.entry === null) {
      return { ok: false, path, problem: `${request.path} is not in ideas/; nothing was deleted` };
    }
    const doomed =
      found.entry.type === "dir"
        ? found.entries
            .filter((each) => each.path.startsWith(`${request.path}/`))
            .sort((a, b) => b.path.split("/").length - a.path.split("/").length)
        : [];
    for (const each of [
      ...doomed.filter((e) => e.type !== "dir"),
      ...doomed.filter((e) => e.type === "dir"),
      found.entry,
    ]) {
      const gone = await project.deleteFile(each.path, each.type === "dir" ? null : each.etag);
      if (!gone.ok) return { ok: false, path, problem: gone.problem };
    }
    return { ok: true, path, problem: null };
  } catch (error) {
    return { ok: false, path, problem: (error as Error).message };
  }
}

// ------------------------------------------------------- what `[[` and `#` offer

/**
 * The completion sets, and each half comes from where it actually lives.
 *
 * `[[` offers records, over the read seams records mode already built
 * (`readRecordTree`): the app has one route to a record and this mode does not add a
 * second. `#` offers the tags that exist across `ideas/` today, read out of the files
 * -- there is no tag registry in v1, and `kyosei/gate-tags.yml` is a registry of GATE
 * tags on a different object, so borrowing it would offer a set unrelated to what is
 * written in these files. The renderer states that in as many words instead of
 * implying a registry exists.
 *
 * Both are closed sets, which is the point (KYO-55): the editor only inserts what is
 * in them, so a dangling `[[KYO-999]]` is not typeable by accident. Drift is
 * prevented at the input rather than flagged by a sweep afterwards.
 */
export const TAGS_FROM =
  "every #tag and frontmatter `tags:` value across ideas/ (there is no tag registry in v1)";

export async function readCompletions(project: ServedProject): Promise<Completions> {
  const tree = await readRecordTree(project, "kind").catch((error: unknown) => ({
    ok: false,
    records: [] as RecordCompletion[],
    currency: currencyFailure((error as Error).message),
    refusal: (error as Error).message,
    command: "",
  }));
  const records: RecordCompletion[] = (tree.records ?? []).map((entry) => ({
    id: entry.id,
    title: entry.title,
    kind: entry.kind,
    status: entry.status,
  }));
  const currency: CurrencyFacts = tree.currency ?? (await readCurrency(project));

  const tags = new Set<string>();
  let problem: string | null = tree.ok ? null : (tree.refusal ?? null);
  try {
    const listed = await project.ideaEntries();
    if (listed.ok && listed.exists) {
      const texts = await markdownTexts(project, scanIdeas(listed.entries).files);
      for (const text of texts.values()) for (const tag of fileTags(text)) tags.add(tag);
    } else if (!listed.ok) {
      problem = problem ?? `ideas/ could not be scanned for tags: ${listed.problem}`;
    }
  } catch (error) {
    problem = problem ?? `ideas/ could not be scanned for tags: ${(error as Error).message}`;
  }
  return {
    ok: records.length > 0 || tags.size > 0 || problem === null,
    records,
    tags: [...tags].sort((a, b) => a.localeCompare(b)),
    recordsFrom: tree.command ?? "",
    tagsFrom: TAGS_FROM,
    currency,
    problem,
  };
}

/** Exported for the checks: the scan is the tree's membership rule. */
export { scanIdeas };
