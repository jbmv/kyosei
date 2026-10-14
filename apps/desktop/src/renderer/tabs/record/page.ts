// The record page: every child object the read model returns, as its own section, in
// id order (KYO-233.c1).
//
// One of v2's worst faults was that a record's `.m1` could be read only in a
// tooltip. So the rule this file is written to is
// literal: NOTHING about a record lives only in a tooltip, only in a title
// attribute, or only in a field the page decided was too long to show. Every field
// `RecordDocument` carries is drawn, every child object is its own anchorable block
// carrying its own id, and the one thing that is collapsed -- the obligations -- is
// collapsed with a control that opens it, with its contents already in the DOM.
//
// It DERIVES NOTHING. `getRecordDocument` (KYO-231) returns the children already in
// id order and the obligation grouping already collapsed by the CLI's own
// `docs_check.collapse_obligations` (KYO-219); this file paints that envelope. Where
// a field is a string, it goes through the link renderer (`renderer/links/`) so an
// `[[id]]` inside it is clickable, and the link renderer hands the URL to
// `context.followLink` -- no id is parsed anywhere on this page.

import type {
  ClaimView,
  CoveringDispatchView,
  CriterionView,
  DispatchView,
  DocRefView,
  EvidenceView,
  FlagView,
  GateDocument,
  LinkView,
  MemberView,
  NoteView,
  ObligationCollapse,
  RecordDocument,
  ReportView,
  TouchView,
} from "../../../shared/protocol";
import { type Child, el, facts, json } from "../../dom";
import { idLink, type LinkSurface, linkedProse, linkedText } from "../../links";

/** A `null` field, as this page says "nothing here" in one voice. */
const NOTHING = "—";

function shown(value: string | null | undefined): string {
  return value === null || value === undefined || value === "" ? NOTHING : value;
}

/** A short, human line about one child, for the "sent here" strip to quote. */
function summaryOf(kind: string, detail: string): string {
  return detail === "" ? `a ${kind}` : `a ${kind}, ${detail}`;
}

/**
 * One child object: its own block, carrying its own id.
 *
 * `data-child-id` is what an anchor resolves against, and it is the id the CLI
 * minted -- so a deep link to `FIX-1.g1` finds the gate by the id in the delivery
 * and not by counting sections. `data-child-summary` is what the strip says the
 * reader was sent to, composed here where the child's own fields are in hand.
 */
function child(
  kind: string,
  id: string,
  summary: string,
  surface: LinkSurface,
  head: Child[],
  body: Child[],
): HTMLElement {
  return el(
    "article",
    {
      class: "child",
      data: { child: kind, childId: id, childKind: kind, childSummary: summary },
    },
    [
      el("div", { class: "child-head" }, [
        idLink(id, surface, { class: "child-id" }),
        el("span", { class: "child-kind", text: kind }),
        ...head,
      ]),
      ...body,
    ],
  );
}

/** A tag chip: a state word the eye should find before it reads the sentence. */
function tag(value: string, tone?: string): HTMLElement {
  return el("span", {
    class: "tag",
    text: value,
    data: tone === undefined ? { tag: value } : { tag: value, tone },
  });
}

/** A one-line row of `label: value` pairs, for a child's metadata. */
function meta(pairs: [string, string | null][]): HTMLElement {
  const row = el("div", { class: "child-meta" });
  for (const [label, value] of pairs) {
    row.append(
      el("span", { class: "meta-pair" }, [
        el("span", { class: "meta-label", text: label }),
        el("span", { class: "meta-value", text: shown(value) }),
      ]),
    );
  }
  return row;
}

/**
 * A block that starts closed, with its contents already in the DOM.
 *
 * Built eagerly and HIDDEN rather than built on first open, for one reason that is
 * not about performance: a deep link can name an id inside a collapsed group
 * (`KYO-232.o1` is an obligation), and an anchor cannot resolve against an element
 * that does not exist yet. `expandFor` below re-opens whatever a target is inside.
 */
function expandable(label: string, name: string, contents: Child[]): HTMLElement {
  const body = el("div", { class: "expand-body" }, contents);
  body.hidden = true;
  const control = el("button", {
    class: "expand-control",
    text: `▸ ${label}`,
    data: { action: "expand", expandable: name },
  });
  const box = el("div", { class: "expandable", data: { expandable: name, expanded: "false" } }, [
    control,
    body,
  ]);
  control.addEventListener("click", () => {
    const open = box.dataset.expanded !== "true";
    box.dataset.expanded = String(open);
    body.hidden = !open;
    control.textContent = `${open ? "▾" : "▸"} ${label}`;
  });
  return box;
}

