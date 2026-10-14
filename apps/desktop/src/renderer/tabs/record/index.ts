// The record tab (KYO-233).
//
// What is in this file is the TAB: its argument, its lifecycle, and the anchor. The
// page itself -- every child object as a section -- is `page.ts`, and every link on
// it is drawn by the shared link renderer in `renderer/links/`.
//
// --------------------------------------------------------------------- the argument
//
// The record id, and nothing else. The brief for this record described a second form
// -- `FIX-1#FIX-1.g1`, the record with its anchor in the argument -- and it is NOT
// what shipped, for two reasons stated here because the argument is the tab's
// IDENTITY:
//
//   nothing produces it. `shell/shell.ts`'s `onDelivery` opens `{kind: "record",
//   arg: target.id}` and keeps `target.anchor` for its own strip, so no anchor has
//   ever reached a tab argument; a grammar nothing writes is a branch nothing runs.
//
//   it would be wrong if something did. `{kind, arg}` is what the shell dedups on
//   and what a saved layout stores, so two links into one record at two children
//   would be TWO TABS on the same record rather than one whose highlight moved --
//   and a restored layout would reopen a reader where a link once sent them.
//
// ------------------------------------------------------------------- the anchor
//
// So an anchor reaches this tab exactly one way: the resolved delivery. The
// controller names `FIX-1.g1` in the chat, the reader clicks it, `main/links.ts`
// resolves it, and `renderer/links/` holds the feed of those deliveries -- which
// says at its own top why it subscribes at module load rather than per tab.
//
// The anchor is INSTANCE STATE and re-applied on every paint, because `render` may be
// called again (a move between columns re-mounts the tab) and a highlight that
// vanished when the reader dragged the tab somewhere would be the app losing the one
// thing the link was for.

import type { RecordDocument } from "../../../shared/protocol";
import { el } from "../../dom";
import { latestOpenRequest, onOpenRequest } from "../../links";
import type { TabContext, TabInstance, TabKind } from "../kind";
import { expandFor, renderDocument, renderProblem } from "./page";
import { ensureRecordStyles } from "./styles";

const kind: TabKind = {
  kind: "record",
  label: "Record",
  open(context: TabContext): TabInstance {
    ensureRecordStyles();
    const asked = context.arg;
    let title = asked === "" ? "Record" : asked;
    let mount: HTMLElement | null = null;
    let closed = false;
    let document_: RecordDocument | null = null;
    let problem: string | null = null;

    /** The child this tab is pointed at, and the link that pointed it there. */
    let anchor: string | null = null;
    let sentFrom: string | null = null;
    /** The delivery already taken, so a repaint does not re-take an older one. */
    let takenSeq = 0;

    /**
     * Scroll the anchored child into view, highlight it, and say what it is.
     *
     * Everything it needs is in the DOM the page just drew: the child carries the id
     * the read model gave it and a one-line summary of itself, so the strip quotes
     * the record rather than a sentence this file made up about it.
     */
    const applyAnchor = (page: HTMLElement): void => {
      const strip = page.querySelector<HTMLElement>('[data-region="record-sent-here"]');
      if (anchor === null || strip === null) return;
      const target = page.querySelector<HTMLElement>(`[data-child-id="${CSS.escape(anchor)}"]`);
      if (target === null) {
        strip.dataset.tone = "missing";
        strip.replaceChildren(
          el("span", {
            text:
              `sent here: ${anchor}, which this record does not carry. It may have been ` +
              "written since this page was read, or it may belong to another record.",
          }),
        );
        strip.hidden = false;
        return;
      }
      expandFor(target);
      for (const previous of page.querySelectorAll<HTMLElement>('[data-anchored="true"]')) {
        delete previous.dataset.anchored;
      }
      target.dataset.anchored = "true";
      strip.dataset.tone = "sent-here";
      strip.replaceChildren(
        el("span", {
          class: "sent-here-text",
          text: `sent here: ${anchor}, ${target.dataset.childSummary ?? "a child of this record"}${
            sentFrom === null ? "" : ` — from ${sentFrom}`
          }`,
        }),
        el("button", {
          class: "sent-here-close",
          text: "×",
          tip: "Dismiss",
          data: { action: "dismiss-sent-here" },
          on: {
            click: () => {
              strip.hidden = true;
              delete target.dataset.anchored;
            },
          },
        }),
      );
      strip.hidden = false;
      // `block: "center"` rather than `"start"`: the strip sits above the scroller, so
      // a child scrolled to the top edge reads as one the reader has to look for.
      target.scrollIntoView({ block: "center", behavior: "auto" });
    };

    const paint = (): void => {
      if (mount === null || closed) return;
      const surface = { bridge: context.bridge, followLink: context.followLink };

      if (document_ === null || !document_.ok) {
        mount.dataset.state = "problem";
        mount.replaceChildren(
          renderProblem(
            problem ?? document_?.refusal ?? "this record could not be read",
            document_?.command ?? "",
            surface,
          ),
        );
        return;
      }

      title = `${document_.id} ${document_.title}`;
      context.setTitle(title);
      mount.dataset.state = "ready";
      mount.dataset.recordId = document_.id;
      mount.dataset.anchor = anchor ?? "";
      const strip = el("div", { class: "sent-here", data: { region: "record-sent-here" } });
      strip.hidden = true;
      const page = el("div", { class: "record-scroll" }, [
        strip,
        renderDocument(document_, surface),
      ]);
      mount.replaceChildren(page);
      applyAnchor(page);
    };

    /**
     * A link was followed somewhere in the window: is it about this record?
     *
     * Compared against the tab's own record id, which is the id the resolver put in
     * the delivery -- not against the URL, and not by taking the id apart.
     */
    const take = (seq: number, id: string, at: string | null, url: string): boolean => {
      if (seq <= takenSeq || id !== asked || at === null) return false;
      takenSeq = seq;
      anchor = at;
      sentFrom = url;
      return true;
    };

    const off = onOpenRequest((seen) => {
      if (!take(seen.seq, seen.request.target.id, seen.request.target.anchor, seen.request.url)) {
        return;
      }
      paint();
    });

    return {
      title: () => title,
      render(host) {
        mount = host;
        host.dataset.state = "loading";
        host.replaceChildren(el("div", { class: "empty", text: "…" }));
        // The delivery that OPENED this tab, if that is why it exists: the shell
        // received it, opened the tab, and the tab was not subscribed yet when it
        // did. Read once, here, rather than raced for.
        const seen = latestOpenRequest();
        if (seen !== null) {
          take(seen.seq, seen.request.target.id, seen.request.target.anchor, seen.request.url);
        }
        void context.bridge
          .getRecordDocument(asked)
          .then((answer) => {
            document_ = answer;
            problem = null;
            paint();
          })
          .catch((error: unknown) => {
            document_ = null;
            problem = String(error);
            paint();
          });
      },
      close() {
        closed = true;
        off();
      },
    };
  },
};

export default kind;
