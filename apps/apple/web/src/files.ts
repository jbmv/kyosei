// The Files view's logic (KYO-736): which roots have a chip, which rows a tree shows, how a
// file is drawn, which words of a reply may be file paths, and what the server's answer makes
// of each. It holds no state and touches no element, so that it can be tested without a page.
// The documents it reads are the file routes' (`files`, `files/<root>/tree`,
// `files/resolve`), as `kyo serve` returns them.

// ------------------------------------------------------------------------------- the roots

/** One root of `GET .../files`. */
export interface RootInfo {
  name: string;
  /** Where the root is on the server's machine. */
  path?: string;
  served: boolean;
  /** Set for a root that is one directory for each session, as `scratch` is. */
  per_session?: boolean;
}

/**
 * The roots that have a chip, in the server's order: those it says it serves, and a root
 * that is one directory for each session while a session is followed. Whether that session
 * has such a directory is the server's to say when its tree is asked for, and its refusal
 * names the remedy. A root the server refuses outright has no chip.
 */
export function servedRoots(document: unknown, session: string | null = null): RootInfo[] {
  const roots = (document as { roots?: unknown } | null)?.roots;
  if (!Array.isArray(roots)) return [];
  return roots.filter((root): root is RootInfo => {
    if (!root || typeof root !== "object" || typeof (root as RootInfo).name !== "string") return false;
    return (root as RootInfo).per_session === true ? !!session : (root as RootInfo).served === true;
  });
}

/** The root that is one directory for each session, which a request names the session for. */
export function isPerSession(roots: RootInfo[], name: string): boolean {
  return roots.some((root) => root.name === name && root.per_session === true);
}

const ROOT_LABELS: Record<string, string> = { code: "Code", store: "Kyosei", scratch: "Scratch" };

/** What a root's chip says: the store repository is Kyosei's. */
export function rootLabel(name: string): string {
  return ROOT_LABELS[name] ?? name.charAt(0).toUpperCase() + name.slice(1);
}

/** The root to show: the one chosen, when it is served, and otherwise the first. */
export function rootShown(chosen: string, roots: RootInfo[]): string | null {
  return roots.some((root) => root.name === chosen) ? chosen : roots[0]?.name ?? null;
}

// -------------------------------------------------------------------------------- the tree

/** One entry of a directory listing. */
export interface Entry {
  name: string;
  /** Relative to the root. */
  path: string;
  type: string;
  mtime_ns?: number;
  size?: number;
  media_type?: string;
  text?: boolean;
}

/** The entries of a `tree` document, or null when it is not one. */
export function entriesOf(document: unknown): Entry[] | null {
  const entries = (document as { entries?: unknown } | null)?.entries;
  if (!Array.isArray(entries)) return null;
  return entries.filter((entry): entry is Entry =>
    !!entry && typeof entry === "object" && typeof (entry as Entry).name === "string"
    && typeof (entry as Entry).path === "string");
}

/** The name a listing is kept under. */
export function dirKey(root: string, path: string): string {
  return `${root}:${path}`;
}

export function isDotfile(name: string): boolean {
  return name.startsWith(".");
}

/** Folders before files, each by name without regard to case. */
export function inOrder(entries: Entry[]): Entry[] {
  const folder = (entry: Entry) => (entry.type === "dir" ? 0 : 1);
  return [...entries].sort((a, b) =>
    folder(a) - folder(b)
    || a.name.toLowerCase().localeCompare(b.name.toLowerCase())
    || a.name.localeCompare(b.name));
}

export interface TreeRow {
  entry: Entry;
  depth: number;
  /** A folder that is open. */
  open: boolean;
  /** An open folder whose listing has not arrived. */
  waiting: boolean;
}

/**
 * The rows of one root's tree, top to bottom: each open folder is followed by its own
 * entries, one level deeper. Dotfiles are left out unless `dotfiles` is set, and with a
 * dotfolder goes everything in it.
 */