/** Open every collapsed block the element is inside, innermost first. */
export function expandFor(element: HTMLElement): void {
  let box = element.closest<HTMLElement>('[data-expandable][data-expanded="false"]');
  while (box !== null) {
    box.querySelector<HTMLElement>(':scope > [data-action="expand"]')?.click();
    box =
      box.parentElement?.closest<HTMLElement>('[data-expandable][data-expanded="false"]') ?? null;
  }
}

/** The heading every section carries: its name, and how many children are under it. */
function sectionHead(name: string, count: string): HTMLElement {
  return el("h2", { class: "section-head" }, [
    el("span", { class: "section-name", text: name }),
    el("span", { class: "section-count", text: count }),
  ]);
}

/** One section: a child kind, its count, and its children in the order they came. */
function section(name: string, children: HTMLElement[], extra: Child[] = []): HTMLElement {
  return el(
    "section",
    { class: "record-section", data: { section: name, count: children.length } },
    [
      sectionHead(name, String(children.length)),
      ...extra,
      ...(children.length === 0
        ? [el("div", { class: "none", data: { region: "section-empty" }, text: "(none)" })]
        : children),
    ],
  );
}

/** A section holding one block rather than a list of children. */
function blockSection(name: string, contents: Child[]): HTMLElement {
  return el("section", { class: "record-section", data: { section: name } }, [
    sectionHead(name, ""),
    ...contents,
  ]);
}

// ------------------------------------------------------------------ the child kinds

function criterion(view: CriterionView, surface: LinkSurface): HTMLElement {
  return child(
    "criterion",
    view.id,
    summaryOf("criterion", view.state),
    surface,
    [tag(view.state, view.state.startsWith("met") ? "good" : "open")],
    [
      linkedProse(view.text, surface, { class: "child-text" }),
      view.reason !== null && meta([["reason", view.reason]]),
      // The verdict history, which is what KYO-233.c1 means by "criteria with
      // verdict history": every verification, each its own anchorable child,
      // because `FIX-1.v2` is an id a link can name.
      ...view.verifications.map((verification) =>
        child(
          "verification",
          verification.id,
          summaryOf("verification", verification.verdict),
          surface,
          [tag(verification.verdict, verification.verdict === "met" ? "good" : "open")],
          [
            meta([
              ["method", verification.method],
              ["by", verification.author],
              ["at", verification.createdAt],
            ]),
            verification.reason !== null &&
              linkedProse(verification.reason, surface, { class: "child-text" }),
          ],
        ),
      ),
    ],
  );
}

function claim(view: ClaimView, surface: LinkSurface): HTMLElement {
  return child(
    "claim",
    view.id,
    summaryOf("claim", view.level),
    surface,
    [
      tag(view.level, view.level === "verified" ? "good" : "open"),
      view.supersededBy !== null && tag("superseded", "muted"),
    ],
    [
      linkedProse(view.statement, surface, { class: "child-text" }),
      meta([
        ["method", view.method],
        ["by", view.author],
        ["at", view.createdAt],
      ]),
      (view.supersedes !== null || view.supersededBy !== null) &&
        el("div", { class: "child-meta" }, [
          view.supersedes !== null &&
            el("span", { class: "meta-pair" }, [
              el("span", { class: "meta-label", text: "supersedes" }),
              idLink(view.supersedes, surface),
            ]),
          view.supersededBy !== null &&
            el("span", { class: "meta-pair" }, [
              el("span", { class: "meta-label", text: "superseded by" }),
              idLink(view.supersededBy, surface),
            ]),
        ]),
    ],
  );
}

/**
 * A claim shown BESIDE a gate: a reference to it, not a second copy of it.
 *
 * The distinction is load-bearing. A claim stands beside its gate (KYO-88) and also
 * in the claims section, and if both were `child()` blocks the page would hold two
 * elements carrying `data-child-id="FIX-1.m1"` -- and an anchor, which resolves
 * against exactly that attribute, would highlight whichever came first while the
 * reader's link said nothing about which. So the claims section holds the claim and
 * this holds a pointer to it: the id as a link, the level, and what it says.
 */
