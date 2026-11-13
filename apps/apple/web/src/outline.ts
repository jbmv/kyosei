/**
 * The shape of a prompt's lines as the composer draws them (KYO-902): how deeply each is
 * indented, and which are headings. The text itself is sent as typed, so this decides only
 * where the indent guides and the heading rules go.
 */

/** One level of indentation, as Tab makes it in a list. */
export const INDENT_WIDTH = 2;

export interface LineShape {
  /** Levels of indentation: one guide is drawn for each. */
  depth: number;
  /** 1 to 6 for a line that begins with that many `#` and a space; 0 otherwise. */
  heading: number;
}

const HEADING = /^(#{1,6})\s+\S/;

function indentOf(line: string): number {
  let columns = 0;
  for (const char of line) {
    if (char === " ") columns += 1;
    else if (char === "\t") columns += INDENT_WIDTH;
    else break;
  }
  return Math.floor(columns / INDENT_WIDTH);
}

/**
 * The shape of each line of `text`. A blank line between two indented lines takes the
 * shallower of its neighbours' depths, so the guide of a nested run is not broken by an
 * empty line inside it and does not run on past the run's end.
 */
export function lineShapes(text: string): LineShape[] {
  const lines = text.split("\n");
  const blank = lines.map((line) => line.trim() === "");
  const shapes = lines.map((line, index): LineShape => ({
    depth: blank[index] ? 0 : indentOf(line),
    heading: HEADING.exec(line)?.[1].length ?? 0,
  }));
  for (let index = 0; index < lines.length; index++) {
    if (!blank[index]) continue;
    let before = index - 1;
    while (before >= 0 && blank[before]) before--;
    let after = index + 1;
    while (after < lines.length && blank[after]) after++;
    shapes[index].depth = before < 0 || after >= lines.length
      ? 0 : Math.min(indentOf(lines[before]), indentOf(lines[after]));
  }
  return shapes;
}

/**
 * The line after the section that begins at `line` (KYO-904): a heading's section runs to the
 * next heading of its level or above, and any other line's to the next line that is not
 * indented more deeply than it, so a list item is taken with the items nested in it. Blank
 * lines at the section's end are left behind.
 */
export function sectionEnd(text: string, line: number): number {
  const lines = text.split("\n");
  const shapes = lineShapes(text);
  const first = shapes[line];
  if (!first) return line;
  let end = line + 1;
  if (first.heading) {
    while (end < lines.length && !(shapes[end].heading && shapes[end].heading <= first.heading)) end++;
  } else {
    while (end < lines.length && !shapes[end].heading
      && (shapes[end].depth > first.depth || (lines[end].trim() === "" && shapes[end].depth >= first.depth && first.depth > 0)
        || (lines[end].trim() === "" && end + 1 < lines.length && shapes[end + 1].depth > first.depth && !shapes[end + 1].heading))) end++;
  }
  while (end > line + 1 && lines[end - 1].trim() === "") end--;
  return end;
}

/** A box's words with what its lines hold, each by the line it is on. */
export interface Lined<T> { text: string; marks?: { line: number; items: T[] }[] }

/**
 * Lines `from` up to `to` of `source` moved to the end of `target`, each with what it holds.
 * The section is outdented to the left edge, since it leaves the item it was nested in.
 */
export function moveLines<T>(source: Lined<T>, target: Lined<T>, from: number, to: number): { source: Lined<T>; target: Lined<T> } {
  const lines = source.text.split("\n");
  const taken = lines.slice(from, to);
  const indent = Math.min(...taken.filter((line) => line.trim()).map((line) => line.length - line.trimStart().length));
  const moved = taken.map((line) => (line.trim() ? line.slice(Number.isFinite(indent) ? indent : 0) : line));
  const kept = [...lines.slice(0, from), ...lines.slice(to)];
  const held = target.text === "" && !(target.marks ?? []).length ? [] : target.text.split("\n");
  const marks = source.marks ?? [];
  return {
    source: {
      text: kept.join("\n"),
      marks: marks.filter((mark) => mark.line < from || mark.line >= to)
        .map((mark) => (mark.line >= to ? { ...mark, line: mark.line - (to - from) } : mark)),
    },
    target: {
      text: [...held, ...moved].join("\n"),
      marks: [
        ...(target.marks ?? []),
        ...marks.filter((mark) => mark.line >= from && mark.line < to)
          .map((mark) => ({ ...mark, line: held.length + mark.line - from })),
      ],
    },
  };
}
