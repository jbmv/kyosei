// Which line of a prompt each mark belongs to, kept true while the text is edited. A text
// area reports only its new text, so each edit is read as one replaced range and the lines'
// ids are carried across it. Nothing here touches the page.

export interface Shift {
  /** One id for each line of the text after the edit. */
  ids: number[];
  /** Lines the edit removed entirely. */
  dropped: number[];
  /** Lines joined into another by the edit, as `[from, into]`. */
  merged: [number, number][];
}

function lineOf(text: string, position: number): number {
  let line = 0;
  for (let at = text.indexOf("\n"); at >= 0 && at < position; at = text.indexOf("\n", at + 1)) line++;
  return line;
}

/**
 * Carries the lines' ids from `before` to `after`.
 *
 * A line keeps its id while any of its text survives, a line typed into keeps it, and two
 * lines joined become one that holds both. `caret` is the caret after the edit: the text
 * behind it is unchanged in an ordinary edit, which settles where the edit was when the
 * texts alone leave it open, as when one of two lines that begin alike is deleted.
 */
export function shiftLines(
  before: string, after: string, ids: number[], fresh: () => number, caret?: number,
): Shift {
  if (before === after) return { ids, dropped: [], merged: [] };
  const shorter = Math.min(before.length, after.length);
  let tail = 0;
  const hinted = caret === undefined ? -1 : after.length - caret;
  if (hinted >= 0 && hinted <= shorter
      && before.slice(before.length - hinted) === after.slice(after.length - hinted)) {
    tail = hinted;
  }
  let head = 0;
  while (head < shorter - tail && before[head] === after[head]) head++;
  if (hinted !== tail) {
    while (tail < shorter - head && before[before.length - 1 - tail] === after[after.length - 1 - tail]) tail++;
  }

  const changedTo = before.length - tail;
  const first = lineOf(before, head);
  const last = lineOf(before, changedTo);
  const lastAfter = lineOf(after, after.length - tail);
  const headKept = head - (before.lastIndexOf("\n", head - 1) + 1);
  const lineEnd = before.indexOf("\n", changedTo);
  const tailKept = (lineEnd < 0 ? before.length : lineEnd) - changedTo;

  const middle: number[] = [];
  for (let line = first; line <= lastAfter; line++) middle.push(-1);
  const dropped = ids.slice(first + 1, last);
  const merged: [number, number][] = [];

  if (first === last) {
    // An edit inside one line. A Return at its start pushes its text, and its id, down.
    const down = lastAfter > first && headKept === 0 && tailKept > 0;
    middle[down ? middle.length - 1 : 0] = ids[first];
  } else {
    const top = ids[first];
    const bottom = ids[last];
    if (lastAfter === first) {
      if (headKept > 0 && tailKept > 0) {
        middle[0] = top;
        merged.push([bottom, top]);
      } else if (tailKept > 0) {
        middle[0] = bottom;
        dropped.push(top);
      } else if (headKept > 0) {
        middle[0] = top;
        dropped.push(bottom);
      } else {
        // Nothing of either line is left, so the line that remains is a new one.
        dropped.push(top, bottom);
      }
    } else {
      middle[0] = top;
      if (tailKept > 0) middle[middle.length - 1] = bottom;
      else dropped.push(bottom);
    }
  }
  return {
    ids: [...ids.slice(0, first), ...middle.map((id) => (id < 0 ? fresh() : id)), ...ids.slice(last + 1)],
    dropped,
    merged,
  };
}
