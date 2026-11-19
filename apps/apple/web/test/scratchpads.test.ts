// Run with `npm test`. The composer has one prompt, the last box, and the boxes above it are
// scratchpads: headed by their first line (KYO-917), one of them open at a time (KYO-936), never
// sent, and moved into the prompt by Create prompt or Send to prompt (KYO-903, KYO-936), which
// Command-Z takes back. A box's text is read and its caret set through `window.kyo.composer`, since the
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

test("a scratchpad differs from a prompt of the same words, and not by a collapsed state an earlier build saved (KYO-936.c1)", () => {
  const plain = boxSignature({ text: "a" });
  assert.equal(plain, boxSignature({ text: "a", marks: [], scratch: null }));
  const pad = boxSignature({ text: "a", scratch: {} });
  assert.notEqual(pad, plain);
  // An earlier build's empty heading is no heading, and one with words still differs.
  assert.equal(pad, boxSignature({ text: "a", scratch: { heading: "" } }));
  assert.notEqual(pad, boxSignature({ text: "a", scratch: { heading: "h" } }));
  // Which scratchpad is open is each window's own: a copy saved collapsed is the same box.
  assert.equal(pad, boxSignature({ text: "a", scratch: { collapsed: true } }));
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
      [...document.querySelectorAll("#boxes .box")].map((box) => box.querySelectorAll(".cm-editor .cm-content[contenteditable=true]").length),
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
    await page.run(`(() => { ${START} document.getElementById("new-scratch").click(); })()`);
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
    // KYO-936: which scratchpad is open is not saved.
    assert.deepEqual(kept.map((box) => [box.text, box.scratch]), [["\ngate evidence\nseen working", {}]]);
    // The same draft read on another device, or after a relaunch: no caret has been in the
    // scratchpad there, so it is collapsed.
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
      ["scratch", "topic", "topic\nnotes", true], ["scratch", "only a heading", "only a heading", true], ["prompt", null, "the prompt", false],
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
      ["a", "topic\nnotes", {}, [1]], ["h", "only a heading", {}, []], ["b", "the prompt!", null, []],
    ]);
    // Read again, as on another device, the heading is not added a second time.
    await page.reload();
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(kept)}, etag: "e2" }); })()`);
    boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => box.text), ["topic\nnotes", "only a heading", "the prompt!"]);
  } finally { await page.close(); }
});

/** Each scratchpad's button: what it reads and whether it takes a press. */
const BUTTONS = `[...document.querySelectorAll("#boxes .box.scratch .box-create")].map((button) => [button.textContent, button.disabled])`;
/** The titles of the marks each box's gutter shows, in the boxes' order. */
const MARKS = `[...document.querySelectorAll("#boxes .box")].map((box) => [...box.querySelectorAll(".mark.held")].map((mark) => mark.title))`;
const OPEN = `[...document.querySelectorAll("#scratches > .box")].map((box) => !box.classList.contains("collapsed"))`;
const FOCUSED = `window.kyo.composer.boxes().findIndex((box) => box.focused)`;
const ALT = 1;

test("one scratchpad is open at a time: the caret put in one, or + adding one, opens it and collapses the others, and a press on a collapsed head opens that one (KYO-936.c1)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    // A draft saved by an earlier build, with the second scratchpad collapsed and the others open.
    const draft = [
      { id: "a", text: "alpha\nnotes", scratch: {} }, { id: "b", text: "beta\nnotes", scratch: { collapsed: true } },
      { id: "c", text: "gamma\nnotes", scratch: {} }, { id: "m", text: "the prompt" },
    ];
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(draft)}, etag: "e" }); })()`);
    // No caret has been in any of them here: none is open, whatever the draft says.
    assert.deepEqual(await page.run(OPEN), [false, false, false]);
    await page.run(`window.kyo.composer.select(0, 2)`);
    assert.deepEqual(await page.run(OPEN), [true, false, false]);
    // The focus put in another by the page, as a press in its text puts it.
    await page.run(`window.kyo.composer.select(2, 1)`);
    assert.deepEqual(await page.run(OPEN), [false, false, true]);
    // A press on a collapsed head opens that one with the caret in it; on the open head, it collapses.
    await page.run(`document.querySelectorAll("#scratches .scratch-title")[1].click()`);
    assert.deepEqual(await page.run(OPEN), [false, true, false]);
    assert.equal(await page.run(FOCUSED), 1);
    assert.deepEqual(await page.run(`[...document.querySelectorAll("#scratches .scratch-toggle")].map((toggle) => toggle.getAttribute("aria-expanded"))`),
      ["false", "true", "false"]);
    await page.run(`document.querySelectorAll("#scratches .scratch-toggle")[1].click()`);
    assert.deepEqual(await page.run(OPEN), [false, false, false]);
    assert.equal(await page.run(FOCUSED), -1);
    // None of this is an edit: nothing was saved, and another device's copy that differs only
    // in a collapsed state changes nothing here.
    await page.run(pause);
    assert.equal(await page.run(`${saved} ?? null`), null);
    await page.run(`window.kyo.composer.select(0, 2)`);
    await page.run(`window.kyo.draftElsewhere({ boxes: ${JSON.stringify(draft.map((box) => (box.scratch ? { ...box, scratch: { collapsed: true } } : box)))}, etag: "e2" })`);
    assert.deepEqual(await page.run(OPEN), [true, false, false]);
    assert.equal(await page.run(FOCUSED), 0);
    await page.run(pause);
    assert.equal(await page.run(`${saved} ?? null`), null);
    // + adds one, open, with the caret in it.
    await page.run(`window.kyo.composer.select(0, 0)`);
    await page.run(`document.getElementById("new-scratch").click()`);
    assert.deepEqual(await page.run(OPEN), [false, false, false, true]);
    assert.equal(await page.run(FOCUSED), 3);
    // The caret in the prompt leaves the open one open, for when the scratchpads are gone back
    // to, once it holds words (an empty one leaves with the caret, KYO-939).
    await page.type("n");
    await page.run(`window.kyo.composer.select(4, 0)`);
    assert.deepEqual(await page.run(OPEN), [false, false, false, true]);
  } finally { await page.close(); }
});

