// Run with `npm test`. The composer has one prompt, the last box, and the boxes above it are
// scratchpads: headed by their first line (KYO-917), collapsible, never sent, and moved into the
// prompt by Create prompt (KYO-903). A box's text is read and its caret set through `window.kyo.composer`, since the
// box is an editor (KYO-910). A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { test } from "node:test";
import { META, SHIFT, openPage } from "./chrome.ts";
import { boxSignature, headingAsLine, scratchTitle } from "../src/drafts.ts";

const STATE = `(() => { const texts = window.kyo.composer.boxes().map((box) => box.text);
  return [...document.querySelectorAll("#boxes .box")].map((box, index) => ({
  kind: box.classList.contains("main") ? "prompt" : "scratch",
  text: texts[index],
  heading: box.querySelector(".scratch-title")?.textContent ?? null,
  collapsed: box.classList.contains("collapsed"),
  send: Boolean(box.querySelector(".box-send")),
  create: box.querySelector(".box-create") ? !box.querySelector(".box-create").disabled : null,
})); })()`;
interface Box { kind: string; text: string; heading: string | null; collapsed: boolean; send: boolean; create: boolean | null }
const START = `window.kyo.project({ project: "p", scope: "server/p" });`;
const saved = `window.posted.filter((m) => m.name === "saveDraft").at(-1)?.body.boxes`;
const pause = `new Promise((done) => setTimeout(done, 900))`;

test("a collapsed state is part of what a box holds, and a prompt's signature is as it was", () => {
  const plain = boxSignature({ text: "a" });
  assert.equal(plain, boxSignature({ text: "a", marks: [], scratch: null }));
  const pad = boxSignature({ text: "a", scratch: {} });
  assert.notEqual(pad, plain);
  // An earlier build's empty heading is no heading, and one with words still differs.
  assert.equal(pad, boxSignature({ text: "a", scratch: { heading: "" } }));
  assert.notEqual(pad, boxSignature({ text: "a", scratch: { heading: "h" } }));
  assert.notEqual(pad, boxSignature({ text: "a", scratch: { collapsed: true } }));
  assert.equal(pad, boxSignature({ text: "a", scratch: { collapsed: false } }));
});

test("a scratchpad's head is its first line that holds words, and an earlier build's heading becomes that line once (KYO-917.c1, c3)", () => {
  assert.equal(scratchTitle("\n  \n  - first words  \nmore"), "- first words");
  assert.equal(scratchTitle(" \n"), "");
  const marks = [{ line: 0, items: ["a"] }, { line: 2, items: ["b"] }];
  const moved = headingAsLine("notes\n\nlast", marks, { heading: " topic ", collapsed: true });
  assert.deepEqual(moved, {
    text: "topic\nnotes\n\nlast", marks: [{ line: 1, items: ["a"] }, { line: 3, items: ["b"] }], scratch: { collapsed: true },
  });
  // What it leaves has no heading, so reading it again moves nothing.
  assert.deepEqual(headingAsLine(moved.text, moved.marks, moved.scratch), moved);
  // A heading alone is the text; one already the first line is not added again; none is dropped.
  assert.deepEqual(headingAsLine("", [], { heading: "only" }), { text: "only", marks: [], scratch: {} });
  assert.deepEqual(headingAsLine("topic\nnotes", [], { heading: "topic" }), { text: "topic\nnotes", marks: [], scratch: {} });
  assert.deepEqual(headingAsLine("notes", [], { heading: "  " }), { text: "notes", marks: [], scratch: {} });
  // A box with marks and no words keeps its marks on their line, below the heading.
  assert.deepEqual(headingAsLine("", [{ line: 0, items: ["a"] }], { heading: "h" }),
    { text: "h\n", marks: [{ line: 1, items: ["a"] }], scratch: {} });
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
    // KYO-910.c1: nothing in the composer is a text area, and each box holds one editor.
    const shape = await page.run(`[
      document.querySelectorAll("#composer textarea").length,
      [...document.querySelectorAll("#boxes > .box")].map((box) => box.querySelectorAll(".cm-editor .cm-content[contenteditable=true]").length),
    ]`);
    assert.deepEqual(shape, [0, [1, 1, 1]]);
    // Reading it is not an edit and saves nothing; the next edit saves the boxes as they are.
    await page.run(pause);
    assert.equal(await page.run(`${saved} ?? null`), null);
    await page.run(`window.kyo.composer.select(2, 0)`);
    await page.type("!");
    await page.run(pause);
    const kept = await page.run<{ id: string; scratch?: unknown }[]>(saved);
    assert.deepEqual(kept.map((box) => [box.id, box.scratch ?? null]), [
      ["a", {}], ["b", {}], ["c", null],
    ]);
  } finally { await page.close(); }
});

