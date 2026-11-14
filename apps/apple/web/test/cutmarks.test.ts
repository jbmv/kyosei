// Run with `npm test`. A line cut from a box takes its anchors, attachments and tags with it
// when it is pasted into a box of the same window (KYO-938): only for a cut of whole lines and
// a paste of the text that was cut, once, as one step of the box's history, and a file still
// uploading goes with its line. The system clipboard is a DataTransfer carrying text alone.
// A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { test } from "node:test";
import { META, openPage, type Page } from "./chrome.ts";

const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const A = "a".repeat(64);
const START = `window.kyo.project({ project: "p", scope: "server/p" })`;
const pause = (ms = 900) => `new Promise((done) => setTimeout(done, ${ms}))`;
const ANCHOR = { kind: "anchor", key: "r:m1", session: "S1", start: 0, end: 20, words: "A reply paragraph.", exact: true };
const TAG = { kind: "tag", name: "review" };
const FILE = { kind: "file", id: A, name: "first.png", path: `/home/composer/attachments/p/s1/${A}.png`, thumb: "data:image/jpeg;base64,AA" };
/** The prompt holds the lines with marks; the scratchpad is the place they are cut to. */
const DRAFT = {
  boxes: [
    { id: "pad", text: "notes\nlast", scratch: {} },
    {
      id: "m", text: "one\ntwo\nthree\nfour",
      marks: [{ line: 1, items: [ANCHOR, TAG] }, { line: 2, items: [FILE] }],
    },
  ],
  etag: "e1",
};

async function open(page: Page): Promise<void> {
  await page.reload();
  await page.run(START);
  await page.run(`window.kyo.draft(${JSON.stringify(DRAFT)})`);
}

/** Cuts from `from` to `to` of box `index`, keeping the clipboard in `window.clip`. */
const cut = (index: number, from: number, to: number) => `(() => {
  window.kyo.composer.select(${index}, ${from}, ${to});
  window.clip = new DataTransfer();
  document.activeElement.dispatchEvent(new ClipboardEvent("cut", { clipboardData: window.clip, bubbles: true, cancelable: true }));
})()`;
const copy = (index: number, from: number, to: number) => `(() => {
  window.kyo.composer.select(${index}, ${from}, ${to});
  window.clip = new DataTransfer();
  document.activeElement.dispatchEvent(new ClipboardEvent("copy", { clipboardData: window.clip, bubbles: true, cancelable: true }));
})()`;
/** Pastes what `window.clip` holds, or `words`, at `at` of box `index`. */
const paste = (index: number, at: number, words?: string) => `(() => {
  window.kyo.composer.select(${index}, ${at});
  const data = new DataTransfer();
  data.setData("text/plain", ${words === undefined ? `window.clip.getData("text/plain")` : JSON.stringify(words)});
  document.activeElement.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
})()`;

const BOXES = `window.kyo.composer.boxes().map((box) => [box.kind, box.text])`;
/** The draft last saved, as each box's marks: its line and the kinds it holds. */
async function saved(page: Page): Promise<[string, [number, string[]][]][]> {
  await page.run(pause());
  return page.run(`window.posted.filter((m) => m.name === "saveDraft").at(-1).body.boxes.map((box) =>
    [box.id, (box.marks ?? []).map((mark) => [mark.line, mark.items.map((item) => item.kind)])])`);
}

