// Run with `npm test`. What the Records view's lists cost on a store of several hundred
// records (KYO-833), as laid out by KYO-862: the drawer's lists and the home's table, in a headless Chrome fed the frames the app would send; a machine
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

interface Row { id: string; kind: string; title: string; status: string; seq: number; created_at: string; plan: string | null }

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
      // Every thirtieth record belongs to the first open plan of the list, and one open record.
      plan: index % 30 === 7 || index === 63 ? "KYO-55" : null,
    };
  });
}

const ROWS = records();
const newestIds = (rows: Row[]) => [...rows].reverse().map((row) => row.id);
const ALL = newestIds(ROWS);
const OPEN = newestIds(ROWS.filter((row) => row.status === "open"));
const READY = OPEN.slice(0, 12);
const AWAITING = ALL.filter((_, index) => index % 100 === 3);
/** An open plan of the list (index 54: a plan, and open), and the records that name it. */
const PLAN = "KYO-55";
const MEMBERS = newestIds(ROWS.filter((row) => row.plan === PLAN));

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

test("the home is a table of the chosen Location, sorted by a pressed column, with counts beside the Locations", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  const awaiting = AWAITING.length;
  const ready = READY.filter((id) => !AWAITING.includes(id)).length;
  const otherOpen = OPEN.filter((id) => !AWAITING.includes(id) && !READY.includes(id)).length;
  const closed = SIZE - awaiting - ready - otherOpen;
  const home = () => opened.run<{ locations: [string, string, boolean][]; rows: string[]; sorted: string }>(`({
    locations: [...document.querySelectorAll("#records-locations [data-location]")].map((row) =>
      [row.dataset.location, row.querySelector(".location-count").textContent, row.classList.contains("current")]),
    rows: [...document.querySelectorAll("#records-table [data-open]")].map((row) => row.dataset.open),
    sorted: [...document.querySelectorAll(".records-head [aria-sort]")]
      .map((button) => button.dataset.recordSort + " " + button.getAttribute("aria-sort")).join(),
  })`);
  // It opens on All, newest first.
  let found = await home();
  assert.deepEqual(found.locations, [
    ["Awaiting you", String(awaiting), false], ["Dispatchable", String(ready), false],
    ["Other open", String(otherOpen), false], ["Closed", String(closed), false], ["Reference", "0", false],
    ["All", String(SIZE), true], ["Shift notes", "0", false], ["Briefs", "0", false],
  ]);
  assert.deepEqual(found.rows, ALL);
  assert.equal(found.sorted, "name descending");
  const heads = await opened.run<string[]>(`[...document.querySelectorAll(".records-head button")].map((b) => b.textContent)`);
  assert.deepEqual(heads, ["Name", "Kind", "Status", "Modified"]);
  // A Location is a filter of the table.
  await opened.run(`document.querySelector('#records-locations [data-location="Awaiting you"]').click()`);
  found = await home();
  assert.deepEqual(found.rows, AWAITING);
  assert.equal(found.locations[0][2], true);
  // A column's head sorts by it, and a second press reverses it.
  await opened.run(`document.querySelector('.records-head [data-record-sort="name"]').click()`);
  found = await home();
  assert.deepEqual(found.rows, [...AWAITING].reverse());
  assert.equal(found.sorted, "name ascending");
  // The field above the table narrows the Location's rows, and knows `kind:`.
  await opened.run(`(() => {
    document.querySelector('#records-locations [data-location="All"]').click();
    const field = document.getElementById("records-home-search");
    field.value = "kind:plan numbered 83";
    field.dispatchEvent(new Event("input", { bubbles: true }));
  })()`);
  found = await home();
  const plans = ROWS.filter((row) => row.kind === "plan" && row.title.includes("numbered 83")).map((row) => row.id);
  assert.ok(plans.length > 0);
  assert.deepEqual(found.rows, plans);
  // The Kind control beside the home's field is the field's `kind:` token, and is the home's own.
  const kinds = await opened.run<{ label: string; drawer: string; field: string; rows: number }>(`(() => {
    const control = document.querySelector("#records-landing .kind-control");
    const field = document.getElementById("records-home-search");
    field.value = "";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    [...control.querySelectorAll(".kind-menu input")].find((box) => box.value === "plan").click();
    return {
      label: control.querySelector(".kind-button").textContent,
      drawer: document.querySelector("#records-side .kind-button").textContent,
      field: field.value.trim(),
      rows: document.querySelectorAll("#records-table [data-open]").length,
    };
  })()`);
  assert.deepEqual(kinds, {
    label: "Kind: plan", drawer: "Kind", field: "kind:plan", rows: ROWS.filter((row) => row.kind === "plan").length,
  });
  // A plan in the side bar filters the table to its members, and a Location puts it back.
  await opened.run(`(() => {
    const field = document.getElementById("records-home-search");
    field.value = "";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    document.querySelector('#records-plans [data-plan="${PLAN}"]').click();
  })()`);
  found = await home();
  // The sort pressed above still stands: oldest first.
  assert.deepEqual(found.rows, [...MEMBERS].reverse());
  assert.ok(found.locations.every(([, , current]) => !current), "no Location is the current one");
  const planRow = await opened.run<{ count: string; name: string; red: string[] }>(`({
    count: document.querySelector('#records-plans .current .location-count').textContent,
    name: document.querySelector('#records-plans .current .tree-name').textContent,
    red: [...document.querySelectorAll("#records-table .rec-status")]
      .filter((cell) => getComputedStyle(cell).color === "rgb(255, 59, 48)").map((cell) => cell.textContent),
  })`);
  assert.equal(planRow.count, String(MEMBERS.length));
  // KYO-883: the plan is named by its id and title, and its outstanding members' status is red.
  assert.match(planRow.name, new RegExp(`^${PLAN} `));
  assert.ok(planRow.red.length > 0 && planRow.red.every((status) => status === "open"), JSON.stringify(planRow.red));
  const redInAll = `[...document.querySelectorAll("#records-table .rec-status")].some((cell) => getComputedStyle(cell).color === "rgb(255, 59, 48)")`;
  await opened.run(`document.querySelector('#records-locations [data-location="All"]').click()`);
  assert.deepEqual((await home()).rows, [...ALL].reverse());
  // Outside a plan the status is not coloured.
  assert.equal(await opened.run<boolean>(redInAll), false);
  // The home's field is its own: the drawer's lists are not narrowed by it.
  const drawer = await opened.run<number>(`document.querySelectorAll("#records-list [data-open]").length`);
  assert.equal(drawer, SIZE);
});

