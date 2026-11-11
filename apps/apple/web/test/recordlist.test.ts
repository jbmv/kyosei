// Run with `npm test`. What a filter chip of the Records view costs on a store of several
// hundred records (KYO-833), in a headless Chrome fed the frames the app would send; a machine
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

interface Pressed { took: number; landing: string[]; side: string[]; pressed: string; count: string }

/**
 * Presses a chip of the landing page or of the side bar, and reads the two lists once the
 * frame that shows them has been drawn. `took` is from the press to the end of that frame:
 * the handler, the style and layout it leaves to do, and the paint.
 */
const press = (opened: Page, place: "landing" | "side", name: string) => opened.run<Pressed>(`new Promise((done) => {
  const chip = document.querySelector("#records-${place} .chip[data-filter=${name}]");
  const ids = (list) => [...document.querySelectorAll(list + " [data-open]")].map((row) => row.dataset.open);
  const began = performance.now();
  chip.click();
  requestAnimationFrame(() => {
    const channel = new MessageChannel();
    channel.port1.onmessage = () => done({
      took: performance.now() - began,
      landing: ids("#records-groups"),
      side: ids("#records-list"),
      pressed: chip.getAttribute("aria-pressed"),
      count: document.getElementById("records-count").textContent,
    });
    channel.port2.postMessage(0);
  });
})`);

test("a chip turned on or off redraws both lists within 100 ms, with the same records in the same order", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  const steps: [string, string[]][] = [
    ["open", OPEN], ["open", ALL],
    ["dispatchable", READY], ["dispatchable", ALL],
    ["awaiting", AWAITING], ["awaiting", ALL],
  ];
  for (const place of ["landing", "side"] as const) {
    for (const [name, expected] of steps) {
      const found = await press(opened, place, name);
      const what = `${name} pressed ${found.pressed} on the ${place}`;
      assert.deepEqual(found.landing, expected, `${what}: the landing page`);
      assert.deepEqual(found.side, expected, `${what}: the side bar`);
      assert.equal(
        found.count, expected.length === SIZE ? `${SIZE} records` : `${expected.length} of ${SIZE} records`, what);
      // The limit the criterion states. Measured at about 20 ms with every record put back,
      // and about 30 ms before the rows were kept and left undrawn off screen.
      assert.ok(found.took < 100, `${what} took ${found.took} ms`);
    }
  }
});

test("a press puts back the rows already drawn, and a row out of the window is not laid out", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  // Each row is marked, the done ones are filtered out and put back, and the marks are counted.
  await opened.run(`document.querySelectorAll("#records-groups [data-open], #records-list [data-open]")
    .forEach((row) => { row.marked = true; })`);
  await press(opened, "landing", "open");
  await press(opened, "landing", "open");
  const found = await opened.run<{
    landing: number; side: number; marked: number; first: boolean; last: boolean; sideRule: string; height: number;
  }>(`(() => {
    const landing = [...document.querySelectorAll("#records-groups [data-open]")];
    const side = [...document.querySelectorAll("#records-list [data-open]")];
    const drawn = (row) => row.querySelector(".rec-title").checkVisibility({ contentVisibilityAuto: true });
    return {
      landing: landing.length,
      side: side.length,
      marked: [...landing, ...side].filter((row) => row.marked).length,
      first: drawn(landing[0]),
      last: drawn(landing.at(-1)),
      sideRule: getComputedStyle(side[0]).contentVisibility,
      height: document.getElementById("records-groups").getBoundingClientRect().height / landing.length,
    };
  })()`);
  assert.equal(found.landing, SIZE);
  assert.equal(found.side, SIZE);
  assert.equal(found.marked, 2 * SIZE, "every row is the element it was before the two presses");
  assert.equal(found.first, true, "the first row is in the window and drawn");
  assert.equal(found.last, false, "the last row is far below the window and not drawn");
  assert.equal(found.sideRule, "auto");
  // A row not drawn takes the room it takes when drawn, so the page is as long as it was.
  const drawnHeight = await opened.run<number>(
    `document.querySelector("#records-groups [data-open]").getBoundingClientRect().height`);
  assert.ok(Math.abs(found.height - drawnHeight) < 0.05, `a row is ${drawnHeight}px drawn and ${found.height}px on average`);
});

test("a kept row follows the record that is open, and a new list from the server is drawn anew", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open(records(40));
  const current = () => opened.run<{ side: string[]; changed: number }>(`(() => ({
    side: [...document.querySelectorAll("#records-list .rec-row.current")].map((row) => row.dataset.open),
    changed: window.listChanges ?? 0,
  }))()`);
  await opened.run(`(() => {
    window.listChanges = 0;
    new MutationObserver((changes) => { window.listChanges += changes.length; })
      .observe(document.getElementById("records-list"), { childList: true });
    document.querySelector('#records-groups [data-open="KYO-7"]').click();
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
