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
  gates: [
    { id: "KYO-9.g1", type: "awaiting:human-run", do: "Look at it" },
    { id: "KYO-9.g2", type: "awaiting:event", do: "Wait", cleared_at: "2026-01-02T00:00:00Z", cleared_as: "answered" },
  ],
  members: [
    { id: "KYO-10", kind: "task", title: "An open member", status: "open" },
    { id: "KYO-11", kind: "task", title: "A finished member", status: "done" },
  ],
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
  const outline = await page.run<{ shown: boolean; rows: string[]; items: string[]; open: boolean; folded: boolean; kept: boolean }>(`(() => {
    const row = document.querySelector('#records-table [data-open="KYO-9"]');
    row.click();
    window.kyo.record({ id: "KYO-9", document: ${JSON.stringify(RECORD)} });
    const section = [...document.querySelectorAll("#record-outline .outline-section")]
      .find((line) => line.textContent.includes("Criteria"));
    // KYO-846: a section lists its items without a press, and one the person folds stays
    // folded when the record is drawn again.
    const open = !section.nextElementSibling.hidden;
    const items = [...section.nextElementSibling.querySelectorAll(".outline-row")].map((line) => line.textContent);
    section.querySelector(".chevron").click();
    const folded = section.nextElementSibling.hidden;
    window.kyo.record({ id: "KYO-9", document: ${JSON.stringify(RECORD)} });
    const again = [...document.querySelectorAll("#record-outline .outline-section")]
      .find((line) => line.textContent.includes("Criteria"));
    const others = [...document.querySelectorAll("#record-outline .outline-items")].filter((list) => list !== again.nextElementSibling);
    return {
      open, folded, items,
      kept: again.nextElementSibling.hidden && others.every((list) => !list.hidden),
      shown: document.body.classList.contains("has-outline"),
      rows: [...document.querySelectorAll("#record-outline > .outline-row, #record-outline .outline-section .outline-row")]
        .map((line) => line.textContent),
    };
  })()`);
  assert.equal(outline.shown, true);
  assert.equal(outline.open, true, "a section's items show without a press");
  assert.equal(outline.folded, true, "the caret folds it");
  assert.equal(outline.kept, true, "it stays folded when the record is drawn again, the others open");
  assert.equal(outline.rows[0], "KYO-9");
  assert.ok(outline.rows.some((row) => row.startsWith("Criteria")), outline.rows.join(" | "));
  assert.deepEqual(outline.items.length, 2);
  assert.ok(outline.items[0].startsWith("c1"), outline.items[0]);
});

test("an item's row holds its id, its first words and its state, in red while a gate or a member is open", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const found = await page.run<Record<string, { ref: string; text: string; state: string; open: boolean }[]>>(`(() => {
    const out = {};
    for (const section of document.querySelectorAll("#record-outline .outline-section")) {
      const name = section.textContent.split(" ")[0].replace("›", "").trim();
      out[name] = [...section.nextElementSibling.querySelectorAll(".outline-item")].map((row) => ({
        ref: row.querySelector(".outline-ref")?.textContent ?? "",
        text: row.querySelector(".outline-text")?.textContent ?? "",
        state: row.querySelector(".outline-state")?.textContent ?? "",
        open: Boolean(row.querySelector(".outline-state.open")),
      }));
    }
    return out;
  })()`);
  assert.deepEqual(found.Criteria, [
    { ref: "c1:", text: "The first", state: "met", open: false },
    { ref: "c2:", text: "The second", state: "unmet-unbuilt", open: false },
  ]);
  assert.deepEqual(found.Gates, [
    { ref: "g1:", text: "Look at it", state: "open", open: true },
    { ref: "g2:", text: "Wait", state: "cleared as answered", open: false },
  ]);
  assert.deepEqual(found.Members, [
    { ref: "", text: "KYO-10 An open member", state: "open", open: true },
    { ref: "", text: "KYO-11 A finished member", state: "done", open: false },
  ]);
});
