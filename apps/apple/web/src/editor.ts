/**
 * A composer box's editor on CodeMirror 6 (KYO-906, KYO-909). The document is the text as
 * typed, which is what is saved and sent; everything else here is drawn over it or kept beside
 * it: the marks each line holds, the gutter that shows them, the hanging indent of wrapped
 * rows, the indent guides, the heading rules, the folded sections (KYO-913) and the lines
 * beside a heading's section (KYO-917). The composer's boxes are built here (KYO-910).
 *
 * What the composer decides (drafts, sending, the mark's panel, uploads) stays in `chat.ts`,
 * which reaches this module through the callbacks of `EditorOptions` and the functions below.
 */
import {
  EditorSelection, EditorState, type Extension, Prec, RangeSet, StateEffect, StateField, type Text,
  type Transaction,
} from "@codemirror/state";
import {
  Decoration, type DecorationSet, EditorView, GutterMarker, ViewPlugin, type ViewUpdate, gutter, gutters,
  keymap, placeholder,
} from "@codemirror/view";
import {
  history, historyKeymap, insertNewline, invertedEffects, isolateHistory, standardKeymap,
} from "@codemirror/commands";
import {
  codeFolding, foldEffect, foldKeymap, foldService, foldable, foldedRanges, unfoldEffect,
} from "@codemirror/language";
import { shiftLines } from "./marks.ts";
import { INDENT_WIDTH, headingSections, lineShapes, sectionEnd } from "./outline.ts";

// ---------------------------------------------------------------- marks (proposal 5.2)

/** What a box's lines hold, kept beside the text and carried through its edits. */
export interface LineMarks<T> {
  /** One id for each line of the document, kept by a line while any of its text survives. */
  ids: number[];
  /** What each line holds, by the line's id. A line that holds nothing has no entry. */
  items: Map<number, T[]>;
  /** What a removed line held, by that line's text, so undoing or pasting it back restores it. */
  lost: Map<string, T[]>;
  /** Raised by `refreshMarks`, so that markers whose look depends on the page are redrawn. */
  version: number;
}

/** One item placed on, or removed from, the line that `pos` is in. */
interface ItemChange { pos: number; item: unknown; index?: number }

const mapItem = (change: ItemChange, mapping: { mapPos(pos: number, assoc?: number): number }) =>
  ({ ...change, pos: mapping.mapPos(change.pos, -1) });

/** Places an item on a line. In a transaction of its own it is one undo step. */
export const placeEffect = StateEffect.define<ItemChange>({ map: mapItem });
/** Removes an item from a line, by identity; also an undo step. */
export const removeEffect = StateEffect.define<ItemChange>({ map: mapItem });
const refreshEffect = StateEffect.define<null>();

let nextId = 1;
const freshId = () => nextId++;

function linesOf(text: string): string[] {
  return text.split("\n");
}

/**
 * Carries the lines' ids across one replaced range, by `shiftLines`' rules, as `trackEdit`
 * does in the text area: joined lines keep both lines' items, a removed line's items are kept
 * by its text, and a line that is back takes up what it held.
 */
function carry<T>(marks: LineMarks<T>, before: string, after: string, caret: number): LineMarks<T> {
  const shift = shiftLines(before, after, marks.ids, freshId, caret);
  if (shift.ids === marks.ids && !shift.dropped.length && !shift.merged.length) return marks;
  const items = new Map(marks.items);
  const lost = new Map(marks.lost);
  const old = linesOf(before);
  for (const [from, into] of shift.merged) {
    const moved = items.get(from);
    if (moved) items.set(into, [...(items.get(into) ?? []), ...moved]);
    items.delete(from);
  }
  for (const id of shift.dropped) {
    const held = items.get(id);
    const text = old[marks.ids.indexOf(id)];
    if (held && text) lost.set(text, held);
    items.delete(id);
  }
  const now = linesOf(after);
  shift.ids.forEach((id, line) => {
    const back = now[line] && !items.has(id) ? lost.get(now[line]) : undefined;
    if (!back) return;
    items.set(id, back);
    lost.delete(now[line]);
  });
  return { ...marks, ids: shift.ids, items, lost };
}

/**
 * Applies each change of a transaction in turn, so that an edit of several ranges (Tab over
 * a selection indents each line by its own change) carries each line's id on its own. The
 * caret given to `shiftLines` is the end of the inserted text, behind which nothing changed.
 */
function carryChanges<T>(marks: LineMarks<T>, tr: Transaction): LineMarks<T> {
  let text = tr.startState.doc.toString();
  let carried = marks;
  tr.changes.iterChanges((fromA, toA, fromB, toB, inserted) => {
    const start = fromB;
    const end = fromB + (toA - fromA);
    const next = text.slice(0, start) + inserted.toString() + text.slice(end);
    carried = carry(carried, text, next, toB);
    text = next;
  });
  return carried;
}

function changeItems<T>(marks: LineMarks<T>, doc: Text, change: ItemChange, place: boolean): LineMarks<T> {
  const line = doc.lineAt(Math.max(0, Math.min(change.pos, doc.length))).number - 1;
  const id = marks.ids[line];
  if (id === undefined) return marks;
  const held = marks.items.get(id) ?? [];
  const item = change.item as T;
  let now: T[];
  if (place) {
    const at = change.index === undefined ? held.length : Math.min(change.index, held.length);
    now = [...held.slice(0, at), item, ...held.slice(at)];
  } else {
    if (!held.includes(item)) return marks;
    now = held.filter((other) => other !== item);
  }
  const items = new Map(marks.items);
  if (now.length) items.set(id, now);
  else items.delete(id);
  return { ...marks, items };
}

function initialMarks<T>(doc: Text, saved: { line: number; items: T[] }[]): LineMarks<T> {
  const ids = Array.from({ length: doc.lines }, freshId);
  return {
    ids,
    items: new Map(saved.filter((mark) => mark.line < ids.length && mark.items.length)
      .map((mark) => [ids[mark.line], mark.items])),
    lost: new Map(),
    version: 0,
  };
}

