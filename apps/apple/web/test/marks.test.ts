// Run with `npm test`. A mark must stay with its paragraph through every kind of edit.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  drawnSpan, heldLine, lineOf, nearest, scrollToShow, sentenceAt, shiftLines, sourceIndex, sourceSpan,
} from "../src/marks.ts";

/** Applies an edit to three lines with ids 1, 2, 3 and new ids from 10. */
function edit(before: string, after: string, caret?: number) {
  let next = 10;
  const ids = before.split("\n").map((_, index) => index + 1);
  const shift = shiftLines(before, after, ids, () => next++, caret);
  assert.equal(shift.ids.length, after.split("\n").length, "one id for each line");
  return shift;
}

test("typing inside a line moves nothing", () => {
  assert.deepEqual(edit("one\ntwo\nthree", "one\ntwo!\nthree", 8).ids, [1, 2, 3]);
});

test("a Return at a line's end leaves its id above the new line", () => {
  assert.deepEqual(edit("one\ntwo\nthree", "one\ntwo\n\nthree", 8).ids, [1, 2, 10, 3]);
});

test("a Return at a line's start carries its id down with the text", () => {
  assert.deepEqual(edit("one\ntwo\nthree", "one\n\ntwo\nthree", 5).ids, [1, 10, 2, 3]);
});

test("a Return inside a line keeps the id on the first half", () => {
  assert.deepEqual(edit("one\ntwo\nthree", "one\nt\nwo\nthree", 6).ids, [1, 2, 10, 3]);
});

test("a line above pushes the ids below it down", () => {
  assert.deepEqual(edit("one\ntwo\nthree", "zero\none\ntwo\nthree", 5).ids, [10, 1, 2, 3]);
});

test("joining two lines merges the lower into the upper", () => {
  const shift = edit("one\ntwo\nthree", "onetwo\nthree", 3);
  assert.deepEqual(shift.ids, [1, 3]);
  assert.deepEqual(shift.merged, [[2, 1]]);
});

test("deleting a whole line with the Return after it drops that line only", () => {
  const shift = edit("line1\nline2\nline3", "line1\nline3", 6);
  assert.deepEqual(shift.ids, [1, 3]);
  assert.deepEqual(shift.dropped, [2]);
  assert.deepEqual(shift.merged, []);
});

test("deleting a whole line with the Return before it drops that line only", () => {
  const shift = edit("line1\nline2\nline3", "line1\nline3", 5);
  assert.deepEqual(shift.ids, [1, 3]);
  assert.deepEqual(shift.dropped, [2]);
});

test("backspacing an empty line away drops it and keeps its neighbours", () => {
  const shift = edit("one\n\nthree", "one\nthree", 3);
  assert.deepEqual(shift.ids, [1, 3]);
  assert.deepEqual(shift.dropped, [2]);
});

test("replacing several lines drops the ones wholly inside the selection", () => {
  const shift = edit("one\ntwo\nthree", "oXe", 2);
  assert.deepEqual(shift.ids, [1]);
  assert.deepEqual(shift.dropped, [2]);
  assert.deepEqual(shift.merged, [[3, 1]]);
});

test("replacing a line's whole text keeps its id", () => {
  assert.deepEqual(edit("one\ntwo\nthree", "one\nnew\nthree", 7).ids, [1, 2, 3]);
});

test("pasting lines into the middle gives them new ids", () => {
  assert.deepEqual(edit("one\ntwo\nthree", "one\na\nb\ntwo\nthree", 8).ids, [1, 10, 11, 2, 3]);
});

test("without a caret the edit is still found", () => {
  assert.deepEqual(edit("one\ntwo\nthree", "one\nthree").ids.length, 2);
  assert.deepEqual(edit("one\ntwo", "one\ntwo\nthree").ids, [1, 2, 10]);
});

test("clearing everything leaves one new line", () => {
  const shift = edit("one\ntwo\nthree", "", 0);
  assert.deepEqual(shift.ids, [10]);
  assert.deepEqual(shift.dropped.sort(), [1, 2, 3]);
});

test("deleting a line and the Return after it, above an empty line, drops it", () => {
  const shift = edit("one\ntwo\n\nfour", "one\n\nfour", 4);
  assert.deepEqual(shift.dropped.includes(2), true);
  assert.equal(shift.ids[0], 1);
  assert.equal(shift.ids[2], 4);
});

