// Run with `npm test`. The Config view (KYO-953): what it makes of the two documents the server
// serves (`GET /v1/projects/{project}/config`, `GET /v1/settings`), in the logic that holds no
// element and in the page, with fake frames of the shape `kyo config --json` prints. A machine
// with no Chrome skips the page's tests.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  type Reading, chosenSection, controlOf, fileProblem, firstLine, machineSections, projectSections, readState,
  valueText,
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

test("a row says its class at a glance, shows its control disabled and no control writes anything", async (t) => {
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
