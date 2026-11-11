// Run with `npm test`. The project switcher's rows, its session links, and the composer's
// prompts kept for each project (KYO-761).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  type Keep, type ProjectsFrame, LEGACY_KEY, anchorGoneNote, anchorOpens, choices, keepBoxes,
  keptBoxes, keptKey, listNote, meetDraft, metKey, orchestrator, sessionLinks, typedChoice,
} from "../src/projects.ts";

const frame = (over: Partial<ProjectsFrame> = {}): ProjectsFrame => ({
  project: "alpha",
  projects: [
    { name: "alpha", can_open: true, problem: null },
    { name: "beta", can_open: false, problem: "the store is missing" },
    { name: "gamma", can_open: true, problem: null },
    { name: "delta", can_open: true, problem: null },
  ],
  problem: null,
  live: {},
  ...over,
});

/** A `localStorage` of one page, which a second page of the same app reads again. */
function memory(held = new Map<string, string>()): Keep & { held: Map<string, string> } {
  return {
    held,
    getItem: (key) => held.get(key) ?? null,
    setItem: (key, value) => void held.set(key, value),
    removeItem: (key) => void held.delete(key),
  };
}

test("the dropdown lists every served project by name, in the server's order", () => {
  assert.deepEqual(choices(frame()).map((row) => row.name), ["alpha", "beta", "gamma", "delta"]);
});

test("the window's project is the one marked", () => {
  assert.deepEqual(choices(frame({ project: "gamma" })).filter((row) => row.current).map((row) => row.name), ["gamma"]);
  assert.equal(choices(frame({ project: "" })).some((row) => row.current), false);
});

test("a project that cannot be opened is listed with its problem and takes no press", () => {
  const beta = choices(frame()).find((row) => row.name === "beta")!;
  assert.equal(beta.openable, false);
  assert.equal(beta.problem, "the store is missing");
  const unexplained = choices(frame({ projects: [{ name: "x", can_open: false, problem: null }] }))[0];
  assert.equal(unexplained.openable, false);
  assert.match(unexplained.problem!, /no reason/);
});

test("before the list is read there are no rows, and the note says why", () => {
  assert.deepEqual(choices(null), []);
  assert.deepEqual(choices(frame({ projects: null })), []);
  assert.equal(listNote(null), "Reading the server's projects.");
  assert.match(listNote(frame({ projects: null, problem: "kyo serve cannot be reached." })), /could not be read: kyo serve cannot be reached\./);
});

test("a list that was read keeps its rows when a later read fails, and says so", () => {
  const failed = frame({ problem: "kyo serve cannot be reached." });
  assert.equal(choices(failed).length, 4);
  assert.match(listNote(failed), /could not be read again/);
  assert.equal(listNote(frame()), "");
  assert.match(listNote(frame({ projects: [] })), /lists no project/);
});

test("a project's link follows its first live session that has a pane", () => {
  assert.equal(orchestrator(undefined), null);
  assert.equal(orchestrator([{ session_id: "a", title: null, has_pane: false }]), null);
  assert.equal(
    orchestrator([
      { session_id: "a", title: null, has_pane: false },
      { session_id: "b", title: "B", has_pane: true },
      { session_id: "c", title: "C", has_pane: true },
    ])?.session_id,
    "b",
  );
});

test("the bar links each project with a live orchestrator, and marks the window's own", () => {
  const links = sessionLinks(frame({
    project: "gamma",
    live: {
      alpha: [{ session_id: "a1", title: "Alpha work", has_pane: true }],
      beta: [{ session_id: "b1", title: null, has_pane: true }],
      gamma: [{ session_id: "g1", title: null, has_pane: true }],
      delta: [{ session_id: "d1", title: null, has_pane: false }],
      unlisted: [{ session_id: "u1", title: null, has_pane: true }],
    },
  }));
  assert.deepEqual(links, [
    { project: "alpha", session: "a1", title: "Alpha work", current: false },
    { project: "gamma", session: "g1", title: null, current: true },
  ]);
});

test("no project is linked before the list is read or when none is live", () => {
  assert.deepEqual(sessionLinks(null), []);
  assert.deepEqual(sessionLinks(frame()), []);
  assert.deepEqual(sessionLinks(frame({ projects: null, live: { alpha: [{ session_id: "a", title: null, has_pane: true }] } })), []);
});

test("a typed letter moves to the next openable project that begins with it", () => {
  const rows = choices(frame({
    projects: [
      { name: "alpha", can_open: true, problem: null },
      { name: "Apex", can_open: true, problem: null },
      { name: "awry", can_open: false, problem: "broken" },
      { name: "beta", can_open: true, problem: null },
    ],
  }));
  assert.equal(typedChoice(rows, "a", null), "alpha");
  assert.equal(typedChoice(rows, "a", "alpha"), "Apex");
  assert.equal(typedChoice(rows, "a", "Apex"), "alpha");
  assert.equal(typedChoice(rows, "b", "alpha"), "beta");
  assert.equal(typedChoice(rows, "z", "alpha"), null);
});

