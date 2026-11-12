// Run with `npm test`. The terminal's button stands in the views bar on every view, and from
// another view it opens Chat with the terminal shown (KYO-883); Chat has no side bar, and its
// second row is shown only to hold an open search. A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { test } from "node:test";
import { openPage } from "./chrome.ts";

test("the terminal button is in the views bar on every view, and from another view opens Chat with the terminal (KYO-883)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const shown = await page.run(`(() => {
      window.kyo.project({ project: "p", scope: "server/p" });
      const vis = () => getComputedStyle(document.getElementById("terminal-toggle")).display !== "none";
      const go = (name) => [...document.querySelectorAll(".view-item")].find((i) => i.textContent === name).click();
      const out = [vis(), Boolean(document.querySelector("#views #terminal-toggle"))];
      for (const v of ["Records", "Files", "History"]) { go(v); out.push(vis()); }
      document.getElementById("terminal-toggle").click();
      out.push(document.body.classList.contains("view-chat"), document.body.classList.contains("terminal-open"));
      return out;
    })()`);
    assert.deepEqual(shown, [true, true, true, true, true, true, true]);
  } finally {
    await page.close();
  }
});

test("Chat has no side bar, and its second row is shown only while a search is open (KYO-883)", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const seen = await page.run<{ menu: string; row: string; pageTop: number; views: number; searching: string }>(`(() => {
      window.kyo.project({ project: "p", scope: "server/p" });
      const out = {
        menu: getComputedStyle(document.getElementById("menu")).display,
        row: getComputedStyle(document.getElementById("top")).display,
        pageTop: Math.round(document.getElementById("page").getBoundingClientRect().top),
        views: Math.round(document.getElementById("views").getBoundingClientRect().bottom),
      };
      window.kyo.find();
      out.searching = getComputedStyle(document.getElementById("top")).display;
      return out;
    })()`);
    assert.deepEqual([seen.menu, seen.row, seen.pageTop, seen.searching], ["none", "none", seen.views, "flex"]);
  } finally {
    await page.close();
  }
});

test("both drawers start under the bar of navigation icons, out or docked, and never cover it", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  await page.reload();
  const tops = await page.run<{ bar: number; left: number; right: number }>(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    [...document.querySelectorAll(".view-item")].find((item) => item.textContent === "Records").click();
    return {
      bar: document.getElementById("top").getBoundingClientRect().bottom,
      left: parseFloat(getComputedStyle(document.getElementById("menu-panel")).top),
      right: parseFloat(getComputedStyle(document.getElementById("outline-panel")).top),
    };
  })()`);
  assert.deepEqual([tops.left, tops.right], [tops.bar, tops.bar]);
  await page.close();
});
