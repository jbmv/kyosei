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

// KYO-969: with no live session in the project the chat draws no turn, and the start panel
// stands alone near the centre of the view. These need a page; a machine with no Chrome skips them.
import { openPage } from "./chrome.ts";

const SESSIONS = (live: boolean) => `{ sessions: [
  { session_id: "S1", title: "t", live: ${live}, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null } ] }`;
const OPEN = (live: boolean) => `(() => {
  window.kyo.project({ project: "p", scope: "server/p" });
  window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: ${SESSIONS(live)} });
  window.kyo.apply({ reset: true,
    turns: [{ id: "u1", at: "2026-01-01T00:00:00Z", text: "A prompt of several words." }],
    replies: [{ message_id: "m1", uuid: "x1", at: "2026-01-01T00:00:01Z", text: "A reply." }] });
})()`;
const SEEN = `(() => {
  const place = (id) => { const r = document.getElementById(id).getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; };
  const page = place("page"), start = place("start"), text = place("start-text"), button = place("start-button");
  const shown = (id) => document.getElementById(id).getBoundingClientRect().height > 0;
  return { turns: document.querySelectorAll("#chat .turn, #chat .reply").length, chatText: document.getElementById("chat").textContent,
    startShown: shown("start"), text: document.getElementById("start-text").textContent,
    button: document.getElementById("start-button").textContent, buttonEnabled: !document.getElementById("start-button").disabled,
    across: (start.left + start.right) / 2 / ((page.left + page.right) / 2), down: (start.top + start.bottom) / 2 / ((page.top + page.bottom) / 2),
    buttonUnder: button.top >= text.bottom - 1, jump: getComputedStyle(document.getElementById("jump-bottom")).display };
})()`;

test("with no live session the chat holds no turn and the start panel stands near the centre with the button under its words (KYO-969.c1)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(OPEN(false));
    const seen = await page.run<any>(SEEN);
    assert.equal(seen.turns, 0);
    assert.equal(seen.chatText, "");
    assert.equal(seen.startShown, true);
    assert.equal(seen.text, "This project has no live session.");
    assert.equal(seen.button, "Start orchestrator");
    assert.equal(seen.buttonUnder, true);
    assert.ok(seen.across > 0.9 && seen.across < 1.1, JSON.stringify(seen));
    assert.ok(seen.down > 0.55 && seen.down < 1, JSON.stringify(seen));
  } finally { await page.close(); }
});

test("the start's later states show in the same place (KYO-969.c2)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(OPEN(false));
    const first = await page.run<any>(SEEN);
    const frames: [string, string][] = [
      ["starting", `{ phase: "starting" }`],
      ["trust", `{ phase: "answered", status: 200, reply: { state: "waiting-for-trust", session: "KYO", trust_folder: "/work/p", attach: "tmux attach -t KYO" } }`],
      ["refusal", `{ phase: "answered", status: 403, reply: { ok: false, error: "This device may not start sessions." } }`],
    ];
    for (const [name, frame] of frames) {
      await page.run(`window.kyo.start(${frame})`);
      const seen = await page.run<any>(SEEN);
      assert.equal(seen.turns, 0, name);
      assert.equal(seen.startShown, true, name);
      assert.notEqual(seen.text, first.text, name);
      assert.ok(Math.abs(seen.across - first.across) < 0.05, `${name} ${JSON.stringify(seen)}`);
      assert.ok(seen.down > 0.3 && seen.down < 1, `${name} ${JSON.stringify(seen)}`);
    }
  } finally { await page.close(); }
});

test("when a session becomes live the panel goes and the chat draws; the ended text is in History (KYO-969.c3)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(OPEN(false));
    assert.equal((await page.run<any>(SEEN)).turns, 0);
    // The same session runs again: what was received is drawn, with the panel gone.
    await page.run(`window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: ${SESSIONS(true)} })`);
    const live = await page.run<any>(SEEN);
    assert.equal(live.startShown, false);
    assert.equal(live.turns, 2);
    // A session that ends goes the other way, and a later batch is not drawn while none is live.
    await page.run(`window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: ${SESSIONS(false)} })`);
    await page.run(`window.kyo.apply({ turns: [{ id: "u2", at: "2026-01-01T00:00:05Z", text: "later" }] })`);
    const ended = await page.run<any>(SEEN);
    assert.equal(ended.turns, 0);
    await page.run(`window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: ${SESSIONS(true)} })`);
    assert.deepEqual(await page.run(`[...document.querySelectorAll("#chat .turn")].map((e) => e.textContent)`), ["A prompt of several words.", "later"]);
  } finally { await page.close(); }
});
