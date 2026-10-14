// A document, rendered. Read-only, and small on purpose. Ported verbatim from v2
// (KYO-139) for KYO-235's docs tab -- see `mermaid.ts`'s note on why
// this directory recovers rather than reinvents these two files. The only edits from
// the original are the import path (`../dom` -> `../../dom`, since this directory is
// one level deeper in v3's tree) and the drop of the `headingId`-driven outline pane,
// which was v2's right rail and has no v3 counterpart to feed (KYO-232 deleted the
// three-pane layout entire); `headingId` itself is kept, because a heading still needs
// a stable anchor for `kyosei://` deep links (KYO-230) to scroll to.
//
// Records mode shows a record's description in a `<pre class="doc-prose">`, which is
// right for it: a description is a paragraph or two and its markdown is incidental.
// A Layer 3 document is not that -- it is `charter.md`, and a viewer that showed it as
// one long monospace block would be a worse way to read it than the editor the reader
// already has. So headings, lists, quotes, code and mermaid are rendered as themselves.
//
// This is NOT a markdown implementation and does not try to be. It reads the subset
// this project's documents are written in; anything it does not recognise falls through
// to a paragraph carrying the source line, which is the honest failure -- the text is
// still there and still readable, just not styled. Nothing here is ever asked to
// round-trip: docs mode has no editor and no save path (KYO-136 decision 6), so a
// rendering that loses a detail of the source loses it from the SCREEN, never from
// the file.
//
// Built through `el()` and `textContent`, never `innerHTML`. A document is a file this
// app did not write, and the escaping question is one to not have rather than to get
// right.

import { type Child, el } from "../../dom";
import { diagramKind, renderMermaid } from "./mermaid";
import { ensureDocsStyles } from "./styles";

/** The section-metadata keys the charter's Layer 3 defines, as `docs_check` reads them. */
const METADATA_KEYS = ["governs", "status", "amended-by"] as const;

const METADATA = new RegExp(`^\\s*(${METADATA_KEYS.join("|")}):\\s*(.*)$`, "i");
const FENCE = /^(\s*)(`{3,}|~{3,})\s*(\S*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^(\s*)[-*+]\s+(.*)$/;
const NUMBERED = /^(\s*)(\d+)[.)]\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;

/** A heading's anchor, so a `kyosei://` link naming a section can scroll to it. */
export function headingId(index: number): string {
  return `doc-h${index}`;
}

// ------------------------------------------------------------------ inline spans

const INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)]*\))|(\*[^*]+\*|_[^_]+_)/;

/**
 * Inline code, bold, italic and links, as DOM nodes.
 *
 * A link is rendered as its TEXT plus its target in a tooltip and is deliberately not
 * an `<a href>`: the renderer's CSP allows no remote origin, a relative href inside an
 * Electron renderer navigates the app's own window away from itself, and docs mode has
 * no navigation model for either. Showing where a link points without offering to
 * follow it is the honest half.
 */
export function inlineSpans(text: string): Child[] {
  const out: Child[] = [];
  let rest = text;
  for (;;) {
    const found = rest.match(INLINE);
    if (found === null || found.index === undefined) break;
    if (found.index > 0) out.push(document.createTextNode(rest.slice(0, found.index)));
    const token = found[0];
    if (token.startsWith("`")) {
      out.push(el("code", { class: "md-code", text: token.slice(1, -1) }));
    } else if (token.startsWith("**")) {
      out.push(el("strong", { text: token.slice(2, -2) }));
    } else if (token.startsWith("[")) {
      const cut = token.indexOf("](");
      out.push(
        el("span", {
          class: "md-link",
          text: token.slice(1, cut),
          tip: `links to ${token.slice(cut + 2, -1)} — docs mode reads, it does not navigate`,
        }),
      );
    } else {
      out.push(el("em", { text: token.slice(1, -1) }));
    }
    rest = rest.slice(found.index + token.length);
  }
  if (rest !== "") out.push(document.createTextNode(rest));
  return out;
}

// ---------------------------------------------------------------- fenced blocks

/**
 * One fenced block: a mermaid diagram drawn, or code shown as code.
 *
 * The two ways a mermaid block can fail to draw are DIFFERENT and are told apart, which
 * is the whole of law 6 applied to a picture. A dialect this build does not read
 * (`sequenceDiagram`, `gantt`, ...) says so and names the dialect. A `flowchart` whose
 * syntax `parseFlowchart` refused says THAT, because the reader can look at the source
 * below and see what it choked on. Neither renders as a blank space where a diagram
 * ought to be, and neither renders as a diagram.
 */
function fenceBlock(info: string, body: string): HTMLElement {
  const language = info.toLowerCase();
  if (language === "mermaid") {
    const drawn = renderMermaid(body);
    if (drawn !== null) {
      return el(
        "figure",
        { class: "md-diagram", data: { region: "doc-diagram", diagram: "drawn" } },
        [drawn],
      );
    }
    const kind = diagramKind(body);
    const known = kind === "flowchart" || kind === "graph";
    return el(
      "figure",
      {
        class: "md-diagram md-diagram-undrawn",
        data: { region: "doc-diagram", diagram: "source", "diagram-kind": kind || "(none)" },
      },
      [
        el("figcaption", {
          class: "md-diagram-note",
          text: known
            ? `mermaid ${kind}: this build could not read the syntax of this diagram, so its source is shown rather than a picture that might be wrong.`
            : `mermaid ${kind || "diagram"}: this build draws flowcharts only, so its source is shown rather than a picture that might be wrong.`,
        }),
        el("pre", { class: "md-pre" }, [el("code", { text: body })]),
      ],
    );
  }
  return el(
    "pre",
    { class: "md-pre", data: { region: "doc-code", language: language || "text" } },
    [el("code", { text: body })],
  );
}

