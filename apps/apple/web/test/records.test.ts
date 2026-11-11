// Run with `npm test`. The Records view's lists, history, links and record page (KYO-732).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  HISTORY_LIMIT, awaitingIds, dispatchableIds, historyFrom, historyRows,
  latestVerdict, matchesSearch, narrow, newestFirst, parseLink, prefixOf, recordOf, recordPage,
  refParts, visit,
  pressed, reportItem, taskOf,
} from "../src/records.ts";

const rows = [
  { id: "KYO-1", kind: "task", title: "Ideas layer and promotion", status: "done", seq: 1 },
  { id: "KYO-12", kind: "bug", title: "The serve route drops a flag", status: "open", seq: 12 },
  { id: "KYO-7", kind: "plan", title: "Native apps", status: "open", seq: 7 },
  { id: "KYO-30", kind: "task", title: "A top bar of views", status: "open", seq: 30 },
  { id: "KYO-21", kind: "decision", title: "kyo serve starts a session", status: "open", seq: 21 },
  { id: "KYO-25", kind: "task", title: "Serve the record page", status: "wont-do", seq: 25 },
];
const ids = (list: { id: string }[]) => list.map((row) => row.id);
const all = { open: false, dispatchable: false, awaiting: false, search: "" };
const next = { ready: [{ id: "KYO-30" }, { id: "KYO-12" }], blocked: [{ id: "KYO-7" }] };

test("the newest record comes first", () => {
  assert.deepEqual(ids(newestFirst(rows)), ["KYO-30", "KYO-25", "KYO-21", "KYO-12", "KYO-7", "KYO-1"]);
  const dated = [
    { id: "A-1", kind: "task", title: "", status: "open", created_at: "2026-01-01T00:00:00Z" },
    { id: "A-2", kind: "task", title: "", status: "open", created_at: "2026-03-01T00:00:00Z" },
  ];
  assert.deepEqual(ids(newestFirst(dated)), ["A-2", "A-1"]);
});

test("the time a record was filed leads, and its number is no stand-in", () => {
  const mixed = [
    { id: "KYO-9", kind: "task", title: "", status: "open", seq: 9, created_at: "2026-01-01T00:00:00Z" },
    { id: "OPS-2", kind: "bug", title: "", status: "open", seq: 2, created_at: "2026-02-01T00:00:00Z" },
    { id: "KYO-8", kind: "plan", title: "", status: "open", seq: 8, created_at: "2026-02-01T00:00:00Z" },
  ];
  assert.deepEqual(ids(newestFirst(mixed)), ["KYO-8", "OPS-2", "KYO-9"]);
});

test("filters and search narrow the flat list and keep its order", () => {
  const dated = rows.map((row, index) => ({ ...row, created_at: `2026-01-0${index + 1}T00:00:00Z` }));
  const order = ids(newestFirst(dated));
  const shown = ids(narrow(newestFirst(dated), { ...all, open: true }, new Set(), new Set()));
  assert.deepEqual(shown, order.filter((id) => shown.includes(id)));
});

test("with nothing chosen and nothing typed, every record is shown", () => {
  assert.equal(narrow(rows, all, new Set()).length, rows.length);
});

test("Open shows only records whose status is open", () => {
  const shown = narrow(rows, { ...all, open: true }, new Set());
  assert.deepEqual(ids(shown), ["KYO-12", "KYO-7", "KYO-30", "KYO-21"]);
});

test("Dispatchable shows only what kyo next reports ready", () => {
  const ready = dispatchableIds(next);
  assert.deepEqual([...ready], ["KYO-30", "KYO-12"]);
  assert.deepEqual(ids(narrow(rows, { ...all, dispatchable: true }, ready)), ["KYO-12", "KYO-30"]);
  // Before kyo next has answered, nothing is known to be dispatchable.
  assert.deepEqual(narrow(rows, { ...all, dispatchable: true }, dispatchableIds(null)), []);
  assert.deepEqual([...dispatchableIds({ ready: "no" })], []);
});