function claimReference(view: ClaimView, surface: LinkSurface): HTMLElement {
  return el("div", { class: "claim-ref", data: { claimRef: view.id } }, [
    el("div", { class: "child-head" }, [
      idLink(view.id, surface, { class: "child-id" }),
      tag(view.level, view.level === "verified" ? "good" : "open"),
    ]),
    linkedProse(view.statement, surface, { class: "child-text" }),
  ]);
}

/**
 * One gate, with the claims that stand beside it (KYO-88) and its payload as written.
 *
 * The payload is the one field on a record whose LINE BREAKS carry meaning -- it is a
 * procedure a human follows, with numbered steps and indented sub-lines -- so it is
 * its own `pre-wrap` element and `tests/gate-payload.spec.ts` measures where the
 * glyphs actually land (KYO-160). `data-region="gate-payload"` and
 * `data-gate-open` are the names that check locates by: they were here before this
 * page was, and a criterion that was met before stage 3 is not unmet after it.
 */
function gate(view: GateDocument, surface: LinkSurface): HTMLElement {
  const node = child(
    "gate",
    view.id,
    summaryOf("gate", `${view.type}, ${view.open ? "open" : "cleared"}`),
    surface,
    [
      tag(view.type, view.open ? "open" : "muted"),
      tag(view.open ? "open" : "cleared", view.open ? "warn" : "good"),
      ...view.tags.map((label) => tag(label, "muted")),
    ],
    [
      view.detail !== null &&
        linkedProse(view.detail, surface, {
          class: "gate-payload",
          data: { region: "gate-payload" },
        }),
      meta([
        ["opened", view.createdAt],
        ["cleared", view.clearedAt],
        ["why", view.clearedWhy],
      ]),
      view.claims.length > 0 &&
        el("div", { class: "beside" }, [
          el("div", { class: "beside-head", text: "claims made while this gate was open" }),
          ...view.claims.map((made) => claimReference(made, surface)),
        ]),
    ],
  );
  node.dataset.gateId = view.id;
  node.dataset.gateOpen = String(view.open);
  node.dataset.gateType = view.type;
  return node;
}

function note(view: NoteView, surface: LinkSurface): HTMLElement {
  return child(
    "note",
    view.id,
    summaryOf("note", view.supersededBy === null ? "standing" : "superseded"),
    surface,
    [
      view.supersededBy !== null && tag("superseded", "muted"),
      view.redactedAt !== null && tag("redacted", "warn"),
    ],
    [
      linkedProse(view.text, surface, { class: "child-text" }),
      meta([["at", view.createdAt]]),
      view.supersededBy !== null &&
        el("div", { class: "child-meta" }, [
          el("span", { class: "meta-pair" }, [
            el("span", { class: "meta-label", text: "superseded by" }),
            idLink(view.supersededBy, surface),
          ]),
          el("span", { class: "meta-pair" }, [
            el("span", { class: "meta-label", text: "at" }),
            el("span", { class: "meta-value", text: shown(view.supersededAt) }),
          ]),
        ]),
      view.redactedAt !== null &&
        meta([
          ["redacted", view.redactedAt],
          ["why", view.redactedWhy],
        ]),
    ],
  );
}

/**
 * KYO-587 (KYO-583): an artifact the CLI recognised as recx search output is
 * drawn with `kyo show`'s own lines before it, and its text -- written by the authors of
 * the repositories recx found -- is set as plain text only: no `[[id]]` or URL becomes a
 * link and nothing is parsed as markup (c2). Every other artifact is drawn as before.
 */
function evidenceBody(view: EvidenceView, surface: LinkSurface): HTMLElement[] {
  if (view.untrustedNotice === null) {
    return [linkedProse(view.text, surface, { class: "child-text" })];
  }
  return [
    el(
      "div",
      { class: "child-notice", data: { region: "untrusted-notice" } },
      view.untrustedNotice.map((line) => el("p", { text: line })),
    ),
    el("div", {
      class: "prose child-text",
      data: { region: "untrusted-text" },
      text: view.untrustedText ?? view.text,
    }),
  ];
}

