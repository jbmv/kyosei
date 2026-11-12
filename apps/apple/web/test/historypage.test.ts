// Run with `npm test`. The History view (KYO-825): Command-Shift-F opens it, the field asks
// the server with the longer excerpt, each result is a block of its own, a press opens the
// message in the chat and lights it, the chips narrow by who wrote it, Load more asks for the
// next page, and the opened results are listed in the side bar. The server's half is
// tests/test_transcript_search.py and the history routes' tests. A machine with no Chrome
// skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { META, SHIFT, openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const wait = (ms: number) => new Promise((done) => setTimeout(done, ms));

async function open(): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "Today", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null, prompts: 3 },
      { session_id: "S0", title: "Earlier", live: false, first_seen: "2025-12-31T00:00:00Z", remote_control_url: null, prompts: 1 },
      { session_id: "S9", title: "Before the index", live: false, first_seen: "2025-12-01T00:00:00Z", remote_control_url: null, prompts: null },
    ] } });
    window.kyo.apply({ reset: true,
      turns: [{ id: "u-1", at: "2026-01-01T00:00:00Z", text: "Can we spec the anchor mode?" }],
      replies: [{ message_id: "msg_1", uuid: "x-1", at: "2026-01-01T00:00:01Z", text: "The anchor mode builds on right-click." }] });
  })()`);
  return opened;
}

const answer = {
  ok: true, more: true, cursor: "next",
  results: [
    { project: "p", session_id: "S1", message: "x-1", message_id: "msg_1", role: "assistant",
      at: "2026-01-01T00:00:01Z", matches: 1,
      excerpt: [{ text: "The ", match: false }, { text: "anchor", match: true }, { text: " mode builds on right-click.", match: false }] },
    { project: "p", session_id: "S1", message: "u-1", message_id: null, role: "person",
      at: "2026-01-01T00:00:00Z", matches: 1,
      excerpt: [{ text: "Can we spec the ", match: false }, { text: "anchor", match: true }, { text: " mode?", match: false }] },
  ],
};

test("Command-Shift-F opens History, and the field asks with the longer excerpt", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await opened.press("f", META | SHIFT);
  const shown = await opened.run<{ view: boolean; focused: boolean; find: boolean }>(`({
    view: !document.getElementById("view-history").hidden,
    focused: document.activeElement === document.querySelector(".history-search"),
    find: !document.getElementById("find").hidden,
  })`);
  assert.deepEqual(shown, { view: true, focused: true, find: false });
  await opened.run(`window.posted.length = 0`);
  await opened.type("anchor");
  await wait(450);
  const asked = await opened.run<Record<string, unknown>[]>(
    `window.posted.filter((p) => p.name === "history" && p.body.want === "search").map((p) => p.body)`);
  assert.equal(asked.length, 1);
  assert.equal(asked[0].q, "anchor");
  assert.equal(asked[0].role, undefined);
  assert.equal(typeof asked[0].context, "number");
});

test("results are grouped in a folded box per session; each result inside is a block", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const drawn = await page.run<{ groups: string[]; open: boolean[]; blocks: string[]; more: boolean; count: string }>(`(() => {
    const ask = window.posted.filter((p) => p.name === "history").pop().body.ask;
    window.kyo.historySearched({ ask, status: 200, document: ${JSON.stringify(answer)} });
    // An answer to an older request is not drawn.
    window.kyo.historySearched({ ask: ask - 1, status: 200, document: { ok: true, results: [] } });
    document.querySelector("#history-results [data-group]").click();
    return {
      groups: [...document.querySelectorAll("#history-results .history-group-title")].map((e) => e.textContent),
      open: [...document.querySelectorAll("#history-results .history-group")].map((e) => e.classList.contains("open")),
      blocks: [...document.querySelectorAll("#history-results .history-result .history-who")].map((e) => e.textContent),
      more: !document.getElementById("history-more").hidden,
      count: document.getElementById("history-count").textContent,
    };
  })()`);
  assert.deepEqual(drawn.groups, ["Today"], "both results are of one session, in one box");
  assert.deepEqual(drawn.open, [true], "folded at first, opened by its head");
  assert.deepEqual(drawn.blocks, ["Claude", "You"]);
  assert.equal(drawn.more, true);
  assert.equal(drawn.count, "2+ messages in 1 session");
});

test("a press on a block opens the session in History, never in the chat, at the message", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const asked = await page.run<{ history: boolean; wants: string[] }>(`(() => {
    window.posted.length = 0;
    document.querySelector('#history-results [data-result="0"]').click();
    return {
      history: !document.getElementById("view-history").hidden,
      wants: window.posted.filter((p) => p.name === "history").map((p) => p.body.want),
    };
  })()`);
  assert.equal(asked.history, true, "the History view stays");
  assert.deepEqual(asked.wants.sort(), ["marks", "opened", "turns"]);
  const read = await page.run<{ reader: boolean; lit: string | null; outline: string[]; tags: string[]; selected: boolean; nav: boolean }>(`(() => {
    const ask = window.posted.filter((p) => p.body.want === "turns").pop().body.ask;
    window.kyo.historyTurns({ ask, session: "S1", status: 200, document: {
      turns: [{ id: "u-1", at: "2026-01-01T00:00:00Z", text: "Can we spec the anchor mode?", images: [{ index: 0 }] },
        { id: "u-2", at: "2026-01-01T00:00:03Z", text: "attached /home/composer/${"ab".repeat(32)}.png\\nSee the file" }],
      replies: [{ message_id: "msg_1", uuid: "x-1", at: "2026-01-01T00:00:01Z", text: "The anchor mode builds on right-click." }],
      tool_calls: [{ call_id: "c1", uuid: "y1", at: "2026-01-01T00:00:02Z", name: "Bash", input: {}, result: null }] } });
    window.kyo.historyMarks({ ask, session: "S1", status: 200, document: { marks: [
      { note: "n1", text: "Look here", tags: ["layout"], anchors: [{ role: "agent", message: "msg_1", start: 0, end: 3 }] },
      { note: "n2", text: "And here", tags: [], anchors: [{ role: "person", uuid: "u-1", start: 0, end: 3 }] } ] } });
    return {
      reader: !document.getElementById("history-reader").hidden && document.getElementById("history-home-page").hidden,
      lit: document.querySelector("#history-reader .history-flash")?.dataset.hkey ?? null,
      outline: [...document.querySelectorAll("#history-outline .outline-row")].map((r) => r.className.replace("outline-row ", "").replace(" current", "") + ":" + r.textContent),
      tags: [...document.querySelectorAll("#history-tags .mention-name")].map((e) => e.textContent),
      selected: document.getElementById("chat").querySelector(".history-flash") === null,
      nav: !document.getElementById("history-home").disabled,
    };
  })()`);
  assert.equal(read.reader, true);
  assert.equal(read.lit, "r:msg_1");
  assert.deepEqual(read.outline, ["person:Can we spec the anchor mode?", "assistant:The anchor mode builds on right-click.", "person:See the file"],
    "prompts and replies only, the person's set in");
  assert.deepEqual(read.tags, ["#layout"]);
  assert.equal(read.selected, true, "the chat is untouched");
  assert.equal(read.nav, true, "Home returns to the results");
  // KYO-845: the drawer's rows open their places, with the anchors as the server gives them
  // (a reply's by role agent and its message, a prompt's by its uuid), and the reader asks for
  // a prompt's image by the session it reads.
  const drawer = await page.run<{ places: string[]; lit: (string | null)[]; image: unknown; attached: unknown; shown: number; attachments: string[] }>(`(() => {
    // The outline marks the one message last opened; several may still be fading.
    const lit = () => document.querySelector("#history-outline .outline-row.current")?.dataset.key ?? null;
    const rows = [...document.querySelectorAll("#history-anchors .mention-row")];
    const seen = [];
    for (const row of [...rows].reverse()) { row.click(); seen.push(lit()); }
    document.querySelector("#history-tags .mention-row").click();
    seen.push(lit());
    document.querySelector("#history-attachments .mention-row").click();
    seen.push(lit());
    return {
      places: rows.map((row) => row.querySelector(".mention-place").textContent),
      lit: seen,
      image: window.posted.filter((p) => p.name === "image" && p.body.turn).pop()?.body ?? null,
      attached: window.posted.filter((p) => p.name === "image" && p.body.attachment).pop()?.body ?? null,
      shown: document.querySelectorAll("#history-reader .turn .turn-image").length,
      attachments: [...document.querySelectorAll("#history-attachments .mention-name")].map((e) => e.textContent),
    };
  })()`);
  assert.deepEqual(drawer.places, ["on a reply", "on a prompt"]);
  assert.deepEqual(drawer.lit, ["t:u-1", "r:msg_1", "r:msg_1", "t:u-1"], "the prompt's anchor, the reply's, the tag, the attachment");
  assert.deepEqual(drawer.image, { turn: "u-1", index: 0, session: "S1" });
  assert.equal(drawer.shown, 2, "the pasted image and the attached one are drawn in the reader");
  // KYO-850: a file attached to a paragraph is listed too, and asked for by the session read.
  assert.deepEqual(drawer.attachments, ["1 image", "1 attached file"]);
  assert.deepEqual(drawer.attached, { attachment: "ab".repeat(32), session: "S1" });
  const back = await page.run<{ results: number; reader: boolean }>(`(() => {
    document.getElementById("history-home").click();
    return {
      results: document.querySelectorAll("#history-results .history-result").length,
      reader: !document.getElementById("history-reader").hidden,
    };
  })()`);
  assert.deepEqual(back, { results: 2, reader: false }, "the results are kept");
});

test("Command-F in the reader searches the session, and the drawers dock on a press", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.run(`document.getElementById("history-back").click()`);
  await page.press("f", META);
  const found = await page.run<{ find: boolean; placeholder: string }>(`({
    find: !document.getElementById("find").hidden,
    placeholder: document.getElementById("find-field").placeholder,
  })`);
  assert.deepEqual(found, { find: true, placeholder: "Search this session" });
  const docks = await page.run<{ left: boolean; right: boolean; outline: boolean }>(`(() => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    document.getElementById("menu-icon").click();
    document.getElementById("outline-icon").click();
    return {
      left: document.body.classList.contains("dock-left"),
      right: document.body.classList.contains("dock-right"),
      outline: getComputedStyle(document.getElementById("outline")).display !== "none",
    };
  })()`);
  assert.deepEqual(docks, { left: true, right: true, outline: true });
});

test("the chips ask again for one party, and the opened results are listed", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const result = await page.run<{ role: unknown; rows: string[] }>(`(() => {
    document.getElementById("history-home").click();
    window.posted.length = 0;
    document.querySelector('.history-filters [data-role="assistant"]').click();
    const role = window.posted.filter((p) => p.body.want === "search").pop()?.body.role;
    window.kyo.historyRecent({ status: 200, document: { ok: true, entries: [
      { session: "S1", message: "u-1", words: "Can we spec the anchor mode?", opened_at: "2026-01-01T00:00:05Z" },
    ] } });
    return { role, rows: [...document.querySelectorAll("#history-recent .mention-name")].map((e) => e.textContent) };
  })()`);
  assert.equal(result.role, "person");
  assert.deepEqual(result.rows, ["Can we spec the anchor mode?"]);
});


test("the side bar lists the sessions with their prompts, and a press reads one from its start", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const listed = await page.run<{ names: string[]; places: string[]; asked: unknown; reader: boolean }>(`(() => {
    const rows = [...document.querySelectorAll("#history-sessions .mention-row")];
    window.posted.length = 0;
    rows[1].click();
    return {
      names: rows.map((row) => row.querySelector(".mention-name").textContent),
      places: rows.map((row) => row.querySelector(".mention-place").textContent.split(" · ")[0]),
      asked: window.posted.filter((p) => p.name === "history" && p.body.want === "turns").map((p) => p.body.session),
      reader: !document.getElementById("history-reader").hidden,
    };
  })()`);
  assert.deepEqual(listed.names, ["Today", "Earlier", "Before the index"], "newest first");
  assert.deepEqual(listed.places.slice(0, 2), ["3 prompts", "1 prompt"]);
  assert.ok(!listed.places[2].includes("prompt"), "no count where the server gives none");
  assert.deepEqual(listed.asked, ["S0"]);
  assert.equal(listed.reader, true);
});

test("the History search field does not set the Records search, nor the Records field the others", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const fields = await page.run<{ records: string; history: string; files: string }>(`(() => {
    const type = (field, text) => { field.value = text; field.dispatchEvent(new Event("input", { bubbles: true })); };
    const history = document.querySelector(".history-search");
    const records = document.querySelector("#records-side .records-search");
    type(history, "anchor");
    const first = records.value;
    type(history, "");
    type(records, "KYO");
    window.kyo.recordLists({ list: { records: [{ id: "KYO-9", kind: "task", title: "A record", status: "open" }] } });
    const result = { records: first, history: history.value, files: document.querySelector(".files-search").value };
    type(records, "");
    return result;
  })()`);
  assert.deepEqual(fields, { records: "", history: "", files: "" });
});

test("the History drawer is headed and spaced as the homes' lists are (KYO-883)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  const looks = await opened.run<{ head: string[]; homeHead: string[]; row: string; gap: number }>(`(() => {
    [...document.querySelectorAll(".view-item")].find((item) => item.textContent.includes("History")).click();
    const style = (element) => { const s = getComputedStyle(element); return [s.fontSize, s.fontWeight, s.color]; };
    const heads = [...document.querySelectorAll("#history-side > h4")];
    return {
      head: style(heads[1]),
      homeHead: style(document.querySelector(".files-home-side h5")),
      row: getComputedStyle(document.querySelector("#history-sessions .mention-row")).fontSize,
      gap: parseFloat(getComputedStyle(heads[1]).marginTop),
    };
  })()`);
  assert.deepEqual(looks.head, looks.homeHead);
  assert.equal(looks.row, "13px");
  assert.ok(looks.gap >= 14, String(looks.gap));
});
