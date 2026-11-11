// Run with `npm test`. A path in a reminder of the session's list is a link to Files, as one in
// a reply is (2026-11-11): the page asks the server where it is, draws it as a link when the
// server places it, and a press on it opens the file without opening the reminder to edit. A
// machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const wait = (ms: number) => new Promise((done) => setTimeout(done, ms));

const resolved = {
  found: true, given: "ideas/small-things.md", line: null, media_type: "text/markdown",
  mtime_ns: 1, name: "small-things.md", ok: true, path: "ideas/small-things.md", root: "store",
  size: 10, text: true, type: "file",
};

test("a path in a reminder is asked about, drawn as a link, and opens Files when pressed", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  await page.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.agenda({ state: "shown", session_id: "S1", etag: '"e"', items: [
      { id: "p1", text: "Review: ideas/small-things.md", author: "person", checked: false },
    ] });
  })()`);
  await wait(100);
  const asked = await page.run<string[]>(
    `window.posted.filter((p) => p.body.want === "resolve").map((p) => p.body.given)`);
  assert.deepEqual(asked, ["ideas/small-things.md"]);
  await page.run(`window.kyo.filesResolved({ given: "ideas/small-things.md", status: 200, document: ${JSON.stringify(resolved)} })`);
  await wait(100);
  const result = await page.run<{ link: string | null; files: boolean; editing: boolean }>(`(() => {
    const link = document.querySelector('#agenda-items li[data-id="p1"] .agenda-text a.path-link');
    const text = link?.textContent ?? null;
    link?.click();
    return {
      link: text,
      files: !document.getElementById("files-side").hidden,
      editing: !!document.querySelector("#agenda-items .agenda-edit"),
    };
  })()`);
  assert.deepEqual(result, { link: "ideas/small-things.md", files: true, editing: false });
});
