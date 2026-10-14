// Registry-backed autocomplete: `[[` offers records, `#` offers tags (KYO-55), and --
// new in this record -- `[[<record-id>.` offers that record's own child ids (KYO-234.c5).
//
// Recovered from v2's `renderer/ideas/complete.ts`, which this
// module is a close port of; see that file's own header for the law this encodes. What
// is added here is the third trigger kind, and the closed-set discipline extends to it
// unchanged: a child id is offered only when it is present on the record document the
// bridge actually returned, so `[[KYO-226.c9]]` is not typeable by accident just because
// `[[KYO-226.c1]]` exists.
//
// The trigger matching stays deliberately conservative: a token this file does not
// recognise offers nothing at all, rather than guessing at what was meant.

import type { RecordCompletion, RecordDocument } from "../../../shared/protocol";

/** What kind of thing the caret is in the middle of typing. */
export type TriggerKind = "record" | "tag" | "child";

export interface Trigger {
  kind: TriggerKind;
  /** What has been typed after the `[[`, the `#`, or the child id's `.`. */
  query: string;
  /** Where the trigger token starts, so an insertion replaces the whole of it. */
  start: number;
  /** Set only for `kind: "child"`: the record id the dot followed. */
  recordId?: string;
}

/**
 * A `[[` that is still open: no `]]` and no newline between it and the caret.
 *
 * A newline ends it because a wiki link does not span lines in any editor that reads
 * these files, and letting it span one would leave the popup open over a paragraph the
 * human moved on from.
 */
