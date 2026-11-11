// Run with `npm test`. The terminal belongs to the chat: Records, Files and History do not show
// its button (2026-11-11). A machine with no Chrome skips this.
import assert from "node:assert/strict";
import { test } from "node:test";
import { openPage } from "./chrome.ts";
test("the terminal button shows in Chat and in no other view", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  await page.reload();
  const shown = await page.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    const vis = () => getComputedStyle(document.getElementById("terminal-toggle")).display !== "none";
    const go = (name) => [...document.querySelectorAll(".view-item")].find((i) => i.textContent === name).click();
    const out = [vis()];
    for (const v of ["Records", "Files", "History", "Chat"]) { go(v); out.push(vis()); }
    return out;
  })()`);
  assert.deepEqual(shown, [true, false, false, false, true]);
  await page.close();
});
