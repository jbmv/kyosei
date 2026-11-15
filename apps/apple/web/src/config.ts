// The Config view (KYO-950 section 8, KYO-953): what it draws of the two documents `kyo serve`
// serves, `GET /v1/projects/{project}/config` and `GET /v1/settings`. The page holds the order
// and the titles of its sections and nothing keyed by a setting's name: each row is drawn from
// what the document says of the setting, its class and its reason included, so that a key a
// later build adds appears as a row in an older app. This build reads and writes nothing; a row
// that will have a control says which, and the control is drawn disabled. The module holds no
// state and touches no element, so that it can be tested without a page.

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

/** The control a row will have, drawn disabled in this build. */
export type Control =
  | { kind: "text"; value: string; clearable: boolean }
  | { kind: "number"; value: string; clearable: boolean }
  | { kind: "segments"; options: string[]; value: string | null }
  | { kind: "switch"; value: boolean | null }
  | { kind: "turn-off"; value: boolean | null }
  | { kind: "fields"; fields: { label: string; value: string }[] };

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
  editable: "will have a control here; the control is not live in this build",
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

/** The control a row will have, from the document's own description of how it is written. */
export function controlOf(write: unknown, value: unknown): Control | null {
  const shape = record(record(write).value);
  const clearable = record(write).clearable === true;
  const options = strings(shape.enum);
  if (options.length) return { kind: "segments", options, value: text(value) };
  switch (shape.type) {
    case "boolean": {
      const allowed = Array.isArray(shape.values) ? shape.values : [];
      const now = typeof value === "boolean" ? value : null;
      return allowed.length === 1 && allowed[0] === false
        ? { kind: "turn-off", value: now }
        : { kind: "switch", value: now };
    }
    case "integer":
      return { kind: "number", value: value === null || value === undefined ? "" : String(value), clearable };
    case "string":
      return { kind: "text", value: text(value) ?? "", clearable };
    default:
      return null;
  }
}

function statusOf(value: unknown, label?: string): Status | null {
  const found = record(value);
  const status = text(found.status);
  if (!status) return null;
  return { status, detail: text(found.detail), fix: text(found.fix), label: label ?? text(found.label) ?? undefined };
}

function settingRow(setting: Record<string, unknown>, file: string): RowView | null {
  const klass = classOf(setting.class);
  const name = text(setting.key);
  if (!klass || !name) return null;
  const present = setting.present === true;
  const shown = present ? valueText(setting.value) : null;
  const comment = strings(setting.comment);
  const status = statusOf(setting.status);
  const control = klass === "editable" ? controlOf(setting.write, present ? setting.value : null) : null;
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
    oneWay: null,
    how: klass === "request" || alsoAsked ? "Changed by asking the session." : null,
    id: `${file}:decision:${id}`,
  };
}

function agentRow(agent: Record<string, unknown>): RowView | null {
  const name = text(agent.name);
  const klass = classOf(agent.class);
  if (!name || !klass) return null;
  const model = text(agent.model);
  const effort = text(agent.effort);
  const status = statusOf(agent.status);
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
    control: klass === "editable"
      ? { kind: "fields", fields: [{ label: "model", value: model ?? "" }, { label: "effort", value: effort ?? "" }] }
      : null,
    oneWay: null,
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
  for (const entry of settings) {
    const row = settingRow(entry, text(entry.file) ?? "config");
    if (row) place(sections, text(entry.section) ?? "identity").rows.push(row);
  }
  for (const entry of (Array.isArray(document.agent_types) ? document.agent_types : []).map(record)) {
    const row = agentRow(entry);
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
  for (const entry of settings) {
    const row = settingRow(entry, text(entry.file) ?? "settings");
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