export function treeRows(
  root: string, listings: ReadonlyMap<string, Entry[]>, open: ReadonlySet<string>,
  dotfiles: boolean,
): TreeRow[] {
  const rows: TreeRow[] = [];
  const walk = (path: string, depth: number) => {
    for (const entry of inOrder(listings.get(dirKey(root, path)) ?? [])) {
      if (!dotfiles && isDotfile(entry.name)) continue;
      const isOpen = entry.type === "dir" && open.has(dirKey(root, entry.path));
      rows.push({
        entry, depth, open: isOpen,
        waiting: isOpen && !listings.has(dirKey(root, entry.path)),
      });
      if (isOpen) walk(entry.path, depth + 1);
    }
  };
  walk("", 0);
  return rows;
}

/** The folder a path is in: `docs` for `docs/plan.md`, and the root's own `""` above that. */
export function folderOf(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut < 0 ? "" : path.slice(0, cut);
}

export function nameOf(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

/** The folders above a path, outermost first and without the root: `a`, `a/b` for `a/b/c`. */
export function foldersAbove(path: string): string[] {
  const parts = path.split("/").filter(Boolean);
  return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/"));
}

// ------------------------------------------------------------------------------- one file

/** How a file is drawn. */
export type FileKind = "markdown" | "text" | "image" | "other";

export function fileKind(file: { name: string; media_type?: string; text?: boolean }): FileKind {
  const type = (file.media_type ?? "").split(";")[0].trim().toLowerCase();
  if (type === "text/markdown" || /\.(md|markdown)$/i.test(file.name)) return "markdown";
  if (type.startsWith("image/")) return "image";
  if (file.text === true || type.startsWith("text/")) return "text";
  return "other";
}

const LANGUAGES: Record<string, string> = {
  py: "python", pyi: "python", js: "javascript", mjs: "javascript", cjs: "javascript",
  jsx: "javascript", ts: "typescript", mts: "typescript", cts: "typescript", tsx: "typescript",
  json: "json", jsonl: "json", yml: "yaml", yaml: "yaml", toml: "ini", ini: "ini", cfg: "ini",
  conf: "ini", sh: "bash", bash: "bash", zsh: "bash", css: "css", html: "xml", htm: "xml",
  xml: "xml", svg: "xml", plist: "xml", swift: "swift", sql: "sql", diff: "diff", patch: "diff",
  md: "markdown", markdown: "markdown",
};

/**
 * The highlighter's language for a file, by its extension, or null for a type it does not
 * know, which is then drawn plain.
 */
export function languageOf(name: string): string | null {
  const base = nameOf(name);
  const dot = base.lastIndexOf(".");
  if (dot <= 0) return null;
  return LANGUAGES[base.slice(dot + 1).toLowerCase()] ?? null;
}

/** A size in the reader's words. */
export function sizeWords(bytes: number | undefined | null): string {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} byte${bytes === 1 ? "" : "s"}`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/**
 * A text file longer than this is shown as its head, with the rest offered: drawing and
 * colouring every line of a large file holds the page still.
 */
export const HEAD_CHARS = 400_000;
/** A text longer than this is drawn plain when the whole of it is asked for. */
export const HIGHLIGHT_CHARS = 1_500_000;

export interface Head { text: string; whole: boolean }

/** The head of a text: all of it when short, and otherwise its lines up to `limit`. */
export function headOf(text: string, limit = HEAD_CHARS): Head {
  if (text.length <= limit) return { text, whole: true };
  const cut = text.lastIndexOf("\n", limit);
  return { text: text.slice(0, cut > 0 ? cut + 1 : limit), whole: false };
}

/** How many lines a text has. A last line with no newline after it counts. */
export function lineCount(text: string): number {
  if (!text) return 0;
  let count = 0;
  for (let at = text.indexOf("\n"); at >= 0; at = text.indexOf("\n", at + 1)) count += 1;
  return text.endsWith("\n") ? count : count + 1;
}

// ------------------------------------------------------------------- paths in what is said

/** A stretch of a text that may be a file path, as it would be asked of the server. */
export interface PathSpan { from: number; to: number; text: string }

const SEGMENT = "[A-Za-z0-9_.@+%~=-]+";
const PATH_LIKE = new RegExp(
  "(?<![A-Za-z0-9_.@+%~=\\-/:\\\\<])"
  + `((?:~|\\.)?/${SEGMENT}(?:/${SEGMENT})*/?|${SEGMENT}(?:/${SEGMENT})+/?)`
  + "(:\\d+(?:[:-]\\d+)?)?",
  "g");
const EXTENSION = /\.(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{1,10}$/;
const LINE_SUFFIX = /:(\d+)(?:[:-]\d+)?$/;

/** Whether a path is one worth asking the server about. */
function askable(path: string): boolean {
  const parts = path.split("/").filter(Boolean);
  if (parts.some((part) => part === "..")) return false;
  if (path.startsWith("~/")) return parts.length >= 2;
  // One segment after the slash is a command such as /clear, far more often than a file.
  if (path.startsWith("/")) return parts.length >= 2;
  if (path.startsWith("./")) return parts.length >= 2 && (EXTENSION.test(path) || path.endsWith("/"));
  // A relative path has a slash, and an extension or a closing slash; `and/or` has neither.
  return parts.length >= 2 && (path.endsWith("/") || EXTENSION.test(path));
}

/**
 * The stretches of a text that may be file paths: an absolute path, a path under `~/`, or a
 * relative path with a slash and an extension, each with or without a line after a colon.
 * Whether one is a file is the server's to say; this only decides what is asked.
 */
export function pathSpans(text: string): PathSpan[] {
  const spans: PathSpan[] = [];
  for (const match of text.matchAll(PATH_LIKE)) {
    let path = match[1];
    let line = match[2] ?? "";
    // The full stop that ends a sentence is not the path's.
    const tidy = path.replace(/\.+$/, "");
    if (tidy !== path) {
      path = tidy;
      line = "";
    }
    // A touch on a record's page marks a new file with a plus sign before its path.
    const lead = /^\++/.exec(path)?.[0].length ?? 0;
    path = path.slice(lead);
    if (!path || !askable(path)) continue;
    const found = path + line;
    const from = match.index + lead;
    spans.push({ from, to: from + found.length, text: found });
  }
  return spans;
}

/**
 * A code span that is one absolute path and nothing else, which may then hold spaces, as a
 * path under `Application Support` does.
 */
export function wholePath(text: string): string | null {
  const trimmed = text.trim();
  if (!/^(~\/|\/)[^\n]*\S$/.test(trimmed) || !trimmed.includes(" ")) return null;
  if (/[<>|*?"`$;&]|\s-{1,2}\w|\s\/|\s~\//.test(trimmed)) return null;
  const path = trimmed.replace(LINE_SUFFIX, "");
  return path.split("/").filter(Boolean).length >= 2 ? trimmed : null;
}

