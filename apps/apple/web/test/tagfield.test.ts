// Run with `npm test`. The composer's tag field (KYO-941). A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { openPage } from "./chrome.ts";
import { TAG_PATTERN, TAG_RULE, tagProblem } from "../src/tags.ts";

const START = `window.kyo.project({ project: "p", scope: "server/p" });`;
const OPEN = `(() => { ${START}
  window.kyo.draft({ boxes: [{ id: "m", text: "a paragraph" }], etag: "e" });
  window.kyo.composer.select(0, 0);
  const gutter = document.querySelector("#boxes .box.main .cm-gutter.kyo-marks");
  gutter.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true,
    clientX: gutter.getBoundingClientRect().left + 5,
    clientY: document.querySelector("#boxes .box.main .cm-line").getBoundingClientRect().top + 4 }));
  document.querySelector(".mark-panel input").focus();
})()`;

test("typing tag-test one character at a time keeps what is typed (KYO-941.c5)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(OPEN);
    const seen: string[] = [];
    for (const letter of "tag-test") {
      await page.type(letter);
      seen.push(await page.run<string>(`document.querySelector(".mark-panel input").value`));
    }
    assert.deepEqual(seen, ["t", "ta", "tag", "tag-", "tag-t", "tag-te", "tag-tes", "tag-test"]);
  } finally { await page.close(); }
});

test("typing tag-test with a key event and the app's draft echo between the characters keeps what is typed (KYO-941.c5)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(OPEN);
    const seen: string[] = [];
    for (const letter of "tag-test") {
      await page.run(`document.querySelector(".mark-panel input").dispatchEvent(new KeyboardEvent("keydown", { key: ${JSON.stringify(letter)}, bubbles: true, cancelable: true }))`);
      await page.type(letter);
      await page.run(`window.kyo.draftElsewhere?.({ boxes: [{ id: "m", text: "a paragraph" }], etag: "e" })`);
      seen.push(await page.run<string>(`document.querySelector(".mark-panel input")?.value ?? null`));
    }
    assert.deepEqual(seen, ["t", "ta", "tag", "tag-", "tag-t", "tag-te", "tag-tes", "tag-test"]);
  } finally { await page.close(); }
});

const ROOT = new URL("../../../../", import.meta.url);

test("the field's rule and the server's accept the same tags (KYO-941.c2)", () => {
  const source = readFileSync(new URL("src/kyosei/cmd_notes.py", ROOT), "utf8");
  const pattern = source.match(/^TAG_RE = re\.compile\(r"(.*)"\)$/m)?.[1];
  assert.ok(pattern, "cmd_notes.py states TAG_RE");
  const server = new RegExp(pattern);
  assert.equal(TAG_PATTERN.source, server.source, "one statement of the rule");
  const corpus = ["", " ", "a", "tag", "tag-test", "tag test", "a,b", ",", "#", "##x", "#x y", " #name ", "Name",
    "a\tb", "a\nb", "a b", "é", "x".repeat(64), "x".repeat(65), "#" + "x".repeat(64), "#" + "x".repeat(65), "-", "a/b", "a;b"];
  for (const raw of corpus) {
    // `normalise_tag` strips the text, then every leading `#`, then lower-cases it.
    const accepted = server.test(raw.trim().replace(/^#+/, "").toLowerCase());
    assert.equal(tagProblem(raw) === null, accepted, `the two disagree on ${JSON.stringify(raw)}`);
  }
  // The server's refusal names the tag it refused, and states the rule the field states.
  assert.ok(source.includes(`must be 1 to 64 characters with no space or comma`));
  assert.ok(TAG_RULE.endsWith("must be 1 to 64 characters with no space or comma"));
});

test("a space or a comma typed in the tag field enters nothing and says so, and a long tag is not added (KYO-941.c1, c4)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(OPEN);
    const read = `({ value: document.querySelector(".mark-panel input").value, note: document.querySelector(".mark-panel .tag-note").textContent,
      noteShown: getComputedStyle(document.querySelector(".mark-panel .tag-note")).display !== "none",
      tags: [...document.querySelectorAll(".mark-item")].map((chip) => chip.textContent) })`;
    type Field = { value: string; note: string; noteShown: boolean; tags: string[] };
    await page.type("tag");
    await page.type(" ");
    let now = await page.run<Field>(read);
    assert.equal(now.value, "tag");
    assert.match(now.note, /space is not allowed.*1 to 64 characters with no space or comma/);
    assert.equal(now.noteShown, true);
    await page.type(",");
    now = await page.run<Field>(read);
    assert.equal(now.value, "tag");
    assert.match(now.note, /comma is not allowed/);
    // The next accepted character clears the note.
    await page.type("x");
    now = await page.run<Field>(read);
    assert.deepEqual([now.value, now.note], ["tagx", ""]);
    // Too long: Enter adds nothing and the field says why.
    await page.run(`(() => { const field = document.querySelector(".mark-panel input"); field.value = "x".repeat(65);
      field.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true })); })()`);
    now = await page.run<Field>(read);
    assert.deepEqual(now.tags.filter((tag) => tag.startsWith("#")), []);
    assert.match(now.note, /is not added: a tag must be 1 to 64 characters with no space or comma/);
    // A valid one is added, lower-cased, without the `#`.
    await page.run(`(() => { const field = document.querySelector(".mark-panel input"); field.value = "#Fine";
      field.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true })); })()`);
    assert.deepEqual((await page.run<Field>(read)).tags.filter((tag) => tag.startsWith("#")).map((tag) => tag.replace(/×$/, "")), ["#fine"]);
  } finally { await page.close(); }
});

test("a refused tag kept in an older draft is marked invalid on its chip (KYO-941.c3)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    await page.run(`(() => { ${START}
      window.kyo.draft({ boxes: [{ id: "m", text: "a paragraph", marks: [{ line: 0, items: [{ kind: "tag", name: "tag test" }, { kind: "tag", name: "ok" }] }] }], etag: "e" });
      window.kyo.composer.select(0, 0);
      document.querySelector("#boxes .box.main .mark.held").click();
    })()`);
    const chips = await page.run<{ text: string; invalid: boolean; title: string }[]>(
      `[...document.querySelectorAll(".mark-panel .mark-item")].map((chip) => ({ text: chip.textContent, invalid: chip.classList.contains("invalid"), title: chip.title }))`);
    assert.deepEqual(chips.map((chip) => [chip.text.replace(/×$/, ""), chip.invalid]), [["#tag test", true], ["#ok", false]]);
    assert.match(chips[0].title, /1 to 64 characters with no space or comma/);
  } finally { await page.close(); }
});
