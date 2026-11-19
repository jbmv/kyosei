// Run with `npm test`. Anchor mode (KYO-823): from a paragraph's + panel, the next press on a
// place in the chat anchors it to that paragraph, as a right-click on a word would, without
// the Anchor to menu. A hover shows what a press would take and dims what it could not, a
// press there does nothing, and Escape ends the mode. The History reader's passages are
// anchored as the chat's are, by right-click and in the mode, and name the session they are
// in (KYO-972). A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const FIRST = "First sentence of the reply. Second sentence of it.\n\nA second paragraph here.";
const wait = (ms: number) => new Promise((done) => setTimeout(done, ms));

/** A session whose chat has a reply and a tool call, a box typed in, and its + panel open. */
async function open(): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.apply({ reset: true,
      turns: [{ id: "u1", at: "2026-01-01T00:00:00Z", text: "A prompt." }],
      replies: [{ message_id: "m1", uuid: "x1", at: "2026-01-01T00:00:01Z", text: ${JSON.stringify(FIRST)} }],
      tool_calls: [{ call_id: "c1", uuid: "y1", at: "2026-01-01T00:00:02Z", name: "Bash",
        input: { command: "ls" }, result: { text: "a", is_error: false } }] });
    window.kyo.composer.select(0, 0);
  })()`);
  await opened.type("my paragraph");
  await wait(100);
  // The + beside a paragraph shows where the pointer is over the box's text, and opens its panel.
  await opened.run(`(() => {
    const line = document.querySelector("#boxes .cm-line").getBoundingClientRect();
    document.querySelector("#boxes .cm-content").dispatchEvent(new MouseEvent("mousemove",
      { bubbles: true, clientX: line.left + 10, clientY: line.top + line.height / 2 }));
    document.querySelector("#boxes .mark.ghost").click();
  })()`);
  return opened;
}

/** A pointer at the middle of the n-th word of an element, as `{x, y}`, found in the page. */
const pointAt = (selector: string, word: string) => `(() => {
  const holder = document.querySelector(${JSON.stringify(selector)});
  const walker = document.createTreeWalker(holder, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const at = node.data.indexOf(${JSON.stringify(word)});
    if (at < 0) continue;
    const range = document.createRange();
    range.setStart(node, at);
    range.setEnd(node, at + ${word.length});
    const box = range.getBoundingClientRect();
    return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
  }
  return null;
})()`;

const move = (opened: Page, point: { x: number; y: number }) => opened.run(`(() => {
  const target = document.elementFromPoint(${point.x}, ${point.y});
  target.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: ${point.x}, clientY: ${point.y} }));
})()`);

const press = (opened: Page, point: { x: number; y: number }) => opened.run(`(() => {
  const target = document.elementFromPoint(${point.x}, ${point.y});
  const at = { bubbles: true, cancelable: true, button: 0, clientX: ${point.x}, clientY: ${point.y} };
  target.dispatchEvent(new MouseEvent("mousedown", at));
  target.dispatchEvent(new MouseEvent("mouseup", at));
  target.dispatchEvent(new MouseEvent("click", at));
})()`);

const state = (opened: Page) => opened.run<{
  mode: boolean; preview: string[]; grey: boolean; dimmed: number; anchors: string[]; menu: boolean;
}>(`(() => ({
  mode: document.body.classList.contains("anchor-mode"),
  preview: [...(CSS.highlights.get("anchor-preview") ?? [])].map((range) => range.toString()),
  grey: document.body.classList.contains("anchor-off"),
  dimmed: [...document.querySelectorAll("*")].filter((e) => getComputedStyle(e).opacity !== "1").length,
  anchors: [...document.querySelectorAll("#boxes .mark.held")].flatMap((mark) => mark.title.split("\\n")),
  menu: !!document.getElementById("anchor-menu")?.isConnected,
}))()`);

test("the + panel's Anchor starts the mode; a hover shows the sentence and a press anchors it", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  const button = await opened.run<boolean>(`(() => {
    const button = document.querySelector(".mark-panel .mark-anchor-mode");
    // The paragraph's other actions stand between the button and the divider (KYO-911).
    const divider = Boolean(document.querySelector(".mark-panel .mark-anchor-mode ~ .mark-divider ~ input"));
    button?.click();
    return !!button && !!divider;
  })()`);
  assert.equal(button, true, "the panel has the Anchor button, divided from the tag field");
  assert.equal((await state(opened)).mode, true);

  const point = (await opened.run<{ x: number; y: number }>(pointAt("#chat .reply", "Second")))!;
  await move(opened, point);
  await wait(50);
  assert.deepEqual((await state(opened)).preview, ["Second sentence of it."], "the hover shows what a press takes");

  await press(opened, point);
  const after = await state(opened);
  assert.equal(after.mode, false, "a press that anchors ends the mode");
  assert.equal(after.menu, false, "no Anchor to menu is shown");
  assert.deepEqual(after.anchors, ["anchor: Second sentence of it."]);
});

test("over what cannot be anchored the pointer greys, nothing dims, and a press does nothing", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await opened.run(`document.querySelector(".mark-panel .mark-anchor-mode").click()`);
  const point = await opened.run<{ x: number; y: number }>(`(() => {
    const box = document.querySelector("#chat .group-head").getBoundingClientRect();
    return { x: box.left + 20, y: box.top + box.height / 2 };
  })()`);
  await move(opened, point);
  await wait(50);
  const hovered = await state(opened);
  assert.equal(hovered.grey, true, "the pointer is grey");
  assert.deepEqual(hovered.preview, []);
  // The space between a reply's two paragraphs is not a place either (it lit the whole reply).
  const gap = await opened.run<{ x: number; y: number }>(`(() => {
    const [one, two] = document.querySelectorAll("#chat .reply [data-source-line]");
    const a = one.getBoundingClientRect(), b = two.getBoundingClientRect();
    return { x: a.left + 5, y: (a.bottom + b.top) / 2 };
  })()`);
  await move(opened, gap);
  await wait(50);
  const between = await state(opened);
  assert.deepEqual([between.grey, between.preview], [true, []]);
  await press(opened, point);
  const after = await state(opened);
  assert.deepEqual([after.mode, after.anchors], [true, []], "the mode stays on and nothing is anchored");
});

test("a selection the person made is anchored as it is", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await opened.run(`document.querySelector(".mark-panel .mark-anchor-mode").click()`);
  const point = (await opened.run<{ x: number; y: number }>(pointAt("#chat .reply", "reply")))!;
  await opened.run(`(() => {
    const node = [...document.querySelector("#chat .reply").querySelectorAll("p")][0].firstChild;
    const at = node.data.indexOf("the reply");
    const range = document.createRange();
    range.setStart(node, at);
    range.setEnd(node, at + "the reply".length);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  })()`);
  await press(opened, point);
  assert.deepEqual((await state(opened)).anchors, ["anchor: the reply"]);
});

test("Escape ends the mode without anchoring", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await opened.run(`document.querySelector(".mark-panel .mark-anchor-mode").click()`);
  await opened.run(`document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))`);
  const after = await state(opened);
  assert.deepEqual([after.mode, after.anchors], [false, []]);
});

const RECORD = {
  id: "KYO-9", kind: "task", title: "A record to anchor", status: "open",
  description: "What the record is about.",
  criteria: [{ id: "KYO-9.c1", text: "A criterion of it", state: "unmet-unbuilt" }],
};

test("in Records a press on the record's metadata anchors the whole record", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  // The record is opened first, then the box's + panel and the mode.
  await opened.run(`(() => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    window.kyo.records({ project: "p" });
    const row = document.createElement("div");
    row.dataset.open = "KYO-9";
    document.body.append(row);
    row.click();
    row.remove();
    window.kyo.record({ id: "KYO-9", document: ${JSON.stringify(RECORD)} });
    // The box's + panel and the mode, with the record open.
    const line = document.querySelector("#boxes .cm-line").getBoundingClientRect();
    document.querySelector("#boxes .cm-content").dispatchEvent(new MouseEvent("mousemove",
      { bubbles: true, clientX: line.left + 10, clientY: line.top + line.height / 2 }));
    document.querySelector("#boxes .mark.ghost").click();
    document.querySelector(".mark-panel .mark-anchor-mode").click();
  })()`);
  const point = await opened.run<{ x: number; y: number } | null>(`(() => {
    const meta = [...document.querySelectorAll("#record *")].find((e) =>
      !e.closest("[data-record-ref]") && e.children.length === 0 && e.textContent.trim()
      && e.getBoundingClientRect().height > 0);
    if (!meta) return null;
    const box = meta.getBoundingClientRect();
    return { x: box.left + 2, y: box.top + box.height / 2 };
  })()`);
  assert.ok(point, "the record's page has metadata outside its parts");
  await press(opened, point!);
  assert.deepEqual((await state(opened)).anchors, ["anchor: KYO-9 A record to anchor"]);
});

// ---- the History reader (KYO-972): a past session's passage is anchored as the chat's are,
// and the anchor names that session.

const PAST = "A reply of the past session. It has a second sentence.";

/** The chat follows S1, a box is typed in, and History's reader holds S0, which has ended. */
async function openReader(): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "Fresh", live: true, first_seen: "2026-01-02T00:00:00Z", remote_control_url: null, prompts: 0 },
      { session_id: "S0", title: "Earlier", live: false, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null, prompts: 1 },
    ] } });
    window.kyo.apply({ reset: true, turns: [], replies: [] });
    window.kyo.composer.select(0, 0);
  })()`);
  await opened.type("my paragraph");
  await opened.run(`document.querySelector('#view-items [data-view-item="history"]').click()`);
  await readSession(opened, "S0");
  return opened;
}

