// Run with `npm test`. The terminal drawer's arithmetic (KYO-721).
import assert from "node:assert/strict";
import { test } from "node:test";
import { bytesOf, fontToFit, lineBreakKeys, viewSize } from "../src/terminal.ts";

test("a view states the whole cells it holds", () => {
  assert.deepEqual(viewSize(724, 300, 7.2, 15), { cols: 100, rows: 20 });
  assert.deepEqual(viewSize(100000, 100000, 7.2, 15), { cols: 500, rows: 300 });
});

test("a view not yet measured, or too small for the server to accept, states nothing", () => {
  assert.equal(viewSize(0, 300, 7.2, 15), null);
  assert.equal(viewSize(724, 300, 0, 15), null);
  assert.equal(viewSize(60, 300, 7.2, 15), null);
  assert.equal(viewSize(724, 30, 7.2, 15), null);
});

test("a pane the view sized is drawn at the base font", () => {
  assert.equal(fontToFit(12, 100, 100), 12);
  assert.equal(fontToFit(12, 100, 80), 12);
  assert.equal(fontToFit(12, 0, 80), 12);
});

test("a wider pane, sized by another terminal, is drawn smaller so its lines fit", () => {
  assert.equal(fontToFit(12, 100, 120), 10);
  assert.equal(fontToFit(12, 100, 150), 8);
  // Never so small that it cannot be read: past that the drawer scrolls.
  assert.equal(fontToFit(12, 100, 400), 7);
});

test("a frame's bytes are what the pane wrote, whatever they are", () => {
  const written = [0x1b, 0x5b, 0x33, 0x31, 0x6d, 0xe2, 0x94, 0x80, 0x00, 0xff];
  const frame = Buffer.from(written).toString("base64");
  assert.deepEqual(Array.from(bytesOf(frame)), written);
});

test("Shift-Return and Option-Return are a line break, and Return alone is the emulator's", () => {
  const key = { key: "Enter", shiftKey: false, altKey: false, metaKey: false, ctrlKey: false };
  assert.equal(lineBreakKeys(key), null);
  assert.equal(lineBreakKeys({ ...key, shiftKey: true }), "\x1b\r");
  assert.equal(lineBreakKeys({ ...key, altKey: true }), "\x1b\r");
  assert.equal(lineBreakKeys({ ...key, shiftKey: true, metaKey: true }), null);
  assert.equal(lineBreakKeys({ ...key, key: "a", shiftKey: true }), null);
});