const marksField = StateField.define<LineMarks<unknown>>({
  create: (state) => initialMarks(state.doc, []),
  update(value, tr) {
    let marks = tr.docChanged ? carryChanges(value, tr) : value;
    for (const effect of tr.effects) {
      if (effect.is(placeEffect)) marks = changeItems(marks, tr.state.doc, effect.value, true);
      else if (effect.is(removeEffect)) marks = changeItems(marks, tr.state.doc, effect.value, false);
      else if (effect.is(refreshEffect)) marks = { ...marks, version: marks.version + 1 };
    }
    return marks;
  },
});

/**
 * A placing undone is a removal, and a removal undone is a placing, so that both stand in the
 * box's one history among the edits of its text. A removal keeps its index, so that undoing
 * it puts the item back where it was among the line's items.
 */
const invertMarks = invertedEffects.of((tr) => {
  const inverted: StateEffect<unknown>[] = [];
  const before = tr.startState.field(marksField);
  for (const effect of tr.effects) {
    if (effect.is(placeEffect)) inverted.push(removeEffect.of(effect.value));
    else if (effect.is(removeEffect)) {
      const line = tr.startState.doc.lineAt(Math.min(effect.value.pos, tr.startState.doc.length)).number - 1;
      const index = (before.items.get(before.ids[line]) ?? []).indexOf(effect.value.item);
      if (index >= 0) inverted.push(placeEffect.of({ ...effect.value, index }));
    }
  }
  return inverted;
});

/** What the lines of the box hold now. */
export function marksOf<T>(state: EditorState): LineMarks<T> {
  return state.field(marksField) as LineMarks<T>;
}

/** The marks as a saved draft keeps them: each line that holds something, by its index. */
export function savedMarks<T>(state: EditorState): { line: number; items: T[] }[] {
  const marks = marksOf<T>(state);
  return marks.ids.flatMap((id, line) => {
    const items = marks.items.get(id);
    return items?.length ? [{ line, items }] : [];
  });
}

/** The index of the line whose id is `id`, or -1 when no line has it now. */
export function lineOfId(state: EditorState, id: number): number {
  return marksOf(state).ids.indexOf(id);
}

function lineStart(state: EditorState, line: number): number {
  return state.doc.line(Math.max(1, Math.min(line + 1, state.doc.lines))).from;
}

/** Places `item` on line `line` (counted from 0) as one undo step. */
export function placeItem<T>(view: EditorView, line: number, item: T): void {
  view.dispatch({
    effects: placeEffect.of({ pos: lineStart(view.state, line), item }),
    annotations: isolateHistory.of("full"),
    userEvent: "mark.place",
  });
}

/** Removes `item` from line `line` as one undo step; nothing happens if the line lacks it. */
export function removeItem<T>(view: EditorView, line: number, item: T): void {
  view.dispatch({
    effects: removeEffect.of({ pos: lineStart(view.state, line), item }),
    annotations: isolateHistory.of("full"),
    userEvent: "mark.remove",
  });
}

/** Redraws the marks, for when what they show depends on something outside the box. */
export function refreshMarks(view: EditorView): void {
  view.dispatch({ effects: refreshEffect.of(null) });
}

// ------------------------------------------------- the ghost and the landing (proposal 5.3)

interface Cues {
  /** The start of the line under the pointer, which holds nothing yet; null when none. */
  ghost: number | null;
  /** The start of the line under the pointer, whatever it holds; null when none (KYO-913). */
  hover: number | null;
  /** Whether the caret's line is shown as where the next anchor or attachment lands. */
  landing: boolean;
}

const pointerEffect = StateEffect.define<{ ghost: number | null; hover: number | null }>();
const landingEffect = StateEffect.define<boolean>();

const cuesField = StateField.define<Cues>({
  create: () => ({ ghost: null, hover: null, landing: false }),
  update(value, tr) {
    // An edit moves the lines under the pointer, which the next move of the mouse finds again.
    let cues = tr.docChanged && (value.ghost !== null || value.hover !== null) ? { ...value, ghost: null, hover: null } : value;
    for (const effect of tr.effects) {
      if (effect.is(pointerEffect)) cues = { ...cues, ...effect.value };
      else if (effect.is(landingEffect)) cues = { ...cues, landing: effect.value };
    }
    return cues;
  },
});

/** Shows or hides the landing arrow on the caret's line, which follows the caret. */
export function showLanding(view: EditorView, on: boolean): void {
  if (view.state.field(cuesField).landing === on) return;
  view.dispatch({ effects: landingEffect.of(on) });
}

/**
 * The line (counted from 0) the landing arrow stands on, or null while it is not shown. The
 * fold gutter draws it (KYO-913), beside the line's mark rather than over it.
 */
export function landingLine(state: EditorState): number | null {
  return state.field(cuesField).landing ? state.doc.lineAt(state.selection.main.head).number - 1 : null;
}

/** The line (counted from 0) under the pointer that shows the ghost `+`, or null. */
export function ghostLine(state: EditorState): number | null {
  const ghost = state.field(cuesField).ghost;
  return ghost === null ? null : state.doc.lineAt(Math.min(ghost, state.doc.length)).number - 1;
}

/** The line (counted from 0) under the pointer, or null while the pointer is not on the box. */
export function hoverLine(state: EditorState): number | null {
  const hover = state.field(cuesField).hover;
  return hover === null ? null : state.doc.lineAt(Math.min(hover, state.doc.length)).number - 1;
}

// ---------------------------------------------------------------------- the gutter

/**
 * The strip left of the text (KYO-917), from the box's edge in: the section lines, the marks,
 * then the folds, so that a chevron stands next to the words it folds. The mark and the chevron
 * are each inset in their gutter, so that the edge, a mark or the ghost, a chevron and the
 * line's first character stand at least four pixels apart, and the text starts `TEXT_START`
 * pixels from the edge. The composer's stylesheet sets its Delete button and a scratchpad's
 * head on the same measures.
 */
export const SECTIONS_WIDTH = 8;
export const MARKS_WIDTH = 24;
const MARK_INSET = 2;
export const FOLDS_WIDTH = 16;
const CHEVRON_INSET = 2;
const CHEVRON_WIDTH = 10;

type Draw = (view: EditorView) => HTMLElement;

class DrawnMarker extends GutterMarker {
  constructor(readonly key: string, readonly parts: readonly unknown[], readonly draw: Draw) { super(); }
  eq(other: GutterMarker): boolean {
    return other instanceof DrawnMarker && other.key === this.key
      && other.parts.length === this.parts.length && other.parts.every((part, at) => part === this.parts[at]);
  }
  toDOM(view: EditorView): Node {
    return this.draw(view);
  }
}

