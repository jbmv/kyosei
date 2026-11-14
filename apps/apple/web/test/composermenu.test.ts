// Run with `npm test`. KYO-912: Indent and Outdent in WebKit's menu on a box's words. The page
// tells the app on every right-click whether it was on a box's words, by a `composerMenu`
// message; the app adds the two items to WebKit's menu, which keeps its spelling suggestions,
// and calls `window.kyo.indent()` or `window.kyo.outdent()` when one is chosen. The menu and
// its items are the app's, built in ChatView.swift, and are not seen here. KYO-915: Edit > Undo
// and Edit > Redo. The page tells the app, by an `undo` message, whether a box's words hold the
// focus and whether there is then a step to undo and to redo; the app enables its two items
// from that and calls `window.kyo.undo()` or `window.kyo.redo()` when one is chosen. A machine
// with no Chrome skips these.
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

// What the page last told the app of Undo and Redo, read once the telling has run.
const told = () => page!.run<{ box: boolean; undo: boolean; redo: boolean } | undefined>(
  `new Promise((done) => setTimeout(() => done(window.posted.filter((p) => p.name === "undo").at(-1)?.body), 30))`);
// The box's words, and what each gutter mark holds.
const held = () => page!.run<[string[], string[]]>(`[window.kyo.composer.boxes().map((box) => box.text),
  [...document.querySelectorAll("#boxes .mark.held")].flatMap((mark) => mark.title.split("\\n"))]`);
const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

test("Undo and Redo from the menu take back and put back an anchor, a tag and an attachment, and the app is told when there is a step (KYO-915.c1, c2)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  await page.run(`(() => { ${START}
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.apply({ reset: true,
      turns: [{ id: "u1", at: "2026-01-01T00:00:00Z", text: "A prompt of several words." }],
      replies: [{ message_id: "m1", uuid: "x1", at: "2026-01-01T00:00:01Z", text: "First paragraph of the reply here." }] });
    window.kyo.draft({ boxes: [{ id: "a", text: "abc" }], etag: "e" });
    window.kyo.composer.select(0, 3); })()`);
  // A box with the caret and no step: the items are the box's, and neither is enabled.
  assert.deepEqual(await told(), { box: true, undo: false, redo: false });
  assert.equal(await page.run(`window.kyo.undo()`), false);
  assert.equal(await page.run(`window.kyo.redo()`), false);

  // Each of the three is placed, taken back with the words left, and put back.
  const steps: [string, () => Promise<unknown>][] = [
    ["anchor: First paragraph of the reply here.", () => page!.run(`(() => {
      const target = document.querySelector("#chat .reply p");
      const at = target.getBoundingClientRect();
      target.dispatchEvent(new MouseEvent("contextmenu", {
        bubbles: true, cancelable: true, clientX: at.left + 30, clientY: at.top + at.height / 2,
      }));
      document.querySelector("#anchor-menu .anchor-new").click();
    })()`)],
    ["#review", async () => {
      await page!.run(`(() => {
        const line = document.querySelector("#boxes .cm-line").getBoundingClientRect();
        document.querySelector("#boxes .cm-content").dispatchEvent(new MouseEvent("mousemove",
          { bubbles: true, clientX: line.left + 10, clientY: line.top + line.height / 2 }));
        document.querySelector("#boxes .mark").click();
        // The panel of a mark that holds something leaves the caret in the box.
        document.querySelector(".mark-panel input").focus();
      })()`);
      await page!.type("review");
      // The tag is typed in the panel's field, which is not a box: the items are WebKit's there.
      assert.deepEqual(await told(), { box: false, undo: false, redo: false });
      assert.equal(await page!.run(`window.kyo.undo()`), false);
      await page!.press("Enter");
      await page!.run(`window.kyo.composer.select(0, 3)`);
    }],
    ["attached: shot.png", async () => {
      await page!.run(`(() => {
        const data = new DataTransfer();
        data.items.add(new File([Uint8Array.from(atob(${JSON.stringify(PNG)}), (c) => c.charCodeAt(0))], "shot.png", { type: "image/png" }));
        document.activeElement.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
      })()`);
      await page!.run(`new Promise((done) => setTimeout(done, 200))`);
      const ref = await page!.run<string>(`window.posted.filter((m) => m.name === "attach").at(-1).body.ref`);
      await page!.run(`window.kyo.attached({ ref: ${JSON.stringify(ref)}, id: "f1", path: "/tmp/shot.png" })`);
    }],
  ];
  const placed: string[] = [];
  for (const [title, place] of steps) {
    await place();
    assert.deepEqual(await held(), [["abc"], [...placed, title]], `${title} is placed`);
    assert.deepEqual(await told(), { box: true, undo: true, redo: false }, `${title} is a step to undo`);
    assert.equal(await page.run(`window.kyo.undo()`), true);
    assert.deepEqual(await held(), [["abc"], placed], `Undo removes ${title} and leaves the rest`);
    assert.deepEqual(await told(), { box: true, undo: placed.length > 0, redo: true }, `${title} is a step to redo`);
    assert.equal(await page.run(`window.kyo.redo()`), true);
    assert.deepEqual(await held(), [["abc"], [...placed, title]], `Redo puts ${title} back`);
    placed.push(title);
  }

  // With the focus in no box the two calls do nothing, and the app is told the items are not a box's.
  await page.run(`document.activeElement.blur()`);
  assert.deepEqual(await told(), { box: false, undo: false, redo: false });
  assert.equal(await page.run(`window.kyo.undo()`), false);
  assert.deepEqual(await held(), [["abc"], placed]);
});
