// Run with `npm test`. Evidence that names a composer attachment (KYO-856): the record's page
// draws the file where the evidence is listed and asks the app for it by the attachment's id
// and the session it is kept under, which is the attachment route's address. The browser
// tests need a web view, and a machine with no Chrome skips them.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";
import { attachedOf, recordPage } from "../src/records.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const ID = "ab".repeat(32);
const PDF_ID = "cd".repeat(32);
const GONE_ID = "ef".repeat(32);
const reference = (id: string, extension: string, session: string, present = true) => ({
  text: `attachment ${id}.${extension} of session ${session}`,
  attachment: {
    id, session, name: `${id}.${extension}`, media_type: "image/png",
    path: `/home/composer/attachments/p/${session}/${id}.${extension}`, present,
  },
});
const RECORD = {
  id: "KYO-9", kind: "bug", title: "A defect with a screenshot", status: "open",
  evidence_artifacts: [
    { id: "KYO-9.e1", sha256: "11".repeat(32), author: "agent", ...reference(ID, "png", "records") },
    { id: "KYO-9.e2", sha256: "22".repeat(32), author: "agent", text: "a capture in words" },
    { id: "KYO-9.e3", sha256: "33".repeat(32), author: "agent", ...reference(PDF_ID, "pdf", "S1") },
    { id: "KYO-9.e4", sha256: "44".repeat(32), author: "agent", ...reference(GONE_ID, "png", "S1", false) },
  ],
};

test("evidence that names an attachment carries it, and other evidence does not", () => {
  const items = recordPage(RECORD).sections.find((part) => part.name === "Evidence")!.items;
  assert.deepEqual(items[0].attached, {
    id: ID, session: "records", name: `${ID}.png`, extension: "png",
    line: `attachment ${ID}.png of session records`,
  });
  assert.equal(items[0].text, "", "the image stands where the line would");
  assert.equal(items[1].attached, undefined);
  assert.equal(items[1].text, "a capture in words");
  assert.equal(items[2].attached?.extension, "pdf");
  // A file the server says is gone is not asked for; the line stays and the item says so.
  assert.equal(items[3].attached, undefined);
  assert.equal(items[3].text, `attachment ${GONE_ID}.png of session S1`);
  assert.ok(items[3].meta!.includes("the file is no longer there"));
  // Text shaped as a reference is not one unless the server resolved it.
  assert.equal(attachedOf({ text: `attachment ${ID}.png of session records` }), null);
  assert.equal(attachedOf({ attachment: { id: ID, session: "S1", name: "../x.png", present: true } }), null);
});

test("the record's page draws a referenced image and asks for it through the attachment route", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  const drawn = await page.run<{
    asked: unknown[]; waiting: (string | null)[]; texts: string[]; chip: string | null;
    shown: string | null; pending: number; missing: string | null;
  }>(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.records({ project: "p" });
    [...document.querySelectorAll(".view-item")].find((item) => item.textContent.includes("Records")).click();
    window.kyo.recordLists({ list: { records: [{ id: "KYO-9", kind: "bug", title: "A defect with a screenshot", status: "open" }] } });
    document.querySelector('#records-groups [data-open="KYO-9"]').click();
    window.kyo.record({ id: "KYO-9", document: ${JSON.stringify(RECORD)} });
    const item = (ref) => document.querySelector('.rec-item[data-ref="' + ref + '"]');
    const asked = window.posted.filter((p) => p.name === "image").map((p) => p.body);
    const waiting = [...document.querySelectorAll(".rec-item img.turn-image")].map((image) => image.dataset.image ?? null);
    const texts = ["KYO-9.e1", "KYO-9.e2", "KYO-9.e4"].map((ref) => item(ref).querySelector(".rec-text")?.textContent.trim() ?? "");
    const chip = item("KYO-9.e3").querySelector(".turn-anchor")?.textContent ?? null;
    // The app answers with the file's bytes, as it does for a prompt's attachment.
    window.kyo.image({ key: "a:${ID}", data: "data:image/png;base64,iVBORw0KGgo=" });
    const image = item("KYO-9.e1").querySelector("img.turn-image");
    const shown = image.getAttribute("src");
    const pending = document.querySelectorAll(".rec-item img[data-image]").length;
    return { asked, waiting, texts, chip, shown, pending, missing: item("KYO-9.e4").querySelector("img") ? "drawn" : null };
  })()`);
  assert.deepEqual(drawn.asked, [{ attachment: ID, session: "records" }], "one request: the image, by id and session");
  assert.deepEqual(drawn.waiting, [`a:${ID}`]);
  assert.deepEqual(drawn.texts, ["", "a capture in words", `attachment ${GONE_ID}.png of session S1`]);
  assert.match(drawn.chip ?? "", /^PDF cdcdcdcd$/, "a PDF is named in a chip and not fetched");
  assert.equal(drawn.shown, "data:image/png;base64,iVBORw0KGgo=");
  assert.equal(drawn.pending, 0);
  assert.equal(drawn.missing, null, "a file that is gone is not drawn");
});
