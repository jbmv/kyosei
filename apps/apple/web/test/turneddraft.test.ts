// Run with `npm test`. KYO-990: when the composer turns from one draft of the project to
// another, as it does when a session starts and its draft takes the place of the project's,
// the app asks the page for its pending save and reads the new draft a moment later. What is
// typed in between is in neither draft, and is kept: the draft frame that arrives with
// `turned` merges it, as an edit made here, and the page saves it to the draft turned to. A
// turn with nothing typed since still shows the new draft as the server holds it. A machine
// with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

interface Box { id: string; text: string; marks?: unknown }

let page: Page | null = null;

before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const TAGGED = "A tagged line written between sessions";
const PAUSE = 900;

/** The window shows project p with no live session, and the project's draft holds `boxes`. */
async function openProject(boxes: Box[]): Promise<Page> {
  const shown = page!;
  await shown.reload();
  await shown.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.draft({ boxes: ${JSON.stringify(boxes)}, etag: "p1" });
    window.posted.length = 0;
  })()`);
  return shown;
}

/** The app's half of a turn to session S1: the page is asked for its pending save first. */
async function turn(shown: Page): Promise<void> {
  await shown.run(`(() => {
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.flushDraft();
  })()`);
}

const texts = (shown: Page) => shown.run<string[]>(`window.kyo.composer.boxes().map((box) => box.text)`);
const saves = (shown: Page) => shown.run<{ boxes: Box[]; seen: string | null }[]>(
  `window.posted.filter((message) => message.name === "saveDraft").map((message) => message.body)`);
const wait = (shown: Page, ms: number) => shown.run(`new Promise((done) => setTimeout(done, ${ms}))`);

test("a line typed after the composer turned to a new session's draft, before it arrived, is kept and saved there", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const shown = await openProject([{ id: "device-two-box", text: TAGGED }]);
  await turn(shown);
  // Nothing was waiting for a pause, so the flush sent nothing.
  assert.deepEqual(await saves(shown), []);
  await shown.run(`window.kyo.composer.select(0, ${TAGGED.length})`);
  await shown.type(", and a second line typed in the session");
  // The session's draft arrives: the server moved the project's box into it, under its id.
  await shown.run(`window.kyo.draft({ boxes: [{ id: "device-two-box", text: ${JSON.stringify(TAGGED)} }], etag: "s1", turned: true })`);
  const more = `${TAGGED}, and a second line typed in the session`;
  assert.deepEqual(await texts(shown), [more]);
  await wait(shown, PAUSE);
  const asked = await saves(shown);
  assert.ok(asked.length >= 1, "the page asks for a save");
  const last = asked.at(-1)!;
  // The save is made over the session's draft, which the page was handed.
  assert.equal(last.seen, "s1");
  assert.deepEqual(last.boxes.map((box) => [box.id, box.text]), [["device-two-box", more]]);
});

test("a turn with nothing typed since shows the draft turned to as the server holds it", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const shown = await openProject([{ id: "a", text: "The project's prompt" }]);
  await turn(shown);
  await shown.run(`window.kyo.draft({ boxes: [{ id: "b", text: "The session's prompt" }], etag: "s1", turned: true })`);
  assert.deepEqual(await texts(shown), ["The session's prompt"]);
  await wait(shown, PAUSE);
  // Nothing was typed, so nothing of the draft left is copied into this one.
  assert.deepEqual(await saves(shown), []);
});