test("a draft survives a switch away and back, and a restart of the app", () => {
  const keep = memory();
  const alpha = "http://server alpha";
  const beta = "http://server beta";
  keepBoxes(keep, alpha, [{ text: "for alpha" }]);
  // The window turns to beta, which has prompts of its own, and back.
  assert.deepEqual(keptBoxes(keep, beta, false), []);
  keepBoxes(keep, beta, [{ text: "for beta" }, { text: "and another" }]);
  assert.deepEqual(keptBoxes(keep, alpha, false), [{ text: "for alpha" }]);
  assert.deepEqual(keptBoxes(keep, beta, false), [{ text: "for beta" }, { text: "and another" }]);
  // A page loaded afresh reads the same storage.
  const relaunched = memory(new Map(keep.held));
  assert.deepEqual(keptBoxes(relaunched, alpha, true), [{ text: "for alpha" }]);
  assert.deepEqual(keptBoxes(relaunched, beta, false), [{ text: "for beta" }, { text: "and another" }]);
});

test("two servers' projects of one name do not share their prompts", () => {
  const keep = memory();
  keepBoxes(keep, "http://one alpha", [{ text: "one" }]);
  assert.deepEqual(keptBoxes(keep, "http://two alpha", false), []);
  assert.notEqual(keptKey("http://one alpha"), keptKey("http://two alpha"));
});

test("a project left with no unsent prompt keeps none", () => {
  const keep = memory();
  keepBoxes(keep, "s alpha", [{ text: "x" }]);
  keepBoxes(keep, "s alpha", []);
  assert.deepEqual(keptBoxes(keep, "s alpha", false), []);
  assert.equal(keep.held.size, 0);
});

test("prompts kept before the switcher go to the first project shown, once", () => {
  const keep = memory(new Map([[LEGACY_KEY, JSON.stringify([{ text: "old" }, "older"])]]));
  // A window with no project adopts nothing.
  assert.deepEqual(keptBoxes(keep, "s ", false), []);
  assert.deepEqual(keptBoxes(keep, "s alpha", true), [{ text: "old" }, "older"]);
  assert.equal(keep.held.has(LEGACY_KEY), false);
  assert.deepEqual(keptBoxes(keep, "s beta", true), []);
  assert.deepEqual(keptBoxes(keep, "s alpha", false), [{ text: "old" }, "older"]);
});

test("a project's own prompts are not replaced by the ones kept before the switcher", () => {
  const keep = memory(new Map([[LEGACY_KEY, JSON.stringify([{ text: "old" }])]]));
  keepBoxes(keep, "s alpha", [{ text: "own" }]);
  assert.deepEqual(keptBoxes(keep, "s alpha", true), [{ text: "own" }]);
});

test("storage that cannot be read or written keeps nothing and throws nothing", () => {
  const broken: Keep = {
    getItem: () => { throw new Error("denied"); },
    setItem: () => { throw new Error("denied"); },
    removeItem: () => { throw new Error("denied"); },
  };
  keepBoxes(broken, "s alpha", [{ text: "x" }]);
  assert.deepEqual(keptBoxes(broken, "s alpha", true), []);
  const garbled = memory(new Map([[keptKey("s alpha"), "{not json"]]));
  assert.deepEqual(keptBoxes(garbled, "s alpha", false), []);
});

// ------------------------------------------- KYO-763: a prompt carried between sessions

const box = (text: string) => ({ text });

test("the server's draft replaces the prompts the window kept", () => {
  const keep = memory(new Map([[metKey("s alpha"), "1"]]));
  const met = meetDraft(keep, "s alpha", [box("from the server")], [box("kept here")]);
  assert.deepEqual(met, { boxes: [box("from the server")], merged: false });
});

test("the first draft a project is given keeps the prompts only this device held, once", () => {
  const keep = memory();
  const server = [box("from the server"), box("in both")];
  const local = [box("in both"), box("written before the upgrade")];
  const first = meetDraft(keep, "s alpha", server, local);
  assert.deepEqual(first.boxes.map((b) => b.text), ["from the server", "in both", "written before the upgrade"]);
  assert.equal(first.merged, true);
  // From then on a prompt deleted on another device is not brought back by this one.
  const second = meetDraft(keep, "s alpha", server, local);
  assert.deepEqual(second, { boxes: server, merged: false });
  // Another project has not met its draft yet.
  assert.equal(meetDraft(keep, "s beta", [], [box("beta's")]).merged, true);
});

test("a first draft that holds everything kept here is shown as it is", () => {
  const met = meetDraft(memory(), "s alpha", [box("same")], [box("same")]);
  assert.deepEqual(met, { boxes: [box("same")], merged: false });
});

test("a window whose storage is denied shows the server's draft", () => {
  const denied: Keep = {
    getItem: () => { throw new Error("denied"); },
    setItem: () => { throw new Error("denied"); },
    removeItem: () => { throw new Error("denied"); },
  };
  assert.deepEqual(meetDraft(denied, "s alpha", [box("server")], [box("local")]).boxes, [box("server")]);
});

test("an anchor opens only in the session it was made in", () => {
  assert.equal(anchorOpens("session-a", "session-a"), true);
  assert.equal(anchorOpens("session-a", "session-b"), false);
  // With no session followed there is no chat to go to.
  assert.equal(anchorOpens("session-a", null), false);
  assert.equal(anchorOpens("session-a", undefined), false);
  // An anchor with no session recorded is the followed session's.
  assert.equal(anchorOpens("", "session-b"), true);
});

test("an anchor that cannot be opened says whether its session has ended", () => {
  assert.match(anchorGoneNote(false), /session that has ended.*sent with its words.*no longer be opened/);
  assert.match(anchorGoneNote(true), /another session.*sent with its words/);
});
