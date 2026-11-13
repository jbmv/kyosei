// Run with `npm test`. A box's Delete asks once more when the box holds words (KYO-727), and a
// press anywhere else takes the question back at once (2026-11-13). The page is the built
// renderer in a headless Chrome; a machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

async function open(): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.apply({ reset: true, turns: [], replies: [] });
    window.kyo.composer.select(0, 0);
  })()`);
  await opened.type("some words");
  return opened;
}

const label = (p: Page) => p.run<string>(`document.querySelector("#boxes .box-delete").textContent`);
const boxes = (p: Page) => p.run<number>(`document.querySelectorAll("#boxes .box").length`);

test("a press elsewhere takes Delete? back, and the box stays", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const p = await open();
  await p.run(`document.querySelector("#boxes .box-delete").click()`);
  assert.equal(await label(p), "Delete?");
  await p.run(`document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }))`);
  assert.equal(await label(p), "Delete");
  // The next press asks again rather than deleting.
  await p.run(`document.querySelector("#boxes .box-delete").click()`);
  assert.equal(await label(p), "Delete?");
  assert.equal(await boxes(p), 1);
});

test("a press on Delete? itself still deletes", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const p = await open();
  await p.run(`(() => {
    const remove = document.querySelector("#boxes .box-delete");
    remove.click();
    remove.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    remove.click();
  })()`);
  const text = await p.run<string>(`window.kyo.composer.boxes()[0].text`);
  assert.equal(text, "");
});
