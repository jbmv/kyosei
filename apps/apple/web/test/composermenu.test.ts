// Run with `npm test`. KYO-912: Indent and Outdent in WebKit's menu on a box's words. The page
// tells the app on every right-click whether it was on a box's words, by a `composerMenu`
// message; the app adds the two items to WebKit's menu, which keeps its spelling suggestions,
// and calls `window.kyo.indent()` or `window.kyo.outdent()` when one is chosen. The menu and
// its items are the app's, built in ChatView.swift, and are not seen here. A machine with no
// Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, SHIFT, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const START = `window.kyo.project({ project: "p", scope: "server/p" });`;
const read = `(() => { const a = window.kyo.composer.boxes().find((box) => box.focused); return a ? [a.text, a.from, a.to] : null; })()`;
const texts = `window.kyo.composer.boxes().map((box) => box.text)`;

/** Right-clicks the first element the selector finds, and returns what the page posted. */
const rightClick = (selector: string) => page!.run<unknown[]>(`(() => {
  window.posted.length = 0;
  const target = document.querySelector(${JSON.stringify(selector)});
  target.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 10, clientY: 10 }));
  return window.posted.filter((p) => p.name === "composerMenu").map((p) => p.body);
})()`);

test("every right-click tells the app whether it was on a box's words", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  await page.run(`(() => { ${START}
    window.kyo.draft({ boxes: [{ id: "a", text: "first line\\nsecond" }], etag: "e" }); })()`);
  assert.deepEqual(await rightClick("#boxes .cm-line"), [{ composer: true }]);
  assert.deepEqual(await rightClick("#boxes .cm-content"), [{ composer: true }]);
  assert.deepEqual(await rightClick("body"), [{ composer: false }]);
  assert.deepEqual(await rightClick("#chat"), [{ composer: false }]);
  // The page does not take WebKit's menu, which holds the spelling suggestions.
  const shown = await page.run<boolean>(`document.querySelector("#boxes .cm-line")
    .dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true }))`);
  assert.equal(shown, true);
});

test("Indent and Outdent act on the focused box's selection as Tab and Shift-Tab do", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  await page.run(`(() => { ${START}
    window.kyo.draft({ boxes: [{ id: "s", text: "- a\\n- b\\n3. c\\n- d", scratch: { heading: "" } }], etag: "e" });
    window.kyo.composer.select(0, 5, 12); })()`);
  // The same lines and selection as the Tab test in scratchpads.test.ts, by the menu's calls.
  await page.run(`window.kyo.indent()`);
  const indented = await page.run(read);
  assert.deepEqual(indented, ["- a\n  - b\n  1. c\n- d", 4, 16]);
  await page.run(`window.kyo.outdent()`);
  await page.run(`window.kyo.outdent()`);  // nothing left to outdent: still in the box
  const outdented = await page.run(read);
  assert.deepEqual(outdented, ["- a\n- b\n1. c\n- d", 4, 12]);

  // The same from the keys, on the same text, for the comparison.
  await page.run(`(() => {
    window.kyo.draft({ boxes: [{ id: "s", text: "- a\\n- b\\n3. c\\n- d", scratch: { heading: "" } }], etag: "e2" });
    window.kyo.composer.select(0, 5, 12); })()`);
  await page.press("Tab");
  assert.deepEqual(await page.run(read), indented);
  await page.press("Tab", SHIFT);
  await page.press("Tab", SHIFT);
  assert.deepEqual(await page.run(read), outdented);

  // A caret on a plain line, in a prompt box.
  await page.run(`(() => {
    window.kyo.draft({ boxes: [{ id: "a", text: "plain" }], etag: "e3" });
    window.kyo.composer.select(0, 5); })()`);
  await page.run(`window.kyo.indent()`);
  assert.deepEqual(await page.run(read), ["  plain", 7, 7]);
  await page.run(`window.kyo.outdent()`);
  assert.deepEqual(await page.run(read), ["plain", 5, 5]);
});

test("with no box focused, Indent and Outdent do nothing", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  await page.run(`(() => { ${START}
    window.kyo.draft({ boxes: [{ id: "a", text: "- one\\n- two" }], etag: "e" });
    window.kyo.composer.select(0, 8);
    document.activeElement.blur(); })()`);
  assert.equal(await page.run(read), null, "no box has the focus");
  const before = await page.run<string[]>(texts);
  await page.run(`window.kyo.indent()`);
  await page.run(`window.kyo.outdent()`);
  assert.deepEqual(await page.run<string[]>(texts), before);
  assert.equal(await page.run(`document.activeElement === document.body`), true, "the focus is not moved into a box");
});