/** Opens a session from History's side bar and answers the reader's request for it. */
const readSession = async (opened: Page, session: string) => {
  await opened.run(`document.querySelector('#history-sessions [data-session="${session}"]').click()`);
  await answerTurns(opened, session);
};

/** The reader's last request for a session, answered: S0 holds a prompt and a reply, S1 nothing. */
const answerTurns = (opened: Page, session: string) => opened.run(`(() => {
  const ask = window.posted.filter((p) => p.name === "history" && p.body.want === "turns").pop().body.ask;
  window.kyo.historyTurns({ ask, session: ${JSON.stringify(session)}, status: 200, document: ${JSON.stringify(
    session === "S0"
      ? { turns: [{ id: "u0", at: "2026-01-01T00:00:00Z", text: "An old prompt." }],
          replies: [{ message_id: "m0", uuid: "x0", at: "2026-01-01T00:00:01Z", text: PAST }] }
      : { turns: [], replies: [] })} });
})()`);

/** The anchor the composer's prompt would be sent with, as the Send posts it. */
const sentAnchors = async (opened: Page) => {
  await opened.run(`window.posted.length = 0; document.querySelector("#boxes .box.main .box-send").click()`);
  return opened.run<Record<string, unknown>[]>(
    `window.posted.filter((m) => m.name === "send").at(-1).body.marks.flatMap((mark) => mark.anchors)`);
};

