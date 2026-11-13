// Run with `npm test`. The composer has one prompt, the last box, and the boxes above it are
// scratchpads: headed, collapsible, never sent, and moved into the prompt by Create prompt
// (KYO-903). A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { test } from "node:test";
import { META, openPage } from "./chrome.ts";
import { boxSignature } from "../src/drafts.ts";

const STATE = `(() => [...document.querySelectorAll("#boxes .box")].map((box) => ({
  kind: box.classList.contains("main") ? "prompt" : "scratch",
  text: box.querySelector("textarea").value,
  heading: box.querySelector(".scratch-heading")?.value ?? null,
  collapsed: box.classList.contains("collapsed"),
  send: Boolean(box.querySelector(".box-send")),
  create: box.querySelector(".box-create") ? !box.querySelector(".box-create").disabled : null,
})))()`;
interface Box { kind: string; text: string; heading: string | null; collapsed: boolean; send: boolean; create: boolean | null }
const START = `window.kyo.project({ project: "p", scope: "server/p" });`;
const saved = `window.posted.filter((m) => m.name === "saveDraft").at(-1)?.body.boxes`;
const pause = `new Promise((done) => setTimeout(done, 900))`;

test("a heading and a collapsed state are part of what a box holds, and a prompt's signature is as it was", () => {
  const plain = boxSignature({ text: "a" });
  assert.equal(plain, boxSignature({ text: "a", marks: [], scratch: null }));
  const pad = boxSignature({ text: "a", scratch: { heading: "" } });
  assert.notEqual(pad, plain);
  assert.notEqual(pad, boxSignature({ text: "a", scratch: { heading: "h" } }));
  assert.notEqual(pad, boxSignature({ text: "a", scratch: { heading: "", collapsed: true } }));
  assert.equal(pad, boxSignature({ text: "a", scratch: { heading: "", collapsed: false } }));
});

test("an older draft's last prompt is the prompt and the others become scratchpads, saved with the next edit", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START}
      window.kyo.draft({ boxes: [{ id: "a", text: "long notes" }, { id: "b", text: "more" }, { id: "c", text: "next" }], etag: "e" });
    })()`);
    const boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => [box.kind, box.text, box.send]), [
      ["scratch", "long notes", false], ["scratch", "more", false], ["prompt", "next", true],
    ]);
    // Reading it is not an edit and saves nothing; the next edit saves the boxes as they are.
    await page.run(pause);
    assert.equal(await page.run(`${saved} ?? null`), null);
    await page.run(`document.querySelector("#boxes .box.main textarea").focus()`);
    await page.type("!");
    await page.run(pause);
    const kept = await page.run<{ id: string; scratch?: unknown }[]>(saved);
    assert.deepEqual(kept.map((box) => [box.id, box.scratch ?? null]), [
      ["a", { heading: "" }], ["b", { heading: "" }], ["c", null],
    ]);
  } finally { await page.close(); }
});

test("+ adds a scratchpad above the prompt, whose heading is saved and whose state survives a reload of the draft", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START} document.getElementById("new-prompt").click(); })()`);
    await page.type("gate evidence");
    await page.press("Enter");
    await page.type("seen working");
    let boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => [box.kind, box.heading, box.text]), [
      ["scratch", "gate evidence", "seen working"], ["prompt", null, ""],
    ]);
    await page.run(`document.querySelector(".scratch-toggle").click()`);
    await page.run(pause);
    const kept = await page.run<{ id: string; text: string; scratch: unknown }[]>(saved);
    assert.deepEqual(kept.map((box) => [box.text, box.scratch]), [
      ["seen working", { heading: "gate evidence", collapsed: true }],
    ]);
    // The same draft read on another device, or after a relaunch.
    await page.reload();
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(kept)}, etag: "e" }); })()`);
    boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => [box.kind, box.heading, box.collapsed]), [
      ["scratch", "gate evidence", true], ["prompt", null, false],
    ]);
    const hidden = await page.run<string>(`getComputedStyle(document.querySelector(".box.scratch .box-body")).display`);
    assert.equal(hidden, "none");
  } finally { await page.close(); }
});

test("Create prompt moves a scratchpad into an empty prompt and is refused while the prompt holds words", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START}
      window.kyo.draft({ boxes: [{ id: "a", text: "notes", scratch: { heading: "topic" } }], etag: "e" });
      document.querySelector("#boxes .box.main textarea").focus();
    })()`);
    await page.type("x");
    let boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => box.create), [false, null]);
    await page.run(`document.querySelector(".box-create").click()`);
    assert.deepEqual((await page.run<Box[]>(STATE)).map((box) => box.text), ["notes", "x"]);

    await page.press("Backspace", 0, "deleteBackward");
    boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => box.create), [true, null]);
    await page.run(`document.querySelector(".box-create").click()`);
    boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => [box.kind, box.text]), [["prompt", "notes"]]);
    await page.run(pause);
    const kept = await page.run<{ id: string; scratch?: unknown }[]>(saved);
    assert.deepEqual(kept.map((box) => [box.id, box.scratch ?? null]), [["a", null]]);
  } finally { await page.close(); }
});

test("Command-Return in a scratchpad sends the prompt, and after a send an empty prompt is there", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START}
      window.kyo.draft({ boxes: [{ id: "a", text: "notes", scratch: { heading: "" } }, { id: "b", text: "the prompt" }], etag: "e" });
      document.querySelector("#boxes .box.scratch textarea").focus();
    })()`);
    await page.press("Enter", META);
    const sent = await page.run<string[]>(
      `window.posted.filter((m) => m.name === "send").map((m) => m.body.segments.map((s) => s.text).join(""))`);
    assert.deepEqual(sent, ["the prompt"]);
    await page.run(`window.kyo.sent({})`);
    const boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => [box.kind, box.text]), [["scratch", "notes"], ["prompt", ""]]);
  } finally { await page.close(); }
});

test("the prompt stays at the foot of the composer and grows to half its height, then scrolls inside", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const long = Array.from({ length: 80 }, (_, line) => `line ${line}`).join("\n");
    const seen = await page.run<{ column: number; prompt: number; bottom: number; foot: number; inner: boolean; send: boolean }>(`(() => { ${START}
      window.kyo.draft({ boxes: [
        { id: "a", text: ${JSON.stringify(long)}, scratch: { heading: "long" } },
        { id: "b", text: ${JSON.stringify(long)} },
      ], etag: "e" });
      const column = document.getElementById("boxes");
      column.scrollTop = 0;
      const box = document.querySelector("#boxes .box.main");
      const body = box.querySelector(".box-body");
      const send = box.querySelector(".box-send").getBoundingClientRect();
      return {
        column: column.clientHeight, prompt: box.offsetHeight,
        bottom: Math.round(box.getBoundingClientRect().bottom), foot: Math.round(column.getBoundingClientRect().bottom),
        inner: body.scrollHeight > body.clientHeight,
        send: send.bottom <= column.getBoundingClientRect().bottom && send.height > 0,
      };
    })()`);
    assert.ok(Math.abs(seen.prompt - seen.column / 2) <= 2, `${seen.prompt} of ${seen.column}`);
    assert.equal(seen.bottom, seen.foot);  // in view although the column is scrolled to its top
    assert.deepEqual([seen.inner, seen.send], [true, true]);
  } finally { await page.close(); }
});
