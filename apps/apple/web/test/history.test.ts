// Run with `npm test`. The history Files and Records share (KYO-738).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  HISTORY_LIMIT, canBack, canForward, current, fresh, stepBack, stepForward, visitPlace, without,
} from "../src/history.ts";

const walk = (...places: (string | null)[]) => places.reduce((h, p) => visitPlace(h, p), fresh<string>());

test("a fresh history is at home with nowhere to go", () => {
  const h = fresh<string>();
  assert.equal(current(h), null);
  assert.equal(canBack(h), false);
  assert.equal(canForward(h), false);
  assert.equal(stepBack(h), h);
  assert.equal(stepForward(h), h);
});

test("back and forward step through places in the order they were shown, home included", () => {
  let h = walk("a", "b");
  assert.equal(current(h), "b");
  h = stepBack(h);
  assert.equal(current(h), "a");
  h = stepBack(h);
  assert.equal(current(h), null);
  assert.equal(canBack(h), false);
  h = stepForward(stepForward(h));
  assert.equal(current(h), "b");
  assert.equal(canForward(h), false);
});

test("showing a place after going back drops what was ahead", () => {
  let h = stepBack(walk("a", "b"));
  h = visitPlace(h, "c");
  assert.deepEqual(h.places, [null, "a", "c"]);
  assert.equal(canForward(h), false);
});

test("home is a place that keeps the history behind it", () => {
  const h = walk("a", null);
  assert.equal(current(h), null);
  assert.deepEqual(h.places, [null, "a", null]);
  assert.equal(current(stepBack(h)), "a");
});

test("showing the place already shown adds nothing", () => {
  const h = walk("a");
  assert.equal(visitPlace(h, "a"), h);
  assert.equal(visitPlace(fresh<string>(), null).places.length, 1);
});

test("a comparer decides which places are the same", () => {
  const same = (x: { id: number }, y: { id: number }) => x.id === y.id;
  const h = visitPlace(fresh<{ id: number }>(), { id: 1 }, same);
  assert.equal(visitPlace(h, { id: 1 }, same), h);
});

test("the oldest places are forgotten past the limit, and home is among them", () => {
  let h = fresh<number>();
  for (let n = 1; n <= HISTORY_LIMIT + 5; n++) h = visitPlace(h, n);
  assert.equal(h.places.length, HISTORY_LIMIT);
  assert.equal(current(h), HISTORY_LIMIT + 5);
  assert.equal(h.at, HISTORY_LIMIT - 1);
});

test("without removes places that are gone and keeps the position on the same place", () => {
  const h = stepBack(walk("a", "x", "b"));
  assert.equal(current(h), "x");
  const cut = without(h, (place) => place === "x");
  assert.deepEqual(cut.places, [null, "a", "b"]);
  assert.equal(current(cut), "a");
  const other = without(walk("a", "x", "b"), (place) => place === "a");
  assert.equal(current(other), "b");
  assert.deepEqual(other.places, [null, "x", "b"]);
});
