// Run with `npm test`. Inline emphasis in the person's prompts (KYO-967): `*italic*`, `**bold**`
// and `code` between backticks are drawn as such in a prompt, with the markers kept in the text
// at no width, so that a selection, an anchor and a copy still give what was typed. No block
// construct is read and nothing is taken as HTML. A machine with no Chrome skips the page tests.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { META, SHIFT, openPage, type Page } from "./chrome.ts";
import { emphasis, typedText } from "../src/emphasis.ts";

test("emphasis reads italic, bold and code, and every piece's text is what was typed (KYO-967.c1)", () => {
  assert.deepEqual(emphasis("a *b* c"), [
    { kind: "text", text: "a " }, { kind: "em", marker: "*", body: [{ kind: "text", text: "b" }] }, { kind: "text", text: " c" },
  ]);
  assert.deepEqual(emphasis("**b**"), [{ kind: "strong", marker: "**", body: [{ kind: "text", text: "b" }] }]);
  assert.deepEqual(emphasis("`x *y*`"), [{ kind: "code", text: "`x *y*`" }]);
  assert.deepEqual(emphasis("**a *b* c**"), [{
    kind: "strong", marker: "**",
    body: [{ kind: "text", text: "a " }, { kind: "em", marker: "*", body: [{ kind: "text", text: "b" }] }, { kind: "text", text: " c" }],
  }]);
  for (const line of ["a *b* c", "**b** and `c`", "2 * 3 * 4", "* not a list *", "**open", "*", "``", "a ** b ** c", "<b>x</b> *y*"]) {
    assert.equal(typedText(emphasis(line)), line, line);
  }
});

test("a marker that opens nothing stays as typed (KYO-967.c2)", () => {
  for (const line of ["2 * 3 * 4", "* item", "**", "*unclosed", "# heading", "- list"]) {
    assert.deepEqual(emphasis(line).filter((piece) => piece.kind !== "text"), [], line);
  }
});

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const UUID = "9b949b65-606d-47fa-bbfd-f46f11c8635d";
const TYPED = "one *italic* two **bold** three `code` four";

async function open(text: string): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.apply({ reset: true, turns: [{ id: "u1", at: "2026-01-01T00:00:00Z", text: ${JSON.stringify(text)} }], replies: [] });
  })()`);
  return opened;
}

const look = (opened: Page) => opened.run<{
  text: string; em: string[]; strong: string[]; code: string[]; shownText: string; weight: string; style: string; mono: string; width: number[];
}>(`(() => {
  const turn = document.querySelector("#chat .turn");
  const shown = (e) => [...e.childNodes].filter((n) => !(n.classList?.contains("md-mark"))).map((n) => n.textContent).join("");
  const em = turn.querySelector("em"), strong = turn.querySelector("strong"), code = turn.querySelector("code");
  return { text: turn.textContent,
    em: [...turn.querySelectorAll("em")].map(shown), strong: [...turn.querySelectorAll("strong")].map(shown),
    code: [...turn.querySelectorAll("code")].map(shown),
    shownText: [...turn.childNodes].map((n) => n.nodeType === 3 ? n.textContent : n.tagName ? shown(n) : "").join(""),
    weight: strong ? getComputedStyle(strong).fontWeight : "", style: em ? getComputedStyle(em).fontStyle : "",
    mono: code ? getComputedStyle(code).fontFamily : "",
    width: [...turn.querySelectorAll(".md-mark")].map((m) => Math.round(m.getBoundingClientRect().width)) };
})()`);

test("a prompt shows italic, bold and code with the markers not shown (KYO-967.c1)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open(TYPED);
  const seen = await look(opened);
  assert.deepEqual(seen.em, ["italic"]);
  assert.deepEqual(seen.strong, ["bold"]);
  assert.deepEqual(seen.code, ["code"]);
  assert.equal(seen.shownText, "one italic two bold three code four");
  assert.equal(seen.style, "italic");
  assert.ok(Number(seen.weight) >= 600, seen.weight);
  assert.match(seen.mono, /mono|Menlo/i);
  assert.ok(seen.width.length === 6 && seen.width.every((width) => width === 0), JSON.stringify(seen.width));
  // The text is what was typed, markers included, for a selection and for an anchor to measure.
  assert.equal(seen.text, TYPED);
});

test("line breaks, held lines, chips and block-looking lines draw as before (KYO-967.c2)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const text = `# not a heading\n- not a list\n\non your reply ${UUID} "First paragraph of..."\n> not a quote\n*em* line`;
  const opened = await open(text);
  const seen = await opened.run<{ blocks: number; text: string; chips: number; breaks: string }>(`(() => {
    const turn = document.querySelector("#chat .turn");
    return { blocks: turn.querySelectorAll("h1, ul, li, blockquote, hr, pre, p").length, text: turn.textContent,
      chips: turn.querySelectorAll(".turn-anchor").length, breaks: getComputedStyle(turn).whiteSpace };
  })()`);
  assert.equal(seen.blocks, 0);
  assert.equal(seen.chips, 1);
  assert.equal(seen.breaks, "pre-wrap");
  assert.ok(seen.text.startsWith("# not a heading\n- not a list\n\n"), JSON.stringify(seen.text));
  assert.ok(seen.text.endsWith("> not a quote\n*em* line"), JSON.stringify(seen.text));
});

