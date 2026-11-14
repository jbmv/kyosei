// Run with `npm test`. A box's images are files attached to its lines (KYO-910): each mark
// holds the file's id and a small thumbnail, both saved with the draft. They stay with the box
// through Create prompt (KYO-903), through the box's passing to the next session of the
// project (KYO-763) and through a relaunch, and are sent by their ids (KYO-920). A file still
// uploading goes with its line through Send to prompt and through the undo of a move
// (KYO-936). A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { test } from "node:test";
import { META, SHIFT, openPage, type Page } from "./chrome.ts";

const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const THUMB_A = `data:image/jpeg;base64,${PNG}#a`;
const THUMB_B = `data:image/jpeg;base64,${PNG}#b`;
const A = "a".repeat(64);
const B = "b".repeat(64);
const file = (id: string, thumb: string, name: string) =>
  ({ kind: "file", id, name, path: `/home/composer/attachments/p/s1/${id}.png`, thumb });
/** A scratchpad as the draft of an earlier session saved it, with an image on two lines. */
const PAD = {
  id: "pad", text: "notes\nsee this\nand that", scratch: {},
  marks: [{ line: 1, items: [file(A, THUMB_A, "first.png")] }, { line: 2, items: [file(B, THUMB_B, "second.png")] }],
};
const START = `window.kyo.project({ project: "p", scope: "server/p" })`;
const pause = (ms = 900) => `new Promise((done) => setTimeout(done, ${ms}))`;

/**
 * The images each box shows: for every mark in its gutter, the thumbnails its panel draws and
 * the attachment each stands for. A mark's panel is opened by a press on the mark.
 */
async function shown(page: Page): Promise<{ kind: string; images: [string, string][] }[]> {
  return page.run(`(() => [...document.querySelectorAll("#boxes .box")].map((box) => {
    const images = [];
    for (const mark of box.querySelectorAll(".mark.held")) {
      mark.click();
      for (const thumb of document.querySelectorAll(".mark-panel img.mark-thumb")) {
        images.push([thumb.getAttribute("src"), thumb.dataset.attachment ?? ""]);
      }
    }
    return { kind: box.classList.contains("main") ? "prompt" : "scratch", images };
  }))()`);
}

/** The files of each box of the draft the page last asked the app to save. */
async function savedFiles(page: Page): Promise<{ id: string; files: [number, string, string][] }[]> {
  return page.run(`(window.posted.filter((m) => m.name === "saveDraft").at(-1)?.body.boxes ?? []).map((box) => ({
    id: box.id,
    files: (box.marks ?? []).flatMap((mark) => mark.items.filter((item) => item.kind === "file")
      .map((item) => [mark.line, item.id, item.thumb])),
  }))`);
}

const BOTH: [string, string][] = [[THUMB_A, A], [THUMB_B, B]];

test("a box's images keep their thumbnails through Create prompt, a move to the next session and a relaunch, and are sent (KYO-920.c2)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(START);
    await page.run(`window.kyo.draft(${JSON.stringify({ boxes: [PAD], etag: "e1" })})`);
    assert.deepEqual(await shown(page), [{ kind: "scratch", images: BOTH }, { kind: "prompt", images: [] }]);

    // Create prompt: the scratchpad's words and both images become the prompt.
    await page.run(`document.querySelector("#boxes .box.scratch .box-create").click()`);
    assert.deepEqual(await shown(page), [{ kind: "prompt", images: BOTH }]);
    await page.run(pause());
    assert.deepEqual(await savedFiles(page), [{ id: "pad", files: [[1, A, THUMB_A], [2, B, THUMB_B]] }]);
    const saved = await page.run<unknown[]>(`window.posted.filter((m) => m.name === "saveDraft").at(-1).body.boxes`);

    // The session ends: the app turns the composer to the project's draft, which the server
    // has given the box, and then to the next session's, which has taken it (KYO-763).
    for (const etag of ["project-1", "next-1"]) {
      await page.run(`window.kyo.flushDraft()`);
      await page.run(`window.kyo.draft(${JSON.stringify({ boxes: saved, etag, turned: true })})`);
      assert.deepEqual(await shown(page), [{ kind: "prompt", images: BOTH }], `after the draft ${etag}`);
    }

    // A relaunch: the page opens with the boxes this window kept, then the server's draft.
    await page.run(`location.reload()`).catch(() => {});
    await page.run(pause(500));
    await page.run(START);
    assert.deepEqual(await shown(page), [{ kind: "prompt", images: BOTH }], "from what the window kept");
    await page.run(`window.kyo.draft(${JSON.stringify({ boxes: saved, etag: "next-1" })})`);
    assert.deepEqual(await shown(page), [{ kind: "prompt", images: BOTH }], "from the server's draft");

    // Send names each image by its id on its line.
    await page.run(`document.querySelector("#boxes .box.main .box-send").click()`);
    const sent = await page.run<{ line: number; attachments?: string[] }[]>(
      `window.posted.filter((m) => m.name === "send").at(-1).body.marks`);
    assert.deepEqual(sent.map((mark) => [mark.line, mark.attachments]), [[1, [A]], [2, [B]]]);
  } finally {
    await page.close();
  }
});

