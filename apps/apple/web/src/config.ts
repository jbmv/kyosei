// The Config view (KYO-950 section 8, KYO-953): what it draws of the two documents `kyo serve`
// serves, `GET /v1/projects/{project}/config` and `GET /v1/settings`. The page holds the order
// and the titles of its sections and nothing keyed by a setting's name: each row is drawn from
// what the document says of the setting, its class and its reason included, so that a key a
// later build adds appears as a row in an older app. This build reads and writes nothing; a row
// that has a control gets the control the document's `write` descriptor describes (KYO-959): a
// control is chosen from the descriptor's value shape and never from a key's name, and it is
// drawn disabled, with the reason, when the document says that nothing can be written. A row
// the document marks as a request carries the request's own header, which the page places in
// the composer. The module holds no state and touches no element, so that it can be tested
// without a page.

/** The three classes a row is drawn in. A decision the document marks `omitted` has no row. */
export type RowClass = "shown" | "editable" | "request";

/** What one read of a document came to, as the app sends it: the status, the body, the refusal. */
export interface Reading {
  status: number;
  document: unknown;
  error: string | null;
}

/** What the app sends when either document was read. */
export interface ConfigFrame {
  project?: string;
  /** The server's version from `/v1/version`, or null when it was not read. */
  version?: string | null;
  /** The project's document, or null before it was read. */
  read?: Reading | null;
  /** The machine's document, or null before it was read. */
  machine?: Reading | null;
}

/** One check of `kyo onboard`: present, missing, behind, and so on, with what to do about it. */
export interface Status {
  status: string;
  detail: string | null;
  fix: string | null;
  label?: string;
}

/** One field of an object-valued write: a text field, or a menu when the descriptor lists values. */
export interface FieldView { name: string; value: string; options: string[] | null }

/** The control a row has, from the shape of the value its `write` descriptor names. */
export type Control =
  | { kind: "text"; value: string; clearable: boolean }
  | { kind: "number"; value: string; clearable: boolean }
  | { kind: "segments"; options: string[]; value: string | null }
  | { kind: "switch"; value: boolean | null }
  | { kind: "turn-off"; value: boolean | null }
  | { kind: "fields"; fields: FieldView[] };

/** Where a row's control writes: the route under the project (or the server), and the file's etag. */
export interface WriteSpec {
  route: string;
  /** The etag the document gave for the file the setting is in, sent as `If-Match`. */
  etag: string | null;
  /** Whether the route is the server's (`settings/...`) and not a project's. */
  machine: boolean;
}

export interface RowView {
  /** A key of the document, or the title of a decision: the name the row is drawn under. */
  name: string;
  /** The decision of `docs/guide/setup-decisions.md` the row answers, or null. */
  decision: string | null;
  klass: RowClass;
  /** The class in words, which says when a shown row can also be asked for. */
  label: string;
  /** The reason for the class, in the document's own sentence. */
  why: string;
  /** Whether the document has a value to draw; a decision holds none. */
  hasValue: boolean;
  /** The value as text, or null when the setting is not set. */
  value: string | null;
  /** What the absence of the setting means, from the document. */
  absent: string | null;
  /** Whether the value is drawn as a block, since it is more than one line. */
  block: boolean;
  /** The comment on the value's own line, kept after the value. */
  trailing: string | null;
  statuses: Status[];
  /** The comment above the key, one string for each line, verbatim. */
  comment: string[];
  /** The first line of the comment, which is all that is drawn while it is collapsed. */
  first: string | null;
  control: Control | null;
  write: WriteSpec | null;
  /** Why the control is disabled, or null while it can be used. */
  disabled: string | null;
  /** The document's latest change of the setting, in words, or null when none was logged. */
  lastChange: string | null;
  /** The first line of a request about this row, from the document; null offers no request. */
  requestHeader: string | null;
  /** The sentence to show before a change that cannot be undone, from the document. */
  oneWay: string | null;
  /** The sentence for a request row: how it is changed. */
  how: string | null;
  /** A stable name for keeping a row's state across a redraw. */
  id: string;
}

export interface Fact { label: string; text: string; tone?: "ok" | "warn" }
export interface Listing { label: string; names: string[] }