function evidence(view: EvidenceView, surface: LinkSurface): HTMLElement {
  return child(
    "evidence",
    view.id,
    summaryOf("evidence", view.redactedAt === null ? "stored verbatim" : "redacted"),
    surface,
    [
      view.redactedAt !== null && tag("redacted", "warn"),
      view.untrustedNotice !== null && tag("recx search", "warn"),
    ],
    [
      ...evidenceBody(view, surface),
      el("div", { class: "child-meta" }, [
        el("span", { class: "meta-pair" }, [
          el("span", { class: "meta-label", text: "supports" }),
          view.supportsId === null
            ? el("span", { class: "meta-value", text: NOTHING })
            : idLink(view.supportsId, surface),
        ]),
        el("span", { class: "meta-pair" }, [
          el("span", { class: "meta-label", text: "by" }),
          el("span", { class: "meta-value", text: view.author }),
        ]),
        el("span", { class: "meta-pair" }, [
          el("span", { class: "meta-label", text: "at" }),
          el("span", { class: "meta-value", text: view.createdAt }),
        ]),
      ]),
      // The digest is the artifact's identity and the reason evidence is evidence.
      meta([["sha256", view.sha256]]),
      view.redactedAt !== null &&
        meta([
          ["redacted", view.redactedAt],
          ["why", view.redactedWhy],
        ]),
    ],
  );
}

function flag(view: FlagView, surface: LinkSurface): HTMLElement {
  return child(
    "flag",
    view.id,
    summaryOf("flag", `${view.flagType}, ${view.resolvedAt === null ? "unresolved" : "resolved"}`),
    surface,
    [
      tag(view.flagType, view.resolvedAt === null ? "warn" : "muted"),
      tag(
        view.resolvedAt === null ? "unresolved" : "resolved",
        view.resolvedAt === null ? "warn" : "good",
      ),
    ],
    [
      linkedProse(view.detail, surface, { class: "child-text" }),
      meta([
        ["raised", view.createdAt],
        ["resolved", view.resolvedAt],
        ["why", view.resolvedWhy],
      ]),
      (view.otherRecordId !== null || view.otherCriterionId !== null) &&
        el("div", { class: "child-meta" }, [
          view.otherRecordId !== null &&
            el("span", { class: "meta-pair" }, [
              el("span", { class: "meta-label", text: "other record" }),
              idLink(view.otherRecordId, surface),
            ]),
          view.otherCriterionId !== null &&
            el("span", { class: "meta-pair" }, [
              el("span", { class: "meta-label", text: "other criterion" }),
              idLink(view.otherCriterionId, surface),
            ]),
        ]),
    ],
  );
}

/** Whatever the report's own document says about each criterion, if it says anything. */
function verdictRows(body: Record<string, unknown>, surface: LinkSurface): HTMLElement | false {
  const verdicts = body.criteria_verdicts;
  if (!Array.isArray(verdicts) || verdicts.length === 0) return false;
  const rows = el("div", { class: "verdicts", data: { region: "report-verdicts" } });
  for (const entry of verdicts) {
    if (typeof entry !== "object" || entry === null) continue;
    const record = entry as Record<string, unknown>;
    const id = typeof record.criterion_id === "string" ? record.criterion_id : null;
    const verdict = typeof record.verdict === "string" ? record.verdict : NOTHING;
    const method = typeof record.method === "string" ? record.method : NOTHING;
    rows.append(
      el("div", { class: "verdict-row", data: { verdict } }, [
        id === null ? el("span", { text: NOTHING }) : idLink(id, surface),
        tag(verdict, verdict === "met" ? "good" : "open"),
        el("span", { class: "meta-value", text: method }),
      ]),
    );
  }
  return rows;
}

function report(view: ReportView, surface: LinkSurface): HTMLElement {
  return child(
    "report",
    view.id,
    summaryOf("report", view.current ? "the current one" : "superseded"),
    surface,
    [tag(view.current ? "current" : "superseded", view.current ? "good" : "muted")],
    [
      meta([
        ["by", view.author],
        ["at", view.createdAt],
        ["worked from", view.workedFromSha],
      ]),
      verdictRows(view.body, surface),
      // The whole report document, behind a control. Collapsed because a report is
      // pages long; PRESENT because a report is a thing the store holds about this
      // record, and a page that summarised it would be the tooltip problem again.
      expandable(`the whole report (${view.id})`, `report-${view.id}`, [json(view.body)]),
    ],
  );
}

