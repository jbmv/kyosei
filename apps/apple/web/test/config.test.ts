// Run with `npm test`. The Config view (KYO-953): what it makes of the two documents the server
// serves (`GET /v1/projects/{project}/config`, `GET /v1/settings`), in the logic that holds no
// element and in the page, with fake frames of the shape `kyo config --json` prints. A machine
// with no Chrome skips the page's tests.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  type Reading, blockedReason, chosenSection, controlOf, fileProblem, firstLine, holdsRequest, lastChangeText,
  machineSections, noteAfter, numberBody, projectSections, readState, requestText, valueText, writeBody,
} from "../src/config.ts";
import { openPage } from "./chrome.ts";

const status = (state: string, detail: string | null = null, fix: string | null = null) => ({ status: state, detail, fix });

const setting = (over: Record<string, unknown>) => ({
  absent_means: "nothing happens", class: "shown", comment: [], consumers: [], decision: null, file: "config",
  key: "k", keys_in_file: ["k"], last_change: null, lines: { first: 1, last: 1 }, present: true,
  section: "identity", status: null, trailing: null, value: "v", why: "A reason.", ...over,
});

/** A project document of the served shape: three classes, a comment of several lines, an id in it. */
const projectDocument = (over: Record<string, unknown> = {}) => ({
  ok: true, command: "config", project: "p", layout: "split",
  server: { kyosei: "kyosei 0.1.0", stale: false },
  device: { name: "laptop", permissions: ["send", "files"] },
  files: {
    config: { path: "/store/config.yml", etag: "sha256:1", present: true, readable: true, problem: null },
    agent_types: { path: ".claude/agents", present: true },
  },
  settings: [
    setting({ key: "project_prefix", decision: "B3", value: "KYO", status: status("present", "KYO"),
      comment: ["# project_prefix: read by store.allocate"] }),
    setting({
      key: "worker_model", class: "editable", section: "workers", decision: "D1", value: "claude-sonnet-5-5",
      status: status("present", "claude-sonnet-5-5"),
      comment: ["# The model a dispatch names, since KYO-743.", "# Opus runs only when a dispatch names it."],
      write: { route: "config/worker_model", clearable: true, one_way: null, permission: "config",
        value: { type: "string", pattern: "[A-Za-z0-9]+" } },
    }),
    setting({
      key: "worktree_setup", class: "request", section: "workers", decision: "D4", present: false, value: null,
      absent_means: "a brief states that nothing is declared",
      request: { header: "[kyo-config-request] worktree_setup" },
    }),
    setting({
      key: "read_transcripts", class: "editable", section: "people", decision: "D14", value: true,
      write: { route: "config/read_transcripts", clearable: false, permission: "config",
        one_way: "Turning this off cannot be undone from this view.", value: { type: "boolean", values: [false] } },
    }),
    setting({ key: "a_key_a_later_build_adds", class: "request", section: "newer", value: 3 }),
  ],
  agent_types: [],
  beside: { kinds: [{ kind: "task", worker_model: null }, { kind: "bug", worker_model: "x" }], scopes: ["cli"], documents: [] },
  decisions: [
    { id: "B2", title: "store remote", class: "request", request_also: false, why: "A git operation.",
      status: [{ label: "store remote", status: "missing", detail: "none", fix: "git remote add" }] },
    { id: "A3", title: "existing tracking", class: "omitted", request_also: false, why: "", status: [] },
    { id: "C1", title: "plugin or copies", class: "shown", request_also: true, why: "Shown.", status: [] },
  ],
  ...over,
});

const machineDocument = () => ({
  ok: true, command: "config", machine: true, server: { kyosei: "kyosei 0.1.0", stale: true, stale_detail: "older than the install" },
  device: { name: "laptop", permissions: ["send"] },
  files: { settings: { path: "/home/settings.yml", etag: "sha256:2", present: true, readable: true, problem: null } },
  settings: [
    setting({ key: "serve_idle_message", class: "editable", file: "settings", section: "machine", value: false,
      write: { route: "settings/serve_idle_message", clearable: false, one_way: null, permission: "config", value: { type: "boolean" } } }),
  ],
  decisions: [{ id: "E3", title: "kyo serve", class: "shown", request_also: false, why: "x", status: [status("present", "login agent")] }],
});

test("the sections come from the document: a key is a row of its section, and a section the page does not know is still drawn", () => {
  const sections = projectSections(projectDocument() as Record<string, unknown>);
  const names = (id: string) => sections.find((section) => section.id === id)?.rows.map((row) => row.name);
  assert.deepEqual(names("identity"), ["project_prefix"]);
  assert.deepEqual(names("workers"), ["worker_model", "worktree_setup"]);
  assert.deepEqual(names("people"), ["read_transcripts"]);
  // A key of a section no build of the page knew is a row, and the section is titled by its id.
  assert.deepEqual(names("newer"), ["a_key_a_later_build_adds"]);
  assert.equal(sections.find((section) => section.id === "newer")?.title, "newer");
  // A section that holds nothing is not listed, and the omitted decision has no row.
  assert.equal(sections.some((section) => section.id === "prior-art"), false);
  assert.deepEqual(names("decisions"), ["store remote", "plugin or copies"]);
});

