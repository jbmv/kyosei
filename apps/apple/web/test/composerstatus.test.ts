// Run with `npm test`. The line under the composer: what the session is doing, Interrupt
// while it works, the running subagents, and "Waiting for you…" when it is live and idle.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

interface Status { inComposer: boolean; inChat: boolean; text: string; shown: boolean; idle: boolean; interrupt: boolean; agents: string; green: boolean }

const status = `(() => {
  const working = document.getElementById("working");
  const count = document.getElementById("running-agents-count");
  return {
    inComposer: ["working", "interrupt", "running-agents"].every((id) => document.getElementById(id).closest("#composer")),
    inChat: Boolean(document.querySelector("#page #working, #composer-top #interrupt")),
    text: document.getElementById("working-text").textContent,
    shown: !working.hidden,
    idle: working.classList.contains("idle"),
    interrupt: !document.getElementById("interrupt").hidden,
    agents: count.textContent,
    green: count.classList.contains("running"),
  };
})()`;

const session = (live: boolean) => `window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
  { session_id: "S1", title: "t", live: ${live}, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
] } })`;

test("the working line, Interrupt and the subagent count stand under the composer, and an idle live session waits for the person", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  await page.run(`(() => { window.kyo.project({ project: "p", scope: "server/p" }); ${session(true)}; })()`);
  // Before the server has read a pane, nothing is said.
  let now = await page.run<Status>(status);
  assert.deepEqual([now.inComposer, now.inChat, now.shown, now.interrupt, now.agents, now.green],
    [true, false, false, false, "Subagents: 0", false]);
  // Working: the words, the pulse and Interrupt.
  await page.run(`window.kyo.screen({ screen: { state: "working" } })`);
  now = await page.run<Status>(status);
  assert.equal(now.shown, true);
  assert.equal(now.idle, false);
  assert.match(now.text, /^Working/);
  assert.equal(now.interrupt, true);
  // Idle and live: it waits for the person, in italics, with no Interrupt.
  await page.run(`window.kyo.screen({ screen: { state: "idle" } })`);
  now = await page.run<Status>(status);
  assert.deepEqual([now.shown, now.idle, now.text, now.interrupt], [true, true, "Waiting for you…", false]);
  const style = await page.run<string>(`getComputedStyle(document.getElementById("working")).fontStyle`);
  assert.equal(style, "italic");
  // An ended session says nothing.
  await page.run(`(() => { ${session(false)}; })()`);
  now = await page.run<Status>(status);
  assert.equal(now.shown, false);
});

test("with no caret in the composer an arrow marks the paragraph the next anchor lands on, and typing takes it away", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  await page.run(`(() => { window.kyo.project({ project: "p", scope: "server/p" }); ${session(true)}; })()`);
  const arrow = `(() => {
    const found = document.querySelector("#boxes .mark-landing");
    return found ? { top: found.style.top, count: document.querySelectorAll("#boxes .mark-landing").length } : null;
  })()`;
  // Three paragraphs, the caret left in the second, and the box then left for the chat.
  const tops = await page.run<{ focused: unknown }>(`(() => {
    document.getElementById("new-prompt").click();
    const area = document.querySelector("#boxes textarea");
    area.focus();
    area.value = "one\\ntwo\\nthree";
    area.dispatchEvent(new Event("input", { bubbles: true }));
    area.setSelectionRange(5, 5);
    return { focused: ${arrow} };
  })()`);
  assert.equal(tops.focused, null, "no arrow while the box has the caret");
  await page.run(`document.querySelector("#boxes textarea").blur()`);
  await new Promise((done) => setTimeout(done, 50));
  const shown = await page.run<{ top: string; count: number } | null>(arrow);
  assert.ok(shown, "an arrow once the caret is gone");
  assert.equal(shown.count, 1);
  // The caret moved to the first paragraph puts the arrow on a higher line.
  const first = await page.run<{ top: string } | null>(`(() => {
    const area = document.querySelector("#boxes textarea");
    area.focus();
    area.setSelectionRange(0, 0);
    area.blur();
    return new Promise((done) => setTimeout(() => done(${arrow}), 50));
  })()`);
  assert.ok(first);
  assert.ok(parseFloat(first.top) < parseFloat(shown.top), `${first.top} above ${shown.top}`);
  await page.run(`document.querySelector("#boxes textarea").focus()`);
  await new Promise((done) => setTimeout(done, 50));
  assert.equal(await page.run(arrow), null, "it goes when the caret is back");
});