/** A path as asked, without the line after it. */
export function withoutLine(given: string): string {
  return given.replace(LINE_SUFFIX, "");
}

function decoded(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

/** The line a link's fragment names, as `#L12` does, or null. */
function fragmentLine(fragment: string): number | null {
  const found = /^L(\d+)/.exec(fragment);
  return found ? Number(found[1]) : null;
}

/**
 * The path a markdown link's address names, as it would be asked of the server, when the
 * address has no scheme: `docs/plan.md`, and `src/a.py:12` for `src/a.py#L12`. Null for an
 * address that is only a fragment.
 */
export function hrefPath(href: string): string | null {
  const [before, fragment = ""] = href.trim().split("#", 2);
  const path = decoded(before.split("?")[0]);
  if (!path || /^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith("//")) return null;
  const line = fragmentLine(fragment);
  return line === null ? path : `${path}:${line}`;
}

/** Where a link in a served markdown file leads. */
export type LinkPlace =
  /** A file of the same root. */
  | { kind: "inside"; path: string; line: number | null }
  /** A path only the server can place: an absolute one, or one under `~/`. */
  | { kind: "ask"; given: string };

/**
 * Where a link written in the file at `folder` leads. A relative address is read against the
 * file's own folder and stays in its root; one that climbs above the root leads nowhere.
 */
export function linkPlace(folder: string, href: string): LinkPlace | null {
  const given = hrefPath(href);
  if (given === null) return null;
  if (given.startsWith("/") || given.startsWith("~/")) return { kind: "ask", given };
  const found = LINE_SUFFIX.exec(given);
  const line = found ? Number(found[1]) : null;
  const parts = folder.split("/").filter(Boolean);
  for (const part of withoutLine(given).split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") {
      if (!parts.length) return null;
      parts.pop();
    } else {
      parts.push(part);
    }
  }
  return { kind: "inside", path: parts.join("/"), line };
}

