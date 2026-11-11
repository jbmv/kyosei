// Run with `npm test`. KYO-798: a draft saved from another device meets the prompts in this
// window, box by box and by each box's id.
import assert from "node:assert/strict";
import { test } from "node:test";
import { type DraftBox, agreedOn, boxId, boxSignature, mergeDraft, mergeNote } from "../src/drafts.ts";

const box = (id: string, text: string, marks?: unknown): DraftBox => ({ id, text, marks });
const texts = (boxes: DraftBox[]) => boxes.map((each) => each.text);
let minted = 0;
const mint = () => `new-${++minted}`;

test("a window with nothing unsaved shows the server's draft as it is", () => {
  const base = [box("a", "one"), box("b", "two")];
  const remote = [box("b", "two, edited"), box("c", "three"), box("a", "one")];
  const merged = mergeDraft(agreedOn(base), base, remote, mint);
  assert.deepEqual(merged.boxes, remote);
  assert.deepEqual([merged.unsaved, merged.save, merged.both, merged.kept], [false, false, 0, 0]);
  assert.equal(mergeNote(merged), "");
});

test("an edit not yet saved is kept beside what was typed elsewhere in other boxes", () => {
  const base = [box("a", "one"), box("b", "two")];
  const local = [box("a", "one, typed here"), box("b", "two")];
  const remote = [box("a", "one"), box("b", "two, typed there"), box("c", "three")];
  const merged = mergeDraft(agreedOn(base), local, remote, mint);
  assert.deepEqual(texts(merged.boxes), ["one, typed here", "two, typed there", "three"]);
  assert.deepEqual(merged.boxes.map((each) => each.id), ["a", "b", "c"]);
  assert.equal(merged.unsaved, true);
  assert.equal(merged.save, true);
  assert.match(mergeNote(merged), /also changed on another device/);
});

test("a box edited on both devices is shown in both versions, the other under a new id", () => {
  const base = [box("a", "one")];
  const merged = mergeDraft(agreedOn(base), [box("a", "one here")], [box("a", "one there")], mint);
  assert.deepEqual(texts(merged.boxes), ["one here", "one there"]);
  assert.equal(merged.boxes[0].id, "a");
  assert.match(merged.boxes[1].id, /^new-/);
  assert.equal(merged.both, 1);
  assert.match(mergeNote(merged), /Both versions are kept/);
});

test("a box sent elsewhere leaves, and a box holding unsaved edits stays", () => {
  const base = [box("a", "sent from the laptop"), box("b", "two")];
  const local = [box("a", "sent from the laptop"), box("b", "two, still being typed")];
  const merged = mergeDraft(agreedOn(base), local, [box("b", "two")], mint);
  assert.deepEqual(merged.boxes, [box("b", "two, still being typed")]);
  assert.equal(merged.kept, 0);
});

test("a box edited here and sent or deleted elsewhere is kept, and the person is told", () => {
  const base = [box("a", "one"), box("b", "two")];
  const merged = mergeDraft(agreedOn(base), [box("a", "one, edited"), box("b", "two")], [box("b", "two")], mint);
  assert.deepEqual(texts(merged.boxes), ["one, edited", "two"]);
  assert.equal(merged.kept, 1);
  assert.match(mergeNote(merged), /sent or deleted on another device/);
});

test("a box deleted here stays deleted, unless it was edited elsewhere meanwhile", () => {
  const base = [box("a", "one"), box("b", "two")];
  const gone = mergeDraft(agreedOn(base), [box("b", "two")], [box("a", "one"), box("b", "two!")], mint);
  assert.deepEqual(texts(gone.boxes), ["two!"]);
  assert.equal(gone.save, true);
  const back = mergeDraft(agreedOn(base), [box("b", "two")], [box("a", "one more"), box("b", "two")], mint);
  assert.deepEqual(texts(back.boxes), ["one more", "two"]);
});

test("a box written here and never saved is kept, and one added elsewhere takes its server place", () => {
  const base = [box("a", "one"), box("b", "two")];
  const local = [box("a", "one"), box("n", "new here"), box("b", "two")];
  const remote = [box("a", "one"), box("x", "after one"), box("b", "two"), box("y", "last")];
  const merged = mergeDraft(agreedOn(base), local, remote, mint);
  assert.deepEqual(texts(merged.boxes), ["one", "after one", "new here", "two", "last"]);
});

test("a box whose marks were rewritten with their keys in another order is not an edit", () => {
  const here = box("a", "one", [{ line: 0, items: [{ kind: "tag", name: "x" }] }]);
  const there = box("a", "one", [{ items: [{ name: "x", kind: "tag" }], line: 0 }]);
  assert.equal(boxSignature(here), boxSignature(there));
  assert.equal(boxSignature(box("b", "t", [])), boxSignature(box("b", "t")));
  assert.notEqual(boxSignature(here), boxSignature(box("a", "one")));
});

test("a new box id is not one already given", () => {
  const ids = new Set(Array.from({ length: 50 }, () => boxId()));
  assert.equal(ids.size, 50);
});

test("a prompt typed elsewhere takes the place of this window's empty box, not a box beside it", () => {
  // The maintainer, 2026-11-11: a prompt typed on the laptop appeared on the Studio above an
  // empty box; with no other prompt it should simply be the one box.
  const local = [box("blank", "")];
  const remote = [box("p", "typed on the laptop")];
  const merged = mergeDraft(agreedOn([], []), local, remote, mint);
  assert.deepEqual(texts(merged.boxes), ["typed on the laptop"]);
  assert.equal(merged.unsaved, false);
  assert.equal(mergeNote(merged), "");
  // With nothing on the server the empty box stays, so there is somewhere to type.
  assert.deepEqual(texts(mergeDraft(agreedOn([], []), local, [], mint).boxes), [""]);
  // A box with words in it is kept beside the incoming prompt, as before.
  const typed = mergeDraft(agreedOn([], []), [box("mine", "half a thought")], remote, mint);
  assert.deepEqual(texts(typed.boxes).sort(), ["half a thought", "typed on the laptop"]);
});
