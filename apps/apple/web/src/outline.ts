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
  /** Whether the line is three or more hyphens alone, which is drawn as a rule (KYO-923). */
  rule: boolean;
}

const HEADING = /^(#{1,6})\s+\S/;
/** Markdown's thematic break in hyphens: up to three spaces, three or more `-`, nothing else. */
const RULE = /^ {0,3}-{3,}[ \t]*$/;

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
    rule: RULE.test(line),
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
 * Whether a section break stands at line `at` of `lines` (KYO-923): a rule, or the first of
 * two blank lines in a row. The break is the composer's, not markdown's, and changes nothing
 * that is sent; it only ends the sections above it.
 */
function breaksAt(lines: readonly string[], at: number): boolean {
  return RULE.test(lines[at]) || (lines[at].trim() === "" && at + 1 < lines.length && lines[at + 1].trim() === "");
}

/**
 * The line after the section that begins at `line` (KYO-904): a heading's section runs to the
 * next heading of its level or above, and any other line's to the next line that is not
 * indented more deeply than it, so a list item is taken with the items nested in it. A
 * section break, which is two blank lines in a row or a line of three or more hyphens, ends
 * every section above it (KYO-923), so the lines after it belong to none. Blank lines at the
 * section's end are left behind.
 */
export function sectionEnd(text: string, line: number): number {
  const lines = text.split("\n");
  const shapes = lineShapes(text);
  const first = shapes[line];
  if (!first) return line;
  let end = line + 1;
  if (first.heading) {
    while (end < lines.length && !(shapes[end].heading && shapes[end].heading <= first.heading) && !breaksAt(lines, end)) end++;
  } else {
    while (end < lines.length && !shapes[end].heading && !breaksAt(lines, end)
      && (shapes[end].depth > first.depth || (lines[end].trim() === "" && shapes[end].depth >= first.depth && first.depth > 0)
        || (lines[end].trim() === "" && end + 1 < lines.length && shapes[end + 1].depth > first.depth && !shapes[end + 1].heading))) end++;
  }
  while (end > line + 1 && lines[end - 1].trim() === "") end--;
  return end;
}

/**
 * For each line of `text`, the headings whose sections hold it, outermost first, each by the
 * line it is on (KYO-917). A heading's section is the one `sectionEnd` gives, which folding
 * and Send to prompt use too, so the heading's own line is in it and the blank lines at its
 * end are not, and a section break ends it (KYO-923). Sections nest, since a heading's ends at the next heading of its level or above,
 * so the list is each line's chain of sections from the outermost in. A line before the first
 * heading is in none.
 */
export function headingSections(text: string): number[][] {
  const shapes = lineShapes(text);
  const held: number[][] = shapes.map(() => []);
  shapes.forEach((shape, line) => {
    if (!shape.heading) return;
    const end = sectionEnd(text, line);
    for (let at = line; at < end; at++) held[at].push(line);
  });
  return held;
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
