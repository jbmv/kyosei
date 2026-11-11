// Search in the chat, a record and a file (KYO-741, KYO-754). The matching is here and takes only text, so that the page's
// part is the drawing of the marks. What is searched is the text as it is drawn, so that what
// is marked is what is seen: a markdown mark in the source is not a word the person can see.

/**
 * What a query asks for: the query as it was typed, as one phrase (KYO-754), the way a
 * browser's own find reads it. Nothing in it is an operator: a quotation mark is a character
 * to be found, and two words are found only where they stand together in that order. A query
 * of spaces alone asks for nothing.
 */
export function queryPhrase(query: string): string {
  return query.trim() ? fold(query) : "";
}

/**
 * The text in lower case, with every character kept at its own length so that a position in
 * the result is a position in the text. A character whose lower case is longer stays as it is.
 */
export function fold(text: string): string {
  const lower = text.toLowerCase();
  // Lower case never shortens a character, so an equal length is every position kept.
  if (lower.length === text.length) return lower;
  let out = "";
  for (const char of text) {
    const small = char.toLowerCase();
    out += small.length === char.length ? small : char;
  }
  return out;
}

/**
 * Where the phrase occurs in `text`, as sorted `[from, to]` spans that do not overlap.
 * Matching ignores case. A run of spaces in the phrase matches any run of white space in the
 * text, since a line's end inside a paragraph is drawn as a space. `folded` is `fold(text)`
 * when the caller has it already, which is how a text is indexed once and searched on each key.
 */
export function findSpans(text: string, phrase: string, folded: string = fold(text)): [number, number][] {
  const spans: [number, number][] = [];
  if (!phrase) return spans;
  if (!/\s/.test(phrase)) {
    for (let at = folded.indexOf(phrase); at >= 0; at = folded.indexOf(phrase, at + phrase.length)) {
      spans.push([at, at + phrase.length]);
    }
    return spans;
  }
  const pattern = new RegExp(
    phrase.split(/\s+/).map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+"), "g");
  for (let found = pattern.exec(folded); found; found = pattern.exec(folded)) {
    spans.push([found.index, found.index + found[0].length]);
  }
  return spans;
}

/**
 * The last of `count` positions that `holds`, or -1 when none does. Every position that holds
 * stands before every one that does not, so the answer is found by halving.
 */
export function lastHolding(count: number, holds: (index: number) => boolean): number {
  let low = 0;
  let high = count;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (holds(middle)) low = middle + 1;
    else high = middle;
  }
  return low - 1;
}

/** The position of the next or previous hit among `count`, wrapping, or -1 where there are none. */
export function step(current: number, count: number, forward: boolean): number {
  if (count <= 0) return -1;
  if (current < 0 || current >= count) return forward ? 0 : count - 1;
  return (current + (forward ? 1 : count - 1)) % count;
}

/** What the field shows: the current hit of the count, or that there are none. */
export function tally(current: number, count: number, asked: boolean): string {
  if (!asked) return "";
  return count ? `${current + 1} of ${count}` : "No results";
}

/**
 * How far a scroll must move to bring a hit into the band of the window between `top` and
 * `bottom` (KYO-754): none when it is within, and otherwise what puts its middle at the band's.
 */
export function scrollBy(rectTop: number, rectBottom: number, top: number, bottom: number): number {
  if (rectTop >= top && rectBottom <= bottom) return 0;
  return rectTop - (top + bottom) / 2 + (rectBottom - rectTop) / 2;
}