// ------------------------------------------------- lines: indent, guides, headings (5.4)

/** The left padding CodeMirror gives a line, restated so that the hanging indent adds to it. */
const LINE_PADDING = 6;
/** Where a line's first character stands, from the box's edge, past the gutters. */
export const TEXT_START = SECTIONS_WIDTH + MARKS_WIDTH + FOLDS_WIDTH + LINE_PADDING;
/** Where in its level a guide stands: under the marker of the item the line is nested in. */
const GUIDE_INSET = 3;
const GUIDE_WIDTH = 1.5;
/** A line's prefix: its indentation and, on a list item, the marker and the space after it. */
const PREFIX = /^[ \t]*(?:(?:\d+\.|[-*])[ \t])?/;

const widths = new Map<string, number>();
let canvas: CanvasRenderingContext2D | null = null;

/** The font the content is drawn in, as a canvas takes it; empty before it is laid out. */
function fontOf(view: EditorView): string {
  if (!view.contentDOM.isConnected) return "";
  const style = getComputedStyle(view.contentDOM);
  return style.fontSize ? `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}` : "";
}

/** The width of `text` in `font`, measured once and kept. A tab is taken as `tabSize` spaces. */
function widthOf(font: string, text: string, tabSize: number): number {
  const key = `${font}\u0000${text}`;
  let width = widths.get(key);
  if (width === undefined) {
    canvas ??= document.createElement("canvas").getContext("2d");
    if (!canvas) return 0;
    canvas.font = font;
    width = canvas.measureText(text.replace(/\t/g, " ".repeat(tabSize))).width;
    widths.set(key, width);
  }
  return width;
}

/** The background that draws `depth` guides, one indent step apart. */
function guides(depth: number, step: number): string {
  const layers: string[] = [];
  const places: string[] = [];
  for (let level = 0; level < depth; level++) {
    layers.push("linear-gradient(var(--kyo-guide), var(--kyo-guide))");
    places.push(`${(LINE_PADDING + level * step + GUIDE_INSET).toFixed(2)}px 0`);
  }
  return `background-image: ${layers.join(", ")}; background-position: ${places.join(", ")}; `
    + `background-size: ${GUIDE_WIDTH}px 100%; background-repeat: no-repeat;`;
}

const lineDecorations = new Map<string, Decoration>();

function lineDecoration(hang: number, depth: number, heading: number, step: number): Decoration | null {
  if (!hang && !depth && !heading) return null;
  const key = `${hang.toFixed(2)}|${depth}|${heading}|${step.toFixed(2)}`;
  let decoration = lineDecorations.get(key);
  if (!decoration) {
    let style = "";
    if (hang) style += `padding-left: ${(LINE_PADDING + hang).toFixed(2)}px; text-indent: ${(-hang).toFixed(2)}px; `;
    if (depth) style += guides(depth, step);
    decoration = Decoration.line({
      class: [depth ? `depth-${depth}` : "", heading ? `heading level-${heading}` : ""].filter(Boolean).join(" "),
      attributes: style ? { style, "data-depth": String(depth) } : { "data-depth": String(depth) },
    });
    lineDecorations.set(key, decoration);
  }
  return decoration;
}

/**
 * Turns `lineShapes` of the document into a decoration for each visible line: the hanging
 * indent, so a wrapped row starts under the line's words; one guide for each level of
 * nesting, as the line's background, so it runs down every row; and a heading's classes.
 */
const outline = ViewPlugin.fromClass(class {
  decorations: DecorationSet;
  shapes: ReturnType<typeof lineShapes>;
  font: string;

  constructor(view: EditorView) {
    this.shapes = lineShapes(view.state.doc.toString());
    this.font = fontOf(view);
    this.decorations = this.build(view);
  }

  update(update: ViewUpdate): void {
    if (update.docChanged) this.shapes = lineShapes(update.state.doc.toString());
    const font = update.geometryChanged || !this.font ? fontOf(update.view) : this.font;
    if (update.docChanged || update.viewportChanged || font !== this.font) {
      this.font = font;
      this.decorations = this.build(update.view);
    }
  }

  build(view: EditorView): DecorationSet {
    const { doc, tabSize } = view.state;
    const step = this.font ? widthOf(this.font, " ".repeat(INDENT_WIDTH), tabSize) : 0;
    const ranges = [];
    for (const { from, to } of view.visibleRanges) {
      for (let pos = from; pos <= to;) {
        const line = doc.lineAt(pos);
        const shape = this.shapes[line.number - 1];
        const prefix = PREFIX.exec(line.text)?.[0] ?? "";
        // A line of only spaces has no words to hang under.
        const hang = this.font && prefix && prefix.length < line.text.length ? widthOf(this.font, prefix, tabSize) : 0;
        const decoration = shape && lineDecoration(hang, shape.depth, shape.heading, step);
        if (decoration) ranges.push(decoration.range(line.from));
        pos = line.to + 1;
      }
    }
    return Decoration.set(ranges);
  }
}, { decorations: (plugin) => plugin.decorations });

// ------------------------------------------------------------------ section lines (KYO-917)

const SECTION_BAR = 2;
const SECTION_PITCH = 4;
/** How far below the top of a heading's row its section's line begins, below the heading's rule. */
const SECTION_START = 3;

/**
 * The lines of one row: one for each heading section the line is in, outermost at the box's
 * edge. A section that begins or ends on the line rounds that end of its stroke.
 */
class SectionMarker extends GutterMarker {
  constructor(readonly depth: number, readonly starts: number, readonly ends: number, readonly pitch: number, readonly bar: number) {
    super();
  }
  eq(other: GutterMarker): boolean {
    return other instanceof SectionMarker && other.depth === this.depth && other.starts === this.starts
      && other.ends === this.ends && other.pitch === this.pitch && other.bar === this.bar;
  }
  toDOM(): Node {
    const holder = document.createElement("span");
    holder.className = "section-lines";
    for (let slot = 0; slot < this.depth; slot++) {
      const line = document.createElement("span");
      const starts = Boolean(this.starts & (1 << slot));
      const ends = Boolean(this.ends & (1 << slot));
      line.className = `section-line${starts ? " starts" : ""}${ends ? " ends" : ""}`;
      line.style.cssText = `left: ${(slot * this.pitch).toFixed(2)}px; width: ${this.bar.toFixed(2)}px; `
        + `top: ${starts ? SECTION_START : 0}px;`;
      holder.append(line);
    }
    return holder;
  }
}