test("a scratchpad's button reads Create prompt while the prompt is empty and Send to prompt while it holds words or marks, and Send to prompt appends the whole scratchpad with its marks (KYO-936.c2)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START}
      window.kyo.draft({ boxes: [
        { id: "a", text: "  topic\\n\\n  notes", scratch: {}, marks: [{ line: 0, items: [{ kind: "tag", name: "first" }] }, { line: 2, items: [{ kind: "tag", name: "last" }] }] },
        { id: "z", text: "other", scratch: {} },
      ], etag: "e" });
    })()`);
    assert.deepEqual(await page.run(BUTTONS), [["Create prompt", false], ["Create prompt", false]]);
    // A mark alone makes the prompt hold something.
    await page.run(`(() => { window.kyo.composer.select(2, 0);
      document.querySelector("#boxes .box.main .cm-gutter.kyo-marks").dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true,
        clientX: document.querySelector("#boxes .box.main .cm-gutter.kyo-marks").getBoundingClientRect().left + 5,
        clientY: document.querySelector("#boxes .box.main .cm-line").getBoundingClientRect().top + 4 }));
      const field = document.querySelector(".mark-panel input");
      field.value = "held";
      field.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    })()`);
    assert.deepEqual(await page.run(BUTTONS), [["Send to prompt", false], ["Send to prompt", false]]);
    await page.run(`window.kyo.composer.select(2, 0)`);
    await page.type("already here");
    assert.deepEqual(await page.run(BUTTONS), [["Send to prompt", false], ["Send to prompt", false]]);

    await page.run(`document.querySelector("#boxes .box.scratch .box-create").click()`);
    // The whole text goes after the prompt's last line as it was typed, its indent and its
    // blank line kept, each mark on its line, and the scratchpad leaves.
    const boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => [box.kind, box.text]), [["scratch", "other"], ["prompt", "already here\n  topic\n\n  notes"]]);
    assert.deepEqual(await page.run(MARKS), [[], ["#held", "#first", "#last"]]);
    // The caret is at the prompt's end.
    assert.deepEqual(await page.run(`(() => { const box = window.kyo.composer.boxes()[1]; return [box.focused, box.from]; })()`),
      [true, "already here\n  topic\n\n  notes".length]);
    await page.run(pause);
    const kept = await page.run<{ id: string; scratch?: unknown; marks?: { line: number }[] }[]>(saved);
    // The prompt keeps its own id, which is none of the scratchpads'.
    assert.deepEqual(kept.map((box) => [["a", "z"].includes(box.id), box.scratch ?? null, (box.marks ?? []).map((mark) => mark.line)]),
      [[true, {}, []], [false, null, [0, 1, 3]]]);
    assert.equal(kept[0].id, "z");

    // Emptied, the prompt is created from the next scratchpad, under that scratchpad's id.
    await page.run(`window.kyo.composer.select(1, 0, ${"already here\n  topic\n\n  notes".length})`);
    await page.press("Backspace", 0, "deleteBackward");
    await page.run(`(() => { for (let left = 5; left > 0 && document.querySelector("#boxes .box.main .mark.held"); left -= 1) {
      document.querySelector("#boxes .box.main .mark.held").click();
      document.querySelector(".mark-panel .mark-remove").click();
    } })()`);
    assert.deepEqual(await page.run(BUTTONS), [["Create prompt", false]]);
    await page.run(`document.querySelector(".box-create").click()`);
    assert.deepEqual((await page.run<Box[]>(STATE)).map((box) => [box.kind, box.text]), [["prompt", "other"]]);
    await page.run(pause);
    assert.deepEqual((await page.run<{ id: string; scratch?: unknown }[]>(saved)).map((box) => [box.id, box.scratch ?? null]), [["z", null]]);
  } finally { await page.close(); }
});

/** What the composer holds that a move must put back: each box, its marks, its folds, the open scratchpad and the caret. */
const HELD = `(() => { const all = window.kyo.composer.boxes(); const marks = ${MARKS};
  return { boxes: all.map((box, index) => [box.kind, box.id, box.text, marks[index], window.kyo.composer.folds(index)]),
    open: ${OPEN}, caret: all.flatMap((box) => (box.focused ? [[box.id, box.from, box.to]] : [])) }; })()`;
/** The last draft the page asked the app to save: each box's id, kind, text and the lines its marks are on. */
const SAVED = `(${saved} ?? []).map((box) => [box.id, box.scratch ? "scratch" : "prompt", box.text, (box.marks ?? []).map((mark) => [mark.line, mark.items.map((item) => item.name)])])`;
const PADS = [
  { id: "a", text: "alpha", scratch: {} },
  { id: "b", text: "# plan\n- keep\n- take\n  - nested\n- last", scratch: {},
    marks: [{ line: 0, items: [{ kind: "tag", name: "top" }] }, { line: 3, items: [{ kind: "tag", name: "review" }] }, { line: 4, items: [{ kind: "tag", name: "stays" }] }] },
  { id: "c", text: "gamma", scratch: {} },
];
const undo = (page: { press: (key: string, modifiers?: number, command?: string) => Promise<void> }) => page.press("z", META);
const redo = (page: { press: (key: string, modifiers?: number, command?: string) => Promise<void> }) => page.press("z", META | SHIFT);

/** Opens the three scratchpads, the middle one with a fold on "- take" and the caret in it at 9. */
async function openPads(page: NonNullable<Awaited<ReturnType<typeof openPage>>>, prompt: object[]): Promise<void> {
  await page.reload();
  await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify([...PADS, ...prompt])}, etag: "e" }); })()`);
  await page.run(`window.kyo.composer.select(1, ${"# plan\n- keep\n- t".length})`);
  await page.press("[", META | ALT);
  await page.run(`window.kyo.composer.select(1, 9)`);
}