export interface SectionView {
  id: string;
  title: string;
  rows: RowView[];
  /** Facts drawn as lines above the rows: a server's build, a device's name. */
  facts: Fact[];
  /** The files beside `config.yml`, by name. */
  listings: Listing[];
}

/** The classes in the order the legend names them. */
export const CLASS_LABELS: Record<RowClass, string> = {
  shown: "Shown only",
  editable: "Editable here",
  request: "Request",
};

/** What each class means, drawn once above a section's rows. */
export const CLASS_MEANINGS: Record<RowClass, string> = {
  shown: "read from the project, with no way to change it from this view",
  editable: "changed here, by the control the row carries",
  request: "changed by asking the session, which edits the file",
};

/** The titles and the order of the project's sections; an id the document adds follows them. */
export const PROJECT_SECTIONS: { id: string; title: string }[] = [
  { id: "identity", title: "Store and identity" },
  { id: "workers", title: "Workers" },
  { id: "docs", title: "Records and documents" },
  { id: "people", title: "People and transcripts" },
  { id: "prior-art", title: "Prior art" },
  { id: "notices", title: "Notices and onboarding" },
  { id: "decisions", title: "Setup decisions" },
];

/** The titles and the order of the machine's sections. */
export const MACHINE_SECTIONS: { id: string; title: string }[] = [
  { id: "this-machine", title: "This machine" },
  { id: "machine", title: "Machine settings" },
  { id: "device", title: "This device" },
];

const TOKEN_REMEDY =
  "Issue one on the server's machine with `kyo devices issue <name>` and enter it in Settings.";

/** What a reading comes to: the document, or the words that say why there is none. */
export type ReadState =
  | { kind: "waiting" }
  | { kind: "ok"; document: Record<string, unknown> }
  | { kind: "failed"; why: "no-token" | "no-route" | "unreachable" | "refused"; message: string };

/**
 * The state of one reading (section 8.3). A 401 is a missing, unknown or revoked token, and the
 * server's own sentence is printed with how a token is issued. A 404 is a server older than the
 * routes, which is named by its version. No answer, and any other refusal, print what is known.
 */
export function readState(read: Reading | null | undefined, version: string | null | undefined): ReadState {
  if (!read) return { kind: "waiting" };
  if (read.status === 200 && read.document && typeof read.document === "object") {
    return { kind: "ok", document: read.document as Record<string, unknown> };
  }
  if (read.status === 401) {
    const said = read.error ? `${read.error}. ` : "";
    return {
      kind: "failed", why: "no-token",
      message: `The server did not show this device its configuration. ${said}${TOKEN_REMEDY}`,
    };
  }
  if (read.status === 404) {
    const named = version ? `This server is ${version}. ` : "";
    return {
      kind: "failed", why: "no-route",
      message: `This server's build has no configuration route, so there is nothing to show. ${named}`
        + "Update the server's kyo and restart it.",
    };
  }
  if (read.status === 0) {
    return { kind: "failed", why: "unreachable", message: read.error || "The server could not be reached." };
  }
  return {
    kind: "failed", why: "refused",
    message: read.error || `The server answered ${read.status} and no configuration.`,
  };
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === "string") : [];
}

function scalar(value: unknown): boolean {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

/**
 * A list or mapping as indented lines, the way the file itself writes one: `- ` before a
 * list's entry and `key: ` before a mapping's. It is for reading and is not parsed back.
 */
function outline(value: unknown, indent: string): string[] {
  if (Array.isArray(value)) {
    if (value.length === 0) return [`${indent}[]`];
    return value.flatMap((entry) => {
      if (scalar(entry)) return [`${indent}- ${String(entry)}`];
      const [first, ...rest] = outline(entry, `${indent}  `);
      return [`${indent}- ${first!.trimStart()}`, ...rest];
    });
  }
  const entries = Object.entries(record(value));
  if (entries.length === 0) return [`${indent}{}`];
  return entries.flatMap(([key, entry]) => {
    if (scalar(entry)) return [`${indent}${key}: ${String(entry)}`];
    if ((Array.isArray(entry) && entry.length === 0) || (!Array.isArray(entry) && Object.keys(record(entry)).length === 0)) {
      return [`${indent}${key}: ${Array.isArray(entry) ? "[]" : "{}"}`];
    }
    return [`${indent}${key}:`, ...outline(entry, `${indent}  `)];
  });
}

/** A value as the text a row draws, and whether that text runs over more than a line. */
export function valueText(value: unknown): { text: string; block: boolean } | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return { text: value, block: value.includes("\n") };
  if (typeof value === "number" || typeof value === "boolean") return { text: String(value), block: false };
  // One line only for a list of single words, such as an argument list: entries that hold a
  // space are commands, and joined by spaces they could not be told apart.
  if (Array.isArray(value) && value.every((entry) => typeof entry === "string" && !/\s/.test(entry))) {
    const joined = value.join(" ");
    if (joined.length <= 80) return { text: joined, block: false };
  }
  return { text: outline(value, "").join("\n"), block: true };
}

