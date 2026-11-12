// The Records view's logic (KYO-732): which records a list shows, how they are grouped, what
// was viewed recently, which words on a page are record ids, and what a record page says. It
// holds no state and touches no element, so that it can be tested without a page. The
// documents it reads are `kyo list --json`, `kyo next --json` and `kyo show --json`, as
// `kyo serve` returns them.

/** One row of `kyo list --json`. */
export interface RecordRow {
  id: string;
  kind: string;
  title: string;
  status: string;
  /** The status as `kyo list` states it: `current` or `void` for a knowledge record. */
  standing?: string;
  seq?: number;
  created_at?: string;
  updated_at?: string;
}

/**
 * The status a record is shown and filtered by: its standing where the list gives one. A
 * decision is stored `open` and stands `current`, so by its stored status it would count as
 * open work (KYO-862).
 */
export function standingOf(row: RecordRow): string {
  return row.standing || row.status || "";
}

/** What narrows a list: the three filters and the search box. */
export interface Narrowing {
  open: boolean;
  dispatchable: boolean;
  awaiting: boolean;
  search: string;
}

/**
 * The records with the one filed last first (KYO-759). A record's number is the order it was
 * made in a scope, and scopes number apart, so the time it was filed leads; the number only
 * settles records filed together, or rows that carry no time.
 */
export function newestFirst<T extends RecordRow>(rows: T[]): T[] {
  return [...rows].sort((a, b) => {
    const byTime = (b.created_at ?? "").localeCompare(a.created_at ?? "");
    if (byTime) return byTime;
    return (b.seq ?? 0) - (a.seq ?? 0);
  });
}

/** The ids `kyo next` reports as ready to dispatch now. */
export function dispatchableIds(next: unknown): Set<string> {
  const ready = (next as { ready?: unknown } | null)?.ready;
  const ids = new Set<string>();
  if (!Array.isArray(ready)) return ids;
  for (const row of ready) {
    const id = (row as { id?: unknown } | null)?.id;
    if (typeof id === "string") ids.add(id);
  }
  return ids;
}

/** The gate types that wait on the person. */
export const AWAITING_TYPES = ["awaiting:human-run", "awaiting:human-review"];

/**
 * The ids of the records with an open gate waiting on the person, from `kyo gates --json`,
 * which lists open gates only. A gate of another type is not counted, whatever was asked for.
 */
export function awaitingIds(gates: unknown): Set<string> {
  const rows = (gates as { gates?: unknown } | null)?.gates;
  const ids = new Set<string>();
  if (!Array.isArray(rows)) return ids;
  for (const row of rows) {
    const gate = row as { record_id?: unknown; type?: unknown } | null;
    if (typeof gate?.record_id === "string" && AWAITING_TYPES.includes(String(gate.type))) {
      ids.add(gate.record_id);
    }
  }
  return ids;
}

/**
 * The filters after one chip is pressed. Awaiting you is about work already dispatched, so
 * it never stands with Open or Dispatchable: turning it on turns those two off, and turning
 * either of them on turns it off. Open and Dispatchable combine with each other.
 */
export function pressed<T extends { open: boolean; dispatchable: boolean; awaiting: boolean }>(
  filters: T, name: "open" | "dispatchable" | "awaiting",
): T {
  const next = { ...filters, [name]: !filters[name] };
  if (name === "awaiting" && next.awaiting) {
    next.open = false;
    next.dispatchable = false;
  }
  if (name !== "awaiting" && next[name]) next.awaiting = false;
  return next;
}

/** A `kind:plan` or `kind:plan,task` token, anywhere in the search text (KYO-785). */
const KIND_TOKEN = /(^|\s)kind:(\S*)/gi;

/** The kinds the search text names with `kind:` tokens, lower case, each once, in the order typed. */
export function kindsOf(search: string): string[] {
  const kinds: string[] = [];
  for (const match of search.matchAll(KIND_TOKEN)) {
    for (const kind of match[2].toLowerCase().split(",")) {
      if (kind && !kinds.includes(kind)) kinds.push(kind);
    }
  }
  return kinds;
}

/** The search text without its `kind:` tokens: what is searched for as words. */
export function textOf(search: string): string {
  return search.replace(KIND_TOKEN, "$1");
}

/**
 * The search text with its `kind:` tokens replaced by one naming `kinds`, put first; none
 * removes the token. A text that is only the token keeps a space after it, so that what is
 * typed next is words and not part of the last kind.
 */
