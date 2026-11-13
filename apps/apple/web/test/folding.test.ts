// Run with `npm test`. Folding in the composer (KYO-913): a heading folds its section and a list
// item the lines nested under it, as `sectionEnd` gives them; the text saved and sent is the
// same folded or not; a fold's placeholder counts the marks it hides; folds survive a merge
// and are kept by the box's id; and the landing arrow stands in the fold gutter. The editor's
// own page (`editor-page.ts`) and the renderer's page are both used. A machine with no Chrome
// skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { META, openPage, type Page } from "./chrome.ts";
import { sectionEnd } from "../src/outline.ts";

/** Option, as the Mac app's web view reads the key. */
const ALT = 1;
const FOLD = ["[", META | ALT] as const;
const UNFOLD = ["]", META | ALT] as const;

let editorPage: Page | null = null;
before(async () => { editorPage = await openPage(new URL("./editor-page.ts", import.meta.url)); });
after(async () => { await editorPage?.close(); });

/** The lines of the box (counted from 0) that are drawn, a folded section's first line among them. */
const SHOWN = `(() => {
  const view = ed.view();
  return [...view.contentDOM.querySelectorAll(".cm-line")].map((line) => view.state.doc.lineAt(view.posAtDOM(line)).number - 1);
})()`;

const OUTLINE = [
  "# plan",
  "intro words",
  "- one",
  "  - nested a",
  "    - deeper",
  "  - nested b",
  "",
  "- two",
  "## sub",
  "sub words",
  "",
  "# next",
  "last",
].join("\n");

test("a heading folds to the next heading of its level or above and a list item its nested lines, as sectionEnd gives them (KYO-913.c1)", async (t) => {
  if (!editorPage) return t.skip("no Chrome here");
  const opened = editorPage;
  await opened.reload();
  await opened.run(`ed.make(${JSON.stringify(OUTLINE)}, [], 600)`);
  const lines = OUTLINE.split("\n");
  const all = lines.map((_, index) => index);
  const starts = lines.map((_, index) => lines.slice(0, index).join("\n").length + (index ? 1 : 0));
  for (let line = 0; line < lines.length; line++) {
    const end = sectionEnd(OUTLINE, line);
    const hidden = lines[line].trim() ? all.filter((at) => at > line && at < end) : [];
    await opened.run(`ed.select(${starts[line]})`);
    await opened.press(...FOLD);
    await opened.run("ed.settle()");
    const shown = await opened.run<number[]>(SHOWN);
    assert.deepEqual(all.filter((at) => !shown.includes(at)), hidden, `line ${line} (${JSON.stringify(lines[line])}) folds to ${end}`);
    await opened.press(...UNFOLD);
    await opened.run("ed.settle()");
    assert.deepEqual(await opened.run<number[]>(SHOWN), all, `line ${line} unfolds`);
  }
  // The heading's section is up to the next heading of its level, past the `##` inside it.
  // The blank lines at a section's end are left out of it, as Send to prompt leaves them.
  assert.deepEqual([sectionEnd(OUTLINE, 0), sectionEnd(OUTLINE, 2), sectionEnd(OUTLINE, 8)], [10, 6, 10]);
  assert.equal(await opened.run("ed.text()"), OUTLINE);

  // The fold gutter's chevron does the same: shown on the line under the pointer that can fold,
  // a press folds that line's section, and on a folded line the chevron stays and unfolds it.
  const toggles = () => opened.run<{ line: number; folded: boolean }[]>(`ed.settle().then(() => {
    const view = ed.view();
    return [...view.dom.querySelectorAll(".cm-gutter.kyo-folds .fold-toggle")].map((toggle) => ({
      line: view.state.doc.lineAt(view.lineBlockAtHeight(toggle.getBoundingClientRect().top + 4 - view.documentTop).from).number - 1,
      folded: toggle.classList.contains("folded"),
    }));
  })`);
  const hover = (line: number) => opened.run(`(() => {
    const view = ed.view();
    const rect = view.contentDOM.querySelectorAll(".cm-line")[${line}].getBoundingClientRect();
    view.dom.dispatchEvent(new MouseEvent("mousemove", { clientX: rect.left + 5, clientY: rect.top + 3, bubbles: true }));
  })()`);
  await hover(1);
  assert.deepEqual(await toggles(), [], "a line with nothing nested under it has no chevron");
  await hover(2);
  assert.deepEqual(await toggles(), [{ line: 2, folded: false }]);
  await opened.run(`(() => {
    const toggle = ed.view().dom.querySelector(".cm-gutter.kyo-folds .fold-toggle");
    const rect = toggle.getBoundingClientRect();
    toggle.dispatchEvent(new MouseEvent("mousedown", { clientX: rect.left + 4, clientY: rect.top + 4, bubbles: true, cancelable: true }));
    toggle.dispatchEvent(new MouseEvent("click", { clientX: rect.left + 4, clientY: rect.top + 4, bubbles: true, cancelable: true }));
  })()`);
  assert.deepEqual((await opened.run<number[]>(SHOWN)).slice(0, 4), [0, 1, 2, 6]);
  await opened.run(`ed.view().dom.dispatchEvent(new MouseEvent("mouseleave"))`);
  assert.deepEqual(await toggles(), [{ line: 2, folded: true }], "a folded line keeps its chevron with the pointer away");
  assert.equal(await opened.run("ed.focused()"), true, "a press on the chevron leaves the focus in the text");
});