export function firstLine(comment: string[]): string | null {
  return comment.length ? comment[0] : null;
}

export function classLabel(klass: RowClass, requestAlso: boolean): string {
  return requestAlso && klass === "shown" ? "Shown only, or by request" : CLASS_LABELS[klass];
}

function classOf(value: unknown): RowClass | null {
  return value === "shown" || value === "editable" || value === "request" ? value : null;
}

/** The fields of an object-valued write: each entry of the shape that describes a value. */
function fieldsOf(shape: Record<string, unknown>, now: Record<string, unknown>): FieldView[] {
  return Object.entries(shape)
    .filter(([, entry]) => entry && typeof entry === "object" && !Array.isArray(entry))
    .map(([name, entry]) => {
      const options = strings(record(entry).enum);
      const value = now[name];
      return { name, value: value === null || value === undefined ? "" : String(value), options: options.length ? options : null };
    });
}

/**
 * The control a row has, from the document's own description of how it is written: a value
 * that names its options is a segmented choice, a boolean is a switch (or Turn off when only
 * false is accepted), a whole number a number field, a string a text field, and an object
 * one field to each of its parts. `now` is the object's current parts, for the last.
 */
export function controlOf(write: unknown, value: unknown, now: Record<string, unknown> = {}): Control | null {
  const shape = record(record(write).value);
  const clearable = record(write).clearable === true;
  const options = strings(shape.enum);
  if (options.length) return { kind: "segments", options, value: text(value) };
  switch (shape.type) {
    case "boolean": {
      const allowed = Array.isArray(shape.values) ? shape.values : [];
      const current = typeof value === "boolean" ? value : null;
      return allowed.length === 1 && allowed[0] === false
        ? { kind: "turn-off", value: current }
        : { kind: "switch", value: current };
    }
    case "integer":
      return { kind: "number", value: value === null || value === undefined ? "" : String(value), clearable };
    case "string":
      return { kind: "text", value: text(value) ?? "", clearable };
    case "object": {
      const fields = fieldsOf(shape, now);
      return fields.length ? { kind: "fields", fields } : null;
    }
    default:
      return null;
  }
}