test("search narrows by id and by words of the title, as they are typed", () => {
  const found = (search: string) => ids(narrow(rows, { ...all, search }, new Set()));
  assert.deepEqual(found("ser"), ["KYO-12", "KYO-21", "KYO-25"]);
  assert.deepEqual(found("serve"), ["KYO-12", "KYO-21", "KYO-25"]);
  assert.deepEqual(found("serve record"), ["KYO-25"]);
  assert.deepEqual(found("  SERVE   Page "), ["KYO-25"]);
  assert.deepEqual(found("kyo-3"), ["KYO-30"]);
  assert.deepEqual(found("30"), ["KYO-30"]);
  assert.deepEqual(found("nothing-like-this"), []);
  assert.equal(matchesSearch(rows[0], ""), true);
});

test("the two filters and the search combine", () => {
  const ready = dispatchableIds(next);
  assert.deepEqual(
    ids(narrow(rows, { ...all, open: true, dispatchable: true, search: "serve" }, ready)),
    ["KYO-12"]);
  assert.deepEqual(
    ids(narrow(rows, { ...all, open: true, search: "serve" }, ready)), ["KYO-12", "KYO-21"]);
  assert.deepEqual(
    narrow(rows, { ...all, open: true, dispatchable: true, search: "ideas" }, ready), []);
});

const gates = {
  gates: [
    { id: "KYO-30.g1", record_id: "KYO-30", type: "awaiting:human-run" },
    { id: "KYO-21.g1", record_id: "KYO-21", type: "awaiting:human-review" },
    { id: "KYO-21.g2", record_id: "KYO-21", type: "awaiting:human-run" },
    { id: "KYO-12.g1", record_id: "KYO-12", type: "awaiting:event" },
    { id: "KYO-25.g1", record_id: "KYO-25", type: "awaiting:human-run" },
  ],
};

test("Awaiting you shows only records with an open gate waiting on the person", () => {
  const awaiting = awaitingIds(gates);
  assert.deepEqual([...awaiting], ["KYO-30", "KYO-21", "KYO-25"]);
  assert.deepEqual(
    ids(narrow(rows, { ...all, awaiting: true }, new Set(), awaiting)),
    ["KYO-30", "KYO-21", "KYO-25"]);
  // Before kyo gates has answered, nothing is known to be waiting.
  assert.deepEqual(narrow(rows, { ...all, awaiting: true }, new Set(), awaitingIds(null)), []);
  assert.deepEqual([...awaitingIds({ gates: 3 })], []);
});

test("Awaiting you combines with the other two filters and the search", () => {
  const ready = dispatchableIds(next);
  const awaiting = awaitingIds(gates);
  const shown = (narrowing: object) =>
    ids(narrow(rows, { ...all, awaiting: true, ...narrowing }, ready, awaiting));
  assert.deepEqual(shown({ open: true }), ["KYO-30", "KYO-21"]);
  assert.deepEqual(shown({ dispatchable: true }), ["KYO-30"]);
  assert.deepEqual(shown({ search: "serve" }), ["KYO-21", "KYO-25"]);
  assert.deepEqual(shown({ open: true, search: "serve" }), ["KYO-21"]);
  assert.deepEqual(shown({ open: true, dispatchable: true, search: "serve" }), []);
});

test("a viewed record goes to the front of the history, once", () => {
  assert.deepEqual(visit([], "KYO-1"), ["KYO-1"]);
  assert.deepEqual(visit(["KYO-1", "KYO-2", "KYO-3"], "KYO-3"), ["KYO-3", "KYO-1", "KYO-2"]);
  const long = Array.from({ length: HISTORY_LIMIT }, (_, index) => `KYO-${index}`);
  const after = visit(long, "KYO-999");
  assert.equal(after.length, HISTORY_LIMIT);
  assert.equal(after[0], "KYO-999");
  assert.equal(after.includes(`KYO-${HISTORY_LIMIT - 1}`), false);
});

test("a stored history is read as ids, whatever was stored", () => {
  assert.deepEqual(historyFrom(["KYO-2", 4, null, "KYO-1"]), ["KYO-2", "KYO-1"]);
  assert.deepEqual(historyFrom(null), []);
  assert.deepEqual(historyFrom({ ids: [] }), []);
});

test("history rows carry the record's title and kind, or the id alone", () => {
  const shown = historyRows(["KYO-30", "KYO-404"], rows);
  assert.equal(shown[0].title, "A top bar of views");
  assert.deepEqual(shown[1], { id: "KYO-404", kind: "", title: "", status: "" });
});