test("a fold's placeholder counts the marks its lines hold, whose markers are not drawn, and follows them while folded (KYO-913.c3)", async (t) => {
  if (!editorPage) return t.skip("no Chrome here");
  const opened = editorPage;
  await opened.reload();
  const held = (...names: string[]) => names.map((name) => ({ name }));
  await opened.run(`ed.make(${JSON.stringify("# plan\n- a\n- b\nwords\n# next")}, ${JSON.stringify([
    { line: 0, items: held("top") }, { line: 1, items: held("x", "y") }, { line: 2, items: held("z") }, { line: 4, items: held("after") },
  ])}, 600)`);
  const seen = () => opened.run<{ marks: string[]; placeholder: string | null; title: string | null }>(`ed.settle().then(() => {
    const view = ed.view();
    const placeholder = view.dom.querySelector(".cm-foldPlaceholder");
    return {
      marks: [...view.dom.querySelectorAll(".mark.held")].map((mark) => mark.textContent),
      placeholder: placeholder?.textContent ?? null,
      title: placeholder?.title ?? null,
    };
  })`);
  assert.deepEqual((await seen()).marks, ["top", "x,y", "z", "after"]);
  await opened.run("ed.select(0)");
  await opened.press(...FOLD);
  assert.deepEqual(await seen(), { marks: ["top", "after"], placeholder: "… 2 marks", title: "2 items\n1 item" });
  // What the hidden lines hold changes with the section folded, and the placeholder says so.
  await opened.run(`ed.remove(1, "x")`);
  await opened.run(`ed.remove(1, "y")`);
  assert.deepEqual(await seen(), { marks: ["top", "after"], placeholder: "… 1 mark", title: "1 item" });
  await opened.run(`ed.remove(2, "z")`);
  assert.deepEqual(await seen(), { marks: ["top", "after"], placeholder: "…", title: "Unfold" });
  await opened.press("z", META);
  assert.deepEqual(await seen(), { marks: ["top", "after"], placeholder: "… 1 mark", title: "1 item" });
  // A press on the placeholder unfolds the section and its markers are drawn again.
  await opened.run(`document.querySelector(".cm-foldPlaceholder").click()`);
  assert.deepEqual(await seen(), { marks: ["top", "z", "after"], placeholder: null, title: null });
});

// ---------------------------------------------------------------- the composer itself