test("a row carries the name, decision, value, status, comment and reason of its setting, and the class it is in", () => {
  const sections = projectSections(projectDocument() as Record<string, unknown>);
  const model = sections.find((section) => section.id === "workers")!.rows[0];
  assert.deepEqual(
    [model.name, model.decision, model.klass, model.value, model.statuses[0].status, model.why],
    ["worker_model", "D1", "editable", "claude-sonnet-5-5", "present", "A reason."]);
  assert.deepEqual(model.comment, ["# The model a dispatch names, since KYO-743.", "# Opus runs only when a dispatch names it."]);
  assert.equal(model.first, "# The model a dispatch names, since KYO-743.");
  const request = sections.find((section) => section.id === "workers")!.rows[1];
  assert.deepEqual([request.klass, request.value, request.absent], ["request", null, "a brief states that nothing is declared"]);
  assert.match(request.how ?? "", /asking the session/);
  const asked = sections.find((section) => section.id === "decisions")!.rows[1];
  assert.deepEqual([asked.klass, asked.label], ["shown", "Shown only, or by request"]);
});

test("a control is described from the document's own write descriptor, and a change that cannot be undone carries its warning", () => {
  const rows = projectSections(projectDocument() as Record<string, unknown>).flatMap((section) => section.rows);
  assert.deepEqual(rows.find((row) => row.name === "worker_model")!.control, { kind: "text", value: "claude-sonnet-5-5", clearable: true });
  const transcripts = rows.find((row) => row.name === "read_transcripts")!;
  assert.deepEqual(transcripts.control, { kind: "turn-off", value: true });
  assert.equal(transcripts.oneWay, "Turning this off cannot be undone from this view.");
  assert.equal(rows.find((row) => row.name === "worker_model")!.oneWay, null);
  assert.deepEqual(controlOf({ value: { type: "string", enum: ["off", "notice"] } }, "off"), { kind: "segments", options: ["off", "notice"], value: "off" });
  assert.deepEqual(controlOf({ value: { type: "integer" }, clearable: true }, 900), { kind: "number", value: "900", clearable: true });
  assert.deepEqual(controlOf({ value: { type: "boolean" } }, false), { kind: "switch", value: false });
  assert.equal(controlOf({ value: { type: "object" } }, null), null);
  // A shown or request row has no control, whatever else it says.
  assert.equal(rows.find((row) => row.name === "worktree_setup")!.control, null);
});

test("the machine's sections hold its settings, its onboard items and the device, and a stale server is said", () => {
  const sections = machineSections(machineDocument() as Record<string, unknown>);
  assert.deepEqual(sections.map((section) => section.id), ["this-machine", "machine", "device"]);
  assert.deepEqual(sections[1].rows.map((row) => row.name), ["serve_idle_message"]);
  assert.deepEqual(sections[0].rows.map((row) => row.decision), ["E3"]);
  assert.equal(sections[0].facts.find((fact) => fact.label === "Stale")?.text, "older than the install");
  assert.match(sections[2].facts.find((fact) => fact.label === "Config permission")!.text, /kyo devices grant/);
});

test("values: a list of words is one line, a nested value a block, and nothing set is null", () => {
  assert.deepEqual(valueText(["claude", "--chrome"]), { text: "claude --chrome", block: false });
  assert.deepEqual(valueText([{ run: "make venv", note: "first" }]), { text: "- run: make venv\n  note: first", block: true });
  assert.deepEqual(valueText(["make test", "npm run check"]), { text: "- make test\n- npm run check", block: true });
  assert.deepEqual(valueText({ jbmv: {}, level: "problem", code: ["a", "b"] }), { text: "jbmv: {}\nlevel: problem\ncode:\n  - a\n  - b", block: true });
  assert.deepEqual(valueText(true), { text: "true", block: false });
  assert.equal(valueText(null), null);
  assert.equal(firstLine([]), null);
});

test("a file this build refuses is named with its refusal, and the section stays chosen only while it exists", () => {
  const refused = projectDocument({
    files: { config: { path: "c", etag: "e", present: true, readable: false, problem: "worker_model: not a model id" } },
  }) as Record<string, unknown>;
  assert.equal(fileProblem(refused, "config"), "worker_model: not a model id");
  assert.equal(fileProblem(projectDocument() as Record<string, unknown>, "config"), null);
  const sections = projectSections(projectDocument() as Record<string, unknown>);
  assert.equal(chosenSection(sections, "workers")!.id, "workers");
  assert.equal(chosenSection(sections, "gone")!.id, sections[0].id);
  assert.equal(chosenSection([], null), null);
});