/** What a scratchpad's head holds: its words, whether any of it can be edited, and what is drawn. */
const HEAD = `(() => { const head = document.querySelector("#boxes .box.scratch .scratch-head");
  return { words: head.querySelector(".scratch-title").textContent,
    editable: [...head.querySelectorAll("*")].some((element) => element.isContentEditable
      || element.matches("input, textarea, select")),
    shown: [...document.querySelector("#boxes .box.scratch").children]
      .filter((child) => getComputedStyle(child).display !== "none").map((child) => child.className) }; })()`;

test("+ adds a scratchpad with the caret in its text, headed by its first line, which is not edited there, and collapsed to it (KYO-917.c1, c2)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START} document.getElementById("new-prompt").click(); })()`);
    // An empty scratchpad is headed "Scratchpad", and the caret is in its text.
    assert.equal((await page.run<{ words: string }>(HEAD)).words, "Scratchpad");
    assert.deepEqual((await page.run<{ kind: string; focused: boolean }[]>(`window.kyo.composer.boxes()`))
      .map((box) => [box.kind, box.focused]), [["scratch", true], ["prompt", false]]);
    await page.press("Enter");
    await page.type("gate evidence");
    await page.press("Enter");
    await page.type("seen working");
    let boxes = await page.run<Box[]>(STATE);
    // The head shows the first line that holds words, and follows it as it is typed.
    assert.deepEqual(boxes.map((box) => [box.kind, box.heading, box.text]), [
      ["scratch", "gate evidence", "\ngate evidence\nseen working"], ["prompt", null, ""],
    ]);
    const open = await page.run<{ words: string; editable: boolean; shown: string[] }>(HEAD);
    assert.deepEqual(open, { words: "gate evidence", editable: false, shown: ["scratch-head", "box-body", "box-actions"] });
    // A press on the head's words collapses the scratchpad to them, as its button does.
    await page.run(`document.querySelector(".scratch-title").click()`);
    assert.deepEqual((await page.run<{ shown: string[] }>(HEAD)).shown, ["scratch-head"]);
    await page.run(pause);
    const kept = await page.run<{ id: string; text: string; scratch: unknown }[]>(saved);
    assert.deepEqual(kept.map((box) => [box.text, box.scratch]), [["\ngate evidence\nseen working", { collapsed: true }]]);
    // The same draft read on another device, or after a relaunch.
    await page.reload();
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(kept)}, etag: "e" }); })()`);
    boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => [box.kind, box.heading, box.collapsed]), [
      ["scratch", "gate evidence", true], ["prompt", null, false],
    ]);
    assert.deepEqual(await page.run(HEAD), { words: "gate evidence", editable: false, shown: ["scratch-head"] });
    await page.run(`document.querySelector(".scratch-toggle").click()`);
    assert.deepEqual((await page.run<{ shown: string[] }>(HEAD)).shown, ["scratch-head", "box-body", "box-actions"]);
  } finally { await page.close(); }
});

test("a scratchpad an earlier build saved with a heading opens with it as its first line, once, and is saved without it (KYO-917.c3)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const older = [
      { id: "a", text: "notes", scratch: { heading: "topic", collapsed: true }, marks: [{ line: 0, items: [{ kind: "tag", name: "kept" }] }] },
      { id: "h", text: "", scratch: { heading: "only a heading" } },
      { id: "b", text: "the prompt" },
    ];
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(older)}, etag: "e" }); })()`);
    let boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => [box.kind, box.heading, box.text, box.collapsed]), [
      ["scratch", "topic", "topic\nnotes", true], ["scratch", "only a heading", "only a heading", false], ["prompt", null, "the prompt", false],
    ]);
    // Reading it is not an edit; the next edit saves each scratchpad with its heading in its
    // text, its mark on the line it was on, and a scratch object with no heading.
    await page.run(pause);
    assert.equal(await page.run(`${saved} ?? null`), null);
    await page.run(`window.kyo.composer.select(2, 10)`);
    await page.type("!");
    await page.run(pause);
    const kept = await page.run<{ id: string; text: string; scratch?: unknown; marks: { line: number }[] }[]>(saved);
    assert.deepEqual(kept.map((box) => [box.id, box.text, box.scratch ?? null, box.marks.map((mark) => mark.line)]), [
      ["a", "topic\nnotes", { collapsed: true }, [1]], ["h", "only a heading", {}, []], ["b", "the prompt!", null, []],
    ]);
    // Read again, as on another device, the heading is not added a second time.
    await page.reload();
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(kept)}, etag: "e2" }); })()`);
    boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => box.text), ["topic\nnotes", "only a heading", "the prompt!"]);
  } finally { await page.close(); }
});

test("Create prompt moves a scratchpad into an empty prompt and is refused while the prompt holds words", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START}
      window.kyo.draft({ boxes: [{ id: "a", text: "topic\\nnotes", scratch: {} }], etag: "e" });
      window.kyo.composer.select(1, 0);
    })()`);
    await page.type("x");
    let boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => box.create), [false, null]);
    await page.run(`document.querySelector(".box-create").click()`);
    assert.deepEqual((await page.run<Box[]>(STATE)).map((box) => box.text), ["topic\nnotes", "x"]);

    await page.press("Backspace", 0, "deleteBackward");
    boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => box.create), [true, null]);
    await page.run(`document.querySelector(".box-create").click()`);
    // The whole text moves, the first line that headed the scratchpad included (KYO-917.c4).
    boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => [box.kind, box.text]), [["prompt", "topic\nnotes"]]);
    await page.run(pause);
    const kept = await page.run<{ id: string; scratch?: unknown }[]>(saved);
    assert.deepEqual(kept.map((box) => [box.id, box.scratch ?? null]), [["a", null]]);
  } finally { await page.close(); }
});