test("Command-Z after Create prompt puts both boxes back with marks, folds, the scratchpad's place and the caret; Command-Shift-Z repeats it, and each is saved (KYO-936.c3, c5)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await openPads(page, []);
    const before = await page.run<{ boxes: unknown[]; open: boolean[]; caret: unknown[] }>(HELD);
    assert.deepEqual(before.boxes.map((box) => (box as unknown[])[4]), [[], [2], [], []]);
    assert.deepEqual([before.open, before.caret], [[false, true, false], [["b", 9, 9]]]);

    await page.run(`document.querySelectorAll("#boxes .box.scratch .box-create")[1].click()`);
    const after = await page.run<{ boxes: unknown[]; open: boolean[]; caret: unknown[] }>(HELD);
    assert.deepEqual(after.boxes, [
      ["scratch", "a", "alpha", [], []], ["scratch", "c", "gamma", [], []],
      // The mark of the line hidden under the fold is not drawn in the gutter; it is saved below.
      ["prompt", "b", PADS[1].text, ["#top", "#stays"], [2]],
    ]);
    assert.deepEqual(after.caret, [["b", PADS[1].text.length, PADS[1].text.length]]);
    await page.run(pause);
    const savedAfter = await page.run(SAVED);
    assert.deepEqual(savedAfter, [["a", "scratch", "alpha", []], ["c", "scratch", "gamma", []],
      ["b", "prompt", PADS[1].text, [[0, ["top"]], [3, ["review"]], [4, ["stays"]]]]]);

    await undo(page);
    assert.deepEqual(await page.run(HELD), before);
    await page.run(pause);
    assert.deepEqual(await page.run(SAVED), [["a", "scratch", "alpha", []],
      ["b", "scratch", PADS[1].text, [[0, ["top"]], [3, ["review"]], [4, ["stays"]]]], ["c", "scratch", "gamma", []]]);

    await redo(page);
    assert.deepEqual(await page.run(HELD), after);
    await page.run(pause);
    assert.deepEqual(await page.run(SAVED), savedAfter);
    // And back once more: the one move is undone and repeated as often as it is asked for.
    await undo(page);
    assert.deepEqual(await page.run(HELD), before);
  } finally { await page.close(); }
});

