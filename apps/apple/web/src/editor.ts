/**
 * A composer box's editor on CodeMirror 6 (KYO-906, KYO-909). The document is the text as
 * typed, which is what is saved and sent; everything else here is drawn over it or kept beside
 * it: the marks each line holds, the gutter that shows them, the hanging indent of wrapped
 * rows, the indent guides and the heading rules. The page does not use this module yet; the
 * composer moves onto it in a later change.
 *
 * What the composer decides (drafts, sending, the mark's panel, uploads) stays in `chat.ts`,
 * which reaches this module through the callbacks of `EditorOptions` and the functions below.
 */
import {
  EditorSelection, EditorState, type Extension, Prec, RangeSet, StateEffect, StateField, type Text,
  type Transaction,
} from "@codemirror/state";
import {
  Decoration, type DecorationSet, EditorView, GutterMarker, ViewPlugin, type ViewUpdate, gutter,
  keymap, placeholder,
} from "@codemirror/view";
import {
  history, historyKeymap, insertNewline, invertedEffects, isolateHistory, standardKeymap,
} from "@codemirror/commands";
import { shiftLines } from "./marks.ts";
import { INDENT_WIDTH, lineShapes } from "./outline.ts";

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
  /** Whether the caret's line is shown as where the next anchor or attachment lands. */
  landing: boolean;
}

const ghostEffect = StateEffect.define<number | null>();
const landingEffect = StateEffect.define<boolean>();

const cuesField = StateField.define<Cues>({
  create: () => ({ ghost: null, landing: false }),
  update(value, tr) {
    // An edit moves the lines under the pointer, which the next move of the mouse finds again.
    let cues = tr.docChanged && value.ghost !== null ? { ...value, ghost: null } : value;
    for (const effect of tr.effects) {
      if (effect.is(ghostEffect)) cues = { ...cues, ghost: effect.value };
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
 * The line (counted from 0) the landing arrow stands on, or null while it is not shown. Any
 * gutter may draw it: the marks gutter does unless `drawLanding` is null, and a later gutter
 * (the fold toggles of KYO-906's third dispatch) can read this instead.
 */
export function landingLine(state: EditorState): number | null {
  return state.field(cuesField).landing ? state.doc.lineAt(state.selection.main.head).number - 1 : null;
}

/** The line (counted from 0) under the pointer that shows the ghost `+`, or null. */
export function ghostLine(state: EditorState): number | null {
  const ghost = state.field(cuesField).ghost;
  return ghost === null ? null : state.doc.lineAt(Math.min(ghost, state.doc.length)).number - 1;
}

// ---------------------------------------------------------------------- the gutter

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
function shiftIndent(view: EditorView, out: boolean): boolean {
  const { state } = view;
  const { from: start, to: end, empty } = state.selection.main;
  const first = state.doc.lineAt(start);
  // A selection that ends at the start of a line does not take that line.
  const last = state.doc.lineAt(!empty && end > first.from && state.sliceDoc(end - 1, end) === "\n" ? end - 1 : end);
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
 * The editor's own keys, above the history's and the standard ones. Command-Return is bound
 * by none of them: the page's own handler sends the prompt with it, and refuses to in a
 * scratchpad (KYO-903, KYO-904). `defaultKeymap` is not used for that reason, and the
 * standard keymap's Return, which copies the line's indent, is replaced by `newLine`.
 */
const composerKeys: Extension = [
  keymap.of([
    { key: "Tab", run: indentLines, shift: outdentLines },
    { key: "Mod-]", run: indentLines },
    { key: "Mod-[", run: outdentLines },
    { key: "Enter", run: (view) => newLine(view, false), shift: (view) => newLine(view, true) },
  ]),
  keymap.of(historyKeymap),
  keymap.of(standardKeymap.filter((binding) => binding.key !== "Enter")),
];

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
   * The landing arrow on the caret's line while `showLanding` has it on. Null leaves it to
   * another gutter, which reads `landingLine`; left out, an arrow is drawn in the marks gutter.
   */
  drawLanding?: ((view: EditorView) => HTMLElement) | null;
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
  /** Further extensions, as the later folding work adds. */
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
  arrow.className = "mark mark-landing";
  arrow.textContent = "→";
  arrow.title = "An anchor or an attachment made now lands on this paragraph";
  return arrow;
}

/** The marks gutter: a marker per line that holds something, the ghost and the landing arrow. */
function marksGutter<T>(options: EditorOptions<T>): Extension {
  const drawMark = options.drawMark ?? defaultMark;
  const drawLanding = options.drawLanding === undefined ? defaultLanding : options.drawLanding;
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
      const landing = landingLine(state);
      if (drawLanding && landing !== null) {
        found.push({ pos: lineStart(state, landing), marker: new DrawnMarker("landing", [], drawLanding) });
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

/** Follows the pointer over the box, gutter and text, for the ghost `+`. */
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
    if (view.state.field(cuesField).ghost !== ghost) view.dispatch({ effects: ghostEffect.of(ghost) });
  };
  leave = () => {
    if (this.view.state.field(cuesField).ghost !== null) this.view.dispatch({ effects: ghostEffect.of(null) });
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
  ".cm-gutter.kyo-marks": { width: "24px" },
  ".cm-gutter.kyo-marks .cm-gutterElement": { position: "relative" },
  ".cm-line.heading": {
    fontWeight: "600",
    borderRadius: "0 0 4px 4px",
    boxShadow: "inset 0 1px 0 var(--line, rgba(128, 128, 128, 0.4))",
    backgroundColor: "color-mix(in srgb, var(--text, currentColor) 4%, transparent)",
  },
  ".cm-line.heading.level-1": {
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
    marksGutter(options),
    followPointer,
    outline,
    transfers(options),
    EditorView.lineWrapping,
    textFeatures,
    look,
    ...(options.placeholder ? [placeholder(options.placeholder)] : []),
    ...(options.extensions ?? []),
  ];
}

/** Builds a box's editor from its text, its saved marks and the composer's callbacks. */
export function createEditor<T>(options: EditorOptions<T> = {}): EditorView {
  return new EditorView({
    parent: options.parent,
    state: EditorState.create({ doc: options.text ?? "", extensions: editorExtensions(options) }),
  });
}
