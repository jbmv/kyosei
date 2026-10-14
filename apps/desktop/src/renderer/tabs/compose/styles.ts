// The compose tab's own CSS (KYO-234, moved off the elements by KYO-240.c3).
//
// Every rule below was a `style` object on an element until this file existed. Nothing
// about what the reader sees changes -- the declarations are the same declarations --
// and three things about the source do: the promote-to-idea dialog's look is legible in one
// place instead of spread over two `el(...)` calls, the hover and focus states below
// became expressible at all, and the property writes happen once per window instead of
// once per element per render.
//
// `installStyles` and the reason this is not in `shell.css` are `tabs/kind.ts`'s.
// The rules use the shell's own custom properties and bring no palette of their own.

import { installStyles } from "../kind";

const COMPOSE_CSS = `
/* The pane the editor fills. \`position: relative\` is load-bearing: the promote
   overlay below is absolutely positioned and must cover THIS pane rather than the
   whole window, so a promotion started in one column does not grey out the other. */
.compose-pane {
  position: relative;
  height: 100%;
}

/* ------------------------------------------------------------------ promote */

.compose-notice {
  flex: 0 0 auto;
  padding: 4px 10px;
  font-size: var(--fs-sm);
  color: var(--accent);
  background: var(--accent-soft);
  border-bottom: 1px solid var(--border);
}

.promote-check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-sm);
}

.promote-preview {
  max-height: 120px;
  overflow: auto;
  margin: 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--sunken);
  font-family: var(--mono);
  font-size: var(--fs-xs);
  white-space: pre-wrap;
}

.promote-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.4);
  z-index: 50;
}

.promote-dialog {
  background: var(--surface);
  color: var(--fg);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  padding: 16px 18px 14px;
  width: min(520px, 90vw);
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 12px 32px rgb(0 0 0 / 22%);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.promote-dialog h2 {
  margin: 0 0 2px;
  font-size: var(--fs-lg);
  font-weight: 600;
}

/* A labelled field: the label above, the control below, both the dialog's width. */
.promote-field {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: var(--fs-sm);
  color: var(--muted);
}

.promote-title,
.promote-kind {
  font: inherit;
  font-size: var(--fs-md);
  color: var(--fg);
  padding: 5px 8px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--surface);
  min-width: 0;
}

.promote-title:focus,
.promote-kind:focus {
  outline: 2px solid var(--accent-soft);
  border-color: var(--accent);
}

.idea-remove {
  margin: 0;
  accent-color: var(--accent);
}

/* What the write said, in the tone it said it: pending, refused, or done. */
.promote-result {
  min-height: 1.2em;
  font-size: var(--fs-sm);
  color: var(--muted);
}

.promote-result[data-state="none"] {
  display: none;
}

.promote-result[data-state="refused"] {
  color: var(--bad);
}

.promote-result[data-state="ok"] {
  color: var(--good);
}

.promote-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 4px;
}

.promote-submit,
.promote-cancel {
  padding: 5px 14px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--surface);
  font-size: var(--fs-md);
}

.promote-cancel:hover {
  background: var(--sunken);
}

.promote-submit {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--on-accent);
}

.promote-submit[aria-disabled="true"] {
  opacity: 0.5;
}

/* ------------------------------------------------------------------- the editor */

/* The editor fills the pane: a ribbon bar, then the surface, which scrolls. */
.idea-editor {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.idea-header {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}

/* An idea file's folder, created and modified (KYO-257): one muted line under the
   ribbon bar, its parts separated by a middle dot. */
.idea-meta {
  flex: 0 0 auto;
  display: flex;
  flex-wrap: wrap;
  padding: 3px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  color: var(--muted);
  font-size: var(--fs-xs);
}

.idea-meta > span + span::before {
  content: "·";
  margin: 0 7px;
  color: var(--muted-2);
}

.ribbon {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  overflow: hidden;
}

.idea-ribbon {
  flex: 1 1 auto;
}

.ribbon-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--fg);
  font-size: var(--fs-sm);
  white-space: nowrap;
}

.ribbon-btn:hover {
  border-color: var(--border-strong);
  background: var(--sunken);
}

.ribbon-btn-off,
.ribbon-btn-off:hover {
  opacity: 0.45;
  background: var(--surface);
  border-color: var(--border);
}

.save-state {
  flex: 0 0 auto;
  font-size: var(--fs-xs);
  color: var(--muted-2);
  font-variant-numeric: tabular-nums;
}

.save-state[data-state="dirty"] {
  color: var(--warn);
}

.save-state[data-state="saving"] {
  color: var(--accent);
}

.save-state[data-state="saved"] {
  color: var(--good);
}

.save-state[data-state="failed"] {
  color: var(--bad);
  font-weight: 600;
}

.idea-surface {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
  user-select: text;
  cursor: text;
}

/* CodeMirror brings its own layout; what it takes from here is the size, the font
   and the gutter's colours, so the surface reads as this app's and not the library's. */
.idea-surface .cm-editor {
  height: 100%;
  font-size: var(--fs-md);
  background: var(--surface);
}

.idea-surface .cm-editor.cm-focused {
  outline: none;
}

.idea-surface .cm-scroller {
  font-family: var(--font);
  line-height: 1.55;
}

.idea-surface .cm-content {
  padding: 12px 0 40vh;
}

.idea-surface .cm-line {
  padding: 0 16px 0 4px;
}

.idea-surface .cm-gutters {
  border-right: 0;
  background: var(--surface);
  color: var(--muted-2);
}

.idea-surface .cm-activeLine {
  background: transparent;
}

.fold-marker {
  display: inline-block;
  width: 12px;
  text-align: center;
  color: var(--muted-2);
  cursor: pointer;
}

.fold-marker:hover {
  color: var(--fg);
}

.cm-foldPlaceholder {
  padding: 0 6px;
  margin: 0 2px;
  border-radius: 4px;
  background: var(--sunken);
  border: 1px solid var(--border);
  color: var(--muted);
  cursor: pointer;
}

/* ------------------------------------------------------------ the completions */

/* The closed-set popup (KYO-55): one row per offer, the id first, what it is after.
   CodeMirror's own tooltip rules are written at two and three classes deep, so these
   name the library's classes alongside ours to outrank them; the library's default
   label and detail nodes are hidden because \`addToOptions\` in editor.ts draws both. */
.cm-tooltip.complete-popup {
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: 0 8px 24px rgb(0 0 0 / 16%);
  font-family: var(--font);
  font-size: var(--fs-sm);
  overflow: hidden;
}

.cm-tooltip.cm-tooltip-autocomplete.complete-popup > ul {
  font-family: inherit;
  max-height: 240px;
  min-width: 260px;
}

.cm-tooltip.complete-popup > ul > li.complete-row {
  padding: 4px 10px;
  line-height: 1.4;
}

.cm-tooltip.complete-popup > ul > li.complete-row[aria-selected="true"] {
  background: var(--accent);
  color: var(--on-accent);
}

.cm-tooltip.complete-popup .cm-completionLabel,
.cm-tooltip.complete-popup .cm-completionDetail {
  display: none;
}

.complete-row > span {
  display: inline-flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.complete-label {
  flex: 0 0 auto;
  font-size: var(--fs-xs);
}

.complete-detail {
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.complete-row[aria-selected="true"] .complete-detail {
  color: rgb(255 255 255 / 80%);
}

.complete-popup-empty {
  max-width: 320px;
}

.complete-empty {
  padding: 8px 10px;
}

.complete-why {
  margin-top: 4px;
  font-size: var(--fs-xs);
  color: var(--muted);
  line-height: 1.4;
}
`;

/** Install the compose tab's rules. Called when the kind opens a tab. */
export function ensureComposeStyles(): void {
  installStyles("compose", COMPOSE_CSS);
}
