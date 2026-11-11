// Run with `npm test`. The History view's logic (KYO-825): what a search asks, how the
// answer is read, and which chat element a result opens.
import assert from "node:assert/strict";
import { test } from "node:test";
import { HISTORY_CONTEXT, chatKey, foundOf, openedEntries, plainWords, searchQuery } from "../src/searchhistory.ts";

test("a search asks for the text with a longer excerpt, and a role only when one is chosen", () => {
  const both = new Set(["person", "assistant"] as const);
  assert.deepEqual(searchQuery(" anchor mode ", both, null), { q: "anchor mode", context: HISTORY_CONTEXT });
  assert.deepEqual(searchQuery("anchor", new Set(["person"] as const), "c1"),
    { q: "anchor", role: "person", cursor: "c1", context: HISTORY_CONTEXT });
  assert.equal(searchQuery("a", both, null), null, "one letter is not asked about");
  assert.equal(searchQuery("anchor", new Set(), null), null, "with neither chip there is nothing to find");
});

// What `GET .../search?q=anchor` answered (KYO-812), a prompt and a reply.
const answer = {
  ok: true, more: true, cursor: "next",
  results: [
    { project: "p", session_id: "S1", message: "u-1", message_id: null, role: "person",
      at: "2026-11-11T20:00:00Z", matches: 1,
      excerpt: [{ text: "the ", match: false }, { text: "anchor", match: true }, { text: " mode", match: false }] },
    { project: "p", session_id: "S2", message: "x-9", message_id: "msg_1", role: "assistant",
      at: "2026-11-11T19:00:00Z", matches: 2, excerpt: [{ text: "Anchor", match: true }] },
  ],
};

test("an answer is read into results and the next page's cursor", () => {
  const read = foundOf(answer)!;
  assert.equal(read.cursor, "next");
  assert.deepEqual(read.found.map((found) => [found.session, found.role, chatKey(found)]), [
    ["S1", "person", "t:u-1"], ["S2", "assistant", "r:msg_1"],
  ]);
  assert.equal(plainWords(read.found[0].excerpt), "the anchor mode");
  assert.equal(foundOf({ ...answer, more: false })!.cursor, null);
  assert.equal(foundOf({ error: "refused" }), null);
});

test("the opened results are read from the route's answer", () => {
  assert.deepEqual(openedEntries({ entries: [
    { session: "S1", message: "u-1", words: "the anchor mode", opened_at: "2026-11-11T20:01:00Z" },
    { session: 3 },
  ] }), [{ session: "S1", message: "u-1", words: "the anchor mode", opened_at: "2026-11-11T20:01:00Z" }]);
  assert.equal(openedEntries({}), null);
});