/**
 * The section lines of a document, as gutter markers on the lines they stand beside. Two
 * levels of sections stand `SECTION_PITCH` apart; a box whose sections nest deeper draws its
 * lines closer, so that they stay in their gutter and the text does not move as a heading is
 * typed.
 */
function sectionMarkers(doc: Text): RangeSet<GutterMarker> {
  const held = headingSections(doc.toString());
  const deepest = Math.max(0, ...held.map((chain) => chain.length));
  const pitch = deepest <= 2 ? SECTION_PITCH : (SECTIONS_WIDTH - SECTION_BAR) / (deepest - 1);
  const bar = Math.min(SECTION_BAR, Math.max(1, pitch - 1));
  const markers: { pos: number; marker: GutterMarker }[] = [];
  held.forEach((chain, line) => {
    if (!chain.length) return;
    let starts = 0;
    let ends = 0;
    chain.forEach((heading, slot) => {
      if (heading === line) starts |= 1 << slot;
      if (held[line + 1]?.[slot] !== heading) ends |= 1 << slot;
    });
    markers.push({ pos: doc.line(line + 1).from, marker: new SectionMarker(chain.length, starts, ends, pitch, bar) });
  });
  return RangeSet.of(markers.map(({ pos, marker }) => marker.range(pos)));
}

const sectionLinesField = StateField.define<RangeSet<GutterMarker>>({
  create: (state) => sectionMarkers(state.doc),
  update: (value, tr) => (tr.docChanged ? sectionMarkers(tr.state.doc) : value),
});

/**
 * The section lines (KYO-917): a heading's section, as `sectionEnd` gives it to folding and
 * Send to prompt, has a line at the box's edge down every row it holds, the heading's
 * included, and a section nested in it a second line beside the first. They are markers of
 * the outermost gutter, so they lie in no line's background, beside the indent guides and the
 * heading's rule and tint and over none of them. CodeMirror sizes a gutter's element to its
 * line's block, wrapped rows and all, so the line of a section runs unbroken, and draws a
 * folded section's marker on its first line alone. Nothing here changes the text.
 */
const sectionsGutter: Extension = [
  sectionLinesField,
  gutter({ class: "kyo-sections", markers: (view) => view.state.field(sectionLinesField) }),
];

// ------------------------------------------------------------------- keys (proposal 5.6)

const LIST_ITEM = /^(\s*)(?:(\d+)\.|([-*]))\s(.*)$/;
const INDENT = " ".repeat(INDENT_WIDTH);

/** The change that turns `before` into `after`, by their common start and end, at `from`. */
function lineChange(from: number, before: string, after: string): { from: number; to: number; insert: string } | null {
  if (before === after) return null;
  let head = 0;
  while (head < before.length && head < after.length && before[head] === after[head]) head++;
  let tail = 0;
  while (tail < before.length - head && tail < after.length - head
    && before[before.length - 1 - tail] === after[after.length - 1 - tail]) tail++;
  return { from: from + head, to: from + before.length - tail, insert: after.slice(head, after.length - tail) };
}

/**
 * Tab and Shift-Tab (KYO-905): indents or outdents every line the caret or the selection
 * touches, a list item or not, renumbers an indented numbered item to `1.`, and leaves blank
 * lines alone in a selection of several lines. Each line is its own change, so each keeps its
 * marks, and the press is one undo step. The key is always taken, so the focus stays.
 */
function shiftIndent(view: EditorView, out: boolean, only?: { from: number; to: number }): boolean {
  const { state } = view;
  const { from: start, to: end, empty } = state.selection.main;
  const first = only ? state.doc.line(only.from + 1) : state.doc.lineAt(start);
  // A selection that ends at the start of a line does not take that line.
  const last = only ? state.doc.line(only.to)
    : state.doc.lineAt(!empty && end > first.from && state.sliceDoc(end - 1, end) === "\n" ? end - 1 : end);
  const lines: string[] = [];
  for (let number = first.number; number <= last.number; number++) lines.push(state.doc.line(number).text);
  const moved = lines.map((line) => {
    if (out) return line.startsWith(INDENT) ? line.slice(INDENT.length) : line.replace(/^[ \t]/, "");
    if (!line.trim() && lines.length > 1) return line;
    // A numbered item starts a new count where it is nested.
    return INDENT + (LIST_ITEM.exec(line)?.[2] ? line.replace(/\d+\./, "1.") : line);
  });
  const changes = moved.flatMap((line, index) => {
    const change = lineChange(state.doc.line(first.number + index).from, lines[index], line);
    return change ? [change] : [];
  });
  if (!changes.length) return true;
  if (only) {
    // The lines were named, not selected: the selection moves with the text and is not shown.
    view.dispatch({ changes, userEvent: out ? "delete.dedent" : "input.indent", annotations: isolateHistory.of("full") });
    return true;
  }
  const from = first.from;
  const length = moved.join("\n").length;
  const caret = Math.max(from, start + moved[0].length - lines[0].length);
  view.dispatch({
    changes,
    selection: empty ? EditorSelection.cursor(caret) : EditorSelection.range(from, from + length),
    scrollIntoView: true,
    userEvent: out ? "delete.dedent" : "input.indent",
    annotations: isolateHistory.of("full"),
  });
  return true;
}

export const indentLines = (view: EditorView) => shiftIndent(view, false);
export const outdentLines = (view: EditorView) => shiftIndent(view, true);

/**
 * Indent and Outdent in a paragraph's panel (KYO-911): lines `from` to `to` (counted from 0,
 * `to` excluded) are indented or outdented as Tab and Shift-Tab would move a selection of
 * them, in one undo step, and the caret and the selection stay where they were.
 */
export function shiftSection(view: EditorView, from: number, to: number, out: boolean): void {
  if (from < 0 || to <= from || to > view.state.doc.lines) return;
  shiftIndent(view, out, { from, to });
}

/**
 * Return: on a list item with words before the caret, a new item with the same indent and
 * the next marker; on an empty item, the marker taken off and the line kept; otherwise a new
 * line with no indent copied, as a text area inserts. Shift-Return is a plain new line except
 * on an empty item, where it ends the list as Return does.
 */