const START = `window.kyo.project({ project: "p", scope: "server/p" });`;
const saved = `window.posted.filter((m) => m.name === "saveDraft").at(-1)?.body.boxes`;
const sentBody = `window.posted.filter((m) => m.name === "send").at(-1)?.body`;
const pause = `new Promise((done) => setTimeout(done, 900))`;
const settle = `new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))`;

/** A scratchpad with a heading's section and a list item's, and a prompt below with its own. */
const SCRATCH = "# topic\nwords\n- item\n  - nested\n# other\nkeep";
const PROMPT = "# ask\n- first\n  - detail\n- second\ntail";
const BOXES = [
  { id: "a", text: SCRATCH, scratch: {},
    marks: [{ line: 1, items: [{ kind: "tag", name: "review" }] }, { line: 3, items: [{ kind: "tag", name: "later" }] },
      { line: 5, items: [{ kind: "tag", name: "stays" }] }] },
  { id: "b", text: PROMPT, marks: [{ line: 2, items: [{ kind: "tag", name: "inner" }] }] },
];

/** Puts the caret on line `line` of box `index` and folds there by the key. */
async function foldAt(page: Page, index: number, line: number, keys: readonly [string, number] = FOLD): Promise<void> {
  const text = await page.run<string>(`window.kyo.composer.boxes()[${index}].text`);
  const at = text.split("\n").slice(0, line).join("\n").length + (line ? 1 : 0);
  await page.run(`window.kyo.composer.select(${index}, ${at})`);
  await page.press(...keys);
}

/** Types `!` at the end of the prompt, then reads the saved draft and the body Command-Return sends. */
async function saveAndSend(page: Page): Promise<{ saved: unknown; sent: unknown }> {
  await page.run(`window.kyo.composer.select(1, ${PROMPT.length})`);
  await page.type("!");
  await page.run(pause);
  const kept = await page.run(saved);
  await page.press("Enter", META);
  return { saved: kept, sent: await page.run(sentBody) };
}

test("with sections folded, the saved draft and the posted send body are those of the boxes unfolded, and the section lines change neither (KYO-913.c2, KYO-917.c8)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(BOXES)}, etag: "e" }); })()`);
    // Both boxes have headings, whose sections' lines are drawn beside them.
    const lined = `[...document.querySelectorAll("#boxes > .box")].map((box) => box.querySelectorAll(".cm-gutter.kyo-sections .section-line").length)`;
    assert.deepEqual(await page.run(lined), [6, 5]);
    const plain = await saveAndSend(page);

    await page.reload();
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(BOXES)}, etag: "e" }); })()`);
    await foldAt(page, 0, 0);
    await foldAt(page, 1, 1);
    assert.deepEqual(await page.run(`[window.kyo.composer.folds(0), window.kyo.composer.folds(1)]`), [[0], [1]]);
    // A folded heading keeps its line on its own row, and the hidden rows have none.
    assert.deepEqual(await page.run(lined), [3, 4]);
    const folded = await saveAndSend(page);
    assert.deepEqual(await page.run(`[window.kyo.composer.folds(0), window.kyo.composer.folds(1)]`), [[0], [1]]);

    assert.ok(plain.saved && plain.sent, "a draft was saved and a prompt sent");
    assert.deepEqual(folded.saved, plain.saved);
    assert.deepEqual(folded.sent, plain.sent);
    assert.equal((plain.sent as { segments: { text: string }[] }).segments.map((segment) => segment.text).join(""), `${PROMPT}!`);
  } finally { await page.close(); }
});

test("in the composer a fold's placeholder names the marks it hides by their titles, and their markers are not drawn (KYO-913.c3)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(BOXES)}, etag: "e" }); })()`);
    const marks = `[...document.querySelectorAll("#boxes .box.scratch .mark.held")].map((mark) => mark.title)`;
    assert.deepEqual(await page.run(marks), ["#review", "#later", "#stays"]);
    await foldAt(page, 0, 0);
    const placeholder = await page.run<{ text: string; title: string }>(`${settle}.then(() => {
      const found = document.querySelector("#boxes .box.scratch .cm-foldPlaceholder");
      return { text: found.textContent, title: found.title };
    })`);
    assert.deepEqual(placeholder, { text: "… 2 marks", title: "#review\n#later" });
    assert.deepEqual(await page.run(marks), ["#stays"]);
  } finally { await page.close(); }
});

