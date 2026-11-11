// Run with `npm test`. What the Records view's lists cost on a store of several hundred
// records (KYO-833), as laid out by KYO-835: the drawer's results and the home page's groups, in a headless Chrome fed the frames the app would send; a machine
// with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const KINDS = ["bug", "task", "decision", "proposal", "plan"];
/** How many records the store had when the press was reported slow. */
const SIZE = 834;

interface Row { id: string; kind: string; title: string; status: string; seq: number; created_at: string }

/** A list of the store's size and proportions: about one record in nine open, titles of a line or two. */
function records(size = SIZE, word = "record"): Row[] {
  return Array.from({ length: size }, (_, index) => {
    const seq = index + 1;
    return {
      id: `KYO-${seq}`,
      kind: KINDS[index % KINDS.length],
      title: `The ${word} numbered ${seq}: ${"a title of the usual length, which wraps in the side bar ".repeat(1 + (index % 2))}`.trim(),
      status: index % 9 === 0 ? "open" : index % 70 === 1 ? "wont-do" : "done",
      seq,
      created_at: new Date(Date.UTC(2026, 0, 1) + seq * 3600_000).toISOString(),
    };
  });
}

const ROWS = records();
const newestIds = (rows: Row[]) => [...rows].reverse().map((row) => row.id);
const ALL = newestIds(ROWS);
const OPEN = newestIds(ROWS.filter((row) => row.status === "open"));
const READY = OPEN.slice(0, 12);
const AWAITING = ALL.filter((_, index) => index % 100 === 3);

