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
