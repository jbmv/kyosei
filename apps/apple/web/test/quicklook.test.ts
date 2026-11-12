// Run with `npm test`. KYO-883: Quick Look in the image menu. The app adds the item to WebKit's
// own menu and calls `window.kyo.quickLook()` when it is chosen; the page hands the app the
// image the right-click was on: its data when the page holds it whole, or a composer chip's
// attachment id, since the chip holds only a thumbnail. The panel itself is the app's, built
// in ChatView.swift, and is not seen here. A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const PIXEL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

const rightClick = (selector: string) => `(() => {
  const target = document.querySelector(${JSON.stringify(selector)});
  const shown = target.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 40, clientY: 40 }));
  window.posted.length = 0;
  window.kyo.quickLook();
  return { shown, posted: window.posted.filter((p) => p.name === "quicklook").map((p) => p.body) };
})()`;

test("an image in the chat goes to Quick Look as its data, and keeps WebKit's own menu", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  const found = await page.run<{ shown: boolean; posted: unknown[] }>(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null } ] } });
    window.kyo.apply({ turns: [{ id: "t1", at: "2026-01-01T00:00:01Z", text: "look", images: [{ index: 0 }] }] });
    window.kyo.image({ key: "t1/0", data: ${JSON.stringify(PIXEL)} });
    return ${rightClick("#chat .turn-image")};
  })()`);
  assert.equal(found.shown, true, "the page does not take the image's menu");
  assert.deepEqual(found.posted, [{ data: PIXEL, name: "image" }]);
});

test("a composer chip's thumbnail goes to Quick Look as its attachment, and text goes nowhere", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  await page.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.draft({ boxes: [{ id: "a", text: "with a picture", marks: [{ line: 0, items: [
      { kind: "file", id: "abc123", name: "shot.png", thumb: ${JSON.stringify(PIXEL)} } ] }] }], etag: "e" });
  })()`);
  // The thumbnail is in the paragraph's panel, which opens from its mark.
  await page.run(`document.querySelector("#boxes .mark.held").click()`);
  const chip = await page.run<{ shown: boolean; posted: unknown[] }>(rightClick("img.mark-thumb"));
  assert.deepEqual(chip.posted, [{ attachment: "abc123", name: "shot.png", data: PIXEL }]);
  const text = await page.run<{ shown: boolean; posted: unknown[] }>(rightClick("#boxes textarea"));
  assert.deepEqual(text.posted, []);
});
