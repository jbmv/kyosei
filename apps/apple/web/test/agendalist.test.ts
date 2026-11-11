// Run with `npm test`. The session list's bubble as a web view paints it (KYO-781): the
// person's open items, their add field (KYO-781.c3), a divider, the orchestrator's, and the checked items behind a control
// that states their count. A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const ITEMS = [
  { id: "o1", text: "accept 1", author: "orchestrator", checked: false },
  { id: "p1", text: "mine 1", author: "person", checked: false },
  { id: "o2", text: "accept 2", author: "orchestrator", checked: true },
  { id: "p2", text: "mine 2", author: "person", checked: true },
  { id: "p3", text: "mine 3", author: "person", checked: false },
];

async function open(skip: (reason: string) => void): Promise<Page | null> {
  if (!page) { skip("no Chrome"); return null; }
  await page.reload();
  await page.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.agenda({ state: "shown", session_id: "S1", items: ${JSON.stringify(ITEMS)}, etag: '"e"' });
  })()`);
  return page;
}

const painted = (p: Page) => p.run<string[]>(`[...document.querySelectorAll("#agenda-items > li")].map((li) =>
  li.classList.contains("agenda-divider") ? "---" : li.classList.contains("agenda-add-row") ? "+" : li.dataset.id)`);
const control = (p: Page) => p.run<{ hidden: boolean; text: string }>(`(() => {
  const c = document.getElementById("agenda-completed"); return { hidden: c.hidden, text: c.textContent }; })()`);

test("the person's open items, a divider, then the orchestrator's; checked ones are hidden", async (t) => {
  const p = await open((r) => t.skip(r));
  if (!p) return;
  assert.deepEqual(await painted(p), ["p1", "p3", "+", "---", "o1"]);
  assert.deepEqual(await control(p), { hidden: false, text: "Show completed (2)" });
});

test("showing completed keeps the two groups, and hiding returns", async (t) => {
  const p = await open((r) => t.skip(r));
  if (!p) return;
  await p.run(`document.getElementById("agenda-completed").click()`);
  assert.deepEqual(await painted(p), ["p1", "p3", "p2", "+", "---", "o1", "o2"]);
  assert.deepEqual(await control(p), { hidden: false, text: "Hide completed (2)" });
  await p.run(`document.getElementById("agenda-completed").click()`);
  assert.deepEqual(await painted(p), ["p1", "p3", "+", "---", "o1"]);
});

test("no divider with one group, and no control with nothing checked", async (t) => {
  const p = await open((r) => t.skip(r));
  if (!p) return;
  await p.run(`window.kyo.agenda({ state: "shown", session_id: "S1", etag: '"f"',
    items: ${JSON.stringify(ITEMS.filter((i) => i.author === "person" && !i.checked))} })`);
  assert.deepEqual(await painted(p), ["p1", "p3", "+"]);
  assert.equal((await control(p)).hidden, true);
});
