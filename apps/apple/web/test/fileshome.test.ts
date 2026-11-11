// Run with `npm test`. The Files home (KYO-835): a fixed column of Locations, Recents,
// Mentioned and Starred beside a list in Finder's manner, its columns sorting it; a star on a
// row; and the outline of an open markdown file in the right drawer. The stars' server half
// is tests/test_kyo_serve_starred.py. A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const ENTRIES = [
  { name: "docs", path: "docs", type: "dir", mtime_ns: 2e18 },
  { name: "b.md", path: "b.md", type: "file", size: 10, mtime_ns: 3e18 },
  { name: "a.py", path: "a.py", type: "file", size: 300, mtime_ns: 1e18 },
];

async function openFiles(): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    [...document.querySelectorAll(".view-item")].find((item) => item.textContent.includes("Files")).click();
    window.kyo.filesRoots({ status: 200, document: { ok: true, roots: [
      { name: "code", path: "/work/demo", served: true }, { name: "store", path: "/work/store", served: true } ] } });
    window.kyo.filesTree({ root: "code", path: "", session: null, status: 200, document: { ok: true, entries: ${JSON.stringify(ENTRIES)} } });
    window.kyo.filesStarred({ status: 200, document: { ok: true, entries: [{ root: "code", path: "b.md", starred_at: "2026-11-11T20:00:00Z" }] } });
  })()`);
  return opened;
}

test("the home column lists the locations and the starred files, beside a list with four columns", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const opened = await openFiles();
  const home = await opened.run<{ locations: string[]; starred: string[]; head: string[]; first: string[]; star: boolean }>(`(() => ({
    locations: [...document.querySelectorAll("#files-locations .location-row")].map((row) => row.textContent),
    starred: [...document.querySelectorAll("#files-home-starred .mention-name")].map((row) => row.textContent),
    head: [...document.querySelectorAll(".files-head button")].map((button) => button.textContent),
    first: [...document.querySelector(".files-columns .tree-row[data-file]").children].map((cell) => cell.textContent),
    star: document.querySelector('.files-columns [data-star="b.md"]').classList.contains("on"),
  }))()`);
  assert.deepEqual(home.locations, ["Code", "Kyosei"]);
  assert.deepEqual(home.starred, ["b.md"]);
  assert.deepEqual(home.head, ["Name", "Date Modified", "Size", "Kind"]);
  assert.equal(home.first[0].startsWith("a.py"), true);
  assert.equal(home.first[3], "Python");
  assert.equal(home.star, true);
});

test("a column's header sorts the list, a second press reverses it, and folders stay first", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const names = await page!.run<string[][]>(`(() => {
    const order = () => [...document.querySelectorAll(".files-columns .tree-row .tree-name")].map((name) => name.textContent);
    const by = (column) => document.querySelector('.files-head [data-sort="' + column + '"]').click();
    const out = [order()];
    by("size"); out.push(order());
    by("size"); out.push(order());
    by("name");
    return out;
  })()`);
  assert.deepEqual(names, [["docs", "a.py", "b.md"], ["docs", "b.md", "a.py"], ["docs", "a.py", "b.md"]]);
});

test("a press on a row's star stars or unstars the file on the server", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const posted = await page!.run<unknown[]>(`(() => {
    window.posted.length = 0;
    document.querySelector('.files-columns [data-star="a.py"]').click();
    document.querySelector('.files-columns [data-star="b.md"]').click();
    return window.posted.filter((p) => p.name === "files").map((p) => p.body);
  })()`);
  assert.deepEqual(posted, [
    { want: "star", root: "code", path: "a.py" }, { want: "unstar", root: "code", path: "b.md" },
  ]);
});

test("an open markdown file is outlined by its headings in the right drawer, each folding what is under it", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const outline = await page!.run<{ rows: string[]; shown: boolean; folded: string[] }>(`(() => {
    document.querySelector('.files-columns .tree-row[data-file="b.md"]').click();
    window.kyo.filesBlob({ root: "code", path: "b.md", session: null, status: 200, etag: '"e"', media_type: "text/markdown",
      size: 60, text: "# Title\\n\\n## One\\n\\ntext\\n\\n### Deep\\n\\n## Two\\n", data: null, error: null });
    const rows = () => [...document.querySelectorAll("#file-outline .heading-row:not([hidden]) .outline-row")].map((row) => row.textContent);
    const all = rows();
    document.querySelector("#file-outline .heading-row .chevron").click();
    return { rows: all, shown: document.body.classList.contains("has-outline"), folded: rows() };
  })()`);
  assert.deepEqual(outline.rows, ["Title", "One", "Deep", "Two"]);
  assert.equal(outline.shown, true);
  assert.deepEqual(outline.folded, ["Title"], "folding the title hides everything under it");
});
