// A line-based three-way merge, for a save kyo serve refused because the file changed
// since the tab read it (KYO-575).
//
// THE SITUATION. Two editors hold one file -- two windows on one project, or one on
// each of two machines. Each saves against the version IT loaded (kyo serve's If-Match),
// so the second save is refused with a 412 rather than written over the first. What
// the refused tab then holds is three texts:
//
//   base    the version the tab loaded (or last saved): what both sides started from
//   mine    what is in the tab's editor now
//   theirs  what the file holds now, written by somebody else
//
// and this module answers one text that keeps both sides' work, which the tab writes
// against the CURRENT version. Locking was ruled out (a sleeping laptop leaves a lock
// behind) and so was live collaborative editing (too much machinery for a notes file).
//
// THE RULES, which are the whole contract and what the checks hold it to:
//
//   1. A region only one side changed takes that side's change -- including a deletion,
//      so a line one window removed (a promoted selection, a tidy-up) stays removed
//      unless the other side also changed that region.
//   2. A region both sides changed IDENTICALLY is taken once.
//   3. Two insertions at the same place, where neither side changed an existing line,
//      keep both, theirs first and this tab's below, with no marker: nothing was
//      contradicted, and the commonest collision in a scratchpad is two windows that
//      each appended a thought at the end.
//   4. Anything else both sides changed is an OVERLAP: both texts are kept, the other
//      side's above and this tab's below, between three marked lines a person can read
//      and delete (`MERGE_MARKER_*`). Never git's `<<<<<<<`: this is a notes file a
//      person reads in an editor, not a working tree a tool resolves.
//
// So no line either side wrote is ever dropped: every line `mine` or `theirs` added or
// changed appears in the result, in its side's order. Lines are split on "\n" and joined
// with it, which is exactly invertible -- a missing final newline, a CRLF, a blank line
// all survive as the bytes they were.
//
// Written here rather than taken from npm: the whole
// algorithm is a Myers diff and a sweep over its hunks, about two hundred lines, and a
// dependency would have to be vetted, pinned and bundled into the renderer AND the main
// process for a function whose rules -- rule 3 above above all -- are this app's own.
// Electron-free and pure, so `seams.spec.ts` drives it directly.

/** The first of the three lines around an overlap: the other side's text follows. */
export const MERGE_MARKER_THEIRS = "━━ both versions kept: first, the one saved elsewhere ━━";

/** The middle line: this tab's text follows. `where` names the window that merged. */
export function mergeMarkerMine(where: string): string {
  return `━━ then the one from ${where}, saved after it ━━`;
}

/** The closing line. */
export const MERGE_MARKER_END = "━━ end of both versions: delete these three marker lines ━━";

/** What every marker line starts with, so a check (or a person) can find them. */
export const MERGE_MARKER_PREFIX = "━━ ";

export interface MergeResult {
  text: string;
  /** How many overlaps were kept between marker lines (rule 4); 0 is a clean merge. */
  conflicts: number;
}

/** One changed region of a two-way diff: base lines [oStart, oEnd) became side [sStart, sEnd). */
interface Hunk {
  oStart: number;
  oEnd: number;
  sStart: number;
  sEnd: number;
}

/**
 * The matched index pairs of a longest common subsequence of `a` and `b`, in order.
 *
 * Myers' O(ND) algorithm, after the common prefix and suffix are taken off -- so an edit
 * in one place of a long file costs the edit, not the file. The trace keeps only the
 * diagonals each step can reach, O(D^2) memory rather than O(D(N+M)).
 */
export function matchLines(a: readonly string[], b: readonly string[]): Array<[number, number]> {
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head += 1;
  let tail = 0;
  while (
    tail < a.length - head &&
    tail < b.length - head &&
    a[a.length - 1 - tail] === b[b.length - 1 - tail]
  ) {
    tail += 1;
  }
  const pairs: Array<[number, number]> = [];
  for (let i = 0; i < head; i += 1) pairs.push([i, i]);
  const n = a.length - head - tail;
  const m = b.length - head - tail;
  if (n > 0 && m > 0) {
    for (const [x, y] of myers(a, b, head, n, m)) pairs.push([x + head, y + head]);
  }
  for (let i = tail; i > 0; i -= 1) pairs.push([a.length - i, b.length - i]);
  return pairs;
}