test("HTML in a prompt shows as text and is not interpreted (KYO-967.c3)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const text = `<img src=x onerror="window.pwned=1"> *a* <b>bold</b> **<i>x</i>**`;
  const opened = await open(text);
  const seen = await opened.run<{ text: string; tags: string[]; pwned: unknown }>(`(() => {
    const turn = document.querySelector("#chat .turn");
    return { text: turn.textContent, tags: [...turn.querySelectorAll("*")].map((e) => e.tagName.toLowerCase()), pwned: window.pwned ?? null };
  })()`);
  assert.equal(seen.text, text);
  assert.ok(seen.tags.every((tag) => ["span", "em", "strong", "code"].includes(tag)), seen.tags.join());
  assert.equal(seen.pwned, null);
});

test("an anchor placed on a prompt measures the typed text, markers included (KYO-967.c4)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open(TYPED);
  await opened.run(`window.kyo.composer.select(0, 0)`);
  const seen = await opened.run<{ highlighted: string[]; held: string[] }>(`(() => {
    const target = document.querySelector("#chat .turn");
    const box = target.getBoundingClientRect();
    target.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: box.left + 30, clientY: box.top + box.height / 2 }));
    document.querySelector("#anchor-menu .anchor-new").click();
    return { highlighted: [...(CSS.highlights.get("anchored") ?? [])].map((range) => range.toString()),
      held: [...document.querySelectorAll("#boxes .mark.held")].flatMap((mark) => mark.title.split("\\n")) };
  })()`);
  assert.deepEqual(seen.highlighted, [TYPED]);
  assert.equal(seen.held.length, 1, JSON.stringify(seen.held));
  assert.ok(seen.held[0].includes("*italic*"), seen.held[0]);
});

test("a selection of a prompt copies as typed, markers included (KYO-967.c4)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open(TYPED);
  const copied = await opened.run<string>(`(() => {
    const turn = document.querySelector("#chat .turn");
    const range = document.createRange(); range.selectNodeContents(turn);
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    return selection.toString();
  })()`);
  assert.equal(copied, TYPED);
});

test("the History reader draws a prompt's emphasis the same way (KYO-967.c5)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open("hello");
  await opened.press("f", META | SHIFT);
  await opened.type("hello");
  await new Promise((done) => setTimeout(done, 450));
  const seen = await opened.run<{ em: string[]; strong: string[]; code: string[]; text: string }>(`(() => {
    const ask = window.posted.filter((p) => p.name === "history").pop().body.ask;
    window.kyo.historySearched({ ask, status: 200, document: { ok: true, more: false, cursor: null, results: [
      { project: "p", session_id: "S1", message: "u1", message_id: null, role: "person", at: "2026-01-01T00:00:00Z", matches: 1,
        excerpt: [{ text: "hello", match: true }] } ] } });
    document.querySelector("#history-results [data-group]").click();
    document.querySelector('#history-results [data-result="0"]').click();
    const turns = window.posted.filter((p) => p.body.want === "turns").pop().body.ask;
    window.kyo.historyTurns({ ask: turns, session: "S1", status: 200, document: {
      turns: [{ id: "u1", at: "2026-01-01T00:00:00Z", text: ${JSON.stringify(TYPED)} }], replies: [], tool_calls: [] } });
    const turn = document.querySelector("#history-reader .turn");
    const shown = (e) => [...e.childNodes].filter((n) => !n.classList?.contains("md-mark")).map((n) => n.textContent).join("");
    return { em: [...turn.querySelectorAll("em")].map(shown), strong: [...turn.querySelectorAll("strong")].map(shown),
      code: [...turn.querySelectorAll("code")].map(shown), text: turn.textContent };
  })()`);
  assert.deepEqual(seen, { em: ["italic"], strong: ["bold"], code: ["code"], text: TYPED });
});