// ------------------------------------------------------------------- the render

/** One heading a `kyosei://` deep link can point at. */
export interface RenderedHeading {
  heading: string;
  level: number;
  id: string;
}

export interface RenderedDoc {
  body: HTMLElement;
  headings: RenderedHeading[];
}

/**
 * Render a document, and report the headings it drew.
 *
 * The headings are read off THIS pass rather than a second scan of the same text: a
 * heading the body drew and a heading a caller wants to scroll to have to be the same
 * heading or the anchor goes nowhere, and two scans of one file is exactly how they
 * come apart.
 */
export function renderDocument(text: string): RenderedDoc {
  ensureDocsStyles();
  const body = el("div", { class: "md", data: { region: "doc-body" } });
  const headings: RenderedHeading[] = [];
  const lines = text.split("\n");

  let paragraph: string[] = [];
  let quote: string[] = [];
  let listItems: { text: string; depth: number }[] = [];
  let listOrdered = false;

  const flushParagraph = (): void => {
    if (paragraph.length === 0) return;
    body.append(el("p", { class: "md-p" }, inlineSpans(paragraph.join(" "))));
    paragraph = [];
  };
  const flushQuote = (): void => {
    if (quote.length === 0) return;
    body.append(el("blockquote", { class: "md-quote" }, inlineSpans(quote.join(" "))));
    quote = [];
  };
  const flushList = (): void => {
    if (listItems.length === 0) return;
    body.append(
      el(
        listOrdered ? "ol" : "ul",
        { class: "md-list" },
        listItems.map((item) =>
          el("li", { class: `md-li md-li-${Math.min(item.depth, 3)}` }, inlineSpans(item.text)),
        ),
      ),
    );
    listItems = [];
  };
  const flushAll = (): void => {
    flushParagraph();
    flushQuote();
    flushList();
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";

    // A fence swallows its body verbatim, before anything else looks at it. This is
    // what keeps a `governs:` line or a `#` heading INSIDE an example out of the
    // rendering as a live one -- the same distinction `docs_check._fenced` makes, and
    // the reason a README's own explanation of the format is not read as a claim.
    const fence = line.match(FENCE);
    if (fence !== null) {
      flushAll();
      const closer = fence[2] ?? "```";
      const collected: string[] = [];
      index += 1;
      for (; index < lines.length; index += 1) {
        const inner = lines[index] ?? "";
        const end = inner.match(FENCE);
        if (
          end !== null &&
          (end[2] ?? "").startsWith(closer[0] ?? "`") &&
          (end[2] ?? "").length >= closer.length &&
          (end[3] ?? "") === ""
        ) {
          break;
        }
        collected.push(inner);
      }
      body.append(fenceBlock(fence[3] ?? "", collected.join("\n")));
      continue;
    }

    if (line.trim() === "") {
      flushAll();
      continue;
    }

    const heading = line.match(HEADING);
    if (heading !== null) {
      flushAll();
      const level = (heading[1] ?? "#").length;
      const label = (heading[2] ?? "").replace(/\s+#+\s*$/, "").trim();
      const id = headingId(headings.length);
      headings.push({ heading: label, level, id });
      body.append(
        el(
          `h${Math.min(level, 6)}` as "h1",
          {
            class: `md-h md-h${level}`,
            data: { region: "doc-heading", level: String(level) },
            attrs: { id },
          },
          inlineSpans(label),
        ),
      );
      continue;
    }

    // Layer 3 section metadata, drawn as the claim it is rather than as prose. This is
    // the governance layer inside the body: `governs:` is what makes the section a
    // claim about code, and rendering it as an ordinary paragraph would bury the one
    // line a reader opened this mode to find.
    const metadata = line.match(METADATA);
    if (metadata !== null) {
      flushAll();
      const key = (metadata[1] ?? "").toLowerCase();
      const values = (metadata[2] ?? "")
        .split(",")
        .map((value) => value.trim())
        .filter((value) => value !== "");
      body.append(
        el("div", { class: "md-meta", data: { region: "doc-metadata", key } }, [
          el("span", { class: "md-meta-key mono", text: `${key}:` }),
          ...values.map((value) =>
            el("span", { class: "md-meta-value mono", data: { metaValue: value }, text: value }),
          ),
          values.length === 0 ? el("span", { class: "md-meta-empty", text: "(nothing)" }) : null,
        ]),
      );
      continue;
    }

    if (RULE.test(line)) {
      flushAll();
      body.append(el("hr", { class: "md-rule" }));
      continue;
    }

    const quoted = line.match(QUOTE);
    if (quoted !== null) {
      flushParagraph();
      flushList();
      quote.push(quoted[1] ?? "");
      continue;
    }

    const numbered = line.match(NUMBERED);
    const bullet = numbered === null ? line.match(BULLET) : null;
    if (numbered !== null || bullet !== null) {
      flushParagraph();
      flushQuote();
      const ordered = numbered !== null;
      if (listItems.length > 0 && ordered !== listOrdered) flushList();
      listOrdered = ordered;
      const indent = (numbered?.[1] ?? bullet?.[1] ?? "").length;
      listItems.push({
        text: numbered?.[3] ?? bullet?.[2] ?? "",
        depth: Math.floor(indent / 2),
      });
      continue;
    }

    // A continuation of the list item above, not a new paragraph: a wrapped bullet is
    // the commonest shape in this project's own documents.
    if (listItems.length > 0 && /^\s+\S/.test(line)) {
      const last = listItems[listItems.length - 1];
      if (last !== undefined) last.text += ` ${line.trim()}`;
      continue;
    }

    flushQuote();
    flushList();
    paragraph.push(line.trim());
  }
  flushAll();
  return { body, headings };
}
