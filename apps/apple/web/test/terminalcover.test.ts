// Run with `npm test`. The open terminal covers the whole chat pane (KYO-753).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const css = readFileSync(new URL("../static/chat.css", import.meta.url), "utf8");

test("the terminal stands where the chat pane stands and shrinks nothing beneath it", () => {
  const rule = css.match(/^#terminal \{[^}]*\}/m)![0];
  assert.match(rule, /top: calc\(46px \+ var\(--views-height\)\)/);
  assert.match(rule, /bottom: 0/);
  assert.doesNotMatch(rule, /height: 46%/);
  assert.doesNotMatch(css, /body\.terminal-open #page \{ bottom/);
});
