// Run with `npm test`. KYO-912: Indent and Outdent in WebKit's menu on a box's words. The page
// tells the app on every right-click whether it was on a box's words, by a `composerMenu`
// message; the app adds the two items to WebKit's menu, which keeps its spelling suggestions,
// and calls `window.kyo.indent()` or `window.kyo.outdent()` when one is chosen. The menu and
// its items are the app's, built in ChatView.swift, and are not seen here. KYO-915: Edit > Undo
// and Edit > Redo. The page tells the app, by an `undo` message, whether a box's words hold the
// focus and whether there is then a step to undo and to redo; the app enables its two items
// from that and calls `window.kyo.undo()` or `window.kyo.redo()` when one is chosen. A machine
// with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { META, openPage, SHIFT, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const START = `window.kyo.project({ project: "p", scope: "server/p" });`;
const read = `(() => { const a = window.kyo.composer.boxes().find((box) => box.focused); return a ? [a.text, a.from, a.to] : null; })()`;
const texts = `window.kyo.composer.boxes().map((box) => box.text)`;

/** Right-clicks the first element the selector finds, and returns what the page posted. */
const rightClick = (selector: string) => page!.run<unknown[]>(`(() => {
  window.posted.length = 0;
  const target = document.querySelector(${JSON.stringify(selector)});
  target.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 10, clientY: 10 }));
  return window.posted.filter((p) => p.name === "composerMenu").map((p) => p.body);
})()`);

test("every right-click tells the app whether it was on a box's words", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  await page.run(`(() => { ${START}
    window.kyo.draft({ boxes: [{ id: "a", text: "first line\\nsecond" }], etag: "e" }); })()`);
  assert.deepEqual(await rightClick("#boxes .cm-line"), [{ composer: true }]);
  assert.deepEqual(await rightClick("#boxes .cm-content"), [{ composer: true }]);
  assert.deepEqual(await rightClick("body"), [{ composer: false }]);
  assert.deepEqual(await rightClick("#chat"), [{ composer: false }]);
  // The page does not take WebKit's menu, which holds the spelling suggestions.
  const shown = await page.run<boolean>(`document.querySelector("#boxes .cm-line")
    .dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true }))`);
  assert.equal(shown, true);
});

test("Indent and Outdent act on the focused box's selection as Tab and Shift-Tab do", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  await page.run(`(() => { ${START}
    window.kyo.draft({ boxes: [{ id: "s", text: "- a\\n- b\\n3. c\\n- d", scratch: { heading: "" } }], etag: "e" });
    window.kyo.composer.select(0, 5, 12); })()`);
  // The same lines and selection as the Tab test in scratchpads.test.ts, by the menu's calls.
  await page.run(`window.kyo.indent()`);
  const indented = await page.run(read);
  assert.deepEqual(indented, ["- a\n  - b\n  1. c\n- d", 4, 16]);
  await page.run(`window.kyo.outdent()`);
  await page.run(`window.kyo.outdent()`);  // nothing left to outdent: still in the box
  const outdented = await page.run(read);
  assert.deepEqual(outdented, ["- a\n- b\n1. c\n- d", 4, 12]);

  // The same from the keys, on the same text, for the comparison.
  await page.run(`(() => {
    window.kyo.draft({ boxes: [{ id: "s", text: "- a\\n- b\\n3. c\\n- d", scratch: { heading: "" } }], etag: "e2" });
    window.kyo.composer.select(0, 5, 12); })()`);
  await page.press("Tab");
  assert.deepEqual(await page.run(read), indented);
  await page.press("Tab", SHIFT);
  await page.press("Tab", SHIFT);
  assert.deepEqual(await page.run(read), outdented);

  // A caret on a plain line, in a prompt box.
  await page.run(`(() => {
    window.kyo.draft({ boxes: [{ id: "a", text: "plain" }], etag: "e3" });
    window.kyo.composer.select(0, 5); })()`);
  await page.run(`window.kyo.indent()`);
  assert.deepEqual(await page.run(read), ["  plain", 7, 7]);
  await page.run(`window.kyo.outdent()`);
  assert.deepEqual(await page.run(read), ["plain", 5, 5]);
});

test("with no box focused, Indent and Outdent do nothing", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  await page.run(`(() => { ${START}
    window.kyo.draft({ boxes: [{ id: "a", text: "- one\\n- two" }], etag: "e" });
    window.kyo.composer.select(0, 8);
    document.activeElement.blur(); })()`);
  assert.equal(await page.run(read), null, "no box has the focus");
  const before = await page.run<string[]>(texts);
  await page.run(`window.kyo.indent()`);
  await page.run(`window.kyo.outdent()`);
  assert.deepEqual(await page.run<string[]>(texts), before);
  assert.equal(await page.run(`document.activeElement === document.body`), true, "the focus is not moved into a box");
});

