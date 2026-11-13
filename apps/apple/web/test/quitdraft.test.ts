// Run with `npm test`. The page's half of a quit (KYO-782): the composer saves what is typed
// after a pause, so the app asks for a save still waiting before it lets the process end, and
// gives back at the next launch what the server never acknowledged. The page is the built
// renderer in a headless Chrome; a machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

/** A project, a live session and its saved draft, with the one box focused. */
async function open(): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.draft({ boxes: [{ text: "Saved before" }], etag: "e1" });
    window.posted.length = 0;
    window.kyo.composer.select(0, window.kyo.composer.boxes()[0].text.length);
  })()`);
  return opened;
}

const saves = (opened: Page) => opened.run<string[][]>(
  `window.posted.filter((message) => message.name === "saveDraft").map((message) => message.body.boxes.map((box) => box.text))`);
const wait = (opened: Page, ms: number) => opened.run(`new Promise((done) => setTimeout(done, ${ms}))`);

test("a quit is given the save that was waiting for a pause, and the page does not send it again", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await opened.type(", and ten more");
  // Nothing has been sent: the pause has not passed.
  assert.deepEqual(await saves(opened), []);
  const pending = await opened.run<string | null>(`JSON.stringify(window.kyo.pendingDraft())`);
  assert.deepEqual(JSON.parse(pending!).boxes.map((box: { text: string }) => box.text), ["Saved before, and ten more"]);
  // The app makes the save it was handed, so the page's own is dropped, and nothing more is pending.
  assert.equal(await opened.run(`JSON.stringify(window.kyo.pendingDraft())`), "null");
  await wait(opened, 900);
  assert.deepEqual(await saves(opened), []);
});

test("with everything typed already sent, a quit is given nothing", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  assert.equal(await opened.run(`JSON.stringify(window.kyo.pendingDraft())`), "null");
  await opened.type(" and more");
  await wait(opened, 900);
  assert.deepEqual(await saves(opened), [["Saved before and more"]]);
  assert.equal(await opened.run(`JSON.stringify(window.kyo.pendingDraft())`), "null");
});

test("prompts kept at a quit replace the server's draft at the next launch and are saved to it", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await opened.run(`window.kyo.restoreDraft({ boxes: [{ text: "Saved before, and ten more" }, { text: "A second prompt" }] })`);
  const shown = await opened.run<string[]>(`window.kyo.composer.boxes().map((box) => box.text)`);
  assert.deepEqual(shown, ["Saved before, and ten more", "A second prompt"]);
  await wait(opened, 900);
  assert.deepEqual(await saves(opened), [["Saved before, and ten more", "A second prompt"]]);
});
