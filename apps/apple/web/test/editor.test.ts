// Run with `npm test`. The composer's editor module (`src/editor.ts`, KYO-909) in a headless
// Chrome, through a page of its own (`editor-page.ts`): the keys and the document they leave,
// the marks and the history they share, the lines' drawing, and paste and drop. The composer
// does not use the module yet. A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { META, SHIFT, openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(new URL("./editor-page.ts", import.meta.url)); });
after(async () => { await page?.close(); });

/** A fresh page with one focused box of `text`, holding `marks`, `width` pixels wide. */
async function box(text = "", marks: { line: number; items: { name: string }[] }[] = [], width = 360): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`ed.make(${JSON.stringify(text)}, ${JSON.stringify(marks)}, ${width})`);
  return opened;
}

const text = (opened: Page) => opened.run<string>("ed.text()");
const marks = (opened: Page) => opened.run<{ line: number; names: string[] }[]>("ed.marks()");
const pause = (ms: number) => new Promise((done) => setTimeout(done, ms));
/** Longer than the history's grouping of typing into one step (500 ms). */
const NEW_STEP = 650;

test("a box holds its text, and the keys leave exactly the text they make (KYO-909.c2)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const opened = await box("Opening words");
  assert.equal(await text(opened), "Opening words");
  await opened.run("ed.select(13)");
  const steps: [string, () => Promise<void>, string][] = [
    ["Return", () => opened.press("Enter"), "Opening words\n"],
    ["typing", () => opened.type("- one"), "Opening words\n- one"],
    ["Return on an item", () => opened.press("Enter"), "Opening words\n- one\n- "],
    ["typing", () => opened.type("two"), "Opening words\n- one\n- two"],
    ["Tab", () => opened.press("Tab"), "Opening words\n- one\n  - two"],
    ["Return on a nested item", () => opened.press("Enter"), "Opening words\n- one\n  - two\n  - "],
    ["Shift-Tab", () => opened.press("Tab", SHIFT), "Opening words\n- one\n  - two\n- "],
    ["Tab", () => opened.press("Tab"), "Opening words\n- one\n  - two\n  - "],
    ["Return on an empty item", () => opened.press("Enter"), "Opening words\n- one\n  - two\n"],
    ["typing", () => opened.type("1. first"), "Opening words\n- one\n  - two\n1. first"],
    ["Shift-Return", () => opened.press("Enter", SHIFT), "Opening words\n- one\n  - two\n1. first\n"],
    ["typing", () => opened.type("2. second"), "Opening words\n- one\n  - two\n1. first\n2. second"],
    ["Return on a numbered item", () => opened.press("Enter"), "Opening words\n- one\n  - two\n1. first\n2. second\n3. "],
    ["Shift-Return on an empty item", () => opened.press("Enter", SHIFT), "Opening words\n- one\n  - two\n1. first\n2. second\n"],
    // The Return and the ending of the list came in one run of keys, and are one undo step.
    ["undo", () => opened.press("z", META), "Opening words\n- one\n  - two\n1. first\n2. second"],
    ["redo", () => opened.press("z", META | SHIFT), "Opening words\n- one\n  - two\n1. first\n2. second\n"],
  ];
  for (const [what, act, expected] of steps) {
    await act();
    assert.equal(await text(opened), expected, `after ${what}`);
  }

  // Typing is one undo step until a pause, as a Mac text view groups it.
  await box("");
  await opened.type("abc");
  await pause(NEW_STEP);
  await opened.type(" def");
  await opened.press("z", META);
  assert.equal(await text(opened), "abc");
  await opened.press("z", META);
  assert.equal(await text(opened), "");
  await opened.press("z", META | SHIFT);
  await opened.press("z", META | SHIFT);
  assert.equal(await text(opened), "abc def");
});

