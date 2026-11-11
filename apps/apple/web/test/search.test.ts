// Run with `npm test`. Search matches the drawn words as one phrase, in any case (KYO-741,
// KYO-754). The tests of the page at the end run it in a headless Chrome, fed the frames the
// app would send; a machine with no Chrome skips those.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { findSpans, fold, lastHolding, queryPhrase, scrollBy, step, tally } from "../src/search.ts";
import { openPage, type Page } from "./chrome.ts";

const spansIn = (text: string, query: string) => findSpans(text, queryPhrase(query));

test("a word matches every occurrence, whatever its case", () => {
  assert.deepEqual(spansIn("Two copies, two COPIES.", "copies"), [[4, 10], [16, 22]]);
});

test("the query is one phrase: \"with a record open\" matches only that phrase", () => {
  const text = "A record is open with a window. With a record open, a search finds it; open a record with care.";
  const spans = spansIn(text, "with a record open");
  assert.deepEqual(spans.map(([from, to]) => text.slice(from, to)), ["With a record open"]);
  // Each of its words is in the text more than once, and none of them is matched alone.
  for (const word of ["with", "a", "record", "open"]) assert.ok(spansIn(text, word).length > 1, word);
  assert.deepEqual(spansIn("open record a with", "with a record open"), []);
});

test("words are matched together and in order, not each wherever it is", () => {
  assert.deepEqual(spansIn("if two copies were open", "copies open"), []);
  assert.deepEqual(spansIn("if two copies were open", "copies were"), [[7, 18]]);
});

test("a quotation mark is a character to find, not an operator", () => {
  assert.deepEqual(spansIn("two copies were open", '"two copies"'), []);
  assert.deepEqual(spansIn('she said "two copies" twice', '"two copies"'), [[9, 21]]);
  assert.equal(queryPhrase('"Two'), '"two');
});

test("a space in the query matches a line's end or several spaces, and a space typed last is asked for", () => {
  assert.deepEqual(spansIn("two\ncopies and two  copies", "two copies"), [[0, 10], [15, 26]]);
  assert.deepEqual(spansIn("with a, with", "with "), [[0, 5]]);
  assert.deepEqual(spansIn("a.b a+b", "a.b"), [[0, 3]]);
  assert.deepEqual(spansIn("a (b) c", "(b) c"), [[2, 7]]);
});

test("hits do not overlap, and an empty query matches nothing", () => {
  assert.deepEqual(spansIn("aaaa", "aa"), [[0, 2], [2, 4]]);
  assert.deepEqual(spansIn("abcd", "  "), []);
  assert.deepEqual(spansIn("abcd", ""), []);
});

test("the last position that holds is found by halving", () => {
  const starts = [0, 4, 9, 30];
  const lastAtOrBefore = (at: number) => lastHolding(starts.length, (index) => starts[index] <= at);
  assert.deepEqual([-1, 0, 3, 4, 29, 30, 99].map(lastAtOrBefore), [-1, 0, 0, 1, 2, 3, 3]);
  assert.equal(lastHolding(0, () => true), -1);
});

test("folding keeps every position, even where a lower case is longer", () => {
  assert.equal(fold("İA").length, 2);
  assert.deepEqual(spansIn("İ A", "a"), [[2, 3]]);
});

test("stepping wraps, and starts at an end", () => {
  assert.equal(step(-1, 3, true), 0);
  assert.equal(step(-1, 3, false), 2);
  assert.equal(step(2, 3, true), 0);
  assert.equal(step(0, 3, false), 2);
  assert.equal(step(0, 0, true), -1);
});

test("the tally names the current hit and the count", () => {
  assert.equal(tally(1, 4, true), "2 of 4");
  assert.equal(tally(-1, 0, true), "No results");
  assert.equal(tally(-1, 0, false), "");
});

test("a hit within the band of the window needs no scroll, and one outside is brought to its middle", () => {
  assert.equal(scrollBy(100, 120, 50, 500), 0);
  assert.equal(scrollBy(600, 620, 50, 500), 600 - 275 + 10);
  assert.equal(scrollBy(-200, -180, 50, 500), -200 - 275 + 10);
});