test("an image still uploading when Create prompt is pressed lands in the prompt (KYO-920)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(START);
    await page.run(`window.kyo.draft(${JSON.stringify({ boxes: [{ id: "pad", text: "notes\nsee this", scratch: {} }], etag: "e1" })})`);
    await page.run(`window.kyo.composer.select(0, 8)`);
    await page.run(`(() => {
      const data = new DataTransfer();
      data.items.add(new File([Uint8Array.from(atob(${JSON.stringify(PNG)}), (c) => c.charCodeAt(0))], "shot.png", { type: "image/png" }));
      document.activeElement.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
    })()`);
    await page.run(pause(200));
    const ref = await page.run<string>(`window.posted.filter((m) => m.name === "attach").at(-1).body.ref`);

    // The server has not answered yet when the scratchpad becomes the prompt.
    await page.run(`document.querySelector("#boxes .box.scratch .box-create").click()`);
    await page.run(`window.kyo.attached({ ref: ${JSON.stringify(ref)}, id: ${JSON.stringify(A)}, path: "/tmp/shot.png" })`);
    const boxes = await shown(page);
    assert.equal(boxes.length, 1);
    assert.deepEqual(boxes[0].images.map(([src, id]) => [src.startsWith("data:image/"), id]), [[true, A]]);
    await page.run(pause());
    const files = await savedFiles(page);
    assert.deepEqual(files.map((box) => box.files.map(([line, id]) => [line, id])), [[[1, A]]]);
  } finally {
    await page.close();
  }
});

/** Pastes an image into the focused box, and gives the name the page asked the app to upload it under. */
async function pasteImage(page: Page): Promise<string> {
  await page.run(`(() => {
    const data = new DataTransfer();
    data.items.add(new File([Uint8Array.from(atob(${JSON.stringify(PNG)}), (c) => c.charCodeAt(0))], "shot.png", { type: "image/png" }));
    document.activeElement.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  })()`);
  await page.run(pause(200));
  return page.run<string>(`window.posted.filter((m) => m.name === "attach").at(-1).body.ref`);
}

/** Each box's kind with the attachment of every image it shows, and whether one reads as uploading. */
async function attached(page: Page): Promise<[string, string[], boolean][]> {
  const boxes = await shown(page);
  const uploading = await page.run<boolean[]>(`[...document.querySelectorAll("#boxes .box")].map((box) => {
    for (const mark of box.querySelectorAll(".mark.held")) {
      mark.click();
      if (document.querySelector(".mark-panel")?.textContent.includes("(uploading)")) return true;
    }
    return false;
  })`);
  return boxes.map((box, index) => [box.kind, box.images.map(([, id]) => id), uploading[index]]);
}

const sentMarks = `window.posted.filter((m) => m.name === "send").at(-1).body.marks.map((mark) => [mark.line, mark.attachments ?? []])`;