test("Tab and Shift-Tab move every touched line, keep the focus and are one undo step each (KYO-909.c3)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const opened = await box("- a\n\n- b\n3. c\nlast");
  // A selection from inside the first line to the start of the last takes the lines between.
  await opened.run("ed.select(1, 13)");
  await opened.press("Tab");
  assert.equal(await text(opened), "  - a\n\n  - b\n  1. c\nlast");
  assert.deepEqual(await opened.run("ed.selection()"), [0, 19]);
  assert.equal(await opened.run("ed.focused()"), true);
  await opened.press("Tab", SHIFT);
  assert.equal(await text(opened), "- a\n\n- b\n1. c\nlast");
  await opened.press("z", META);
  assert.equal(await text(opened), "  - a\n\n  - b\n  1. c\nlast");
  await opened.press("z", META);
  assert.equal(await text(opened), "- a\n\n- b\n3. c\nlast");
  await opened.press("z", META | SHIFT);
  assert.equal(await text(opened), "  - a\n\n  - b\n  1. c\nlast");

  // The caret alone moves its line, a plain one too, and stays on the same words.
  await box("words here");
  await opened.run("ed.select(6)");
  await opened.press("Tab");
  assert.equal(await text(opened), "  words here");
  assert.deepEqual(await opened.run("ed.selection()"), [8, 8]);
  await opened.press("]", META);
  assert.equal(await text(opened), "    words here");
  await opened.press("[", META);
  await opened.press("Tab", SHIFT);
  assert.equal(await text(opened), "words here");
  assert.deepEqual(await opened.run("ed.selection()"), [6, 6]);
  assert.equal(await opened.run("ed.focused()"), true);
  // Typed words and the Tab after them are two steps: undo takes back the Tab alone.
  await opened.run("ed.select(10)");
  await opened.type("!");
  await opened.press("Tab");
  await opened.press("z", META);
  assert.equal(await text(opened), "words here!");
});

test("Return continues a list or ends it, a plain line gets no indent, and Command-Return is not the editor's (KYO-909.c4)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const opened = await box("  - nested");
  await opened.run("ed.select(10)");
  await opened.press("Enter");
  assert.equal(await text(opened), "  - nested\n  - ");
  await opened.press("Enter");
  assert.equal(await text(opened), "  - nested\n");

  await box("  12. twelve");
  await opened.run("ed.select(12)");
  await opened.press("Enter");
  assert.equal(await text(opened), "  12. twelve\n  13. ");

  await box("  indented plain words");
  await opened.run("ed.select(22)");
  await opened.press("Enter");
  assert.equal(await text(opened), "  indented plain words\n");
  assert.deepEqual(await opened.run("ed.selection()"), [23, 23]);

  // Command-Return reaches the page's own handler untouched, and the text is unchanged.
  await box("- item");
  await opened.run("ed.select(6)");
  await opened.run(`document.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.metaKey) window.sent = { prevented: event.defaultPrevented };
  })`);
  await opened.press("Enter", META);
  assert.deepEqual(await opened.run("window.sent"), { prevented: false });
  assert.equal(await text(opened), "- item");
});

test("placing an item is an undo step of its own, by Command-Z and by the editing command (KYO-909.c5)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const opened = await box("first\nsecond");
  await opened.run("ed.select(12)");
  await opened.type(" words");
  await opened.run(`ed.place(0, "anchor")`);
  assert.deepEqual(await marks(opened), [{ line: 0, names: ["anchor"] }]);

  await opened.press("z", META);
  assert.deepEqual(await marks(opened), []);
  assert.equal(await text(opened), "first\nsecond words");
  await opened.press("z", META | SHIFT);
  assert.deepEqual(await marks(opened), [{ line: 0, names: ["anchor"] }]);
  assert.equal(await text(opened), "first\nsecond words");

  // The Edit menu's path: the editing command, with no Command-Z key for the page to read.
  await opened.press("F13", 0, "undo");
  assert.deepEqual(await marks(opened), []);
  assert.equal(await text(opened), "first\nsecond words");
  // Chrome raises no `historyRedo` for the editing command `redo` here, since its own redo
  // stack is empty (CodeMirror took the undo), so the step is put back by the key.
  await opened.press("z", META | SHIFT);
  assert.deepEqual(await marks(opened), [{ line: 0, names: ["anchor"] }]);
  assert.equal(await text(opened), "first\nsecond words");

  // Then the step before the placing, the typing, and the placing again in order.
  await opened.press("z", META);
  await opened.press("z", META);
  assert.equal(await text(opened), "first\nsecond");
  assert.deepEqual(await marks(opened), []);
  await opened.press("z", META | SHIFT);
  await opened.press("z", META | SHIFT);
  assert.equal(await text(opened), "first\nsecond words");
  assert.deepEqual(await marks(opened), [{ line: 0, names: ["anchor"] }]);

  // A removal is a step too, and undoing it puts the item back in its place among the others.
  await opened.run(`ed.place(0, "tag")`);
  await opened.run(`ed.place(0, "last")`);
  await opened.run(`ed.remove(0, "tag")`);
  assert.deepEqual(await marks(opened), [{ line: 0, names: ["anchor", "last"] }]);
  await opened.press("z", META);
  assert.deepEqual(await marks(opened), [{ line: 0, names: ["anchor", "tag", "last"] }]);
});