test("in the History reader a right-click offers Anchor and Copy, and Anchor names the past session (KYO-972.c1, c2)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await openReader();
  const seen = await opened.run<{ entries: string[]; copies: string[] }>(`(() => {
    const target = document.querySelector("#history-reader .reply p");
    const box = target.getBoundingClientRect();
    const at = { bubbles: true, cancelable: true, clientX: box.left + 30, clientY: box.top + box.height / 2 };
    target.dispatchEvent(new MouseEvent("mousedown", { ...at, button: 2 }));
    target.dispatchEvent(new MouseEvent("contextmenu", at));
    const entries = [...document.querySelectorAll("#anchor-menu > *")].map((e) => e.classList.contains("anchor-new") ? "Anchor"
      : e.classList.contains("anchor-copy") ? "Copy" : e.className === "anchor-heading" ? "Anchor to" : "line");
    window.posted.length = 0;
    document.querySelector("#anchor-menu .anchor-copy").click();
    const copies = window.posted.filter((m) => m.name === "copy").map((m) => m.body.text);
    target.dispatchEvent(new MouseEvent("mousedown", { ...at, button: 2 }));
    target.dispatchEvent(new MouseEvent("contextmenu", at));
    document.querySelector("#anchor-menu .anchor-new").click();
    return { entries, copies };
  })()`);
  assert.deepEqual(seen.entries, ["Anchor", "Copy"], "the chat's two entries; the box has one paragraph, so no Anchor to list");
  assert.deepEqual(seen.copies, ["A reply of the past session."]);
  const held = await state(opened);
  assert.deepEqual(held.anchors, ["anchor: A reply of the past session. (session ended)"]);
  // The reader shows the anchored words, as the chat shows its own.
  const lit = await opened.run<string[]>(`[...(CSS.highlights.get("anchored") ?? [])].map((range) => range.toString())`);
  assert.deepEqual(lit, ["A reply of the past session."]);
  assert.deepEqual(await sentAnchors(opened), [
    { kind: "turn", session: "S0", start: 0, end: 28, message: "m0" },
  ], "the anchor is sent with the reader's session, not the chat's");
});

