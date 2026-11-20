// Run with `npm test`. An anchor to lines of a file in the Files view (KYO-977): what a
// right-click on a block records, the text and context the page sends with it, and how a sent
// anchor's chip reads when the server gives its file as gone, its text as changed, or its
// text as moved. The page is the built renderer in a headless Chrome, fed the frames the app
// would send; a machine with no Chrome skips these. The server half is
// tests/test_kyo977_anchor_text.py.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

/** A heading, a blank line, a rule and a paragraph, as KYO-977.c8 asks for. */
const RULED = "# The heading\n\n---\n\nA paragraph under the rule.\n";

/** A project and a session with one prompt, and the Files view showing `path` with `text`. */
async function openFile(path: string, text: string): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.apply({ reset: true, turns: [{ id: "u1", at: "2026-01-01T00:00:00Z", text: "A prompt." }], replies: [] });
    window.kyo.composer.select(0, 0);
    [...document.querySelectorAll(".view-item")].find((item) => item.textContent.includes("Files")).click();
    window.kyo.filesRoots({ status: 200, document: { ok: true, roots: [{ name: "code", path: "/work/demo", served: true }] } });
    window.kyo.filesTree({ root: "code", path: "", session: null, status: 200, document: { ok: true, entries: [
      { name: ${JSON.stringify(path)}, path: ${JSON.stringify(path)}, type: "file", size: 10, mtime_ns: 1e18 } ] } });
    document.querySelector('.files-columns .tree-row[data-file=${JSON.stringify(path)}]').click();
    window.kyo.filesBlob({ root: "code", path: ${JSON.stringify(path)}, session: null, status: 200, etag: '"e"',
      media_type: ${JSON.stringify(path.endsWith(".md") ? "text/markdown" : "text/x-python")},
      size: ${text.length}, text: ${JSON.stringify(text)}, data: null, error: null });
  })()`);
  return opened;
}

/** Right-clicks the element `selector` of the open file and takes Anchor, the caret's paragraph. */
const anchorAt = (opened: Page, selector: string) => opened.run<boolean>(`(() => {
  const target = document.querySelector(${JSON.stringify(selector)});
  if (!target) return false;
  const box = target.getBoundingClientRect();
  target.dispatchEvent(new MouseEvent("contextmenu", {
    bubbles: true, cancelable: true, clientX: box.left + 5, clientY: box.top + box.height / 2,
  }));
  const entry = document.querySelector("#anchor-menu .anchor-new");
  if (!entry) return false;
  entry.click();
  return true;
})()`);

/** Types a prompt into the first box, sends it, and gives the anchors of its first mark as posted. */
async function sentAnchors(opened: Page): Promise<Record<string, unknown>[]> {
  await opened.run(`window.kyo.composer.select(0, 0)`);
  await opened.type("about this");
  await opened.run(`document.querySelector("#boxes .box.main .box-send").click()`);
  return opened.run(`(() => {
    const sent = window.posted.filter((m) => m.name === "send").at(-1);
    return sent ? sent.body.marks.flatMap((mark) => mark.anchors) : [];
  })()`);
}

test("a right-click on a heading records its own line, and a rule offers no anchor (KYO-977.c8)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const found: Record<string, unknown> = {};
  for (const [name, selector] of [["heading", ".file-body h1"], ["paragraph", ".file-body p"]]) {
    const opened = await openFile("ruled.md", RULED);
    assert.equal(await anchorAt(opened, selector), true, `${name}: the right-click offers Anchor`);
    const [anchor] = await sentAnchors(opened);
    found[name] = anchor?.lines;
  }
  // The heading is line 1 and the paragraph line 5, never the blank line between them.
  assert.deepEqual(found, { heading: [1, 1], paragraph: [5, 5] });
  // The rule is drawn with no source line (pipeline.ts stamps a block's opening token only,
  // and a rule has none), so a right-click on it finds no block and opens no menu.
  const opened = await openFile("ruled.md", RULED);
  assert.equal(await opened.run(`document.querySelector(".file-body hr").hasAttribute("data-source-line")`), false);
  assert.equal(await anchorAt(opened, ".file-body hr"), false);
});

const SOURCE = "import os\n\n\ndef reformat(text):\n    return text.strip()\n\nLIMIT = 3\n";

test("an anchor sends its lines and two lines either side as the file was shown when it was placed (KYO-977.c2)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await openFile("tidy.py", SOURCE);
  // A right-click on line 5 of the source.
  const placed = await opened.run<boolean>(`(() => {
    const code = document.querySelector(".file-code code");
    const walker = document.createTreeWalker(code, NodeFilter.SHOW_TEXT);
    let node; let at = 0;
    while ((node = walker.nextNode())) {
      const index = node.data.indexOf("return");
      if (index >= 0) { at = index; break; }
    }
    const range = document.createRange();
    range.setStart(node, at); range.setEnd(node, at + 1);
    const box = range.getBoundingClientRect();
    code.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: box.left + 1, clientY: box.top + box.height / 2 }));
    const entry = document.querySelector("#anchor-menu .anchor-new");
    if (!entry) return false;
    entry.click();
    return true;
  })()`);
  assert.equal(placed, true);
  // The file changes on the server after the anchor was placed, and the page is sent the new text.
  await opened.run(`window.kyo.filesBlob({ root: "code", path: "tidy.py", session: null, status: 200, etag: '"f"',
    media_type: "text/x-python", size: 60, text: ${JSON.stringify(SOURCE.replace("strip", "rstrip"))}, data: null, error: null })`);
  assert.match(await opened.run<string>(`document.querySelector(".file-code code").textContent`), /rstrip/,
    "the view shows the file as it is now");
  const [anchor] = await sentAnchors(opened);
  assert.deepEqual(anchor, {
    kind: "file", root: "code", path: "tidy.py", lines: [5, 5],
    seen: { before: ["", "def reformat(text):"], lines: ["    return text.strip()"], after: ["", "LIMIT = 3"] },
  });
});

/** A chat whose sent prompt holds one anchor to lines 4-5 of src/tidy.py, and the marks the server gives for it. */
async function sentFileChip(place: Record<string, unknown>): Promise<{ stale: boolean; said: string; title: string; moved: string | null }> {
  const opened = page!;
  await opened.reload();
  const mark = { note: "n1", text: "about this", tags: [], sent: "2026-01-01T00:00:02Z",
    anchors: [{ kind: "file", root: "code", path: "src/tidy.py", commit: "abc1234", lines: [4, 5], ...place }] };
  return opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.apply({ reset: true, replies: [], turns: [{ id: "u1", at: "2026-01-01T00:00:02Z",
      text: ${JSON.stringify('on file src/tidy.py lines 4-5 "def reformat(text): return text.strip()"\nabout this')} }] });
    window.kyo.marks({ marks: ${JSON.stringify([mark])} });
    const chip = document.querySelector("#chat .turn .turn-place");
    return { stale: chip.classList.contains("stale"), said: getComputedStyle(chip, "::after").content,
      title: chip.title, moved: chip.dataset.movedTo ?? null };
  })()`);
}

