// `kyosei.ideas`'s frontmatter and section readers, ported once.
//
// This is the second rule in this app implemented twice, and like `grouping.ts` it
// is here under protest with the reason written down rather than assumed.
//
// The reason is forced by what ideas mode is. `kyo base run <an ideas base> --json`
// answers beautifully for files ON DISK: every row carries `properties` and
// `sections`, parsed by the CLI itself, and the tree uses exactly that (see
// `main/ideas.ts`). What no command answers for is the document the human is
// EDITING -- the buffer in the pane, three keystrokes old, never written. The
// properties tool has to know that a `- item` line just made `tags` a list, and the
// outline has to show a heading typed a second ago. Shelling out per keystroke to a
// process that reads the last saved bytes would answer a question nobody asked.
//
// So the port exists, and it is not trusted: `tests/seams.spec.ts` runs the CLI's
// own ideas base over a fixture vault and compares this file's answer against the
// engine's, file by file, for properties, sections and stub ids. If the CLI's
// parser changes, that check fails here rather than the app quietly disagreeing
// about what a file says.
//
// Every decision below is `ideas.py`'s, kept deliberately. Where one looks arbitrary
// it is transcribed with its reason, because the reasons are what a re-derivation
// would lose.

import type { IdeaProperty, IdeaSection } from "./protocol";

/** `scaffold.IDEAS_DIR` / `SCRATCHPAD_BASENAME`. */
export const IDEAS_DIR = "ideas";
/**
 * The name `ideas.idea_files` skips at any depth.
 *
 * Worth knowing while reading this record: the CLI has always excluded a file of
 * this name from the pool, so a scratchpad kept inside the vault was already
 * invisible to promotion. KYO-114 moves it out of `ideas/` altogether -- from
 * "excluded by a rule" to "not there by construction" -- and the tree honours the
 * exclusion anyway, so a consumer's leftover `ideas/scratchpad.md` lists as the CLI
 * sees it: not an idea.
 */
export const SCRATCHPAD_BASENAME = "scratchpad.md";

/** The file the session scratchpad is, inside the per-project home. */
export const SCRATCHPAD_FILENAME = "scratchpad.md";

/** `ideas.PROMOTED_KEY` / `PROMOTED_AT_KEY`: the stamps `kyo promote` writes. */
export const PROMOTED_KEY = "promoted";
export const PROMOTED_AT_KEY = "promoted_at";

/**
 * The two frontmatter keys this app renders and never edits.
 *
 * They are the store's own record of what was promoted and when. A properties panel
 * that let a human retype them would be an app surface asserting a promotion that
 * never happened -- a write past the seam (KYO-39) dressed as a text field.
 */
export const READ_ONLY_PROPERTIES: readonly string[] = [PROMOTED_KEY, PROMOTED_AT_KEY];