test("lines cut from the prompt are pasted into a scratchpad with their anchor, tag and attachment, in one undo step (KYO-938.c1, c2, c5)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await open(page);
    // "two\nthree\n": the second and third lines, up to the start of the fourth.
    await page.run(cut(1, 4, 14));
    assert.deepEqual(await page.run(BOXES), [["scratch", "notes\nlast"], ["prompt", "one\nfour"]]);
    assert.deepEqual(await saved(page), [["pad", []], ["m", []]]);
    // Into the scratchpad, at the start of its second line.
    await page.run(paste(0, 6));
    assert.deepEqual(await page.run(BOXES), [["scratch", "notes\ntwo\nthree\nlast"], ["prompt", "one\nfour"]]);
    assert.deepEqual(await saved(page), [["pad", [[1, ["anchor", "tag"]], [2, ["file"]]]], ["m", []]]);
    // One Command-Z takes the text and its marks away together.
    await page.press("z", META);
    assert.deepEqual(await page.run(BOXES), [["scratch", "notes\nlast"], ["prompt", "one\nfour"]]);
    assert.deepEqual(await saved(page), [["pad", []], ["m", []]]);
    // A second paste of the same text is plain: the marks went with the first.
    await open(page);
    await page.run(cut(1, 4, 14));
    await page.run(paste(0, 6));
    await page.run(paste(0, 0));
    assert.equal(await page.run(`window.kyo.composer.boxes()[0].text`), "two\nthree\nnotes\ntwo\nthree\nlast");
    assert.deepEqual(await saved(page), [["pad", [[3, ["anchor", "tag"]], [4, ["file"]]]], ["m", []]]);
  } finally {
    await page.close();
  }
});

test("a copy, a cut inside a line and a paste of other words carry nothing (KYO-938.c3)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    // A copy leaves the lines and their marks, and a paste of it is plain.
    await open(page);
    await page.run(copy(1, 4, 14));
    await page.run(paste(0, 6));
    assert.deepEqual(await saved(page), [["pad", []], ["m", [[1, ["anchor", "tag"]], [2, ["file"]]]]]);

    // A cut that begins inside a line, and one that ends inside a line.
    for (const [from, to] of [[5, 14], [4, 12]]) {
      await open(page);
      await page.run(cut(1, from, to));
      await page.run(paste(0, 6));
      const marks = (await saved(page)).find(([id]) => id === "pad")![1];
      assert.deepEqual(marks, [], `${from}-${to}`);
    }

    // The words are not those that were cut.
    await open(page);
    await page.run(cut(1, 4, 14));
    await page.run(paste(0, 6, "two\nthree\n "));
    assert.deepEqual((await saved(page)).find(([id]) => id === "pad")![1], []);
  } finally {
    await page.close();
  }
});

test("a file still uploading when its line is cut is attached to the pasted line, and Send sends it (KYO-938.c4)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(START);
    await page.run(`window.kyo.draft(${JSON.stringify({ boxes: [
      { id: "pad", text: "notes\nlast", scratch: {} }, { id: "m", text: "one\nfour" }], etag: "e1" })})`);
    await page.run(`window.kyo.composer.select(1, 3)`);
    await page.run(`(() => {
      const data = new DataTransfer();
      data.items.add(new File([Uint8Array.from(atob(${JSON.stringify(PNG)}), (c) => c.charCodeAt(0))], "shot.png", { type: "image/png" }));
      document.activeElement.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
    })()`);
    await page.run(pause(200));
    const ref = await page.run<string>(`window.posted.filter((m) => m.name === "attach").at(-1).body.ref`);
    // The file is on the prompt's first line; the server has not answered when it is moved.
    await page.run(cut(1, 0, 4));
    await page.run(paste(0, 6));
    await page.run(`window.kyo.attached({ ref: ${JSON.stringify(ref)}, id: ${JSON.stringify(A)}, path: "/tmp/shot.png" })`);
    assert.deepEqual(await saved(page), [["pad", [[1, ["file"]]]], ["m", []]]);
    assert.equal(await page.run(`window.posted.filter((m) => m.name === "saveDraft").at(-1).body.boxes[0].marks[0].items[0].id`), A);
    // Back in the prompt for Send.
    await page.run(cut(0, 6, 10));
    await page.run(paste(1, 0));
    await page.run(`window.kyo.composer.select(1, 0)`);
    await page.run(`document.querySelector("#boxes .box.main .box-send").click()`);
    assert.deepEqual(await page.run(`window.posted.filter((m) => m.name === "send").at(-1).body.marks.map((mark) => [mark.line, mark.attachments ?? []])`),
      [[0, [A]]]);
  } finally {
    await page.close();
  }
});
