// The words an anchor's chip quotes, read as inline markdown (KYO-758). The quote is a slice
// of a source line and may stop anywhere, so an emphasis it opens and does not close is taken
// as running to the end, and a link it does not finish is its text alone. Only the drawing
// changes: the line sent to the agent and the place a chip opens keep the source words.

export interface Piece { text: string; bold?: boolean; italic?: boolean; code?: boolean }

const wordChar = /[\p{L}\p{N}]/u;

export function chipPieces(source: string): Piece[] {
  const pieces: Piece[] = [];
  let bold = false;
  let italic = false;
  let buffer = "";
  const flush = () => {
    if (!buffer) return;
    const last = pieces[pieces.length - 1];
    const next: Piece = { text: buffer, ...(bold ? { bold } : {}), ...(italic ? { italic } : {}) };
    if (last && !last.code && !!last.bold === bold && !!last.italic === italic) last.text += buffer;
    else pieces.push(next);
    buffer = "";
  };
  const run = (text: string) => {
    for (let at = 0; at < text.length;) {
      const char = text[at];
      const before = text[at - 1] ?? "";
      const after = text[at + 1] ?? "";
      if (char === "\\" && at + 1 < text.length) {
        buffer += after;
        at += 2;
      } else if (char === "`") {
        const close = text.indexOf("`", at + 1);
        flush();
        const code = text.slice(at + 1, close < 0 ? text.length : close);
        if (code) pieces.push({ text: code, code: true, ...(bold ? { bold } : {}), ...(italic ? { italic } : {}) });
        at = close < 0 ? text.length : close + 1;
      } else if (text.startsWith("**", at) || (text.startsWith("__", at) && !wordChar.test(before))) {
        flush();
        bold = !bold;
        at += 2;
      } else if (char === "*" && (italic || (after && !/\s/.test(after)))) {
        flush();
        italic = !italic;
        at += 1;
      } else if (char === "_" && !(wordChar.test(before) && wordChar.test(after)) && (italic || (after && !/\s/.test(after)))) {
        flush();
        italic = !italic;
        at += 1;
      } else if (char === "[") {
        const close = text.indexOf("](", at + 1);
        if (close < 0) {
          buffer += char;
          at += 1;
        } else {
          run(text.slice(at + 1, close));
          const end = text.indexOf(")", close + 2);
          at = end < 0 ? text.length : end + 1;
        }
      } else {
        buffer += char;
        at += 1;
      }
    }
  };
  run(source);
  flush();
  return pieces;
}