test("Send to prompt takes a file still uploading with its line into a prompt that holds words, a paragraph's as the button's (KYO-936.c2)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    for (const how of ["button", "paragraph"]) {
      await page.reload();
      await page.run(START);
      await page.run(`window.kyo.draft(${JSON.stringify({ boxes: [{ id: "pad", text: "notes\nsee this", scratch: {} }, { id: "m", text: "already here" }], etag: "e1" })})`);
      await page.run(`window.kyo.composer.select(0, 8)`);
      const ref = await pasteImage(page);
      if (how === "button") {
        assert.equal(await page.run(`document.querySelector("#boxes .box.scratch .box-create").textContent`), "Send to prompt");
        await page.run(`document.querySelector("#boxes .box.scratch .box-create").click()`);
      } else {
        await page.run(`(() => { document.querySelector("#boxes .box.scratch .mark.held").click(); document.querySelector(".mark-to-prompt").click(); })()`);
      }
      const line = how === "button" ? 2 : 1;
      assert.deepEqual((await attached(page)).at(-1), ["prompt", [""], true], how);
      // The server's answer lands on the prompt's line, which is saved and sent with it.
      await page.run(`window.kyo.attached({ ref: ${JSON.stringify(ref)}, id: ${JSON.stringify(A)}, path: "/tmp/shot.png" })`);
      assert.deepEqual((await attached(page)).at(-1), ["prompt", [A], false], how);
      await page.run(pause());
      assert.deepEqual((await savedFiles(page)).at(-1)!.files.map(([at, id]) => [at, id]), [[line, A]], how);
      await page.run(`document.querySelector("#boxes .box.main .box-send").click()`);
      assert.deepEqual(await page.run(sentMarks), [[line, [A]]], how);
    }
  } finally {
    await page.close();
  }
});

test("a file uploading during a move that is then undone ends attached to the line it was put on, and is sent once the move is repeated (KYO-936.c3, c4)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    // The server answers after the undo, or between the move and the undo.
    for (const answered of ["after the undo", "before the undo"]) {
      await page.reload();
      await page.run(START);
      await page.run(`window.kyo.draft(${JSON.stringify({ boxes: [
        { id: "pad", text: "notes\nsee this", scratch: {}, marks: [{ line: 0, items: [{ kind: "tag", name: "kept" }] }] },
        { id: "m", text: "already here" }], etag: "e1" })})`);
      await page.run(`window.kyo.composer.select(0, 8)`);
      const ref = await pasteImage(page);
      const answer = `window.kyo.attached({ ref: ${JSON.stringify(ref)}, id: ${JSON.stringify(A)}, path: "/tmp/shot.png" })`;
      await page.run(`document.querySelector("#boxes .box.scratch .box-create").click()`);
      if (answered === "before the undo") await page.run(answer);
      await page.press("z", META);
      if (answered === "after the undo") {
        assert.deepEqual(await attached(page), [["scratch", [""], true], ["prompt", [], false]], answered);
        await page.run(answer);
      }
      // Back in the scratchpad, on the line it was pasted on, with the tag that was there.
      assert.deepEqual(await attached(page), [["scratch", [A], false], ["prompt", [], false]], answered);
      assert.deepEqual(await page.run(`window.kyo.composer.boxes().map((box) => [box.kind, box.text, box.focused])`),
        [["scratch", "notes\nsee this", true], ["prompt", "already here", false]], answered);
      await page.run(pause());
      assert.deepEqual((await savedFiles(page)).map((box) => [box.id, box.files.map(([at, id]) => [at, id])]),
        [["pad", [[1, A]]], ["m", []]], answered);
      // The server's answer is no edit: the move is still repeated, and Send sends the file.
      await page.press("z", META | SHIFT);
      assert.deepEqual(await attached(page), [["prompt", [A], false]], answered);
      await page.run(`document.querySelector("#boxes .box.main .box-send").click()`);
      // The prompt's own line, then the scratchpad's two: the tag's and the file's.
      assert.deepEqual(await page.run(sentMarks), [[1, []], [2, [A]]], answered);
    }
  } finally {
    await page.close();
  }
});

test("a file the server refuses after its move was undone leaves its line, and repeating the move does not put it back (KYO-936.c4)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(START);
    await page.run(`window.kyo.draft(${JSON.stringify({ boxes: [{ id: "pad", text: "notes\nsee this", scratch: {} }], etag: "e1" })})`);
    await page.run(`window.kyo.composer.select(0, 8)`);
    const ref = await pasteImage(page);
    await page.run(`document.querySelector("#boxes .box.scratch .box-create").click()`);
    await page.press("z", META);
    await page.run(`window.kyo.attached({ ref: ${JSON.stringify(ref)}, error: "too large" })`);
    assert.deepEqual(await attached(page), [["scratch", [], false], ["prompt", [], false]]);
    await page.press("z", META | SHIFT);
    assert.deepEqual(await attached(page), [["prompt", [], false]]);
    assert.deepEqual(await page.run(`window.kyo.composer.boxes().map((box) => [box.kind, box.text])`), [["prompt", "notes\nsee this"]]);
  } finally {
    await page.close();
  }
});