test("Command-Return in a scratchpad sends nothing; in the prompt it sends, and an empty prompt follows (KYO-904)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const sent = `window.posted.filter((m) => m.name === "send").map((m) => m.body.segments.map((s) => s.text).join(""))`;
    await page.run(`(() => { ${START}
      window.kyo.draft({ boxes: [{ id: "a", text: "notes", scratch: {} }, { id: "b", text: "the prompt" }], etag: "e" });
      window.kyo.composer.select(0, 0);
    })()`);
    await page.press("Enter", META);
    await page.run(`window.kyo.composer.select(0, 5)`);
    await page.press("Enter", META);
    assert.deepEqual(await page.run<string[]>(sent), []);
    await page.run(`window.kyo.composer.select(1, 0)`);
    await page.press("Enter", META);
    assert.deepEqual(await page.run<string[]>(sent), ["the prompt"]);
    await page.run(`window.kyo.sent({})`);
    const boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => [box.kind, box.text]), [["scratch", "notes"], ["prompt", ""]]);
  } finally { await page.close(); }
});

test("Send to prompt moves a paragraph with what is nested under it and its marks, by the panel a right-click in the gutter opens (KYO-904)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const marks = (box: string) => `[...document.querySelectorAll("#boxes .box.${box} .mark.held")].map((mark) => mark.title)`;
    await page.run(`(() => { ${START}
      window.kyo.draft({ boxes: [
        { id: "a", text: "- keep\\n- take\\n  - nested\\n- last", scratch: {},
          marks: [{ line: 2, items: [{ kind: "tag", name: "review" }] }, { line: 3, items: [{ kind: "tag", name: "stays" }] }] },
        { id: "b", text: "already here" },
      ], etag: "e" });
      const gutter = document.querySelector("#boxes .box.scratch .cm-gutter.kyo-marks");
      const lines = document.querySelectorAll("#boxes .box.scratch .cm-line");
      const at = lines[1].getBoundingClientRect();
      gutter.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: gutter.getBoundingClientRect().left + 5, clientY: at.top + at.height / 2 }));
      document.querySelector(".mark-to-prompt").click();
    })()`);
    const boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => [box.kind, box.text]), [
      ["scratch", "- keep\n- last"], ["prompt", "already here\n- take\n  - nested"],
    ]);
    assert.deepEqual(await page.run<string[]>(marks("main")), ["#review"]);
    assert.deepEqual(await page.run<string[]>(marks("scratch")), ["#stays"]);
    // The prompt has no such button: its paragraphs are already where they are sent from.
    const inPrompt = await page.run<boolean>(`(() => {
      document.querySelector("#boxes .box.main .mark.held").click();
      return Boolean(document.querySelector(".mark-to-prompt"));
    })()`);
    assert.equal(inPrompt, false);
  } finally { await page.close(); }
});

