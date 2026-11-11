// Run with `npm test`. A chip draws its quoted words as inline markdown (KYO-758).
import assert from "node:assert/strict";
import { test } from "node:test";
import { chipPieces } from "../src/chipwords.ts";

const stars = (pieces: { text: string }[]) => pieces.map((piece) => piece.text).join("");

test("bold, italic and code are drawn as such, with no marks left", () => {
  assert.deepEqual(chipPieces("a **b** *c* `d`"), [
    { text: "a " }, { text: "b", bold: true }, { text: " " }, { text: "c", italic: true },
    { text: " " }, { text: "d", code: true },
  ]);
});

test("a link is its text without its target", () => {
  const pieces = chipPieces("**Composer and replies:** [KYO-723](kyosei://KYO/KYO-723) (code block…");
  assert.equal(stars(pieces), "Composer and replies: KYO-723 (code block…");
  assert.equal(pieces[0].bold, true);
  assert.ok(!pieces[1].bold);
});

test("a quote cut inside a bold run is bold to its end, without stray stars", () => {
  const pieces = chipPieces("**Composer and re…");
  assert.deepEqual(pieces, [{ text: "Composer and re…", bold: true }]);
});

test("a link cut before it ends is its text alone", () => {
  assert.equal(stars(chipPieces("see [KYO-723](kyosei://KYO/KY")), "see KYO-723");
});

test("snake_case, a lone star and escapes stay literal", () => {
  assert.equal(stars(chipPieces("snake_case_name 2 * 3 \\*x")), "snake_case_name 2 * 3 *x");
});

test("plain words are one piece", () => {
  assert.deepEqual(chipPieces("Gates"), [{ text: "Gates" }]);
});
