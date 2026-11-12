// Run with `npm test`. The chat's side bar lists the links in the person's prompts and the
// assistant's replies (2026-11-11): records, web addresses, served files and paths outside
// them, and not those in a subagent's hand-back or a reply's thinking, which Files' list of
// mentioned files still counts. A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

interface Row { name: string; place: string; kind: string; href: string | null }

const rows = `Array.from(document.querySelectorAll("#side-linked .side-link")).map((row) => ({
  name: row.querySelector(".mention-name").textContent,
  place: row.querySelector(".mention-place").textContent,
  kind: row.dataset.kind,
  href: row.getAttribute("href"),
}))`;

const wait = (ms: number) => new Promise((done) => setTimeout(done, ms));

const resolvedFile = {
  found: true, given: "docs/guide.md", line: null, media_type: "text/markdown",
  mtime_ns: 1794337075722407531, name: "guide.md", ok: true, path: "docs/guide.md", root: "code",
  size: 269, text: true, type: "file",
};

test("the side bar lists the links of prompts and replies, newest first, and no others", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  await page.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.apply({ reset: true,
      turns: [{ id: "u1", at: "2026-01-01T00:00:00Z", text: "See https://example.org/notes please." }],
      replies: [{ message_id: "m1", uuid: "x1", at: "2026-01-01T00:00:02Z",
        text: "Filed [KYO-810](kyosei://KYO/KYO-810); the guide is \`docs/guide.md\`.",
        thinking: "Maybe [KYO-999](kyosei://KYO/KYO-999)." }],
      deliveries: [{ uuid: "d1", at: "2026-01-01T00:00:03Z", kind: "agent-message",
        text: "Report: [KYO-700](kyosei://KYO/KYO-700) and https://elsewhere.example/x",
        sender: "w", sender_id: "w", handback: true, task_id: null, call_id: null, status: null,
        summary: null }],
    });
    window.kyo.filesResolved({ given: "docs/guide.md", status: 200, document: ${JSON.stringify(resolvedFile)} });
  })()`);
  await wait(700);
  const listed = await page.run<Row[]>(rows);
  assert.deepEqual(listed.map((row) => [row.kind, row.name]), [
    ["record", "KYO-810"], ["file", "guide.md"], ["url", "example.org/notes"],
  ]);
  assert.equal(listed[0].href, "kyosei://KYO/KYO-810");
  assert.equal(listed[1].place, "Code · docs");
  // A record is named by its title once the record list is read, which the row asks for.
  assert.equal(listed[0].place, "Record");
  const asked = await page.run<boolean>(`window.posted.some((p) => p.name === "records" && p.body.want === "lists")`);
  assert.equal(asked, true, "the list was asked for");
  await page.run(`window.kyo.recordLists({ list: { records: [
    { id: "KYO-810", kind: "bug", title: "A prompt was typed but not submitted", status: "open" }] } })`);
  assert.equal((await page.run<Row[]>(rows))[0].place, "A prompt was typed but not submitted");
});

test("a row of the list opens what it names: a file in Files, a record in Records", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const shown = await page.run<{ files: boolean; records: boolean }>(`(() => {
    const view = (name) => !document.getElementById(name + "-side")?.hidden;
    document.querySelector('#side-linked .side-link[data-kind="file"]').click();
    const files = view("files");
    document.querySelector('#side-linked .side-link[data-kind="record"]').click();
    return { files, records: view("records") };
  })()`);
  assert.deepEqual(shown, { files: true, records: true });
});

test("pressing the open view's name in the top bar goes to its home, as its Home button does", async (t) => {
  if (!page) return t.skip("no Chrome here");
  const states = await page.run<boolean[]>(`(() => {
    const home = () => document.getElementById("records-home").disabled;
    const records = () => Array.from(document.querySelectorAll(".view-item"))
      .find((item) => item.textContent === "Records");
    document.querySelector('#side-linked .side-link[data-kind="record"]').click();
    const opened = home();
    records().click();
    return [opened, home()];
  })()`);
  assert.deepEqual(states, [false, true], "a record was open, and the press closed it");
});