/**
 * The other records this dispatch formally covers, as links (KYO-296).
 *
 * The line `kyo show` prints as `also formally covers`, on the primary record -- the
 * record the dispatch, the branch and the report all hang off. Until this, the app
 * drew the covered side (`coveredBy` above) and dropped this one, so the terminal and
 * the app disagreed about one line of the same dispatch.
 *
 * Every id is a link, on the same terms as every other id on this page: the reader's
 * errand from here is the record this worker also spoke for. Nothing is composed --
 * the ids come from `kyo show --json`'s per-dispatch `covers` through `dispatchViews`,
 * which is where the decision lives and where it is checked.
 *
 * Drawn only when there is something to draw, which is the rule the covered-by section
 * follows for the same reason: there is no "this dispatch covered nothing else" fact
 * to state, only the ordinary case of a worker sent about one record.
 */
function coversLine(view: DispatchView, surface: LinkSurface): HTMLElement | false {
  if (view.covers.length === 0) return false;
  return el("div", { class: "child-meta", data: { region: "dispatch-covers" } }, [
    el("span", { class: "meta-pair" }, [
      el("span", { class: "meta-label", text: "also formally covers" }),
      ...view.covers.map((id) => idLink(id, surface)),
    ]),
  ]);
}

function dispatch(view: DispatchView, surface: LinkSurface): HTMLElement {
  return child(
    "dispatch",
    view.id,
    summaryOf("dispatch", view.state),
    surface,
    [tag(view.state, view.state === "running" ? "warn" : "muted")],
    [
      meta([
        ["branch", view.branch],
        ["worktree", view.worktree],
        ["model", view.workerModel],
        ["worked from", view.workedFromSha],
        ["opened", view.createdAt],
        ["closed", view.closedAt],
      ]),
      coversLine(view, surface),
      view.terminatedWhy !== null &&
        linkedProse(view.terminatedWhy, surface, { class: "child-text" }),
      view.currentReport !== null && report(view.currentReport, surface),
      ...view.superseded.map((old) => report(old, surface)),
      view.currentReport === null &&
        view.superseded.length === 0 &&
        el("div", { class: "none", text: "no report" }),
    ],
  );
}

/** One `label: <link>` pair, where the value is an id rather than a word. */
function metaLink(label: string, id: string | null, surface: LinkSurface): HTMLElement {
  return el("span", { class: "meta-pair" }, [
    el("span", { class: "meta-label", text: label }),
    id === null
      ? el("span", { class: "meta-value", text: "none ingested yet" })
      : idLink(id, surface),
  ]);
}

/**
 * A dispatch on another record that formally covered this one (KYO-291).
 *
 * When several records are sent to one dispatched worker, the report lands only under
 * the first record. It still does --
 * a report is one artifact with one sha and splitting it would make two accounts of
 * one dispatch -- so this is the way back to it. Every id is a link, because the two
 * things a reader of a covered record wants are the record the work hangs off and the
 * report whose verdicts settled the criteria here.
 *
 * Its fields come from the CLI's own `covered_by_dispatches`; nothing here is derived,
 * and nothing here is a `DispatchView` -- this dispatch is not this record's.
 */
function coveredBy(view: CoveringDispatchView, surface: LinkSurface): HTMLElement {
  return child(
    "covered-by",
    view.id,
    summaryOf("covering dispatch", view.state),
    surface,
    [tag(view.state, view.state === "running" ? "warn" : "muted")],
    [
      el("div", { class: "child-meta" }, [
        metaLink("dispatched against", view.primaryRecordId, surface),
        metaLink("report", view.reportId, surface),
      ]),
      el("div", {
        class: "child-text plain",
        text:
          `This record was not dispatched itself: ${view.id} formally covers it, and ` +
          `everything that dispatch produced hangs off ${view.primaryRecordId}.`,
      }),
    ],
  );
}

function touch(view: TouchView, surface: LinkSurface): HTMLElement {
  return child(
    "touch",
    view.id,
    summaryOf("touch", view.path),
    surface,
    [],
    [
      meta([
        ["path", view.path],
        ["symbol", view.symbol],
      ]),
    ],
  );
}

function docRef(view: DocRefView, surface: LinkSurface): HTMLElement {
  return child(
    "doc-ref",
    view.id,
    summaryOf("doc-ref", view.docPath),
    surface,
    [],
    [
      meta([
        ["document", view.docPath],
        ["section", view.section],
      ]),
      view.note !== null && linkedProse(view.note, surface, { class: "child-text" }),
    ],
  );
}

