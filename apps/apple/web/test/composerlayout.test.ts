// Run with `npm test`. The composer's three sections, the session's reminder list, the
// scratchpads and the prompt (KYO-925): the one in use, which holds the caret or held it last,
// takes up to two thirds of the column and scrolls within that, and each other one is drawn as
// its summary, so that all three are on screen at once. A machine with no Chrome skips the
// tests that need a page.
import assert from "node:assert/strict";
import { test } from "node:test";
import { openPage, type Page } from "./chrome.ts";
import { agendaPeek, type AgendaItem } from "../src/agenda.ts";

const START = `window.kyo.project({ project: "p", scope: "server/p" });`;
const LONG = Array.from({ length: 60 }, (_, line) => `line ${line}`).join("\n");
const item = (id: string, author: AgendaItem["author"], checked = false): AgendaItem =>
  ({ id, text: `item ${id}`, author, checked });
const ITEMS = [
  item("p1", "person"), item("p2", "person"), item("p3", "person"), item("p4", "person"), item("p5", "person", true),
  item("o1", "orchestrator"), item("o2", "orchestrator"), item("o3", "orchestrator", true),
];

test("the collapsed list's line counts what is not drawn, by whose it is (KYO-925.c4, KYO-931)", () => {
  const line = (items: AgendaItem[]) => agendaPeek({ state: "shown", session_id: "s", etag: null, items }).line;
  assert.equal(line(ITEMS), "1 more of yours, 2 of Claude's…");
  assert.equal(line(ITEMS.filter((each) => each.author === "orchestrator")), "2 of Claude's…");
  assert.equal(line(ITEMS.filter((each) => each.checked)), "Every reminder is done…");
  assert.equal(line([]), "No reminders…");
  assert.equal(agendaPeek({ state: "unavailable", why: "The list could not be read." }).line, "The list could not be read.");
  assert.equal(agendaPeek({ state: "absent" }).line, "");
  assert.equal(agendaPeek(null).line, "");
});

/** Each section's place and size, and what its summary shows and how. */
const LAYOUT = `(() => {
  const column = document.getElementById("boxes");
  const outer = column.getBoundingClientRect();
  const rect = (element) => {
    const r = element.getBoundingClientRect();
    return { top: Math.round(r.top), bottom: Math.round(r.bottom), height: Math.round(r.height),
      inside: r.height > 0 && r.top >= outer.top - 0.5 && r.bottom <= outer.bottom + 0.5 };
  };
  const look = (element) => {
    const style = getComputedStyle(element);
    return { text: element.textContent, italic: style.fontStyle === "italic", color: style.color,
      shown: element.getBoundingClientRect().height > 0 && style.display !== "none" };
  };
  const muted = (() => {
    const probe = document.createElement("span");
    probe.style.color = "var(--muted)";
    document.body.append(probe);
    const color = getComputedStyle(probe).color;
    probe.remove();
    return color;
  })();
  const main = column.querySelector(":scope > .box.main");
  return {
    inUse: column.dataset.inUse,
    column: column.clientHeight,
    muted,
    agenda: rect(document.getElementById("agenda")),
    agendaSummary: look(document.getElementById("agenda-summary")),
    peek: [...document.querySelectorAll("#agenda-peek > button")].map((button) => ({
      text: button.textContent, shown: button.getBoundingClientRect().height > 0, italic: getComputedStyle(button).fontStyle === "italic" })),
    agendaItems: document.getElementById("agenda-items").getBoundingClientRect().height,
    scratches: rect(document.getElementById("scratches")),
    pads: [...document.querySelectorAll("#scratches > .box")].map((box) => ({
      ...rect(box), head: box.querySelector(".scratch-title").textContent,
      body: Math.round(box.querySelector(".box-body").getBoundingClientRect().height),
    })),
    prompt: rect(main),
    promptBody: Math.round(main.querySelector(".box-body").getBoundingClientRect().height),
    promptSummary: look(main.querySelector(".box-summary")),
    focused: window.kyo.composer.boxes().findIndex((box) => box.focused),
    active: document.activeElement?.id || document.activeElement?.className || null,
  };
})()`;
interface Rect { top: number; bottom: number; height: number; inside: boolean }
interface Look { text: string; italic: boolean; color: string; shown: boolean }
interface Layout {
  inUse: string; column: number; muted: string;
  agenda: Rect; agendaSummary: Look; peek: { text: string; shown: boolean; italic: boolean }[]; agendaItems: number;
  scratches: Rect; pads: (Rect & { head: string; body: number })[];
  prompt: Rect; promptBody: number; promptSummary: Look;
  focused: number; active: string | null;
}

