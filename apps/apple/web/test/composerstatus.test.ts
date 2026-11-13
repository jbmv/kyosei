// Run with `npm test`. The line under the composer: what the session is doing, Interrupt
// while it works, "Waiting for you…" when it is live and idle, and the subagent list a press
// on its words opens (KYO-921). Also the Chat item of the bar, amber while the session waits
// on the person (KYO-922).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

interface Status { inComposer: boolean; inChat: boolean; text: string; shown: boolean; idle: boolean; interrupt: boolean; count: boolean }

const status = `(() => {
  const working = document.getElementById("working");
  return {
    inComposer: ["working", "interrupt", "running-agents-list"].every((id) => document.getElementById(id).closest("#composer")),
    inChat: Boolean(document.querySelector("#page #working, #composer-top #interrupt")),
    text: document.getElementById("working-text").textContent,
    shown: !working.hidden,
    idle: working.classList.contains("idle"),
    interrupt: !document.getElementById("interrupt").hidden,
    count: Boolean(document.getElementById("running-agents-count")) || /Subagents:/.test(document.getElementById("composer").textContent),
  };
})()`;

const session = (live: boolean) => `window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
  { session_id: "S1", title: "t", live: ${live}, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
] } })`;

test("the working line and Interrupt stand under the composer, with no subagent count beside them, and an idle live session waits for the person", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  await page.run(`(() => { window.kyo.project({ project: "p", scope: "server/p" }); ${session(true)}; })()`);
  // Before the server has read a pane, nothing is said.
  let now = await page.run<Status>(status);
  assert.deepEqual([now.inComposer, now.inChat, now.shown, now.interrupt, now.count],
    [true, false, false, false, false]);
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
  // KYO-921: the running subagents are named there and nowhere else under the composer.
  assert.equal((await page.run<Status>(status)).count, false);
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
  // The arrow is a marker in the box's gutter (KYO-910), placed by the editor on its line.
  const arrow = `(() => {
    const found = document.querySelector("#boxes .mark-landing");
    return found ? { top: found.getBoundingClientRect().top, count: document.querySelectorAll("#boxes .mark-landing").length } : null;
  })()`;
  // Three paragraphs, the caret left in the second, and the box then left for the chat.
  const tops = await page.run<{ focused: unknown }>(`(() => {
    window.kyo.draft({ boxes: [{ id: "a", text: "one\\ntwo\\nthree", scratch: { heading: "" } }], etag: "e" });
    window.kyo.composer.select(0, 5);
    return { focused: ${arrow} };
  })()`);
  assert.equal(tops.focused, null, "no arrow while the box has the caret");
  await page.run(`document.activeElement.blur()`);
  await new Promise((done) => setTimeout(done, 50));
  const shown = await page.run<{ top: number; count: number } | null>(arrow);
  assert.ok(shown, "an arrow once the caret is gone");
  assert.equal(shown.count, 1);
  // The caret moved to the first paragraph puts the arrow on a higher line.
  const first = await page.run<{ top: number } | null>(`(() => {
    window.kyo.composer.select(0, 0);
    document.activeElement.blur();
    return new Promise((done) => setTimeout(() => done(${arrow}), 50));
  })()`);
  assert.ok(first);
  assert.ok(first.top < shown.top, `${first.top} above ${shown.top}`);
  await page.run(`window.kyo.composer.select(0, 0)`);
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

test("a question or a permission prompt in the terminal is a wait on the person: the line is red and the Dock is asked to bounce (KYO-922)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  for (const [state, words] of [
    ["ask-user-question", "Waiting for your answer in the terminal"],
    ["permission-prompt", "Waiting for permission in the terminal"],
  ]) {
    await page.reload();
    await page.run(`(() => { window.kyo.project({ project: "p", scope: "server/p" }); ${session(true)};
      window.kyo.screen({ screen: { state: "working" } }); })()`);
    await page.run(`window.kyo.screen({ screen: { state: "${state}" } })`);
    const now = await page.run<Status>(status);
    assert.deepEqual([now.shown, now.idle, now.text, now.interrupt], [true, true, words, false], state);
    assert.equal(await page.run<string>(`getComputedStyle(document.getElementById("working")).color`), "rgb(255, 59, 48)");
    const attention = () => page!.run<boolean[]>(`window.posted.filter((p) => p.name === "attention").map((p) => p.body.waiting)`);
    assert.deepEqual(await attention(), [true], state);
    // Answered, the session works again and the bounce ends; the turn's end is no second wait
    // before it.
    await page.run(`window.kyo.screen({ screen: { state: "working" } })`);
    assert.deepEqual(await attention(), [true, false], state);
    // A question that follows the wait for the person at the end of a turn is the same wait.
    await page.run(`window.kyo.screen({ screen: { state: "idle" } })`);
    await page.run(`window.kyo.screen({ screen: { state: "${state}" } })`);
    assert.deepEqual(await attention(), [true, false, true], state);
  }
});

