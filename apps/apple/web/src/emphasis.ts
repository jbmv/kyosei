// Inline emphasis in a prompt the person typed (KYO-967): `*italic*`, `**bold**` and
// `code` between backticks, and nothing else. A prompt's line breaks, held lines and chips are
// drawn from its lines, so no block construct is read here, and the text is never taken as
// HTML: the result is a tree of plain strings that the page builds nodes from.

/** One piece of a line: plain text, or an emphasised run with the markers that were typed. */
export type Piece =
  | { kind: "text"; text: string }
  | { kind: "em" | "strong"; marker: string; body: Piece[] }
  | { kind: "code"; text: string };

const space = (char: string | undefined) => char === undefined || /\s/.test(char);

/** The index of the closing run of `marker` for an opener ending at `from`, or -1. */
function closing(line: string, marker: string, from: number): number {
  for (let at = line.indexOf(marker, from); at >= 0; at = line.indexOf(marker, at + 1)) {
    if (space(line[at - 1])) continue;
    // A single `*` does not close at a `**` that a longer run holds.
    if (marker === "*" && (line[at + 1] === "*" || line[at - 1] === "*")) continue;
    if (marker === "**" && line[at + 2] === "*") continue;
    return at;
  }
  return -1;
}

/** One line of a prompt as pieces. The pieces' text, markers included, is the line itself. */
export function emphasis(line: string): Piece[] {
  const pieces: Piece[] = [];
  let plain = "";
  const flush = () => {
    if (plain) pieces.push({ kind: "text", text: plain });
    plain = "";
  };
  let at = 0;
  while (at < line.length) {
    const char = line[at];
    if (char === "`") {
      const end = line.indexOf("`", at + 1);
      if (end > at + 1) {
        flush();
        pieces.push({ kind: "code", text: line.slice(at, end + 1) });
        at = end + 1;
        continue;
      }
    }
    if (char === "*") {
      const marker = line.startsWith("**", at) ? "**" : "*";
      const next = line[at + marker.length];
      if (!space(next) && next !== "*") {
        const end = closing(line, marker, at + marker.length + 1);
        if (end > at + marker.length) {
          flush();
          pieces.push({
            kind: marker === "**" ? "strong" : "em", marker,
            body: emphasis(line.slice(at + marker.length, end)),
          });
          at = end + marker.length;
          continue;
        }
      }
      // A marker that opens nothing is the text it was typed as.
      plain += marker;
      at += marker.length;
      continue;
    }
    plain += char;
    at += 1;
  }
  flush();
  return pieces;
}

/** The pieces' text with every marker, which is what was typed. */
export function typedText(pieces: Piece[]): string {
  return pieces.map((piece) => piece.kind === "text" ? piece.text
    : piece.kind === "code" ? piece.text
    : `${piece.marker}${typedText(piece.body)}${piece.marker}`).join("");
}
