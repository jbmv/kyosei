// Run with `npm test`. The line under the composer: what the session is doing, Interrupt
// while it works, the running subagents, and "Waiting for you…" when it is live and idle.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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
  // What runs in the background is said on the same line, whole, under the composer (KYO-883).
  await page.run(`window.kyo.screen({ screen: { state: "working", background: { running: true, shells: 1, agents: [{ type: "w", description: "x" }] } } })`);
  const whole = await page.run<{ under: string; inChat: boolean }>(`({
    under: document.getElementById("working-text").textContent,
    inChat: Boolean(document.querySelector("#page #background")),
  })`);
  assert.match(whole.under, /^Working.*  ·  1 agent, 1 shell in the background$/);
  // One row, cut with an ellipsis, and the whole line in the tooltip.
  const row = await page.run<{ title: string; wrap: string; overflow: string }>(`(() => {
    const text = document.getElementById("working-text");
    const style = getComputedStyle(text);
    return { title: text.title, wrap: style.whiteSpace, overflow: style.textOverflow };
  })()`);
  assert.deepEqual(row, { title: whole.under, wrap: "nowrap", overflow: "ellipsis" });
  assert.equal(whole.inChat, false);
  // Idle with work still in the background says that work, and is not a wait on the person.
  await page.run(`window.kyo.screen({ screen: { state: "idle", background: { running: true, shells: 1, agents: [] } } })`);
  now = await page.run<Status>(status);
  assert.equal(now.text, "1 shell in the background");
  assert.equal(now.idle, false);
  // Idle and live: it waits for the person, in italics, with no Interrupt.
  await page.run(`window.kyo.screen({ screen: { state: "idle" } })`);
  now = await page.run<Status>(status);
  assert.deepEqual([now.shown, now.idle, now.text, now.interrupt], [true, true, "Waiting for you…", false]);
  const style = await page.run<string>(`getComputedStyle(document.getElementById("working")).fontStyle`);
  assert.equal(style, "italic");
  assert.equal(await page.run<string>(`getComputedStyle(document.getElementById("working")).color`), "rgb(255, 59, 48)");
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

test("only Waiting for you is red at rest: Delete and Interrupt are grey until the pointer is on them (KYO-883)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  await page.run(`(() => { window.kyo.project({ project: "p", scope: "server/p" }); ${session(true)};
    window.kyo.screen({ screen: { state: "working" } });
    window.kyo.draft({ boxes: [{ id: "a", text: "words" }], etag: "e" }); })()`);
  const colours = await page.run<{ interrupt: string; remove: string; muted: string }>(`(() => {
    const probe = document.createElement("span");
    probe.style.color = "var(--muted)";
    document.body.append(probe);
    const out = {
      interrupt: getComputedStyle(document.getElementById("interrupt")).color,
      remove: getComputedStyle(document.querySelector("#boxes .box-delete")).color,
      muted: getComputedStyle(probe).color,
    };
    probe.remove();
    return out;
  })()`);
  assert.deepEqual([colours.interrupt, colours.remove], [colours.muted, colours.muted]);
  assert.notEqual(colours.muted, "rgb(255, 59, 48)");
  // The red on hover is in the stylesheet, read from disk: a headless page cannot hover.
  const css = readFileSync(new URL("../static/chat.css", import.meta.url), "utf8");
  for (const selector of ["#interrupt:hover", ".box-delete:hover"]) {
    const rule = css.split("\n").find((line) => line.startsWith(`${selector} {`));
    assert.match(rule ?? "", /color: var\(--apple-red\)/, selector);
  }
});

test("the session coming to wait on the person asks the app to bounce its Dock icon, and opening on a wait does not (KYO-883)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  const asked = () => page!.run<number>(`window.posted.filter((p) => p.name === "attention" && p.body.waiting).length`);
  const ended = () => page!.run<number>(`window.posted.filter((p) => p.name === "attention" && !p.body.waiting).length`);
  await page.run(`(() => { window.kyo.project({ project: "p", scope: "server/p" }); ${session(true)};
    window.kyo.screen({ screen: { state: "idle" } }); })()`);
  assert.equal(await asked(), 0);
  await page.run(`window.kyo.screen({ screen: { state: "working" } })`);
  assert.equal(await ended(), 1);
  await page.run(`window.kyo.screen({ screen: { state: "idle" } })`);
  assert.equal(await asked(), 1);
  // Work begun again ends the bounce the app keeps up until it is in front.
  await page.run(`window.kyo.screen({ screen: { state: "working" } })`);
  assert.equal(await ended(), 2);
  await page.run(`window.kyo.screen({ screen: { state: "idle" } })`);
  assert.equal(await asked(), 2);
  // A second idle frame, or the clock's redraw, is no new wait.
  await page.run(`window.kyo.screen({ screen: { state: "idle" } })`);
  await new Promise((done) => setTimeout(done, 1100));
  assert.equal(await asked(), 2);
  // Idle over background work is not a wait; its end is.
  await page.run(`window.kyo.screen({ screen: { state: "idle", background: { running: true, shells: 1, agents: [] } } })`);
  await page.run(`window.kyo.screen({ screen: { state: "idle" } })`);
  assert.equal(await asked(), 3);
});