// An anchor or a file goes to the paragraph the caret is in (KYO-727).
test("a caret is in the paragraph that holds it, at either end of the text", () => {
  const text = "one\n\nthree";
  assert.equal(lineOf(text, 0), 0);
  assert.equal(lineOf(text, 3), 0, "at the first paragraph's end");
  assert.equal(lineOf(text, 4), 1, "on the empty paragraph");
  assert.equal(lineOf(text, 5), 2, "at the last paragraph's start");
  assert.equal(lineOf(text, text.length), 2);
});

test("a caret in an empty prompt is in its one paragraph", () => {
  assert.equal(lineOf("", 0), 0);
});

test("a caret after a closing Return is on the new last paragraph", () => {
  assert.equal(lineOf("one\n", 4), 1);
});

// Typing scrolls the composer only as far as the typed line needs (KYO-727).
test("a line inside the view scrolls nothing", () => {
  assert.equal(scrollToShow(100, 122, 50, 700), 0);
  assert.equal(scrollToShow(50, 700, 50, 700), 0, "edge to edge");
});

test("a line under the view comes up by what is hidden", () => {
  assert.equal(scrollToShow(690, 712, 50, 700), 12);
});

test("a line over the view comes down by what is hidden", () => {
  assert.equal(scrollToShow(30, 52, 50, 700), -20);
});

test("a span taller than the view is shown from its top", () => {
  assert.equal(scrollToShow(100, 900, 50, 700), 50);
  assert.equal(scrollToShow(20, 900, 50, 700), -30);
});

// ---- KYO-729: an anchor is drawn at the place its offsets name

/** The drawn words an anchor on `words`, at their `nth` occurrence in `source`, is shown on. */
function shown(source: string, drawn: string, words: string, nth: number, formatted = false) {
  let start = -1;
  for (let count = 0; count <= nth; count++) start = source.indexOf(words, start + 1);
  assert.ok(start >= 0, "the source holds the words that often");
  const span = drawnSpan(source, drawn, start, start + words.length, formatted);
  return span && { at: span[0], text: drawn.slice(span[0], span[1]) };
}

test("words a block holds twice are highlighted where the offsets name, not at the first", () => {
  const block = "Interrupt is grey while idle. Interrupt is red during a turn.";
  assert.deepEqual(shown(block, block, "Interrupt", 0), { at: 0, text: "Interrupt" });
  assert.deepEqual(shown(block, block, "Interrupt", 1), { at: 30, text: "Interrupt" });
  // The fault this replaces: the first occurrence, whatever the offsets said.
  assert.equal(block.indexOf("Interrupt"), 0);
});

test("the second occurrence is found past markdown that shifts the drawn text", () => {
  const source = "**Interrupt** is `grey` while idle, and [Interrupt](https://example.org/a) is red.";
  const drawn = "Interrupt is grey while idle, and Interrupt is red.";
  assert.deepEqual(shown(source, drawn, "Interrupt", 1), { at: 34, text: "Interrupt" });
  assert.deepEqual(shown(source, drawn, "Interrupt", 0), { at: 0, text: "Interrupt" });
});

test("a word repeated three times in a list item is told apart each time", () => {
  const source = "- the send, the hold and the retraction\n";
  const drawn = "the send, the hold and the retraction";
  assert.deepEqual([0, 1, 2].map((nth) => shown(source, drawn, "the", nth)?.at), [0, 10, 23]);
});

test("words drawn differently from their source are placed only when that is asked", () => {
  const source = "Use `kyo show` first. Then **read** the brief.";
  const drawn = "Use kyo show first. Then read the brief.";
  const start = source.indexOf("Then");
  assert.equal(drawnSpan(source, drawn, start, source.length), null);
  const span = drawnSpan(source, drawn, start, source.length, true)!;
  assert.equal(drawn.slice(span[0], span[1]), "Then read the brief.");
});

test("the typographer's quotes and dashes do not move what follows them", () => {
  const source = 'He said "stop" -- twice... and stop it did.';
  const drawn = "He said “stop” – twice… and stop it did.";
  assert.deepEqual(shown(source, drawn, "stop", 1), { at: 28, text: "stop" });
});

test("nearest takes the occurrence closest to the place named", () => {
  assert.equal(nearest("ab ab ab", "ab", 4), 3);
  assert.equal(nearest("ab ab ab", "ab", 100), 6);
  assert.equal(nearest("ab ab ab", "cd", 0), -1);
  assert.equal(nearest("ab", "", 0), -1);
});

