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
      { session_id: "S1", title: "Today", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
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

test("each result is a block with who, when and the marked words; Load more shows for a next page", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const drawn = await page.run<{ blocks: string[]; marks: string[]; more: boolean; count: string }>(`(() => {
    const ask = window.posted.filter((p) => p.name === "history").pop().body.ask;
    window.kyo.historySearched({ ask, status: 200, document: ${JSON.stringify(answer)} });
    // An answer to an older request is not drawn.
    window.kyo.historySearched({ ask: ask - 1, status: 200, document: { ok: true, results: [] } });
    return {
      blocks: [...document.querySelectorAll("#history-results .history-result .history-who")].map((e) => e.textContent),
      marks: [...document.querySelectorAll("#history-results mark")].map((e) => e.textContent),
      more: !document.getElementById("history-more").hidden,
      count: document.getElementById("history-count").textContent,
    };
  })()`);
  assert.deepEqual(drawn.blocks, ["Claude", "You"]);
  assert.deepEqual(drawn.marks, ["anchor", "anchor"]);
  assert.equal(drawn.more, true);
  assert.equal(drawn.count, "2+ messages");
});

test("a press on a block opens the chat at the message, lights it, and records it as opened", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const result = await page.run<{ chat: boolean; lit: string | null; back: boolean; posted: unknown[] }>(`(() => {
    window.posted.length = 0;
    document.querySelector('#history-results [data-result="0"]').click();
    const lit = document.querySelector("#chat .history-flash");
    return {
      chat: document.getElementById("view-history").hidden,
      lit: lit ? lit.dataset.key : null,
      back: !document.getElementById("history-back").hidden,
      posted: window.posted.filter((p) => p.name === "history").map((p) => p.body),
    };
  })()`);
  assert.equal(result.chat, true);
  assert.equal(result.lit, "r:msg_1");
  assert.equal(result.back, true, "a way back to the results");
  assert.deepEqual(result.posted, [{
    want: "opened", session: "S1", message: "x-1", words: "The anchor mode builds on right-click.",
  }]);
  const back = await page.run<{ view: boolean; kept: number }>(`(() => {
    document.getElementById("history-back").click();
    return {
      view: !document.getElementById("view-history").hidden,
      kept: document.querySelectorAll("#history-results .history-result").length,
    };
  })()`);
  assert.deepEqual(back, { view: true, kept: 2 }, "the results are kept");
});

test("the chips ask again for one party, and the opened results are listed", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const result = await page.run<{ role: unknown; rows: string[] }>(`(() => {
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
