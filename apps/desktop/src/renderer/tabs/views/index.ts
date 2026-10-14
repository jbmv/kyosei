// The views tab (KYO-235): records, the awaiting-human view, the brief, and one
// governed document's read-only rendering.
//
// `arg` says which view:
//
//   records             every record, as the read model lists them
//   awaiting            every open gate waiting on a person, across every status
//   brief               the latest session brief
//   brief:<id>          one session brief, as the store kept it
//   doc:<path>          one governed document, rendered (KYO-235.c3)
//   shift-note:<id>     a shift note a `kyosei://` link named
//
// The records SIDEBAR (search, saved bases, the "Awaiting you" pinned group, opening a
// row into a column) is the shell's own (`shell/sidebar.ts`, KYO-232) -- what lives here
// is the view a rail click or a link opens, over the same read model. `doc:<path>` is
// likewise reached from the shell's own "Docs" rail item today: `sidebar.ts` already
// opens a doc row as `{kind: "views", arg: "doc:<path>"}` (KYO-232.c5's placeholder
// wired that route before this record existed to fill it in), so the rendering below is
// what actually shows once a reader clicks a document, and the document view lives here
// rather than in a `docs` tab kind.

import type {
  AwaitingGate,
  DocEntry,
  KyoseiBridge,
  RecordsListRow,
} from "../../../shared/protocol";
import { el, facts, section } from "../../dom";
import { renderDocument } from "../docs/markdown";
import type { TabContext, TabInstance, TabKind } from "../kind";
import { idLink, linkifyText } from "./ids";
import { ensureViewsStyles } from "./styles";

function titleFor(arg: string): string {
  if (arg === "" || arg === "records") return "Records";
  if (arg === "awaiting") return "Awaiting you";
  if (arg === "brief") return "Latest brief";
  if (arg.startsWith("brief:")) return arg.slice("brief:".length);
  if (arg.startsWith("doc:")) return arg.slice("doc:".length).split("/").pop() ?? arg;
  if (arg.startsWith("shift-note:")) return arg.slice("shift-note:".length);
  return arg;
}

/** The project's record-id prefix, asked for once per tab and cached in its closure. */
async function projectPrefix(bridge: KyoseiBridge): Promise<string | null> {
  try {
    const liveFacts = await bridge.getFacts();
    return liveFacts.project.project;
  } catch {
    return null;
  }
}

/** One gate as the awaiting-human view draws it: the record it belongs to, and its own payload (KYO-160's rule that a payload's line breaks carry meaning, same as the record page). */
function awaitingRow(
  entry: AwaitingGate,
  onOpenId: ((id: string, newTab: boolean) => void) | null,
): HTMLElement {
  return el(
    "div",
    {
      class: "gate awaiting-row",
      data: {
        region: "awaiting-row",
        recordId: entry.recordId,
        gateId: entry.gate.id,
        gateType: entry.gate.type,
      },
    },
    [
      el("div", { class: "gate-head" }, [
        onOpenId === null ? el("span", { text: entry.recordId }) : idLink(entry.recordId, onOpenId),
        el("span", { text: ` ${entry.recordTitle} · ${entry.recordKind} · ${entry.recordStatus}` }),
      ]),
      el("div", { class: "muted", text: `${entry.gate.id} · ${entry.gate.type}` }),
      entry.gate.detail !== null &&
        el("div", {
          class: "prose gate-payload",
          data: { region: "gate-payload" },
          text: entry.gate.detail,
        }),
    ],
  );
}

function recordRow(row: RecordsListRow): HTMLElement {
  return el("div", { class: "row", data: { region: "views-record-row", recordId: row.id } }, [
    el("span", { class: "row-label", text: row.id }),
    el("span", { class: "row-detail", text: `${row.kind} · ${row.status} · ${row.title}` }),
  ]);
}

/** Mark each rendered `governs:` value with whether `kyo docs check` says it resolves. */
function markGovernsResolution(body: HTMLElement, doc: DocEntry | null): void {
  if (doc === null) return;
  const broken = new Set(doc.governsBroken);
  for (const value of body.querySelectorAll<HTMLElement>("[data-meta-value]")) {
    const target = value.dataset.metaValue ?? "";
    value.dataset.target = target;
    value.dataset.resolves = String(!broken.has(target));
  }
}

