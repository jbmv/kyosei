// Run with `npm test`. KYO-798: a draft saved on one device appears in the composer of
// another without its being reopened. Two pages of the built renderer, each in a headless
// Chrome, stand for two devices. Between them is a stand-in for the app and the server: it
// keeps one draft under an etag, refuses a save made over a stale etag, and hands each page
// the frames the Mac app sends (`draftSaved` after an acknowledged save, `draftElsewhere`
// once the stream announced a save from the other device or this one's save was refused).
// The server's half is tested against a real server in tests/test_kyo_serve_drafts.py. A
// machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

interface Box { id: string; text: string; marks?: unknown; scratch?: { heading: string; collapsed?: boolean } }
interface Device {
  page: Page;
  /** The etag the app saves under, and the etag of the draft it last handed the page. */
  etag: string | null;
  handed: string | null;
  read: number;
}

let laptop: Device | null = null;
let studio: Device | null = null;
const server = { boxes: [] as Box[], etag: "e0", saves: 0 };

before(async () => {
  const pages = await Promise.all([openPage(), openPage()]);
  if (pages[0] && pages[1]) {
    laptop = { page: pages[0], etag: null, handed: null, read: 0 };
    studio = { page: pages[1], etag: null, handed: null, read: 0 };
  } else {
    await Promise.all(pages.map((page) => page?.close()));
  }
});
after(async () => { await Promise.all([laptop?.page.close(), studio?.page.close()]); });

/** An object's keys in another order, as the app's own JSON writer leaves them. */
function reordered(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(reordered);
  if (value === null || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).reverse().map(([key, held]) => [key, reordered(held)]));
}

const frame = () => JSON.stringify({ boxes: reordered(server.boxes), etag: server.etag });

/** Both devices open the same live session, whose draft holds `boxes`. */
async function open(boxes: Box[]): Promise<[Device, Device]> {
  server.boxes = boxes;
  server.etag = `e${++server.saves}`;
  for (const device of [laptop!, studio!]) {
    await device.page.reload();
    await device.page.run(`(() => {
      window.kyo.project({ project: "p", scope: "server/p" });
      window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
        { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
      ] } });
      window.kyo.draft(${frame()});
      window.posted.length = 0;
    })()`);
    device.etag = server.etag;
    device.handed = server.etag;
    device.read = 0;
  }
  return [laptop!, studio!];
}

/** The app's half of a save: each save the page asked for is made under the device's etag. */
async function save(device: Device): Promise<string[]> {
  const asked = await device.page.run<{ boxes: Box[]; seq: number; seen: string | null }[]>(
    `window.posted.filter((message) => message.name === "saveDraft").map((message) => message.body)`);
  const outcomes: string[] = [];
  for (const body of asked.slice(device.read)) {
    if (body.seen !== device.handed) {
      // Asked for before the page had merged the draft it was handed: dropped.
      outcomes.push("dropped");
    } else if (device.etag === server.etag) {
      server.boxes = body.boxes;
      server.etag = `e${++server.saves}`;
      device.etag = server.etag;
      await device.page.run(`window.kyo.draftSaved({ seq: ${body.seq} })`);
      outcomes.push("saved");
    } else {
      // Refused as stale: the app reads the draft and hands it to the page.
      device.etag = device.handed = server.etag;
      await device.page.run(`window.kyo.draftElsewhere(${frame()})`);
      outcomes.push("refused");
    }
  }
  device.read = asked.length;
  return outcomes;
}

/** The stream's announcement reaching a device: it reads the draft unless it holds that one. */
async function announce(device: Device): Promise<void> {
  if (device.etag === server.etag) return;
  device.etag = device.handed = server.etag;
  await device.page.run(`window.kyo.draftElsewhere(${frame()})`);
}

const shown = (device: Device) => device.page.run<string[]>(
  `window.kyo.composer.boxes().map((box) => box.text)`);
const note = (device: Device) => device.page.run<string>(`document.getElementById("composer-note").textContent`);
const wait = (device: Device, ms: number) => device.page.run(`new Promise((done) => setTimeout(done, ${ms}))`);
/** Puts the caret at the end of the nth box. */
const focus = (device: Device, nth: number) => device.page.run(
  `window.kyo.composer.select(${nth}, window.kyo.composer.boxes()[${nth}].text.length)`);
const PAUSE = 900;

test("what is typed on one device appears on the other, which was not reopened", async (t) => {
  if (!laptop) return t.skip("no Chrome on this machine");
  const [here, there] = await open([{ id: "a", text: "First" }, { id: "b", text: "Second" }]);
  await focus(there, 1);
  await there.page.run(`void (window.marked = document.activeElement)`);
  await focus(here, 0);
  await here.page.type(", typed on the laptop");
  await wait(here, PAUSE);
  assert.deepEqual(await save(here), ["saved"]);
  await announce(there);
  assert.deepEqual(await shown(there), ["First, typed on the laptop", "Second"]);
  // Nothing of the other device's was unsaved, so it is told nothing and saves nothing.
  assert.equal(await note(there), "");
  await wait(there, PAUSE);
  assert.deepEqual(await save(there), []);
  // The box it had the caret in was not rebuilt.
  assert.equal(await there.page.run(`document.activeElement === window.marked`), true);
  // The laptop's own save is not read back to it.
  await announce(here);
  assert.deepEqual(await shown(here), ["First, typed on the laptop", "Second"]);
});

