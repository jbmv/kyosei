// Run with `npm test`. A question the session asks with AskUserQuestion is drawn in the
// conversation from the call's input in the transcript, marked as waiting while the terminal
// shows it, with a button that opens the terminal where it is answered (KYO-922).
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const session = `window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
  { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
] } })`;

// The input as Claude Code records it (measured on a transcript of this project, 2026-08-30).
const input = {
  questions: [
    {
      question: "Which build should go first?", header: "Order", multiSelect: false,
      options: [
        { label: "The page", description: "It holds the drawing, and the app only hands it frames." },
        { label: "The app", description: "It holds the Dock request." },
      ],
    },
    {
      question: "Ship today?", header: "Timing", multiSelect: false,
      options: [{ label: "Yes", description: "" }, { label: "No", description: "Wait for the look." }],
    },
  ],
};
const call = (result: string | null) => `window.kyo.apply({ tool_calls: [{
  call_id: "q1", uuid: "u1", at: "2026-11-13T10:00:00.000Z", name: "AskUserQuestion",
  input: ${JSON.stringify(input)}, result: ${result === null ? "null" : `{ text: ${JSON.stringify(result)}, is_error: false }`},
}] })`;

interface Card {
  count: number; tool: boolean; waiting: boolean; state: string; button: boolean;
  items: { header: string; text: string; options: { label: string; description: string; chosen: boolean }[] }[];
}
const card = `(() => {
  const cards = document.querySelectorAll("#chat .question");
  const element = cards[0];
  if (!element) return { count: 0 };
  return {
    count: cards.length,
    tool: Boolean(document.querySelector("#chat .tool")),
    waiting: element.classList.contains("waiting"),
    state: element.querySelector(".question-state").hidden ? "" : element.querySelector(".question-state").textContent,
    button: Boolean(element.querySelector(".question-open")),
    items: [...element.querySelectorAll(".question-item")].map((item) => ({
      header: item.querySelector(".question-header")?.textContent ?? "",
      text: item.querySelector(".question-text").textContent,
      options: [...item.querySelectorAll(".question-option")].map((row) => ({
        label: row.querySelector(".question-label").textContent,
        description: row.querySelector(".question-description")?.textContent ?? "",
        chosen: row.classList.contains("chosen"),
      })),
    })),
  };
})()`;

test("a question in the terminal is shown in the chat with its headers, options and descriptions, marked as waiting, with a button to the terminal", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  await page.run(`(() => { window.kyo.project({ project: "p", scope: "server/p" }); ${session};
    window.kyo.screen({ screen: { state: "working" } }); })()`);
  // The call reaches the transcript before the terminal shows it: drawn, not yet waiting.
  await page.run(call(null));
  let now = await page.run<Card>(card);
  assert.equal(now.count, 1);
  assert.equal(now.tool, false, "a card, not a row of a tool run");
  assert.equal(now.waiting, false);
  assert.deepEqual(now.items, [
    { header: "Order", text: "Which build should go first?", options: [
      { label: "The page", description: "It holds the drawing, and the app only hands it frames.", chosen: false },
      { label: "The app", description: "It holds the Dock request.", chosen: false },
    ] },
    { header: "Timing", text: "Ship today?", options: [
      { label: "Yes", description: "", chosen: false },
      { label: "No", description: "Wait for the look.", chosen: false },
    ] },
  ]);
  await page.run(`window.kyo.screen({ screen: { state: "ask-user-question" } })`);
  now = await page.run<Card>(card);
  assert.deepEqual([now.waiting, now.button], [true, true]);
  assert.match(now.state, /^Waiting for your answer in the terminal/);
  // The button opens the terminal, from another view too, and a second press keeps it open.
  await page.run(`document.querySelector('#view-items [data-view-item="records"]').click()`);
  await page.run(`document.querySelector("#chat .question-open").click()`);
  const opened = () => page!.run<{ open: boolean; chat: boolean; posts: number }>(`({
    open: document.body.classList.contains("terminal-open"),
    chat: document.body.classList.contains("view-chat"),
    posts: window.posted.filter((p) => p.name === "terminal" && p.body.action === "open").length,
  })`);
  assert.deepEqual(await opened(), { open: true, chat: true, posts: 1 });
  await page.run(`document.querySelector("#chat .question-open").click()`);
  assert.deepEqual(await opened(), { open: true, chat: true, posts: 1 });
  // Answered: the session works again, and the card names the options chosen.
  await page.run(`window.kyo.screen({ screen: { state: "working" } })`);
  await page.run(call(`Your questions have been answered: "Which build should go first?"="The page", "Ship today?"="No". You can now continue with these answers in mind.`));
  now = await page.run<Card>(card);
  assert.deepEqual([now.count, now.waiting, now.button, now.state], [1, false, false, "Answered"]);
  assert.deepEqual(now.items.map((item) => item.options.filter((o) => o.chosen).map((o) => o.label)), [["The page"], ["No"]]);
});

test("an unanswered question of an ended session is not marked as waiting", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  await page.run(`(() => { window.kyo.project({ project: "p", scope: "server/p" }); ${session};
    window.kyo.screen({ screen: { state: "ask-user-question" } }); })()`);
  await page.run(call(null));
  assert.equal((await page.run<Card>(card)).waiting, true);
  await page.run(`window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
    { session_id: "S1", title: "t", live: false, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null } ] } })`);
  const now = await page.run<Card>(card);
  assert.deepEqual([now.waiting, now.button, now.state], [false, false, ""]);
});
