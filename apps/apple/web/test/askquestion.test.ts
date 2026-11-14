// Run with `npm test`. A question the session asks with AskUserQuestion is drawn in the
// conversation from the call's input, marked as waiting while the terminal shows it, with a
// button that opens the terminal where it is answered (KYO-922). Claude Code writes the call
// to the transcript only when it resolves, together with its result, so while the question is
// open the call reaches the page as a pending call, from the session's hook (KYO-940); the
// transcript's record then takes the same card. The answer is a turn of the person's, drawn
// as choices from the question and not as a prompt they typed.
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

// ------------------------------------------------------------------------------ KYO-940

/** The call as `kyo serve` sends it while the question is open: no record yet, so no uuid. */
const pending = { call_id: "q1", uuid: null, at: "2026-11-13T10:00:00.400Z", name: "AskUserQuestion",
  input, result: null, pending: true };
/** The transcript's record of the same call, which Claude Code writes with its result. */
const recorded = (text: string, is_error = false) => ({ call_id: "q1", uuid: "u1", at: "2026-11-13T10:00:00.000Z",
  name: "AskUserQuestion", input, result: { text, is_error } });
const ANSWERED = `The user answered: "Which build should go first?"="The page", "Ship today?"="Not before the look". Read the answers carefully.`;
/** The person's turn the same record yields, as the server divides its answers. */
const answerTurn = { id: "a1", at: "2026-11-13T10:01:00.000Z", form: "answer", text: "The page\nNot before the look",
  answers: [
    { question: "Which build should go first?", header: "Order", multi_select: false, parts: [{ text: "The page", picked: true }] },
    { question: "Ship today?", header: "Timing", multi_select: false, parts: [{ text: "Not before the look", picked: false }] },
  ] };

