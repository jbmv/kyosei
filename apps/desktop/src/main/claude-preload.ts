// The claude view's preload, and it does exactly one thing (KYO-321, KYO-263.c5).
//
// This is the ONLY script of this app's that runs in a page this app has logged a
// human into, and the decision that put it here states its boundary rather than its
// purpose -- read `kyo show KYO-321` before adding anything below:
//
//   isolated world, sandbox on, contextIsolation on;
//   NOTHING exposed to the page -- no `contextBridge`, no `window.kyosei`, nothing
//   written back into the page's world at all;
//   NO DOM mutation -- this file reads one element's text and writes nothing;
//   ONE one-way channel, carrying that text and nothing else;
//   nothing sent back to the page, and nothing the page can call.
//
// `tests/seams.spec.ts` asserts those properties against this file's own source, so
// a widening is a red check rather than a review someone has to notice.
//
// WHY A SCRIPT AT ALL, when the app first ran without one. What claude.ai
// leaves of a `kyosei:` markdown link is an anchor with the words and NO href --
// `<a target="_blank" rel="noreferrer">KYO-263</a>`, as copied from the rendered page
// (KYO-263) -- so the two hooks in `main.ts` never fire (there is no navigation),
// and the right-click menu built before this never appears (claude.ai raises its own
// menu, KYO-263.c3). An element with no href does nothing in the page when it is
// clicked, which is what makes listening for that click free: this takes no gesture
// away from the site, and it prevents nothing.
//
// WHAT IS NOT DONE HERE, deliberately: no bare id or bare URL in plain text is made
// clickable. That would mean walking and rewriting the page's own DOM, which is the
// line KYO-321 draws. The controller writes the markdown-link form instead, and the
// `/kyosei` skill says so.

import { ipcRenderer } from "electron";
import { CHANNEL } from "../shared/protocol";

/**
 * The longest text this sends.
 *
 * An anchor's text is a record id; anything longer is not the gesture, and the main
 * process caps it again anyway (`MAX_CLICKED_TEXT` in `links.ts`) because a cap
 * applied in a renderer is a cap a renderer could be made to skip.
 */
const MAX_TEXT = 200;

/**
 * An href-less anchor the click landed in, or null.
 *
 * `a:not([href])` and not "an anchor whose text looks like an id": what is clickable
 * is decided by the ELEMENT, and what it resolves to is decided in the main process
 * by the link grammar. A guess made here would be a second grammar in a file that
 * must stay this small.
 *
 * An anchor WITH an href is left entirely alone -- it is a real link, the page or
 * `will-navigate` handles it, and intercepting it would take a working link away.
 */
function hrefLessAnchor(target: EventTarget | null): Element | null {
  const node = target as { closest?: unknown } | null;
  if (node === null || typeof node.closest !== "function") return null;
  return (node as Element).closest("a:not([href])");
}

// Capture phase, so the message is sent before a page handler can stop the event
// travelling -- reading it earlier, never taking it. Nothing here calls
// `preventDefault`, `stopPropagation` or `stopImmediatePropagation`: the click goes
// on to do in the page exactly what it did before this file existed.
//
// Left button and no modifier: Cmd-click, Shift-click and the rest are the page's
// and the browser's gestures, and a middle click is not a click on a link at all.
window.addEventListener(
  "click",
  (event: MouseEvent) => {
    if (event.button !== 0) return;
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const anchor = hrefLessAnchor(event.target);
    if (anchor === null) return;
    const text = (anchor.textContent ?? "").trim().slice(0, MAX_TEXT);
    if (text === "") return;
    // One-way: `send`, not `invoke`. The main process answers nothing, so there is no
    // value for the page's world to observe and no promise for it to await.
    ipcRenderer.send(CHANNEL.claudeTextClick, text);
  },
  true,
);