// ---------------------------------------------------------------------- the server's answers

/** What the server made of one path. */
export type Answer =
  | {
    kind: "file"; root: string; path: string; line: number | null; name: string;
    /** `file`, `dir` or `symlink`. */
    type: string; media_type?: string; text?: boolean; size?: number;
  }
  /** An absolute path outside every root the server serves. */
  | { kind: "outside"; path: string; line: number | null }
  /** No root holds it. */
  | { kind: "none" };

/** The answer in a `files/resolve` document, or null when the document is not one. */
export function answerOf(given: string, document: unknown): Answer | null {
  const doc = document as Record<string, unknown> | null;
  if (!doc || typeof doc !== "object" || doc.ok !== true) return null;
  const line = typeof doc.line === "number" ? doc.line : null;
  if (doc.found === true && typeof doc.root === "string" && typeof doc.path === "string") {
    return {
      kind: "file", root: doc.root, path: doc.path, line,
      name: typeof doc.name === "string" && doc.name ? doc.name : nameOf(doc.path),
      type: typeof doc.type === "string" ? doc.type : "file",
      media_type: typeof doc.media_type === "string" ? doc.media_type : undefined,
      text: typeof doc.text === "boolean" ? doc.text : undefined,
      size: typeof doc.size === "number" ? doc.size : undefined,
    };
  }
  // A file inside a root that git does not show (build output) is not served either, and is
  // offered as an outside path is: copied, or revealed where it can be reached.
  if (doc.reason === "outside-every-root" || doc.reason === "not-shown") {
    return { kind: "outside", path: line === null ? given.trim() : withoutLine(given.trim()), line };
  }
  return { kind: "none" };
}

/** The entry a found answer stands for, as a listing would have given it. */
export function entryOf(answer: Extract<Answer, { kind: "file" }>): Entry {
  return {
    name: answer.name, path: answer.path, type: answer.type, media_type: answer.media_type,
    text: answer.text, size: answer.size,
  };
}

const FILE_TOPS = new Set([
  "Users", "home", "tmp", "private", "var", "etc", "opt", "usr", "Volumes", "Library",
  "Applications", "System", "mnt", "srv", "root",
]);

/**
 * Whether a path outside every root is drawn as a link at all. The server compares text and
 * looks at nothing outside a root, so a route such as `/v1/projects` gets the same answer as a
 * file would; only a path under the home folder, under one of the usual top folders, or with
 * an extension is taken for a file.
 */
export function looksOutside(path: string): boolean {
  if (path.startsWith("~/")) return true;
  if (!path.startsWith("/")) return false;
  const parts = path.split("/").filter(Boolean);
  return FILE_TOPS.has(parts[0] ?? "") || EXTENSION.test(parts.at(-1) ?? "");
}

/** How long an answer of "no root holds it" stands before the path is asked again. */
export const NONE_STANDS_MS = 60_000;

/**
 * Whether an answer is still to be used. A found file and an outside path stand for the
 * session; a path no root held may have been written since, so it is asked again later.
 */
export function stands(answer: Answer, askedAt: number, now: number): boolean {
  return answer.kind !== "none" || now - askedAt < NONE_STANDS_MS;
}

// -------------------------------------------------------------- mentioned in this session

/** A served file a reply or a prompt named. */
export interface Mention {
  root: string;
  path: string;
  line: number | null;
  /** When the message that named it was written. */
  at: string;
  entry: Entry;
}

/** The list after one more mention: each file once, under its latest mention, newest first. */
export function mentioned(list: Mention[], item: Mention): Mention[] {
  const same = (other: Mention) => other.root === item.root && other.path === item.path;
  const before = list.find(same);
  const kept = before && before.at > item.at ? before : item;
  return [kept, ...list.filter((other) => !same(other))]
    .sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
}

// ------------------------------------------------------------------ linked in this chat

/**
 * A link in a prompt or a reply of the chat, as the chat's side bar lists it (2026-11-11):
 * a served file, a path outside the served folders, a record or a web address. Unlike
 * `Mention`, which Files lists, it counts only what the chat shows as the person's and the
 * assistant's words, never a subagent's hand-back or a command's output.
 */