test("section 8.3: no token, a refusal, no answer and a server older than the routes are each said in words", () => {
  const read = (over: Partial<Reading>): Reading => ({ status: 200, document: null, error: null, ...over });
  assert.deepEqual(readState(null, null), { kind: "waiting" });
  assert.equal(readState(read({ status: 200, document: { ok: true } }), null).kind, "ok");
  const noToken = readState(read({ status: 401, error: "this route needs the header `Authorization: Bearer <token>`" }), null);
  assert.equal(noToken.kind === "failed" && noToken.why, "no-token");
  assert.match(noToken.kind === "failed" ? noToken.message : "", /Authorization: Bearer.*kyo devices issue <name>.*Settings/);
  const old = readState(read({ status: 404, error: "no such route" }), "kyosei 0.0.9");
  assert.equal(old.kind === "failed" && old.why, "no-route");
  assert.match(old.kind === "failed" ? old.message : "", /no configuration route.*kyosei 0\.0\.9/);
  const gone = readState(read({ status: 0, error: "The server could not be reached." }), null);
  assert.equal(gone.kind === "failed" && gone.message, "The server could not be reached.");
  const other = readState(read({ status: 409, error: "not a project" }), null);
  assert.equal(other.kind === "failed" && other.message, "not a project");
});

// ------------------------------------------------------------------------------ the page

