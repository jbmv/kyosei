// Run with `npm test`. The side panel's disclosure mark is one drawing turned about its own centre (KYO-751).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const chat = readFileSync(new URL("../src/chat.ts", import.meta.url), "utf8");
const css = readFileSync(new URL("../static/chat.css", import.meta.url), "utf8");

test("the open and closed marks are the same drawing, not two text glyphs", () => {
  assert.ok(!/finished \$\{[^}]*"⌄"/.test(chat), "no glyph swap beside a finished count");
  assert.match(chat, /drawFinished\("side-finished"/);
  assert.match(chat, /drawFinished\("side-agents-finished"/);
});

test("the open mark turns about the centre of a square box", () => {
  const rule = css.match(/^\.disclosure \{[^}]*\}/m)![0];
  assert.match(rule, /transform-origin: 50% 50%/);
  assert.match(rule, /width: 10px; height: 10px/);
  assert.match(css, /\.disclosure\.open \{ transform: rotate\(90deg\)/);
});

test("the chevron's path is centred on the box's centre", () => {
  const d = chat.match(/setAttribute\("d", "M([\d.]+) ([\d.]+) L([\d.]+) ([\d.]+) L([\d.]+) ([\d.]+)"\)/)!;
  const [x1, y1, x2, y2, x3, y3] = d.slice(1).map(Number);
  assert.equal((Math.min(x1, x2, x3) + Math.max(x1, x2, x3)) / 2, 5);
  assert.equal((Math.min(y1, y2, y3) + Math.max(y1, y2, y3)) / 2, 5);
});