// `ideas._FENCE_RE`: `---` opens, `---` or `...` closes (both are YAML document-end
// markers; Obsidian writes the former).
const FENCE_RE = /^(?:---|\.\.\.)[ \t]*$/;
// `ideas._KEY_RE`: a top-level key line -- no indentation, not a comment, not an item.
const KEY_RE = /^([^\s:#-][^:]*):(?:\s|$)/;
// `ideas._FLOW_RE`: `key: [a, b]`. Read, never written.
const FLOW_RE = /^\[(.*)\]$/;
// `ideas._ITEM_RE`: the block-list continuation, the one shape that makes a value a list.
const ITEM_RE = /^\s*-(?:\s|$)/;
// `ideas._FENCE_LINE_RE`: a code fence, indented up to three spaces.
const FENCE_LINE_RE = /^ {0,3}(`{3,}|~{3,})/;
// `ideas._ATX_RE`. One to six `#` and then WHITESPACE -- CommonMark, and the reason
// it matters here is `ideas/` being an Obsidian vault full of `#tags`: `#tag` with no
// space is not a heading. Setext headings are deliberately not recognised.
const ATX_RE = /^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$/;
// `ideas._CLOSING_HASHES_RE`: `## Title ##`.
const CLOSING_HASHES_RE = /[ \t]+#+$/;
/**
 * `ideas._STUB_RE`, and the app matches it rather than the whole stub.
 *
 * Its known looseness travels with it, stated rather than fixed here: the pattern is
 * not fence-aware, so an *example* of a stub quoted inside prose reads as a real
 * promotion. That happens in this repository's own ideas tree. Tightening it in the
 * app would make this surface disagree with `kyo promote` about which sections are
 * stubs, which is worse than agreeing with it about an edge case -- the fix belongs
 * in `ideas.py`, and this comment is the pointer to it.
 */
const STUB_RE = /^[ \t]*>[ \t]*\*\*Promoted -> ([^*\s]+)\*\*/gm;

/** `prose.flatten`: whitespace runs collapsed, ends trimmed. */
export function flatten(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

export interface Frontmatter {
  /** The block's lines without its fences, or null when the file has none. */
  lines: string[] | null;
  /** Everything after it -- the text the editor pane shows. */
  body: string;
}

/**
 * `ideas.split_frontmatter`.
 *
 * `null` for the block means the file has no frontmatter, INCLUDING an opening
 * `---` that is never closed: that is a horizontal rule or an unfinished edit, not a
 * property block. Treating it as one would hide the top of a file behind a
 * properties panel the moment a human typed three dashes.
 */
export function splitFrontmatter(text: string): Frontmatter {
  if (!text.startsWith("---")) return { lines: null, body: text };
  const lines = text.split("\n");
  if ((lines[0] ?? "").replace(/[ \t]+$/, "") !== "---") return { lines: null, body: text };
  for (let index = 1; index < lines.length; index += 1) {
    if (FENCE_RE.test(lines[index] ?? "")) {
      return { lines: lines.slice(1, index), body: lines.slice(index + 1).join("\n") };
    }
  }
  return { lines: null, body: text };
}

/**
 * `ideas._segments`: the block cut into `(key, its lines)` runs, losing nothing.
 *
 * Reassembling the segments in order reproduces the input exactly, which is what
 * makes writing one property back a splice rather than a re-render -- a key this app
 * does not understand (a nested mapping, a trailing comment) survives an edit to the
 * key beside it byte for byte.
 */
export function frontmatterSegments(lines: string[]): { key: string | null; lines: string[] }[] {
  const segments: { key: string | null; lines: string[] }[] = [];
  let key: string | null = null;
  let current: string[] = [];
  for (const line of lines) {
    const match = KEY_RE.exec(line);
    if (match !== null) {
      if (current.length > 0 || key !== null) segments.push({ key, lines: current });
      key = (match[1] ?? "").trim();
      current = [line];
    } else {
      current.push(line);
    }
  }
  if (current.length > 0 || key !== null) segments.push({ key, lines: current });
  return segments;
}

function unquote(value: string): string {
  const trimmed = value.trim();
  if (trimmed.length >= 2 && trimmed[0] === trimmed[trimmed.length - 1]) {
    const quote = trimmed[0];
    if (quote === '"' || quote === "'") return trimmed.slice(1, -1).trim();
  }
  return trimmed;
}

/**
 * `ideas._values_in`: the scalar values one `key:` segment carries.
 *
 * Three shapes are read -- `key: one`, `key: [one, two]`, and a block list -- because
 * all three are things a human or Obsidian may leave behind. Only the block list is
 * ever written (see `renderProperty`), so this app speaks one dialect and
 * understands three, exactly as the CLI does.
 */
export function valuesIn(lines: string[]): string[] {
  const first = lines[0] ?? "";
  const colon = first.indexOf(":");
  const inline = colon === -1 ? "" : first.slice(colon + 1).trim();
  const values: string[] = [];
  if (inline !== "") {
    const flow = FLOW_RE.exec(inline);
    if (flow !== null) values.push(...(flow[1] ?? "").split(","));
    else values.push(inline);
  }
  for (const line of lines.slice(1)) {
    const stripped = line.trim();
    if (stripped.startsWith("-")) values.push(stripped.slice(1));
  }
  return values.map(unquote).filter((value) => value !== "");
}

/**
 * `ideas.frontmatter_properties`, as the right rail renders and edits it.
 *
 * The CLI returns a mapping of three value shapes (list, string, null); this returns
 * the same information as an ordered array, because the properties tool writes the
 * block back and the file's own key ORDER is part of what must survive that. A
 * mapping would have thrown it away on the first round trip.
 *
 * No key list anywhere decides which properties are legitimate -- law 13, the pool
 * has zero required ceremony. A key declared with no value is reported with no
 * values, which is a different fact from the key being absent, and a nested mapping
 * reads the same way: visible rather than silently dropped, and not editable into
 * something it was not.
 */
export function readProperties(text: string): IdeaProperty[] {
  const { lines } = splitFrontmatter(text);
  if (lines === null) return [];
  const properties: IdeaProperty[] = [];
  for (const segment of frontmatterSegments(lines)) {
    if (segment.key === null) continue;
    const values = valuesIn(segment.lines);
    const first = segment.lines[0] ?? "";
    const colon = first.indexOf(":");
    const inline = colon === -1 ? "" : first.slice(colon + 1).trim();
    const list = FLOW_RE.test(inline) || segment.lines.slice(1).some((line) => ITEM_RE.test(line));
    properties.push({
      key: segment.key,
      values,
      list,
      readOnly: READ_ONLY_PROPERTIES.includes(segment.key),
    });
  }
  return properties;
}

/** `ideas.stub_record_ids`: every record a promotion stub in `text` points at. */
export function stubRecordIds(text: string): string[] {
  const found: string[] = [];
  // A `g` regex carries `lastIndex` between calls; a fresh one per read is the only
  // way this function is safe to call twice on the same string.
  const pattern = new RegExp(STUB_RE.source, "gm");
  let match = pattern.exec(text);
  while (match !== null) {
    found.push(match[1] ?? "");
    match = pattern.exec(text);
  }
  return found;
}

function headingText(raw: string): string {
  return flatten(raw.replace(CLOSING_HASHES_RE, ""));
}

/** `ideas._scan_headings`: every ATX heading outside a code fence. */
function scanHeadings(body: string): { index: number; level: number; heading: string }[] {
  const found: { index: number; level: number; heading: string }[] = [];
  let fence: string | null = null;
  const lines = body.split("\n");
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    const marker = FENCE_LINE_RE.exec(line);
    if (marker !== null) {
      const character = (marker[1] ?? "")[0] ?? "";
      if (fence === null) fence = character;
      else if (fence === character) fence = null;
      continue;
    }
    // A fenced block is prose, not structure: a shell comment inside one is not a
    // heading, and the pre-KYO-107 scan had no notion of a fence at all.
    if (fence !== null) continue;
    const heading = ATX_RE.exec(line);
    if (heading !== null) {
      found.push({
        index,
        level: (heading[1] ?? "#").length,
        heading: headingText(heading[2] ?? ""),
      });
    }
  }
  return found;
}

/**
 * `ideas.sections`, as the outline tool draws it.
 *
 * Frontmatter is excluded BEFORE anything is scanned rather than filtered afterwards:
 * a YAML comment is a `#` at the start of a line and would otherwise read as a
 * level-1 heading. `line` is 1-based over the whole file, frontmatter included, which
 * is the CLI's own convention and the number a human sees in their editor's gutter.
 *
 * A section ends at the next heading of the SAME OR HIGHER level, so a subsection
 * travels with its parent -- which is what a human means by "this idea".
 */
export function readSections(text: string): IdeaSection[] {
  const { lines: fmLines, body } = splitFrontmatter(text);
  const offset = fmLines === null ? 0 : fmLines.length + 2;
  const lines = body.split("\n");
  const headings = scanHeadings(body);
  return headings.map((entry, position) => {
    let end = lines.length;
    for (const next of headings.slice(position + 1)) {
      if (next.level <= entry.level) {
        end = next.index;
        break;
      }
    }
    const own = lines
      .slice(entry.index + 1, end)
      .join("\n")
      .replace(/^\n+|\n+$/g, "");
    const promoted = stubRecordIds(own);
    return {
      heading: entry.heading,
      level: entry.level,
      line: offset + entry.index + 1,
      // The stub names exactly one record; a section carrying several stubs is one a
      // human assembled by hand, and naming the first is honest about what the
      // outline can show in a row. The count is not hidden -- `promotedTo` being set
      // at all is what the row marks.
      promotedTo: promoted[0] ?? null,
    };
  });
}

// ------------------------------------------------------------------- the tag set
//
// There is no tag registry in v1 -- not for ideas. `kyosei/gate-tags.yml` exists and
// is the registry for GATE tags, which are a different vocabulary on a different
// object; borrowing it here would offer a completion set that has nothing to do with
// what is written in these files. So the `#` completion set is the tags that already
// exist across `ideas/`, and the UI says exactly that rather than implying a registry.

/**
 * Every `#tag` in a body, in the vault's own sense of the word.
 *
 * Deliberately narrow. A tag must start the token (so a URL fragment and a `#` in
 * running prose are not tags), must be followed by a non-digit (so `#1` is an issue
 * number, which is what Obsidian does too), and stops at whitespace or punctuation
 * that cannot appear in a tag. Nothing here is a registry: this is a reader of what
 * is written, and its output is a set of strings that were typed by a human.
 */
export function readTags(text: string): string[] {
  const found = new Set<string>();
  for (const match of text.matchAll(/(?:^|[\s([])#([A-Za-z_][\w/-]*)/g)) {
    const tag = match[1];
    if (tag !== undefined && tag !== "") found.add(tag);
  }
  return [...found];
}

/**
 * The tags a file carries: its frontmatter `tags` property plus its body's `#tag`s.
 *
 * Both, because both are real in an Obsidian vault and a completion set offering only
 * one of them would be missing tags the human can see in their own editor. The
 * frontmatter side comes through the same property reader as everything else -- a
 * one-line `tags: a, b` is ONE string there and not two tags, which is the CLI's
 * reading and would be wrong to "improve" here.
 */
export function fileTags(text: string): string[] {
  const tags = new Set<string>(readTags(splitFrontmatter(text).body));
  for (const property of readProperties(text)) {
    if (property.key !== "tags") continue;
    for (const value of property.values) tags.add(value.replace(/^#/, ""));
  }
  return [...tags].filter((tag) => tag !== "");
}

// --------------------------------------------------------------- writing it back

/**
 * One property as frontmatter lines.
 *
 * A list is written as a block list and a scalar on its own line -- the one dialect
 * the CLI writes (`ideas._stamp_lines`), so a file this app saves is a file
 * `kyo promote` can still read and rewrite. A list stays a list even at one value:
 * the shape is what `promoted` has had since KYO-30, and collapsing it on save would
 * silently change what a Base query over `properties.tags` matches.
 */
export function renderProperty(property: IdeaProperty): string[] {
  if (property.list) {
    return [`${property.key}:`, ...property.values.map((value) => `  - ${value}`)];
  }
  return [`${property.key}: ${property.values[0] ?? ""}`.replace(/ $/, "")];
}

/**
 * Put an edited property block back on a body.
 *
 * A splice, not a re-render, wherever it can be: a key the human did not touch keeps
 * its own lines exactly, so a comment, a nested mapping or a quoting style this app
 * does not model survives the save. Only keys whose values actually changed are
 * rewritten, and a key the editor dropped is removed with its whole segment.
 */
export function composeIdeaFile(
  original: string,
  properties: IdeaProperty[],
  body: string,
): string {
  const { lines: fmLines } = splitFrontmatter(original);
  const wanted = new Map(properties.map((property) => [property.key, property]));
  const out: string[] = [];
  const seen = new Set<string>();

  for (const segment of frontmatterSegments(fmLines ?? [])) {
    if (segment.key === null) {
      out.push(...segment.lines);
      continue;
    }
    const property = wanted.get(segment.key);
    // Dropped by the properties tool: its whole segment goes, comments and all.
    if (property === undefined) continue;
    seen.add(segment.key);
    const existing = valuesIn(segment.lines);
    const unchanged =
      existing.length === property.values.length &&
      existing.every((value, index) => value === property.values[index]);
    out.push(...(unchanged ? segment.lines : renderProperty(property)));
  }
  for (const property of properties) {
    if (!seen.has(property.key)) out.push(...renderProperty(property));
  }

  if (out.length === 0) return body;
  return `---\n${out.join("\n")}\n---\n${body}`;
}