test("one drawer holds the home's lists on the home and Awaiting you, Recents and All beside an open record, docked on both", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  const shown = () => opened.run<{ menu: string; home: boolean; homeLists: boolean; recordLists: boolean; docked: boolean; paneLeft: number }>(`({
    menu: getComputedStyle(document.getElementById("menu")).display,
    home: document.body.classList.contains("home-page"),
    homeLists: getComputedStyle(document.querySelector("#records-side .records-home-side")).display !== "none",
    recordLists: getComputedStyle(document.getElementById("records-list")).display !== "none",
    docked: document.body.classList.contains("dock-left"),
    paneLeft: Math.round(document.getElementById("view-records").getBoundingClientRect().left),
  })`);
  // KYO-883: on the home the drawer is there, holding the home's lists and not a record's,
  // and the home's page has no fixed column of its own.
  let now = await shown();
  assert.deepEqual([now.menu !== "none", now.home, now.homeLists, now.recordLists], [true, true, true, false]);
  assert.equal(await opened.run<number>(`document.querySelectorAll("#records-landing #records-locations").length`), 0);
  // Docked on the home, the drawer pushes the page over, and stays docked beside a record.
  await opened.run(`document.getElementById("menu-icon").click()`);
  now = await shown();
  assert.deepEqual([now.docked, now.paneLeft], [true, 300]);
  const first = AWAITING[0];
  await opened.run(`document.querySelector('#records-table [data-open="${first}"]').click()`);
  now = await shown();
  assert.deepEqual([now.home, now.homeLists, now.recordLists, now.docked], [false, false, true, true]);
  await opened.run(`document.getElementById("menu-icon").click()`);
  assert.equal((await shown()).docked, false);
  const lists = () => opened.run<{ heads: string[]; awaiting: string[]; recents: string[]; all: number }>(`({
    heads: [...document.querySelectorAll("#records-side > h4")].filter((head) => !head.hidden).map((head) => head.textContent),
    awaiting: [...document.querySelectorAll("#records-awaiting [data-open]")].map((row) => row.dataset.open),
    recents: [...document.querySelectorAll("#records-history [data-open]")].map((row) => row.dataset.open),
    all: document.querySelectorAll("#records-list [data-open]").length,
  })`);
  assert.deepEqual(await lists(), { heads: ["Awaiting you", "Recents", "All"], awaiting: AWAITING, recents: [first], all: SIZE });
  // The three lists share the drawer's height and each scrolls by itself.
  const heights = await opened.run<{ heights: number[]; scrolls: string[] }>(`(() => {
    document.getElementById("menu-icon").click();
    const lists = ["records-awaiting", "records-history", "records-list"].map((id) => document.getElementById(id));
    return {
      heights: lists.map((list) => Math.round(list.getBoundingClientRect().height)),
      scrolls: lists.map((list) => getComputedStyle(list).overflowY),
    };
  })()`);
  assert.ok(heights.heights[0] > 60, String(heights.heights));
  assert.ok(Math.max(...heights.heights) - Math.min(...heights.heights) <= 2, String(heights.heights));
  assert.deepEqual(heights.scrolls, ["auto", "auto", "auto"]);
  // The field narrows all three lists, and a list it empties of Awaiting you is not shown.
  const second = AWAITING[1];
  await search(opened, second.replace("KYO-", "numbered ") + ":");
  assert.deepEqual(await lists(), { heads: ["Awaiting you", "Recents", "All"], awaiting: [second], recents: [], all: 1 });
  await search(opened, "no-such-word");
  assert.deepEqual(await lists(), { heads: ["Recents", "All"], awaiting: [], recents: [], all: 0 });
  // Home turns the same drawer back to the home's lists.
  await opened.run(`document.getElementById("records-home").click()`);
  const back = await shown();
  assert.deepEqual([back.home, back.homeLists, back.recordLists], [true, true, false]);
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

test("Shift notes is a Location of the home, the latest note is one press away, and a note opens as a page with its session", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open(records(12));
  const found = await opened.run<{ count: string; rows: string[]; kinds: string[]; latest: string; counted: string; asked: boolean }>(`(() => {
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "The session", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.recordLists({
      list: { records: ${JSON.stringify(records(12))} },
      notes: { notes: [
        { id: "KYO-S2", seq: 2, author: "orchestrator", created_at: "2026-01-03T10:00:00Z", handoff_for: null, text: "The second note." },
        { id: "KYO-S1", seq: 1, author: "orchestrator", created_at: "2026-01-02T10:00:00Z", handoff_for: "KYO-B2", text: "The first note." },
      ] },
      briefs: { briefs: [{ id: "KYO-B1", seq: 1, at: "2026-01-01T09:00:00Z", session_id: "S1", session_start: true, build_commit: null }] },
    });
    document.querySelector('#records-locations [data-location="Shift notes"]').click();
    const before = window.posted.length;
    const out = {
      count: document.querySelector('#records-locations [data-location="Shift notes"] .location-count').textContent,
      rows: [...document.querySelectorAll("#records-table [data-open]")].map((row) => row.dataset.open),
      kinds: [...document.querySelectorAll("#records-table .kind")].map((kind) => kind.textContent),
      latest: document.getElementById("records-home-latest-note") === null ? "gone" : "shown",
      counted: document.getElementById("records-count").textContent,
    };
    document.querySelector('#records-table [data-open="KYO-S2"]').click();
    out.asked = window.posted.slice(before).some((p) => p.name === "records" && p.body.want === "record" && p.body.id === "KYO-S2");
    return out;
  })()`);
  assert.deepEqual(found, {
    count: "2", rows: ["KYO-S2", "KYO-S1"], kinds: ["shift note", "shift note"], latest: "gone",
    counted: "2 shift notes", asked: true,
  });
  const drawn = await opened.run<{ head: string; body: string; session: string; view: boolean }>(`(() => {
    window.kyo.record({ id: "KYO-S2", document: { object: "shift-note", id: "KYO-S2", seq: 2, author: "orchestrator",
      created_at: "2026-01-03T10:00:00Z", text: "The second note." } });
    const link = document.querySelector("#record .session-link");
    const out = {
      head: document.querySelector("#record .record-head").textContent,
      body: document.querySelector("#record .reply").textContent.trim(),
      session: link.textContent,
    };
    link.click();
    out.view = document.body.classList.contains("view-history");
    return out;
  })()`);
  assert.match(drawn.head, /KYO-S2/);
  assert.match(drawn.head, /shift note/);
  assert.equal(drawn.body, "The second note.");
  assert.equal(drawn.session, "The session");
  assert.equal(drawn.view, true, "the link opens the session in History");
});

test("a chosen plan's members still outstanding have their status in red, and settled ones do not (KYO-883)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const member = (seq: number, status: string) => ({
    id: `KYO-${seq}`, kind: "task", title: `Member ${seq}`, status, seq,
    created_at: new Date(Date.UTC(2026, 0, 1) + seq * 3600_000).toISOString(), plan: "KYO-1",
  });
  const rows = [
    { id: "KYO-1", kind: "plan", title: "The plan", status: "open", seq: 1, created_at: "2026-01-01T00:00:00Z", plan: null },
    ...["open", "working", "built", "verified", "done", "wont-do"].map((status, index) => member(index + 2, status)),
  ];
  const opened = await open(rows as Row[]);
  const red = await opened.run<string[]>(`(() => {
    document.querySelector('#records-plans [data-plan="KYO-1"]').click();
    return [...document.querySelectorAll("#records-table .rec-status")]
      .filter((cell) => getComputedStyle(cell).color === "rgb(255, 59, 48)").map((cell) => cell.textContent).sort();
  })()`);
  assert.deepEqual(red, ["built", "open", "verified", "working"]);
});