const RECORD_TRIGGER = /\[\[([^\]\n]*)$/;

/**
 * A `[[<record-id>.` still open: the record-id shape is loose on purpose -- it only
 * decides WHEN to ask the bridge for that record's children, never which ids exist.
 * The closed set (KYO-55) is enforced by `matchChildIds` filtering against the record
 * document the bridge actually returned, not by this pattern being exact.
 */
const CHILD_TRIGGER = /\[\[([A-Z][A-Z0-9]{1,15}-[1-9][0-9]*)\.([A-Za-z0-9]*)$/;

/**
 * A `#` that begins a token.
 *
 * `(?:^|[\s([])` is what keeps this from firing inside a URL fragment or mid-word, and
 * requiring a letter or `_` first is what keeps `#1` an issue number rather than a tag
 * -- both are Obsidian's own readings, and this surface is the vault's.
 */
const TAG_TRIGGER = /(?:^|[\s([])#([A-Za-z_][\w/-]*)?$/;

/**
 * What the caret is in the middle of, if anything.
 *
 * `null` is the ordinary answer and the important one: a surface that offered a list
 * whenever it was not sure would put a popup over the text on most keystrokes. The
 * child trigger is tried first because it is the more specific reading of the same
 * `[[` prefix the record trigger also matches.
 */
export function triggerAt(text: string, caret: number): Trigger | null {
  const before = text.slice(0, Math.max(0, Math.min(caret, text.length)));
  const child = CHILD_TRIGGER.exec(before);
  if (child !== null) {
    const recordId = child[1] ?? "";
    const query = child[2] ?? "";
    return { kind: "child", recordId, query, start: child.index };
  }
  const record = RECORD_TRIGGER.exec(before);
  if (record !== null) {
    return { kind: "record", query: record[1] ?? "", start: record.index };
  }
  const tag = TAG_TRIGGER.exec(before);
  if (tag !== null) {
    const query = tag[1] ?? "";
    // `tag.index` is the start of the whitespace the pattern consumed, not of the `#`;
    // the token begins one character later unless the match began at the start.
    const hash = before.length - query.length - 1;
    return { kind: "tag", query, start: hash };
  }
  return null;
}

export interface Suggestion {
  /** What goes in the document, without its `[[ ]]` or `#`. */
  value: string;
  /** The primary line in the list. */
  label: string;
  /** The secondary line -- a record's title, what a tag is, or a child's own kind. */
  detail: string;
}

/** How many rows the popup shows. Enough to choose from, few enough to read. */
export const MAX_SUGGESTIONS = 8;

/**
 * Records matching `query` on id AND title, in that order of preference.
 *
 * The record says "matching id and title", so a reader who remembers "the bubbles one"
 * finds KYO-113 without remembering its number, and one who remembers the number does
 * not have to scroll past every title that happens to contain it.
 */
export function matchRecords(records: readonly RecordCompletion[], query: string): Suggestion[] {
  const needle = query.trim().toLowerCase();
  const byId: RecordCompletion[] = [];
  const byTitle: RecordCompletion[] = [];
  for (const record of records) {
    const id = record.id.toLowerCase();
    const title = record.title.toLowerCase();
    if (needle === "" || id.includes(needle)) byId.push(record);
    else if (title.includes(needle)) byTitle.push(record);
  }
  return [...byId, ...byTitle].slice(0, MAX_SUGGESTIONS).map((record) => ({
    value: record.id,
    label: record.id,
    detail: record.title === "" ? `${record.kind} · ${record.status}` : record.title,
  }));
}

/** Tags matching `query`, prefix matches first -- the ones a typist meant. */
export function matchTags(tags: readonly string[], query: string): Suggestion[] {
  const needle = query.trim().toLowerCase();
  const prefix: string[] = [];
  const contains: string[] = [];
  for (const tag of tags) {
    const lower = tag.toLowerCase();
    if (needle === "" || lower.startsWith(needle)) prefix.push(tag);
    else if (lower.includes(needle)) contains.push(tag);
  }
  return [...prefix, ...contains].slice(0, MAX_SUGGESTIONS).map((tag) => ({
    value: tag,
    label: `#${tag}`,
    detail: "a tag that already exists in ideas/",
  }));
}

/** One child id, gathered off a record document, with what kind of object it is. */
interface ChildRow {
  id: string;
  what: string;
}

/**
 * Every child id a record document carries, across the seven shapes KYO-234.c5 names
 * (criteria, gates, claims, notes, evidence, flags, dispatches) -- the read model's own
 * arrays, never a re-derivation of the id grammar `main/links.ts` owns. Touches, reports
 * and verifications are not in this list because `[[<id>.` is what a human types about a
 * record's own children while composing, and those three either have no separate page
 * (a report is read through its dispatch) or are not something KYO-234.c5 asks for.
 */
function childRows(doc: RecordDocument): ChildRow[] {
  const rows: ChildRow[] = [];
  for (const criterion of doc.criteria) rows.push({ id: criterion.id, what: "criterion" });
  for (const gate of doc.gates) rows.push({ id: gate.id, what: "gate" });
  for (const claim of doc.claims) rows.push({ id: claim.id, what: "claim" });
  for (const note of doc.notes) rows.push({ id: note.id, what: "note" });
  for (const item of doc.evidence) rows.push({ id: item.id, what: "evidence" });
  for (const flag of doc.flags) rows.push({ id: flag.id, what: "flag" });
  for (const dispatch of doc.dispatches) rows.push({ id: dispatch.id, what: "dispatch" });
  return rows;
}

/**
 * Child ids of `doc` matching `query`, closed against exactly what the record document
 * holds right now (KYO-55 extended to child shapes, KYO-234.c5). `doc` may be null while
 * the bridge read is still out, or for a record that does not exist -- either way the
 * offer is empty rather than guessed.
 */
export function matchChildIds(
  doc: RecordDocument | null,
  recordId: string,
  query: string,
): Suggestion[] {
  if (doc === null || !doc.ok || doc.id !== recordId) return [];
  const needle = query.trim().toLowerCase();
  const rows = childRows(doc).filter(
    (row) => needle === "" || row.id.toLowerCase().includes(needle),
  );
  return rows.slice(0, MAX_SUGGESTIONS).map((row) => ({
    value: row.id,
    label: row.id,
    detail: row.what,
  }));
}

/**
 * What the trigger under the caret may offer, out of the closed sets -- the record and
 * tag cases only. The child case needs a bridge read keyed on `trigger.recordId` and is
 * therefore async; see `editor.ts`'s `closedSetSource`, which calls `matchChildIds`
 * directly once that read lands.
 */
export function suggestionsFor(
  trigger: Trigger,
  sets: { records: readonly RecordCompletion[]; tags: readonly string[] } | null,
): Suggestion[] {
  if (sets === null) return [];
  if (trigger.kind === "record") return matchRecords(sets.records, trigger.query);
  if (trigger.kind === "tag") return matchTags(sets.tags, trigger.query);
  return [];
}

export interface Insertion {
  text: string;
  /** Where the caret lands afterwards -- past the closing `]]`, ready to keep typing. */
  caret: number;
}

/**
 * Accept a suggestion: replace the whole trigger token with the finished reference.
 *
 * The token is replaced rather than appended to, so a half-typed `[[KYO-1` becomes
 * `[[KYO-113]]` and never `[[KYO-1KYO-113]]`. An already-present `]]` immediately after
 * the caret is consumed rather than duplicated -- editors that auto-close brackets leave
 * one there, and two closing pairs is the defect a reader would then have to notice and
 * fix by hand. `child` shares the record case's bracket wrapping: both are `[[id]]`
 * forms, and the id is already the full one (`KYO-226.c1`), not just what followed the
 * dot.
 */
export function applySuggestion(
  text: string,
  caret: number,
  trigger: Trigger,
  suggestion: Suggestion,
): Insertion {
  const head = text.slice(0, trigger.start);
  let tailStart = caret;
  let inserted: string;
  if (trigger.kind === "record" || trigger.kind === "child") {
    inserted = `[[${suggestion.value}]]`;
    if (text.slice(caret, caret + 2) === "]]") tailStart = caret + 2;
  } else {
    inserted = `#${suggestion.value}`;
  }
  return { text: head + inserted + text.slice(tailStart), caret: head.length + inserted.length };
}

/** A `[[id]]` reference under an offset, for the editor's click-to-follow (KYO-234.c5). */
export function linkAt(text: string, offset: number): string | null {
  const LINK_RE = /\[\[([^\]\n]+)\]\]/g;
  for (const match of text.matchAll(LINK_RE)) {
    const start = match.index;
    const end = start + match[0].length;
    if (offset >= start && offset <= end) return match[1] ?? null;
  }
  return null;
}
