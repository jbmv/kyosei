// Run with `npm test`. KYO-883: the chat holds its place while new output arrives, the arrow at
// its foot counts the messages below, and the person's own prompt is shown when sent: at once
// and dimmed for a prompt sent to an idle session, undimmed when the server confirms it, and
// replaced by the transcript's turn. A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const AT = "2026-11-12T10:00:00.000Z";
const later = (seconds: number) => new Date(Date.parse(AT) + seconds * 1000).toISOString();
const long = "A paragraph of reply that takes up some room on the page. ".repeat(12);

/** The first read of a session: enough replies to scroll. */
const firstLoad = JSON.stringify({
  turns: [{ id: "t1", at: AT, text: "The first prompt" }],
  replies: Array.from({ length: 12 }, (_, index) => ({ message_id: `m${index}`, at: later(index + 1), text: long })),
});

interface Place { end: boolean; arrow: boolean; count: string; top: number }
const where = `(() => {
  const page = document.getElementById("page");
  return {
    end: page.scrollHeight - page.scrollTop - page.clientHeight < 8,
    arrow: document.getElementById("jump-bottom").classList.contains("shown"),
    count: document.getElementById("jump-count").textContent,
    top: page.scrollTop,
  };
})()`;

async function openSession(here: Page): Promise<void> {
  await here.reload();
  await here.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "${AT}", remote_control_url: null },
    ] } });
    window.kyo.screen({ screen: { state: "idle" } });
    window.kyo.apply(${firstLoad});
  })()`);
}

test("a session's first read lands at its end, and later output is counted, not followed", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await openSession(page);
  let now = await page.run<Place>(where);
  assert.deepEqual([now.end, now.arrow, now.count], [true, false, ""]);
  const before = now.top;
  // Two replies arrive while the reader is at the end: the page stays, and the arrow counts them.
  await page.run(`window.kyo.apply(${JSON.stringify({ replies: [
    { message_id: "n1", at: later(60), text: long }, { message_id: "n2", at: later(61), text: long },
  ] })})`);
  now = await page.run<Place>(where);
  assert.equal(now.top, before);
  assert.deepEqual([now.end, now.arrow, now.count], [false, true, "New messages: 2"]);
  // A tool call is output but not a message: the count stays.
  await page.run(`window.kyo.apply(${JSON.stringify({ tool_calls: [
    { call_id: "c1", at: later(62), name: "Bash", input: { command: "ls" }, result: { text: "a", is_error: false } },
  ] })})`);
  assert.equal((await page.run<Place>(where)).count, "New messages: 2");
  // Reaching the end clears the count and the arrow.
  await page.run(`(() => { const p = document.getElementById("page"); p.scrollTop = p.scrollHeight; p.dispatchEvent(new Event("scroll")); })()`);
  now = await page.run<Place>(where);
  assert.deepEqual([now.end, now.arrow, now.count], [true, false, ""]);
});

test("a prompt sent to an idle session shows at once, dimmed, and is replaced by its turn", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await openSession(page);
  // The reader has scrolled up; sending brings them to their prompt.
  await page.run(`document.getElementById("page").scrollTop = 0`);
  await page.run(`window.kyo.draft({ boxes: [{ id: "a", text: "Please look at the arrow" }], etag: "e1" })`);
  await page.run(`document.querySelector("#boxes .box .box-send").click()`);
  const preview = `(() => {
    const shown = document.querySelector("#chat .turn.sending, #chat .turn:last-child");
    return { text: shown?.textContent ?? "", dim: shown?.classList.contains("sending") ?? false,
      turns: document.querySelectorAll("#chat .turn").length };
  })()`;
  let shown = await page.run<{ text: string; dim: boolean; turns: number }>(preview);
  assert.deepEqual([shown.text, shown.dim, shown.turns], ["Please look at the arrow", true, 2]);
  assert.equal((await page.run<Place>(where)).end, true);
  // The server queues every prompt and answers with its id: the preview stays, dimmed, and
  // the held box is not shown for it.
  const heldShown = `!document.getElementById("held").hidden`;
  await page.run(`window.kyo.sent({ id: "m1" })`);
  await page.run(`window.kyo.held({ outcome: "held", id: "m1", held: { id: "m1", retrying: true, reason: null, detail: null } })`);
  shown = await page.run(preview);
  assert.deepEqual([shown.dim, shown.turns, await page.run<boolean>(heldShown)], [true, 2, false]);
  // The queue delivered it: the preview loses its dimming.
  await page.run(`window.kyo.held({ outcome: "delivered", id: "m1", held: null })`);
  shown = await page.run(preview);
  assert.deepEqual([shown.dim, shown.turns], [false, 2]);
  // The transcript's turn takes its place, and the chat shows it.
  await page.run(`document.getElementById("page").scrollTop = 0`);
  await page.run(`window.kyo.apply(${JSON.stringify({ turns: [{ id: "t2", at: later(90), text: "Please look at the arrow" }] })})`);
  shown = await page.run(preview);
  assert.deepEqual([shown.text, shown.dim, shown.turns], ["Please look at the arrow", false, 2]);
  assert.equal((await page.run<Place>(where)).end, true);
});

test("a prompt's turn takes the preview's place though the server wrote anchor lines into it", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await openSession(page);
  const typed = "Short one.\n\nAnd the second paragraph, which is longer than the first.";
  await page.run(`window.kyo.draft(${JSON.stringify({ boxes: [{ id: "a", text: typed }], etag: "e1" })})`);
  await page.run(`document.querySelector("#boxes .box .box-send").click()`);
  await page.run(`window.kyo.sent({ id: "m1" })`);
  await page.run(`window.kyo.held({ outcome: "delivered", id: "m1", held: null })`);
  const anchored = '[re: KYO-883 at "the arrow"]\nShort one.\n\n[re: KYO-884 at "red"]\nAnd the second paragraph, which is longer than the first.';
  await page.run(`window.kyo.apply(${JSON.stringify({ turns: [{ id: "t2", at: later(90), text: anchored }] })})`);
  const turns = await page.run<{ previews: number; turns: number }>(`({
    previews: document.querySelectorAll("#chat .turn:not([data-key])").length,
    turns: document.querySelectorAll("#chat .turn").length,
  })`);
  assert.deepEqual(turns, { previews: 0, turns: 2 });
});

test("a previewed prompt not delivered within the grace period is held: the preview goes and Edit and Send now are offered", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await openSession(page);
  await page.run(`window.kyo.draft({ boxes: [{ id: "a", text: "Sent as a turn began" }], etag: "e1" })`);
  await page.run(`document.querySelector("#boxes .box .box-send").click()`);
  await page.run(`window.kyo.sent({ id: "m1" })`);
  // The session began a turn, and the server waits for it to end, with no reason given.
  await page.run(`window.kyo.screen({ screen: { state: "working" } })`);
  await page.run(`window.kyo.held({ outcome: "held", id: "m1", held: { id: "m1", retrying: true, reason: null, detail: null } })`);
  const state = `({
    previews: document.querySelectorAll("#chat .turn:not([data-key])").length,
    box: !document.getElementById("held").hidden,
    edit: !document.getElementById("held-edit").hidden,
    sendNow: !document.getElementById("held-send-now").hidden,
  })`;
  assert.deepEqual(await page.run(state), { previews: 1, box: false, edit: true, sendNow: true });
  await new Promise((done) => setTimeout(done, 2700));
  assert.deepEqual(await page.run(state), { previews: 0, box: true, edit: true, sendNow: true });
});

test("a prompt sent while the session works is held, and is not drawn in the chat", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await openSession(page);
  await page.run(`window.kyo.screen({ screen: { state: "working" } })`);
  await page.run(`window.kyo.draft({ boxes: [{ id: "a", text: "Held for later" }], etag: "e1" })`);
  await page.run(`document.querySelector("#boxes .box .box-send").click()`);
  assert.equal(await page.run<number>(`document.querySelectorAll("#chat .turn.sending").length`), 0);
  await page.run(`window.kyo.sent({ id: "held-1" })`);
  await page.run(`window.kyo.held({ outcome: "delivered", id: "held-1", held: null })`);
  // A preview whose prompt the queue says waits on something is taken away, and the held box says why.
  await page.run(`window.kyo.screen({ screen: { state: "idle" } })`);
  await page.run(`window.kyo.draft({ boxes: [{ id: "c", text: "Waits" }], etag: "e3" })`);
  await page.run(`document.querySelector("#boxes .box .box-send").click()`);
  await page.run(`window.kyo.sent({ id: "held-2" })`);
  assert.equal(await page.run<number>(`document.querySelectorAll("#chat .turn.sending").length`), 1);
  await page.run(`window.kyo.held({ outcome: "held", id: "held-2", held: { id: "held-2", retrying: true, reason: "input-box-in-use", detail: "text is in the terminal's input box" } })`);
  assert.equal(await page.run<number>(`document.querySelectorAll("#chat .turn.sending").length`), 0);
  assert.equal(await page.run<boolean>(`!document.getElementById("held").hidden`), true);
  await page.run(`window.kyo.held({ outcome: "delivered", id: "held-2", held: null })`);
  // A refused send takes its preview away.
  await page.run(`window.kyo.draft({ boxes: [{ id: "b", text: "Refused" }], etag: "e2" })`);
  await page.run(`document.querySelector("#boxes .box .box-send").click()`);
  assert.equal(await page.run<number>(`document.querySelectorAll("#chat .turn.sending").length`), 1);
  await page.run(`window.kyo.sent({ error: "No session is live." })`);
  assert.equal(await page.run<number>(`document.querySelectorAll("#chat .turn.sending, #chat .turn:not([data-key])").length`), 0);
});