test("anchor mode takes a press on a passage of the History reader (KYO-972.c1)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await openReader();
  await opened.run(`(() => {
    const line = document.querySelector("#boxes .cm-line").getBoundingClientRect();
    document.querySelector("#boxes .cm-content").dispatchEvent(new MouseEvent("mousemove",
      { bubbles: true, clientX: line.left + 10, clientY: line.top + line.height / 2 }));
    document.querySelector("#boxes .mark.ghost").click();
    document.querySelector(".mark-panel .mark-anchor-mode").click();
  })()`);
  const point = (await opened.run<{ x: number; y: number }>(pointAt("#history-reader .reply", "second")))!;
  await move(opened, point);
  await wait(50);
  assert.deepEqual((await state(opened)).preview, ["It has a second sentence."], "the hover shows what a press takes");
  await press(opened, point);
  const after = await state(opened);
  assert.equal(after.mode, false);
  assert.deepEqual(after.anchors, ["anchor: It has a second sentence. (session ended)"]);
  assert.deepEqual(await sentAnchors(opened), [
    { kind: "turn", session: "S0", start: 29, end: 54, message: "m0" },
  ]);
});

test("the chip of an anchor to a past session opens History at the passage (KYO-972.c3)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await openReader();
  await opened.run(`(() => {
    const target = document.querySelector("#history-reader .reply p");
    const box = target.getBoundingClientRect();
    target.dispatchEvent(new MouseEvent("contextmenu",
      { bubbles: true, cancelable: true, clientX: box.left + 30, clientY: box.top + box.height / 2 }));
    document.querySelector("#anchor-menu .anchor-new").click();
  })()`);
  // The reader turns to another session, and the window to the chat.
  await readSession(opened, "S1");
  await opened.run(`document.querySelector('#view-items [data-view-item="chat"]').click()`);
  const chip = await opened.run<{ disabled: boolean; title: string }>(`(() => {
    document.querySelector("#boxes .mark.held").click();
    const button = document.querySelector(".mark-panel .mark-item .mark-anchor");
    window.posted.length = 0;
    button.click();
    return { disabled: button.disabled, title: button.closest(".mark-item").title };
  })()`);
  assert.equal(chip.disabled, false, "the chip takes a press");
  assert.match(chip.title, /open it in History/);
  const asked = await opened.run<{ view: boolean; wants: unknown[] }>(`({
    view: !document.getElementById("view-history").hidden,
    wants: window.posted.filter((p) => p.name === "history" && p.body.want === "turns").map((p) => p.body.session),
  })`);
  assert.deepEqual(asked, { view: true, wants: ["S0"] }, "History opens and reads the anchor's session");
  await answerTurns(opened, "S0");
  const shown = await opened.run<{ reader: boolean; lit: string | null; revealed: string[] }>(`({
    reader: !document.getElementById("history-reader").hidden,
    lit: document.querySelector("#history-reader .history-flash")?.dataset.hkey ?? null,
    revealed: [...document.querySelectorAll("#history-reader .revealed")].map((e) => e.textContent),
  })`);
  assert.deepEqual(shown, { reader: true, lit: "r:m0", revealed: [PAST] });
});

test("after a paragraph's last sentence, what is lit is what a press anchors, not the whole reply", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await opened.run(`document.querySelector(".mark-panel .mark-anchor-mode").click()`);
  // A point in the blank space to the right of the first paragraph's text.
  const point = await opened.run<{ x: number; y: number }>(`(() => {
    const block = document.querySelector("#chat .reply [data-source-line]");
    const text = document.createRange();
    text.selectNodeContents(block);
    const line = [...text.getClientRects()].pop();
    const box = block.getBoundingClientRect();
    return { x: Math.min(line.right + 60, box.right - 4), y: line.top + line.height / 2 };
  })()`);
  await move(opened, point);
  await wait(50);
  const lit = await opened.run<{ words: string[]; wholeReply: boolean }>(`(() => ({
    words: [
      ...[...(CSS.highlights.get("anchor-preview") ?? [])].map((range) => range.toString()),
      ...[...document.querySelectorAll(".anchor-lit")].map((element) => element.textContent),
    ],
    wholeReply: !!document.querySelector("#chat .reply.anchor-lit, #chat > .anchor-lit"),
  }))()`);
  assert.equal(lit.wholeReply, false, "the whole reply is not lit");
  assert.equal(lit.words.length > 0, true, "something in the paragraph is lit");
  assert.equal(lit.words.every((words) => !words.includes("A second paragraph")), true);
  await press(opened, point);
  const [anchored] = (await state(opened)).anchors;
  const said = anchored.replace(/^anchor: /, "").replace(/…$/, "");
  assert.equal(lit.words.some((words) => words.includes(said)), true, `lit ${JSON.stringify(lit.words)}, anchored ${anchored}`);
});
