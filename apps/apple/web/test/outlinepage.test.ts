// Run with `npm test`. A composer box draws a guide for each level a line is indented and a
// rule on each heading line, on the lines they belong to, without changing the text
// (KYO-902). Since the box is an editor (KYO-910), each is a decoration of its line: the
// guides are the line's background, so they run down every row it wraps to, and the rows a
// long nested item wraps to start under its words. A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { test } from "node:test";
import { openPage } from "./chrome.ts";

const TEXT = "# bugs\n- parent\n  - child\n    - grandchild\n  - second child\n## next\nplain";

test("a box draws indent guides and heading rules on their own lines and leaves the text alone (KYO-902)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const seen = await page.run<{
      text: string; guides: number[]; places: string[]; headings: string[];
    }>(`(() => {
      window.kyo.project({ project: "p", scope: "server/p" });
      window.kyo.draft({ boxes: [{ id: "a", text: ${JSON.stringify(TEXT)} }], etag: "e" });
      const lines = [...document.querySelectorAll("#boxes .box.main .cm-line")];
      const layers = (line) => (getComputedStyle(line).backgroundImage.match(/linear-gradient/g) ?? []).length;
      return {
        text: window.kyo.composer.boxes()[0].text,
        guides: lines.map(layers),
        places: [...new Set(lines.filter((line) => layers(line))
          .flatMap((line) => getComputedStyle(line).backgroundPositionX.split(", ")))],
        headings: lines.map((line) => [...line.classList].filter((name) => name === "heading" || name.startsWith("level-")).join(" ")),
      };
    })()`);
    assert.equal(seen.text, TEXT);
    assert.deepEqual(seen.guides, [0, 0, 1, 2, 1, 0, 0]);
    // One place for each level, shared by the lines under it, one indent step apart.
    const places = seen.places.map(parseFloat).sort((a, b) => a - b);
    assert.equal(places.length, 2);
    assert.ok(places[1] - places[0] > 4);
    assert.deepEqual(seen.headings, ["heading level-1", "", "", "", "", "heading level-2", ""]);

    // A text with no nesting and no headings draws neither.
    const cleared = await page.run<number>(`(() => {
      window.kyo.draft({ boxes: [{ id: "a", text: "plain words" }], etag: "e2" });
      return [...document.querySelectorAll("#boxes .box.main .cm-line")]
        .filter((line) => getComputedStyle(line).backgroundImage !== "none" || line.classList.contains("heading")).length;
    })()`);
    assert.equal(cleared, 0);
  } finally {
    await page.close();
  }
});

test("the rows a nested item wraps to start under its first word, in the composer (KYO-910.c4)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const item = "    - grandchild item whose words run on well past the width of the box, so that it wraps "
      + "onto a second row and then onto a third row of the same line";
    const text = `- parent\n  - child\n${item}`;
    const seen = await page.run<{ word: number; rows: number[]; first: number; text: string }>(`(() => {
      window.kyo.project({ project: "p", scope: "server/p" });
      document.documentElement.style.setProperty("--composer-width", "420px");
      window.kyo.draft({ boxes: [{ id: "a", text: ${JSON.stringify(text)} }], etag: "e" });
      return new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(() => {
        const box = window.kyo.composer;
        const text = box.boxes()[0].text;
        const start = text.lastIndexOf("\\n") + 1;
        const word = start + text.slice(start).indexOf("grandchild");
        // The first character of each row the line is drawn in, found where the top changes.
        const rows = [];
        let top = null;
        for (let pos = start; pos < text.length; pos++) {
          const at = box.coords(0, pos);
          if (top === null || at.top > top + 2) rows.push(at.left);
          top = at.top;
        }
        done({ word: box.coords(0, word).left, rows, first: rows[0], text });
      })));
    })()`);
    assert.equal(seen.text, text, "the drawing leaves the text as typed");
    assert.ok(seen.rows.length >= 3, `${seen.rows.length} rows`);
    assert.ok(seen.word - seen.first > 20, "the line's first row starts at its indentation");
    for (const left of seen.rows.slice(1)) {
      assert.ok(Math.abs(left - seen.word) <= 1, `a wrapped row starts at ${left}, the item's words at ${seen.word}`);
    }
  } finally {
    await page.close();
  }
});
