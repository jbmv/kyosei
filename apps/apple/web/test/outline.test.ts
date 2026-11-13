import assert from "node:assert/strict";
import { test } from "node:test";

import { lineShapes } from "../src/outline.ts";

const depths = (text: string) => lineShapes(text).map((shape) => shape.depth);
const headings = (text: string) => lineShapes(text).map((shape) => shape.heading);

test("a nested item is one level deeper for each two spaces", () => {
  assert.deepEqual(depths("- a\n  - b\n    - c\n  - d\n- e"), [0, 1, 2, 1, 0]);
});

test("a continuation line and a numbered item are indented as their spaces say", () => {
  assert.deepEqual(depths("1. a\n   more\n  1. b"), [0, 1, 1]);
});

test("a tab is one level", () => {
  assert.deepEqual(depths("- a\n\t- b"), [0, 1]);
});

test("a blank line inside a nested run keeps the run's guide and one after it does not", () => {
  assert.deepEqual(depths("- a\n  - b\n\n  - c\n\n- d"), [0, 1, 1, 1, 0, 0]);
  assert.deepEqual(depths("  - b\n"), [1, 0]);
});

test("a heading is one to six # and a space before words", () => {
  assert.deepEqual(headings("# bugs\n## chat pane\n- a\n#tag\n####### seven\n# "), [1, 2, 0, 0, 0, 0]);
});

import { moveLines, sectionEnd } from "../src/outline.ts";

test("a list item's section is the item and what is nested in it", () => {
  const text = "- a\n  - b\n    - c\n  - d\n- e";
  assert.equal(sectionEnd(text, 0), 4);
  assert.equal(sectionEnd(text, 1), 3);
  assert.equal(sectionEnd(text, 2), 3);
  assert.equal(sectionEnd(text, 4), 5);
});

test("a plain line is its own section, and a blank line after a section is left behind", () => {
  assert.equal(sectionEnd("one\ntwo", 0), 1);
  assert.equal(sectionEnd("- a\n  - b\n\n- c", 0), 2);
  assert.equal(sectionEnd("- a\n\n  - b\n- c", 0), 3);
});

test("a heading's section runs to the next heading of its level or above", () => {
  const text = "# one\n- a\n## inner\n- b\n# two\n- c";
  assert.equal(sectionEnd(text, 0), 4);
  assert.equal(sectionEnd(text, 2), 4);
  assert.equal(sectionEnd(text, 4), 6);
  assert.equal(sectionEnd(text, 1), 2);  // an item stops at a heading
});

test("moved lines go to the end of the target with what they hold, outdented, and the rest close up", () => {
  const source = { text: "- a\n  - b\n    - c\n- d", marks: [{ line: 1, items: ["x"] }, { line: 3, items: ["y"] }] };
  const moved = moveLines(source, { text: "first", marks: [{ line: 0, items: ["z"] }] }, 1, 3);
  assert.deepEqual(moved.source, { text: "- a\n- d", marks: [{ line: 1, items: ["y"] }] });
  assert.deepEqual(moved.target, { text: "first\n- b\n  - c", marks: [{ line: 0, items: ["z"] }, { line: 1, items: ["x"] }] });
  assert.deepEqual(moveLines(source, { text: "" }, 3, 4).target, { text: "- d", marks: [{ line: 0, items: ["y"] }] });
});
