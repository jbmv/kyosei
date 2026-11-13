// Run with `npm test`. A scratchpad is moved among the others by dragging its head, its fold
// toggle included, and a press on the head that does not move still folds and opens it
// (KYO-926). The new order is saved with the draft, and a window that reads the draft shows
// it. The pointer is driven by mouse events dispatched in the page. A machine with no Chrome
// skips these.
import assert from "node:assert/strict";
import { test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

const START = `window.kyo.project({ project: "p", scope: "server/p" });`;
const saved = `window.posted.filter((m) => m.name === "saveDraft").at(-1)?.body.boxes`;
const pause = `new Promise((done) => setTimeout(done, 900))`;
const DRAFT = [
  { id: "a", text: "alpha\nnotes", scratch: {} },
  { id: "b", text: "beta\nnotes", scratch: {} },
  { id: "c", text: "gamma\nnotes", scratch: {} },
  { id: "m", text: "the prompt" },
];
const ORDER = `[...document.querySelectorAll("#scratches > .box .scratch-title")].map((title) => title.textContent)`;
const COLLAPSED = `[...document.querySelectorAll("#scratches > .box")].map((box) => box.classList.contains("collapsed"))`;

/**
 * Presses on the head part `part` ("title" or "toggle") of scratchpad `from`, moves the pointer
 * to `to` px below the middle of scratchpad `over` (or by `to` px alone when `over` is null),
 * and lets go, with the click a press ends in.
 */
function drag(from: number, part: "title" | "toggle", over: number | null, to: number): string {
  return `(() => {
    const boxes = [...document.querySelectorAll("#scratches > .box")];
    const target = boxes[${from}].querySelector(".scratch-${part}");
    const start = target.getBoundingClientRect();
    const x = start.left + start.width / 2, y = start.top + start.height / 2;
    const event = (type, at, on) => on.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientX: x, clientY: at }));
    event("mousedown", y, target);
    let end = y + ${to};
    ${over === null ? "" : `const r = boxes[${over}].getBoundingClientRect(); end = r.top + r.height / 2 + ${to};`}
    for (let step = 1; step <= 4; step += 1) event("mousemove", y + (end - y) * step / 4, document.body);
    event("mouseup", end, document.body);
    event("click", end, target);
  })()`;
}

async function open(page: Page): Promise<void> {
  await page.reload();
  await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(DRAFT)}, etag: "e" });
    window.kyo.composer.select(0, 2); })()`);
}

test("a scratchpad dragged by its head moves among the others, and the order is saved with the draft (KYO-926.c1, c2)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await open(page);
    // Below the middle of the last: it goes last, and the click that ends the drag folds nothing.
    await page.run(drag(0, "title", 2, 4));
    assert.deepEqual(await page.run(ORDER), ["beta", "gamma", "alpha"]);
    assert.deepEqual(await page.run(COLLAPSED), [false, false, false]);
    // The box being typed in keeps the caret where it was.
    assert.deepEqual(await page.run(`(() => { const box = window.kyo.composer.boxes().find((each) => each.focused);
      return box && [box.text, box.from]; })()`), ["alpha\nnotes", 2]);
    await page.run(pause);
    assert.deepEqual((await page.run<{ id: string }[]>(saved)).map((box) => box.id), ["b", "c", "a", "m"]);
    // By its toggle, above the middle of the first: it goes first.
    await page.run(drag(1, "toggle", 0, -4));
    assert.deepEqual(await page.run(ORDER), ["gamma", "beta", "alpha"]);
    assert.deepEqual(await page.run(COLLAPSED), [false, false, false]);
    await page.run(pause);
    const order = await page.run<{ id: string }[]>(saved);
    assert.deepEqual(order.map((box) => box.id), ["c", "b", "a", "m"]);
    // Read again, as on another device or after a relaunch, the draft shows the same order.
    await page.reload();
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(order)}, etag: "e2" }); })()`);
    assert.deepEqual(await page.run(ORDER), ["gamma", "beta", "alpha"]);
  } finally { await page.close(); }
});

test("another window showing the draft takes the new order when the moved draft is saved (KYO-926.c2)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(DRAFT)}, etag: "e" }); })()`);
    const moved = [DRAFT[2], DRAFT[0], DRAFT[1], DRAFT[3]];
    await page.run(`window.kyo.draftElsewhere({ boxes: ${JSON.stringify(moved)}, etag: "e2" })`);
    assert.deepEqual(await page.run(ORDER), ["gamma", "alpha", "beta"]);
    assert.deepEqual(await page.run(`window.kyo.composer.boxes().map((box) => box.id)`), ["c", "a", "b", "m"]);
  } finally { await page.close(); }
});

test("a press on the head that does not move folds and opens the scratchpad as before, and a drag back to its place moves nothing (KYO-926.c1)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await open(page);
    await page.run(drag(1, "toggle", null, 2));
    assert.deepEqual(await page.run(COLLAPSED), [false, true, false]);
    await page.run(drag(1, "title", null, 0));
    assert.deepEqual(await page.run(COLLAPSED), [false, false, false]);
    // The fold and the opening are saved; what follows is not.
    await page.run(pause);
    await page.run(`window.posted = []`);
    await page.run(drag(1, "title", null, 12));
    assert.deepEqual(await page.run(ORDER), ["alpha", "beta", "gamma"]);
    assert.deepEqual(await page.run(COLLAPSED), [false, false, false]);
    await page.run(pause);
    assert.equal(await page.run(`${saved} ?? null`), null);
    // Nothing of the drag is left drawn.
    assert.deepEqual(await page.run(`[document.querySelectorAll(".scratch-drop, #scratches .dragging").length,
      document.body.classList.contains("sorting-scratch"),
      [...document.querySelectorAll("#scratches > .box")].map((box) => box.style.transform)]`), [0, false, ["", "", ""]]);
  } finally { await page.close(); }
});

test("the heads move the same way while the scratchpads are drawn as heads, and the section in use stays", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await open(page);
    await page.run(`window.kyo.composer.select(3, 0)`);
    assert.equal(await page.run(`document.getElementById("boxes").dataset.inUse`), "prompt");
    await page.run(drag(2, "title", 0, -4));
    assert.deepEqual(await page.run(ORDER), ["gamma", "alpha", "beta"]);
    assert.equal(await page.run(`document.getElementById("boxes").dataset.inUse`), "prompt");
    assert.equal(await page.run(`window.kyo.composer.boxes().findIndex((box) => box.focused)`), 3);
  } finally { await page.close(); }
});