function member(view: MemberView, surface: LinkSurface): HTMLElement {
  return child(
    "member",
    view.id,
    summaryOf("member", view.standing),
    surface,
    // KYO-280: a standing the CLI did not send is no pill at all, not an empty one.
    [tag(view.kind, "muted"), ...(view.standing === "" ? [] : [tag(view.standing, "open")])],
    [el("div", { class: "child-text plain", text: view.title }), meta([["status", view.status]])],
  );
}

/** A typed edge to another record: the other end is a link, because it is an id. */
function linkRow(view: LinkView, surface: LinkSurface): HTMLElement {
  return el(
    "div",
    { class: "link-row", data: { linkType: view.type, linkDirection: view.direction } },
    [
      tag(view.type, "muted"),
      el("span", { class: "meta-value", text: view.direction }),
      idLink(view.other, surface),
    ],
  );
}

/**
 * The obligations, collapsed by settlement -- and the collapse is the CLI's own.
 *
 * `docs_check.collapse_obligations` grouped these; KYO-219.c3 built that function so
 * this viewer would read the same grouping rather than deriving a second one. Each
 * group opens to its obligation ids, and each id is a link, because `KYO-232.o1` is
 * an id a deep link can name -- which is why the contents are in the DOM while the
 * group is shut.
 */
function obligations(view: ObligationCollapse, surface: LinkSurface): HTMLElement[] {
  return view.groups.map((group) =>
    el(
      "div",
      { class: "obligation-group", data: { obligationState: group.state, count: group.count } },
      [
        el("div", { class: "child-head" }, [
          tag(group.state, group.state === "open" ? "warn" : "good"),
          el("span", { class: "child-kind", text: `${group.count} obligation(s)` }),
        ]),
        group.settledWhy !== null &&
          linkedProse(group.settledWhy, surface, { class: "child-text" }),
        meta([
          ["method", group.method],
          ["by", group.author],
          ["at", group.settledAt],
        ]),
        expandable(`${group.count} obligation(s), ${group.state}`, `obligations-${group.state}`, [
          ...group.details.map((detail) =>
            el("div", { class: "obligation-detail" }, [
              el("div", { class: "child-text plain", text: detail.detail }),
              meta([
                ["documents", detail.docPaths.join(", ")],
                ["sections", detail.sections.join(", ")],
              ]),
              el(
                "div",
                { class: "id-list" },
                detail.obligationIds.map((id) =>
                  child(
                    "docs-obligation",
                    id,
                    summaryOf("docs obligation", group.state),
                    surface,
                    [],
                    [el("span", { class: "meta-value", text: detail.detail })],
                  ),
                ),
              ),
            ]),
          ),
          group.causes.length > 0 && meta([["causes", group.causes.join("; ")]]),
          group.dispatchIds.length > 0 &&
            el("div", { class: "id-list" }, [
              el("span", { class: "meta-label", text: "dispatches" }),
              ...group.dispatchIds.map((id) => idLink(id, surface)),
            ]),
        ]),
      ],
    ),
  );
}

// ------------------------------------------------------------------ the whole page

/** The record's own header: every scalar field the envelope carries. */
function header(doc: RecordDocument, surface: LinkSurface): HTMLElement {
  return el("header", { class: "record-head", data: { region: "record-head" } }, [
    el("h1", { class: "record-title" }, [
      idLink(doc.id, surface, { class: "record-id" }),
      el("span", { text: doc.title }),
    ]),
    el("div", { class: "chips" }, [
      tag(doc.kind, "muted"),
      tag(doc.status, "open"),
      // A knowledge record filed `open` reads `current` (KYO-121): both words are
      // shown, because showing one of them would be hiding what the store holds.
      el("span", {
        class: "tag",
        text: doc.standing,
        data: { tag: doc.standing, tone: "standing", standing: doc.standing },
      }),
      doc.scope !== null && tag(doc.scope, "muted"),
    ]),
    el("div", { class: "child-meta" }, [
      el("span", { class: "meta-pair" }, [
        el("span", { class: "meta-label", text: "plan" }),
        doc.plan === null
          ? el("span", { class: "meta-value", text: NOTHING })
          : idLink(doc.plan, surface),
      ]),
      el("span", { class: "meta-pair" }, [
        el("span", { class: "meta-label", text: "created" }),
        el("span", { class: "meta-value", text: doc.createdAt }),
      ]),
      el("span", { class: "meta-pair" }, [
        el("span", { class: "meta-label", text: "updated" }),
        el("span", { class: "meta-value", text: doc.updatedAt }),
      ]),
    ]),
  ]);
}