/** Opens the page on the Config view with the frames of a project and returns the page. */
async function configPage(t: { skip(message: string): void }, frames: { project?: unknown; machine?: unknown; version?: string | null }) {
  const page = await openPage();
  if (!page) { t.skip("no Chrome here"); return null; }
  await page.reload();
  await page.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.recordLists({ list: { records: [{ id: "KYO-743", title: "t", kind: "task", status: "open" }] } });
    document.querySelector('.view-item[data-view-item="config"]').click();
    window.kyo.configRead({ project: "p", version: ${JSON.stringify(frames.version ?? null)},
      read: ${JSON.stringify(frames.project ?? null)}, machine: ${JSON.stringify(frames.machine ?? null)} });
  })()`);
  return page;
}

const answered = (document: unknown): Reading => ({ status: 200, document, error: null });

test("the view is a view of the bar named Config, and opening it asks the app for both documents", async (t) => {
  const page = await openPage(); if (!page) return t.skip("no Chrome here");
  try {
    await page.reload();
    const seen = await page.run<{ names: string[]; asked: unknown[]; shown: boolean }>(`(() => {
      window.kyo.project({ project: "p", scope: "server/p" });
      const names = [...document.querySelectorAll(".view-item")].map((item) => item.textContent);
      document.querySelector('.view-item[data-view-item="config"]').click();
      return { names, asked: window.posted.filter((post) => post.name === "config").map((post) => post.body),
        shown: document.body.classList.contains("view-config") && !document.getElementById("view-config").hidden };
    })()`);
    assert.deepEqual(seen.names, ["Chat", "Records", "Files", "History", "Config"]);
    assert.deepEqual(seen.asked, [{ want: "read" }]);
    assert.equal(seen.shown, true);
    // A change of a configuration file reads both again, and the app's turn to the view does too.
    const again = await page.run<number>(`(() => {
      window.kyo.configChanged({ kind: "config" });
      window.kyo.openView({ view: "files" }); window.kyo.openView({ view: "config" });
      return window.posted.filter((post) => post.name === "config").length;
    })()`);
    assert.equal(again, 2);
    const open = await page.run<string>(`(window.kyo.openView({ view: "history" }), document.body.className)`);
    assert.match(open, /view-history/);
  } finally {
    await page.close();
  }
});

test("the page draws every section, each row from its document, and a comment whole once opened with its ids as links", async (t) => {
  const page = await configPage(t, { project: answered(projectDocument()), machine: answered(machineDocument()) });
  if (!page) return;
  try {
    const seen = await page.run<{ side: string[]; rows: string[]; classes: string[]; comment: string; first: string; links: string[] }>(`(() => {
      const pick = (title) => [...document.querySelectorAll(".config-section-button")].find((b) => b.textContent === title).click();
      const side = [...document.querySelectorAll("#config-side h4, .config-section-button")].map((e) => e.textContent);
      pick("Workers");
      const row = (name) => document.querySelector('.config-row[data-name="' + name + '"]');
      const comment = row("worker_model").querySelector(".config-comment");
      const out = {
        side,
        rows: [...document.querySelectorAll(".config-row")].map((r) => r.dataset.name),
        classes: [...document.querySelectorAll(".config-row")].map((r) => r.dataset.class),
        first: comment.querySelector(".config-first").textContent,
        comment: comment.querySelector(".config-comment-text").textContent,
        links: [...comment.querySelectorAll("a.ref")].map((a) => a.getAttribute("href")),
      };
      return out;
    })()`);
    assert.deepEqual(seen.side, [
      "Project p", "Store and identity", "Workers", "Records and documents", "People and transcripts", "Setup decisions",
      "newer", "Machine", "This machine", "Machine settings", "This device"]);
    assert.deepEqual(seen.rows, ["worker_model", "worktree_setup"]);
    assert.deepEqual(seen.classes, ["editable", "request"]);
    assert.equal(seen.first, "# The model a dispatch names, since KYO-743.");
    assert.equal(seen.comment, "# The model a dispatch names, since KYO-743.\n# Opus runs only when a dispatch names it.");
    assert.equal(seen.links.length, 1);
    assert.match(seen.links[0], /^kyosei:\/\/[^/]*\/KYO-743$/);
    // A press on the id opens the record in Records.
    const opened = await page.run<{ view: boolean; asked: unknown }>(`(() => {
      document.querySelector(".config-comment a.ref").click();
      return { view: document.body.classList.contains("view-records"),
        asked: window.posted.filter((p) => p.name === "records" && p.body.want === "record").map((p) => p.body.id).at(-1) };
    })()`);
    assert.deepEqual(opened, { view: true, asked: "KYO-743" });
  } finally {
    await page.close();
  }
});

test("a row says its class at a glance, shows its control disabled with the reason, and no control writes anything", async (t) => {
  const page = await configPage(t, { project: answered(projectDocument()), machine: answered(machineDocument()) });
  if (!page) return;
  try {
    const seen = await page.run<Record<string, unknown>>(`(() => {
      const pick = (title) => [...document.querySelectorAll(".config-section-button")].find((b) => b.textContent === title).click();
      pick("Workers");
      const badge = (name) => document.querySelector('.config-row[data-name="' + name + '"] .config-class').textContent;
      const bar = (name) => getComputedStyle(document.querySelector('.config-row[data-name="' + name + '"]')).borderLeftColor;
      const out = { editable: badge("worker_model"), request: badge("worktree_setup"),
        distinct: new Set([bar("worker_model"), bar("worktree_setup")]).size,
        enabled: [...document.querySelectorAll(".config-control input, .config-control button")].filter((e) => !e.disabled).length,
        controls: document.querySelectorAll(".config-control").length,
        how: document.querySelector('.config-row[data-name="worktree_setup"] .config-how').textContent,
        unset: document.querySelector('.config-row[data-name="worktree_setup"] .config-value').textContent };
      pick("People and transcripts");
      out.warning = document.querySelector(".config-warning").textContent;
      out.posted = window.posted.filter((p) => p.name === "config" && p.body.want !== "read").length
        + window.posted.filter((p) => ["send", "sendNow", "saveDraft"].includes(p.name)).length;
      return out;
    })()`);
    assert.equal(seen.editable, "Editable here");
    assert.equal(seen.request, "Request");
    assert.equal(seen.distinct, 2);
    assert.equal(seen.enabled, 0);
    assert.equal(seen.controls, 1);
    assert.match(String(seen.how), /asking the session/);
    assert.equal(seen.unset, "not set: a brief states that nothing is declared");
    assert.equal(seen.warning, "Turning this off cannot be undone from this view.");
    assert.equal(seen.posted, 0);
    // Without the config permission each control says so, naming the grant on the machine.
    const reasons = await page.run<string[]>(`[...document.querySelectorAll(".config-blocked")].map((e) => e.textContent)`);
    assert.equal(reasons.length, 1);
    assert.match(reasons[0], /kyo devices grant laptop --permission config/);
  } finally {
    await page.close();
  }
});

test("the view draws again on a read, and keeps a comment open that a reader opened", async (t) => {
  const page = await configPage(t, { project: answered(projectDocument()), machine: answered(machineDocument()) });
  if (!page) return;
  try {
    const kept = await page.run<boolean>(`(() => {
      [...document.querySelectorAll(".config-section-button")].find((b) => b.textContent === "Workers").click();
      const details = document.querySelector(".config-comment");
      details.open = true;
      return new Promise((done) => setTimeout(() => {
        window.kyo.configRead({ project: "p", version: null, read: { status: 200, error: null, document: ${JSON.stringify(projectDocument())} },
          machine: { status: 200, error: null, document: ${JSON.stringify(machineDocument())} } });
        done(document.querySelector(".config-comment").open);
      }, 50));
    })()`);
    assert.equal(kept, true);
  } finally {
    await page.close();
  }
});

test("a file the build refuses is shown with its refusal and no live control", async (t) => {
  const refused = projectDocument({
    files: { config: { path: "/s/config.yml", etag: "e", present: true, readable: false, problem: "worker_model: not a model id" } },
  });
  const page = await configPage(t, { project: answered(refused), machine: answered(machineDocument()) });
  if (!page) return;
  try {
    const seen = await page.run<{ note: string; rows: number; live: number }>(`(() => {
      [...document.querySelectorAll(".config-section-button")].find((b) => b.textContent === "Workers").click();
      return { note: document.querySelector(".config-note").textContent, rows: document.querySelectorAll(".config-row").length,
        live: [...document.querySelectorAll(".config-control input, .config-control button")].filter((e) => !e.disabled).length };
    })()`);
    assert.match(seen.note, /worker_model: not a model id/);
    assert.deepEqual([seen.rows, seen.live], [2, 0]);
  } finally {
    await page.close();
  }
});

test("with no token, and against a server that answers 404, the view says what section 8.3 states", async (t) => {
  const none: Reading = { status: 401, document: null, error: "this route needs the header `Authorization: Bearer <token>`" };
  const page = await configPage(t, { project: none, machine: none });
  if (!page) return;
  try {
    const text = await page.run<string>(`document.getElementById("config-page").textContent`);
    assert.match(text, /kyo devices issue <name>/);
    assert.equal(await page.run<number>(`document.querySelectorAll(".config-row").length`), 0);
    const old: Reading = { status: 404, document: null, error: "no route" };
    await page.run(`window.kyo.configRead({ project: "p", version: "kyosei 0.0.9", read: ${JSON.stringify(old)}, machine: ${JSON.stringify(old)} })`);
    const oldText = await page.run<string>(`document.getElementById("config-page").textContent`);
    assert.match(oldText, /no configuration route/);
    assert.match(oldText, /kyosei 0\.0\.9/);
  } finally {
    await page.close();
  }
});

test("the page is readable at a narrow width and in both colour schemes", async (t) => {
  const page = await configPage(t, { project: answered(projectDocument()), machine: answered(machineDocument()) });
  if (!page) return;
  try {
    const seen = await page.run<{ overflow: boolean; rows: number }>(`(() => {
      [...document.querySelectorAll(".config-section-button")].find((b) => b.textContent === "Workers").click();
      const pane = document.getElementById("view-config");
      return { overflow: pane.scrollWidth > pane.clientWidth + 1, rows: document.querySelectorAll(".config-row").length };
    })()`);
    assert.deepEqual(seen, { overflow: false, rows: 2 });
    assert.ok((await page.shot()).length > 1000);
  } finally {
    await page.close();
  }
});

// ------------------------------------------------------------------------------ controls (KYO-959)

/** A document a device holding the config permission reads: an editable row of each shape, and requests. */
const editorDocument = (over: Record<string, unknown> = {}) => projectDocument({
  device: { name: "laptop", permissions: ["send", "config"], holds_config: true },
  settings: [
    setting({
      key: "worker_model", class: "editable", section: "workers", value: "claude-sonnet-5-5",
      last_change: { at: "2026-11-15T20:01Z", device: "laptop", key: "worker_model", previous: "claude-opus-5-5", new: "claude-sonnet-5-5" },
      write: { route: "config/worker_model", clearable: true, one_way: null, permission: "config", value: { type: "string", pattern: "x" } },
    }),
    setting({
      key: "worker_command_timeout_ceiling_seconds", class: "editable", section: "workers", present: false, value: null,
      write: { route: "config/worker_command_timeout_ceiling_seconds", clearable: true, one_way: null, permission: "config",
        value: { type: "integer", minimum: 1 } },
    }),
    setting({
      key: "docs_coverage.level", class: "editable", section: "docs", value: "notice",
      write: { route: "config/docs_coverage.level", clearable: false, one_way: null, permission: "config",
        value: { type: "string", enum: ["off", "notice", "problem"] } },
    }),
    setting({
      key: "read_transcripts", class: "editable", section: "people", value: true,
      write: { route: "config/read_transcripts", clearable: false, permission: "config",
        one_way: "Turning this off cannot be undone from this view.", value: { type: "boolean", values: [false] } },
    }),
    setting({
      key: "worktree_setup", class: "request", section: "workers", present: false, value: null,
      request: { header: "[kyo-config-request] worktree_setup, kyosei/config.yml lines 67-85 (setup decision D4)" },
    }),
  ],
  agent_types: [{
    name: "kyosei-worker", model: "sonnet", effort: "medium", etag: "sha256:agent", class: "editable", status: null,
    write: { route: "agent-types/kyosei-worker", clearable: false, permission: "config", one_way: null,
      value: { type: "object", model: { type: "string", pattern: "x" }, effort: { type: "string", enum: ["low", "medium", "high"] } } },
  }],
  ...over,
}) as Record<string, unknown>;

const rowOf = (document: Record<string, unknown>, name: string) =>
  projectSections(document).flatMap((section) => section.rows).find((row) => row.name === name)!;

test("a control is drawn from the write descriptor's value shape, with the file's etag and the route it names", () => {
  const document = editorDocument();
  const model = rowOf(document, "worker_model");
  assert.deepEqual(model.write, { route: "config/worker_model", etag: "sha256:1", machine: false });
  assert.equal(model.disabled, null);
  assert.equal(model.lastChange, "Last changed 2026-11-15T20:01Z by laptop: claude-opus-5-5 to claude-sonnet-5-5.");
  assert.deepEqual(rowOf(document, "worker_command_timeout_ceiling_seconds").control, { kind: "number", value: "", clearable: true });
  assert.deepEqual(rowOf(document, "docs_coverage.level").control,
    { kind: "segments", options: ["off", "notice", "problem"], value: "notice" });
  // An object-valued write is one field to each part: the part that lists values is a menu.
  const agent = rowOf(document, "kyosei-worker");
  assert.deepEqual(agent.control, { kind: "fields", fields: [
    { name: "model", value: "sonnet", options: null }, { name: "effort", value: "medium", options: ["low", "medium", "high"] }] });
  assert.deepEqual(agent.write, { route: "agent-types/kyosei-worker", etag: "sha256:agent", machine: false });
  // The machine's route is the server's, with the settings file's etag.
  const machine = machineSections({ ...machineDocument(), server: { kyosei: "k", stale: false },
    device: { name: "laptop", permissions: ["config"], holds_config: true } } as Record<string, unknown>);
  assert.deepEqual(machine[1].rows[0].write, { route: "settings/serve_idle_message", etag: "sha256:2", machine: true });
  assert.deepEqual(machine[1].rows[0].control, { kind: "switch", value: false });
});

test("the controls are disabled with a reason: a stale server, a file this build cannot read, a device without config", () => {
  const stale = { ...editorDocument(), server: { kyosei: "k", stale: true, stale_detail: "older than the install" } };
  assert.match(blockedReason(stale, "config")!, /stale: older than the install.*kyo serve --restart/);
  const refused = editorDocument({ files: { config: { path: "/s", etag: "e", present: true, readable: false, problem: "worker_model: bad" } } });
  assert.match(blockedReason(refused, "config")!, /cannot read the file: worker_model: bad/);
  assert.equal(rowOf(refused, "kyosei-worker").disabled, blockedReason(refused, "config"));
  const bare = { ...editorDocument(), device: { name: "phone", permissions: ["send"], holds_config: false } };
  assert.match(blockedReason(bare, "config")!, /kyo devices grant phone --permission config/);
  assert.equal(blockedReason(editorDocument(), "config"), null);
});

test("a refusal prints the server's sentence, a 412 also says what the value now is, and a failure is not known to be written", () => {
  assert.equal(noteAfter({ status: 200, document: { ok: true }, error: null }), null);
  assert.deepEqual(noteAfter({ status: 422, document: { error: "docs_coverage.level: problem needs a code list. Add one." }, error: null }),
    { tone: "refused", keep: false, text: "docs_coverage.level: problem needs a code list. Add one." });
  const changed = noteAfter({ status: 412, document: { error: "the file changed", current: { value: "claude-opus-5-5", etag: "e2" } }, error: null })!;
  assert.equal(changed.keep, true);
  assert.equal(changed.text, "the file changed. Changed elsewhere since this view was read; it now reads claude-opus-5-5.");
  assert.equal(noteAfter({ status: 412, document: { current: { value: { model: "opus", effort: "high" } } }, error: null })!.text,
    "Changed elsewhere since this view was read; it now reads model opus, effort high.");
  const failed = noteAfter({ status: 0, document: null, error: "The server could not be reached." })!;
  assert.equal(failed.tone, "failed");
  assert.match(failed.text, /not known until the view reads the file again/);
  assert.equal(noteAfter({ status: 500, document: { error: "log-unwritable" }, error: null })!.tone, "failed");
});

test("bodies: a number is sent as a number when it is one and as typed otherwise, and an object is the body as it is", () => {
  assert.equal(numberBody(" 900 "), 900);
  assert.equal(numberBody("9.5"), "9.5");
  assert.equal(numberBody("abc"), "abc");
  assert.deepEqual(writeBody("x"), { value: "x" });
  assert.deepEqual(writeBody({ model: "m", effort: "low" }), { model: "m", effort: "low" });
  assert.equal(lastChangeText(null), null);
});

test("a request is its header and an empty line, and a box holds it when its first line is the header", () => {
  assert.equal(requestText("[kyo-config-request] trunk"), "[kyo-config-request] trunk\n\n");
  assert.equal(holdsRequest("[kyo-config-request] trunk\n\nmore", "[kyo-config-request] trunk"), true);
  assert.equal(holdsRequest("words\n[kyo-config-request] trunk", "[kyo-config-request] trunk"), false);
  // Only a row the document gives a request header has a request: serve_send and orchestrator_command have none.
  const machine = machineSections({ ...machineDocument(), settings: [
    setting({ key: "serve_send", class: "shown", file: "settings", section: "machine", value: true }),
    setting({ key: "orchestrator_command", class: "shown", file: "settings", section: "machine", value: ["claude"] }),
  ] } as Record<string, unknown>);
  assert.deepEqual(machine.flatMap((section) => section.rows).map((row) => row.requestHeader), [null, null, null]);
});

/** What the page posted to the app as a write, in order. */
const writes = (page: { run<T>(e: string): Promise<T> }) =>
  page.run<unknown[]>(`window.posted.filter((p) => p.name === "configWrite").map((p) => p.body)`);

const choose = (title: string) =>
  `[...document.querySelectorAll(".config-section-button")].find((b) => b.textContent === "${title}").click();`;

test("a control writes through its route with the file's etag; a write that took reads again and clears the row", async (t) => {
  const page = await configPage(t, { project: answered(editorDocument()), machine: answered(machineDocument()) });
  if (!page) return;
  try {
    await page.run(`(() => {
      ${choose("Workers")}
      const row = document.querySelector('.config-row[data-name="worker_model"]');
      const input = row.querySelector("input");
      input.value = "claude-opus-5-5"; input.dispatchEvent(new Event("input"));
      [...row.querySelectorAll("button")].find((b) => b.textContent === "Save").click();
    })()`);
    assert.deepEqual(await writes(page), [{
      id: "config:worker_model", project: "p", route: "config/worker_model", machine: false, etag: "sha256:1",
      body: { value: "claude-opus-5-5" },
    }]);
    // While it is out the row's controls are off.
    assert.equal(await page.run<number>(`document.querySelectorAll('.config-row[data-name="worker_model"] input:not(:disabled)').length`), 0);
    const before = await page.run<number>(`window.posted.filter((p) => p.name === "config").length`);
    const after = await page.run<{ reads: number; value: string; last: string }>(`(() => {
      window.kyo.configWritten({ id: "config:worker_model", project: "p", status: 200, error: null,
        document: { ok: true, key: "worker_model", value: "claude-opus-5-5" } });
      const doc = ${JSON.stringify(editorDocument())};
      doc.settings[0].value = "claude-opus-5-5";
      doc.settings[0].last_change = { at: "2026-11-15T21:00Z", device: "laptop", previous: "claude-sonnet-5-5", new: "claude-opus-5-5" };
      window.kyo.configRead({ project: "p", version: null, read: { status: 200, error: null, document: doc },
        machine: { status: 200, error: null, document: ${JSON.stringify(machineDocument())} } });
      const row = document.querySelector('.config-row[data-name="worker_model"]');
      return { reads: window.posted.filter((p) => p.name === "config").length,
        value: row.querySelector("input").value, last: row.querySelector(".config-last").textContent };
    })()`);
    assert.equal(after.reads, before + 1);
    assert.equal(after.value, "claude-opus-5-5");
    assert.equal(after.last, "Last changed 2026-11-15T21:00Z by laptop: claude-sonnet-5-5 to claude-opus-5-5.");
    // The number field sends a number, Clear sends null, a segment sends its option, the agent type both fields.
    await page.run(`(() => {
      const row = (n) => document.querySelector('.config-row[data-name="' + n + '"]');
      const input = row("worker_command_timeout_ceiling_seconds").querySelector("input");
      input.value = "900"; input.dispatchEvent(new Event("input"));
      [...row("worker_command_timeout_ceiling_seconds").querySelectorAll("button")].find((b) => b.textContent === "Save").click();
      [...row("worker_model").querySelectorAll("button")].find((b) => b.textContent === "Clear").click();
      const agent = row("kyosei-worker");
      const effort = agent.querySelector("select"); effort.value = "high"; effort.dispatchEvent(new Event("change"));
      [...agent.querySelectorAll("button")].find((b) => b.textContent === "Save").click();
    })()`);
    const sent = (await writes(page)).slice(1) as { route: string; body: unknown; etag: string }[];
    assert.deepEqual(sent.map((w) => [w.route, w.body, w.etag]), [
      ["config/worker_command_timeout_ceiling_seconds", { value: 900 }, "sha256:1"],
      ["config/worker_model", { value: null }, "sha256:1"],
      ["agent-types/kyosei-worker", { model: "sonnet", effort: "high" }, "sha256:agent"],
    ]);
    await page.run(`(${choose("Records and documents").replace(/;$/, "")}, document.querySelector('.config-row[data-name="docs_coverage.level"] .chip:not([aria-pressed="true"])').click())`);
    assert.deepEqual(((await writes(page)).at(-1) as { body: unknown }).body, { value: "off" });
  } finally {
    await page.close();
  }
});

test("a change that cannot be undone shows its sentence and asks before it writes", async (t) => {
  const page = await configPage(t, { project: answered(editorDocument()), machine: answered(machineDocument()) });
  if (!page) return;
  try {
    const seen = await page.run<{ warned: string; before: number; buttons: string[]; after: number }>(`(() => {
      ${choose("People and transcripts")}
      const row = () => document.querySelector('.config-row[data-name="read_transcripts"]');
      const warned = row().querySelector(".config-warning").textContent;
      [...row().querySelectorAll("button")].find((b) => b.textContent === "Turn off").click();
      const before = window.posted.filter((p) => p.name === "configWrite").length;
      const buttons = [...row().querySelectorAll(".config-control button")].map((b) => b.textContent);
      return { warned, before, buttons, after: 0 };
    })()`);
    assert.equal(seen.warned, "Turning this off cannot be undone from this view.");
    assert.equal(seen.before, 0);
    assert.deepEqual(seen.buttons, ["Confirm change", "Cancel"]);
    // Cancel sends nothing and the question goes.
    await page.run(`[...document.querySelectorAll(".config-control button")].find((b) => b.textContent === "Cancel").click()`);
    assert.equal((await writes(page)).length, 0);
    await page.run(`[...document.querySelectorAll(".config-control button")].find((b) => b.textContent === "Turn off").click();
      [...document.querySelectorAll(".config-control button")].find((b) => b.textContent === "Confirm change").click()`);
    assert.deepEqual((await writes(page)).map((w) => (w as { body: unknown }).body), [{ value: false }]);
  } finally {
    await page.close();
  }
});

test("a refused write leaves the server's value and prints the sentence; a 412 keeps what was typed and reads again", async (t) => {
  const page = await configPage(t, { project: answered(editorDocument()), machine: answered(machineDocument()) });
  if (!page) return;
  try {
    await page.run(`(() => {
      ${choose("Workers")}
      const row = document.querySelector('.config-row[data-name="worker_model"]');
      const input = row.querySelector("input");
      input.value = "bad model"; input.dispatchEvent(new Event("input"));
      [...row.querySelectorAll("button")].find((b) => b.textContent === "Save").click();
      window.kyo.configWritten({ id: "config:worker_model", project: "p", status: 422, error: null,
        document: { ok: false, error: "worker_model: a model id has no spaces. Try claude-sonnet-5-5." } });
    })()`);
    const refused = await page.run<{ text: string; value: string; tone: string }>(`(() => {
      const row = document.querySelector('.config-row[data-name="worker_model"]');
      return { text: row.querySelector(".config-refusal").textContent, value: row.querySelector("input").value,
        tone: row.querySelector(".config-refusal").dataset.tone };
    })()`);
    assert.deepEqual(refused, { text: "worker_model: a model id has no spaces. Try claude-sonnet-5-5.", value: "claude-sonnet-5-5", tone: "refused" });
    const before = await page.run<number>(`window.posted.filter((p) => p.name === "config").length`);
    const kept = await page.run<{ text: string; value: string; reads: number }>(`(() => {
      const row = () => document.querySelector('.config-row[data-name="worker_model"]');
      const input = row().querySelector("input");
      input.value = "claude-haiku-5"; input.dispatchEvent(new Event("input"));
      [...row().querySelectorAll("button")].find((b) => b.textContent === "Save").click();
      window.kyo.configWritten({ id: "config:worker_model", project: "p", status: 412, error: null,
        document: { ok: false, error: "kyosei/config.yml changed", reason: "changed", current: { value: "claude-opus-5-5", etag: "sha256:9" } } });
      return { text: row().querySelector(".config-refusal").textContent, value: row().querySelector("input").value,
        reads: window.posted.filter((p) => p.name === "config").length };
    })()`);
    assert.equal(kept.value, "claude-haiku-5");
    assert.match(kept.text, /Changed elsewhere since this view was read; it now reads claude-opus-5-5\./);
    assert.equal(kept.reads, before + 1);
  } finally {
    await page.close();
  }
});

test("Ask the session… places the request in the empty prompt box, or in a scratchpad when it holds words, and sends nothing", async (t) => {
  const page = await configPage(t, { project: answered(editorDocument()), machine: answered(machineDocument()) });
  if (!page) return;
  const header = "[kyo-config-request] worktree_setup, kyosei/config.yml lines 67-85 (setup decision D4)";
  try {
    const first = await page.run<{ boxes: string[]; mark: boolean; placed: string }>(`(() => {
      ${choose("Workers")}
      document.querySelector('.config-row[data-name="worktree_setup"] .config-ask button').click();
      const row = document.querySelector('.config-row[data-name="worktree_setup"]');
      return { boxes: [...document.querySelectorAll("#boxes .cm-content")].map((e) => e.textContent),
        mark: !row.querySelector(".config-mark").hidden, placed: row.querySelector(".config-placed").textContent };
    })()`);
    assert.deepEqual(first.boxes, [header]);
    assert.equal(first.mark, true);
    // No session is live in the stub, so the words are in the project's draft, which the sentence says.
    assert.match(first.placed, /No session is running, so it is in the project's draft/);
    assert.match(first.placed, /Nothing was sent/);
    // Pressing it again goes to the box that holds it, and adds none.
    const again = await page.run<number>(`(() => {
      document.querySelector('.config-row[data-name="worktree_setup"] .config-ask button').click();
      return document.querySelectorAll("#boxes .cm-content").length;
    })()`);
    assert.equal(again, 1);
    assert.equal(await page.run<number>(`window.posted.filter((p) => ["send", "sendNow"].includes(p.name)).length`), 0);
  } finally {
    await page.close();
  }
});

test("with words in the prompt box the request opens a scratchpad, and the row reads In the composer only while a box holds it", async (t) => {
  const page = await configPage(t, { project: answered(editorDocument()), machine: answered(machineDocument()) });
  if (!page) return;
  const header = "[kyo-config-request] worktree_setup, kyosei/config.yml lines 67-85 (setup decision D4)";
  try {
    await page.run(`(() => {
      const content = document.querySelector("#boxes .box.main .cm-content");
      content.focus();
    })()`);
    await page.type("my own words");
    const seen = await page.run<{ boxes: string[]; scratch: number; mark: boolean; main: string }>(`(() => {
      ${choose("Workers")}
      document.querySelector('.config-row[data-name="worktree_setup"] .config-ask button').click();
      const row = document.querySelector('.config-row[data-name="worktree_setup"]');
      return { boxes: [...document.querySelectorAll("#boxes .cm-content")].map((e) => e.textContent),
        scratch: document.querySelectorAll("#boxes .box.scratch").length, mark: !row.querySelector(".config-mark").hidden,
        main: document.querySelector("#boxes .box.main .cm-content").textContent };
    })()`);
    assert.equal(seen.scratch, 1);
    assert.equal(seen.main, "my own words");
    assert.ok(seen.boxes.includes(header));
    assert.equal(seen.mark, true);
    // The mark follows the box: delete the scratchpad's words and it goes.
    const gone = await page.run<Promise<boolean>>(`new Promise((done) => {
      document.querySelector("#boxes .box.scratch .box-remove, #boxes .box.scratch [data-action='delete']")?.click();
      const view = document.querySelector("#boxes .box.scratch .cm-content");
      if (view) { view.focus(); document.execCommand("selectAll"); document.execCommand("delete"); }
      setTimeout(() => done(document.querySelector('.config-row[data-name="worktree_setup"] .config-mark').hidden), 150);
    })`);
    assert.equal(gone, true);
  } finally {
    await page.close();
  }
});