// What the page last told the app of Undo and Redo, read once the telling has run.
const told = () => page!.run<{ box: boolean; undo: boolean; redo: boolean } | undefined>(
  `new Promise((done) => setTimeout(() => done(window.posted.filter((p) => p.name === "undo").at(-1)?.body), 30))`);
// The box's words, and what each gutter mark holds.
const held = () => page!.run<[string[], string[]]>(`[window.kyo.composer.boxes().map((box) => box.text),
  [...document.querySelectorAll("#boxes .mark.held")].flatMap((mark) => mark.title.split("\\n"))]`);
const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

test("Undo and Redo from the menu take back and put back an anchor, a tag and an attachment, and the app is told when there is a step (KYO-915.c1, c2)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  await page.run(`(() => { ${START}
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.apply({ reset: true,
      turns: [{ id: "u1", at: "2026-01-01T00:00:00Z", text: "A prompt of several words." }],
      replies: [{ message_id: "m1", uuid: "x1", at: "2026-01-01T00:00:01Z", text: "First paragraph of the reply here." }] });
    window.kyo.draft({ boxes: [{ id: "a", text: "abc" }], etag: "e" });
    window.kyo.composer.select(0, 3); })()`);
  // A box with the caret and no step: the items are the box's, and neither is enabled.
  assert.deepEqual(await told(), { box: true, undo: false, redo: false });
  assert.equal(await page.run(`window.kyo.undo()`), false);
  assert.equal(await page.run(`window.kyo.redo()`), false);

  // Each of the three is placed, taken back with the words left, and put back.
  const steps: [string, () => Promise<unknown>][] = [
    ["anchor: First paragraph of the reply here.", () => page!.run(`(() => {
      const target = document.querySelector("#chat .reply p");
      const at = target.getBoundingClientRect();
      target.dispatchEvent(new MouseEvent("contextmenu", {
        bubbles: true, cancelable: true, clientX: at.left + 30, clientY: at.top + at.height / 2,
      }));
      document.querySelector("#anchor-menu .anchor-new").click();
    })()`)],
    ["#review", async () => {
      await page!.run(`(() => {
        const line = document.querySelector("#boxes .cm-line").getBoundingClientRect();
        document.querySelector("#boxes .cm-content").dispatchEvent(new MouseEvent("mousemove",
          { bubbles: true, clientX: line.left + 10, clientY: line.top + line.height / 2 }));
        document.querySelector("#boxes .mark").click();
        // The panel of a mark that holds something leaves the caret in the box.
        document.querySelector(".mark-panel input").focus();
      })()`);
      await page!.type("review");
      // The tag is typed in the panel's field, which is not a box: the items are WebKit's there.
      assert.deepEqual(await told(), { box: false, undo: false, redo: false });
      assert.equal(await page!.run(`window.kyo.undo()`), false);
      await page!.press("Enter");
      await page!.run(`window.kyo.composer.select(0, 3)`);
    }],
    ["attached: shot.png", async () => {
      await page!.run(`(() => {
        const data = new DataTransfer();
        data.items.add(new File([Uint8Array.from(atob(${JSON.stringify(PNG)}), (c) => c.charCodeAt(0))], "shot.png", { type: "image/png" }));
        document.activeElement.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
      })()`);
      await page!.run(`new Promise((done) => setTimeout(done, 200))`);
      const ref = await page!.run<string>(`window.posted.filter((m) => m.name === "attach").at(-1).body.ref`);
      await page!.run(`window.kyo.attached({ ref: ${JSON.stringify(ref)}, id: "f1", path: "/tmp/shot.png" })`);
    }],
  ];
  const placed: string[] = [];
  for (const [title, place] of steps) {
    await place();
    assert.deepEqual(await held(), [["abc"], [...placed, title]], `${title} is placed`);
    assert.deepEqual(await told(), { box: true, undo: true, redo: false }, `${title} is a step to undo`);
    assert.equal(await page.run(`window.kyo.undo()`), true);
    assert.deepEqual(await held(), [["abc"], placed], `Undo removes ${title} and leaves the rest`);
    assert.deepEqual(await told(), { box: true, undo: placed.length > 0, redo: true }, `${title} is a step to redo`);
    assert.equal(await page.run(`window.kyo.redo()`), true);
    assert.deepEqual(await held(), [["abc"], [...placed, title]], `Redo puts ${title} back`);
    placed.push(title);
  }

  // With the focus in no box the two calls do nothing, and the app is told the items are not a box's.
  await page.run(`document.activeElement.blur()`);
  assert.deepEqual(await told(), { box: false, undo: false, redo: false });
  assert.equal(await page.run(`window.kyo.undo()`), false);
  assert.deepEqual(await held(), [["abc"], placed]);
});

// ---------------------------------------------------------------------------------------------
// KYO-975: emphasis drawn live in a box, Command-B and Command-I, and Bold, Italic and Regular.

/** What the focused box shows: each line's visible text, and the classes of its emphasis runs. */
const drawn = () => page!.run<{ shown: string[]; italic: string[]; bold: string[]; code: string[] }>(`(() => {
  const words = (selector) => [...document.querySelectorAll("#boxes " + selector)].map((node) => node.textContent);
  return { shown: [...document.querySelectorAll("#boxes .cm-line")].map((line) => line.textContent),
    italic: words(".md-em"), bold: words(".md-strong"), code: words(".md-code") };
})()`);
const settle = () => page!.run(`new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))`);
const open = async (text: string, scratch = false) => {
  await page!.reload();
  await page!.run(`(() => { ${START}
    window.kyo.draft({ boxes: [{ id: "a", text: ${JSON.stringify(text)}${scratch ? `, scratch: { heading: "" }` : ""} }], etag: "e" }); })()`);
};

test("emphasis draws live with the markers hidden, in a prompt box and in a scratchpad (KYO-975.c1)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  for (const scratch of [false, true]) {
    await open("plain *soft* and **loud** and `code` end", scratch);
    // The box holds the focus with the caret at the start, outside every run.
    await page.run(`window.kyo.composer.select(0, 0)`);
    await settle();
    const now = await drawn();
    assert.deepEqual(now.italic.map((words) => words), ["soft"], "the markers are not in the drawn text");
    assert.deepEqual(now.bold, ["loud"]);
    assert.deepEqual(now.code, ["code"]);
    assert.equal(now.shown[0], "plain soft and loud and code end");
    // The italic and bold computed styles differ from the plain text's.
    const styles = await page.run<string[]>(`(() => ["#boxes .md-em", "#boxes .md-strong", "#boxes .md-code"]
      .map((selector) => { const s = getComputedStyle(document.querySelector(selector));
        return [s.fontStyle, s.fontWeight, s.fontFamily.includes("mono") || s.fontFamily.includes("Menlo")].join("/"); }))()`);
    assert.equal(styles[0].split("/")[0], "italic");
    assert.ok(Number(styles[1].split("/")[1]) > 500, `bold weight ${styles[1]}`);
    assert.ok(styles[2].endsWith("/true"), `code font ${styles[2]}`);
  }
  // A marker that opens nothing, and a run across a space, stay as typed, as in a sent prompt.
  await open("a * b and * c* and **x** done");
  await page.run(`window.kyo.composer.select(0, 0)`);
  await settle();
  const odd = await drawn();
  assert.deepEqual(odd.bold, ["x"]);
  assert.deepEqual(odd.italic, []);
  assert.equal(odd.shown[0], "a * b and * c* and x done");
});

test("a box and a sent prompt read the same runs, nested ones included (KYO-975.c1)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const lines = ["**bold with *inner* italic**", "*one* and *two*", "`a *b* c`", "**un*closed", "x*y*z"];
  await open(lines.join("\n"));
  await page.run(`window.kyo.composer.select(0, ${lines.join("\n").length})`);
  await settle();
  const inBox = await drawn();
  // The same lines as a sent prompt: the chat draws a turn of them with `emphasis`.
  await page.run(`(() => { window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null }] } });
    window.kyo.apply({ reset: true, turns: [{ id: "u1", at: "2026-01-01T00:00:00Z", text: ${JSON.stringify(lines.join("\n"))} }], replies: [] }); })()`);
  const sent = await page.run<{ italic: string[]; bold: string[]; code: string[] }>(`(() => {
    const words = (selector) => [...document.querySelectorAll("#chat .turn " + selector)].map((node) => {
      const copy = node.cloneNode(true);
      copy.querySelectorAll(".md-mark").forEach((mark) => mark.remove());
      return copy.textContent; });
    return { italic: words("em"), bold: words("strong"), code: words("code") }; })()`);
  assert.deepEqual(inBox.italic.sort(), sent.italic.sort());
  assert.deepEqual(inBox.bold, ["bold with inner italic"], "the nested run's markers are not drawn");
  assert.deepEqual(inBox.bold.sort(), sent.bold.sort());
  assert.deepEqual(inBox.code.sort(), sent.code.sort());
  assert.ok(sent.italic.length >= 4 && sent.bold.length === 1, "the lines hold runs to compare");
});

test("the markers of the run the caret or selection touches are shown, and hide again as it moves (KYO-975.c2)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const text = "aa **bold** bb *it* cc";
  await open(text);
  const at = (from: number, to = from) => page!.run(`window.kyo.composer.select(0, ${from}, ${to})`).then(settle).then(drawn);
  assert.equal((await at(0)).shown[0], "aa bold bb it cc");
  // Touching the opening marker, inside the words, and touching the closing marker.
  assert.equal((await at(3)).shown[0], "aa **bold** bb it cc");
  assert.equal((await at(7)).shown[0], "aa **bold** bb it cc");
  assert.equal((await at(11)).shown[0], "aa **bold** bb it cc");
  // One space past it, the markers are hidden again; the other run is untouched until reached.
  assert.equal((await at(12)).shown[0], "aa bold bb it cc");
  assert.equal((await at(16)).shown[0], "aa bold bb *it* cc");
  // A selection that crosses both runs shows both.
  assert.equal((await at(1, 17)).shown[0], text);
  // Arrow keys move the caret into a run and out of it.
  await at(2);
  assert.equal((await drawn()).shown[0], "aa bold bb it cc");
  await page.press("ArrowRight");
  assert.equal((await settle().then(drawn)).shown[0], "aa **bold** bb it cc");
  // The box that has lost its focus draws the run, whatever its caret holds.
  await page.run(`document.activeElement.blur()`);
  assert.equal((await settle().then(drawn)).shown[0], "aa bold bb it cc");
  // Typed text is typed: the closing marker typed by hand makes a run, hidden on moving away.
  await open("");
  await page.run(`window.kyo.composer.select(0, 0)`);
  await page.type("a *word*");
  assert.equal((await settle().then(drawn)).shown[0], "a *word*", "the caret is at the closing marker");
  await page.type(" and more");
  assert.equal((await settle().then(drawn)).shown[0], "a word and more");
});

test("the drawing leaves the text, the saved draft, a copy and the marks as typed (KYO-975.c3)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const text = "one *two* **three**\nfour";
  await open(text);
  await page.run(`window.kyo.composer.select(0, 20)`);
  await page.run(`window.kyo.composer.tag(0, 0, "review")`);
  await page.run(`window.kyo.composer.select(0, 0)`);
  await settle();
  assert.equal((await drawn()).shown[0], "one two three", "the run is drawn");
  assert.deepEqual(await page.run(`window.kyo.composer.boxes().map((box) => box.text)`), [text]);
  // The saved draft holds the markers and the tag on its line.
  await page.run(`window.posted.length = 0; window.kyo.composer.select(0, 20, 23)`);
  await page.type("");
  const saved = await page.run<{ text: string; marks?: { line: number }[] }[]>(`(async () => {
    await new Promise((done) => setTimeout(done, 1300));
    return window.posted.filter((p) => p.name === "saveDraft").flatMap((p) => p.body.boxes); })()`);
  assert.ok(saved.every((box) => box.text === text), `saved ${JSON.stringify(saved)}`);
  // A copy of the selection, which crosses the hidden markers, carries them.
  await page.run(`window.kyo.composer.select(0, 4, 19)`);
  const copied = await page.run<string>(`(() => { const data = new DataTransfer();
    document.activeElement.dispatchEvent(new ClipboardEvent("copy", { clipboardData: data, bubbles: true, cancelable: true }));
    return data.getData("text/plain"); })()`);
  assert.equal(copied, "*two* **three**");
  assert.deepEqual(await page.run(`[...document.querySelectorAll("#boxes .mark.held")].map((mark) => mark.title)`).then((marks) => (marks as string[]).length), 1, "the tag stays on its line");
});

test("Command-B and Command-I wrap a selection, unwrap a run, and act on the word at the caret (KYO-975.c4)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const state = () => page!.run<[string, number, number]>(read);
  await open("alpha beta gamma");
  await page.run(`window.kyo.composer.select(0, 6, 10)`);
  await page.press("b", META);
  assert.deepEqual(await state(), ["alpha **beta** gamma", 8, 12], "bold wraps and keeps the words selected");
  await page.press("b", META);
  assert.deepEqual(await state(), ["alpha beta gamma", 6, 10], "bold again removes the markers");
  await page.press("i", META);
  assert.deepEqual((await state())[0], "alpha *beta* gamma");
  await page.press("i", META);
  assert.deepEqual((await state())[0], "alpha beta gamma");
  // A selection that is itself a run, markers and all, is unwrapped.
  await open("alpha **beta** gamma");
  await page.run(`window.kyo.composer.select(0, 6, 14)`);
  await page.press("b", META);
  assert.deepEqual((await state())[0], "alpha beta gamma");
  // Italic on bold text wraps; italic does not remove bold.
  await open("alpha **beta** gamma");
  await page.run(`window.kyo.composer.select(0, 6, 14)`);
  await page.press("i", META);
  assert.deepEqual((await state())[0], "alpha ***beta*** gamma");
  // Spaces at the selection's ends stay outside the markers, a selection over lines wraps each.
  await open("  one two  \nthree");
  await page.run(`window.kyo.composer.select(0, 0, 17)`);
  await page.press("b", META);
  assert.deepEqual((await state())[0], "  **one two**  \n**three**");
  // With no selection the word at the caret is the target, and a caret in a space does nothing.
  await open("alpha beta gamma");
  await page.run(`window.kyo.composer.select(0, 8)`);
  await page.press("b", META);
  assert.deepEqual((await state())[0], "alpha **beta** gamma");
  await page.press("b", META);
  assert.deepEqual((await state())[0], "alpha beta gamma");
  await open("alpha  beta");
  await page.run(`window.kyo.composer.select(0, 6)`);
  await page.press("b", META);
  assert.deepEqual((await state())[0], "alpha  beta", "no word at the caret, nothing is done");
  // It is one undo step.
  await open("alpha beta");
  await page.run(`window.kyo.composer.select(0, 6, 10)`);
  await page.press("b", META);
  await page.press("z", META);
  assert.deepEqual((await state())[0], "alpha beta");
});

test("Bold, Italic and Regular of the menu act as the keys do, and Regular removes the markers (KYO-975.c5)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const text = () => page!.run<string>(`window.kyo.composer.boxes()[0].text`);
  await open("alpha beta gamma");
  await page.run(`window.kyo.composer.select(0, 6, 10)`);
  await page.run(`window.kyo.bold()`);
  assert.equal(await text(), "alpha **beta** gamma");
  await page.run(`window.kyo.regular()`);
  assert.equal(await text(), "alpha beta gamma");
  await page.run(`window.kyo.italic()`);
  assert.equal(await text(), "alpha *beta* gamma");
  // Regular over a selection of several runs removes each run's markers, and not code's.
  await open("*a* b **c** `d` *e*");
  await page.run(`window.kyo.composer.select(0, 0, 14)`);
  await page.run(`window.kyo.regular()`);
  assert.equal(await text(), "a b c `d` *e*");
  // Nested runs lose both pairs; a partly selected run loses its markers whole.
  await open("**bold *in* x** tail");
  await page.run(`window.kyo.composer.select(0, 8, 12)`);
  await page.run(`window.kyo.regular()`);
  assert.equal(await text(), "bold in x tail");
  await open("one **two** three");
  await page.run(`window.kyo.composer.select(0, 6, 8)`);
  await page.run(`window.kyo.regular()`);
  assert.equal(await text(), "one two three");
  // A caret in a run, and nothing selected: that run only.
  await open("*a* *b*");
  await page.run(`window.kyo.composer.select(0, 5)`);
  await page.run(`window.kyo.regular()`);
  assert.equal(await text(), "*a* b");
  // Text with no emphasis is left alone.
  await open("plain");
  await page.run(`window.kyo.composer.select(0, 0, 5)`);
  await page.run(`window.kyo.regular()`);
  assert.equal(await text(), "plain");
  // With no box focused the three do nothing.
  await open("alpha beta");
  await page.run(`window.kyo.composer.select(0, 0, 5); document.activeElement.blur()`);
  await page.run(`window.kyo.bold(); window.kyo.italic(); window.kyo.regular()`);
  assert.equal(await text(), "alpha beta");
});

test("inline code in a box draws as code with its backticks hidden away from the caret (KYO-975.c6)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await open("run `make test` now");
  await page.run(`window.kyo.composer.select(0, 0)`);
  assert.equal((await settle().then(drawn)).shown[0], "run make test now");
  await page.run(`window.kyo.composer.select(0, 8)`);
  const inside = await settle().then(drawn);
  assert.equal(inside.shown[0], "run `make test` now");
  assert.deepEqual(inside.code, ["`make test`"]);
});