async function openWaiting(): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => { window.kyo.project({ project: "p", scope: "server/p" }); ${session};
    window.kyo.apply({ reset: true, turns: [{ id: "t1", at: "2026-11-13T09:59:00.000Z", text: "Ask me." }], replies: [] });
    window.kyo.screen({ screen: { state: "ask-user-question" } }); })()`);
  return opened;
}

test("a question is drawn while it is open, before the transcript holds its call, and the transcript's record with the answer takes the same card (KYO-940)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await openWaiting();
  // The dialog is open and the transcript holds nothing of the call.
  assert.equal((await opened.run<Card>(card)).count, 0);
  await opened.run(`window.kyo.apply(${JSON.stringify({ pending_tool_calls: [pending] })})`);
  let now = await opened.run<Card>(card);
  assert.deepEqual([now.count, now.tool, now.waiting, now.button], [1, false, true, true]);
  assert.match(now.state, /^Waiting for your answer in the terminal/);
  assert.deepEqual(now.items.map((item) => [item.header, item.text, item.options.map((o) => o.label)]), [
    ["Order", "Which build should go first?", ["The page", "The app"]],
    ["Timing", "Ship today?", ["Yes", "No"]],
  ]);
  assert.equal(now.items[0].options[0].description, "It holds the drawing, and the app only hands it frames.");
  // Said again, as each frame that states them does: still one card.
  await opened.run(`window.kyo.apply(${JSON.stringify({ pending_tool_calls: [pending] })})`);
  assert.equal((await opened.run<Card>(card)).count, 1);
  // Answered. The call and its result reach the transcript together, and the call is pending no more.
  await opened.run(`window.kyo.screen({ screen: { state: "working" } })`);
  await opened.run(`window.kyo.apply(${JSON.stringify({ tool_calls: [recorded(ANSWERED)], turns: [answerTurn], pending_tool_calls: [] })})`);
  now = await opened.run<Card>(card);
  assert.deepEqual([now.count, now.waiting, now.button, now.state], [1, false, false, "Answered"]);
  assert.deepEqual(now.items.map((item) => item.options.filter((o) => o.chosen).map((o) => o.label)), [["The page"], []]);
  // The card stands where it was drawn, after the prompt and before the answer.
  const order = await opened.run<string[]>(`[...document.querySelectorAll("#chat [data-key]")].map((e) => e.dataset.key)`);
  assert.deepEqual(order, ["t:t1", "c:q1", "t:a1"]);
});

test("a question that was open when the stream began is drawn from the ready frame, and a dismissed one reads as not answered", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await openWaiting();
  await opened.run(`window.kyo.ready(${JSON.stringify({ screen: { state: "ask-user-question" }, pending_tool_calls: [pending] })})`);
  let now = await opened.run<Card>(card);
  assert.deepEqual([now.count, now.waiting, now.items.length], [1, true, 2]);
  // Escape: the transcript's record of the call carries an error as its result.
  await opened.run(`window.kyo.screen({ screen: { state: "idle" } })`);
  await opened.run(`window.kyo.apply(${JSON.stringify({ tool_calls: [recorded("The user doesn't want to proceed with this tool use.", true)], pending_tool_calls: [] })})`);
  now = await opened.run<Card>(card);
  assert.deepEqual([now.count, now.waiting, now.state], [1, false, "Not answered"]);
});

test("the transcript's record of an open call, arriving while it is pending, keeps the one card, and a pending call that is withdrawn unrecorded is taken away", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await openWaiting();
  await opened.run(`window.kyo.apply(${JSON.stringify({ pending_tool_calls: [pending] })})`);
  // With the hook installed Claude Code also writes the call at once, without a result.
  await opened.run(`window.kyo.apply(${JSON.stringify({ tool_calls: [{ ...recorded(""), result: null }], pending_tool_calls: [] })})`);
  let now = await opened.run<Card>(card);
  assert.deepEqual([now.count, now.waiting], [1, true], "the transcript's call stands when it is pending no more");
  // A later frame that lists the call as pending does not put it back over the transcript's.
  await opened.run(`window.kyo.apply(${JSON.stringify({ tool_calls: [recorded(ANSWERED)] })})`);
  await opened.run(`window.kyo.apply(${JSON.stringify({ pending_tool_calls: [pending] })})`);
  now = await opened.run<Card>(card);
  assert.deepEqual([now.count, now.state], [1, "Answered"]);

  const again = await openWaiting();
  await again.run(`window.kyo.apply(${JSON.stringify({ pending_tool_calls: [pending] })})`);
  assert.equal((await again.run<Card>(card)).count, 1);
  await again.run(`window.kyo.apply(${JSON.stringify({ pending_tool_calls: [] })})`);
  assert.equal((await again.run<Card>(card)).count, 0);
});

interface DrawnTurn {
  text: string; answer: boolean; questions: string[]; shown: string[];
  parts: { text: string; picked: boolean; tick: boolean; mark: string }[];
}
const turnsDrawn = `(() => [...document.querySelectorAll("#chat .turn")].map((turn) => ({
  text: turn.textContent,
  answer: turn.classList.contains("turn-answer"),
  questions: [...turn.querySelectorAll(".turn-answer-item")].map((item) => getComputedStyle(item, "::before").content),
  shown: [...turn.querySelectorAll(".turn-sep")].map((sep) => getComputedStyle(sep).display),
  parts: [...turn.querySelectorAll(".turn-choice, .turn-typed")].map((part) => ({
    text: part.textContent,
    picked: part.classList.contains("turn-choice"),
    tick: Boolean(part.querySelector("svg")),
    mark: getComputedStyle(part, "::after").content,
  })),
})))()`;

test("an answer picked from a question's options is drawn as a choice from that question, an answer typed is marked as typed, and a prompt is neither (KYO-940)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = page;
  await opened.reload();
  const multi = { id: "a2", at: "2026-11-13T10:02:00.000Z", form: "answer", text: "Apple, Plum, and a quince",
    answers: [{ question: "Which fruits?", header: "Fruits", multi_select: true, parts: [
      { text: "Apple", picked: true }, { text: "Plum", picked: true }, { text: "and a quince", picked: false }] }] };
  // A server that predates the field sends the answer's text alone, and one whose answers
  // are not the turn's text is not believed: both are drawn as the turn was before.
  const older = { id: "a3", at: "2026-11-13T10:03:00.000Z", form: "answer", text: "The page" };
  const unlike = { id: "a4", at: "2026-11-13T10:04:00.000Z", form: "answer", text: "The page",
    answers: [{ question: "Which?", parts: [{ text: "The app", picked: true }] }] };
  await opened.run(`(() => { window.kyo.project({ project: "p", scope: "server/p" }); ${session};
    window.kyo.apply(${JSON.stringify({ reset: true, replies: [], turns: [
      { id: "t1", at: "2026-11-13T09:59:00.000Z", text: "The page", answers: [] }, answerTurn, multi, older, unlike] })}); })()`);
  const [typed, answer, several, before, disbelieved] = await opened.run<DrawnTurn[]>(turnsDrawn);
  assert.deepEqual([typed.answer, typed.parts.length, typed.text], [false, 0, "The page"], "a prompt the person typed is a plain turn");
  assert.equal(answer.answer, true);
  assert.deepEqual(answer.questions, ['"Which build should go first?"', '"Ship today?"'], "each answer under its question");
  assert.deepEqual(answer.parts, [
    { text: "The page", picked: true, tick: true, mark: "none" },
    { text: "Not before the look", picked: false, tick: false, mark: '"typed"' },
  ]);
  assert.equal(answer.text, answerTurn.text, "the turn's text is the answers alone, which an anchor measures against");
  assert.deepEqual(answer.shown, ["none"], "the break between two answers is text of the turn that is not shown");
  assert.deepEqual(several.parts.map((part) => [part.text, part.picked]), [["Apple", true], ["Plum", true], ["and a quince", false]]);
  assert.equal(several.text, multi.text);
  for (const plain of [before, disbelieved]) assert.deepEqual([plain.answer, plain.parts.length, plain.text], [false, 0, "The page"]);
});