const kind: TabKind = {
  kind: "views",
  label: "Views",
  // Its rail entry ("Awaiting you", KYO-235) is gone with KYO-248, and its home entry
  // with KYO-257.c8: the rail's Awaiting list is the one place for it, and each row
  // carries the gate's instruction on hover. The page itself stays, for a saved layout
  // that still holds one.
  home: [
    {
      order: 60,
      label: "Latest brief",
      detail: "The most recent session brief",
      icon: "calendar",
      arg: "brief",
    },
    // No Records entry (KYO-257): the sidebar's records list serves better. The records
    // page itself stays, for a saved layout that still holds one.
  ],
  open(context: TabContext): TabInstance {
    ensureViewsStyles();
    const arg = context.arg === "" ? "records" : context.arg;
    let mount: HTMLElement | null = null;
    let closed = false;
    let prefix: string | null = null;

    const show = (...children: HTMLElement[]): void => {
      if (mount === null || closed) return;
      mount.dataset.state = "ready";
      mount.replaceChildren(el("div", { class: "raw" }, children));
    };

    const problem = (text: string): void => {
      if (mount === null || closed) return;
      mount.dataset.state = "problem";
      mount.replaceChildren(el("div", { class: "empty", text }));
    };

    /** Open an id this view found -- in a record, a child, a shift note, a brief. */
    const openId = (id: string, newTab = false): void => {
      if (prefix === null) return;
      context.followLink(`kyosei://${prefix}/${id}`, { newTab });
    };

    async function paint(): Promise<void> {
      if (arg === "records") {
        const list = await context.bridge.getRecordsList();
        if (!list.ok) return problem(list.problem ?? "the records list could not be read");
        return show(
          el("h1", { text: "Records" }),
          el(
            "div",
            { class: "results", data: { region: "views-records" } },
            list.rows.map(recordRow),
          ),
          section("currency", facts([[list.currency.state, list.currency.says]])),
        );
      }
      if (arg === "awaiting") {
        prefix = await projectPrefix(context.bridge);
        const view = await context.bridge.getAwaitingHuman();
        if (!view.ok) return problem(view.problem ?? "the awaiting-human query failed");
        return show(
          el("h1", { text: `Awaiting you (${view.count})`, data: { region: "awaiting-count" } }),
          el("p", {
            class: "muted",
            text: `Every open gate of type ${view.types.join(" or ")}, across every record status.`,
          }),
          view.gates.length === 0
            ? el("div", { class: "empty", text: "Nothing is waiting on a person right now." })
            : el(
                "div",
                { class: "results", data: { region: "awaiting-list" } },
                view.gates.map((entry) => awaitingRow(entry, prefix === null ? null : openId)),
              ),
        );
      }
      if (arg === "brief") {
        const [brief, resolvedPrefix] = await Promise.all([
          context.bridge.getLatestBrief(),
          projectPrefix(context.bridge),
        ]);
        prefix = resolvedPrefix;
        if (!brief.ok) return problem(brief.problem ?? "the latest brief could not be read");
        if (brief.run === null) return problem("This store holds no session briefs yet.");
        const onOpen = prefix === null ? null : openId;
        return show(
          el("h1", { text: `Brief ${brief.run.id}` }),
          facts([
            ["at", brief.run.at],
            ["actor", brief.run.actor],
            ["session start", String(brief.run.sessionStart)],
            ["previous", brief.previousBriefId ?? ""],
          ]),
          ...brief.sections.map((s) =>
            el("section", { class: "raw-section", data: { section: s.key } }, [
              el("h2", { text: s.title }),
              s.items.length === 0
                ? el("p", { class: "muted", text: "(nothing in this window)" })
                : el(
                    "dl",
                    { class: "facts" },
                    s.items.flatMap((item) => [
                      el("dt", {}, [
                        // A shift-note id resolves (TARGET_KINDS carries "shift-note"); an
                        // event id does not -- `main/links.ts`'s ledger-event shape opens
                        // nothing, so it is shown as text rather than offered as a click
                        // that can only ever refuse.
                        onOpen === null || item.kind !== "shift-note"
                          ? el("span", { text: item.id })
                          : idLink(item.id, onOpen),
                        item.recordId === null
                          ? null
                          : el("span", { class: "muted" }, [
                              " — ",
                              onOpen === null
                                ? el("span", { text: item.recordId })
                                : idLink(item.recordId, onOpen),
                            ]),
                      ]),
                      el(
                        "dd",
                        { data: { region: "brief-item-text" } },
                        linkifyText(item.text, onOpen),
                      ),
                    ]),
                  ),
            ]),
          ),
        );
      }
      if (arg.startsWith("brief:")) {
        const [doc, resolvedPrefix] = await Promise.all([
          context.bridge.getBrief(arg.slice("brief:".length)),
          projectPrefix(context.bridge),
        ]);
        prefix = resolvedPrefix;
        if (!doc.ok || doc.run === null) {
          return problem(doc.problem ?? "that brief could not be read");
        }
        const onOpen = prefix === null ? null : openId;
        return show(
          el("h1", { text: `Brief ${doc.run.id}` }),
          facts([
            ["at", doc.run.at],
            ["actor", doc.run.actor],
            ["previous", doc.previousBriefId ?? ""],
          ]),
          section(
            "shift notes",
            doc.shiftNotes.length === 0
              ? el("p", { class: "muted", text: "(none)" })
              : el(
                  "dl",
                  { class: "facts" },
                  doc.shiftNotes.flatMap((n) => [
                    el("dt", {}, [
                      onOpen === null ? el("span", { text: n.id }) : idLink(n.id, onOpen),
                    ]),
                    el("dd", {}, linkifyText(n.text, onOpen)),
                  ]),
                ),
          ),
          section(
            "events",
            doc.events.length === 0
              ? el("p", { class: "muted", text: "(none)" })
              : el(
                  "dl",
                  { class: "facts" },
                  doc.events.flatMap((e) => [
                    el("dt", {}, [
                      // A ledger event id opens nothing (`main/links.ts`'s "ledger event"
                      // shape) -- shown as text, same reasoning as the "brief" case above.
                      el("span", { text: e.id }),
                      e.recordId === null
                        ? null
                        : el("span", { class: "muted" }, [
                            " — ",
                            onOpen === null
                              ? el("span", { text: e.recordId })
                              : idLink(e.recordId, onOpen),
                          ]),
                    ]),
                    el("dd", { data: { region: "brief-item-text" } }, [
                      el("span", { class: "muted", text: `${e.actor}: ` }),
                      ...linkifyText(e.command, onOpen),
                    ]),
                  ]),
                ),
          ),
        );
      }
      if (arg.startsWith("doc:")) {
        const path = arg.slice("doc:".length);
        const [doc, tree] = await Promise.all([
          context.bridge.openDoc(path),
          context.bridge.getDocTree(),
        ]);
        if (!doc.ok) return problem(doc.problem ?? `${path} could not be read`);
        const entry = tree.ok ? (tree.documents.find((d) => d.path === path) ?? null) : null;
        const rendered = renderDocument(doc.text);
        markGovernsResolution(rendered.body, entry);
        const banner =
          entry === null
            ? null
            : el(
                "div",
                {
                  class: "doc-banner",
                  data: {
                    region: "doc-banner",
                    staleness: entry.staleness,
                    answered: String(entry.staleness === "current" || entry.staleness === "stale"),
                  },
                },
                [
                  el("span", {
                    class: "doc-staleness",
                    data: { region: "doc-staleness", staleness: entry.staleness },
                    text: entry.staleness,
                  }),
                  el("span", { class: "doc-banner-says muted", text: entry.stalenessSays }),
                  entry.governs.length === 0
                    ? null
                    : el(
                        "div",
                        { class: "doc-banner-governs", data: { region: "doc-banner-governs" } },
                        entry.governs.map((target) =>
                          el("span", {
                            class: "doc-governs-target mono",
                            data: {
                              target,
                              resolves: String(!entry.governsBroken.includes(target)),
                            },
                            text: target,
                          }),
                        ),
                      ),
                ],
              );
        return show(
          el("h1", { text: doc.path }),
          ...(banner === null ? [] : [banner]),
          el("div", { class: "doc-view", data: { region: "doc-view", doc: doc.path } }, [
            rendered.body,
          ]),
        );
      }
      if (arg.startsWith("shift-note:")) {
        // By id, out of the whole catalogue (KYO-248 built the list the Notes sidebar
        // draws, which is every note); before it this view could only show the latest
        // and name what was asked for.
        const asked = arg.slice("shift-note:".length);
        const catalogue = await context.bridge.getShiftNotes();
        if (!catalogue.ok) {
          return problem(catalogue.problem ?? "the shift notes could not be read");
        }
        const note = catalogue.notes.find((candidate) => candidate.id === asked);
        if (note === undefined) return problem(`This store holds no shift note called ${asked}.`);
        prefix = await projectPrefix(context.bridge);
        const onOpen = prefix === null ? null : openId;
        return show(
          el("h1", { text: `Shift note ${note.id}`, data: { region: "shift-note-title" } }),
          facts([
            ["author", note.author],
            ["at", note.createdAt],
            ["handoff", note.handoffFor === null ? "" : `opened ${note.handoffFor}`],
          ]),
          el(
            "div",
            { class: "prose", data: { region: "shift-note-text" } },
            note.redactedAt === null
              ? linkifyText(note.text, onOpen)
              : [`[redacted: ${note.redactedWhy ?? ""}]`],
          ),
        );
      }
      return problem(`views: nothing is called “${arg}”.`);
    }

    return {
      title: () => titleFor(arg),
      render(host) {
        mount = host;
        host.dataset.state = "loading";
        host.dataset.view = arg;
        host.replaceChildren(el("div", { class: "empty", text: "…" }));
        void paint().catch((error: unknown) => problem(`kyosei: ${String(error)}`));
      },
      close() {
        closed = true;
      },
    };
  },
};

export default kind;