/** A value as the log of a change prints it. */
function loggedValue(value: unknown): string {
  if (value === null || value === undefined) return "not set";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

/** The document's latest change of a setting, in one sentence, or null when none was logged. */
export function lastChangeText(change: unknown): string | null {
  const found = record(change);
  const at = text(found.at);
  if (!at) return null;
  const who = text(found.device);
  return `Last changed ${at}${who ? ` by ${who}` : ""}: ${loggedValue(found.previous)} to ${loggedValue(found.new)}.`;
}

/**
 * Why a document's controls cannot be used, or null when they can. A stale server refuses every
 * write; a file this build cannot read is shown from its raw text and nothing is written to it;
 * a device without the `config` permission is refused by the server and is told how to get it.
 */
export function blockedReason(document: Record<string, unknown>, file: string): string | null {
  const server = record(document.server);
  if (server.stale === true) {
    const detail = text(server.stale_detail) ?? "a newer build is installed than the one this server runs";
    return `The server is stale: ${detail.replace(/[.\s]*$/, "")}. Restart it with \`kyo serve --restart\` and the controls work again.`;
  }
  const problem = fileProblem(document, file);
  if (problem) return `This build cannot read the file: ${problem.replace(/[.\s]*$/, "")}. Nothing is written to it until it reads.`;
  const device = record(document.device);
  if (device.holds_config !== true) {
    const name = text(device.name) ?? "<device>";
    return `This device does not hold the config permission. On the server's machine run \`kyo devices grant ${name} --permission config\`.`;
  }
  return null;
}

function statusOf(value: unknown, label?: string): Status | null {
  const found = record(value);
  const status = text(found.status);
  if (!status) return null;
  return { status, detail: text(found.detail), fix: text(found.fix), label: label ?? text(found.label) ?? undefined };
}

function settingRow(
  setting: Record<string, unknown>, file: string, etag: string | null, blocked: string | null,
): RowView | null {
  const klass = classOf(setting.class);
  const name = text(setting.key);
  if (!klass || !name) return null;
  const present = setting.present === true;
  const shown = present ? valueText(setting.value) : null;
  const comment = strings(setting.comment);
  const status = statusOf(setting.status);
  const control = klass === "editable" ? controlOf(setting.write, present ? setting.value : null) : null;
  const route = text(record(setting.write).route);
  const trailing = text(setting.trailing);
  return {
    name,
    decision: text(setting.decision),
    klass,
    label: classLabel(klass, false),
    why: text(setting.why) ?? "",
    hasValue: true,
    value: shown?.text ?? null,
    absent: text(setting.absent_means),
    block: shown?.block ?? false,
    trailing: trailing && trailing.trim() ? trailing : null,
    statuses: status ? [status] : [],
    comment,
    first: firstLine(comment),
    control,
    write: control && route ? { route, etag, machine: route.startsWith("settings/") } : null,
    disabled: control ? blocked : null,
    lastChange: lastChangeText(setting.last_change),
    requestHeader: text(record(setting.request).header),
    oneWay: text(record(setting.write).one_way),
    how: klass === "request" ? "Changed by asking the session, which edits the file." : null,
    id: `${file}:${name}`,
  };
}

function decisionRow(decision: Record<string, unknown>, file: string): RowView | null {
  const klass = classOf(decision.class);
  const name = text(decision.title);
  const id = text(decision.id);
  if (!klass || !name || !id) return null;
  const alsoAsked = decision.request_also === true;
  const statuses = (Array.isArray(decision.status) ? decision.status : [])
    .map((entry) => statusOf(entry)).filter((entry): entry is Status => entry !== null);
  return {
    name,
    decision: id,
    klass,
    label: classLabel(klass, alsoAsked),
    why: text(decision.why) ?? "",
    hasValue: false,
    value: null,
    absent: null,
    block: false,
    trailing: null,
    statuses,
    comment: [],
    first: null,
    control: null,
    write: null,
    disabled: null,
    lastChange: null,
    requestHeader: text(record(decision.request).header),
    oneWay: null,
    how: klass === "request" || alsoAsked ? "Changed by asking the session." : null,
    id: `${file}:decision:${id}`,
  };
}

function agentRow(agent: Record<string, unknown>, blocked: string | null): RowView | null {
  const name = text(agent.name);
  const klass = classOf(agent.class);
  if (!name || !klass) return null;
  const model = text(agent.model);
  const effort = text(agent.effort);
  const status = statusOf(agent.status);
  const control = klass === "editable" ? controlOf(agent.write, null, agent) : null;
  const route = text(record(agent.write).route);
  return {
    name,
    decision: null,
    klass,
    label: classLabel(klass, false),
    why: "An agent type of the code checkout, which a dispatch of that type runs as.",
    hasValue: true,
    value: [model ? `model ${model}` : null, effort ? `effort ${effort}` : null].filter(Boolean).join(", ") || null,
    absent: "the file names neither",
    block: false,
    trailing: null,
    statuses: status ? [status] : [],
    comment: [],
    first: null,
    control,
    write: control && route ? { route, etag: text(agent.etag), machine: false } : null,
    disabled: control ? blocked : null,
    lastChange: lastChangeText(agent.last_change),
    requestHeader: text(record(agent.request).header),
    oneWay: text(record(agent.write).one_way),
    how: klass === "request" ? "Changed by asking the session." : null,
    id: `agent:${name}`,
  };
}

/** The names a `beside` entry lists, each with its other non-empty fields when it is a mapping. */
function namesOf(entries: unknown): string[] {
  return (Array.isArray(entries) ? entries : []).map((entry) => {
    if (typeof entry === "string") return entry;
    const found = record(entry);
    const [first, ...rest] = Object.entries(found).filter(([, value]) => value !== null && value !== undefined);
    if (!first) return "";
    const extra = rest.map(([key, value]) => `${key.replace(/_/g, " ")} ${String(value)}`).join(", ");
    return extra ? `${String(first[1])} (${extra})` : String(first[1]);
  }).filter(Boolean);
}

function emptySection(id: string, title: string): SectionView {
  return { id, title, rows: [], facts: [], listings: [] };
}

function titled(order: { id: string; title: string }[], extra: string[]): SectionView[] {
  const sections = order.map((entry) => emptySection(entry.id, entry.title));
  for (const id of extra) if (!sections.some((section) => section.id === id)) sections.push(emptySection(id, id));
  return sections;
}

function place(sections: SectionView[], id: string): SectionView {
  return sections.find((section) => section.id === id) ?? sections[sections.length - 1];
}

/** The status of a file the document reads: its refusal, when it is one this build refuses. */
export function fileProblem(document: Record<string, unknown>, name: string): string | null {
  const file = record(record(document.files)[name]);
  return file.readable === false ? text(file.problem) ?? "This build cannot read the file." : null;
}

function serverFacts(document: Record<string, unknown>): Fact[] {
  const server = record(document.server);
  const facts: Fact[] = [];
  const version = text(server.kyosei);
  if (version) facts.push({ label: "Server", text: version });
  if (server.stale === true) {
    facts.push({
      label: "Stale", tone: "warn",
      text: text(server.stale_detail) ?? "The server runs an older build than the one installed.",
    });
  }
  return facts;
}

/** The project's sections, drawn from its document. Sections that hold nothing are dropped. */
export function projectSections(document: Record<string, unknown>): SectionView[] {
  const settings = (Array.isArray(document.settings) ? document.settings : []).map(record);
  const known = new Set(PROJECT_SECTIONS.map((section) => section.id));
  const sections = titled(PROJECT_SECTIONS, [...new Set(settings.map((entry) => text(entry.section)).filter(
    (id): id is string => id !== null && !known.has(id)))]);
  const blocked = blockedReason(document, "config");
  const etag = text(record(record(document.files).config).etag);
  for (const entry of settings) {
    const row = settingRow(entry, text(entry.file) ?? "config", etag, blocked);
    if (row) place(sections, text(entry.section) ?? "identity").rows.push(row);
  }
  for (const entry of (Array.isArray(document.agent_types) ? document.agent_types : []).map(record)) {
    const row = agentRow(entry, blocked);
    if (row) place(sections, "workers").rows.push(row);
  }
  const beside = record(document.beside);
  const docs = place(sections, "docs");
  for (const [key, entries] of Object.entries(beside)) {
    const names = namesOf(entries);
    if (names.length) docs.listings.push({ label: key.replace(/_/g, " "), names });
  }
  const decisions = place(sections, "decisions");
  for (const entry of (Array.isArray(document.decisions) ? document.decisions : []).map(record)) {
    if (entry.class === "omitted") continue;
    const row = decisionRow(entry, "config");
    if (row) decisions.rows.push(row);
  }
  const fact = serverFacts(document);
  if (fact.length) place(sections, "identity").facts.push(...fact);
  return sections.filter((section) => section.rows.length || section.listings.length || section.facts.length);
}

/** The machine's sections, and the device's, drawn from the machine's document. */
export function machineSections(document: Record<string, unknown>): SectionView[] {
  const sections = titled(MACHINE_SECTIONS, []);
  const settings = (Array.isArray(document.settings) ? document.settings : []).map(record);
  const blocked = blockedReason(document, "settings");
  const etag = text(record(record(document.files).settings).etag);
  for (const entry of settings) {
    const row = settingRow(entry, text(entry.file) ?? "settings", etag, blocked);
    if (row) place(sections, "machine").rows.push(row);
  }
  for (const entry of (Array.isArray(document.decisions) ? document.decisions : []).map(record)) {
    if (entry.class === "omitted") continue;
    const row = decisionRow(entry, "settings");
    if (row) place(sections, "this-machine").rows.push(row);
  }
  place(sections, "this-machine").facts.push(...serverFacts(document));
  const device = record(document.device);
  const name = text(device.name);
  if (name) {
    const permissions = strings(device.permissions);
    const device_ = place(sections, "device");
    device_.facts.push({ label: "Device", text: name });
    device_.facts.push({ label: "Permissions", text: permissions.length ? permissions.join(", ") : "none" });
    device_.facts.push({
      label: "Config permission",
      text: permissions.includes("config")
        ? "held, which the write routes will need"
        : "not held; a device is given it on the server's machine with `kyo devices grant <device> --permission config`",
    });
  }
  return sections.filter((section) => section.rows.length || section.listings.length || section.facts.length);
}

/** The section to show: the one chosen when it is still there, and otherwise the first. */
export function chosenSection(sections: SectionView[], wanted: string | null): SectionView | null {
  return sections.find((section) => section.id === wanted) ?? sections[0] ?? null;
}

// ---------------------------------------------------------------- writing (KYO-959)

/** What a write came to, as the app sends it: the status, the server's body and a failure. */
export interface WriteResult {
  status: number;
  /** The server's JSON body, whether it was a success or a refusal. */
  document: unknown;
  /** The refusal's sentence, when the server gave one, or why no answer came. */
  error: string | null;
}

/** What is printed under a control after a write that did not take, and what the field keeps. */
export interface RowNote {
  /** `refused` for an answer that changed nothing, `failed` for one that may have. */
  tone: "refused" | "failed";
  text: string;
  /** The attempted value, kept in the field after a 412; null puts the server's value back. */
  keep: boolean;
}

/** A value as one line of a sentence: an object by its parts, which an agent type's is. */
export function plainValue(value: unknown): string {
  if (value === null || value === undefined) return "not set";
  if (typeof value === "object" && !Array.isArray(value)) {
    return Object.entries(record(value)).map(([key, part]) => `${key} ${plainValue(part)}`).join(", ");
  }
  return Array.isArray(value) ? value.join(", ") : String(value);
}

/** A whole number as the route takes it. Anything else is sent as typed, for the server to refuse. */
export function numberBody(input: string): number | string {
  const trimmed = input.trim();
  return /^-?\d+$/.test(trimmed) ? Number(trimmed) : input;
}

/** The body of a write from the control's parts; one value, or an object for a row of fields. */
export function writeBody(value: unknown): unknown {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : { value };
}

/**
 * What to print under the control after a write, or null when it was written. A refusal is
 * the server's own sentence, which names its remedy. A 412 also says what the value now is,
 * from the `current` the server returned, and keeps what was attempted in the field. Any
 * other answer (no answer, a 5xx) is a failure, and the row is not known to be written.
 */
export function noteAfter(result: WriteResult): RowNote | null {
  if (result.status === 200) return null;
  const body = record(result.document);
  const said = result.error ?? text(body.error);
  if (result.status === 412) {
    const now = plainValue(record(body.current).value);
    return {
      tone: "refused", keep: true,
      text: `${said ? `${said.replace(/[.\s]*$/, "")}. ` : ""}Changed elsewhere since this view was read; it now reads ${now}.`,
    };
  }
  if (result.status >= 400 && result.status < 500) {
    return { tone: "refused", keep: false, text: said ?? `The server answered ${result.status}.` };
  }
  return {
    tone: "failed", keep: true,
    text: `${said ?? `The server answered ${result.status}.`} Whether the change was written is not known until the view reads the file again.`,
  };
}

/** The request's text for the composer: its header, a line break and an empty line. */
export function requestText(header: string): string {
  return `${header}\n\n`;
}

/** Whether a box's text is the request with this header: its first line is the header. */
export function holdsRequest(boxText: string, header: string): boolean {
  return boxText.split("\n", 1)[0] === header;
}
