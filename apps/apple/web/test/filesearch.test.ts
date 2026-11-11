// Run with `npm test`. The Files view's search over the code and the store roots, and its list
// of recently opened files (KYO-814): the page asks one root at a time, draws each root's
// answer under its name, opens a line it is pressed on, records each file opened, and lists
// the server's recent files. The server's half is tested in tests/test_kyo_serve_tree.py and
// tests/test_kyo_serve_recent.py. A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const wait = (ms: number) => new Promise((done) => setTimeout(done, ms));

const roots = { ok: true, max_file_bytes: 33554432, roots: [
  { name: "code", path: "/work/demo", served: true },
  { name: "store", path: "/work/store", served: true },
] };

async function openFiles(target: Page): Promise<void> {
  await target.reload();
  await target.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    Array.from(document.querySelectorAll(".view-item")).find((item) => item.textContent === "Files").click();
    window.kyo.filesRoots({ status: 200, document: ${JSON.stringify(roots)} });
    window.posted.length = 0;
  })()`);
}

test("a search asks each root, and draws each root's files and lines under its name", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await openFiles(page);
  await page.run(`(() => {
    const field = document.getElementById("files-search");
    field.value = "guide";
    field.dispatchEvent(new Event("input"));
  })()`);
  await wait(400);
  const asked = await page.run<{ q: string; root: string }[]>(
    `window.posted.filter((p) => p.body.want === "search").map((p) => ({ q: p.body.q, root: p.body.root }))`);
  assert.deepEqual(asked, [{ q: "guide", root: "code" }, { q: "guide", root: "store" }]);
  const drawn = await page.run<{ searching: boolean; heads: string[]; rows: string[] }>(`(() => {
    window.kyo.filesSearched({ q: "guide", root: "code", status: 200, document: { ok: true, more: false, results: [
      { root: "code", path: "docs/guide.md", match: "name" },
      { root: "code", path: "README.md", match: "contents", line: 4, text: "See the guide." },
    ] } });
    window.kyo.filesSearched({ q: "guide", root: "store", status: 200, document: { ok: true, more: false, results: [] } });
    // An answer to an older text is not drawn.
    window.kyo.filesSearched({ q: "gui", root: "code", status: 200, document: { ok: true, results: [
      { root: "code", path: "old.md", match: "name" },
    ] } });
    const found = document.getElementById("files-found");
    return {
      searching: document.getElementById("files-side").classList.contains("searching") && !found.hidden,
      heads: Array.from(found.querySelectorAll("h4")).map((h) => h.textContent),
      rows: Array.from(found.querySelectorAll(".found-file, .found-line, .found-note")).map((r) => r.textContent),
    };
  })()`);
  assert.equal(drawn.searching, true);
  assert.deepEqual(drawn.heads, ["Code", "Kyosei"], "each root under the label its chip has");
  assert.deepEqual(drawn.rows, ["guide.mddocs", "README.md", "4See the guide.", "Nothing found."]);
});

test("pressing a found line opens the file there and records it as opened", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const opened = await page.run<{ path: string; posted: unknown[] }>(`(() => {
    window.posted.length = 0;
    document.querySelector('#files-found .found-line[data-found-line="4"]').click();
    return {
      path: document.getElementById("files-path").textContent,
      posted: window.posted.filter((p) => p.body.want === "opened").map((p) => p.body),
    };
  })()`);
  assert.match(opened.path, /README\.md/);
  assert.deepEqual(opened.posted, [{ want: "opened", root: "code", path: "README.md" }]);
});

test("Escape in the field ends the search and shows the tree and the lists again", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const shown = await page.run<boolean>(`(() => {
    const field = document.getElementById("files-search");
    field.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    return !document.getElementById("files-side").classList.contains("searching")
      && document.getElementById("files-found").hidden && field.value === "";
  })()`);
  assert.equal(shown, true);
});

test("the recently opened files are asked for on entering Files and listed newest first", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  const result = await page.run<{ asked: boolean; rows: string[] }>(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.posted.length = 0;
    Array.from(document.querySelectorAll(".view-item")).find((item) => item.textContent === "Files").click();
    const asked = window.posted.some((p) => p.body.want === "recent");
    window.kyo.filesRecent({ status: 200, document: { ok: true, limit: 50, entries: [
      { root: "store", path: "docs/proposals/native-apps.md", opened_at: "2026-11-11T19:00:00Z" },
      { root: "code", path: "README.md", opened_at: "2026-11-11T18:00:00Z" },
    ] } });
    return {
      asked,
      rows: Array.from(document.querySelectorAll("#files-recent .mention-name")).map((r) => r.textContent),
    };
  })()`);
  assert.equal(result.asked, true);
  assert.deepEqual(result.rows, ["native-apps.md", "README.md"]);
});
