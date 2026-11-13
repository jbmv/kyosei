// Run with `npm test`. KYO-809: a held prompt taken back returns with its anchors and tags on
// any device. The page here has never held the prompt (nothing of it was kept in its storage,
// as on a second machine) and is handed the frame the Mac app builds from the server's answer
// to the retraction. The server's half, that the answer carries the prompt's marks, is tested
// against a real server in tests/test_kyo_serve_held.py. A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const marks = [
  {
    line: 0,
    anchors: [
      { kind: "turn", session: "S1", start: 3, end: 9, uuid: "u-1" },
      { kind: "record", id: "KYO-12" },
    ],
    tags: ["review"],
  },
  { line: 1, anchors: [{ kind: "file", root: "store", path: "docs/a.md", lines: [2, 4] }], tags: [] },
];

test("a prompt taken back on another device keeps its anchors and tags", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  const saved = await page.run<{ text: string; marks: { line: number; items: { kind: string }[] }[] }>(`(async () => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.retracted(${JSON.stringify({ text: "first\nsecond", prompt: { marks } })});
    await new Promise((done) => setTimeout(done, 1200));
    const asked = window.posted.filter((message) => message.name === "saveDraft");
    return asked[asked.length - 1].body.boxes.find((box) => box.text === "first\\nsecond");
  })()`);
  assert.ok(saved, "the box was rebuilt");
  assert.deepEqual(saved.marks.map((mark) => [mark.line, mark.items.map((item) => item.kind)]), [
    [0, ["anchor", "record", "tag"]],
    [1, ["path"]],
  ]);
  const drawn = await page.run<string[]>(
    `[...document.querySelectorAll("#boxes .mark.held")].map((mark) => mark.title)`);
  assert.deepEqual(drawn, [
    "anchor: an earlier message (session ended)\nanchor: KYO-12\n#review",
    "anchor: a.md:2",
  ]);
});

test("a plain prompt taken back is its text alone", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  const texts = await page.run<string[]>(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.retracted({ text: "plain" });
    return window.kyo.composer.boxes().map((box) => box.text);
  })()`);
  assert.deepEqual(texts, ["plain"]);
});

const order = `window.kyo.composer.boxes().map((box) => box.text).filter(Boolean)`;

test("a prompt taken back is the prompt again, and what was typed since becomes a scratchpad (KYO-903)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  await page.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.screen({ screen: { state: "working" } });
    window.kyo.draft({ boxes: [{ id: "a", text: "notes", scratch: { heading: "kept" } }, { id: "b", text: "sent" }], etag: "e" });
    document.querySelector("#boxes .box.main .box-send").click();
    window.kyo.sent({ id: "m1" });
    window.kyo.composer.select(window.kyo.composer.boxes().findIndex((box) => box.kind === "prompt"), 0);
  })()`);
  await page.type("typed since");
  const seen = await page.run<{ texts: string[]; kinds: string[] }>(`(() => {
    window.kyo.retracted({ text: "sent" });
    const boxes = window.kyo.composer.boxes();
    return { texts: boxes.map((box) => box.text), kinds: boxes.map((box) => box.kind) };
  })()`);
  // The scratchpad's heading, from an earlier build, is its first line (KYO-917).
  assert.deepEqual(seen.texts, ["kept\nnotes", "typed since", "sent"]);
  assert.deepEqual(seen.kinds, ["scratch", "scratch", "prompt"]);
});

test("a prompt taken back whose place this window does not know goes last, as the latest sent (KYO-883)", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  const texts = await page.run<string[]>(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.draft({ boxes: [{ id: "a", text: "first" }, { id: "b", text: "second" }], etag: "e" });
    window.kyo.retracted({ text: "from elsewhere" });
    return ${order};
  })()`);
  assert.deepEqual(texts, ["first", "second", "from elsewhere"]);
});
