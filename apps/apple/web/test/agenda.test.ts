// Run with `npm test`. The session's task list as the composer's bubble shows it (KYO-771),
// and the list each change by the person saves.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  type AgendaItem, MAX_TEXT, agendaView, itemText, listAfter, refusalText,
} from "../src/agenda.ts";

const items: AgendaItem[] = [
  { id: "1", text: "push before close", author: "person", checked: false },
  { id: "2", text: "dispatch wave 3", author: "orchestrator", checked: false },
  { id: "3", text: "merge wave 2", author: "orchestrator", checked: true },
];
const sent = [
  { id: "1", text: "push before close", checked: false },
  { id: "2", text: "dispatch wave 3", checked: false },
  { id: "3", text: "merge wave 2", checked: true },
];

test("no bubble before a frame arrives, or with no live session", () => {
  assert.equal(agendaView(null).shown, false);
  assert.equal(agendaView({ state: "absent" }).shown, false);
});

test("a list that cannot be read says why, and offers nothing to press", () => {
  const view = agendaView({ state: "unavailable", why: "This device's token lacks the send permission." });
  assert.deepEqual(view, {
    shown: true, why: "This device's token lacks the send permission.", rows: [], canAdd: false,
  });
});

test("each item shows its text, its check and whose it is", () => {
  const view = agendaView({ state: "shown", session_id: "s", items, etag: '"e"' });
  assert.equal(view.shown, true);
  assert.equal(view.why, null);
  assert.equal(view.canAdd, true);
  assert.deepEqual(view.rows, [
    { id: "1", text: "push before close", checked: false, mine: true, whose: "you" },
    { id: "2", text: "dispatch wave 3", checked: false, mine: false, whose: "orchestrator" },
    { id: "3", text: "merge wave 2", checked: true, mine: false, whose: "orchestrator" },
  ]);
});

test("an empty list is still a bubble the person can add to", () => {
  const view = agendaView({ state: "shown", session_id: "s", items: [], etag: null });
  assert.deepEqual(view, { shown: true, why: null, rows: [], canAdd: true });
});

test("the person adds an item at the end, with no id and no author", () => {
  assert.deepEqual(listAfter(items, { kind: "add", text: "  email\n the notes " }), [
    ...sent, { text: "email the notes" },
  ]);
  assert.equal(listAfter(items, { kind: "add", text: "  \n" }), null);
});

test("the person edits, checks, unchecks and removes their own item", () => {
  assert.deepEqual(listAfter(items, { kind: "edit", id: "1", text: "push, then close" })?.[0], {
    id: "1", text: "push, then close", checked: false,
  });
  assert.deepEqual(listAfter(items, { kind: "check", id: "1", checked: true })?.[0], {
    id: "1", text: "push before close", checked: true,
  });
  const checked = items.map((item) => (item.id === "1" ? { ...item, checked: true } : item));
  assert.equal(listAfter(checked, { kind: "check", id: "1", checked: false })?.[0].checked, false);
  assert.deepEqual(listAfter(items, { kind: "remove", id: "1" }), sent.slice(1));
});

test("every other item is sent as it was read", () => {
  const after = listAfter(items, { kind: "check", id: "1", checked: true })!;
  assert.deepEqual(after.slice(1), sent.slice(1));
});

test("a change that changes nothing saves nothing", () => {
  assert.equal(listAfter(items, { kind: "edit", id: "1", text: "push before close" }), null);
  assert.equal(listAfter(items, { kind: "edit", id: "1", text: " " }), null);
  assert.equal(listAfter(items, { kind: "check", id: "1", checked: false }), null);
  assert.equal(listAfter(items, { kind: "remove", id: "9" }), null);
});

test("the orchestrator's items take no change from the bubble", () => {
  assert.equal(listAfter(items, { kind: "edit", id: "2", text: "reworded" }), null);
  assert.equal(listAfter(items, { kind: "check", id: "2", checked: true }), null);
  assert.equal(listAfter(items, { kind: "check", id: "3", checked: false }), null);
  assert.equal(listAfter(items, { kind: "remove", id: "2" }), null);
});

test("an item's text is one line of at most the server's length", () => {
  assert.equal(itemText("a\n b\t c"), "a b c");
  assert.equal(itemText("x".repeat(MAX_TEXT + 40))?.length, MAX_TEXT);
  assert.equal(itemText(""), null);
});

test("a stale save says the list changed and that the change was not made", () => {
  const text = refusalText({ status: 412, error: "the list of session s changed since that etag was read" });
  assert.match(text, /changed before this was saved/);
  assert.match(text, /your change was not made/);
});

test("any other refusal is the server's sentence, or the status when it sent none", () => {
  assert.equal(
    refusalText({ status: 403, error: "item 2 is the orchestrator's" }), "item 2 is the orchestrator's",
  );
  assert.equal(refusalText({ status: 500 }), "The list was not saved (HTTP 500).");
  assert.match(refusalText({ status: 0 }), /not reached/);
});
