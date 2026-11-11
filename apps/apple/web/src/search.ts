// Search in the chat (KYO-741). The matching is here and takes only text, so that the page's
// part is the drawing of the marks. What is searched is the text as it is drawn, so that what
// is marked is what is seen: a markdown mark in the source is not a word the person can see.

/** The words a query asks for: each bare word, and each quoted phrase as written. */
export function queryTerms(query: string): string[] {
  const terms: string[] = [];
  const pattern = /"([^"]*)"|(\S+)/g;
  for (let found = pattern.exec(query); found; found = pattern.exec(query)) {
    // An unclosed quote leaves its words to be read as bare words.
    const term = found[1] !== undefined ? found[1].trim() : found[2].replace(/^"+/, "");
    if (term) terms.push(fold(term));
  }
  return terms;
}

/**
 * The text in lower case, with every character kept at its own length so that a position in
 * the result is a position in the text. A character whose lower case is longer stays as it is.
 */
export function fold(text: string): string {
  let out = "";
  for (const char of text) {
    const lower = char.toLowerCase();
    out += lower.length === char.length ? lower : char;
  }
  return out;
}

/**
 * Where the terms occur in `text`, as sorted, non-overlapping `[from, to]` spans, or none
 * when the text does not hold every term. Matching ignores case. `folded` is `fold(text)`
 * when the caller has it already, which is how a text is indexed once and searched on each key.
 */
export function findSpans(text: string, terms: string[], folded: string = fold(text)): [number, number][] {
  if (!terms.length) return [];
  const spans: [number, number][] = [];
  for (const term of terms) {
    const before = spans.length;
    for (let at = folded.indexOf(term); at >= 0; at = folded.indexOf(term, at + term.length)) {
      spans.push([at, at + term.length]);
    }
    if (spans.length === before) return [];
  }
  spans.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  const merged: [number, number][] = [];
  for (const span of spans) {
    const last = merged[merged.length - 1];
    if (last && span[0] <= last[1]) last[1] = Math.max(last[1], span[1]);
    else merged.push([span[0], span[1]]);
  }
  return merged;
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
