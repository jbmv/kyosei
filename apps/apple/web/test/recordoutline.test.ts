// Run with `npm test`. The outline of an open record in the right drawer (KYO-835): a row for
// the record, then each section of its page with its count, whose caret lists the section's
// items with their states, and which is shown only while a record is open. A machine with no
// Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const RECORD = {
  id: "KYO-9", kind: "task", title: "A record with sections", status: "open",
  description: "What it is about.",
  criteria: [
    { id: "KYO-9.c1", text: "The first", state: "met" },
    { id: "KYO-9.c2", text: "The second", state: "unmet-unbuilt" },
  ],
  touches: [{ id: "KYO-9.t1", path: "src/a.py" }],
};

test("the outline names the record's sections, lists a section's items on its caret, and shows only with a record open", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  const before = await page.run<boolean>(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.records({ project: "p" });
    [...document.querySelectorAll(".view-item")].find((item) => item.textContent.includes("Records")).click();
    window.kyo.recordLists({ list: { records: [{ id: "KYO-9", kind: "task", title: "A record with sections", status: "open" }] } });
    return document.body.classList.contains("has-outline");
  })()`);
  assert.equal(before, false, "no outline on the home page");
  const outline = await page.run<{ shown: boolean; rows: string[]; items: string[] }>(`(() => {
    const row = document.querySelector('#records-groups [data-open="KYO-9"]');
    row.click();
    window.kyo.record({ id: "KYO-9", document: ${JSON.stringify(RECORD)} });
    const section = [...document.querySelectorAll("#record-outline .outline-section")]
      .find((line) => line.textContent.includes("Criteria"));
    section.querySelector(".chevron").click();
    return {
      shown: document.body.classList.contains("has-outline"),
      rows: [...document.querySelectorAll("#record-outline > .outline-row, #record-outline .outline-section .outline-row")]
        .map((line) => line.textContent),
      items: [...section.nextElementSibling.querySelectorAll(".outline-row")].map((line) => line.textContent),
    };
  })()`);
  assert.equal(outline.shown, true);
  assert.equal(outline.rows[0], "KYO-9");
  assert.ok(outline.rows.some((row) => row.startsWith("Criteria")), outline.rows.join(" | "));
  assert.deepEqual(outline.items.length, 2);
  assert.ok(outline.items[0].startsWith("c1"), outline.items[0]);
});