function myers(
  a: readonly string[],
  b: readonly string[],
  at: number,
  n: number,
  m: number,
): Array<[number, number]> {
  const max = n + m;
  const offset = max + 1;
  const v = new Int32Array(2 * max + 3);
  // trace[d] is v as it stood when step d began, over the diagonals -d..d.
  const trace: Int32Array[] = [];
  const at_ = (d: number, k: number): number => {
    const row = trace[d];
    if (row === undefined || k < -d || k > d) return 0;
    return row[k + d] ?? 0;
  };
  let found = -1;
  outer: for (let d = 0; d <= max; d += 1) {
    trace.push(v.slice(offset - d, offset + d + 1));
    for (let k = -d; k <= d; k += 2) {
      let x =
        k === -d || (k !== d && (v[offset + k - 1] ?? 0) < (v[offset + k + 1] ?? 0))
          ? (v[offset + k + 1] ?? 0)
          : (v[offset + k - 1] ?? 0) + 1;
      let y = x - k;
      while (x < n && y < m && a[at + x] === b[at + y]) {
        x += 1;
        y += 1;
      }
      v[offset + k] = x;
      if (x >= n && y >= m) {
        found = d;
        break outer;
      }
    }
  }
  const pairs: Array<[number, number]> = [];
  let x = n;
  let y = m;
  for (let d = found; d >= 0; d -= 1) {
    const k = x - y;
    const prevK = k === -d || (k !== d && at_(d, k - 1) < at_(d, k + 1)) ? k + 1 : k - 1;
    const prevX = at_(d, prevK);
    const prevY = prevX - prevK;
    while (x > prevX && y > prevY) {
      pairs.push([x - 1, y - 1]);
      x -= 1;
      y -= 1;
    }
    if (d > 0) {
      x = prevX;
      y = prevY;
    }
  }
  return pairs.reverse();
}

/** The changed regions between `base` and one side, from the matched pairs. */
function hunksOf(base: readonly string[], side: readonly string[]): Hunk[] {
  const hunks: Hunk[] = [];
  let o = 0;
  let s = 0;
  for (const [mo, ms] of [...matchLines(base, side), [base.length, side.length] as const]) {
    if (mo > o || ms > s) hunks.push({ oStart: o, oEnd: mo, sStart: s, sEnd: ms });
    o = mo + 1;
    s = ms + 1;
  }
  return hunks;
}

/** Do two regions of the base collide? A pure insertion collides only strictly inside. */
function collides(gs: number, ge: number, h: Hunk): boolean {
  if (gs === ge && h.oStart === h.oEnd) return gs === h.oStart;
  if (gs === ge) return h.oStart < gs && gs < h.oEnd;
  if (h.oStart === h.oEnd) return gs < h.oStart && h.oStart < ge;
  return h.oStart < ge && gs < h.oEnd;
}

/** One side's lines for the base region [gs, ge), given its hunks inside that region. */
function sideText(
  base: readonly string[],
  side: readonly string[],
  hunks: readonly Hunk[],
  gs: number,
  ge: number,
): string[] {
  const first = hunks[0];
  const last = hunks[hunks.length - 1];
  if (first === undefined || last === undefined) return base.slice(gs, ge);
  return side.slice(first.sStart - (first.oStart - gs), last.sEnd + (ge - last.oEnd));
}

const sameLines = (a: readonly string[], b: readonly string[]): boolean =>
  a.length === b.length && a.every((line, i) => line === b[i]);

/**
 * Merge `mine` and `theirs`, both edited from `base` (see the rules at the top).
 * `where` names this tab's side in the marker line ("this window on devbox", say).
 */