function newLine(view: EditorView, shift: boolean): boolean {
  const { state } = view;
  const selection = state.selection.main;
  if (selection.empty) {
    const line = state.doc.lineAt(selection.head);
    const item = LIST_ITEM.exec(state.sliceDoc(line.from, selection.head));
    if (item) {
      const [, indent, number, bullet, content] = item;
      if (!content) {
        view.dispatch({ changes: { from: line.from, to: selection.head }, scrollIntoView: true, userEvent: "delete" });
        return true;
      }
      if (!shift) {
        const marker = number ? `${Number(number) + 1}.` : bullet;
        view.dispatch(state.update(state.replaceSelection(`\n${indent}${marker} `), { scrollIntoView: true, userEvent: "input" }));
        return true;
      }
    }
  }
  return insertNewline(view);
}

/**
 * The editor's own keys, above the fold keys, the history's and the standard ones. The fold
 * keys are CodeMirror's: Command-Option-[ folds the caret's section and Command-Option-]
 * unfolds it (KYO-913). Command-Return is bound by none of them: the page's own handler sends
 * the prompt with it, and refuses to in a scratchpad (KYO-903, KYO-904). `defaultKeymap` is
 * not used for that reason, and the standard keymap's Return, which copies the line's indent,
 * is replaced by `newLine`.
 */
const composerKeys: Extension = [
  keymap.of([
    { key: "Tab", run: indentLines, shift: outdentLines },
    { key: "Mod-]", run: indentLines },
    { key: "Mod-[", run: outdentLines },
    { key: "Enter", run: (view) => newLine(view, false), shift: (view) => newLine(view, true) },
  ]),
  keymap.of(foldKeymap),
  keymap.of(historyKeymap),
  keymap.of(standardKeymap.filter((binding) => binding.key !== "Enter")),
];

// -------------------------------------------------------------- folding (proposal 5.5)

/** The section ends of one document, computed as asked and kept while the document is the same. */
const sectionEnds = new WeakMap<Text, { text: string; ends: Map<number, number> }>();

function sectionEndIn(doc: Text, line: number): number {
  let known = sectionEnds.get(doc);
  if (!known) {
    known = { text: doc.toString(), ends: new Map() };
    sectionEnds.set(doc, known);
  }
  let end = known.ends.get(line);
  if (end === undefined) {
    end = sectionEnd(known.text, line);
    known.ends.set(line, end);
  }
  return end;
}

/**
 * What a line folds (KYO-913): from its end to the end of the last line of its section, as
 * `sectionEnd` gives it and Send to prompt moves it (KYO-904). A heading folds up to the next
 * heading of its level or above, and a list item the lines nested under it. A line whose
 * section is itself alone folds nothing, and a blank line, which has no words to stand for
 * what it hides, folds nothing either.
 */
const sections = foldService.of((state, lineStart) => {
  const line = state.doc.lineAt(lineStart);
  if (!line.text.trim()) return null;
  const end = sectionEndIn(state.doc, line.number - 1);
  return end > line.number ? { from: line.to, to: state.doc.line(end).to } : null;
});

/** A folded line, by its index and its words, so that a box drawn again can fold it again. */
export interface FoldedLine { line: number; text: string }

/** The lines whose sections are folded now, each the line that stays in view. */
export function foldedLines(state: EditorState): FoldedLine[] {
  const lines: FoldedLine[] = [];
  foldedRanges(state).between(0, state.doc.length, (from) => {
    const line = state.doc.lineAt(from);
    lines.push({ line: line.number - 1, text: line.text });
  });
  return lines;
}

/**
 * The folds that put `wanted` back on this document: each on the line of the same index if it
 * still reads the same, or else on the nearest line that does, and only where that line folds.
 */
function refoldEffects(state: EditorState, wanted: readonly FoldedLine[]): StateEffect<unknown>[] {
  const taken = new Set<number>();
  const effects: StateEffect<unknown>[] = [];
  const count = state.doc.lines;
  for (const { line, text } of wanted) {
    let found = -1;
    for (let distance = 0; found < 0 && distance < count; distance++) {
      for (const at of distance ? [line - distance, line + distance] : [line]) {
        if (at >= 0 && at < count && !taken.has(at) && state.doc.line(at + 1).text === text) {
          found = at;
          break;
        }
      }
    }
    if (found < 0) continue;
    const { from, to } = state.doc.line(found + 1);
    const range = foldable(state, from, to);
    if (!range) continue;
    taken.add(found);
    effects.push(foldEffect.of(range));
  }
  return effects;
}

/** Folds again the lines a box had folded before it was drawn again. */
export function refold(view: EditorView, wanted: readonly FoldedLine[]): void {
  const effects = refoldEffects(view.state, wanted);
  if (effects.length) view.dispatch({ effects });
}

/** The folded section that begins on the line from `from` to `to`, if one does. */
function foldOn(state: EditorState, from: number, to: number): { from: number; to: number } | null {
  let found: { from: number; to: number } | null = null;
  foldedRanges(state).between(from, to, (start, end) => {
    if (!found && start >= from && start <= to) found = { from: start, to: end };
  });
  return found;
}

/** Whether line `line` (counted from 0) is folded now, can fold, or neither (KYO-911). */
export function lineFold(state: EditorState, line: number): "folded" | "foldable" | null {
  if (line < 0 || line >= state.doc.lines) return null;
  const { from, to } = state.doc.line(line + 1);
  return foldOn(state, from, to) ? "folded" : foldable(state, from, to) ? "foldable" : null;
}

/**
 * Folds the section that begins on line `line` (counted from 0) or unfolds it if it is folded,
 * as the fold keys do at the caret and the chevron does on its line. It says whether it did.
 */
export function toggleFold(view: EditorView, line: number): boolean {
  if (line < 0 || line >= view.state.doc.lines) return false;
  const { from, to } = view.state.doc.line(line + 1);
  const folded = foldOn(view.state, from, to);
  if (folded) {
    view.dispatch({ effects: unfoldEffect.of(folded) });
    return true;
  }
  const range = foldable(view.state, from, to);
  if (!range) return false;
  view.dispatch({ effects: foldEffect.of(range) });
  return true;
}

/** What a fold's placeholder says: how many of its lines hold a mark, and what they hold. */
interface FoldSummary { marks: number; title: string }

