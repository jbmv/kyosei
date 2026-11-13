// Run with `npm test`. A composer box draws a guide for each level a line is indented and a
// rule on each heading line, placed on the lines they belong to, without changing the text
// (KYO-902). A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { test } from "node:test";
import { openPage } from "./chrome.ts";

const TEXT = "# bugs\n- parent\n  - child\n    - grandchild\n  - second child\n## next\nplain";

test("a box draws indent guides and heading rules on their own lines and leaves the text alone (KYO-902)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const seen = await page.run<{
      text: string; guides: number[][]; headings: [string, number][]; lines: number[];
      press: string; lefts: number[];
    }>(`(() => {
      window.kyo.project({ project: "p", scope: "server/p" });
      const area = document.querySelector("#boxes textarea");
      area.value = ${JSON.stringify(TEXT)};
      area.dispatchEvent(new InputEvent("input", { inputType: "insertText" }));
      const words = area.parentElement;
      const lines = [...words.querySelector(".box-mirror").children].map((line) => line.offsetTop);
      const guides = [...words.querySelectorAll(".outline-guide")];
      return {
        text: area.value,
        lines,
        guides: lines.map((top) => guides.filter((g) => g.offsetTop === top).map((g) => g.offsetHeight)),
        lefts: [...new Set(guides.map((g) => Math.round(g.offsetLeft)))].sort((a, b) => a - b),
        headings: [...words.querySelectorAll(".outline-heading")].map((h) => [h.className, h.offsetTop]),
        press: getComputedStyle(words.querySelector(".box-outline")).pointerEvents,
      };
    })()`);
    assert.equal(seen.text, TEXT);
    assert.deepEqual(seen.guides.map((line) => line.length), [0, 0, 1, 2, 1, 0, 0]);
    assert.equal(seen.lefts.length, 2);  // one place for each level, shared by the lines under it
    assert.ok(seen.lefts[1] - seen.lefts[0] > 4);
    assert.deepEqual(seen.headings, [
      ["outline-heading level-1", seen.lines[0]], ["outline-heading level-2", seen.lines[5]],
    ]);
    assert.equal(seen.press, "none");

    const cleared = await page.run<number>(`(() => {
      const area = document.querySelector("#boxes textarea");
      area.value = "plain words";
      area.dispatchEvent(new InputEvent("input", { inputType: "insertText" }));
      return area.parentElement.querySelector(".box-outline").children.length;
    })()`);
    assert.equal(cleared, 0);
  } finally {
    await page.close();
  }
});