test("marks follow their lines through edits, and a line back by undo takes back what it held (KYO-909.c6)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const held = (name: string) => ({ name });
  const opened = await box("alpha\nbeta\ngamma\ndelta", [{ line: 1, items: [held("b")] }, { line: 3, items: [held("d")] }]);
  assert.deepEqual(await marks(opened), [{ line: 1, names: ["b"] }, { line: 3, names: ["d"] }]);

  // A Return at the start of a line pushes its text, and its marks, down.
  await opened.run("ed.select(0)");
  await opened.press("Enter");
  assert.equal(await text(opened), "\nalpha\nbeta\ngamma\ndelta");
  assert.deepEqual(await marks(opened), [{ line: 2, names: ["b"] }, { line: 4, names: ["d"] }]);

  // Indenting every line keeps each line's marks on it, the lines inside the selection too.
  await opened.run("ed.select(0, ed.text().length)");
  await opened.press("Tab");
  assert.equal(await text(opened), "\n  alpha\n  beta\n  gamma\n  delta");
  assert.deepEqual(await marks(opened), [{ line: 2, names: ["b"] }, { line: 4, names: ["d"] }]);

  // Joining two lines keeps both lines' marks on the one that remains.
  await opened.run("ed.select(ed.text().indexOf('  delta'))");
  await opened.press("Backspace");
  assert.equal(await text(opened), "\n  alpha\n  beta\n  gamma  delta");
  assert.deepEqual(await marks(opened), [{ line: 2, names: ["b"] }, { line: 3, names: ["d"] }]);

  // A deleted line's marks go, and come back with it by undo.
  await pause(NEW_STEP);
  await opened.run("ed.select(ed.text().indexOf('  beta'), ed.text().indexOf('  gamma'))");
  await opened.press("Backspace");
  assert.equal(await text(opened), "\n  alpha\n  gamma  delta");
  assert.deepEqual(await marks(opened), [{ line: 2, names: ["d"] }]);
  await opened.press("z", META);
  assert.equal(await text(opened), "\n  alpha\n  beta\n  gamma  delta");
  assert.deepEqual(await marks(opened), [{ line: 2, names: ["b"] }, { line: 3, names: ["d"] }]);
});

test("the wrapped rows of a nested item start under its first word (KYO-909.c7)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const item = "    - grandchild item whose words run on well past the width of the box, so that it wraps onto several rows below its first";
  const opened = await box(`- parent\n  - child\n${item}\nplain`);
  const seen = await opened.run<{ word: number; rows: number[]; edge: number }>(`(() => {
    const view = ed.view();
    const line = view.state.doc.line(3);
    const wordAt = line.from + line.text.indexOf("grandchild");
    const word = view.coordsAtPos(wordAt, 1);
    const rows = [];
    let top = word.top;
    for (let pos = wordAt + 1; pos < line.to; pos++) {
      const at = view.coordsAtPos(pos, 1);
      if (at.top > top + 2) { rows.push(at.left); top = at.top; }
    }
    return { word: word.left, rows, edge: view.coordsAtPos(line.from, 1).left };
  })()`);
  assert.ok(seen.rows.length >= 2, `the item wraps to ${seen.rows.length + 1} rows`);
  for (const left of seen.rows) assert.ok(Math.abs(left - seen.word) <= 1, `a row starts at ${left}, the word at ${seen.word}`);
  // The first row starts at the left edge, as typed.
  assert.ok(seen.word - seen.edge > 20);
});

