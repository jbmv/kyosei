// The views tab's own CSS (KYO-235, given a home by KYO-240.c3).
//
// This tab declared class names and styled none of them, which is the third of the
// three answers stage 3 gave to "where does a tab's CSS go" -- and the one with a
// visible cost: `ids.ts` calls `.id-link` "one clickable id, styled", and with no rule
// anywhere a `<button>` in the middle of a sentence renders as the platform's default
// grey box, mid-paragraph, in every brief and shift note this tab draws.
//
// `installStyles` and the reason this is not in `shell.css` are `tabs/kind.ts`'s.
// The rules use the shell's own custom properties and bring no palette of their own.
//
// The `.md-*` and `.mmd-*` classes `tabs/docs/markdown.ts` and `mermaid.ts` write when
// this tab renders a governed document are that directory's (`docs/styles.ts`, installed
// by `renderDocument` itself since KYO-253); the banner this tab draws above a document
// is this tab's markup and is styled here.

import { installStyles } from "../kind";

const VIEWS_CSS = `
/* An id inside rendered prose: a button, because clicking it follows a link through
   the main process rather than navigating this document, and it must read as the text
   it sits in rather than as a control. The same treatment the link renderer gives
   \`.link\` (\`renderer/links/styles.ts\`), which is what the reader is comparing it to. */
.id-link {
  color: var(--accent);
  background: none;
  border: none;
  padding: 0;
  margin: 0;
  font: inherit;
  cursor: pointer;
  text-decoration: underline;
  text-decoration-color: color-mix(in srgb, var(--accent) 35%, transparent);
  text-underline-offset: 2px;
}

.id-link:hover {
  text-decoration-color: var(--accent);
}

.id-link:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
  border-radius: 2px;
}

/* A list of rows drawn by a view: the same rows the sidebar draws, inside the page.
   Scoped to the page, because the sidebar's own lists carry the class too. */
.raw .results {
  margin: 6px 0 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  overflow: hidden;
}

/* One gate awaiting a person: a card, with the payload kept as written. */
.raw .awaiting-row {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
}

.awaiting-row:last-child {
  border-bottom: 0;
}

.awaiting-row .gate-payload {
  margin: 6px 0 0;
}

/* --------------------------------------------------------------- a document's banner */

/* Above a governed document: its staleness verdict, what that means, and the paths it
   governs -- each marked with whether \`kyo docs check\` says it resolves. */
.doc-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 10px;
  margin: 6px 0 16px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--sunken);
  font-size: var(--fs-sm);
}

.doc-staleness {
  font-size: var(--fs-2xs);
  font-family: var(--mono);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  padding: 1px 7px;
  border-radius: 999px;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--muted);
}

.doc-staleness[data-staleness="current"] {
  color: var(--good);
  border-color: var(--good-border);
  background: var(--good-soft);
}

.doc-staleness[data-staleness="stale"] {
  color: var(--warn);
  border-color: var(--warn-border);
  background: var(--warn-soft);
}

.doc-banner-says {
  flex: 1 1 auto;
  min-width: 0;
}

.doc-banner-governs {
  flex: 1 0 100%;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.doc-governs-target {
  font-size: var(--fs-xs);
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--muted);
}

.doc-governs-target[data-resolves="false"] {
  color: var(--bad);
  border-color: var(--bad-border);
  background: var(--bad-soft);
  text-decoration: line-through;
}
`;

/** Install the views tab's rules. Called when the kind opens a tab. */
export function ensureViewsStyles(): void {
  installStyles("views", VIEWS_CSS);
}