function foldSummary<T>(state: EditorState, range: { from: number; to: number }, describe: (items: T[]) => string): FoldSummary {
  const marks = marksOf<T>(state);
  const first = state.doc.lineAt(range.from).number;
  const last = state.doc.lineAt(Math.min(range.to, state.doc.length)).number;
  const held: string[] = [];
  for (let number = first + 1; number <= last; number++) {
    const items = marks.items.get(marks.ids[number - 1]);
    if (items?.length) held.push(describe(items));
  }
  return { marks: held.length, title: held.length ? held.join("\n") : "Unfold" };
}

function paintFold(element: HTMLElement, summary: FoldSummary): void {
  const text = summary.marks ? `… ${summary.marks} ${summary.marks === 1 ? "mark" : "marks"}` : "…";
  if (element.textContent !== text) element.textContent = text;
  if (element.title !== summary.title) element.title = summary.title;
  element.classList.toggle("holds-marks", summary.marks > 0);
}

/**
 * Folding: CodeMirror's fold state and its placeholder, which names how many of the folded
 * lines hold a mark, since their markers are not drawn while they are hidden. CodeMirror
 * draws a placeholder from what it was given when the section was folded, so each update
 * paints the placeholders again from the marks as they are now.
 */
function folding<T>(describe: (items: T[]) => string): Extension {
  return [
    codeFolding({
      preparePlaceholder: (state, range) => foldSummary(state, range, describe),
      placeholderDOM(_view, onclick, prepared: FoldSummary) {
        const element = document.createElement("span");
        element.className = "cm-foldPlaceholder kyo-fold";
        element.setAttribute("aria-label", "folded lines");
        element.onclick = onclick;
        paintFold(element, prepared);
        return element;
      },
    }),
    sections,
    EditorView.updateListener.of((update) => {
      const { view, state } = update;
      for (const element of view.contentDOM.querySelectorAll<HTMLElement>(".kyo-fold")) {
        const pos = view.posAtDOM(element);
        const range = foldOn(state, pos, pos) ?? foldOn(state, state.doc.lineAt(pos).from, state.doc.lineAt(pos).to);
        if (range) paintFold(element, foldSummary(state, range, describe));
      }
    }),
  ];
}

class FoldToggle extends GutterMarker {
  constructor(readonly folded: boolean) { super(); }
  eq(other: GutterMarker): boolean {
    return other instanceof FoldToggle && other.folded === this.folded;
  }
  toDOM(): Node {
    const toggle = document.createElement("span");
    toggle.className = this.folded ? "fold-toggle folded" : "fold-toggle";
    toggle.textContent = "›";
    toggle.title = this.folded ? "Unfold (⌥⌘])" : "Fold (⌥⌘[)";
    return toggle;
  }
}

const openToggle = new FoldToggle(false);
const foldedToggle = new FoldToggle(true);

/**
 * The fold gutter, between the marks and the text (KYO-917): a chevron on the line under the
 * pointer when it can fold, and on every folded line always. A press on it folds or unfolds that line's section.
 * The landing arrow is drawn here too (KYO-913), over the chevron where the two meet, so that
 * it no longer covers the line's mark; it takes no press, which reaches the chevron under it.
 */
function foldsGutter<T>(options: EditorOptions<T>): Extension {
  const drawLanding = options.drawLanding === undefined ? defaultLanding : options.drawLanding;
  return gutter({
    class: "kyo-folds",
    markers(view) {
      const { state } = view;
      const found: { pos: number; marker: GutterMarker }[] = [];
      const folded = new Set<number>();
      foldedRanges(state).between(0, state.doc.length, (from) => {
        const start = state.doc.lineAt(from).from;
        folded.add(start);
        found.push({ pos: start, marker: foldedToggle });
      });
      const hover = hoverLine(state);
      if (hover !== null) {
        const line = state.doc.line(hover + 1);
        if (!folded.has(line.from) && foldable(state, line.from, line.to)) found.push({ pos: line.from, marker: openToggle });
      }
      const landing = landingLine(state);
      if (drawLanding && landing !== null) {
        // A caret inside a folded section is shown on the line that stands for the section.
        const pos = view.lineBlockAt(lineStart(state, landing)).from;
        found.push({ pos, marker: new DrawnMarker("landing", [], drawLanding) });
      }
      return RangeSet.of(found.map(({ pos, marker }) => marker.range(pos)), true);
    },
    domEventHandlers: {
      mousedown: (_view, _line, event) => {
        // The focus and the caret stay where they are; the press is the click's.
        event.preventDefault();
        return true;
      },
      click(view, block) {
        return toggleFold(view, view.state.doc.lineAt(block.from).number - 1);
      },
    },
  });
}

// ------------------------------------------------------------------------- the editor

export interface EditorOptions<T> {
  /** Where the editor's element is appended, if anywhere. */
  parent?: HTMLElement;
  text?: string;
  /** What lines held when the box was saved, by line index. */
  marks?: { line: number; items: T[] }[];
  /** Shown while the box is empty: "Reply…" or "Notes…". */
  placeholder?: string;
  /** The marker of a line that holds something, as `drawMarks` builds it. */
  drawMark?: (view: EditorView, id: number, items: T[]) => HTMLElement;
  /** The ghost `+` on the line under the pointer that holds nothing; none without it. */
  drawGhost?: (view: EditorView, id: number) => HTMLElement;
  /**
   * The landing arrow on the caret's line while `showLanding` has it on, drawn in the fold
   * gutter (KYO-913). Left out, an arrow is drawn; null draws none.
   */
  drawLanding?: ((view: EditorView) => HTMLElement) | null;
  /** What one line's items are, for the title of a fold whose hidden lines hold them. */
  describeItems?: (items: T[]) => string;
  /** Lines to fold once the box is built: those it had folded before it was drawn again. */
  folds?: readonly FoldedLine[];
  /** The folded lines, each time a section is folded or unfolded or a folded box is edited. */
  onFolds?: (view: EditorView, lines: FoldedLine[]) => void;
  /** A right-click in the marks gutter, with the line under the pointer and its id. True takes it. */
  onGutterMenu?: (view: EditorView, line: number, id: number, event: MouseEvent) => boolean;
  /**
   * Files pasted or dropped. The editor inserts nothing for them: a paste's position is the
   * caret, and a drop's is where it fell. Which to attach, and to which line, is the caller's.
   */
  onFiles?: (view: EditorView, files: File[], how: "paste" | "drop", pos: number) => void;
  /** Text pasted into the box, with where it was put, after it is in the document. */
  onPaste?: (view: EditorView, text: string, from: number) => void;
  /** Every update of the view: edits, the selection, the marks, the focus. */
  onUpdate?: (update: ViewUpdate) => void;
  /** Further extensions. */
  extensions?: Extension[];
}