test("nested lines carry a guide for each level and headings their level's class, and the text is unchanged (KYO-909.c8)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const source = "# bugs\n- parent\n  - child\n    - grandchild\n\n    - after a blank\n  - second child\n## next\nplain";
  const opened = await box(source);
  const seen = await opened.run<{ lines: { depth: number; guides: number[]; heading: string }[]; step: number }>(`(() => {
    const view = ed.view();
    const probe = document.createElement("span");
    probe.style.whiteSpace = "pre";
    probe.textContent = "  ".repeat(10);
    view.contentDOM.querySelector(".cm-line").append(probe);
    const step = probe.getBoundingClientRect().width / 10;
    probe.remove();
    const lines = [...view.contentDOM.querySelectorAll(".cm-line")].map((line) => {
      const style = getComputedStyle(line);
      const layers = style.backgroundImage === "none" ? 0 : style.backgroundImage.split("linear-gradient").length - 1;
      const guides = layers ? style.backgroundPositionX.split(",").map((x) => parseFloat(x)) : [];
      return { depth: layers, guides, heading: [...line.classList].filter((c) => c === "heading" || c.startsWith("level-")).join(" ") };
    });
    return { lines, step };
  })()`);
  assert.deepEqual(seen.lines.map((line) => line.depth), [0, 0, 1, 2, 2, 2, 1, 0, 0]);
  for (const line of seen.lines) {
    line.guides.slice(1).forEach((x, at) => assert.ok(Math.abs(x - line.guides[at] - seen.step) < 0.5,
      `guides ${line.guides} are one step (${seen.step}) apart`));
  }
  assert.deepEqual(seen.lines.map((line) => line.heading), ["heading level-1", "", "", "", "", "", "", "heading level-2", ""]);
  assert.equal(await text(opened), source);
});

test("a file pasted or dropped goes to the attach callback and inserts nothing; pasted text is reported (KYO-909.c9)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const opened = await box("first line\nsecond line");
  await opened.run("ed.select(3)");
  const calls = await opened.run<Record<string, unknown>[]>(`(() => {
    const view = ed.view();
    const files = (...names) => {
      const transfer = new DataTransfer();
      for (const name of names) transfer.items.add(new File(["file words"], name, { type: name.endsWith(".png") ? "image/png" : "text/markdown" }));
      return transfer;
    };
    view.contentDOM.dispatchEvent(new ClipboardEvent("paste", { clipboardData: files("shot.png"), bubbles: true, cancelable: true }));
    const second = view.contentDOM.querySelectorAll(".cm-line")[1].getBoundingClientRect();
    view.contentDOM.dispatchEvent(new DragEvent("drop", {
      dataTransfer: files("notes.md", "shot.png"), clientX: second.left + 30, clientY: second.top + second.height / 2,
      bubbles: true, cancelable: true,
    }));
    return ed.calls.slice();
  })()`);
  assert.equal(await text(opened), "first line\nsecond line");
  assert.deepEqual(calls, [
    { kind: "files", how: "paste", pos: 3, names: ["shot.png"] },
    { kind: "files", how: "drop", pos: calls[1]?.pos, names: ["notes.md", "shot.png"] },
  ]);
  const dropped = Number(calls[1].pos);
  assert.ok(dropped > 11 && dropped <= 22, `the drop fell on the second line, at ${dropped}`);

  const pasted = await opened.run(`(() => {
    ed.calls.length = 0;
    const transfer = new DataTransfer();
    transfer.setData("text/plain", "pasted words");
    ed.view().contentDOM.dispatchEvent(new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true, cancelable: true }));
    return ed.calls.slice();
  })()`);
  assert.equal(await text(opened), "firpasted wordsst line\nsecond line");
  assert.deepEqual(pasted, [{ kind: "paste", text: "pasted words", from: 3 }]);
});