interface ChatItem { waiting: boolean; colour: string; amber: string; current: boolean }
const chatItem = `(() => {
  const item = document.querySelector('#view-items [data-view-item="chat"]');
  const probe = document.createElement("span");
  probe.style.color = "var(--amber)";
  document.body.append(probe);
  const amber = getComputedStyle(probe).color;
  probe.remove();
  return { waiting: item.classList.contains("waiting"), colour: getComputedStyle(item).color, amber,
    current: item.getAttribute("aria-current") === "page" };
})()`;

test("the Chat item of the bar is amber while the live session waits on the person and nothing runs, in every view (KYO-922)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  await page.run(`(() => { window.kyo.project({ project: "p", scope: "server/p" }); ${session(true)}; })()`);
  // Before a pane is read, and while the session works, it is as it was.
  let item = await page.run<ChatItem>(chatItem);
  assert.equal(item.waiting, false);
  await page.run(`window.kyo.screen({ screen: { state: "working" } })`);
  item = await page.run<ChatItem>(chatItem);
  assert.equal(item.waiting, false);
  assert.notEqual(item.colour, item.amber);
  // Waiting with Chat shown: amber, though it is the current view.
  await page.run(`window.kyo.screen({ screen: { state: "idle" } })`);
  item = await page.run<ChatItem>(chatItem);
  assert.deepEqual([item.waiting, item.current, item.colour], [true, true, item.amber]);
  // From another view too, and the bar drawn again keeps it.
  await page.run(`document.querySelector('#view-items [data-view-item="records"]').click()`);
  item = await page.run<ChatItem>(chatItem);
  assert.deepEqual([item.waiting, item.current, item.colour], [true, false, item.amber]);
  // A question in the terminal is a wait on the person.
  await page.run(`window.kyo.screen({ screen: { state: "ask-user-question" } })`);
  assert.equal((await page.run<ChatItem>(chatItem)).waiting, true);
  // A subagent or a background shell still running is not everything waiting on the person.
  for (const background of [`{ running: true, shells: 1, agents: [] }`, `{ running: true, shells: 0, agents: [{ type: "w", description: "x" }] }`]) {
    for (const state of ["idle", "ask-user-question"]) {
      await page.run(`window.kyo.screen({ screen: { state: "${state}", background: ${background} } })`);
      assert.equal((await page.run<ChatItem>(chatItem)).waiting, false, `${state} ${background}`);
    }
  }
  await page.run(`window.kyo.screen({ screen: { state: "idle" } })`);
  assert.equal((await page.run<ChatItem>(chatItem)).waiting, true);
  // An ended session waits on no one.
  await page.run(`(() => { ${session(false)}; })()`);
  assert.equal((await page.run<ChatItem>(chatItem)).waiting, false);
});

const AGENT_AT = "2026-11-12T10:00:00.000Z";
/** A running subagent started `ago` seconds before now, and a finished one that took 95s. */
const agents = (ago: number) => `window.kyo.apply({
  tool_calls: [
    { call_id: "c1", uuid: "u1", at: new Date(Date.now() - ${ago} * 1000).toISOString(), name: "Agent", input: {}, result: null },
    { call_id: "c2", uuid: "u2", at: "${AGENT_AT}", name: "Agent", input: {}, result: null },
  ],
  subagents: [
    { agent_id: "a1", description: "Still working", agent_type: "kyosei-worker", tool_call_uuid: "u1" },
    { agent_id: "a2", description: "Already done", agent_type: "kyosei-worker", tool_call_uuid: "u2" },
  ],
  deliveries: [{ uuid: "d1", at: "${AGENT_AT}", kind: "task", text: "done", sender: null, sender_id: null,
    handback: false, task_id: "a2", call_id: "c2", status: "completed", summary: null,
    usage: { total_tokens: 12000, duration_ms: 95000 } }],
})`;