function defaultMark(_view: EditorView, _id: number, items: unknown[]): HTMLElement {
  const mark = document.createElement("span");
  mark.className = "mark held";
  mark.textContent = String(items.length);
  return mark;
}

function defaultLanding(): HTMLElement {
  const arrow = document.createElement("span");
  arrow.className = "mark-landing";
  arrow.textContent = "→";
  arrow.title = "An anchor or an attachment made now lands on this paragraph";
  return arrow;
}

/**
 * The marks gutter: a marker per line that holds something, and the ghost. CodeMirror draws a
 * line's markers at the top of its block, so a line hidden in a folded section shows none.
 */
function marksGutter<T>(options: EditorOptions<T>): Extension {
  const drawMark = options.drawMark ?? defaultMark;
  return gutter({
    class: "kyo-marks",
    markers(view) {
      const { state } = view;
      const marks = marksOf<T>(state);
      const found: { pos: number; marker: GutterMarker }[] = [];
      marks.ids.forEach((id, line) => {
        const items = marks.items.get(id);
        if (!items?.length) return;
        found.push({ pos: lineStart(state, line), marker: new DrawnMarker(`held:${id}`, [items, marks.version], (v) => drawMark(v, id, items)) });
      });
      const ghost = ghostLine(state);
      const ghostId = ghost === null ? undefined : marks.ids[ghost];
      if (options.drawGhost && ghost !== null && ghostId !== undefined && !marks.items.get(ghostId)?.length) {
        const draw = options.drawGhost;
        found.push({ pos: lineStart(state, ghost), marker: new DrawnMarker(`ghost:${ghostId}`, [], (v) => draw(v, ghostId)) });
      }
      return RangeSet.of(found.map(({ pos, marker }) => marker.range(pos)), true);
    },
    domEventHandlers: {
      contextmenu(view, block, event) {
        if (!options.onGutterMenu) return false;
        const line = view.state.doc.lineAt(block.from).number - 1;
        return options.onGutterMenu(view, line, marksOf(view.state).ids[line], event as MouseEvent);
      },
    },
  });
}

/** Follows the pointer over the box, gutters and text, for the ghost `+` and the fold chevron. */
const followPointer = ViewPlugin.fromClass(class {
  constructor(readonly view: EditorView) {
    view.dom.addEventListener("mousemove", this.move);
    view.dom.addEventListener("mouseleave", this.leave);
  }
  move = (event: MouseEvent) => {
    const { view } = this;
    const block = view.lineBlockAtHeight(event.clientY - view.documentTop);
    const line = view.state.doc.lineAt(block.from);
    const marks = marksOf(view.state);
    const ghost = marks.items.get(marks.ids[line.number - 1])?.length ? null : line.from;
    const cues = view.state.field(cuesField);
    if (cues.ghost !== ghost || cues.hover !== line.from) view.dispatch({ effects: pointerEffect.of({ ghost, hover: line.from }) });
  };
  leave = () => {
    const cues = this.view.state.field(cuesField);
    if (cues.ghost !== null || cues.hover !== null) this.view.dispatch({ effects: pointerEffect.of({ ghost: null, hover: null }) });
  };
  destroy() {
    this.view.dom.removeEventListener("mousemove", this.move);
    this.view.dom.removeEventListener("mouseleave", this.leave);
  }
});

/** Paste and drop (proposal 5.8): files go to the caller, and text pasted is reported. */
function transfers<T>(options: EditorOptions<T>): Extension {
  return [
    EditorView.domEventHandlers({
      paste(event, view) {
        const files = Array.from(event.clipboardData?.files ?? []);
        if (!files.length) return false;
        event.preventDefault();
        options.onFiles?.(view, files, "paste", view.state.selection.main.head);
        return true;
      },
      drop(event, view) {
        const files = Array.from(event.dataTransfer?.files ?? []);
        if (!files.length) return false;
        // CodeMirror would read each file and insert its contents; the page decides instead.
        event.preventDefault();
        const pos = view.posAtCoords({ x: event.clientX, y: event.clientY }) ?? view.state.selection.main.head;
        options.onFiles?.(view, files, "drop", pos);
        return true;
      },
    }),
    EditorView.updateListener.of((update) => {
      if (options.onPaste) {
        for (const tr of update.transactions) {
          if (!tr.isUserEvent("input.paste")) continue;
          let text = "";
          let from = -1;
          tr.changes.iterChanges((_fromA, _toA, fromB, _toB, inserted) => {
            if (from < 0) from = fromB;
            text += inserted.toString();
          });
          if (text) options.onPaste(update.view, text, from);
        }
      }
      options.onUpdate?.(update);
    }),
  ];
}

/**
 * The editor's look that is not the page's: no monospace, no gutter fill, no focus ring (the
 * box draws its own), and the guides and heading rules of KYO-902. Colours come from the
 * page's variables where it has them.
 */