test("the gutter draws held marks, the ghost and the landing arrow on their lines, and the text keeps its spelling and menu (KYO-909)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const opened = await box("one\ntwo\nthree", [{ line: 2, items: [{ name: "x" }, { name: "y" }] }]);
  const lineOfMark = (selector: string) => opened.run<number[]>(`ed.settle().then(() => {
    const view = ed.view();
    return [...view.dom.querySelectorAll(${JSON.stringify(selector)})].map((mark) =>
      view.state.doc.lineAt(view.lineBlockAtHeight(mark.getBoundingClientRect().top + 2 - view.documentTop).from).number - 1);
  })`);
  assert.deepEqual(await lineOfMark(".mark.held"), [2]);
  assert.equal(await opened.run(`ed.view().dom.querySelector(".mark.held").textContent`), "x,y");

  await opened.run("ed.select(5)");
  await opened.run("ed.landing(true)");
  assert.deepEqual(await lineOfMark(".mark-landing"), [1]);
  await opened.run("ed.select(1)");
  assert.deepEqual(await lineOfMark(".mark-landing"), [0]);
  await opened.run("ed.landing(false)");
  assert.deepEqual(await lineOfMark(".mark-landing"), []);

  // The ghost follows the pointer over lines that hold nothing, and not over one that does.
  const hover = (line: number) => opened.run(`(() => {
    const rect = ed.view().contentDOM.querySelectorAll(".cm-line")[${line}].getBoundingClientRect();
    ed.view().dom.dispatchEvent(new MouseEvent("mousemove", { clientX: rect.left + 5, clientY: rect.top + 3, bubbles: true }));
  })()`);
  await hover(0);
  assert.deepEqual(await lineOfMark(".mark.ghost"), [0]);
  await hover(2);
  assert.deepEqual(await lineOfMark(".mark.ghost"), []);

  // A right-click in the gutter names the line under the pointer; on the text it is left alone.
  const menus = await opened.run<{ calls: Record<string, unknown>[]; onText: boolean }>(`(() => {
    const view = ed.view();
    const gutter = view.dom.querySelector(".cm-gutter.kyo-marks").getBoundingClientRect();
    const second = view.contentDOM.querySelectorAll(".cm-line")[1].getBoundingClientRect();
    view.dom.querySelector(".cm-gutter.kyo-marks").dispatchEvent(new MouseEvent("contextmenu", {
      clientX: gutter.left + 4, clientY: second.top + 3, bubbles: true, cancelable: true }));
    const onText = new MouseEvent("contextmenu", { clientX: second.left + 4, clientY: second.top + 3, bubbles: true, cancelable: true });
    view.contentDOM.querySelectorAll(".cm-line")[1].dispatchEvent(onText);
    return { calls: ed.calls.filter((call) => call.kind === "gutterMenu"), onText: onText.defaultPrevented };
  })()`);
  assert.equal(menus.calls.length, 1);
  assert.equal(menus.calls[0].line, 1);
  assert.equal(menus.onText, false);

  const attributes = await opened.run(`(() => {
    const content = ed.view().contentDOM;
    return ["spellcheck", "autocorrect", "autocapitalize", "writingsuggestions"].map((name) => content.getAttribute(name));
  })()`);
  assert.deepEqual(attributes, ["true", "off", "off", "false"]);

  await box("");
  assert.equal(await opened.run(`ed.view().dom.querySelector(".cm-placeholder")?.textContent`), "Reply…");
});

/**
 * Each drawn line's section lines (KYO-917): for each line of the box drawn, counted from 0,
 * the box of each line beside it, outermost first, measured from the editor's left edge, with
 * the line's own block.
 */