test("an id names its record", () => {
  assert.equal(recordOf("KYO-732"), "KYO-732");
  assert.equal(recordOf("KYO-732.c1"), "KYO-732");
  assert.equal(recordOf("KYO-732.d12"), "KYO-732");
  assert.equal(recordOf("KYO-L296"), null);
  assert.equal(recordOf("notes"), null);
  assert.equal(prefixOf("PAC2-14"), "PAC2");
  assert.equal(prefixOf("readme"), null);
});

test("a kyosei:// link names a record and the element in it", () => {
  assert.deepEqual(parseLink("kyosei://kyosei/KYO-263"), { record: "KYO-263", ref: "KYO-263" });
  assert.deepEqual(parseLink("kyosei://KYO/KYO-730.c6"), { record: "KYO-730", ref: "KYO-730.c6" });
  assert.deepEqual(parseLink(" KYOSEI://kyosei/KYO%2D12 "), { record: "KYO-12", ref: "KYO-12" });
  assert.equal(parseLink("kyosei://kyosei/"), null);
  assert.equal(parseLink("kyosei://kyosei/ideas"), null);
  assert.equal(parseLink("https://example.com/KYO-1"), null);
  assert.equal(parseLink("kyosei://kyosei/%E0%A4%A"), null);
});

test("ids in a text become links, and other hyphenated words do not", () => {
  const parts = refParts("See KYO-731 and KYO-730.c6 (UTF-8, KYO-L296, XKYO-5, KYO-12a).", ["KYO"]);
  assert.deepEqual(parts, [
    { text: "See " },
    { text: "KYO-731", link: { record: "KYO-731", ref: "KYO-731" } },
    { text: " and " },
    { text: "KYO-730.c6", link: { record: "KYO-730", ref: "KYO-730.c6" } },
    { text: " (UTF-8, KYO-L296, XKYO-5, KYO-12a)." },
  ]);
  assert.deepEqual(refParts("KYO-1", ["KYO"]), [{ text: "KYO-1", link: { record: "KYO-1", ref: "KYO-1" } }]);
  assert.deepEqual(refParts("KYO-1", []), [{ text: "KYO-1" }]);
  assert.deepEqual(refParts("", ["KYO"]), []);
  // A sentence's full stop after an id is not part of it.
  assert.equal(refParts("Spawned from KYO-731.", ["KYO"])[1].text, "KYO-731");
});

test("the latest verdict is the verification recorded last", () => {
  const criterion = {
    verifications: [
      { id: "KYO-9.v1", seq: 1, verdict: "unmet-unobserved" },
      { id: "KYO-9.v3", seq: 3, verdict: "met" },
      { id: "KYO-9.v2", seq: 2, verdict: "unmet-unbuilt" },
    ],
  };
  assert.equal(latestVerdict(criterion)!.id, "KYO-9.v3");
  assert.equal(latestVerdict({ verifications: [] }), null);
  assert.equal(latestVerdict({}), null);
});

const work = {
  id: "KYO-9", kind: "task", status: "open", standing: "open", title: "A record page",
  plan: "KYO-7", scope: null, description: "Shows KYO-8.",
  created_at: "2026-11-10T18:13:46.588Z", updated_at: "2026-11-10T18:14:44.475Z",
  discussion: { id: "KYO-6" },
  criteria: [
    {
      id: "KYO-9.c1", seq: 1, state: "met", text: "The page shows the title", reason: null,
      verifications: [{
        id: "KYO-9.v1", seq: 1, verdict: "met", method: "machine-checked", author: "orchestrator",
        created_at: "2026-11-10T19:00:00.000Z", evidence_id: "KYO-9.e1", reason: null,
      }],
    },
    {
      id: "KYO-9.c2", seq: 2, state: "unmet-unobserved", text: "A person says it is right",
      reason: "needs a person", verifications: [],
    },
  ],
  gates: [
    { id: "KYO-9.g1", type: "awaiting:human-run", tags: ["ui"], do: "Open a record", cleared_at: null },
    {
      id: "KYO-9.g2", type: "awaiting:human-review", tags: [], do: "Read the plan",
      cleared_at: "2026-11-10T20:00:00.000Z", cleared_as: "answered", cleared_why: "It was read",
      credit: "KYO-9.p1",
    },
  ],
  links: [{ id: "KYO-L5", type: "produced-by", direction: "outgoing", other: "KYO-6" }],
  dispatches: [{
    id: "KYO-9.d1", state: "running", worker_model: "claude-fable-5-2", branch: "kyo-9-page",
    brief: "# Dispatch brief", worked_from_sha: "0841dc98", report_id: "KYO-9.r1", covers: [],
    created_at: "2026-11-10T18:14:24.294Z",
  }],
  reports: [{
    id: "KYO-9.r1", dispatch_id: "KYO-9.d1", author: "orchestrator",
    created_at: "2026-11-10T19:00:00.000Z",
    body: {
      criteria_verdicts: [{
        criterion_id: "KYO-9.c1", verdict: "met", method: "machine-checked", evidence: "tests pass",
      }],
      premise_false: [{ claim: "The stream is followed", correction: "It is polled", impact: "Added" }],
      disagreements: [], corrected_numbers: [], commits: [{ sha: "abc1234", subject: "The page" }],
    },
  }],
  notes: [{ id: "KYO-9.n1", text: "A note", created_at: "2026-11-10T19:30:00.000Z" }],
  claims: [{ id: "KYO-9.k1", statement: "It renders", level: "observed", method: "ran it", author: "worker" }],
  credits: [], evidence_artifacts: [], touches: [{ id: "KYO-9.t1", path: "src/records.ts", symbol: null }],
  flags: [], members: [],
};