test("Send to prompt on a folded heading moves its whole section with its marks, and the fold goes with it (KYO-913.c4)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(BOXES)}, etag: "e" }); })()`);
    await foldAt(page, 0, 0);
    await page.run(`${settle}.then(() => {
      const gutter = document.querySelector("#boxes .box.scratch .cm-gutter.kyo-marks");
      const at = document.querySelector("#boxes .box.scratch .cm-line").getBoundingClientRect();
      gutter.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true,
        clientX: gutter.getBoundingClientRect().left + 5, clientY: at.top + 4 }));
      document.querySelector(".mark-to-prompt").click();
    })`);
    const boxes = await page.run<{ kind: string; text: string }[]>(`window.kyo.composer.boxes()`);
    assert.deepEqual(boxes.map((box) => [box.kind, box.text]), [
      ["scratch", "# other\nkeep"], ["prompt", `${PROMPT}\n# topic\nwords\n- item\n  - nested`],
    ]);
    await page.run(`window.kyo.composer.select(1, 0)`);
    await page.run(`document.activeElement.blur()`);
    await page.run(pause);
    const kept = await page.run<{ id: string; marks: { line: number; items: { name: string }[] }[] }[]>(saved);
    assert.deepEqual(kept.map((box) => [box.id, box.marks.map((mark) => [mark.line, mark.items.map((item) => item.name)])]), [
      ["a", [[1, ["stays"]]]],
      ["b", [[2, ["inner"]], [6, ["review"]], [8, ["later"]]]],
    ]);
    // The section arrives folded on its heading, which is now the prompt's sixth line.
    assert.deepEqual(await page.run(`[window.kyo.composer.folds(0), window.kyo.composer.folds(1)]`), [[], [5]]);
  } finally { await page.close(); }
});

test("a fold survives a merge that leaves its box in place, is folded again in a box drawn again, and the fold keys work at the caret (KYO-913.c5)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(BOXES)}, etag: "e" }); })()`);
    const folds = `[window.kyo.composer.folds(0), window.kyo.composer.folds(1)]`;
    // Command-Option-[ folds the caret's section and Command-Option-] unfolds it.
    await foldAt(page, 0, 2);
    assert.deepEqual(await page.run(folds), [[2], []]);
    await foldAt(page, 0, 2, UNFOLD);
    assert.deepEqual(await page.run(folds), [[], []]);
    await foldAt(page, 0, 0);
    await foldAt(page, 1, 1);
    assert.deepEqual(await page.run(folds), [[0], [1]]);
    await page.run(`document.activeElement.blur()`);

    // Another device changed the prompt: the scratchpad is left in place and keeps its fold.
    const scratchView = `document.querySelector("#boxes .box.scratch .cm-editor")`;
    await page.run(`window.before = ${scratchView}`);
    const changed = [BOXES[0], { ...BOXES[1], text: `# ask\n- first\n  - detail\n- second\nnew tail` }];
    await page.run(`window.kyo.draftElsewhere({ boxes: ${JSON.stringify(changed)}, etag: "e2" })`);
    assert.equal(await page.run(`window.before === ${scratchView}`), true, "the scratchpad's box was left in place");
    assert.deepEqual(await page.run(`window.kyo.composer.boxes().map((box) => box.text)`), [SCRATCH, changed[1].text]);
    // The prompt was drawn again, from its new text, and its fold was kept by its id.
    assert.deepEqual(await page.run(folds), [[0], [1]]);

    // A change above a folded heading draws the box again, and the heading is folded where it now is.
    const moved = [{ ...BOXES[0], text: `added first\n${SCRATCH}`,
      marks: BOXES[0].marks.map((mark) => ({ ...mark, line: mark.line + 1 })) }, changed[1]];
    await page.run(`window.kyo.draftElsewhere({ boxes: ${JSON.stringify(moved)}, etag: "e3" })`);
    assert.equal(await page.run(`window.before === ${scratchView}`), false, "the scratchpad was drawn again");
    assert.deepEqual(await page.run(`window.kyo.composer.boxes().map((box) => box.text)`), [moved[0].text, changed[1].text]);
    assert.deepEqual(await page.run(folds), [[1], [1]]);
    // The whole draft read again, as on choosing the session anew, draws every box again.
    await page.run(`window.kyo.draft({ boxes: ${JSON.stringify(moved)}, etag: "e4" })`);
    assert.deepEqual(await page.run(folds), [[1], [1]]);
  } finally { await page.close(); }
});

