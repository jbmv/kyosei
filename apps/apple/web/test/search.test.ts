// Run with `npm test`. Search in the chat matches the drawn words, in any case, all terms at once.
import assert from "node:assert/strict";
import { test } from "node:test";
import { findSpans, fold, queryTerms, step, tally } from "../src/search.ts";

const spansIn = (text: string, query: string) => findSpans(text, queryTerms(query));

test("a word matches every occurrence, whatever its case", () => {
  assert.deepEqual(spansIn("Two copies, two COPIES.", "copies"), [[4, 10], [16, 22]]);
});

test("several words match a text that holds them all", () => {
  assert.deepEqual(spansIn("if two copies were open", "copies open"), [[7, 13], [19, 23]]);
  assert.deepEqual(spansIn("if two copies were open", "copies closed"), []);
});

test("a quoted phrase matches as written, in order and with its spacing", () => {
  assert.deepEqual(spansIn("two copies were open; copies two", '"two copies"'), [[0, 10]]);
  assert.deepEqual(spansIn("copies two", '"two copies"'), []);
  assert.deepEqual(spansIn("two  copies", '"two copies"'), []);
});

test("a phrase and a word are both required", () => {
  assert.deepEqual(spansIn("a b c d", '"a b" d'), [[0, 3], [6, 7]]);
  assert.deepEqual(spansIn("a b c", '"a b" d'), []);
});

test("an unclosed quote reads as a word", () => {
  assert.deepEqual(queryTerms('"two copies'), ["two", "copies"]);
});

test("overlapping hits are one span, and an empty query matches nothing", () => {
  assert.deepEqual(spansIn("abcd", "abc bcd"), [[0, 4]]);
  assert.deepEqual(spansIn("abcd", "  "), []);
  assert.deepEqual(spansIn("abcd", '""'), []);
});

test("folding keeps every position, even where a lower case is longer", () => {
  assert.equal(fold("İA").length, 2);
  assert.deepEqual(spansIn("İ A", "a"), [[2, 3]]);
});

test("stepping wraps, and starts at an end", () => {
  assert.equal(step(-1, 3, true), 0);
  assert.equal(step(-1, 3, false), 2);
  assert.equal(step(2, 3, true), 0);
  assert.equal(step(0, 3, false), 2);
  assert.equal(step(0, 0, true), -1);
});

test("the tally names the current hit and the count", () => {
  assert.equal(tally(1, 4, true), "2 of 4");
  assert.equal(tally(-1, 0, true), "No results");
  assert.equal(tally(-1, 0, false), "");
});