export function withKinds(search: string, kinds: string[]): string {
  const rest = textOf(search).replace(/^\s+/, "");
  if (!kinds.length) return rest;
  return `kind:${kinds.join(",")} ${rest}`;
}

function searchWords(search: string): string[] {
  return textOf(search).toLowerCase().split(/\s+/).filter(Boolean);
}

/** The kinds of `rows`, each once, in alphabetical order. */
export function kindsPresent(rows: RecordRow[]): string[] {
  return [...new Set(rows.map((row) => row.kind).filter(Boolean))].sort();
}

/** What the Kind control reads: the kinds checked, and whether any of them is no kind of the store. */
export function kindControlText(search: string, present: string[]): { text: string; active: boolean; unknown: string[] } {
  const kinds = kindsOf(search);
  const unknown = kinds.filter((kind) => !present.includes(kind));
  if (!kinds.length) return { text: "Kind", active: false, unknown };
  const named = kinds.join(", ");
  return { text: `Kind: ${named}${unknown.length ? " (no such kind)" : ""}`, active: true, unknown };
}

/** Whether every word typed is found in the record's id or its title. */
export function matchesSearch(row: RecordRow, search: string): boolean {
  const id = row.id.toLowerCase();
  const title = row.title.toLowerCase();
  return searchWords(search).every((word) => id.includes(word) || title.includes(word));
}

/**
 * The rows the filters and the search leave, in the order given. Open keeps the records
 * whose status is `open`; Dispatchable keeps those in `ready`; Awaiting you keeps those in
 * `awaiting`; all of them combine.
 */
export function narrow<T extends RecordRow>(
  rows: T[], narrowing: Narrowing, ready: ReadonlySet<string>,
  awaiting: ReadonlySet<string> = new Set(),
): T[] {
  const kinds = kindsOf(narrowing.search);
  return rows.filter((row) =>
    (!kinds.length || kinds.includes(row.kind.toLowerCase()))
    && (!narrowing.open || standingOf(row) === "open")
    && (!narrowing.dispatchable || ready.has(row.id))
    && (!narrowing.awaiting || awaiting.has(row.id))
    && matchesSearch(row, narrowing.search));
}

/** How many viewed records are remembered. */
export const HISTORY_LIMIT = 30;

/** The history after `id` is viewed: it comes first, and appears once. */
export function visit(history: string[], id: string, limit = HISTORY_LIMIT): string[] {
  return [id, ...history.filter((other) => other !== id)].slice(0, limit);
}

/** A stored history as a list of ids, whatever was stored. */
export function historyFrom(stored: unknown): string[] {
  if (!Array.isArray(stored)) return [];
  return stored.filter((id): id is string => typeof id === "string").slice(0, HISTORY_LIMIT);
}

/** History rows, most recent first. A record the list no longer holds is named by its id. */
export function historyRows(history: string[], rows: RecordRow[]): RecordRow[] {
  const byId = new Map(rows.map((row) => [row.id, row]));
  return history.map((id) => byId.get(id) ?? { id, kind: "", title: "", status: "" });
}

// ------------------------------------------------------------------------------ record ids

// An entry of a report has a second part: `KYO-740.r1.p1` (KYO-757).
const REF = /^([A-Z][A-Z0-9]*-\d+)(\.[a-z]+\d+){0,2}$/;

/**
 * The record an id belongs to: `KYO-732` for `KYO-732.c1` and for `KYO-732.r1.p1`. Null when
 * it is not an id.
 */
export function recordOf(ref: string): string | null {
  return REF.exec(ref)?.[1] ?? null;
}

/** The prefix of a record id: `KYO` for `KYO-732`. */
export function prefixOf(id: string): string | null {
  return /^([A-Z][A-Z0-9]*)-\d+/.exec(id)?.[1] ?? null;
}

export interface LinkTarget {
  /** The record to open. */
  record: string;
  /** The id the link named, which may be an element of the record such as a criterion. */
  ref: string;
}