export interface ChatLink {
  /** What makes two links one: the file's root and path, the outside path, the record, the address. */
  key: string;
  kind: "file" | "outside" | "record" | "url";
  name: string;
  place: string;
  /** When the message holding it was written. */
  at: string;
  /** The address the row follows: `#` for a path, which is followed by `given`. */
  href: string;
  /** For a path, the words the chat gave it, by which the server's answer is found. */
  given?: string;
}

/** The list after one more link: each once, under its latest message, newest message first. */
export function withLink(list: ChatLink[], item: ChatLink): ChatLink[] {
  const before = list.find((other) => other.key === item.key);
  const kept = before && before.at > item.at ? before : item;
  // A later link of the same message goes after the earlier, so a message reads in order.
  return [...list.filter((other) => other.key !== item.key), kept]
    .sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
}

/**
 * The link an anchor of the chat is, when it is a record or a web address. A file's link is
 * made from the server's answer instead. The renderer makes a web address of a bare file name
 * such as `README.md`, since `.md` is also a country's domain; such an address is no link
 * the writer meant and gives null. `record` is the record id the address names, if any.
 */
export function addressLink(
  href: string, text: string, at: string, record: string | null,
): ChatLink | null {
  if (/^\s*kyosei:/i.test(href)) {
    return record
      ? { key: `record:${record}`, kind: "record", name: text.trim() || record, place: "Record", at, href }
      : null;
  }
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  const shown = text.trim();
  if (href === `http://${shown}` && !shown.includes("/")) return null;
  const bare = `${url.host}${url.pathname === "/" ? "" : url.pathname}`;
  const name = shown && shown !== href ? shown : bare;
  return { key: `url:${url.href}`, kind: "url", name, place: url.host, at, href };
}

/**
 * The web addresses written out in a prompt's text, which the chat draws as plain text: each
 * `http://` or `https://` run up to a space, without the punctuation that ends a sentence.
 */
export function webAddresses(text: string): string[] {
  const found: string[] = [];
  for (const match of text.matchAll(/\bhttps?:\/\/[^\s<>"'`]+/g)) {
    let address = match[0].replace(/[.,;:!?]+$/, "");
    // A closing bracket belongs to the address only when it opened one.
    while (/[)\]]$/.test(address)) {
      const close = address.endsWith(")") ? ")" : "]";
      const open = close === ")" ? "(" : "[";
      if (address.split(open).length >= address.split(close).length) break;
      address = address.slice(0, -1).replace(/[.,;:!?]+$/, "");
    }
    found.push(address);
  }
  return found;
}

// ------------------------------------------------------------- a path on this machine

/** A prefix of a path on the server's machine, and what it stands for on this one. */
export interface Mapping { server: string; here: string }

const trimmed = (path: string) => (path.length > 1 ? path.replace(/\/+$/, "") : path);

/**
 * Where a path on the server's machine is on this one, or null when it has no place here.
 * A mapping whose prefix the path begins with is applied, the longest first; without one
 * the path is its own when the server's machine is this one.
 */
export function localPath(path: string, sameMachine: boolean, mappings: Mapping[]): string | null {
  const usable = mappings
    .map((mapping) => ({ server: trimmed(mapping.server.trim()), here: trimmed(mapping.here.trim()) }))
    .filter((mapping) => mapping.server && mapping.here)
    .sort((a, b) => b.server.length - a.server.length);
  for (const mapping of usable) {
    if (path === mapping.server) return mapping.here;
    const prefix = mapping.server.endsWith("/") ? mapping.server : `${mapping.server}/`;
    if (path.startsWith(prefix)) {
      return `${mapping.here.endsWith("/") ? mapping.here : `${mapping.here}/`}${path.slice(prefix.length)}`;
    }
  }
  return sameMachine ? path : null;
}

/** A file's absolute path on the server's machine, from its root's. */
export function serverPath(roots: RootInfo[], root: string, path: string): string | null {
  const base = roots.find((info) => info.name === root)?.path;
  if (!base) return null;
  return path ? `${trimmed(base)}/${path}` : base;
}
