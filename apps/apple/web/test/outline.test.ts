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