/** A `kyosei://<project>/<id>` link's target, or null when it names no record. */
export function parseLink(href: string): LinkTarget | null {
  const found = /^kyosei:\/\/[^/?#]*\/([^/?#]+)/i.exec(href.trim());
  if (!found) return null;
  let ref: string;
  try {
    ref = decodeURIComponent(found[1]);
  } catch {
    return null;
  }
  const record = recordOf(ref);
  return record ? { record, ref } : null;
}

export interface TextPart {
  text: string;
  /** Set when the text is an id: the record to open and the id itself. */
  link?: LinkTarget;
}

/**
 * A text split at the record ids in it. Only ids with one of `prefixes` count, so that
 * `UTF-8` and `SHA-256` stay words.
 */
export function refParts(text: string, prefixes: Iterable<string>): TextPart[] {
  const known = [...new Set(prefixes)].filter((prefix) => /^[A-Z][A-Z0-9]*$/.test(prefix));
  if (!known.length || !text) return text ? [{ text }] : [];
  const pattern = new RegExp(
    `(?<![A-Za-z0-9-])(?:${known.join("|")})-\\d+(?:\\.[a-z]+\\d+){0,2}(?![A-Za-z0-9])`, "g");
  const parts: TextPart[] = [];
  let from = 0;
  for (const match of text.matchAll(pattern)) {
    const record = recordOf(match[0]);
    if (!record) continue;
    if (match.index > from) parts.push({ text: text.slice(from, match.index) });
    parts.push({ text: match[0], link: { record, ref: match[0] } });
    from = match.index + match[0].length;
  }
  if (from < text.length) parts.push({ text: text.slice(from) });
  return parts;
}

// ---------------------------------------------------------------------------- a record page

/** How an item's state is coloured: green for what runs now, and quiet for what is past. */
export type Tone = "running" | "quiet" | "plain";

export interface Line {
  label: string;
  text: string;
  /** A heading drawn above this line, where a new group of lines begins. */
  group?: string;
  /**
   * The id the line can be anchored by, as a criterion's can: the id of a report's false
   * premise, disagreement, corrected number or verdict (KYO-757).
   */
  ref?: string;
}

export interface Item {
  /** The item's id. It is drawn as text, and names the item for a citation. */
  ref?: string;
  /** Whether the id is one a prompt can cite: a criterion, a gate, a dispatch or a note. */
  citable?: boolean;
  state?: string;
  tone?: Tone;
  /** Short facts beside the id. */
  meta?: string[];
  text?: string;
  lines?: Line[];
  /** Long text shown folded, such as a dispatch's brief. */
  folded?: Line[];
  /** Set when the item's lines are drawn folded under this label, as a superseded report's. */
  collapsed?: string;
  /** A file the item names in the attachment store, drawn where the item is (KYO-856). */
  attached?: Attached;
}

/** A composer attachment that evidence names: its id, its session and its file's name. */
export interface Attached {
  id: string;
  session: string;
  name: string;
  extension: string;
  /** The evidence's own text, which is the line that names the file. */
  line: string;
}

/**
 * The attachment a piece of evidence names, from the `attachment` that `kyo show --json` adds
 * to evidence whose text is a reference (KYO-856). Null for other evidence, and for a file
 * the server says is no longer there, which is then named in words.
 */
export function attachedOf(artifact: Doc): Attached | null {
  const found = artifact.attachment;
  if (!found || typeof found !== "object" || found.present !== true) return null;
  const name = /^([0-9a-f]{64})\.(png|jpg|gif|webp|pdf)$/.exec(String(found.name ?? ""));
  const session = word(found.session);
  if (!name || name[1] !== found.id || !session) return null;
  return { id: name[1], session, name: name[0], extension: name[2], line: word(artifact.text) ?? name[0] };
}

export interface Section { name: string; count: string; items: Item[] }

export interface Page {
  id: string;
  kind: string;
  status: string;
  title: string;
  facts: Line[];
  description: string;
  sections: Section[];
  /** The sections the record has nothing in. */
  none: string[];
}

type Doc = Record<string, any>;

const list = (value: unknown): Doc[] =>
  Array.isArray(value) ? value.filter((row) => row && typeof row === "object") : [];

const word = (value: unknown): string | null =>
  typeof value === "string" && value ? value : null;

const words = (...values: unknown[]): string[] =>
  values.map(word).filter((value): value is string => value !== null);

/** A stored instant in the reader's own time, or the text as stored when it is not one. */
export function when(at: unknown): string | null {
  const text = word(at);
  if (!text) return null;
  const date = new Date(text);
  if (!Number.isFinite(date.getTime())) return text;
  return date.toLocaleString([], {
    year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

/** A criterion's latest verdict: the verification with the highest sequence number. */
export function latestVerdict(criterion: Doc): Doc | null {
  let latest: Doc | null = null;
  for (const verification of list(criterion.verifications)) {
    if (!latest || (verification.seq ?? 0) >= (latest.seq ?? 0)) latest = verification;
  }
  return latest;
}

function line(label: string, ...parts: unknown[]): Line[] {
  const text = words(...parts).join(" · ");
  return text ? [{ label, text }] : [];
}

function criterionItem(criterion: Doc): Item {
  const verdict = latestVerdict(criterion);
  return {
    ref: word(criterion.id) ?? undefined,
    citable: true,
    state: word(criterion.state) ?? undefined,
    tone: criterion.state === "met" ? "plain" : "quiet",
    text: word(criterion.text) ?? "",
    lines: [
      ...line("reason", criterion.reason),
      ...(verdict
        ? line(
          "latest verdict", verdict.verdict, verdict.method, verdict.author,
          when(verdict.created_at), verdict.id,
          verdict.evidence_id && `evidence ${verdict.evidence_id}`,
          verdict.credit && `credit ${verdict.credit}`,
          verdict.overrides && `overrides ${verdict.overrides}`)
        : [{ label: "latest verdict", text: "none recorded" }]),
      // A verdict often repeats the criterion's own reason, which is then said once.
      ...(verdict && verdict.reason !== criterion.reason
        ? line("verdict's reason", verdict.reason) : []),
    ],
  };
}

function gateItem(gate: Doc): Item {
  const cleared = word(gate.cleared_at);
  return {
    ref: word(gate.id) ?? undefined,
    citable: true,
    state: cleared ? `cleared as ${word(gate.cleared_as) ?? "unknown"}` : "open",
    tone: cleared ? "quiet" : "plain",
    meta: words(gate.type, ...(Array.isArray(gate.tags) ? gate.tags : [])),
    text: word(gate.do) ?? "",
    lines: [
      ...line("cleared", when(cleared), gate.credit && `credit ${gate.credit}`),
      ...line("why", gate.cleared_why),
    ],
  };
}

/**
 * The part of a dispatch's frozen brief an orchestrator wrote: what follows its `## Task`
 * heading, up to the `## Report` heading the generator closes it with. Null when the brief
 * has no such heading.
 */
export function taskOf(brief: unknown): string | null {
  const text = word(brief);
  if (!text) return null;
  const start = /^## Task[ \t]*$/m.exec(text);
  if (!start) return null;
  const rest = text.slice(start.index + start[0].length);
  const end = /^## Report[ \t]*$/m.exec(rest);
  const task = (end ? rest.slice(0, end.index) : rest).trim();
  return task || null;
}

/** `covered` marks a dispatch of another record, whose id this record's page cannot cite. */
function dispatchItem(dispatch: Doc, covered = false): Item {
  const brief = word(dispatch.brief);
  const task = taskOf(brief);
  return {
    ref: word(dispatch.id) ?? undefined,
    citable: !covered,
    state: word(dispatch.state) ?? undefined,
    tone: dispatch.state === "running" ? "running" : "quiet",
    lines: [
      ...line("model", dispatch.worker_model),
      ...line("branch", dispatch.branch),
      ...line("base", dispatch.worked_from_sha),
      ...line("dispatched", when(dispatch.created_at)),
      ...line("report", dispatch.report_id),
      ...line("also covers", ...(Array.isArray(dispatch.covers) ? dispatch.covers : [])),
      ...line("closed", when(dispatch.closed_at)),
      ...line("ended because", dispatch.terminated_why),
    ],
    folded: [
      ...(task ? [{ label: "Task", text: task }] : []),
      ...(brief ? [{ label: `Brief, ${brief.length.toLocaleString("en-US")} characters`, text: brief }] : []),
    ],
  };
}

/**
 * A report in the order an orchestrator reviews it: what the brief got wrong, where the
 * worker departed from it, what it did and why, corrected numbers, each criterion's verdict,
 * the docs verdicts, and the commits. `supersededBy` names the report that replaced this one,
 * which is then drawn folded.
 *
 * A false premise, a disagreement, a corrected number and a verdict each carry the id the
 * server gives the entry in `entry_ids`, which lists the ids of each array of the body in the
 * array's order (KYO-757). The id is the server's to derive, so a report from a server that
 * sends none has lines that cannot be anchored to. A verdict is anchored by its own id and
 * not its criterion's: the anchor is to what the worker said, and the criterion has its own
 * line on the page.
 */
export function reportItem(report: Doc, supersededBy: string | null = null): Item {
  const body: Doc = report.body && typeof report.body === "object" ? report.body : {};
  const group = (name: string, lines: Line[]): Line[] =>
    lines.length ? [{ ...lines[0], group: name }, ...lines.slice(1)] : [];
  const ids: Doc = report.entry_ids && typeof report.entry_ids === "object" ? report.entry_ids : {};
  /** The entries of one array of the body, each with its id when the server gave one. */
  const entries = (array: string): { entry: Doc; ref?: string }[] =>
    (Array.isArray(body[array]) ? body[array] as unknown[] : [])
      .map((entry, index) => ({
        entry, ref: (Array.isArray(ids[array]) && word(ids[array][index])) || undefined,
      }))
      .filter((one): one is { entry: Doc; ref: string | undefined } =>
        Boolean(one.entry) && typeof one.entry === "object");
  const anchored = (ref: string | undefined): { ref?: string } => (ref ? { ref } : {});
  const lines: Line[] = [
    ...group("False premises", entries("premise_false").map(({ entry: premise, ref }) => ({
      label: "premise false",
      text: words(
        premise.claim, premise.correction && `Correction: ${premise.correction}`,
        premise.impact && `Impact: ${premise.impact}`).join("\n"),
      ...anchored(ref),
    }))),
    ...group("Disagreements with the brief", entries("disagreements").map(({ entry: disagreement, ref }) => ({
      label: "disagreement",
      text: words(
        disagreement.topic, disagreement.brief_said && `The brief said: ${disagreement.brief_said}`,
        disagreement.worker_did && `The worker did: ${disagreement.worker_did}`,
        disagreement.rationale && `Because: ${disagreement.rationale}`).join("\n"),
      ...anchored(ref),
    }))),
    ...group("What the worker did and why", line("summary", body.summary)),
    ...group("Corrected numbers", entries("corrected_numbers").map(({ entry: number, ref }) => ({
      label: "corrected number",
      text: words(
        number.claim, number.brief_value && `The brief said: ${number.brief_value}`,
        number.actual_value && `Measured: ${number.actual_value}`,
        number.method && `By: ${number.method}`).join("\n"),
      ...anchored(ref),
    }))),
    ...group("Verdicts", entries("criteria_verdicts").map(({ entry: verdict, ref }) => ({
      label: words(verdict.criterion_id, verdict.verdict, verdict.method).join(" · "),
      text: words(
        verdict.evidence, verdict.reason && `Reason: ${verdict.reason}`, verdict.notes).join("\n"),
      ...anchored(ref),
    }))),
    ...group("Docs verdicts", list(body.docs_verdicts).map((docs) => ({
      label: words(docs.verdict, docs.method).join(" · "),
      text: words(
        words(docs.doc_path, docs.section && `§ ${docs.section}`).join(" "), docs.reason).join("\n"),
    }))),
    ...group("Commits", list(body.commits).map((commit) => ({
      label: "commit", text: words(commit.sha, commit.subject).join(" "),
    }))),
    ...group("Files touched", Array.isArray(body.touched_files) && body.touched_files.length
      ? [{ label: "touched", text: words(...body.touched_files).join("\n") }] : []),
  ];
  const amends = word(body.amends);
  return {
    ref: word(report.id) ?? undefined,
    state: supersededBy ? "superseded" : undefined,
    tone: supersededBy ? "quiet" : "plain",
    meta: words(
      report.dispatch_id, report.author, when(report.created_at),
      amends && `amends ${amends}`, supersededBy && `superseded by ${supersededBy}`),
    lines,
    collapsed: supersededBy ? `Superseded report, ${lines.length} entries` : undefined,
  };
}

function creditItem(credit: Doc, evidence: Map<string, string>): Item {
  const quote = word(credit.quote_id);
  return {
    ref: word(credit.id) ?? undefined,
    state: word(credit.role) ?? undefined,
    tone: credit.withdrawn_at || credit.superseded_at ? "quiet" : "plain",
    meta: words(
      credit.person_name ?? credit.person, credit.outcome,
      credit.transcript_check && `transcript ${credit.transcript_check}`,
      credit.reconstructed === true && "reconstructed", when(credit.observed_at)),
    lines: [
      ...line("words", quote && evidence.get(quote)),
      ...line("quoted in", quote),
      ...line(
        "recommended", credit.recommended,
        credit.recommended_by && `by ${credit.recommended_by}`),
      ...list(credit.alternatives).map((alternative) => ({
        label: alternative.chosen ? "alternative, chosen" : "alternative",
        text: word(alternative.text) ?? "",
      })),
      ...line("basis", credit.basis_id),
      ...line("superseded by", credit.superseded_by),
      ...line("withdrawn", when(credit.withdrawn_at), credit.withdrawn_by, credit.withdrawn_why),
    ],
  };
}

const LONG_TEXT = 700;

/** The reports of `reports` with the id of the one that amended each, from the amending report. */
function supersessions(reports: Doc[]): Map<string, string> {
  const by = new Map<string, string>();
  for (const report of reports) {
    const amended = word(report.body?.amends);
    if (amended && word(report.id)) by.set(amended, report.id);
  }
  return by;
}

/**
 * The dispatches of other records that formally cover this one, and their reports, drawn
 * from the covering record's own document where `covering` holds it. A record covered by a
 * dispatch has no dispatch or report of its own, so without these its page showed neither.
 */
function coveredBy(doc: Doc, covering: ReadonlyMap<string, Doc>): { dispatches: Item[]; reports: Item[] } {
  const dispatches: Item[] = [];
  const reports: Item[] = [];
  for (const entry of list(doc.covered_by_dispatches)) {
    const primary = word(entry.primary_record_id);
    const full = primary ? covering.get(primary) : undefined;
    const dispatch = list(full?.dispatches).find((one) => one.id === entry.id);
    if (dispatch) {
      dispatches.push({ ...dispatchItem(dispatch, true), meta: words(`on ${primary}`) });
    } else {
      dispatches.push({
        ref: word(entry.id) ?? undefined, state: word(entry.state) ?? undefined, tone: "quiet",
        meta: words(primary && `on ${primary}`),
        lines: line("report", entry.report_id),
      });
    }
    const all = list(full?.reports);
    const replaced = supersessions(all);
    for (const report of all.filter((one) => one.dispatch_id === entry.id)) {
      reports.push(reportItem(report, replaced.get(report.id) ?? null));
    }
  }
  return { dispatches, reports };
}

/**
 * What a record page shows, from `kyo show --json`: the same parts `kyo show` prints, each
 * with its id. `titles` names the records a link or a member points at, and `covering` holds
 * the documents of the records whose dispatches cover this one, by id.
 */
export function recordPage(
  doc: Doc, titles: ReadonlyMap<string, string> = new Map(),
  covering: ReadonlyMap<string, Doc> = new Map(),
): Page {
  const named = (id: unknown): string => {
    const text = word(id) ?? "";
    const title = titles.get(text);
    return title ? `${text} ${title}` : text;
  };
  const evidence = new Map<string, string>();
  for (const artifact of list(doc.evidence_artifacts)) {
    if (word(artifact.id) && word(artifact.text)) evidence.set(artifact.id, artifact.text);
  }
  const carried: Doc = doc.carried && typeof doc.carried === "object" ? doc.carried : {};
  const standing = word(doc.standing);
  const facts: Line[] = [
    ...line("status", doc.status, standing && standing !== doc.status && `standing ${standing}`),
    ...line("plan", doc.plan),
    { label: "scope", text: word(doc.scope) ?? "none declared" },
    ...line("discussion", doc.discussion?.id),
    ...line("created", when(doc.created_at)),
    ...line("updated", when(doc.updated_at)),
    ...line("not done because", doc.wont_do_reason),
    ...line("carried since", when(carried.since), carried.since_note),
  ];
  const gates = list(doc.gates);
  const openGates = gates.filter((gate) => !gate.cleared_at).length;
  const flags = list(doc.flags);
  const cover = coveredBy(doc, covering);
  const replaced = supersessions(list(doc.reports));
  const all: Section[] = [
    {
      name: "Criteria", count: String(list(doc.criteria).length),
      items: list(doc.criteria).map(criterionItem),
    },
    {
      name: "Gates", count: `${openGates} open of ${gates.length}`, items: gates.map(gateItem),
    },
    {
      name: "Members", count: String(list(doc.members).length),
      items: list(doc.members).map((member) => ({
        state: word(member.status) ?? undefined,
        tone: member.status === "open" ? "plain" as const : "quiet" as const,
        meta: words(member.kind),
        text: words(member.id, member.title).join(" "),
      })),
    },
    {
      name: "Links", count: String(list(doc.links).length),
      items: list(doc.links).map((link) => ({
        ref: word(link.id) ?? undefined,
        meta: words(link.type, link.direction),
        text: named(link.other),
      })),
    },
    {
      name: "Dispatches",
      count: String(list(doc.dispatches).length + cover.dispatches.length),
      items: [...list(doc.dispatches).map((one) => dispatchItem(one)), ...cover.dispatches],
    },
    {
      name: "Reports", count: String(list(doc.reports).length + cover.reports.length),
      items: [
        ...list(doc.reports).map((one) => reportItem(one, replaced.get(one.id) ?? null)),
        ...cover.reports,
      ],
    },
    {
      name: "Notes", count: String(list(doc.notes).length),
      items: list(doc.notes).map((note) => ({
        ref: word(note.id) ?? undefined,
        citable: true,
        meta: words(
          when(note.created_at), note.superseded_by && `superseded by ${note.superseded_by}`,
          note.redacted_at && "redacted"),
        tone: note.superseded_at ? "quiet" as const : "plain" as const,
        text: word(note.text) ?? word(note.redacted_why) ?? "",
      })),
    },
    {
      name: "Claims", count: String(list(doc.claims).length),
      items: list(doc.claims).map((claim) => ({
        ref: word(claim.id) ?? undefined,
        state: word(claim.level) ?? undefined,
        tone: claim.superseded_at ? "quiet" as const : "plain" as const,
        meta: words(
          claim.method, claim.author, when(claim.created_at),
          claim.superseded_by && `superseded by ${claim.superseded_by}`),
        text: word(claim.statement) ?? word(claim.redacted_why) ?? "",
      })),
    },
    {
      name: "Credits", count: String(list(doc.credits).length),
      items: list(doc.credits).map((credit) => creditItem(credit, evidence)),
    },
    {
      name: "Evidence", count: String(list(doc.evidence_artifacts).length),
      items: list(doc.evidence_artifacts).map((artifact) => {
        const text = word(artifact.text) ?? word(artifact.redacted_why) ?? "";
        const long = text.length > LONG_TEXT;
        const attached = attachedOf(artifact);
        const gone = !attached && artifact.attachment?.present === false;
        return {
          ref: word(artifact.id) ?? undefined,
          meta: words(
            artifact.author, when(artifact.created_at),
            artifact.sha256 && `sha256 ${String(artifact.sha256).slice(0, 12)}`,
            artifact.supports_id && `supports ${artifact.supports_id}`,
            gone && "the file is no longer there"),
          // The image stands where the line that names it would.
          text: long || attached ? "" : text,
          folded: long ? [{ label: `${text.slice(0, 90).trimEnd()}…`, text }] : [],
          ...(attached ? { attached } : {}),
        };
      }),
    },
    {
      name: "Touches", count: String(list(doc.touches).length),
      items: list(doc.touches).map((touch) => ({
        ref: word(touch.id) ?? undefined,
        text: words(touch.path, touch.symbol && `#${touch.symbol}`).join(""),
      })),
    },
    {
      name: "Flags",
      count: `${flags.length}, ${flags.filter((flag) => !flag.resolved_at).length} unresolved`,
      items: flags.map((flag) => ({
        ref: word(flag.id) ?? undefined,
        state: flag.resolved_at ? "resolved" : word(flag.flag_type) ?? undefined,
        tone: flag.resolved_at ? "quiet" as const : "plain" as const,
        meta: flag.resolved_at ? words(flag.flag_type) : [],
        text: word(flag.detail) ?? "",
        lines: line("resolved", when(flag.resolved_at), flag.resolved_by, flag.resolved_why),
      })),
    },
  ];
  return {
    id: word(doc.id) ?? "",
    kind: word(doc.kind) ?? "",
    status: word(doc.status) ?? "",
    title: word(doc.title) ?? "",
    facts,
    description: word(doc.description) ?? "",
    sections: all.filter((section) => section.items.length),
    none: all.filter((section) => !section.items.length).map((section) => section.name),
  };
}

/** The statuses of work under way, shown with the open records on the Records home. */
const UNDER_WAY = new Set(["open", "working", "built", "verified"]);

/** One group of the Records home (KYO-835). */
export interface HomeGroup<T> { name: "Awaiting you" | "Dispatchable" | "Other open" | "Closed"; rows: T[] }

/**
 * The Records home's groups, each record in one, in the order of `rows`: those awaiting the
 * person, then those ready to dispatch, then the rest of the work under way, then everything
 * settled, a current decision included.
 */
export function homeGroups<T extends RecordRow>(
  rows: T[], ready: ReadonlySet<string>, awaiting: ReadonlySet<string>,
): HomeGroup<T>[] {
  const groups: HomeGroup<T>[] = [
    { name: "Awaiting you", rows: [] }, { name: "Dispatchable", rows: [] },
    { name: "Other open", rows: [] }, { name: "Closed", rows: [] },
  ];
  for (const row of rows) {
    const at = awaiting.has(row.id) ? 0 : ready.has(row.id) ? 1 : UNDER_WAY.has(standingOf(row)) ? 2 : 3;
    groups[at].rows.push(row);
  }
  return groups;
}

// ------------------------------------------------- the Records home as a table (KYO-862)

/** The Locations of the Records home's side bar, each a filter of the table. */
export const LOCATIONS = ["Awaiting you", "Dispatchable", "Other open", "Closed", "All"] as const;
export type RecordLocation = typeof LOCATIONS[number];

/** A stored Location, or All when what was stored names none. */
export function locationFrom(stored: unknown): RecordLocation {
  return LOCATIONS.find((name) => name === stored) ?? "All";
}

/** The rows of each Location, in the order of `rows`; All is every row. */
export function located<T extends RecordRow>(
  rows: T[], ready: ReadonlySet<string>, awaiting: ReadonlySet<string>,
): Record<RecordLocation, T[]> {
  const [waiting, dispatchable, open, closed] = homeGroups(rows, ready, awaiting).map((group) => group.rows);
  return { "Awaiting you": waiting, Dispatchable: dispatchable, "Other open": open, Closed: closed, All: rows };
}

/** The table's columns that sort it. */
export const RECORD_SORTS = ["name", "kind", "status", "created", "modified"] as const;
export type RecordSortBy = typeof RECORD_SORTS[number];
export interface RecordSort { by: RecordSortBy; descending: boolean }

/** The table as it first stands: the newest record first. */
export const NEWEST: RecordSort = { by: "created", descending: true };

/** A stored sort, or newest first when what was stored is none. */
export function recordSortFrom(stored: unknown): RecordSort {
  const given = stored as Partial<RecordSort> | null;
  const by = RECORD_SORTS.find((name) => name === given?.by);
  return by ? { by, descending: given?.descending === true } : NEWEST;
}

/** The sort after a column's head is pressed: the other way round, or that column, dates newest first. */
export function pressedSort(sort: RecordSort, by: RecordSortBy): RecordSort {
  if (sort.by === by) return { by, descending: !sort.descending };
  return { by, descending: by === "created" || by === "modified" };
}

/** The order a Status column runs in: work from open to settled, then the knowledge states. */
const STATUS_ORDER = ["open", "working", "built", "verified", "done", "wont-do", "current", "void"];

function statusRank(status: string): number {
  const at = STATUS_ORDER.indexOf(status);
  return at === -1 ? STATUS_ORDER.length : at;
}

/** `rows` in the order a sort gives, as a new list. Records equal under it stand newest first. */
export function sortRecords<T extends RecordRow>(rows: T[], sort: RecordSort): T[] {
  const age = (row: T) => row.seq ?? 0;
  const compare: Record<RecordSortBy, (a: T, b: T) => number> = {
    name: (a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" }),
    kind: (a, b) => a.kind.localeCompare(b.kind),
    status: (a, b) => statusRank(standingOf(a)) - statusRank(standingOf(b)) || standingOf(a).localeCompare(standingOf(b)),
    created: (a, b) => age(a) - age(b) || (a.created_at ?? "").localeCompare(b.created_at ?? ""),
    modified: (a, b) => (a.updated_at ?? a.created_at ?? "").localeCompare(b.updated_at ?? b.created_at ?? ""),
  };
  const by = compare[sort.by];
  return [...rows].sort((a, b) => (sort.descending ? by(b, a) : by(a, b)) || age(b) - age(a));
}

/**
 * The drawer's filters from what was stored (KYO-861). Open only is the one filter the page
 * has a control for; a Dispatchable or Awaiting you value stored by an earlier build has no
 * control to turn it off, so it is never read.
 */
export function storedFilters(stored: unknown): { open: boolean; dispatchable: false; awaiting: false } {
  return { open: (stored as { open?: unknown } | null)?.open === true, dispatchable: false, awaiting: false };
}

/** The day a record was created or changed, as the table states it; empty when unknown. */
export function dayOf(at: unknown, now = new Date()): string {
  if (typeof at !== "string" || Number.isNaN(Date.parse(at))) return "";
  const when = new Date(at);
  return when.toLocaleDateString([], when.getFullYear() === now.getFullYear()
    ? { month: "short", day: "numeric" } : { year: "numeric", month: "short", day: "numeric" });
}
