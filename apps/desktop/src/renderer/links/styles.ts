// The link renderer's own CSS.
//
// `installStyles` is `tabs/kind.ts`'s, where the convention is stated: a surface that
// needs rules carries them beside its own markup and installs them once under a name,
// rather than editing the shared `shell.css` or setting style properties on elements.
// This module is not a tab -- it is the shared link renderer every tab draws through --
// but it is the same rule for the same reason, and one helper means one place a check
// can enumerate what a window is carrying.
//
// The rules use the shell's own custom properties (`--accent`, `--border`, ...), which
// are declared on `:root` in `shell.css`: this styles like the app, it does not bring
// a palette of its own.

import { installStyles } from "../tabs/kind";

const LINK_CSS = `
.link {
  color: var(--accent);
  cursor: pointer;
  border-radius: 3px;
  padding: 0 1px;
  text-decoration: underline;
  text-decoration-color: color-mix(in srgb, var(--accent) 35%, transparent);
  text-underline-offset: 2px;
}

.link:hover {
  background: var(--accent-soft);
  text-decoration-color: var(--accent);
}

.link:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.link[data-link-problem] {
  color: var(--bad);
  text-decoration-style: wavy;
}
`;

/** Install the link rules. Called by every entry point that can draw a link. */
export function ensureLinkStyles(): void {
  installStyles("links", LINK_CSS);
}