const look = EditorView.theme({
  "&": { color: "inherit", background: "none", font: "inherit" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "inherit", lineHeight: "inherit", overflow: "visible" },
  ".cm-content": { padding: "0", "--kyo-guide": "var(--line, rgba(128, 128, 128, 0.4))" },
  ".cm-line": { padding: `0 2px 0 ${LINE_PADDING}px` },
  ".cm-gutters": { background: "none", border: "none", color: "inherit" },
  // KYO-917: the section lines at the box's edge, then the marks, then the folds (`TEXT_START`).
  ".cm-gutter.kyo-sections": { width: `${SECTIONS_WIDTH}px` },
  ".cm-gutter.kyo-sections .cm-gutterElement": { position: "relative" },
  ".section-line": {
    position: "absolute", bottom: "0", borderRadius: "0",
    background: "color-mix(in srgb, var(--muted, #888) 60%, transparent)",
  },
  ".section-line.starts": { borderTopLeftRadius: "1px", borderTopRightRadius: "1px" },
  ".section-line.ends": { borderBottomLeftRadius: "1px", borderBottomRightRadius: "1px" },
  ".cm-gutter.kyo-marks": { width: `${MARKS_WIDTH}px` },
  ".cm-gutter.kyo-marks .cm-gutterElement": { position: "relative" },
  ".cm-gutter.kyo-marks .mark": { left: `${MARK_INSET}px` },
  // KYO-913: the fold gutter. Its chevron points right on a folded line and down on one that
  // can fold; the landing arrow covers it where both stand.
  ".cm-gutter.kyo-folds": { width: `${FOLDS_WIDTH}px` },
  ".cm-gutter.kyo-folds .cm-gutterElement": { position: "relative", cursor: "default" },
  // The chevron is square, so that turning it down leaves its box where it was, centred on the
  // line's first row as the mark is.
  ".fold-toggle": {
    position: "absolute", left: `${CHEVRON_INSET}px`, top: `${11 - CHEVRON_WIDTH / 2}px`, width: `${CHEVRON_WIDTH}px`,
    height: `${CHEVRON_WIDTH}px`, lineHeight: `${CHEVRON_WIDTH}px`, textAlign: "center", fontSize: "14px",
    color: "var(--muted, #888)", cursor: "pointer", transform: "rotate(90deg)",
  },
  ".fold-toggle.folded": { transform: "none" },
  ".fold-toggle:hover": { color: "var(--text, currentColor)" },
  ".cm-gutter.kyo-folds .mark-landing": {
    position: "absolute", left: `${CHEVRON_INSET - 1}px`, top: "1px", zIndex: "1", width: `${CHEVRON_WIDTH + 2}px`,
    height: "20px", lineHeight: "20px", textAlign: "center", fontSize: "12px", overflow: "hidden", pointerEvents: "none",
  },
  ".cm-foldPlaceholder.kyo-fold": {
    margin: "0 0 0 4px", padding: "0 5px", border: "none", borderRadius: "4px",
    background: "color-mix(in srgb, var(--text, currentColor) 8%, transparent)",
    color: "var(--muted, #888)", fontWeight: "400", cursor: "pointer",
  },
  ".cm-foldPlaceholder.kyo-fold.holds-marks": { color: "var(--pastel-blue, #4a7fd6)", fontWeight: "600" },
  // KYO-918: a heading's bar begins at its section's line, in the strip left of the text: a
  // `#` at the first line and a deeper heading at the second, with its words where they were.
  ".cm-line.heading": {
    marginLeft: `${-(SECTIONS_WIDTH - SECTION_PITCH)}px`,
    paddingLeft: `${LINE_PADDING + SECTIONS_WIDTH - SECTION_PITCH}px`,
    fontWeight: "600",
    borderRadius: "0 0 4px 4px",
    boxShadow: "inset 0 1px 0 var(--line, rgba(128, 128, 128, 0.4))",
    backgroundColor: "color-mix(in srgb, var(--text, currentColor) 4%, transparent)",
  },
  ".cm-line.heading.level-1": {
    marginLeft: `${-SECTIONS_WIDTH}px`,
    paddingLeft: `${LINE_PADDING + SECTIONS_WIDTH}px`,
    boxShadow: "inset 0 2px 0 var(--muted, rgba(128, 128, 128, 0.8))",
    backgroundColor: "color-mix(in srgb, var(--text, currentColor) 8%, transparent)",
  },
  ".cm-line.heading.level-2": { boxShadow: "inset 0 1px 0 var(--muted, rgba(128, 128, 128, 0.8))" },
  ".cm-placeholder": { color: "var(--muted, #888)" },
});

/**
 * WebKit underlines misspelled words only in editable text that asks for it (KYO-796).
 * CodeMirror's defaults of no corrections and no capitals are kept, and so is its
 * `writingsuggestions="false"`, which turns off the system's inline predictions. Nothing here
 * handles `contextmenu` on the text, so the system's menu, with its suggestions, stays.
 */
const textFeatures = EditorView.contentAttributes.of({
  spellcheck: "true", autocorrect: "off", autocapitalize: "off",
});

/** The extensions of a box, for a caller that builds its own `EditorState`. */
export function editorExtensions<T>(options: EditorOptions<T> = {}): Extension[] {
  return [
    marksField.init((state) => initialMarks(state.doc, options.marks ?? [])),
    invertMarks,
    cuesField,
    history(),
    Prec.high(composerKeys),
    // The rows wrap, so nothing scrolls sideways, and gutters held fixed would stick to the
    // left edge of a scrolling box around the editor: the prompt's body, past whose edge the
    // composer's stylesheet sets the gutters, so that they came to stand over the text.
    gutters({ fixed: false }),
    folding(options.describeItems ?? ((items: T[]) => `${items.length} ${items.length === 1 ? "item" : "items"}`)),
    // The gutters stand in the order they are given, from the box's edge in (KYO-917). The
    // section lines are innermost, against the text: a heading's bar reaches left over their
    // strip to the line of its own section, so the line runs down from the bar's end (KYO-918).
    marksGutter(options),
    foldsGutter(options),
    sectionsGutter,
    followPointer,
    outline,
    transfers(options),
    ...(options.onFolds ? [foldWatch(options.onFolds)] : []),
    EditorView.lineWrapping,
    textFeatures,
    look,
    ...(options.placeholder ? [placeholder(options.placeholder)] : []),
    ...(options.extensions ?? []),
  ];
}

/** Calls `onFolds` when the folded lines may have changed: a fold, an unfold, or an edit. */
function foldWatch(onFolds: (view: EditorView, lines: FoldedLine[]) => void): Extension {
  return EditorView.updateListener.of((update) => {
    if (foldedRanges(update.startState) === foldedRanges(update.state)) return;
    onFolds(update.view, foldedLines(update.state));
  });
}

/**
 * Builds a box's editor from its text, its saved marks and the composer's callbacks, and folds
 * the lines `folds` names where they still fold.
 */
export function createEditor<T>(options: EditorOptions<T> = {}): EditorView {
  const view = new EditorView({
    parent: options.parent,
    state: EditorState.create({ doc: options.text ?? "", extensions: editorExtensions(options) }),
  });
  if (options.folds?.length) refold(view, options.folds);
  return view;
}