test("Command-Z after Send to prompt, the button's and a paragraph's, puts both boxes back, and Command-Shift-Z repeats it (KYO-936.c3, c5)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    const prompt = { id: "m", text: "- ask\n  - sub\nalready here", marks: [{ line: 2, items: [{ kind: "tag", name: "mine" }] }] };
    await openPads(page, [prompt]);
    // The prompt holds a fold of its own, and the caret goes back to the scratchpad.
    await page.run(`window.kyo.composer.select(3, 0)`);
    await page.press("[", META | ALT);
    await page.run(`window.kyo.composer.select(1, 9)`);
    const before = await page.run<{ boxes: unknown[]; open: boolean[]; caret: unknown[] }>(HELD);
    assert.deepEqual(before.boxes.map((box) => (box as unknown[])[4]), [[], [2], [], [0]]);

    // The button: the whole scratchpad goes after the prompt's last line.
    await page.run(`document.querySelectorAll("#boxes .box.scratch .box-create")[1].click()`);
    const whole = `${prompt.text}\n${PADS[1].text}`;
    const sent = await page.run<{ boxes: unknown[] }>(HELD);
    assert.deepEqual(sent, {
      boxes: [["scratch", "a", "alpha", [], []], ["scratch", "c", "gamma", [], []],
        ["prompt", "m", whole, ["#mine", "#top", "#stays"], [0, 5]]],
      open: [false, false], caret: [["m", whole.length, whole.length]],
    });
    await undo(page);
    assert.deepEqual(await page.run(HELD), before);
    await page.run(pause);
    assert.deepEqual(await page.run(SAVED), [["a", "scratch", "alpha", []],
      ["b", "scratch", PADS[1].text, [[0, ["top"]], [3, ["review"]], [4, ["stays"]]]], ["c", "scratch", "gamma", []],
      ["m", "prompt", prompt.text, [[2, ["mine"]]]]]);
    await redo(page);
    assert.deepEqual(await page.run(HELD), sent);
    await page.run(pause);
    assert.deepEqual(await page.run(SAVED), [["a", "scratch", "alpha", []], ["c", "scratch", "gamma", []],
      ["m", "prompt", whole, [[2, ["mine"]], [3, ["top"]], [6, ["review"]], [7, ["stays"]]]]]);
    await undo(page);
    assert.deepEqual(await page.run(HELD), before);

    // A paragraph's, from the gutter's panel: "- take" goes with what is nested under it, its
    // mark and its fold; the rest of the scratchpad stays.
    await page.run(`(() => {
      const gutter = document.querySelectorAll("#boxes .box.scratch")[1].querySelector(".cm-gutter.kyo-marks");
      const at = document.querySelectorAll("#boxes .box.scratch")[1].querySelectorAll(".cm-line")[2].getBoundingClientRect();
      gutter.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: gutter.getBoundingClientRect().left + 5, clientY: at.top + at.height / 2 }));
      document.querySelector(".mark-to-prompt").click();
    })()`);
    const part = `${prompt.text}\n- take\n  - nested`;
    const moved = await page.run<{ boxes: unknown[] }>(HELD);
    assert.deepEqual(moved.boxes, [["scratch", "a", "alpha", [], []],
      ["scratch", "b", "# plan\n- keep\n- last", ["#top", "#stays"], []], ["scratch", "c", "gamma", [], []],
      ["prompt", "m", part, ["#mine"], [0, 3]]]);
    await undo(page);
    assert.deepEqual(await page.run(HELD), before);
    await redo(page);
    assert.deepEqual(await page.run(HELD), moved);
    await page.run(pause);
    assert.deepEqual(await page.run(SAVED), [["a", "scratch", "alpha", []],
      ["b", "scratch", "# plan\n- keep\n- last", [[0, ["top"]], [2, ["stays"]]]], ["c", "scratch", "gamma", []],
      ["m", "prompt", part, [[2, ["mine"]], [4, ["review"]]]]]);
  } finally { await page.close(); }
});