const SECTIONS = `ed.settle().then(() => {
  const view = ed.view();
  const edge = view.dom.getBoundingClientRect().left;
  const rows = new Map();
  for (const element of view.dom.querySelectorAll(".cm-gutter.kyo-sections .cm-gutterElement")) {
    const rect = element.getBoundingClientRect();
    if (!rect.height) continue;
    const block = view.lineBlockAtHeight(rect.top + 1 - view.documentTop);
    rows.set(view.state.doc.lineAt(block.from).number - 1, [...element.querySelectorAll(".section-line")].map((bar) => {
      const box = bar.getBoundingClientRect();
      return { left: box.left - edge, right: box.right - edge, top: box.top, bottom: box.bottom };
    }));
  }
  return [...view.contentDOM.querySelectorAll(".cm-line")].map((line) => {
    const number = view.state.doc.lineAt(view.posAtDOM(line)).number;
    const block = view.lineBlockAt(view.state.doc.line(number).from);
    return { line: number - 1, top: block.top + view.documentTop, bottom: block.bottom + view.documentTop, bars: rows.get(number - 1) ?? [] };
  });
})`;

test("a heading's section has a line at the box's edge down all its rows, a nested section a second beside it, and a folded section the line on its heading's row (KYO-917.c7, c8)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const source = [
    "before the first heading",
    "# History view",
    "## when a session ends",
    "Records are kept with the session that wrote them, and the list shows them by day, so this line wraps onto rows below.",
    "- one item",
    "  - nested item under it",
    "",
    "test",
    "",
    "# Other",
    "last words",
  ].join("\n");
  const opened = await box(source);
  type Row = { line: number; top: number; bottom: number; bars: { left: number; right: number; top: number; bottom: number }[] };
  const rows = await opened.run<Row[]>(SECTIONS);
  // How many lines each line carries: none before the first heading, two in the nested section,
  // the plain line after the list among them, and none on the blank line that ends a section.
  assert.deepEqual(rows.map((row) => row.bars.length), [0, 1, 2, 2, 2, 2, 2, 2, 0, 1, 1]);
  const wrapped = rows[3];
  assert.ok(wrapped.bottom - wrapped.top > 30, `the long line wraps (${wrapped.bottom - wrapped.top}px)`);
  for (const row of rows) {
    row.bars.forEach((bar, slot) => {
      // In one strip beside the text (KYO-918), the nested one beside the first, and as tall
      // as the line's rows.
      const edge = rows.find((other) => other.bars.length)!.bars[0].left;
      assert.ok(Math.abs(bar.left - edge - slot * 4) < 0.5, `line ${row.line}'s line ${slot} stands at ${bar.left}`);
      assert.ok(bar.right - bar.left >= 1.5, "a line is two pixels wide");
      assert.ok(Math.abs(bar.bottom - row.bottom) < 0.5, `line ${row.line}'s line ${slot} runs to the foot of its rows`);
    });
  }
  // Unbroken: down a section, each line's stroke starts where the one above it ends; it starts
  // on its heading's row, a little below the heading's rule.
  for (const [heading, slot, end] of [[1, 0, 8], [2, 1, 8], [9, 0, 11]]) {
    const bars = rows.filter((row) => row.line >= heading && row.line < end).map((row) => row.bars[slot]);
    assert.ok(bars[0].top > rows[heading].top && bars[0].top - rows[heading].top <= 4, `section ${heading} starts on its heading's row`);
    bars.slice(1).forEach((bar, at) => assert.ok(Math.abs(bar.top - bars[at].bottom) < 0.5, `section ${heading} is unbroken at line ${heading + at + 1}`));
  }

  // Folded, the heading's row carries the line and the hidden rows carry none.
  await opened.run(`ed.select(${source.indexOf("# History view")})`);
  await opened.press("[", META | 1);
  const folded = await opened.run<Row[]>(SECTIONS);
  assert.deepEqual(folded.map((row) => [row.line, row.bars.length]), [[0, 0], [1, 1], [8, 0], [9, 1], [10, 1]]);
  assert.ok(Math.abs(folded[1].bars[0].bottom - folded[1].bottom) < 0.5);
  // The lines are drawn beside the text and change none of it.
  assert.equal(await text(opened), source);
  await opened.press("]", META | 1);
  assert.deepEqual((await opened.run<Row[]>(SECTIONS)).map((row) => row.bars.length), [0, 1, 2, 2, 2, 2, 2, 2, 0, 1, 1]);
});
