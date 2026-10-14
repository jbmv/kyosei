// What is drawn over the page right now: a context menu, a prompt.
//
// One question with one consumer that cannot answer it by itself -- the claude tab.
// Its page is a `WebContentsView`, which the main process composites ABOVE this whole
// document, so a menu opened over the claude column is drawn underneath the page and
// every click on it lands in claude.ai instead. No z-index reaches across that
// boundary. The tab has to step aside while something is open, and this is how it
// learns that something is.
//
// An overlay says so with `data-overlay` on the element it appends to `<body>`. The
// attribute rather than a list of selectors here, so the next popup opts in where it
// is built instead of in a file its author has no reason to open.

const SELECTOR = "body > [data-overlay]";

export function overlayOpen(): boolean {
  return document.querySelector(SELECTOR) !== null;
}

/** Call `listener` whenever an overlay is appended to or removed from `<body>`. */
export function onOverlayChange(listener: () => void): () => void {
  const observer = new MutationObserver(listener);
  observer.observe(document.body, { childList: true });
  return () => observer.disconnect();
}
