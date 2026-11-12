// Run with `npm test`. The Records home as a table (KYO-862): which records a Location
// holds, how a column sorts them, and which stored filters the drawer reads (KYO-861).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  LOCATIONS, NEWEST, dayOf, located, locationFrom, narrow, pressedSort, recordSortFrom, sortRecords,
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
  assert.deepEqual(ids(found.Closed), ["K-4", "K-5"]);
  assert.deepEqual(ids(found.All), ids(rows));
});

test("a record is placed, filtered and sorted by its standing where the list gives one", () => {
  const decision = { id: "K-9", kind: "decision", title: "z", status: "open", standing: "current", seq: 9 };
  const all = [...rows, decision];
  assert.deepEqual(ids(located(all, new Set(), new Set()).Closed), ["K-4", "K-5", "K-9"]);
  const openOnly = { open: true, dispatchable: false, awaiting: false, search: "" };
  assert.deepEqual(ids(narrow(all, openOnly, new Set())), ["K-1", "K-2"]);
  assert.equal(ids(sortRecords(all, { by: "status", descending: true }))[0], "K-9");
});

test("a stored Location that names none is All", () => {
  assert.equal(locationFrom("Closed"), "Closed");
  assert.equal(locationFrom("Elsewhere"), "All");
  assert.equal(locationFrom(undefined), "All");
});

test("a column sorts the records, and a second press of it reverses them", () => {
  assert.deepEqual(ids(sortRecords(rows, NEWEST)), ["K-5", "K-4", "K-3", "K-2", "K-1"]);
  const byName = pressedSort(NEWEST, "name");
  assert.deepEqual(byName, { by: "name", descending: false });
  assert.deepEqual(ids(sortRecords(rows, byName)), ["K-2", "K-1", "K-3", "K-4", "K-5"], "case is ignored");
  assert.deepEqual(ids(sortRecords(rows, pressedSort(byName, "name"))), ["K-5", "K-4", "K-3", "K-1", "K-2"]);
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
