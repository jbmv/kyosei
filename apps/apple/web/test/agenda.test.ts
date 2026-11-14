// Run with `npm test`. The session's task list as the composer's bubble shows it (KYO-771),
// and the list each change by the person saves.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  type AgendaItem, MAX_TEXT, agendaPeek, agendaView, itemText, listAfter, refusalText,
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
    shown: true, why: "This device's token lacks the send permission.", rows: [], dividerAt: null,
    completed: 0, showCompleted: false, canAdd: false,
  });
});

test("each item shows its text, its check and whose it is", () => {
  const view = agendaView({ state: "shown", session_id: "s", items, etag: '"e"' });
  assert.equal(view.shown, true);
  assert.equal(view.why, null);
  assert.equal(view.canAdd, true);
  assert.deepEqual(view.rows, [
    { id: "1", text: "push before close", checked: false, mine: true, whose: "you" },
    { id: "2", text: "dispatch wave 3", checked: false, mine: false, whose: "Claude" },
  ]);
  assert.equal(view.completed, 1);
  const all = agendaView({ state: "shown", session_id: "s", items, etag: '"e"' }, true);
  assert.deepEqual(all.rows[2], { id: "3", text: "merge wave 2", checked: true, mine: false, whose: "Claude" });
});

test("an empty list is still a bubble the person can add to", () => {
  const view = agendaView({ state: "shown", session_id: "s", items: [], etag: null });
  assert.deepEqual(view, {
    shown: true, why: null, rows: [], dividerAt: null, completed: 0, showCompleted: false, canAdd: true,
  });
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

const mixed: AgendaItem[] = [
  { id: "o1", text: "accept 1", author: "orchestrator", checked: false },
  { id: "p1", text: "mine 1", author: "person", checked: false },
  { id: "o2", text: "accept 2", author: "orchestrator", checked: true },
  { id: "p2", text: "mine 2", author: "person", checked: true },
  { id: "o3", text: "accept 3", author: "orchestrator", checked: false },
  { id: "p3", text: "mine 3", author: "person", checked: false },
];
const frameOf = (list: AgendaItem[]) => ({ state: "shown", session_id: "s", items: list, etag: null }) as const;

test("the person's unchecked items come first, then the divider, then the orchestrator's", () => {
  const view = agendaView(frameOf(mixed));
  assert.deepEqual(view.rows.map((row) => row.id), ["p1", "p3", "o1", "o3"]);
  assert.equal(view.dividerAt, 2);
  assert.equal(view.completed, 2);
});

test("completed items follow their own group when shown, and the groups stay", () => {
  const view = agendaView(frameOf(mixed), true);
  assert.deepEqual(view.rows.map((row) => row.id), ["p1", "p3", "p2", "o1", "o3", "o2"]);
  assert.equal(view.dividerAt, 3);
  assert.equal(view.showCompleted, true);
});

test("the divider stands before the orchestrator's rows, and only when there are some", () => {
  assert.equal(agendaView(frameOf(mixed.filter((item) => item.author === "person"))).dividerAt, null);
  // With only the orchestrator's rows the divider still stands, below the person's add field.
  assert.equal(agendaView(frameOf(mixed.filter((item) => item.author === "orchestrator"))).dividerAt, 0);
  const onlyDone = mixed.map((item) => ({ ...item, checked: item.author === "person" }));
  assert.equal(agendaView(frameOf(onlyDone)).dividerAt, 0);
  assert.equal(agendaView(frameOf(onlyDone), true).dividerAt, 3);
});

const make = (author: AgendaItem["author"], count: number, from = 1): AgendaItem[] =>
  Array.from({ length: count }, (_, at) => ({ id: `${author[0]}${from + at}`, text: `${author} ${from + at}`, author, checked: false }));
const peek = (list: AgendaItem[]) => {
  const { items: drawn, line } = agendaPeek({ state: "shown", session_id: "s", etag: null, items: list });
  return { drawn: drawn.map((item) => item.id), line };
};

test("the collapsed list draws the first three of the person's items and names the rest (KYO-931.c1)", () => {
  assert.deepEqual(peek([...make("person", 5), ...make("orchestrator", 2)]), {
    drawn: ["p1", "p2", "p3"], line: "2 more of yours, 2 of Claude's…",
  });
  assert.deepEqual(peek([...make("person", 2), ...make("orchestrator", 1)]), { drawn: ["p1", "p2"], line: "1 of Claude's…" });
  assert.deepEqual(peek(make("person", 3)), { drawn: ["p1", "p2", "p3"], line: "" });
  assert.deepEqual(peek(make("orchestrator", 2)), { drawn: [], line: "2 of Claude's…" });
  assert.deepEqual(peek([]), { drawn: [], line: "No reminders…" });
});

test("the collapsed list skips checked items and keeps the list's order (KYO-931.c1)", () => {
  const list: AgendaItem[] = [
    { id: "a", text: "a", author: "person", checked: true },
    ...make("person", 4, 1),
    { id: "o", text: "o", author: "orchestrator", checked: true },
  ];
  assert.deepEqual(peek(list), { drawn: ["p1", "p2", "p3"], line: "1 more of yours…" });
  assert.deepEqual(peek([list[0], list[5]]), { drawn: [], line: "Every reminder is done…" });
});
