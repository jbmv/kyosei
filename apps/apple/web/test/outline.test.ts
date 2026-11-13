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

import { headingSections } from "../src/outline.ts";

test("each line is in the sections of the headings above it, outermost first, as sectionEnd gives them (KYO-917)", () => {
  const text = [
    "before",        // 0: before the first heading, in none
    "# one",         // 1
    "## inner",      // 2
    "- item",        // 3
    "  - nested",    // 4
    "",              // 5
    "test",          // 6: a plain line after a list is still in both sections
    "",              // 7: a blank line at the end of a section is left out of it
    "# two",         // 8
    "### deep",      // 9: a level skipped is still nested
    "words",         // 10
  ].join("\n");
  assert.deepEqual(headingSections(text), [
    [], [1], [1, 2], [1, 2], [1, 2], [1, 2], [1, 2], [], [8], [8, 9], [8, 9],
  ]);
  // Each heading's lines are exactly those sectionEnd gives it.
  headingSections(text).forEach((chain) => chain.forEach((heading) => assert.ok(heading < sectionEnd(text, heading))));
  for (const heading of [1, 2, 8, 9]) {
    const lines = headingSections(text).flatMap((chain, line) => (chain.includes(heading) ? [line] : []));
    assert.deepEqual(lines, Array.from({ length: sectionEnd(text, heading) - heading }, (_, at) => heading + at));
  }
  assert.deepEqual(headingSections("no headings\n- here"), [[], []]);
});

test("two blank lines in a row end every section above them, and the lines after are in none (KYO-923.c1)", () => {
  const text = [
    "# one",         // 0
    "## inner",      // 1
    "words",         // 2
    "",              // 3: one blank line is still inside the sections
    "more",          // 4
    "",              // 5: the first of two blank lines ends both sections
    "",              // 6
    "after",         // 7: in no section
    "- item",        // 8
    "# two",         // 9
    "last",          // 10
  ].join("\n");
  assert.equal(sectionEnd(text, 0), 5);
  assert.equal(sectionEnd(text, 1), 5);
  assert.equal(sectionEnd(text, 9), 11);
  assert.deepEqual(headingSections(text), [[0], [0, 1], [0, 1], [0, 1], [0, 1], [], [], [], [], [9], [9]]);
  // A line of spaces counts as blank, and a nested list does not run on past the break.
  assert.equal(sectionEnd("# h\na\n  \n\t\nb", 0), 2);
  assert.equal(sectionEnd("- a\n  - b\n\n\n  - c", 0), 2);
  // A heading with the break right under it holds only itself.
  assert.equal(sectionEnd("# h\n\n\nwords", 0), 1);
});

test("a line of three or more hyphens is a rule, which ends every section above it (KYO-923.c2)", () => {
  const rules = (text: string) => lineShapes(text).map((shape) => shape.rule);
  assert.deepEqual(rules("---\n-----\n   ---  \n--\n- - -\n    ---\n----a\n# ---\n- ---"),
    [true, true, true, false, false, false, false, false, false]);
  const text = "# one\n## inner\nwords\n\n---\nafter\n# two\n---";
  assert.equal(sectionEnd(text, 0), 3, "the blank line before the rule is left behind");
  assert.equal(sectionEnd(text, 1), 3);
  assert.equal(sectionEnd(text, 6), 7);
  assert.deepEqual(headingSections(text), [[0], [0, 1], [0, 1], [], [], [], [6], []]);
  // A list item stops at a rule too, and the rule line itself is a section of one line.
  assert.equal(sectionEnd("- a\n  - b\n  ---\n  - c", 0), 2);
  assert.equal(sectionEnd(text, 4), 5);
});