test("a place in the drawn text is found in the source past the marks before it", () => {
  const source = "**Interrupt** is grey. Interrupt is red.";
  const drawn = "Interrupt is grey. Interrupt is red.";
  assert.equal(sourceIndex(source, drawn, drawn.lastIndexOf("Interrupt")), source.lastIndexOf("Interrupt"));
});

// ---- KYO-729: a right-click with no selection names the sentence under the pointer

function sentence(text: string, at: number): string {
  const [from, to] = sentenceAt(text, at);
  return text.slice(from, to);
}

test("the sentence under a place ends at its full stop", () => {
  const text = "Interrupt is grey while idle. Interrupt is red during a turn. It stays red.";
  assert.equal(sentence(text, 2), "Interrupt is grey while idle.");
  assert.equal(sentence(text, 33), "Interrupt is red during a turn.");
  assert.equal(sentence(text, text.length - 1), "It stays red.");
});

test("a sentence ends at a line break or at the end of its block", () => {
  assert.equal(sentence("first line\nsecond line\nthird", 14), "second line");
  assert.equal(sentence("no stop at the end", 5), "no stop at the end");
  assert.equal(sentence("one. two", 6), "two");
});

test("a full stop inside a word ends nothing, and a question mark ends a sentence", () => {
  const text = "Open chat.ts at 3.5 times the size. Is it red? Yes.";
  assert.equal(sentence(text, 6), "Open chat.ts at 3.5 times the size.");
  assert.equal(sentence(text, 38), "Is it red?");
});

test("a place on the space between two sentences names the one that follows", () => {
  assert.equal(sentence("One. Two.", 4), "Two.");
});

test("a sentence's span in the source takes the marks that wrap its words", () => {
  const source = "**Interrupt** is grey. Then `kyo show` says **why.** Read [the brief](https://example.org/b).";
  const drawn = "Interrupt is grey. Then kyo show says why. Read the brief.";
  const of = (at: number) => {
    const [from, to] = sentenceAt(drawn, at);
    const span = sourceSpan(source, drawn, from, to)!;
    return source.slice(span[0], span[1]);
  };
  assert.equal(of(3), "**Interrupt** is grey.");
  assert.equal(of(25), "Then `kyo show` says **why.**");
  assert.equal(of(drawn.length - 3), "Read [the brief](https://example.org/b).");
});

test("the sentence of a repeated word is the one clicked, in the source as on the page", () => {
  const source = "- **Interrupt** is grey while idle. Interrupt is red during a turn.\n";
  const drawn = "Interrupt is grey while idle. Interrupt is red during a turn.";
  const [from, to] = sentenceAt(drawn, drawn.lastIndexOf("Interrupt") + 2);
  const [start, end] = sourceSpan(source, drawn, from, to)!;
  assert.equal(source.slice(start, end), "Interrupt is red during a turn.");
  // And it is drawn back where it was clicked.
  assert.deepEqual(drawnSpan(source, drawn, start, end), [from, to]);
});

test("drawn text that came from nowhere in the source has no span", () => {
  assert.equal(sourceSpan("alpha beta", "≠≠≠", 0, 3), null);
});

// ---- KYO-730: the line above a held prompt

const REPLY = "8c1f73e2-0d9d-4c8e-a1d5-3f2e0b7a4c11";
const COUNTS = "(replies are your text messages; tool calls are not counted)";

test("the line above a held prompt is read for its time, its reply and the replies after", () => {
  const line = `held 14 minutes: written before your reply ${REPLY} "The install is done and a..." and 2 later replies ${COUNTS}`;
  assert.deepEqual(heldLine(line), {
    held: "14 minutes", uuid: REPLY, words: "The install is done and a...", later: 2,
  });
});

test("a line with one reply, one later reply or seconds is read as well", () => {
  assert.deepEqual(heldLine(`held 1 second: written before your reply ${REPLY} "done" ${COUNTS}`), {
    held: "1 second", uuid: REPLY, words: "done", later: 0,
  });
  assert.equal(heldLine(`held 1 minute: written before your reply ${REPLY} "a "quoted" word" and 1 later reply ${COUNTS}`)?.later, 1);
});

test("a line a person might type, and an anchor line, are not taken for it", () => {
  assert.equal(heldLine("held 14 minutes: written before your reply"), null);
  assert.equal(heldLine(`on your reply ${REPLY} "the transcript does not stream; the..."`), null);
  assert.equal(heldLine(`held 14 minutes: written before your reply ${REPLY} "x"`), null);
});
