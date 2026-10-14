// Turning free text into clickable kyosei ids, for the brief tab (KYO-235.c2).
//
// The id GRAMMAR lives in one place, `main/links.ts` (KYO-230), and this file does not
// duplicate its resolver -- it only recognises the SHAPE of an id closely enough to
// offer it as a click, and hands the raw string to `context.followLink`, which is the
// main process's job to parse and resolve (`tabs/kind.ts`: "a tab never parses an id
// and never follows a URL itself"). `CHILD_LETTERS` below is a copy of
// `CHILD_KIND_BY_LETTER`'s keys, for exactly the reason `main/links.ts`'s own comment
// gives for ITS copy of the CLI's Python table: there is no importing across the
// main/renderer bundle boundary (`main/links.ts` pulls in Electron-adjacent main-process
// modules; this file ships in the browser bundle), so a letter missing here costs a
// brief a clickable id rather than an id nobody could ever have minted with it -- the
// click still reaches `followLink`, which refuses an unrecognised shape by name.
//
// Deliberately NOT distinguishing record ids from child ids from shift-note ids here:
// KYO-235.c2 asks for "every record, dispatch, flag, criterion and shift-note id" to be
// a link, and every one of those shapes an id this pattern matches. The resolver decides
// what each one opens; this file only decides what LOOKS like an id worth a click.

import { type Child, el } from "../../dom";

const CHILD_LETTERS = "cdefgmnortvx";

/** A record id, or a child id: `KYO-12` or `KYO-12.c3`. Not shift-note/brief/link/event/sweep shapes -- those are linked by their own `id` field, never found inside prose. */
const ID_PATTERN = new RegExp(
  `\\b[A-Z][A-Z0-9]{1,15}-[1-9][0-9]*(?:\\.[${CHILD_LETTERS}][1-9][0-9]*)?\\b`,
  "g",
);

/** One clickable id, styled and labelled so a check can find it by the id it names. */
export function idLink(id: string, onOpen: (id: string, newTab: boolean) => void): HTMLElement {
  return el("button", {
    class: "id-link",
    text: id,
    data: { idLink: id },
    tip: `open ${id}`,
    on: {
      click: (event) => {
        event.preventDefault();
        onOpen(id, event.metaKey || event.ctrlKey);
      },
    },
  });
}

/**
 * `text`, with every id-shaped token turned into a clickable link.
 *
 * `onOpen` is null when this tab has not yet learned the project's id prefix (KYO-235's
 * `followLink` needs `kyosei://<project>/<id>`, and the project is asked for once per
 * open) -- the text renders plain rather than offering links that cannot yet resolve.
 */
export function linkifyText(
  text: string,
  onOpen: ((id: string, newTab: boolean) => void) | null,
): Child[] {
  if (onOpen === null || text === "") return [text];
  const out: Child[] = [];
  let cursor = 0;
  for (const match of text.matchAll(ID_PATTERN)) {
    const id = match[0];
    const start = match.index;
    if (start > cursor) out.push(text.slice(cursor, start));
    out.push(idLink(id, onOpen));
    cursor = start + id.length;
  }
  if (cursor < text.length) out.push(text.slice(cursor));
  return out;
}