test("a work record's page carries each part kyo show prints, with its id", () => {
  const page = recordPage(work, new Map([["KYO-6", "The discussion"]]));
  assert.equal(page.id, "KYO-9");
  assert.equal(page.kind, "task");
  assert.equal(page.title, "A record page");
  assert.equal(page.description, "Shows KYO-8.");
  const fact = (label: string) => page.facts.find((line) => line.label === label)?.text;
  assert.equal(fact("status"), "open");
  assert.equal(fact("plan"), "KYO-7");
  assert.equal(fact("scope"), "none declared");
  assert.equal(fact("discussion"), "KYO-6");
  const section = (name: string) => page.sections.find((part) => part.name === name)!;
  assert.deepEqual(
    page.sections.map((part) => part.name),
    ["Criteria", "Gates", "Links", "Dispatches", "Reports", "Notes", "Claims", "Touches"]);
  assert.deepEqual(page.none, ["Members", "Credits", "Evidence", "Flags"]);

  const [first, second] = section("Criteria").items;
  assert.equal(first.ref, "KYO-9.c1");
  assert.equal(first.citable, true);
  assert.equal(first.state, "met");
  const verdict = first.lines!.find((line) => line.label === "latest verdict")!.text;
  assert.match(verdict, /^met · machine-checked · orchestrator · /);
  assert.match(verdict, /KYO-9\.v1 · evidence KYO-9\.e1$/);
  assert.deepEqual(second.lines, [
    { label: "reason", text: "needs a person" },
    { label: "latest verdict", text: "none recorded" },
  ]);

  assert.equal(section("Gates").count, "1 open of 2");
  const [open, cleared] = section("Gates").items;
  assert.deepEqual(
    { ref: open.ref, state: open.state, text: open.text, meta: open.meta },
    { ref: "KYO-9.g1", state: "open", text: "Open a record", meta: ["awaiting:human-run", "ui"] });
  assert.equal(cleared.state, "cleared as answered");
  assert.equal(cleared.lines!.at(-1)!.text, "It was read");

  assert.equal(section("Links").items[0].text, "KYO-6 The discussion");
  const dispatch = section("Dispatches").items[0];
  assert.equal(dispatch.ref, "KYO-9.d1");
  assert.equal(dispatch.tone, "running");
  assert.deepEqual(dispatch.folded, [{ label: "Brief, 16 characters", text: "# Dispatch brief" }]);
  assert.deepEqual(
    dispatch.lines!.filter((line) => ["model", "branch", "base"].includes(line.label)),
    [
      { label: "model", text: "claude-fable-5-2" }, { label: "branch", text: "kyo-9-page" },
      { label: "base", text: "0841dc98" },
    ]);
  assert.ok(dispatch.lines!.some((line) => line.label === "report" && line.text === "KYO-9.r1"));

  const report = section("Reports").items[0];
  // The review order: false premises first, then the verdicts, then the commits.
  assert.equal(report.lines![0].label, "premise false");
  assert.equal(report.lines![0].group, "False premises");
  assert.match(report.lines![0].text, /Correction: It is polled/);
  assert.deepEqual(report.lines![1], {
    label: "KYO-9.c1 · met · machine-checked", text: "tests pass", group: "Verdicts", ref: "KYO-9.c1",
  });
  assert.deepEqual(report.lines!.at(-1), { label: "commit", text: "abc1234 The page", group: "Commits" });

  assert.equal(section("Notes").items[0].citable, true);
  assert.equal(section("Claims").items[0].text, "It renders");
  assert.equal(section("Touches").items[0].text, "src/records.ts");
});

