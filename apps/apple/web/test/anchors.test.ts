// Run with `npm test`. What the composer does with an anchor that only a web view can show:
// the keys that undo and redo, the box's own undo history beside them, and what is painted
// in the chat (KYO-780, KYO-712). The page is the built renderer in a headless Chrome, fed
// the frames the app would send; a machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { META, SHIFT, openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const FIRST = "First paragraph of the reply here.";
const SECOND = "Second paragraph, with more words in it.";
const UUID = "9b949b65-606d-47fa-bbfd-f46f11c8635d";

/** A project, a session and a chat of two prompts and a reply, with one empty box focused. */
async function open(marks: object[] = []): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.apply({ reset: true,
      turns: [
        { id: "u1", at: "2026-01-01T00:00:00Z", text: "A prompt of several words." },
        { id: "u2", at: "2026-01-01T00:00:02Z",
          text: 'on your reply ${UUID} "First paragraph of..."\\nThe paragraph that was anchored.\\n\\nAnother paragraph.' },
      ],
      replies: [{ message_id: "m1", uuid: "x1", at: "2026-01-01T00:00:01Z", text: ${JSON.stringify(`${FIRST}\n\n${SECOND}\n\n- a **bold** item`)} }] });
    window.kyo.marks({ marks: ${JSON.stringify(marks)} });
    document.querySelector("#boxes textarea").focus();
  })()`);
  return opened;
}

interface Shown { highlighted: string[]; bars: string[]; boxes: { text: string; anchors: string[] }[] }

/** The chat's highlights and bars for unsent anchors, and each box's text and anchors. */
const SHOWN = `(() => ({
  highlighted: [...(CSS.highlights.get("anchored") ?? [])].map((range) => range.toString()),
  bars: [...document.querySelectorAll("#chat .anchored")].map((block) => block.textContent),
  boxes: [...document.querySelectorAll("#boxes .box")].map((box) => ({
    text: box.querySelector("textarea").value,
    anchors: [...box.querySelectorAll(".mark.held")].flatMap((mark) => mark.title.split("\\n")),
  })),
}))()`;
const shown = (opened: Page) => opened.run<Shown>(SHOWN);

/** Right-clicks an element of the chat and takes the menu's first entry, the caret's paragraph. */
const anchor = (opened: Page, selector: string) => opened.run(`(() => {
  const target = document.querySelector(${JSON.stringify(selector)});
  const box = target.getBoundingClientRect();
  target.dispatchEvent(new MouseEvent("contextmenu", {
    bubbles: true, cancelable: true, clientX: box.left + 30, clientY: box.top + box.height / 2,
  }));
  document.querySelector("#anchor-menu .anchor-new").click();
})()`);

const undo = (opened: Page) => opened.press("z", META, "undo");
const redo = (opened: Page) => opened.press("z", META | SHIFT, "redo");
const focusBox = (opened: Page, index: number) =>
  opened.run(`document.querySelectorAll("#boxes textarea")[${index}].focus()`);

test("Command-Z takes back an anchor before the typing, and Shift-Command-Z puts it back (KYO-780)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await opened.type("abc");
  await anchor(opened, "#chat .reply p");
  assert.deepEqual(await shown(opened), {
    highlighted: [FIRST], bars: [], boxes: [{ text: "abc", anchors: ["anchor: First paragraph of the reply here."] }],
  });

  await undo(opened);
  assert.deepEqual(await shown(opened), { highlighted: [], bars: [], boxes: [{ text: "abc", anchors: [] }] },
    "the anchor is gone, the text is as it was, and the chat shows no highlight");

  await undo(opened);
  assert.deepEqual((await shown(opened)).boxes, [{ text: "ab", anchors: [] }], "the next undo is the box's own");

  await redo(opened);
  assert.deepEqual((await shown(opened)).boxes, [{ text: "abc", anchors: [] }], "the typing comes back first");
  await redo(opened);
  assert.deepEqual(await shown(opened), {
    highlighted: [FIRST], bars: [], boxes: [{ text: "abc", anchors: ["anchor: First paragraph of the reply here."] }],
  }, "then the anchor, with its highlight");
});

test("typing after an anchor is undone before the anchor is (KYO-780)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await opened.type("abc");
  await anchor(opened, "#chat .reply p");
  await opened.type("d");
  await undo(opened);
  assert.deepEqual((await shown(opened)).boxes, [{ text: "abc", anchors: ["anchor: First paragraph of the reply here."] }]);
  await undo(opened);
  assert.deepEqual((await shown(opened)).boxes, [{ text: "abc", anchors: [] }]);
  await undo(opened);
  assert.deepEqual((await shown(opened)).boxes, [{ text: "ab", anchors: [] }]);
});

test("an anchor in an empty box is taken back with no typing to undo (KYO-780)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await anchor(opened, "#chat .reply p");
  assert.deepEqual((await shown(opened)).highlighted, [FIRST]);
  await undo(opened);
  assert.deepEqual(await shown(opened), { highlighted: [], bars: [], boxes: [{ text: "", anchors: [] }] });
  await redo(opened);
  assert.deepEqual((await shown(opened)).highlighted, [FIRST]);
});

test("the Undo menu item, which reaches the page as an edit, takes back the anchor too (KYO-780)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await opened.type("abc");
  await anchor(opened, "#chat .reply p");
  // The editing command with no Command-Z for the page to read, as the menu item gives it.
  await opened.press("F13", 0, "undo");
  assert.deepEqual(await shown(opened), { highlighted: [], bars: [], boxes: [{ text: "abc", anchors: [] }] });
  // The engine sends the page a redo only when the box's own history has one, so with none
  // the menu's Redo does not put the anchor back, and the key does.
  await opened.press("F13", 0, "redo");
  assert.deepEqual((await shown(opened)).boxes, [{ text: "abc", anchors: [] }]);
  await redo(opened);
  assert.deepEqual((await shown(opened)).boxes, [{ text: "abc", anchors: ["anchor: First paragraph of the reply here."] }]);
});

test("a removed anchor leaves the chat at once, and another box's anchor on the same words stays (KYO-780)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  // Two boxes anchored to the same words, and the first also to a list item.
  await opened.type("one");
  await anchor(opened, "#chat .reply p");
  await anchor(opened, "#chat .reply li strong");
  await opened.run(`document.getElementById("new-prompt").click()`);
  await opened.type("two");
  await anchor(opened, "#chat .reply p");
  const first = "anchor: First paragraph of the reply here.";
  const ITEM = "a bold item";
  let now = await shown(opened);
  assert.deepEqual(now.boxes.map((box) => box.anchors.length), [2, 1]);
  assert.deepEqual(now.highlighted.sort(), [FIRST, FIRST, ITEM]);

  // By undo: the second box's anchor goes, the first box's keeps the words highlighted.
  await undo(opened);
  now = await shown(opened);
  assert.deepEqual(now.boxes[1], { text: "two", anchors: [] });
  assert.deepEqual(now.highlighted, [FIRST, ITEM]);
  await redo(opened);
  assert.deepEqual((await shown(opened)).highlighted.sort(), [FIRST, FIRST, ITEM]);

  // By its chip: the first box's anchor on the words goes, the second box's keeps them.
  await opened.run(`(() => {
    document.querySelector("#boxes .box .mark.held").click();
    document.querySelector(".mark-panel .mark-remove").click();
  })()`);
  now = await shown(opened);
  assert.equal(now.boxes[0].anchors.includes(first), false);
  assert.deepEqual(now.highlighted.sort(), [FIRST, ITEM]);

  // By deleting its line: the first box's paragraph goes with the anchor it still held.
  await focusBox(opened, 0);
  await opened.run(`(() => {
    const area = document.querySelectorAll("#boxes textarea")[0];
    area.setSelectionRange(area.value.length, area.value.length);
  })()`);
  await opened.press("Enter", 0, "insertNewline");
  await opened.type("more");
  await opened.run(`document.querySelectorAll("#boxes textarea")[0].setSelectionRange(0, 4)`);
  await opened.press("Backspace", 0, "deleteBackward");
  now = await shown(opened);
  assert.deepEqual(now.boxes[0], { text: "more", anchors: [] });
  assert.deepEqual(now.highlighted, [FIRST], "the list item's highlight is gone and the other box's anchor is still shown");

  // By deleting the box that holds the last anchor on the words, while another box stays.
  await opened.run(`(() => {
    const remove = document.querySelectorAll("#boxes .box .box-delete")[1];
    remove.click();
    remove.click();
  })()`);
  now = await shown(opened);
  assert.deepEqual(now, { highlighted: [], bars: [], boxes: [{ text: "more", anchors: [] }] });
});

test("a sent paragraph whose anchor is stale says so on its chip and names the paragraph (KYO-712)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const read = `(() => {
    const chip = document.querySelector("#chat .turn .turn-anchor[data-anchor]");
    return { stale: chip.classList.contains("stale"), title: chip.title,
      said: getComputedStyle(chip, "::after").content, text: chip.parentElement.textContent };
  })()`;
  type Chip = { stale: boolean; title: string; said: string; text: string };
  const mark = { note: "n1", text: "The paragraph that was anchored.", tags: [], sent: "2026-01-01T00:00:02Z" };
  const opened = await open([{ ...mark, anchors: [{ role: "agent", uuid: UUID, stale: true }] }]);
  const stale = await opened.run<Chip>(read);
  assert.equal(stale.stale, true);
  assert.equal(stale.said, '"place lost"');
  assert.match(stale.title, /\(stale: the place "The paragraph that was anchored\." was anchored to is no longer in the transcript as it was\)$/);
  assert.equal(stale.text.includes("place lost"), false, "the warning is no part of the prompt's text");

  // An anchor the server can still place is drawn as before, and a warning goes when the frame says so.
  await opened.run(`window.kyo.marks({ marks: ${JSON.stringify([{ ...mark, anchors: [{ role: "agent", message: "m1", start: 0, end: 5 }] }])} })`);
  const fresh = await opened.run<Chip>(read);
  assert.equal(fresh.stale, false);
  assert.equal(fresh.said, "none");
  assert.equal(fresh.title.includes("stale"), false);
});
