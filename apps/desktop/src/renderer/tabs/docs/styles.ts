// The rendered document's CSS (KYO-253, settling KYO-242): every `.md-*` class
// `markdown.ts` writes and every `.mmd-*` class `mermaid.ts` writes has a rule here.
//
// These renderers were recovered from v2 for KYO-235, and their rules were not: they
// lived in v2's deleted stylesheet, so a governed document opened in the views tab
// rendered as the browser's defaults -- Times-sized headings, blue underlined links,
// black-on-white diagram nodes with no fill. `views/styles.ts` reported the gap rather
// than guessing at it (KYO-240.d1); this file closes it.
//
// This directory is not a tab kind (nothing opens a "docs" tab; the views tab draws a
// document), so the sheet is installed by `renderDocument` itself, the one entry point
// through which any document is drawn. `installStyles` is idempotent, so the call
// costs nothing after the first.
//
// The rules use the shell's own custom properties and bring no palette of their own.

import { installStyles } from "../kind";

const DOCS_CSS = `
/* ------------------------------------------------------------------ the document */

.doc-view {
  max-width: 760px;
  user-select: text;
}

.md {
  font-size: var(--fs-md);
  line-height: 1.55;
}

/* Written two classes deep: the views tab draws a document inside its \`.raw\` page,
   whose own \`h1\`/\`h2\` placeholder rules would otherwise outrank a bare class. */
.md .md-h {
  margin: 22px 0 8px;
  font-weight: 600;
  line-height: 1.25;
  letter-spacing: -0.1px;
  text-transform: none;
  color: var(--fg);
}

.md .md-h:first-child {
  margin-top: 6px;
}

.md .md-h1 {
  font-size: var(--fs-2xl);
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border);
}

.md .md-h2 {
  font-size: var(--fs-xl);
}

.md .md-h3 {
  font-size: var(--fs-lg);
}

.md .md-h4,
.md .md-h5,
.md .md-h6 {
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  font-size: var(--fs-xs);
}

.md-p {
  margin: 0 0 10px;
}

.md-list {
  margin: 0 0 10px;
  padding-left: 24px;
}

.md-li {
  margin: 2px 0;
}

/* Nested items: the depth is written as a class because the list is flat in the DOM. */
.md-li-1 {
  margin-left: 20px;
  list-style-type: circle;
}

.md-li-2 {
  margin-left: 40px;
  list-style-type: square;
}

.md-li-3 {
  margin-left: 60px;
  list-style-type: square;
}

.md-link {
  color: var(--accent);
  text-decoration: underline;
  text-decoration-color: color-mix(in srgb, var(--accent) 35%, transparent);
  text-underline-offset: 2px;
  background: none;
  border: 0;
  padding: 0;
  font: inherit;
  cursor: pointer;
}

.md-link:hover {
  text-decoration-color: var(--accent);
}

.md-code {
  font-family: var(--mono);
  font-size: 0.92em;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--sunken);
}

.md-pre {
  margin: 0 0 12px;
  padding: 10px 12px;
  border-radius: var(--radius);
  background: var(--sunken);
  font-family: var(--mono);
  font-size: var(--fs-sm);
  line-height: 1.45;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.md-pre code {
  font: inherit;
}

.md-quote {
  margin: 0 0 10px;
  padding: 2px 0 2px 12px;
  border-left: 3px solid var(--border-strong);
  color: var(--muted);
}

.md-rule {
  border: 0;
  border-top: 1px solid var(--border);
  margin: 16px 0;
}

/* Front matter, one line per key, drawn before the body as the file's own facts. */
.md-meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 1px 0;
  font-size: var(--fs-xs);
  color: var(--muted);
}

.md-meta + :not(.md-meta) {
  margin-top: 12px;
}

.md-meta-key {
  color: var(--muted-2);
}

.md-meta-value {
  overflow-wrap: anywhere;
}

.md-meta-empty {
  color: var(--muted-2);
  font-style: italic;
}

/* ---------------------------------------------------------------- the diagrams */

.md-diagram {
  margin: 4px 0 14px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  overflow: auto;
}

/* A mermaid block the renderer could not draw: shown as its source, and said so. */
.md-diagram-undrawn {
  border-style: dashed;
  border-color: var(--border-strong);
  background: var(--sunken);
}

.md-diagram-undrawn .md-pre {
  margin: 0;
  background: none;
  padding: 0;
}

.md-diagram-note {
  margin-bottom: 8px;
  font-size: var(--fs-xs);
  color: var(--warn);
}

.mmd {
  display: block;
  max-width: 100%;
  height: auto;
  font-family: var(--font);
  font-size: var(--fs-sm);
}

.mmd-node {
  fill: var(--surface);
  stroke: var(--border-strong);
  stroke-width: 1.2;
}

/* The shapes a flowchart node can take; the diamond (a decision) reads as a question. */
.mmd-rect,
.mmd-round,
.mmd-stadium,
.mmd-circle {
  fill: var(--surface);
}

.mmd-diamond {
  fill: var(--sunken);
}

.mmd-node-group:hover .mmd-node {
  stroke: var(--accent);
  fill: var(--accent-soft);
}

.mmd-label {
  fill: var(--fg);
  font-size: var(--fs-sm);
}

.mmd-edge {
  fill: none;
  stroke: var(--muted);
  stroke-width: 1.2;
}

.mmd-edge-dashed {
  stroke-dasharray: 5 3;
}

.mmd-edge-label {
  fill: var(--muted);
  font-size: var(--fs-xs);
}

.mmd-arrowhead {
  fill: var(--muted);
}
`;

/** Install the document renderer's rules. Called by `renderDocument`. */
export function ensureDocsStyles(): void {
  installStyles("docs", DOCS_CSS);
}
