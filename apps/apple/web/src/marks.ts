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

/** The line of `text` that `position` is in, which for a caret is the paragraph it stands in. */
export function lineOf(text: string, position: number): number {
  let line = 0;
  for (let at = text.indexOf("\n"); at >= 0 && at < position; at = text.indexOf("\n", at + 1)) line++;
  return line;
}

/**
 * How far a view must scroll to show the span from `top` to `bottom`, all four measured on
 * the same axis: nothing while the span is inside the view, and otherwise the least that
 * brings it in. A span taller than the view is shown from its top.
 */
export function scrollToShow(top: number, bottom: number, viewTop: number, viewBottom: number): number {
  if (top < viewTop) return top - viewTop;
  if (bottom > viewBottom) return Math.min(bottom - viewBottom, top - viewTop);
  return 0;
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

// ---- where an anchor's words are drawn (KYO-729)
//
// An anchor's offsets count the source text of a reply or a prompt, and the page shows that
// text drawn: markdown without its marks, quotes and dashes set by the typographer, a server
// line as a chip. The two are matched character by character, in order, so that a place in
// one can be found in the other and words that occur twice are told apart by where they are.

/** How far ahead a character of the drawn text is looked for, as one of a run and alone. */
const RUN = 3;
const CLOSE = 16;
const FAR = 400;
const ALONE = 40;

const SPACE = /\s/;

interface Matching {
  /** For each drawn character, the source index it was drawn from, or where it would be. */
  at: number[];
  /** Whether that character was found in the source; a drawn-only one was not. */
  found: boolean[];
}

/**
 * Matches each character of `drawn` to one of `source`, in order. A character is taken where
 * the three characters from it next occur, if that is close; else where it next occurs alone,
 * if close; else at the run farther off. One found nowhere near was drawn from nothing, as a
 * curled quote is, and takes the place the matching has reached without moving it. The
 * address of a link is passed over once its words are matched.
 */
function match(source: string, drawn: string): Matching {
  const at: number[] = [];
  const found: boolean[] = [];
  let reached = 0;
  const ahead = (words: string, within: number) => {
    const index = source.slice(reached, reached + within + words.length).indexOf(words);
    return index < 0 ? -1 : reached + index;
  };
  for (let index = 0; index < drawn.length; index++) {
    const char = drawn[index];
    // A link's address is not drawn, and its characters must not be taken for later ones.
    if (char !== "]" && source.startsWith("](", reached)) {
      const close = source.indexOf(")", reached);
      if (close >= 0) reached = close + 1;
    }
    if (SPACE.test(char)) {
      const space = reached < source.length && SPACE.test(source[reached]);
      at.push(reached);
      found.push(space);
      if (space) reached++;
      continue;
    }
    const run = drawn.slice(index, index + RUN);
    const inRun = run.length === RUN ? ahead(run, FAR) : -1;
    const alone = ahead(char, ALONE);
    let place = -1;
    if (inRun >= 0 && inRun - reached <= CLOSE) place = inRun;
    else if (alone >= 0 && alone - reached <= CLOSE) place = alone;
    else if (inRun >= 0) place = inRun;
    else place = alone;
    at.push(place < 0 ? reached : place);
    found.push(place >= 0);
    if (place >= 0) reached = place + 1;
  }
  return { at, found };
}

/** Where `words` occur in `text` nearest to `about`, or -1 when they do not occur. */
export function nearest(text: string, words: string, about: number): number {
  let best = -1;
  if (!words) return best;
  for (let at = text.indexOf(words); at >= 0; at = text.indexOf(words, at + 1)) {
    if (best < 0 || Math.abs(at - about) < Math.abs(best - about)) best = at;
  }
  return best;
}

/**
 * The sentence of `text` that position `at` is in, as `[from, to]`: it ends at a full stop,
 * question mark or exclamation mark that white space or the end follows, at a line break, or
 * at the end of the text, and has no white space at either end. A full stop inside a word,
 * as in a file name or a number, ends nothing.
 */
export function sentenceAt(text: string, at: number): [number, number] {
  let from = 0;
  let to = text.length;
  for (const end of text.matchAll(/[.!?]+["')\]”’]*(?=\s|$)|\n/g)) {
    const stop = end.index + (end[0] === "\n" ? 0 : end[0].length);
    if (at < stop) {
      to = stop;
      break;
    }
    from = end.index + end[0].length;
  }
  while (from < to && SPACE.test(text[from])) from++;
  while (to > from && SPACE.test(text[to - 1])) to--;
  return [from, to];
}

/** Inline marks that open or close around words, which a span of the source takes with it. */
const OPENING = "*_`~[\"'";
const CLOSING = "*_`~\"'";

/**
 * The span of `source` that the drawn characters `from` to `to` came from, or null when
 * their ends cannot be found in it. The marks that wrap the words are part of the span, and
 * so is the address of a link the words end in.
 */
export function sourceSpan(source: string, drawn: string, from: number, to: number): [number, number] | null {
  const { at, found } = match(source, drawn);
  while (from < to && !(found[from] && !SPACE.test(drawn[from]))) from++;
  while (to > from && !(found[to - 1] && !SPACE.test(drawn[to - 1]))) to--;
  if (from >= to) return null;
  let start = at[from];
  let end = at[to - 1] + 1;
  while (start > 0 && OPENING.includes(source[start - 1])) start--;
  const closing = () => {
    while (end < source.length && CLOSING.includes(source[end])) end++;
  };
  closing();
  const link = /^\]\([^)\n]*\)/.exec(source.slice(end));
  if (link) end += link[0].length;
  closing();
  return [start, end];
}

/** The source index the drawn character `index` came from, or where it would have been. */
export function sourceIndex(source: string, drawn: string, index: number): number {
  const { at } = match(source, drawn);
  return index < at.length ? at[index] : source.length;
}

/**
 * Where the span `start` to `end` of `source` is drawn, as `[from, to]` in `drawn`, or null.
 * Words drawn as they are written are found by their text, at the occurrence nearest to
 * where the offsets point, so that the same words earlier in the text are not taken for
 * them. With `formatted`, words drawn differently from their source are placed by the
 * characters that came from the span.
 */
export function drawnSpan(
  source: string, drawn: string, start: number, end: number, formatted = false,
): [number, number] | null {
  const { at, found } = match(source, drawn);
  const first = at.findIndex((place) => place >= start);
  const words = source.slice(start, end);
  const written = nearest(drawn, words, first < 0 ? drawn.length : first);
  if (written >= 0) return [written, written + words.length];
  if (!formatted) return null;
  let from = -1;
  let to = -1;
  at.forEach((place, index) => {
    if (!found[index] || SPACE.test(drawn[index]) || place < start || place >= end) return;
    if (from < 0) from = index;
    to = index + 1;
  });
  return from < 0 ? null : [from, to];
}

// ---- the line above a held prompt (KYO-730)

/** What `kyo serve` wrote above a prompt it held across one or more of the agent's replies. */
export interface HeldLine {
  /** How long it was held, as written: `14 minutes`. */
  held: string;
  /** The record of the first reply its writer had not seen. */
  uuid: string;
  /** That reply's opening words, with `...` where they go on. */
  words: string;
  /** How many more replies followed that one. */
  later: number;
}

const HELD_LINE = /^held (\d+ (?:second|minute)s?): written before your reply ([0-9a-f-]{36}) "(.*)"(?: and (\d+) later repl(?:y|ies))? \(replies are your text messages; tool calls are not counted\)$/;

/** Reads the line `kyo serve` types above a held prompt at delivery, or null for any other. */
export function heldLine(line: string): HeldLine | null {
  const read = line.match(HELD_LINE);
  return read && { held: read[1], uuid: read[2], words: read[3], later: Number(read[4] ?? 0) };
}