const settle = `new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))`;

async function layout(page: Page): Promise<Layout> {
  await page.run(settle);
  return page.run<Layout>(LAYOUT);
}

/** Two thirds of the column, as the section in use takes it when its content needs it. */
function twoThirds(seen: Layout, height: number): void {
  assert.ok(Math.abs(height - seen.column * 0.66) <= 2, `${height} of ${seen.column}`);
}

/** All three sections on screen, in their order, none over another (KYO-925.c1). */
function allShown(seen: Layout): void {
  assert.ok(seen.agenda.inside && seen.scratches.inside && seen.prompt.inside, JSON.stringify(seen));
  assert.ok(seen.agenda.bottom <= seen.scratches.top && seen.scratches.bottom <= seen.prompt.top, JSON.stringify(seen));
}

async function full(page: Page): Promise<void> {
  await page.reload();
  await page.run(`(() => { ${START}
    window.kyo.draft({ boxes: [
      { id: "a", text: ${JSON.stringify(`first pad\n${LONG}`)}, scratch: {} },
      { id: "b", text: "second pad\\nnotes", scratch: { collapsed: true } },
      { id: "c", text: ${JSON.stringify(`\n  third pad\n${LONG}`)}, scratch: {} },
      { id: "m", text: ${JSON.stringify(`  \nthe prompt's first line\n${LONG}`)} },
    ], etag: "e" });
    window.kyo.agenda({ state: "shown", session_id: "S1", etag: '"e"', items: ${JSON.stringify(ITEMS)} });
  })()`);
}

test("the prompt is in use at first; the list and each scratchpad show their summaries, all on screen (KYO-925.c1, c4, c6)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await full(page);
    const seen = await layout(page);
    assert.equal(seen.inUse, "prompt");
    allShown(seen);
    twoThirds(seen, seen.prompt.height);
    assert.equal(seen.promptSummary.shown, false);
    // The list is its one line: italic, grey, the unchecked items counted by whose they are.
    assert.deepEqual(seen.agendaSummary, { text: "1 more of yours, 2 of Claude's…", italic: true, color: seen.muted, shown: true });
    // KYO-931: above that line, the first three of the person's unchecked items, one line each.
    assert.deepEqual(seen.peek.map((each) => [each.text, each.shown]), [["item p1", true], ["item p2", true], ["item p3", true]]);
    assert.equal(seen.agendaItems, 0);
    assert.ok(seen.agenda.height < 100, JSON.stringify(seen.agenda));
    // Each scratchpad is its head, its first line that holds words, one under another, the
    // collapsed one among them.
    assert.deepEqual(seen.pads.map((pad) => [pad.head, pad.body]), [["first pad", 0], ["second pad", 0], ["third pad", 0]]);
    for (const pad of seen.pads) assert.ok(pad.height < 40, JSON.stringify(pad));
    assert.ok(seen.pads[0].bottom <= seen.pads[1].top && seen.pads[1].bottom <= seen.pads[2].top);
  } finally { await page.close(); }
});

test("a press on a drawn item of the collapsed list opens the list with the caret in its add field (KYO-931.c2)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await full(page);
    await page.run(`document.querySelector("#agenda-peek > button").click()`);
    const seen = await layout(page);
    assert.equal(seen.inUse, "agenda");
    assert.equal(seen.active, "agenda-add");
    assert.deepEqual(seen.peek.map((each) => each.shown), [false, false, false]);
    assert.ok(seen.agendaItems > 0);
  } finally { await page.close(); }
});

test("the section holding the caret grows to two thirds and the others shrink to their summaries, back when the caret leaves (KYO-925.c2, c5)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await full(page);
    await page.run(`window.kyo.composer.select(0, 3)`);
    let seen = await layout(page);
    assert.equal(seen.inUse, "scratch");
    allShown(seen);
    twoThirds(seen, seen.scratches.height);
    // The scratchpads are as their toggles say: the first open, the second collapsed.
    assert.ok(seen.pads[0].body > 100 && seen.pads[1].body === 0, JSON.stringify(seen.pads));
    // The prompt is its first line that holds words and an ellipsis, italic and grey, above its buttons.
    assert.deepEqual(seen.promptSummary, { text: "the prompt's first line…", italic: true, color: seen.muted, shown: true });
    assert.equal(seen.promptBody, 0);
    assert.ok(seen.prompt.height < 80, JSON.stringify(seen.prompt));
    assert.equal(seen.agendaSummary.shown, true);

    // The caret in the prompt: it grows, and the scratchpads go back to their heads.
    await page.run(`window.kyo.composer.select(3, 0)`);
    seen = await layout(page);
    assert.equal(seen.inUse, "prompt");
    allShown(seen);
    twoThirds(seen, seen.prompt.height);
    assert.deepEqual(seen.pads.map((pad) => pad.body), [0, 0, 0]);

    // A press on the list's summary opens it with the caret in its add field, and the prompt
    // shrinks to its first line and gives up the caret.
    await page.run(`document.getElementById("agenda-summary").click()`);
    seen = await layout(page);
    assert.equal(seen.inUse, "agenda");
    assert.equal(seen.active, "agenda-add");
    assert.equal(seen.agendaSummary.shown, false);
    assert.ok(seen.agendaItems > 0);
    assert.equal(seen.promptSummary.shown, true);
    allShown(seen);

    // A press on the prompt's summary puts the caret back where it was.
    await page.run(`document.querySelector("#boxes .box.main .box-summary").click()`);
    seen = await layout(page);
    assert.equal(seen.inUse, "prompt");
    assert.equal(seen.focused, 3);
    assert.deepEqual(await page.run(`(() => { const box = window.kyo.composer.boxes()[3]; return [box.from, box.to]; })()`), [0, 0]);
  } finally { await page.close(); }
});

test("a short section in use takes only what its content needs", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START}
      window.kyo.draft({ boxes: [{ id: "a", text: "pad\\nshort", scratch: {} }, { id: "m", text: "one line" }], etag: "e" });
      window.kyo.composer.select(0, 0);
    })()`);
    const seen = await layout(page);
    assert.equal(seen.inUse, "scratch");
    assert.ok(seen.scratches.height < seen.column / 3, JSON.stringify(seen.scratches));
    // With no session there is no list, and the two sections stand at the foot of the column.
    assert.equal(seen.agenda.height, 0);
    assert.ok(seen.prompt.bottom >= seen.scratches.bottom && seen.prompt.inside);
  } finally { await page.close(); }
});