const ANCHORED = "def reformat(text): return text.strip()";

test("a sent anchor whose file is gone reads 'file deleted or moved', and one whose text changed 'stale reference' (KYO-977.c5, c6)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const line = 'on file src/tidy.py lines 4-5 "def reformat(text): return text.strip()"';
  const gone = await sentFileChip({ stale: true, stale_reason: "absent", anchored: ANCHORED });
  assert.equal(gone.stale, true);
  assert.equal(gone.said, '"file deleted or moved"');
  assert.equal(gone.title, `${line}\n(stale reference: the file was deleted or moved since "def reformat(text): return text.strip()" was anchored)`);
  const changed = await sentFileChip({ stale: true, stale_reason: "changed", anchored: ANCHORED });
  assert.equal(changed.stale, true);
  assert.equal(changed.said, '"stale reference"');
  assert.equal(changed.title, `${line}\n(stale reference: the file was edited since "def reformat(text): return text.strip()" was anchored)`);
  const twice = await sentFileChip({ stale: true, stale_reason: "ambiguous", found: [[5, 6], [11, 12]], anchored: ANCHORED });
  assert.equal(twice.said, '"stale reference"');
  assert.match(twice.title, /was anchored, and those words are now at 2 places in it\)$/);
  // An anchor filed before the text was stored has no words to quote.
  const old = await sentFileChip({ stale: true, stale_reason: "changed" });
  assert.match(old.title, /\(stale reference: the file was edited since this was anchored\)$/);
  // Words longer than the hover quotes are cut.
  const long = await sentFileChip({ stale: true, stale_reason: "changed", anchored: "one two three four five six seven eight nine ten" });
  assert.match(long.title, /since "one two three four five six seven eight…" was anchored\)$/);
});

test("a sent anchor whose text moved is not stale, and names the lines it is at now (KYO-977.c4)", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const moved = await sentFileChip({ lines: [6, 7], moved_from: [4, 5] });
  assert.equal(moved.stale, false);
  assert.equal(moved.said, "none");
  assert.match(moved.title, /\n\(moved: the file was edited, and what was anchored at lines 4-5 is now at lines 6-7\)$/);
  assert.equal(moved.moved, "6-7");
  const fresh = await sentFileChip({});
  assert.deepEqual([fresh.stale, fresh.said, fresh.moved], [false, "none", null]);
  assert.equal(fresh.title.includes("\n"), false);
});