test("an edit after a move ends the chance to undo it: Command-Z then acts on the box's own history, and Edit > Undo's command reaches a move as the key does (KYO-936.c3, c7)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await openPads(page, []);
    const before = await page.run(HELD);
    await page.run(`document.querySelectorAll("#boxes .box.scratch .box-create")[1].click()`);
    const after = await page.run<{ boxes: unknown[] }>(HELD);
    // The editing commands Edit > Undo and Edit > Redo raise in the focused box.
    const command = (type: string) => page.run(`document.activeElement.dispatchEvent(new InputEvent("beforeinput", { inputType: ${JSON.stringify(type)}, bubbles: true, cancelable: true }))`);
    assert.equal(await command("historyUndo"), false);
    assert.deepEqual(await page.run(HELD), before);
    assert.equal(await command("historyRedo"), false);
    assert.deepEqual(await page.run(HELD), after);
    // A letter typed in the prompt: Command-Z takes the letter back and leaves the move.
    await page.type("!");
    await undo(page);
    assert.deepEqual((await page.run<{ boxes: unknown[] }>(HELD)).boxes, after.boxes);
    await undo(page);
    assert.deepEqual((await page.run<{ boxes: unknown[] }>(HELD)).boxes, after.boxes);

    // Undone, then a letter typed in a scratchpad: Command-Shift-Z no longer repeats the move.
    await openPads(page, []);
    const again = await page.run(HELD);
    await page.run(`document.querySelectorAll("#boxes .box.scratch .box-create")[1].click()`);
    await undo(page);
    assert.deepEqual(await page.run(HELD), again);
    await page.run(`window.kyo.composer.select(0, 5)`);
    await page.type("!");
    await redo(page);
    assert.deepEqual((await page.run<Box[]>(STATE)).map((box) => [box.kind, box.text]),
      [["scratch", "alpha!"], ["scratch", PADS[1].text], ["scratch", "gamma"], ["prompt", ""]]);
  } finally { await page.close(); }
});

