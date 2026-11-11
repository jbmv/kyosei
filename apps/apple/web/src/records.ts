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
  seq?: number;
  created_at?: string;
}

/** What narrows a list: the three filters and the search box. */
export interface Narrowing {
  open: boolean;
  dispatchable: boolean;
  awaiting: boolean;
  search: string;
}

/** The records with the one created last first. */
export function newestFirst<T extends RecordRow>(rows: T[]): T[] {
  return [...rows].sort((a, b) => {
    const bySeq = (b.seq ?? 0) - (a.seq ?? 0);
    if (bySeq) return bySeq;
    return (b.created_at ?? "").localeCompare(a.created_at ?? "");
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

function searchWords(search: string): string[] {
  return search.toLowerCase().split(/\s+/).filter(Boolean);
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
  return rows.filter((row) =>
    (!narrowing.open || row.status === "open")
    && (!narrowing.dispatchable || ready.has(row.id))
    && (!narrowing.awaiting || awaiting.has(row.id))
    && matchesSearch(row, narrowing.search));
}

/** The kinds a project usually has, in the order the landing page lists them. */
export const KIND_ORDER = [
  "task", "bug", "chore", "plan", "proposal", "decision", "investigation", "discussion",
  "evidence", "reminder",
];

export interface KindGroup<T extends RecordRow> { kind: string; rows: T[] }

/** The rows under their kinds, newest first within each; a kind with no row is left out. */
export function byKind<T extends RecordRow>(rows: T[]): KindGroup<T>[] {
  const groups = new Map<string, T[]>();
  for (const row of newestFirst(rows)) {
    const group = groups.get(row.kind);
    if (group) group.push(row);
    else groups.set(row.kind, [row]);
  }
  const place = (kind: string) => {
    const index = KIND_ORDER.indexOf(kind);
    return index < 0 ? KIND_ORDER.length : index;
  };
  return [...groups.entries()]
    .sort(([a], [b]) => place(a) - place(b) || a.localeCompare(b))
    .map(([kind, rows]) => ({ kind, rows }));
}

/** A group this long starts folded, so that the kinds after it can be seen. */
export const FOLD_ABOVE = 12;

/** Whether a kind's group is folded: as the person last left it, or by its length. */
export function isFolded(kind: string, count: number, chosen: Record<string, boolean>): boolean {
  return Object.hasOwn(chosen, kind) ? chosen[kind] : count > FOLD_ABOVE;
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

const REF = /^([A-Z][A-Z0-9]*-\d+)(\.[a-z]+\d+)?$/;

/** The record an id belongs to: `KYO-732` for `KYO-732.c1`. Null when it is not an id. */
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
    `(?<![A-Za-z0-9-])(?:${known.join("|")})-\\d+(?:\\.[a-z]+\\d+)?(?![A-Za-z0-9])`, "g");
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
  /** The id the line can be anchored by, as a criterion's can: a verdict's criterion. */
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
 */
export function reportItem(report: Doc, supersededBy: string | null = null): Item {
  const body: Doc = report.body && typeof report.body === "object" ? report.body : {};
  const group = (name: string, lines: Line[]): Line[] =>
    lines.length ? [{ ...lines[0], group: name }, ...lines.slice(1)] : [];
  const lines: Line[] = [
    ...group("False premises", list(body.premise_false).map((premise) => ({
      label: "premise false",
      text: words(
        premise.claim, premise.correction && `Correction: ${premise.correction}`,
        premise.impact && `Impact: ${premise.impact}`).join("\n"),
    }))),
    ...group("Disagreements with the brief", list(body.disagreements).map((disagreement) => ({
      label: "disagreement",
      text: words(
        disagreement.topic, disagreement.brief_said && `The brief said: ${disagreement.brief_said}`,
        disagreement.worker_did && `The worker did: ${disagreement.worker_did}`,
        disagreement.rationale && `Because: ${disagreement.rationale}`).join("\n"),
    }))),
    ...group("What the worker did and why", line("summary", body.summary)),
    ...group("Corrected numbers", list(body.corrected_numbers).map((number) => ({
      label: "corrected number",
      text: words(
        number.claim, number.brief_value && `The brief said: ${number.brief_value}`,
        number.actual_value && `Measured: ${number.actual_value}`,
        number.method && `By: ${number.method}`).join("\n"),
    }))),
    ...group("Verdicts", list(body.criteria_verdicts).map((verdict) => ({
      label: words(verdict.criterion_id, verdict.verdict, verdict.method).join(" · "),
      text: words(
        verdict.evidence, verdict.reason && `Reason: ${verdict.reason}`, verdict.notes).join("\n"),
      ref: word(verdict.criterion_id) ?? undefined,
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
        return {
          ref: word(artifact.id) ?? undefined,
          meta: words(
            artifact.author, when(artifact.created_at),
            artifact.sha256 && `sha256 ${String(artifact.sha256).slice(0, 12)}`,
            artifact.supports_id && `supports ${artifact.supports_id}`),
          text: long ? "" : text,
          folded: long ? [{ label: `${text.slice(0, 90).trimEnd()}…`, text }] : [],
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
