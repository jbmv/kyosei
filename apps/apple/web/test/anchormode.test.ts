// Run with `npm test`. Anchor mode (KYO-823): from a paragraph's + panel, the next press on a
// place in the chat anchors it to that paragraph, as a right-click on a word would, without
// the Anchor to menu. A hover shows what a press would take and dims what it could not, a
// press there does nothing, and Escape ends the mode. A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const FIRST = "First sentence of the reply. Second sentence of it.";
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
    document.querySelector("#boxes textarea").focus();
  })()`);
  await opened.type("my paragraph");
  await wait(100);
  // The + beside a paragraph shows where the pointer is over the box, and opens its panel.
  await opened.run(`(() => {
    const area = document.querySelector("#boxes textarea").getBoundingClientRect();
    document.querySelector("#boxes .box-body").dispatchEvent(new MouseEvent("mousemove",
      { bubbles: true, clientX: area.left + 10, clientY: area.top + 8 }));
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
  mode: boolean; preview: string[]; dimmed: number; anchors: string[]; menu: boolean;
}>(`(() => ({
  mode: document.body.classList.contains("anchor-mode"),
  preview: [...(CSS.highlights.get("anchor-preview") ?? [])].map((range) => range.toString()),
  dimmed: document.querySelectorAll(".anchor-dim").length,
  anchors: [...document.querySelectorAll("#boxes .mark.held")].flatMap((mark) => mark.title.split("\\n")),
  menu: !!document.getElementById("anchor-menu")?.isConnected,
}))()`);

test("the + panel's Anchor starts the mode; a hover shows the sentence and a press anchors it", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  const button = await opened.run<boolean>(`(() => {
    const button = document.querySelector(".mark-panel .mark-anchor-mode");
    const divider = button?.nextElementSibling?.classList.contains("mark-divider");
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

test("a hover on what cannot be anchored dims it, and a press there does nothing", async (t) => {
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
  assert.equal(hovered.dimmed, 1);
  assert.deepEqual(hovered.preview, []);
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