test("when the focus leaves the composer, the section last used keeps its room (KYO-925.c3)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await full(page);
    await page.run(`window.kyo.composer.select(2, 5)`);
    await page.run(`document.activeElement.blur()`);
    let seen = await layout(page);
    assert.equal(seen.focused, -1);
    assert.equal(seen.inUse, "scratch");
    twoThirds(seen, seen.scratches.height);
    assert.equal(seen.promptSummary.shown, true);
    // The same for the list, left for the chat.
    await page.run(`document.getElementById("agenda-summary").click()`);
    await page.run(`document.activeElement.blur()`);
    seen = await layout(page);
    assert.equal(seen.inUse, "agenda");
    assert.ok(seen.agendaItems > 0 && seen.agendaSummary.shown === false);
  } finally { await page.close(); }
});

test("a press on a scratchpad's head while they are heads goes into it, opening it if collapsed; the focus put in any section opens it", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await full(page);
    await page.run(`document.querySelectorAll("#scratches .scratch-title")[1].click()`);
    let seen = await layout(page);
    assert.equal(seen.inUse, "scratch");
    assert.equal(seen.focused, 1);
    assert.ok(seen.pads[1].body > 0, JSON.stringify(seen.pads[1]));
    assert.deepEqual(await page.run(`[...document.querySelectorAll("#scratches > .box")].map((box) => box.classList.contains("collapsed"))`),
      [false, false, false]);
    // In use, a press on a head folds and opens the scratchpad as it did.
    await page.run(`document.querySelectorAll("#scratches .scratch-toggle")[0].click()`);
    assert.deepEqual(await page.run(`[...document.querySelectorAll("#scratches > .box")].map((box) => box.classList.contains("collapsed"))`),
      [true, false, false]);
    // A focus the page puts in the list, as its own redraw does, opens the list.
    await page.run(`document.getElementById("agenda-add").focus()`);
    seen = await layout(page);
    assert.equal(seen.inUse, "agenda");
    assert.equal(seen.focused, -1);
  } finally { await page.close(); }
});