test("Edit > Undo directly after a move takes it back as Command-Z does, Edit > Redo repeats it, and the app is told there is a step (KYO-915.c4)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    // What the page last told the app its Undo and Redo items would act on.
    const told = `new Promise((done) => setTimeout(() => done(window.posted.filter((m) => m.name === "undo").at(-1)?.body), 30))`;
    for (const button of ["Create prompt", "Send to prompt"]) {
      await openPads(page, button === "Send to prompt" ? [{ id: "m", text: "already here" }] : []);
      const before = await page.run(HELD);
      // The boxes are newly drawn from the draft, so no box's own history holds a step.
      assert.deepEqual(await page.run(told), { box: true, undo: false, redo: false }, button);
      assert.equal(await page.run(`document.querySelectorAll("#boxes .box.scratch .box-create")[1].textContent`), button);
      await page.run(`document.querySelectorAll("#boxes .box.scratch .box-create")[1].click()`);
      const after = await page.run(HELD);
      assert.notDeepEqual(after, before, button);
      assert.deepEqual(await page.run(told), { box: true, undo: true, redo: false }, button);
      // The calls the app makes when Edit > Undo and Edit > Redo are chosen.
      assert.equal(await page.run(`window.kyo.undo()`), true, button);
      assert.deepEqual(await page.run(HELD), before, button);
      assert.deepEqual(await page.run(told), { box: true, undo: false, redo: true }, button);
      assert.equal(await page.run(`window.kyo.redo()`), true, button);
      assert.deepEqual(await page.run(HELD), after, button);
      assert.deepEqual(await page.run(told), { box: true, undo: true, redo: false }, button);
      // A letter typed ends the chance: Undo then takes back the letter and leaves the move.
      await page.type("!");
      assert.equal(await page.run(`window.kyo.undo()`), true, button);
      assert.deepEqual(await page.run(HELD), after, button);
      assert.deepEqual(await page.run(told), { box: true, undo: false, redo: true }, button);
    }
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
      window.kyo.composer.select(0, 0);
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

// KYO-935: the prompt in use grows to what the other sections' summaries leave, where it once
// stopped at two thirds of the column (KYO-925) and before that at half.
test("the prompt stays at the foot of the composer and grows to what the scratchpad's head leaves, then scrolls inside", async (t) => {
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
    assert.ok(seen.prompt > seen.column * 0.66 && seen.prompt < seen.column, `${seen.prompt} of ${seen.column}`);
    assert.equal(seen.bottom, seen.foot);  // in view although the column is scrolled to its top
    assert.deepEqual([seen.inner, seen.send], [true, true]);
  } finally { await page.close(); }
});

test("Tab and Shift-Tab indent and outdent the caret's line or the selected lines and stay in the box (KYO-905)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const read = `(() => { const a = window.kyo.composer.boxes().find((box) => box.focused); return a ? [a.text, a.from, a.to, a.kind === "scratch"] : null; })()`;
    await page.run(`(() => { ${START} document.getElementById("new-scratch").click();
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
    await page.run(`(() => { ${START} document.getElementById("new-scratch").click(); })()`);
    await page.type("Create prompt test");  // the caret is in the text after +
    assert.equal((await page.run<{ words: string }>(HEAD)).words, "Create prompt test");
    await page.run(`document.querySelector(".box-create").click()`);
    const boxes = await page.run<Box[]>(STATE);
    assert.deepEqual(boxes.map((box) => [box.kind, box.text]), [["prompt", "Create prompt test"]]);
  } finally { await page.close(); }
});

const IDS = `window.kyo.composer.boxes().map((box) => box.id)`;
/** Puts the focus in the box at an index, as a press in its text does. */
const into = (index: number) => `window.kyo.composer.select(${index}, 0)`;

test("a scratchpad that holds no words and no marks leaves when the caret is put in another box or in the reminder list, and one with words, or a mark on an empty line, stays (KYO-939.c1, c3)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START}
      window.kyo.draft({ boxes: [
        { id: "words", text: "kept", scratch: {} },
        { id: "blank", text: "\\n \\n  ", scratch: {} },
        { id: "marked", text: "", marks: [{ line: 0, items: [{ kind: "tag", name: "t" }] }], scratch: {} },
        { id: "p", text: "prompt" }], etag: "e" });
    })()`);
    // The caret in the prompt: the blank scratchpad goes, the one with words and the one with
    // a mark on an empty line stay.
    await page.run(into(3));
    assert.deepEqual(await page.run(IDS), ["words", "marked", "p"]);

    // Another scratchpad's caret takes an abandoned one too: + adds an empty scratchpad, and
    // the caret put in another scratchpad removes it.
    await page.run(`document.getElementById("new-scratch").click()`);
    assert.equal((await page.run<string[]>(IDS)).length, 4);
    await page.run(into(0));
    assert.deepEqual(await page.run(IDS), ["words", "marked", "p"]);

    // The reminder list's fields take it as well.
    await page.run(`document.getElementById("new-scratch").click()`);
    assert.equal((await page.run<string[]>(IDS)).length, 4);
    await page.run(`window.kyo.agenda({ state: "shown", session_id: "s", items: [], etag: null })`);
    await page.run(`document.getElementById("agenda-summary").click()`);
    assert.deepEqual(await page.run(IDS), ["words", "marked", "p"]);
  } finally { await page.close(); }
});