test("the landing arrow stands in the fold gutter, over a fold toggle and beside the paragraph's mark (KYO-913.c6)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: [
      { id: "a", text: "# topic\\nwords\\n# plain", scratch: {},
        marks: [{ line: 0, items: [{ kind: "tag", name: "top" }] }, { line: 2, items: [{ kind: "tag", name: "plain" }] }] },
    ], etag: "e" }); })()`);
    await foldAt(page, 0, 0);
    const look = (line: number) => page.run<{
      inFolds: boolean; overlap: boolean; overToggle: boolean; under: string; arrow: string;
    }>(`(async () => {
      window.kyo.composer.select(0, ${line === 0 ? 0 : "window.kyo.composer.boxes()[0].text.length"});
      document.activeElement.blur();
      await new Promise((done) => setTimeout(done, 50));
      await ${settle};
      const arrow = document.querySelector("#boxes .mark-landing");
      const box = arrow.getBoundingClientRect();
      // A row by the middle of what is drawn on it, since the marker, the arrow and the chevron
      // are of different heights, each centred on the line's first row.
      const row = (element) => { const rect = element.getBoundingClientRect(); return Math.round(rect.top + rect.height / 2); };
      const mark = [...document.querySelectorAll("#boxes .mark.held")].find((held) => Math.abs(row(held) - row(arrow)) < 4);
      const toggle = document.querySelector("#boxes .fold-toggle.folded");
      const other = mark.getBoundingClientRect();
      const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
      return {
        inFolds: Boolean(arrow.closest(".cm-gutter.kyo-folds")),
        overlap: box.right > other.left && other.right > box.left && box.bottom > other.top && other.bottom > box.top,
        overToggle: Boolean(toggle) && Math.abs(row(toggle) - row(arrow)) < 4,
        under: hit?.className ?? "",
        arrow: getComputedStyle(arrow).zIndex,
      };
    })()`);
    // On the folded heading, which holds a mark: the arrow covers the chevron, and a press
    // there still reaches the chevron under it.
    const onHeading = await look(0);
    assert.deepEqual([onHeading.inFolds, onHeading.overlap, onHeading.overToggle, onHeading.under],
      [true, false, true, "fold-toggle folded"]);
    // On a line that does not fold and holds a mark, the arrow stands beside the mark.
    const onPlain = await look(2);
    assert.deepEqual([onPlain.inFolds, onPlain.overlap, onPlain.overToggle], [true, false, false]);
  } finally { await page.close(); }
});

// ------------------------------------------------- the gutters' order and spacing (KYO-917)

test("in every box the marks gutter is the outer one and the folds stand between it and the text, all at least four pixels apart at the composer's narrowest (KYO-917.c5, c6)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    // The composer dragged as narrow as it goes, by its band.
    const width = await page.run<number>(`(() => {
      const band = document.getElementById("band");
      band.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, clientX: 600 }));
      document.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: window.innerWidth - 10 }));
      document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      return document.getElementById("composer").getBoundingClientRect().width;
    })()`);
    assert.equal(width, 300);
    await page.run(`(() => { ${START} window.kyo.draft({ boxes: ${JSON.stringify(BOXES)}, etag: "e" }); })()`);
    await foldAt(page, 1, 1);
    // For each box, and each line asked for, with the pointer over it: the boxes of its section
    // lines, its mark or ghost, its chevron and its first character, from the box's inner edge.
    const measure = (kind: string, line: number) => page.run<{
      gutters: string[]; bars: [number, number][]; mark: [number, number] | null; chevron: [number, number] | null;
      first: [number, number]; arrow: boolean;
    }>(`(async () => {
      const box = document.querySelector("#boxes .box.${kind}");
      const edge = box.getBoundingClientRect().left + parseFloat(getComputedStyle(box).borderLeftWidth);
      const span = (element) => { const rect = element.getBoundingClientRect(); return [rect.left - edge, rect.right - edge]; };
      const editor = box.querySelector(".cm-editor");
      const row = box.querySelectorAll(".cm-line")[${line}];
      const at = row.getBoundingClientRect();
      editor.dispatchEvent(new MouseEvent("mousemove", { clientX: at.left + 20, clientY: at.top + 4, bubbles: true }));
      await ${settle};
      const onRow = (selector) => [...box.querySelectorAll(selector)].find((element) => {
        const rect = element.getBoundingClientRect();
        return rect.height && rect.top < at.top + 12 && rect.bottom > at.top + 4;
      });
      const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
      const range = document.createRange();
      const text = walker.nextNode();
      range.setStart(text, 0);
      range.setEnd(text, 1);
      const bars = onRow(".cm-gutter.kyo-sections .cm-gutterElement");
      const mark = onRow(".cm-gutter.kyo-marks .mark");
      const chevron = onRow(".cm-gutter.kyo-folds .fold-toggle");
      return {
        gutters: [...box.querySelectorAll(".cm-gutters > .cm-gutter")].map((gutter) => gutter.className.replace("cm-gutter ", "")),
        bars: bars ? [...bars.querySelectorAll(".section-line")].map(span) : [],
        mark: mark ? span(mark) : null,
        chevron: chevron ? span(chevron) : null,
        first: span(range),
        arrow: Boolean(box.querySelector(".cm-gutter.kyo-folds .mark-landing")) || !box.querySelector(".mark-landing"),
      };
    })()`);
    const apart = (what: string, left: [number, number] | undefined | null, right: [number, number] | undefined | null) => {
      assert.ok(left && right, `${what}: both are drawn`);
      assert.ok(right[0] - left[1] >= 4, `${what}: ${JSON.stringify(left)} and ${JSON.stringify(right)} stand ${right[0] - left[1]}px apart`);
    };
    // The scratchpad's "- item" holds nothing and folds: the ghost and an open chevron. Its
    // "words" holds a mark and does not fold. The prompt's "- first" is folded, and its
    // "  - detail" hidden; its "# ask" holds nothing and folds.
    const cases: [string, number, boolean][] = [["scratch", 2, true], ["scratch", 1, false], ["main", 1, true], ["main", 0, true]];
    for (const [kind, line, folds] of cases) {
      const seen = await measure(kind, line);
      assert.deepEqual(seen.gutters, ["kyo-sections", "kyo-marks", "kyo-folds"], `${kind}: the gutters from the box's edge in`);
      assert.ok(seen.arrow, `${kind}: the landing arrow, if drawn, is in the fold gutter`);
      if (seen.bars.length) {
        assert.ok(Math.abs(seen.bars[0][0]) < 0.5, `${kind} line ${line}: its section's line is at the box's edge`);
        apart(`${kind} line ${line}: section line and mark`, seen.bars.at(-1), seen.mark);
      }
      apart(`${kind} line ${line}: mark and first character`, seen.mark, seen.first);
      if (folds) {
        apart(`${kind} line ${line}: mark and chevron`, seen.mark, seen.chevron);
        apart(`${kind} line ${line}: chevron and first character`, seen.chevron, seen.first);
      } else {
        assert.equal(seen.chevron, null);
      }
    }
  } finally { await page.close(); }
});