test("a knowledge record's page carries its credits, with the person's words", () => {
  const page = recordPage({
    id: "KYO-21", kind: "decision", status: "open", standing: "current", title: "A decision",
    scope: "engine", description: "",
    credits: [{
      id: "KYO-21.p1", role: "decided", person: "jbmv", outcome: "overrode",
      transcript_check: "matched", quote_id: "KYO-21.e1", recommended: "Run it locally",
      recommended_by: "orchestrator",
      alternatives: [{ chosen: false, text: "Over SSH" }, { chosen: true, text: "Through a route" }],
    }],
    evidence_artifacts: [{ id: "KYO-21.e1", text: "have kyo serve start the session", sha256: "ddee0fd3e55c38dd" }],
    members: [{ id: "KYO-22", kind: "task", status: "done", title: "Build it" }],
  });
  assert.equal(page.facts[0].text, "open · standing current");
  assert.equal(page.facts.find((line) => line.label === "scope")!.text, "engine");
  const credit = page.sections.find((part) => part.name === "Credits")!.items[0];
  assert.equal(credit.state, "decided");
  assert.deepEqual(credit.meta, ["jbmv", "overrode", "transcript matched"]);
  assert.deepEqual(credit.lines, [
    { label: "words", text: "have kyo serve start the session" },
    { label: "quoted in", text: "KYO-21.e1" },
    { label: "recommended", text: "Run it locally · by orchestrator" },
    { label: "alternative", text: "Over SSH" },
    { label: "alternative, chosen", text: "Through a route" },
  ]);
  const evidence = page.sections.find((part) => part.name === "Evidence")!.items[0];
  assert.equal(evidence.text, "have kyo serve start the session");
  assert.ok(evidence.meta!.includes("sha256 ddee0fd3e55c"));
  assert.equal(page.sections.find((part) => part.name === "Members")!.items[0].text, "KYO-22 Build it");
  assert.ok(page.none.includes("Criteria") && page.none.includes("Gates"));
});

test("a document with parts missing still makes a page", () => {
  const page = recordPage({ id: "KYO-3" });
  assert.equal(page.id, "KYO-3");
  assert.deepEqual(page.sections, []);
  assert.equal(page.none.length, 12);
});

test("Awaiting you never stands with Open or Dispatchable", () => {
  const all = { open: true, dispatchable: true, awaiting: false, search: "x" };
  assert.deepEqual(pressed(all, "awaiting"), { open: false, dispatchable: false, awaiting: true, search: "x" });
  const waiting = { open: false, dispatchable: false, awaiting: true, search: "" };
  // Turning Open or Dispatchable on turns Awaiting you off, so the three never stand together.
  assert.deepEqual(pressed(waiting, "open"), { open: true, dispatchable: false, awaiting: false, search: "" });
  assert.deepEqual(pressed(waiting, "dispatchable"), { open: false, dispatchable: true, awaiting: false, search: "" });
  const open = { open: true, dispatchable: false, awaiting: false, search: "" };
  assert.deepEqual(pressed(open, "dispatchable"), { open: true, dispatchable: true, awaiting: false, search: "" });
  assert.deepEqual(pressed(waiting, "awaiting"), { open: false, dispatchable: false, awaiting: false, search: "" });
});

