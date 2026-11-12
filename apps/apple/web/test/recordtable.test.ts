// Run with `npm test`. The Records home as a table (KYO-862): which records a Location
// holds, how a column sorts them, and which stored filters the drawer reads (KYO-861).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  LOCATIONS, NEWEST, briefPage, briefRows, dayOf, located, notePage, noteRows, parseLink, sessionAt, locationFrom, membersOf, narrow, openPlans, pressedSort, recordSortFrom, sortRecords,
  storedFilters,
} from "../src/records.ts";

const rows = [
  { id: "K-1", kind: "task", title: "banana", status: "open", seq: 1, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-03-01T00:00:00Z" },
  { id: "K-2", kind: "bug", title: "Apple", status: "open", seq: 2, created_at: "2026-01-02T00:00:00Z", updated_at: "2026-01-02T00:00:00Z" },
  { id: "K-3", kind: "plan", title: "cherry", status: "built", seq: 3, created_at: "2026-01-03T00:00:00Z", updated_at: "2026-02-01T00:00:00Z" },
  { id: "K-4", kind: "task", title: "date", status: "done", seq: 4, created_at: "2026-01-04T00:00:00Z" },
  { id: "K-5", kind: "decision", title: "elder", status: "current", seq: 5, created_at: "2026-01-05T00:00:00Z", updated_at: "2026-01-06T00:00:00Z" },
];
const ids = (list: { id: string }[]) => list.map((row) => row.id);

test("each Location holds its records in the order given, and All holds every one", () => {
  const found = located(rows, new Set(["K-2", "K-1"]), new Set(["K-1"]));
  assert.deepEqual(Object.keys(found), [...LOCATIONS]);
  assert.deepEqual(ids(found["Awaiting you"]), ["K-1"]);
  assert.deepEqual(ids(found.Dispatchable), ["K-2"]);
  assert.deepEqual(ids(found["Other open"]), ["K-3"]);
  // K-5 has no standing in this list, so its status places it: a decision stored `current`.
  assert.deepEqual(ids(found.Closed), ["K-4"]);
  assert.deepEqual(ids(found.Reference), ["K-5"]);
  assert.deepEqual(ids(found.All), ids(rows));
});

test("a record is placed, filtered and sorted by its standing where the list gives one", () => {
  const decision = { id: "K-9", kind: "decision", title: "z", status: "open", standing: "current", seq: 9 };
  const all = [...rows, decision];
  assert.deepEqual(ids(located(all, new Set(), new Set()).Closed), ["K-4"]);
  assert.deepEqual(ids(located(all, new Set(), new Set()).Reference), ["K-5", "K-9"]);
  const openOnly = { open: true, dispatchable: false, awaiting: false, search: "" };
  assert.deepEqual(ids(narrow(all, openOnly, new Set())), ["K-1", "K-2"]);
  assert.equal(ids(sortRecords(all, { by: "status", descending: true }))[0], "K-9");
});

test("the Plans are the plans still open, and a plan's members are the records that name it", () => {
  const all = [
    { id: "K-1", kind: "plan", title: "p", status: "open", standing: "open" },
    { id: "K-2", kind: "plan", title: "q", status: "done", standing: "done" },
    { id: "K-3", kind: "task", title: "a", status: "done", plan: "K-1" },
    { id: "K-4", kind: "task", title: "b", status: "open", plan: null },
    { id: "K-5", kind: "bug", title: "c", status: "open", plan: "K-1" },
  ];
  assert.deepEqual(ids(openPlans(all)), ["K-1"]);
  assert.deepEqual(ids(membersOf(all, "K-1")), ["K-3", "K-5"]);
  assert.deepEqual(ids(membersOf(all, "K-2")), []);
});

test("a stored Location that names none is All", () => {
  assert.equal(locationFrom("Closed"), "Closed");
  assert.equal(locationFrom("Elsewhere"), "All");
  assert.equal(locationFrom(undefined), "All");
});

test("a column sorts the records, and a second press of it reverses them", () => {
  // Name is the record's number, the order of creation, and starts newest first.
  assert.deepEqual(NEWEST, { by: "name", descending: true });
  assert.deepEqual(ids(sortRecords(rows, NEWEST)), ["K-5", "K-4", "K-3", "K-2", "K-1"]);
  const byName = pressedSort(NEWEST, "name");
  assert.deepEqual(byName, { by: "name", descending: false });
  assert.deepEqual(ids(sortRecords(rows, byName)), ["K-1", "K-2", "K-3", "K-4", "K-5"]);
  assert.deepEqual(pressedSort({ by: "kind", descending: false }, "name"), NEWEST);
  // Records of one kind stand newest first within it.
  assert.deepEqual(ids(sortRecords(rows, { by: "kind", descending: false })), ["K-2", "K-5", "K-3", "K-4", "K-1"]);
  // Status runs from open through the work states to the knowledge states.
  assert.deepEqual(ids(sortRecords(rows, { by: "status", descending: false })), ["K-2", "K-1", "K-3", "K-4", "K-5"]);
  // A date column starts newest first; a record never changed counts by its creation.
  assert.deepEqual(pressedSort(byName, "modified"), { by: "modified", descending: true });
  assert.deepEqual(ids(sortRecords(rows, { by: "modified", descending: true })), ["K-1", "K-3", "K-5", "K-4", "K-2"]);
  assert.deepEqual(ids(rows), ["K-1", "K-2", "K-3", "K-4", "K-5"], "the list given is left as it was");
});

test("a stored sort that names no column is newest first", () => {
  assert.deepEqual(recordSortFrom({ by: "status", descending: true }), { by: "status", descending: true });
  assert.deepEqual(recordSortFrom({ by: "size" }), NEWEST);
  assert.deepEqual(recordSortFrom({ by: "created", descending: false }), NEWEST, "a column the table no longer has");
  assert.deepEqual(recordSortFrom(null), NEWEST);
});

test("a stored filter with no control on the page never narrows the search (KYO-861)", () => {
  const kept = storedFilters({ open: true, dispatchable: true, awaiting: true });
  assert.deepEqual(kept, { open: true, dispatchable: false, awaiting: false });
  assert.deepEqual(storedFilters({ dispatchable: true }), { open: false, dispatchable: false, awaiting: false });
  assert.deepEqual(storedFilters(null), { open: false, dispatchable: false, awaiting: false });
  // What an earlier build stored, and the search that found nothing: no plan is dispatchable.
  const search = { ...storedFilters({ dispatchable: true }), search: "kind:plan" };
  assert.deepEqual(ids(narrow(rows, search, new Set(["K-2"]), new Set())), ["K-3"]);
});

test("a day is stated without its year in the present year, and not at all when unknown", () => {
  const now = new Date("2026-06-01T12:00:00Z");
  assert.doesNotMatch(dayOf("2026-03-05T12:00:00Z", now), /2026/);
  assert.match(dayOf("2025-03-05T12:00:00Z", now), /2025/);
  assert.equal(dayOf(undefined, now), "");
  assert.equal(dayOf("not a date", now), "");
});

const NOTES = { notes: [
  { id: "K-S2", seq: 2, author: "orchestrator", created_at: "2026-01-03T10:00:00Z", handoff_for: null,
    text: "Second note, first line.\nIts second line." },
  { id: "K-S1", seq: 1, author: "orchestrator", created_at: "2026-01-01T10:00:00Z", handoff_for: "K-B2", text: "First note." },
] };
const BRIEFS = { briefs: [
  { id: "K-B1", seq: 1, at: "2026-01-01T09:00:00Z", session_id: "aaaaaaaa-1111", session_start: true, build_commit: "0123456789ab" },
  { id: "K-B2", seq: 2, at: "2026-01-02T09:00:00Z", session_id: "bbbbbbbb-2222", session_start: true, build_commit: null },
  { id: "K-B3", seq: 3, at: "2026-01-03T09:30:00Z", session_id: "bbbbbbbb-2222", session_start: false, build_commit: null },
] };

test("shift notes and briefs are rows of their own Locations, newest first, and no part of All (KYO-869)", () => {
  const notes = noteRows(NOTES);
  assert.deepEqual(notes.map((row) => [row.id, row.kind, row.title, row.standing]), [
    ["K-S2", "shift note", "Second note, first line.", "note"],
    ["K-S1", "shift note", "First note.", "handoff"],
  ]);
  const briefs = briefRows(BRIEFS);
  assert.deepEqual(briefs.map((row) => [row.id, row.title, row.standing]), [
    ["K-B3", "Brief · session bbbbbbbb", "brief"],
    ["K-B2", "Session start · session bbbbbbbb", "session start"],
    ["K-B1", "Session start · session aaaaaaaa · build 01234567", "session start"],
  ]);
  const found = located(rows, new Set(), new Set(), { notes, briefs });
  assert.deepEqual(ids(found["Shift notes"]), ["K-S2", "K-S1"]);
  assert.deepEqual(ids(found.Briefs), ["K-B3", "K-B2", "K-B1"]);
  assert.deepEqual(ids(found.All), ids(rows));
  assert.deepEqual(noteRows(null), []);
  assert.deepEqual(briefRows({}), []);
});

test("a note's session is the one whose opening brief came last before it", () => {
  assert.equal(sessionAt(BRIEFS, "2026-01-03T10:00:00Z"), "bbbbbbbb-2222");
  assert.equal(sessionAt(BRIEFS, "2026-01-01T10:00:00Z"), "aaaaaaaa-1111");
  assert.equal(sessionAt(BRIEFS, "2025-12-31T00:00:00Z"), null);
  assert.equal(sessionAt(null, "2026-01-03T10:00:00Z"), null);
});

test("a shift note and a brief are drawn as pages, and a link to either names it", () => {
  const note = notePage({ object: "shift-note", ...NOTES.notes[1] }, "K-B2");
  assert.equal(note.id, "K-S1");
  assert.equal(note.kind, "shift note");
  assert.equal(note.description, "First note.");
  assert.deepEqual(note.facts.map((fact) => fact.label), ["author", "written", "handoff for"]);
  assert.equal(note.facts[2].text, "K-B2");
  const brief = briefPage({
    run: BRIEFS.briefs[1], previous_brief_id: "K-B1",
    shift_notes: [{ id: "K-S1", author: "orchestrator", created_at: "2026-01-01T10:00:00Z", text: "First note." }],
    events: [{ id: "K-E5", actor: "orchestrator", at: "2026-01-01T10:00:00Z", command: "note --shift", record_id: null }],
  });
  assert.equal(brief.id, "K-B2");
  assert.equal(brief.status, "session start");
  assert.deepEqual(brief.sections.map((section) => [section.name, section.items.length]), [["Shift notes", 1], ["Events", 1]]);
  assert.equal(brief.sections[1].items[0].text, "note --shift");
  assert.equal(parseLink("kyosei://KYO/KYO-S392")?.record, "KYO-S392");
  assert.equal(parseLink("kyosei://KYO/KYO-B180")?.record, "KYO-B180");
});