export function merge3(base: string, mine: string, theirs: string, where: string): MergeResult {
  if (mine === theirs) return { text: mine, conflicts: 0 };
  if (mine === base) return { text: theirs, conflicts: 0 };
  if (theirs === base) return { text: mine, conflicts: 0 };
  const o = base.split("\n");
  const a = mine.split("\n");
  const b = theirs.split("\n");
  const tagged = [
    ...hunksOf(o, a).map((hunk) => ({ hunk, side: "mine" as const })),
    ...hunksOf(o, b).map((hunk) => ({ hunk, side: "theirs" as const })),
  ].sort(
    (x, y) =>
      x.hunk.oStart - y.hunk.oStart ||
      // At one start, a pure insertion sorts first: it goes BEFORE a changed region
      // that starts where it does, and does not collide with it.
      x.hunk.oEnd - x.hunk.oStart - (y.hunk.oEnd - y.hunk.oStart) ||
      // ...and between two insertions at one place, theirs is written first.
      (x.side === "theirs" ? -1 : 1),
  );

  const out: string[] = [];
  let conflicts = 0;
  let cursor = 0;
  let i = 0;
  while (i < tagged.length) {
    const head = tagged[i];
    if (head === undefined) break;
    let gs = head.hunk.oStart;
    let ge = head.hunk.oEnd;
    const group = [head];
    i += 1;
    for (;;) {
      const next = tagged[i];
      if (next === undefined || !collides(gs, ge, next.hunk)) break;
      group.push(next);
      gs = Math.min(gs, next.hunk.oStart);
      ge = Math.max(ge, next.hunk.oEnd);
      i += 1;
    }
    out.push(...o.slice(cursor, gs));
    cursor = ge;
    const mineHunks = group.filter((g) => g.side === "mine").map((g) => g.hunk);
    const theirHunks = group.filter((g) => g.side === "theirs").map((g) => g.hunk);
    const ours = sideText(o, a, mineHunks, gs, ge);
    const others = sideText(o, b, theirHunks, gs, ge);
    if (mineHunks.length === 0) out.push(...others);
    else if (theirHunks.length === 0) out.push(...ours);
    else if (sameLines(ours, others)) out.push(...ours);
    else if (gs === ge) out.push(...others, ...ours);
    else {
      conflicts += 1;
      out.push(MERGE_MARKER_THEIRS, ...others, mergeMarkerMine(where), ...ours, MERGE_MARKER_END);
    }
  }
  out.push(...o.slice(cursor));
  return { text: out.join("\n"), conflicts };
}

/**
 * The line-level changes that turn `from` into `to`, as character offsets in `from`:
 * what an editor dispatches to put a reloaded file on screen without moving a caret
 * that sits in a line neither text changed (KYO-575.c3).
 */
export function lineChanges(
  from: string,
  to: string,
): Array<{ from: number; to: number; insert: string }> {
  if (from === to) return [];
  const a = from.split("\n");
  const b = to.split("\n");
  const starts: number[] = [];
  let at = 0;
  for (const line of a) {
    starts.push(at);
    at += line.length + 1;
  }
  const offsetOf = (line: number): number => (line < a.length ? (starts[line] ?? 0) : at);
  const changes: Array<{ from: number; to: number; insert: string }> = [];
  for (const hunk of hunksOf(a, b)) {
    // Each line carries its own "\n" except the last, so a region is spliced as
    // whole lines: [start of oStart, start of oEnd) replaced by the new lines + "\n".
    const inserted = b.slice(hunk.sStart, hunk.sEnd);
    let start = offsetOf(hunk.oStart);
    let end = offsetOf(hunk.oEnd);
    let insert = inserted.map((line) => `${line}\n`).join("");
    if (hunk.oStart >= a.length) {
      // Lines added after the final line, which has no "\n" of its own to end it.
      start = from.length;
      end = from.length;
      insert = `\n${inserted.join("\n")}`;
    } else if (hunk.oEnd >= a.length) {
      // The region runs to the end of the text (so does its side: the closing pair
      // matches the two ends), and the last line carries no "\n".
      end = from.length;
      insert = inserted.join("\n");
      if (inserted.length === 0 && hunk.oStart > 0) {
        // A deletion of the final lines also takes the "\n" that ended the line before.
        start -= 1;
      }
    }
    changes.push({ from: start, to: end, insert });
  }
  return changes;
}

/** Apply `lineChanges` output to a string: the check that it reproduces `to`. */
export function applyChanges(
  text: string,
  changes: ReadonlyArray<{ from: number; to: number; insert: string }>,
): string {
  let out = "";
  let at = 0;
  for (const change of changes) {
    out += text.slice(at, change.from) + change.insert;
    at = change.to;
  }
  return out + text.slice(at);
}