test("the prompt stays at the foot of the composer and grows to half its height, then scrolls inside", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const long = Array.from({ length: 80 }, (_, line) => `line ${line}`).join("\n");
    const seen = await page.run<{ column: number; prompt: number; bottom: number; foot: number; inner: boolean; send: boolean }>(`(() => { ${START}
      window.kyo.draft({ boxes: [
        { id: "a", text: ${JSON.stringify(long)}, scratch: {} },
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

test("Tab and Shift-Tab indent and outdent the caret's line or the selected lines and stay in the box (KYO-905)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const read = `(() => { const a = window.kyo.composer.boxes().find((box) => box.focused); return a ? [a.text, a.from, a.to, a.kind === "scratch"] : null; })()`;
    await page.run(`(() => { ${START} document.getElementById("new-prompt").click();
      window.kyo.composer.select(0, 0); })()`);
    await page.type("plain");
    await page.press("Tab");  // not a list item: indented all the same, and the caret stays
    assert.deepEqual(await page.run(read), ["  plain", 7, 7, true]);
    await page.press("Tab", SHIFT);
    assert.deepEqual(await page.run(read), ["plain", 5, 5, true]);
    await page.run(`(() => {
      window.kyo.draft({ boxes: [{ id: "s", text: "- a\\n- b\\n3. c\\n- d", scratch: {} }], etag: "e" });
      window.kyo.composer.select(0, 5, 12); })()`);
    await page.press("Tab");  // the selection touches the second and third lines
    assert.deepEqual(await page.run(read), ["- a\n  - b\n  1. c\n- d", 4, 16, true]);
    await page.press("Tab", SHIFT);
    await page.press("Tab", SHIFT);  // nothing left to outdent: still in the box
    assert.deepEqual(await page.run(read), ["- a\n- b\n1. c\n- d", 4, 12, true]);
  } finally { await page.close(); }
});

/** A right-click in the gutter of box `box` (`main` or `scratch`) on its line `line`, counted from 0. */
const gutterMenu = (box: string, line: number) => `(() => {
  const gutter = document.querySelector("#boxes .box.${box} .cm-gutter.kyo-marks");
  const at = document.querySelectorAll("#boxes .box.${box} .cm-line")[${line}].getBoundingClientRect();
  return gutter.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true,
    clientX: gutter.getBoundingClientRect().left + 5, clientY: at.top + at.height / 2 }));
})()`;
/** The panel's buttons, by their words, and whether each is enabled. */
const PANEL = `[...document.querySelectorAll("#boxes .mark-panel button.mark-action, #boxes .mark-panel button.mark-to-prompt")]
  .map((button) => [button.textContent, !button.disabled])`;
const press = (name: string) => `document.querySelector("#boxes .mark-panel .mark-${name}").click()`;
const PROMPT = `(() => { const box = window.kyo.composer.boxes().at(-1); return [box.text, box.from, box.to]; })()`;

test("a right-click in the prompt's gutter opens the panel of the line under the pointer, whose Indent and Outdent move it with its nested lines as Tab does, one undo step each (KYO-911)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const text = "- a\n- b\n  - nested\n\n  - two\n- c";
    await page.run(`(() => { ${START}
      window.kyo.draft({ boxes: [{ id: "p", text: ${JSON.stringify(text)} }], etag: "e" });
      window.kyo.composer.select(0, ${text.length});
    })()`);
    // KYO-911.c1: the right-click is taken, and the panel is the second line's, in the prompt.
    assert.equal(await page.run(gutterMenu("main", 1)), false);
    assert.deepEqual(await page.run(PANEL), [["Indent", true], ["Outdent", true], ["Fold", true]]);
    assert.equal(await page.run(`Boolean(document.querySelector("#boxes .box.main .mark-panel"))`), true);

    // KYO-911.c2: the line and the lines nested under it move, the blank line among them is left
    // alone, the line after the section stays, and the caret stays where it was, moved with its text.
    await page.run(press("indent"));
    const indented = "- a\n  - b\n    - nested\n\n    - two\n- c";
    assert.deepEqual(await page.run(PROMPT), [indented, indented.length, indented.length]);
    // The panel is still open, on the same line, and a second press acts again.
    await page.run(press("indent"));
    const twice = "- a\n    - b\n      - nested\n\n      - two\n- c";
    assert.deepEqual(await page.run(PROMPT), [twice, twice.length, twice.length]);
    await page.run(press("outdent"));
    assert.deepEqual((await page.run<[string]>(PROMPT))[0], indented);

    // As Tab and Shift-Tab act on a selection of those lines.
    const tabbed = await page.run<string>(`(() => {
      window.kyo.draft({ boxes: [{ id: "q", text: ${JSON.stringify(text)} }], etag: "f" });
      window.kyo.composer.select(0, 4, ${text.length - 4});
      return null;
    })()`).then(() => page.press("Tab")).then(() => page.run<[string]>(PROMPT)).then(([typed]) => typed);
    assert.equal(tabbed, indented);
    await page.press("Tab", SHIFT);
    assert.equal((await page.run<[string]>(PROMPT))[0], text);

    // Each press is one step of the box's undo, and an undo puts the caret back where it was.
    await page.run(`window.kyo.composer.select(0, 1)`);
    await page.run(gutterMenu("main", 1));
    await page.run(press("indent"));
    await page.run(press("outdent"));
    await page.run(press("outdent"));
    assert.deepEqual((await page.run<[string]>(PROMPT))[0], "- a\n- b\n- nested\n\n- two\n- c");
    // A press in the box closes the panel, whose tag field has the focus, and focuses the box.
    await page.run(`(() => {
      const content = document.querySelector("#boxes .box.main .cm-content");
      content.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
      content.focus();
    })()`);
    assert.equal(await page.run(`Boolean(document.querySelector("#boxes .mark-panel"))`), false);
    await page.press("z", META, "undo");
    assert.deepEqual(await page.run(PROMPT), [text, 1, 1]);
    await page.press("z", META, "undo");
    assert.deepEqual(await page.run(PROMPT), [indented, 1, 1]);
    await page.press("z", META, "undo");
    assert.deepEqual(await page.run(PROMPT), [text, 1, 1]);

    // Outdent is disabled where none of the lines is indented, and a scratchpad's panel holds
    // the same actions and Send to prompt.
    await page.run(`(() => { window.kyo.draft({ boxes: [
      { id: "s", text: "- x\\n- y", scratch: {} }, { id: "r", text: "z" },
    ], etag: "g" }); })()`);
    await page.run(gutterMenu("scratch", 0));
    assert.deepEqual(await page.run(PANEL), [["Indent", true], ["Outdent", false], ["Send to prompt", true]]);
  } finally { await page.close(); }
});

test("the panel offers Fold or Unfold only on a line that can fold, and it acts as the fold keys do (KYO-911)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const text = "# plan\nwords\n- a\n  - nested\n- b";
    await page.run(`(() => { ${START}
      window.kyo.draft({ boxes: [{ id: "p", text: ${JSON.stringify(text)} }], etag: "e" });
    })()`);
    const offered = async (line: number) => {
      await page.run(gutterMenu("main", line));
      return page.run<string[]>(`[...document.querySelectorAll("#boxes .mark-panel .mark-fold")].map((button) => button.textContent)`);
    };
    // A heading and an item with nested lines fold; a plain line, a nested item with nothing
    // under it, and the last item do not.
    assert.deepEqual(await offered(0), ["Fold"]);
    assert.deepEqual(await offered(1), []);
    assert.deepEqual(await offered(3), []);
    assert.deepEqual(await offered(4), []);
    assert.deepEqual(await offered(2), ["Fold"]);

    // Fold folds the line's section, as Command-Option-[ does at the caret, and the panel
    // then offers Unfold, which unfolds it as Command-Option-] does.
    const folds = `window.kyo.composer.folds(0)`;
    await page.run(press("fold"));
    const byPanel = await page.run<number[]>(folds);
    assert.deepEqual(byPanel, [2]);
    assert.deepEqual(await page.run(`[...document.querySelectorAll("#boxes .mark-panel .mark-fold")].map((button) => button.textContent)`), ["Unfold"]);
    await page.run(press("fold"));
    assert.deepEqual(await page.run(folds), []);
    await page.run(`window.kyo.composer.select(0, ${text.indexOf("- a")})`);
    await page.press("[", META | 1);
    assert.deepEqual(await page.run(folds), byPanel);
    await page.press("]", META | 1);
    assert.deepEqual(await page.run(folds), []);
    // A heading folds the whole of its section, and the text is unchanged by either.
    await page.run(gutterMenu("main", 0));
    await page.run(press("fold"));
    assert.deepEqual(await page.run(folds), [0]);
    assert.deepEqual(await page.run<number>(`document.querySelectorAll("#boxes .box.main .cm-line").length`), 1);
    assert.equal((await page.run<[string]>(PROMPT))[0], text);
  } finally { await page.close(); }
});

test("Create prompt on a scratchpad of one line moves that line, which headed it, into the prompt (KYO-916, KYO-917.c4)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START} document.getElementById("new-prompt").click(); })()`);
    await page.type("Create prompt test");  // the caret is in the text after +
    assert.equal((await page.run<{ words: string }>(HEAD)).words, "Create prompt test");
    await page.run(`document.querySelector(".box-create").click()`);
    const boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => [box.kind, box.text]), [["prompt", "Create prompt test"]]);
  } finally { await page.close(); }
});