test("a device holding unsaved edits keeps them, is told, and both versions reach both devices", async (t) => {
  if (!laptop) return t.skip("no Chrome on this machine");
  const [here, there] = await open([{ id: "a", text: "First" }, { id: "b", text: "Second" }]);
  await focus(here, 0);
  await here.page.type(" from the laptop");
  await focus(there, 0);
  await there.page.type(" from the Studio");
  await there.page.run(`void (window.marked = document.activeElement)`);
  await wait(here, PAUSE);
  assert.deepEqual(await save(here), ["saved"]);
  // The Studio has asked for its own save, which the app has not made when the laptop's is
  // announced and read.
  await announce(there);
  assert.deepEqual(await shown(there), ["First from the Studio", "First from the laptop", "Second"]);
  assert.match(await note(there), /edited here and on another device\. Both versions are kept/);
  // The caret stays in the box being typed in, which was not rebuilt.
  assert.equal(await there.page.run(`document.activeElement === window.marked`), true);
  await there.page.type("!");
  await wait(there, PAUSE);
  // The save asked for before the merge would put the laptop's words out of the draft, and
  // is dropped. The one the merge asked for holds both.
  assert.deepEqual(await save(there), ["dropped", "saved"]);
  await announce(here);
  assert.deepEqual(await shown(here), ["First from the Studio!", "First from the laptop", "Second"]);
  assert.deepEqual(server.boxes.map((box) => box.text), ["First from the Studio!", "First from the laptop", "Second"]);
});

test("a save refused as stale is merged with the draft saved first, and saved again", async (t) => {
  if (!laptop) return t.skip("no Chrome on this machine");
  const [here, there] = await open([{ id: "a", text: "First" }, { id: "b", text: "Second" }]);
  await focus(here, 0);
  await here.page.type(" from the laptop");
  await focus(there, 1);
  await there.page.type(" from the Studio");
  await wait(here, PAUSE);
  assert.deepEqual(await save(here), ["saved"]);
  // No announcement reached the Studio before its own save went out.
  assert.deepEqual(await save(there), ["refused"]);
  assert.deepEqual(await shown(there), ["First from the laptop", "Second from the Studio"]);
  assert.match(await note(there), /also changed on another device\. What was typed here is kept/);
  await wait(there, PAUSE);
  assert.deepEqual(await save(there), ["saved"]);
  await announce(here);
  assert.deepEqual(await shown(here), ["First from the laptop", "Second from the Studio"]);
});

test("a prompt sent from one device leaves the other, which keeps its unsaved edits", async (t) => {
  if (!laptop) return t.skip("no Chrome on this machine");
  const kept = { id: "a", text: "Notes", scratch: { heading: "" } };
  const [here, there] = await open([kept, { id: "b", text: "Sent from the laptop" }]);
  await focus(there, 0);
  await there.page.type(", still being typed");
  await here.page.run(`document.querySelector("#boxes .box.main .box-send").click()`);
  const sends = await here.page.run<number>(`window.posted.filter((message) => message.name === "send").length`);
  assert.equal(sends, 1);
  await here.page.run(`window.kyo.sent({ id: "held-1" })`);
  // The sender saves the draft without the sent prompt at once, with no pause.
  assert.deepEqual(await save(here), ["saved"]);
  assert.deepEqual(server.boxes.map((box) => box.id), ["a"]);
  await announce(there);
  // The scratchpad keeps what is being typed, and the prompt that was sent is empty again.
  assert.deepEqual(await shown(there), ["Notes, still being typed", ""]);
  await wait(there, PAUSE);
  assert.deepEqual(await save(there), ["saved"]);
  assert.deepEqual(server.boxes, [{ ...kept, text: "Notes, still being typed", marks: [] }]);
});

test("every box is saved with an id, which it keeps while it is edited", async (t) => {
  if (!laptop) return t.skip("no Chrome on this machine");
  const [here] = await open([{ id: "a", text: "First" }]);
  // The + button adds a scratchpad above the prompt, with the caret in its heading.
  await here.page.run(`document.getElementById("new-prompt").click()`);
  await here.page.type("A heading");
  await here.page.run(`window.kyo.composer.select(0, 0)`);
  await here.page.type("Notes");
  await wait(here, PAUSE);
  await save(here);
  const [second, first] = server.boxes;
  assert.equal(first.id, "a");
  assert.ok(second.id && second.id !== "a");
  assert.deepEqual([second.text, second.scratch], ["Notes", { heading: "A heading" }]);
  await here.page.type(", edited");
  await wait(here, PAUSE);
  await save(here);
  assert.deepEqual(server.boxes.map((box) => box.id), [second.id, "a"]);
});
