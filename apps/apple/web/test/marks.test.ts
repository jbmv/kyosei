// Run with `npm test`. A mark must stay with its paragraph through every kind of edit.
import assert from "node:assert/strict";
import { test } from "node:test";
import { shiftLines } from "../src/marks.ts";

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