test("an empty scratchpad stays while the caret is in it and while the focus is outside the composer (KYO-939.c2)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START} document.getElementById("new-scratch").click(); })()`);
    assert.deepEqual((await page.run<string[]>(IDS)).length, 2);
    // Its text emptied while the caret is in it: it stays.
    await page.type("x");
    await page.press("Backspace");
    assert.equal((await page.run<string[]>(IDS)).length, 2);
    // The focus put outside the composer, as a press in the chat does, or taken from the page
    // by another window, removes nothing.
    await page.run(`(() => { const outside = document.createElement("input"); outside.id = "outside";
      document.body.append(outside); outside.focus(); })()`);
    await page.run(`window.dispatchEvent(new Event("blur"))`);
    assert.equal((await page.run<string[]>(IDS)).length, 2);
    // The caret back in it is no other box.
    await page.run(into(0));
    assert.equal((await page.run<string[]>(IDS)).length, 2);
    // Another box does take it.
    await page.run(into(1));
    assert.equal((await page.run<string[]>(IDS)).length, 1);
  } finally { await page.close(); }
});

test("a scratchpad whose lines were cut leaves with the caret, its removal is saved, and the prompt it leaves is as it was (KYO-939.c4)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START}
      window.kyo.draft({ boxes: [{ id: "cut", text: "\\n \\n", scratch: {} }, { id: "p", text: "prompt" }], etag: "e" });
    })()`);
    await page.run(`window.kyo.composer.select(0, 0)`);
    await page.press("a", META);
    await page.press("Backspace");
    await page.run(pause);
    assert.deepEqual((await page.run<{ id: string }[]>(saved)).map((box) => box.id), ["p"]);
    await page.run(into(1));
    assert.deepEqual(await page.run(IDS), ["p"]);
    await page.run(pause);
    assert.deepEqual((await page.run<{ id: string }[]>(saved)).map((box) => box.id), ["p"]);
    // A device that reads the draft with the scratchpad still in it sees it leave, as it
    // sees any deleted box.
    await page.run(`window.kyo.draft({ boxes: [{ id: "cut", text: "\\n \\n", scratch: {} }, { id: "p", text: "prompt" }], etag: "f" })`);
    await page.run(into(1));
    assert.deepEqual(await page.run(IDS), ["p"]);
  } finally { await page.close(); }
});

test("Command-Z after a move still restores the scratchpad it emptied when an empty scratchpad leaves in the move's own redraw (KYO-939.c5)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await openPads(page, []);
    // An empty scratchpad added last holds the caret; Create prompt on the first moves it into
    // the prompt and puts the caret there, which takes the empty one away in the same redraw.
    await page.run(`document.getElementById("new-scratch").click()`);
    const before = await page.run(HELD);
    await page.run(`document.querySelectorAll("#boxes .box.scratch .box-create")[0].click()`);
    assert.deepEqual((await page.run<{ boxes: unknown[][] }>(HELD)).boxes.map((box) => box[0]), ["scratch", "scratch", "prompt"]);
    // Command-Z takes the move back: the scratchpad the move emptied is there, with its text.
    await undo(page);
    const restored = (await page.run<{ boxes: unknown[][] }>(HELD)).boxes;
    assert.deepEqual(restored.slice(0, 3).map((box) => [box[1], box[2]]), [["a", "alpha"], ["b", PADS[1].text], ["c", "gamma"]]);
    assert.deepEqual((await page.run<{ boxes: unknown[][] }>(HELD)).boxes.length, before ? 5 : 0);
    await redo(page);
    assert.deepEqual((await page.run<{ boxes: unknown[][] }>(HELD)).boxes.map((box) => box[0]), ["scratch", "scratch", "prompt"]);
  } finally { await page.close(); }
});

// KYO-971.c1: the right pane has no top bar and no + button, and its first element starts at its top.
test("the right pane has no top bar and its first element starts at the pane's top (KYO-971.c1)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START} })()`);
    const seen = await page.run<{ bar: boolean; plus: boolean; top: number; first: number }>(`(() => {
      const first = [...document.getElementById("composer").children].find((e) => e.getBoundingClientRect().height > 0);
      return { bar: Boolean(document.getElementById("composer-top")), plus: Boolean(document.getElementById("new-prompt")),
        top: Math.round(document.getElementById("composer").getBoundingClientRect().top),
        first: Math.round(first.getBoundingClientRect().top) };
    })()`);
    assert.equal(seen.bar, false);
    assert.equal(seen.plus, false);
    assert.equal(seen.first, seen.top);
  } finally { await page.close(); }
});