interface Listed { hidden: boolean; rows: { name: string; facts: string; running: boolean }[]; rule: number; pointer: string }
const listed = `(() => {
  const list = document.getElementById("running-agents-list");
  return {
    hidden: list.hidden,
    rows: [...list.querySelectorAll(".running-agent")].map((row) => ({
      name: row.querySelector(".running-agent-name").textContent,
      facts: row.querySelector(".running-agent-facts").textContent,
      running: row.classList.contains("running"),
    })),
    rule: list.querySelectorAll("hr").length,
    pointer: getComputedStyle(document.getElementById("working-text")).cursor,
  };
})()`;

test("a press on the line's words opens the subagent list, running then finished, and a press elsewhere closes it (KYO-921)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  await page.run(`(() => { window.kyo.project({ project: "p", scope: "server/p" }); ${session(true)};
    window.kyo.screen({ screen: { state: "idle" } }); })()`);
  // With no subagent, the words open nothing and do not look pressable.
  await page.run(`document.getElementById("working-text").click()`);
  let now = await page.run<Listed>(listed);
  assert.deepEqual([now.hidden, now.pointer], [true, "auto"]);
  await page.run(agents(65));
  // Each of the line's states opens the list: the wait on the person, work, and background.
  const screens = [
    `{ state: "idle" }`,
    `{ state: "working" }`,
    `{ state: "idle", background: { running: true, shells: 0, agents: [{ type: "w", description: "x" }] } }`,
  ];
  for (const screen of screens) {
    await page.run(`window.kyo.screen({ screen: ${screen} })`);
    await page.run(`document.getElementById("working-text").click()`);
    now = await page.run<Listed>(listed);
    assert.equal(now.hidden, false, screen);
    assert.equal(now.pointer, "pointer");
    assert.deepEqual(now.rows.map((row) => [row.name, row.running]), [["Still working", true], ["Already done", false]]);
    assert.equal(now.rule, 1);
    // A second press on the words closes it.
    await page.run(`document.getElementById("working-text").click()`);
    assert.equal((await page.run<Listed>(listed)).hidden, true, screen);
  }
  // Open, a press inside the list keeps it, and a press anywhere else closes it.
  await page.run(`document.getElementById("working-text").click()`);
  const press = (selector: string) => page!.run(`document.querySelector("${selector}").dispatchEvent(new MouseEvent("mousedown", { bubbles: true }))`);
  await press("#running-agents-list .running-agent");
  assert.equal((await page.run<Listed>(listed)).hidden, false);
  await press("#working-text");
  assert.equal((await page.run<Listed>(listed)).hidden, false);
  await press("#chat");
  assert.equal((await page.run<Listed>(listed)).hidden, true);
  // A session whose subagents have all finished still lists them.
  await page.run(`window.kyo.apply({ deliveries: [{ uuid: "d2", at: "${AGENT_AT}", kind: "task", text: "done",
    sender: null, sender_id: null, handback: false, task_id: "a1", call_id: "c1", status: "completed", summary: null }] })`);
  await page.run(`document.getElementById("working-text").click()`);
  now = await page.run<Listed>(listed);
  assert.equal(now.hidden, false);
  assert.deepEqual(now.rows.map((row) => row.running), [false, false]);
  assert.equal(now.rule, 0);
});

test("a running subagent's row says how long it has run, counting up while the list is open, beside a finished row's time (KYO-921)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  await page.run(`(() => { window.kyo.project({ project: "p", scope: "server/p" }); ${session(true)};
    window.kyo.screen({ screen: { state: "working" } }); })()`);
  await page.run(agents(65));
  await page.run(`document.getElementById("working-text").click()`);
  const first = await page.run<Listed>(listed);
  assert.match(first.rows[0].facts, /^kyosei-worker  ·  1m 0?[56]s$/);
  // The finished row keeps the time it took, as before.
  assert.equal(first.rows[1].facts, "kyosei-worker  ·  12k tokens  ·  1m 35s");
  const seconds = (facts: string) => {
    const [, minutes, rest] = facts.match(/(\d+)m (\d+)s$/)!;
    return Number(minutes) * 60 + Number(rest);
  };
  // The clock ticks each second, so what is shown may lag the true time by up to a second.
  await new Promise((done) => setTimeout(done, 2200));
  const later = await page.run<Listed>(listed);
  assert.equal(later.hidden, false);
  assert.ok(seconds(later.rows[0].facts) >= seconds(first.rows[0].facts) + 1,
    `${later.rows[0].facts} after ${first.rows[0].facts}`);
});
