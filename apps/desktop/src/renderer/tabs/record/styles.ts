// The record page's own CSS, injected by the tab that draws it.
//
// Why it is here and not in `shell.css`: `tabs/kind.ts` states the convention and the
// reason -- a tab kind is a directory, and a stylesheet every kind has to edit takes
// that back. `installStyles` is that file's, so every sheet in this app installs the
// same way and a check can enumerate `style[data-styles]`.
//
// It styles with the shell's own custom properties and brings no palette of its own.

import { installStyles } from "../kind";

const RECORD_CSS = `
/* The tab body is a flex column that scrolls; this is the scroller the sticky "sent
   here" strip sticks to, so the strip stays put while the reader moves down a record
   they were sent into the middle of. */
.record-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  user-select: text;
}

.record-page {
  padding: 14px 20px 48px;
  max-width: 920px;
}

/* ------------------------------------------------------------- the "sent here" strip */

.sent-here {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 12px;
  font-size: var(--fs-sm);
  background: var(--accent-soft);
  border-bottom: 1px solid var(--border);
}

/* The display:flex above is an author rule and outranks the user agent's
   [hidden] { display: none }, so a strip hidden by the hidden property would go on
   being drawn. Stated rather than worked around with a class. */
.sent-here[hidden] {
  display: none;
}

.sent-here[data-tone="missing"] {
  background: var(--bad-soft);
  color: var(--bad);
}

.sent-here-text {
  flex: 1 1 auto;
}

.sent-here-close {
  border: 0;
  background: none;
  color: var(--muted);
  font-size: var(--fs-lg);
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
}

/* ------------------------------------------------------------------------ the header */

.record-head {
  padding-bottom: 8px;
}

.record-title {
  font-size: var(--fs-xl);
  margin: 0 0 6px;
  display: flex;
  gap: 8px;
  align-items: baseline;
  flex-wrap: wrap;
}

.record-id {
  font-family: var(--mono);
  font-size: var(--fs-md);
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-bottom: 6px;
}

/* ----------------------------------------------------------------------- the sections */

.record-section {
  border-top: 1px solid var(--border);
  padding-top: 4px;
  margin-top: 14px;
}

.section-head {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: var(--fs-xs);
  font-weight: 700;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  color: var(--muted-2);
  margin: 0 0 6px;
}

.section-name {
  color: var(--muted);
}

.section-count {
  font-family: var(--mono);
  font-weight: 400;
  letter-spacing: 0;
}

/* A gate's payload, kept exactly as written: a procedure's indentation is part of the
   procedure (KYO-160, measured by \`tests/gate-payload.spec.ts\`). */
.gate-payload {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 4px 0;
  font-size: var(--fs-md);
  line-height: 1.5;
}

.none {
  color: var(--muted-2);
  font-size: var(--fs-sm);
  margin: 2px 0 6px;
}

/* ------------------------------------------------------------------- one child object */

.child {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  padding: 8px 10px;
  margin: 0 0 8px;
}

.child .child {
  background: var(--bg);
  margin-top: 8px;
  margin-bottom: 0;
}

.child[data-anchored="true"] {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.child-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 4px;
}

.child-id {
  font-family: var(--mono);
  font-size: var(--fs-xs);
}

.child-kind {
  font-size: var(--fs-xs);
  color: var(--muted-2);
}

.child-text {
  margin: 4px 0;
}

/* KYO-587: the lines before a recx search artifact, which say its text is strangers'. */
.child-notice {
  margin: 4px 0;
  padding: 6px 8px;
  border: 1px solid var(--warn-border);
  background: var(--warn-soft);
  color: var(--warn);
  font-size: var(--fs-xs);
  border-radius: 4px;
}

.child-notice p {
  margin: 0;
}

.child-notice p + p {
  margin-top: 4px;
}

.child-text.plain {
  white-space: normal;
  font-family: var(--font);
  background: none;
  padding: 0;
}

.child-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  font-size: var(--fs-xs);
  margin: 4px 0 0;
}

.meta-pair {
  display: inline-flex;
  gap: 5px;
  align-items: baseline;
}

.meta-label {
  color: var(--muted-2);
  font-family: var(--mono);
}

.meta-value {
  color: var(--muted);
  overflow-wrap: anywhere;
}

.tag {
  font-size: var(--fs-2xs);
  font-family: var(--mono);
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--sunken);
  color: var(--muted);
  border: 1px solid var(--border);
}

.tag[data-tone="good"] {
  background: var(--good-soft);
  color: var(--good);
  border-color: var(--good-border);
}

.tag[data-tone="warn"] {
  background: var(--warn-soft);
  color: var(--warn);
  border-color: var(--warn-border);
}

.tag[data-tone="open"] {
  background: var(--accent-soft);
  color: var(--accent-text);
  border-color: var(--accent-border);
}

.tag[data-tone="standing"] {
  background: var(--surface);
  color: var(--fg);
  border-color: var(--border-strong);
}

.beside {
  margin-top: 8px;
  border-left: 2px solid var(--border-strong);
  padding-left: 8px;
}

.beside-head {
  font-size: var(--fs-xs);
  color: var(--muted-2);
}

.claim-ref {
  margin-top: 6px;
}

.verdicts {
  margin: 4px 0;
  display: grid;
  gap: 3px;
}

.verdict-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-xs);
}

.link-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-sm);
  padding: 3px 0;
}

/* --------------------------------------------------------------- collapsed by default */

.expandable {
  margin: 6px 0 0;
}

.expand-control {
  border: 1px solid var(--border);
  background: var(--sunken);
  border-radius: var(--radius-sm);
  font: inherit;
  font-size: var(--fs-xs);
  color: var(--muted);
  padding: 2px 8px;
  cursor: pointer;
}

.expand-control:hover {
  border-color: var(--border-strong);
  color: var(--fg);
}

.expand-body {
  padding: 6px 0 0 10px;
  border-left: 1px solid var(--border);
  margin-top: 6px;
}

.obligation-group {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  padding: 8px 10px;
  margin: 0 0 8px;
}

.obligation-detail {
  margin-bottom: 8px;
}

.id-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
}
`;

/** Install the record page's rules. Called when a record tab is opened. */
export function ensureRecordStyles(): void {
  installStyles("record", RECORD_CSS);
}
