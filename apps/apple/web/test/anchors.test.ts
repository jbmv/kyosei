// Run with `npm test`. What the composer does with an anchor that only a web view can show:
// the keys that undo and redo, the box's own undo history beside them, and what is painted
// in the chat (KYO-780, KYO-712), and what a prompt with anchors, a tag and a paste is saved
// and sent as (KYO-910). The page is the built renderer in a headless Chrome, fed the frames
// the app would send; a machine with no Chrome skips these. A box's text is read and its
// caret set through `window.kyo.composer`, since the box is an editor (KYO-910).
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
    window.kyo.composer.select(0, 0);
  })()`);
  return opened;
}

interface Shown { highlighted: string[]; bars: string[]; boxes: { text: string; anchors: string[] }[] }

/** The chat's highlights and bars for unsent anchors, and each box's text and anchors. */
const SHOWN = `(() => {
  const texts = window.kyo.composer.boxes().map((box) => box.text);
  return {
    highlighted: [...(CSS.highlights.get("anchored") ?? [])].map((range) => range.toString()),
    bars: [...document.querySelectorAll("#chat .anchored")].map((block) => block.textContent),
    boxes: [...document.querySelectorAll("#boxes .box")].map((box, index) => ({
      text: texts[index],
      anchors: [...box.querySelectorAll(".mark.held")].flatMap((mark) => mark.title.split("\\n")),
    })),
  };
})()`;
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

  // The editor groups typing into one undo step until a pause of 500 ms, as a Mac text view
  // does, where the text area took back one character at a time (KYO-910).
  await undo(opened);
  assert.deepEqual((await shown(opened)).boxes, [{ text: "", anchors: [] }], "the next undo is the box's own");

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
  // The typing before the anchor is one step, the editor's grouping (KYO-910).
  await undo(opened);
  assert.deepEqual((await shown(opened)).boxes, [{ text: "", anchors: [] }]);
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
  // Chrome sends the page a redo only when its own record of edits has one, and the editor
  // took the undo, so the menu's Redo does not put the anchor back here; the key does
  // (KYO-909.c5). How the app's Edit menu behaves is observed in the app.
  await opened.press("F13", 0, "redo");
  assert.deepEqual((await shown(opened)).boxes, [{ text: "abc", anchors: [] }]);
  await redo(opened);
  assert.deepEqual((await shown(opened)).boxes, [{ text: "abc", anchors: ["anchor: First paragraph of the reply here."] }]);
});

test("a removed anchor leaves the chat at once, and another box's anchor on the same words stays (KYO-780)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  // Two boxes anchored to the same words, and the first also to a list item.
  // The first is a scratchpad, which + adds above the prompt (KYO-903); the second is the prompt.
  await opened.run(`document.getElementById("new-prompt").click()`);
  await opened.run(`window.kyo.composer.select(0, 0)`);
  await opened.type("one");
  await anchor(opened, "#chat .reply p");
  await anchor(opened, "#chat .reply li strong");
  await opened.run(`window.kyo.composer.select(1, 0)`);
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
  await opened.run(`window.kyo.composer.select(0, window.kyo.composer.boxes()[0].text.length)`);
  await opened.press("Enter", 0, "insertNewline");
  await opened.type("more");
  await opened.run(`window.kyo.composer.select(0, 0, 4)`);
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
  // The deleted prompt leaves an empty one in its place (KYO-903).
  assert.deepEqual(now, {
    highlighted: [], bars: [], boxes: [{ text: "more", anchors: [] }, { text: "", anchors: [] }],
  });
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

/**
 * What the text-area composer saved and sent for the prompt `typedPrompt` makes, recorded from
 * the build before the editor (base 00bb7214, KYO-910.c3) with the same steps, in which the
 * paste was inserted by the browser after the page had seen it.
 */
const SAVED_BY_TEXT_AREA = {
    "id": "a",
    "text": "Opening words\n- see pasted words and more",
    "marks": [
      {
        "line": 0,
        "items": [
          {
            "kind": "tag",
            "name": "review"
          },
          {
            "kind": "anchor",
            "key": "r:m1",
            "session": "S1",
            "start": 36,
            "end": 76,
            "words": "Second paragraph, with more words in\u2026",
            "exact": true
          }
        ]
      },
      {
        "line": 1,
        "items": [
          {
            "kind": "anchor",
            "key": "r:m1",
            "session": "S1",
            "start": 0,
            "end": 34,
            "words": "First paragraph of the reply here.",
            "exact": true
          }
        ]
      }
    ]
  };
const SENT_BY_TEXT_AREA = {
    "segments": [
      {
        "kind": "typed",
        "text": "Opening words\n- see "
      },
      {
        "kind": "pasted",
        "text": "pasted words"
      },
      {
        "kind": "typed",
        "text": " and more"
      }
    ],
    "marks": [
      {
        "line": 0,
        "anchors": [
          {
            "kind": "turn",
            "session": "S1",
            "start": 36,
            "end": 76,
            "message": "m1"
          }
        ],
        "tags": [
          "review"
        ]
      },
      {
        "line": 1,
        "anchors": [
          {
            "kind": "turn",
            "session": "S1",
            "start": 0,
            "end": 34,
            "message": "m1"
          }
        ],
        "tags": []
      }
    ]
  };

/** A prompt typed and pasted into, with a tag and two anchors on two paragraphs. */
async function typedPrompt(opened: Page): Promise<void> {
  await opened.run(`window.kyo.draft({ boxes: [{ id: "a", text: "Opening words" }], etag: "e" })`);
  await opened.run(`window.kyo.composer.select(0, 13)`);
  await opened.press("Enter", 0, "insertNewline");
  await opened.type("- see ");
  await opened.run(`(() => {
    const data = new DataTransfer();
    data.setData("text/plain", "pasted words");
    document.activeElement.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  })()`);
  // The editor inserts the paste itself, as the browser did into the text area.
  assert.equal(await opened.run(`window.kyo.composer.boxes()[0].text`), "Opening words\n- see pasted words");
  await opened.type(" and more");
  // An anchor on the second paragraph, chosen from the menu's list of the others.
  await opened.run(`window.kyo.composer.select(0, 0)`);
  const rightClick = (selector: string, entry: string) => opened.run(`(() => {
    const target = document.querySelector(${JSON.stringify(selector)});
    const box = target.getBoundingClientRect();
    target.dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true, cancelable: true, clientX: box.left + 30, clientY: box.top + box.height / 2,
    }));
    document.querySelector(${JSON.stringify(entry)}).click();
  })()`);
  await rightClick("#chat .reply p", "#anchor-menu button:not(.anchor-new)");
  // A tag on the first paragraph, from the panel its ghost + opens.
  await opened.run(`(() => {
    const line = document.querySelector("#boxes .cm-line").getBoundingClientRect();
    document.querySelector("#boxes .cm-content").dispatchEvent(new MouseEvent("mousemove",
      { bubbles: true, clientX: line.left + 10, clientY: line.top + line.height / 2 }));
    document.querySelector("#boxes .mark.ghost").click();
  })()`);
  await opened.type("review");
  await opened.press("Enter");
  // And an anchor on the caret's paragraph, the first.
  await rightClick("#chat .reply p + p", "#anchor-menu .anchor-new");
}

test("the saved draft and the posted prompt are, field by field, those the text area made (KYO-910.c3)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await typedPrompt(opened);
  await opened.run(`new Promise((done) => setTimeout(done, 900))`);
  const saved = await opened.run<Record<string, unknown>[]>(
    `window.posted.filter((m) => m.name === "saveDraft").at(-1).body.boxes`);
  assert.equal(saved.length, 1);
  for (const field of ["id", "text", "marks", "scratch"]) {
    assert.deepEqual(saved[0][field], (SAVED_BY_TEXT_AREA as Record<string, unknown>)[field], `saved ${field}`);
  }
  assert.deepEqual(Object.keys(saved[0]).sort(), Object.keys(SAVED_BY_TEXT_AREA).sort());
  await opened.run(`document.querySelector("#boxes .box.main .box-send").click()`);
  const sent = await opened.run<Record<string, unknown>>(`window.posted.filter((m) => m.name === "send").at(-1).body`);
  for (const field of ["segments", "marks"]) {
    assert.deepEqual(sent[field], (SENT_BY_TEXT_AREA as Record<string, unknown>)[field], `sent ${field}`);
  }
  assert.deepEqual(Object.keys(sent).sort(), Object.keys(SENT_BY_TEXT_AREA).sort());
});

const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

test("a file pasted or dropped is attached to its paragraph as an undo step, and a path the app hands over is typed where it fell (KYO-910)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await opened.type("first");
  await opened.press("Enter", 0, "insertNewline");
  await opened.type("second");
  const file = (name: string, type: string) => `new File([Uint8Array.from(atob(${JSON.stringify(PNG)}), (c) => c.charCodeAt(0))], ${JSON.stringify(name)}, { type: ${JSON.stringify(type)} })`;
  // A pasted image goes to the caret's paragraph, the second, and no text is inserted.
  await opened.run(`(() => {
    const data = new DataTransfer();
    data.items.add(${file("shot.png", "image/png")});
    document.activeElement.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  })()`);
  await opened.run(`new Promise((done) => setTimeout(done, 200))`);
  const attach = await opened.run<{ ref: string; name: string }[]>(
    `window.posted.filter((m) => m.name === "attach").map((m) => ({ ref: m.body.ref, name: m.body.name }))`);
  assert.deepEqual(attach.map((body) => body.name), ["shot.png"]);
  await opened.run(`window.kyo.attached({ ref: ${JSON.stringify(attach[0].ref)}, id: "f1", path: "/tmp/shot.png" })`);
  assert.deepEqual(await shown(opened), {
    highlighted: [], bars: [], boxes: [{ text: "first\nsecond", anchors: ["attached: shot.png"] }],
  });
  const lines = `[...document.querySelectorAll("#boxes .mark.held")].map((mark) => {
    const top = mark.getBoundingClientRect().top;
    return [...document.querySelectorAll("#boxes .cm-line")].findIndex((line) => {
      const box = line.getBoundingClientRect();
      return top >= box.top - 2 && top < box.bottom;
    });
  })`;
  assert.deepEqual(await opened.run(lines), [1]);
  // The attaching is a step of the box's history: Command-Z takes it back, and the text stays.
  await undo(opened);
  assert.deepEqual((await shown(opened)).boxes, [{ text: "first\nsecond", anchors: [] }]);
  await redo(opened);
  assert.deepEqual((await shown(opened)).boxes, [{ text: "first\nsecond", anchors: ["attached: shot.png"] }]);

  // A drop of an image and a Markdown file on the first paragraph attaches the image there and
  // inserts nothing: the Markdown file's path is the app's to hand over.
  await opened.run(`(() => {
    const line = document.querySelector("#boxes .cm-line").getBoundingClientRect();
    const data = new DataTransfer();
    data.items.add(${file("drawn.png", "image/png")});
    data.items.add(new File(["# notes"], "notes.md", { type: "text/markdown" }));
    document.querySelector("#boxes .cm-content").dispatchEvent(new DragEvent("drop", {
      dataTransfer: data, bubbles: true, cancelable: true, clientX: line.left + 20, clientY: line.top + line.height / 2,
    }));
  })()`);
  await opened.run(`new Promise((done) => setTimeout(done, 200))`);
  const names = await opened.run<string[]>(`window.posted.filter((m) => m.name === "attach").map((m) => m.body.name)`);
  assert.deepEqual(names, ["shot.png", "drawn.png"]);
  assert.deepEqual(await opened.run(lines), [0, 1]);
  assert.equal((await shown(opened)).boxes[0].text, "first\nsecond");

  // The app hands over the path with the point of the drop: past the end of the first line.
  const point = await opened.run<{ x: number; y: number }>(`(() => {
    const at = window.kyo.composer.coords(0, 5);
    return { x: at.left + 40, y: at.top + 4 };
  })()`);
  await opened.run(`window.kyo.dropPaths({ paths: ["/Users/someone/notes.md"], x: ${point.x}, y: ${point.y} })`);
  assert.equal((await shown(opened)).boxes[0].text, "first/Users/someone/notes.md\nsecond");
  // A drop anywhere but on a box inserts nothing.
  const chat = await opened.run<{ x: number; y: number }>(`(() => {
    const box = document.querySelector("#chat .reply p").getBoundingClientRect();
    return { x: box.left + 10, y: box.top + 5 };
  })()`);
  await opened.run(`window.kyo.dropPaths({ paths: ["/Users/someone/other.md"], x: ${chat.x}, y: ${chat.y} })`);
  assert.equal((await shown(opened)).boxes[0].text, "first/Users/someone/notes.md\nsecond");
});