// KYO-971.c2: one line under the scratchpads, alone when there are none, makes a scratchpad.
test("'Create scratchpad…' stands under the scratchpads and opens a new one with the caret in it, by click or Return (KYO-971.c2)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START} })()`);
    const place = `(() => { const l = document.getElementById("new-scratch"); const s = document.getElementById("scratches");
      const m = document.querySelector("#boxes > .box.main"); return { text: l.textContent, color: getComputedStyle(l).color,
        count: s.querySelectorAll(":scope > .box").length, top: Math.round(l.getBoundingClientRect().top),
        scratchesBottom: Math.round(s.getBoundingClientRect().bottom), mainTop: Math.round(m.getBoundingClientRect().top),
        shown: l.getBoundingClientRect().height > 0, focusable: l.tabIndex === 0,
        caretInScratch: Boolean(document.activeElement?.closest("#scratches .box.scratch")) }; })()`;
    const alone = await page.run<any>(place);
    assert.equal(alone.text, "Create scratchpad…");
    assert.equal(alone.count, 0);
    assert.equal(alone.shown, true);
    assert.equal(alone.focusable, true);
    assert.ok(alone.top < alone.mainTop, JSON.stringify(alone));
    // It has the colour of an empty box's placeholder.
    await page.run(`document.getElementById("new-scratch").click()`);
    const color = await page.run<string>(`getComputedStyle(document.querySelector("#scratches .cm-placeholder") ?? document.getElementById("new-scratch")).color`);
    const after = await page.run<any>(place);
    assert.equal(after.count, 1);
    assert.equal(after.caretInScratch, true);
    assert.ok(after.top >= after.scratchesBottom - 1, JSON.stringify(after));
    assert.equal(after.color, alone.color);
    assert.equal(color, alone.color);
    // An empty scratchpad is an abandoned one, which a later one takes away, so this one has words.
    await page.type("notes");
    // Return on the focused line does the same.
    await page.run(`document.getElementById("new-scratch").focus()`);
    await page.press("Enter");
    const again = await page.run<any>(place);
    assert.equal(again.count, 2);
    assert.equal(again.caretInScratch, true);
  } finally { await page.close(); }
});