// ---- the page (KYO-754): what is counted, what is marked, and what a key typed costs

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const PHRASE = "with a record open";
// A brief long enough that marking every letter of it is thousands of ranges.
const BRIEF = `# Brief\n\n${"A paragraph of the brief, with words and a record that is not open here.\n\n".repeat(400)}`
  + `## Task\n\nThe one place in the fold: search with a record open and step to it.\n\n## Report\n\nEnd.`;
const RECORD = {
  id: "KYO-9", kind: "task", title: "A record to search", status: "open",
  description: "Open a record. A search with words, and a record, finds each open place with care.",
  criteria: [{ id: "KYO-9.c1", text: "Command-F, with a record open, searches its page", state: "unmet-unbuilt" }],
  dispatches: [{ id: "KYO-9.d1", brief: BRIEF, state: "running", created_at: "2026-01-01T00:00:00Z" }],
};

/** The same record with a page several windows long, so that most of its drawn hits are off screen. */
const LONG_RECORD = { ...RECORD, description: "A paragraph of a long page, drawn and not folded.\n\n".repeat(300) };

/** The Records view on a record whose dispatch holds a folded brief, with the search bar open. */
async function openRecord(record: object = RECORD): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.records({ project: "p" });
    window.kyo.recordLists({ list: { records: [
      { id: "KYO-9", kind: "task", title: "A record to search", status: "open" },
      { id: "KYO-10", kind: "task", title: "Another, with a title", status: "open" },
    ] } });
    const row = document.createElement("div");
    row.dataset.open = "KYO-9";
    document.body.append(row);
    row.click();
    row.remove();
    window.kyo.record({ id: "KYO-9", document: ${JSON.stringify(record)} });
    window.kyo.find();
  })()`);
  return opened;
}

interface Found {
  count: string; current: string[]; marked: number; markedInShutFold: number; openFolds: number; listRows: number;
  filter: string; took: number;
}

/** Sets the search field as typing does, waits for the search, and reads what the page then shows. */
const search = (opened: Page, query: string) => opened.run<Found>(`new Promise((done) => {
  const field = document.getElementById("find-field");
  const timeout = window.setTimeout;
  let took = -1;
  // The search runs from the field's own timer, which is the one timed.
  window.setTimeout = (run, wait, ...rest) => timeout(() => {
    const began = performance.now();
    run(...rest);
    if (wait === 120) took = performance.now() - began;
  }, wait);
  field.value = ${JSON.stringify(query)};
  field.dispatchEvent(new Event("input", { bubbles: true }));
  window.setTimeout = timeout;
  const read = () => {
    if (took < 0) return timeout(read, 10);
    const shut = (range) => {
      for (let element = range.startContainer.parentElement; element; element = element.parentElement) {
        if (element.tagName === "DETAILS" && !element.open && !range.startContainer.parentElement.closest("summary")) return true;
      }
      return false;
    };
    const marked = [...(CSS.highlights.get("found") ?? [])];
    done({
      count: document.getElementById("find-count").textContent,
      current: [...(CSS.highlights.get("found-current") ?? [])].map((range) => range.toString()),
      marked: marked.length,
      markedInShutFold: marked.filter(shut).length,
      openFolds: document.querySelectorAll("#record details[open]").length,
      listRows: document.querySelectorAll("#records-groups [data-open]").length,
      filter: document.querySelector("#records-side .records-search").value,
      took,
    });
  };
  timeout(read, 200);
})`);

test("in a record, \"with a record open\" is found as that phrase, in the page and in a fold not yet opened", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await openRecord();
  const found = await search(opened, PHRASE);
  // Once in the criterion and once in the brief, whose fold has not been opened; the Task fold repeats the brief's.
  assert.equal(found.count, "1 of 3");
  assert.deepEqual(found.current, [PHRASE]);
  assert.equal(found.openFolds, 0);
  assert.equal(found.markedInShutFold, 0);
});

test("a hit inside a shut fold opens the fold when it is stepped to", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await openRecord();
  await search(opened, PHRASE);
  await opened.run(`document.getElementById("find-field").focus()`);
  await opened.press("Enter");
  const stepped = await opened.run<{ count: string; open: number; current: string; shown: boolean }>(`(() => {
    const range = [...CSS.highlights.get("found-current")][0];
    const box = range.getBoundingClientRect();
    return {
      count: document.getElementById("find-count").textContent,
      open: document.querySelectorAll("#record details[open]").length,
      current: range.toString(),
      shown: box.height > 0 && box.top >= 0 && box.bottom <= window.innerHeight,
    };
  })()`);
  assert.deepEqual(stepped, { count: "2 of 3", open: 1, current: PHRASE, shown: true });
});

test("a query of one letter counts every hit, marks only those on screen and none in a shut fold, and is quick", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await openRecord(LONG_RECORD);
  const found = await search(opened, "a");
  const count = Number(found.count.replace(/^1 of /, ""));
  // The drawn page holds over two thousand and the brief thousands more; a window holds far fewer.
  assert.ok(count > 4000, found.count);
  assert.ok(found.marked > 0 && found.marked < 600, String(found.marked));
  assert.equal(found.markedInShutFold, 0);
  // The page is moved under the window: the hits then on screen are the ones marked.
  const moved = await opened.run<{ marked: number; onScreen: number }>(`new Promise((done) => {
    const pane = document.getElementById("view-records");
    pane.scrollTop = pane.scrollHeight / 2;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const marked = [...CSS.highlights.get("found")];
      const onScreen = marked.filter((range) => {
        const box = range.getBoundingClientRect();
        return box.bottom > 0 && box.top < window.innerHeight;
      });
      done({ marked: marked.length, onScreen: onScreen.length });
    }));
  })`);
  assert.ok(moved.marked > 0 && moved.marked < 600, String(moved.marked));
  assert.equal(moved.onScreen, moved.marked);
  // The limit the criterion states. Measured at a few milliseconds; the old search took far longer in the app.
  assert.ok(found.took < 100, `the search took ${found.took} ms`);
});

test("the search bar's field does not narrow the list of records", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await openRecord();
  const found = await search(opened, "zebra");
  assert.equal(found.count, "No results");
  assert.equal(found.filter, "");
  assert.equal(found.listRows, 2);
});

test("in the chat, the phrase is found where it is written and its words are not found apart", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = page;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.apply({ reset: true,
      turns: [{ id: "u1", at: "2026-01-01T00:00:00Z", text: "Open a record, with care." }],
      replies: [{ message_id: "m1", uuid: "x1", at: "2026-01-01T00:00:01Z",
        text: ${JSON.stringify("A search **with a record open** reads its page.\n\nWith a file open it reads the file; a record stays.")} }] });
    window.kyo.find();
  })()`);
  const found = await search(opened, PHRASE);
  assert.equal(found.count, "1 of 1");
  // The words are drawn across a bold mark's edge, and are one hit.
  assert.deepEqual(found.current, [PHRASE]);
});

test("in a file, the phrase is found in the lines as they are drawn", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = page;
  await opened.reload();
  const text = "// Search with a record open.\nconst open = record;\nwith(a);\n";
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.files({ project: "p", same_machine: true });
    [...document.querySelectorAll(".view-item")].find((item) => item.textContent.includes("Files")).click();
    window.kyo.filesRoots({ document: { ok: true, roots: [{ name: "code", served: true }] } });
    window.kyo.filesTree({ root: "code", path: "", status: 200, document: { entries: [
      { name: "a.ts", path: "a.ts", type: "file", media_type: "text/plain", text: true, size: 60 },
    ] } });
    document.querySelector('.files-tree .tree-row[data-file="a.ts"]').click();
    window.kyo.filesBlob({ root: "code", path: "a.ts", status: 200, etag: "e1", media_type: "text/plain", size: 60,
      text: ${JSON.stringify(text)} });
    window.kyo.find();
  })()`);
  const found = await search(opened, PHRASE);
  assert.equal(found.count, "1 of 1");
  assert.deepEqual(found.current, [PHRASE]);
});