/** The Records landing page with the list read, no record open and no chip pressed. */
async function open(rows: Row[] = ROWS): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.records({ project: "p" });
    [...document.querySelectorAll(".view-item")].find((item) => item.textContent.includes("Records")).click();
    window.kyo.recordLists({
      list: { records: ${JSON.stringify(rows)} },
      next: { ready: ${JSON.stringify(READY.map((id) => ({ id })))} },
      gates: { gates: ${JSON.stringify(AWAITING.map((id) => ({ record_id: id, type: "awaiting:human-review" })))} },
    });
  })()`);
  return opened;
}

/** Types in the drawer's search field, whose results are the drawer's list (KYO-835). */
const search = (opened: Page, text: string) => opened.run(`(() => {
  const field = document.querySelector("#records-side .records-search");
  field.value = ${JSON.stringify(text)};
  field.dispatchEvent(new Event("input", { bubbles: true }));
})()`);

interface Pressed { took: number; side: string[]; pressed: string }

/**
 * Presses the drawer's Open only chip and reads its list once the frame that shows it has
 * been drawn. `took` is from the press to the end of that frame.
 */
const press = (opened: Page) => opened.run<Pressed>(`new Promise((done) => {
  const chip = document.querySelector("#records-side .chip[data-filter=open]");
  const began = performance.now();
  chip.click();
  requestAnimationFrame(() => {
    const channel = new MessageChannel();
    channel.port1.onmessage = () => done({
      took: performance.now() - began,
      side: [...document.querySelectorAll("#records-list [data-open]")].map((row) => row.dataset.open),
      pressed: chip.getAttribute("aria-pressed"),
    });
    channel.port2.postMessage(0);
  });
})`);

test("Open only narrows the drawer's results and puts them back within 100 ms, in the same order", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await search(opened, "record");
  // Each direction is pressed three times and the fastest is held to the limit: the browser
  // tests of the set run at once, and one press can meet the machine busy (170 to 190 ms
  // were seen that way, about 20 ms alone). Every press is checked for its rows.
  const took = { narrowed: [] as number[], restored: [] as number[] };
  for (let round = 0; round < 3; round += 1) {
    for (const [expected, kind] of [[OPEN, "narrowed"], [ALL, "restored"]] as const) {
      const found = await press(opened);
      assert.deepEqual(found.side, expected, `Open only ${found.pressed}`);
      took[kind].push(found.took);
    }
  }
  for (const [kind, times] of Object.entries(took)) {
    assert.ok(Math.min(...times) < 100, `${kind}: ${times.map((ms) => ms.toFixed(1)).join(", ")} ms`);
  }
});

test("a press puts back the rows already drawn, and a row out of the window is not laid out", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await search(opened, "record");
  await opened.run(`document.querySelectorAll("#records-list [data-open]").forEach((row) => { row.marked = true; })`);
  await press(opened);
  await press(opened);
  const found = await opened.run<{ side: number; marked: number; rule: string }>(`(() => {
    const side = [...document.querySelectorAll("#records-list [data-open]")];
    return {
      side: side.length,
      marked: side.filter((row) => row.marked).length,
      rule: getComputedStyle(side[0]).contentVisibility,
    };
  })()`);
  assert.equal(found.side, SIZE);
  assert.equal(found.marked, SIZE, "every row is the element it was before the two presses");
  assert.equal(found.rule, "auto", "a row out of the window is not laid out");
});

test("the home page groups every record by what it waits on, Closed folded and drawn a hundred at a time", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  const groups = () => opened.run<{ name: string; count: string; rows: number; open: boolean }[]>(`
    [...document.querySelectorAll("#records-groups .home-group")].map((group) => ({
      name: group.dataset.group,
      count: group.querySelector(".home-group-count").textContent,
      rows: group.querySelectorAll("[data-open]").length,
      open: group.classList.contains("open"),
    }))`);
  const awaiting = AWAITING.length;
  const ready = READY.filter((id) => !AWAITING.includes(id)).length;
  const otherOpen = OPEN.filter((id) => !AWAITING.includes(id) && !READY.includes(id)).length;
  const closed = SIZE - awaiting - ready - otherOpen;
  assert.deepEqual(await groups(), [
    { name: "Awaiting you", count: String(awaiting), rows: awaiting, open: true },
    { name: "Dispatchable", count: String(ready), rows: ready, open: true },
    { name: "Other open", count: String(otherOpen), rows: otherOpen, open: true },
    { name: "Closed", count: String(closed), rows: 0, open: false },
  ]);
  await opened.run(`document.querySelector('[data-home-group="Closed"]').click()`);
  assert.equal((await groups())[3].rows, 100);
  await opened.run(`document.querySelector('[data-home-more="Closed"]').click()`);
  assert.equal((await groups())[3].rows, 200);
  // The drawer's list holds the records viewed, not the home page's rows, until a search.
  const drawer = await opened.run<{ results: boolean; history: boolean }>(`({
    results: !document.getElementById("records-list").hidden,
    history: !document.getElementById("records-history").hidden,
  })`);
  assert.deepEqual(drawer, { results: false, history: true });
});

test("a kept row follows the record that is open, and a new list from the server is drawn anew", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open(records(40));
  await search(opened, "record");
  const current = () => opened.run<{ side: string[]; changed: number }>(`(() => ({
    side: [...document.querySelectorAll("#records-list .rec-row.current")].map((row) => row.dataset.open),
    changed: window.listChanges ?? 0,
  }))()`);
  await opened.run(`(() => {
    window.listChanges = 0;
    new MutationObserver((changes) => { window.listChanges += changes.length; })
      .observe(document.getElementById("records-list"), { childList: true });
    document.querySelector('#records-list [data-open="KYO-7"]').click();
  })()`);
  // Opening a record changes no row of the list, which is left as it stands.
  assert.deepEqual(await current(), { side: ["KYO-7"], changed: 0 });
  await opened.run(`document.querySelector('#records-list [data-open="KYO-9"]').click()`);
  assert.deepEqual(await current(), { side: ["KYO-9"], changed: 0 });

  const titles = await opened.run<string[]>(`(() => {
    window.kyo.recordLists({ list: { records: ${JSON.stringify(records(41, "renamed record"))} } });
    return [...document.querySelectorAll("#records-list .rec-title")].map((title) => title.textContent);
  })()`);
  assert.equal(titles.length, 41);
  assert.ok(titles.every((title) => title.startsWith("The renamed record numbered ")), titles[0]);
  assert.deepEqual((await current()).side, ["KYO-9"]);
});
