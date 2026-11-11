// Run with `npm test`. The empty chat's line (KYO-752) is drawn only for a live session whose turns have loaded.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const chat = readFileSync(new URL("../src/chat.ts", import.meta.url), "utf8");
const html = readFileSync(new URL("../static/chat.html", import.meta.url), "utf8");

test("the line waits for loaded turns, a live session and an empty chat", () => {
  const body = chat.match(/function drawEmptyChat\(\)[\s\S]*?\n}/)![0];
  for (const gate of ["turnsLoaded", "!sessionEnded", "menu?.current", "elements.size === 0"]) {
    assert.ok(body.includes(gate), gate);
  }
  assert.match(html, /id="empty-chat" hidden>Session active&hellip; waiting for first input/);
});