/**
 * The sections, in the order `kyo show` prints them.
 *
 * Deliberately the CLI's order and not one invented here: the controller reads the
 * record in a terminal and the human reads it in this app, and two orders would be
 * two records. Every section is drawn even when it is empty, `(none)`, exactly as
 * `kyo show` does -- an absent section and an empty one are different facts and a
 * page that drew only the non-empty ones could not tell a reader which it was.
 */
export function renderDocument(doc: RecordDocument, surface: LinkSurface): HTMLElement {
  return el("div", { class: "record-page", data: { region: "record-page" } }, [
    header(doc, surface),
    blockSection("description", [
      doc.description === ""
        ? el("div", { class: "none", text: "(none)" })
        : linkedProse(doc.description, surface, { data: { region: "description" } }),
      doc.wontDoReason !== null &&
        el("div", { class: "child-meta" }, [
          el("span", { class: "meta-pair" }, [
            el("span", { class: "meta-label", text: "won't do" }),
            el("span", { class: "meta-value", text: doc.wontDoReason }),
          ]),
        ]),
    ]),
    section(
      "criteria",
      doc.criteria.map((view) => criterion(view, surface)),
    ),
    section(
      "gates",
      doc.gates.map((view) => gate(view, surface)),
    ),
    section(
      "claims",
      doc.claims.map((view) => claim(view, surface)),
    ),
    section(
      "notes",
      doc.notes.map((view) => note(view, surface)),
    ),
    section(
      "evidence",
      doc.evidence.map((view) => evidence(view, surface)),
    ),
    section(
      "flags",
      doc.flags.map((view) => flag(view, surface)),
    ),
    section(
      "dispatches",
      doc.dispatches.map((view) => dispatch(view, surface)),
    ),
    // KYO-291, and the one section on this page that is NOT drawn when it is empty.
    //
    // Every block above states an absence, because an empty `criteria` and a missing
    // `criteria` are different facts about a record. This one is not a child object of
    // the record at all: it is another record's dispatch, reaching over. There is no
    // "no dispatch covers this record" fact to state -- that is simply the ordinary
    // case, true of nearly every record in a store -- and a `(none)` here on every
    // page would teach a reader to stop seeing the heading on the pages that have one.
    // `kyo show` draws it on exactly the same condition, and `--json` carries
    // `covered_by_dispatches` unconditionally for anything reading rather than looking.
    doc.coveredBy.length > 0 &&
      section(
        "covered by",
        doc.coveredBy.map((view) => coveredBy(view, surface)),
      ),
    section("obligations", obligations(doc.obligations, surface), [
      el("div", { class: "child-meta", data: { region: "obligation-counts" } }, [
        el("span", { class: "meta-pair" }, [
          el("span", { class: "meta-label", text: "open" }),
          el("span", {
            class: "meta-value",
            text: `${doc.obligations.open} of ${doc.obligations.total}`,
          }),
        ]),
        ...doc.obligations.countsByState.map((count) =>
          el("span", { class: "meta-pair" }, [
            el("span", { class: "meta-label", text: count.state }),
            el("span", { class: "meta-value", text: String(count.count) }),
          ]),
        ),
      ]),
    ]),
    section(
      "touches",
      doc.touches.map((view) => touch(view, surface)),
    ),
    section(
      "doc-refs",
      doc.docRefs.map((view) => docRef(view, surface)),
    ),
    section(
      "links",
      doc.links.map((view) => linkRow(view, surface)),
    ),
    section(
      "members",
      doc.members.map((view) => member(view, surface)),
    ),
    // A surface painting store facts owes the reader the verdict on those facts
    // (KYO-74), and the command that produced them.
    blockSection("provenance", [
      facts([
        [doc.currency.state, doc.currency.says],
        ["command", doc.command],
      ]),
    ]),
  ]);
}

/** What the page says when the read model refused. */
export function renderProblem(problem: string, command: string, surface: LinkSurface): HTMLElement {
  return el("div", { class: "empty", data: { region: "record-problem" } }, [
    el("p", {}, [linkedText(problem, surface)]),
    el("p", { class: "muted", text: command }),
  ]);
}