const FULL_REPORT = {
  id: "KYO-9.r1", dispatch_id: "KYO-9.d1", author: "orchestrator", created_at: "2026-11-10T19:00:00.000Z",
  body: {
    summary: "Built the page.",
    criteria_verdicts: [
      { criterion_id: "KYO-9.c1", verdict: "met", method: "machine-checked", evidence: "pass" },
      { criterion_id: "KYO-9.c2", verdict: "unmet-unobserved", method: "reasoned-about", reason: "needs a person" },
    ],
    premise_false: [{ claim: "A", correction: "B" }],
    disagreements: [{ topic: "T", brief_said: "X", worker_did: "Y", rationale: "Z" }],
    corrected_numbers: [{ claim: "N", brief_value: "1", actual_value: "2", method: "wc" }],
    docs_verdicts: [{ doc_path: "docs/a.md", section: "Intro", verdict: "not-affected", method: "reasoned-about", reason: "none" }],
    commits: [{ sha: "abc1234", subject: "S" }],
    touched_files: ["a.ts", "b.ts"],
  },
};

test("a report is drawn in the order an orchestrator reviews it, every field of it", () => {
  const item = reportItem(FULL_REPORT);
  assert.deepEqual(
    item.lines!.filter((one) => one.group).map((one) => one.group),
    ["False premises", "Disagreements with the brief", "What the worker did and why",
      "Corrected numbers", "Verdicts", "Docs verdicts", "Commits", "Files touched"]);
  const verdicts = item.lines!.filter((one) => one.ref);
  assert.deepEqual(verdicts.map((one) => one.ref), ["KYO-9.c1", "KYO-9.c2"]);
  assert.match(verdicts[1].text, /Reason: needs a person/);
  const docs = item.lines!.find((one) => one.group === "Docs verdicts")!;
  assert.equal(docs.label, "not-affected · reasoned-about");
  assert.match(docs.text, /docs\/a\.md § Intro/);
  assert.equal(item.collapsed, undefined);
});

test("a report that was amended is marked and folded, and the amending one is current", () => {
  const amending = { ...FULL_REPORT, id: "KYO-9.r2", body: { ...FULL_REPORT.body, amends: "KYO-9.r1" } };
  const page = recordPage({ ...work, reports: [FULL_REPORT, amending] });
  const [old, current] = page.sections.find((part) => part.name === "Reports")!.items;
  assert.equal(old.state, "superseded");
  assert.match(old.meta!.join(" "), /superseded by KYO-9\.r2/);
  assert.match(old.collapsed!, /^Superseded report/);
  assert.equal(current.collapsed, undefined);
  assert.match(current.meta!.join(" "), /amends KYO-9\.r1/);
});

test("the task prose of a brief is found between its Task and Report headings", () => {
  const brief = "# Dispatch brief\n\n## Standing stance\n\nx\n\n---\n\n## Task\n\nDo it.\n\n- one\n\n## Report\n\nWrite it.\n";
  assert.equal(taskOf(brief), "Do it.\n\n- one");
  assert.equal(taskOf("# Dispatch brief\n\nno task"), null);
  assert.equal(taskOf("## Task\n\nlast"), "last");
  const dispatch = recordPage({ ...work, dispatches: [{ id: "KYO-9.d1", brief, state: "reported" }] })
    .sections.find((part) => part.name === "Dispatches")!.items[0];
  assert.deepEqual(dispatch.folded!.map((one) => one.label.split(",")[0]), ["Task", "Brief"]);
});

test("a record a dispatch covers draws the dispatch and its report, which it once drew as nothing", () => {
  // KYO-724 was covered by KYO-726.d1 and held no dispatch or report of its own: its page
  // showed neither, whatever `kyo show` printed under "covered by".
  const covered = {
    ...work, id: "KYO-24", dispatches: [], reports: [], touches: [],
    covered_by_dispatches: [{ id: "KYO-9.d1", primary_record_id: "KYO-9", report_id: "KYO-9.r1", state: "reported" }],
  };
  const bare = recordPage(covered);
  assert.equal(bare.sections.find((part) => part.name === "Dispatches")!.items[0].ref, "KYO-9.d1");
  assert.equal(bare.sections.find((part) => part.name === "Reports"), undefined);
  const page = recordPage(covered, new Map(), new Map([["KYO-9", { ...work, reports: [FULL_REPORT] }]]));
  const dispatch = page.sections.find((part) => part.name === "Dispatches")!.items[0];
  assert.equal(dispatch.citable, false);
  assert.equal(dispatch.lines!.find((one) => one.label === "branch")!.text, "kyo-9-page");
  const report = page.sections.find((part) => part.name === "Reports")!.items[0];
  assert.equal(report.ref, "KYO-9.r1");
  assert.ok(report.lines!.some((one) => one.group === "Verdicts"));
});
