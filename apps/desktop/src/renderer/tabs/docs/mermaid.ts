// Read-only mermaid, ported verbatim from v2 (KYO-139) for KYO-235's docs tab.
//
// The shell deleted v2's `src/renderer/docs/` wholesale (KYO-232.c1: "no v2 region ...
// survives"), and KYO-235 recovered this file and `markdown.ts` from the v2 renderer
// into tabs/docs/. The algorithm below is unchanged
// from that commit; the only edit is the import of `svgEl`, which lived in v2's
// `renderer/dom.ts` and has no counterpart in v3's (KYO-232 rewrote the stylesheet and
// the DOM helpers rather than trimming them, and `svgEl` had no other consumer to carry
// it forward for). So it is restated below rather than added to `../../dom`, which has
// no other consumer for it.
//
// KYO-123 deferred three things: mermaid rendering, live preview, and the outline
// pane's drag-between-levels. KYO-136 decision 6 kills the second and third outright --
// there is no editor in this mode to preview from and nothing to drag. It does not kill
// the first, and KYO-139's record says why: "an architecture document that cannot draw
// its own diagram is a poor viewer, and architecture documents are exactly what this
// mode is for."
//
// WHY THIS AND NOT THE MERMAID PACKAGE. Three reasons, and the third is the one that
// decided it. The renderer bundle is a single IIFE built by `build.mjs` with no code
// splitting, and mermaid v11 lazy-loads its diagram grammars through dynamic import, so
// it does not drop into this build as-is. It is also several megabytes against a 1.6mb
// bundle, for one pane. And the record asked for "the cheap half of what was deferred".
//
// WHAT THAT COSTS, STATED RATHER THAN HIDDEN. This draws flowcharts -- `flowchart` and
// `graph`, the dialect architecture documents are written in -- and nothing else. A
// sequence diagram, a state diagram, a class diagram or a flowchart using syntax past
// what `parseFlowchart` reads is NOT drawn wrong: it comes back as `null` and the
// caller shows the source under a label saying which dialect it is and that this build
// cannot draw it. That is the law-6 shape of the thing. A renderer that half-drew an
// unfamiliar diagram would be worse than one that drew nothing, because a diagram is
// believed on sight and its source is not.

/** `document.createElementNS` for SVG, on `dom.ts`'s `el()` pattern (v2's `svgEl`). */
function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number> = {},
  children: (Node | null | undefined | false)[] = [],
): SVGElementTagNameMap[K] {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child);
  }
  return node;
}

/** How a node is drawn, from the brackets its declaration used. */
type NodeShape = "rect" | "round" | "stadium" | "diamond" | "circle";

interface FlowNode {
  id: string;
  label: string;
  shape: NodeShape;
  /** Insertion order, which decides ordering within a rank. */
  seen: number;
}

interface FlowEdge {
  from: string;
  to: string;
  label: string;
  /** A dotted or thick link is drawn as itself; an open link has no arrowhead. */
  dashed: boolean;
  arrow: boolean;
}

export interface Flowchart {
  /** `TD`/`TB`, `BT`, `LR` or `RL`, as the header declared it. */
  direction: string;
  nodes: FlowNode[];
  edges: FlowEdge[];
}

/** The first word of a mermaid block: `flowchart`, `sequenceDiagram`, `gantt`, ... */
export function diagramKind(source: string): string {
  for (const raw of source.split("\n")) {
    const line = raw.trim();
    if (line === "" || line.startsWith("%%")) continue;
    return (line.split(/[\s{]/)[0] ?? "").trim();
  }
  return "";
}

// Longest-first: `[(cylinder)]` and `([stadium])` both end in a bracket the plain
// `[rect]` pattern would also match, so a rect tried first would swallow them and
// carry the inner brackets into the label as text.
const SHAPES: [RegExp, NodeShape][] = [
  [/^\(\((.*)\)\)$/, "circle"],
  [/^\(\[(.*)\]\)$/, "stadium"],
  [/^\[\((.*)\)\]$/, "stadium"],
  [/^\{(.*)\}$/, "diamond"],
  [/^\[(.*)\]$/, "rect"],
  [/^\((.*)\)$/, "round"],
];

/** Strip mermaid's quoting from a label and collapse its whitespace. */
function cleanLabel(raw: string): string {
  const text = raw
    .trim()
    .replace(/^"(.*)"$/s, "$1")
    .replace(/^'(.*)'$/s, "$1");
  return text
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * One `A[Label]` term: its id, and the label and shape if it declared them.
 *
 * A node may be declared once with a label and named bare everywhere else, so the
 * absence of brackets is "no opinion" rather than "no label" -- the caller keeps the
 * first opinion it was given.
 */
function parseTerm(raw: string): { id: string; label: string | null; shape: NodeShape } | null {
  const term = raw.trim();
  if (term === "") return null;
  const open = term.search(/[[({]/);
  if (open === -1) {
    return /^[\w.-]+$/.test(term) ? { id: term, label: null, shape: "rect" } : null;
  }
  const id = term.slice(0, open).trim();
  if (id === "" || !/^[\w.-]+$/.test(id)) return null;
  const rest = term.slice(open);
  for (const [pattern, shape] of SHAPES) {
    const found = rest.match(pattern);
    if (found) return { id, label: cleanLabel(found[1] ?? ""), shape };
  }
  return null;
}

/**
 * Every link on one line, left to right: `A --> B --> C` is two edges.
 *
 * The link syntaxes read here are the ones a hand-written architecture diagram uses:
 * `-->`, `---`, `-.->`, `==>`, each optionally carrying a label as `|text|` after the
 * link or as `-- text -->` inside it. Anything else on the line makes the WHOLE
 * DIAGRAM unreadable rather than this line silently ignorable -- see `parseFlowchart`.
 */
//
// The terminator is a LOOKAHEAD, not a consumed character, and that is not a
// micro-optimisation: `A -->|yes| B` puts the label's opening pipe immediately after
// the arrow with no space between them, so a pattern that required whitespace there
// silently failed to see the commonest labelled edge in mermaid -- and, because
// `parseFlowchart` is all-or-nothing, refused the whole diagram rather than mis-drawing
// it. Caught by `tests/docs.spec.ts`; the refusal is what made it a visible failure.
const LINK = /\s(-{2,3}|-\.-|={2,3}|--\s[^->|]*?\s--|-\.\s[^->|]*?\s\.-)(>|)(?=\s|\|)/;

function splitLinks(
  line: string,
): { terms: string[]; links: { raw: string; label: string; arrow: boolean }[] } | null {
  const terms: string[] = [];
  const links: { raw: string; label: string; arrow: boolean }[] = [];
  let rest = ` ${line} `;
  for (;;) {
    const found = rest.match(LINK);
    if (found === null || found.index === undefined) break;
    terms.push(rest.slice(0, found.index));
    const body = found[1] ?? "";
    const inline = body.match(/^(?:--|-\.)\s(.*?)\s(?:--|\.-)$/);
    let label = inline ? cleanLabel(inline[1] ?? "") : "";
    rest = rest.slice(found.index + found[0].length);
    // `A -->|yes| B`: the label sits immediately after the link, before the target.
    const piped = rest.match(/^\|([^|]*)\|/);
    if (piped) {
      label = cleanLabel(piped[1] ?? "");
      rest = rest.slice(piped[0].length);
    }
    links.push({ raw: body, label, arrow: found[2] === ">" });
  }
  if (links.length === 0) return null;
  terms.push(rest);
  return { terms, links };
}

const HEADER = /^(?:flowchart|graph)\s+(TD|TB|BT|LR|RL)?\s*$/i;

/**
 * Read a mermaid flowchart, or refuse the whole block.
 *
 * ALL OR NOTHING, and that is the load-bearing choice here. A parser that skipped the
 * lines it did not understand would draw a diagram missing an edge, and a missing edge
 * in an architecture diagram is a false statement about the architecture that looks
 * exactly like a true one. So an unreadable line returns `null` and the caller shows
 * the source instead.
 */
export function parseFlowchart(source: string): Flowchart | null {
  const lines = source.split("\n");
  let direction: string | null = null;
  const nodes = new Map<string, FlowNode>();
  const edges: FlowEdge[] = [];

  const note = (term: { id: string; label: string | null; shape: NodeShape }): void => {
    const standing = nodes.get(term.id);
    if (standing === undefined) {
      nodes.set(term.id, {
        id: term.id,
        label: term.label ?? term.id,
        shape: term.label === null ? "rect" : term.shape,
        seen: nodes.size,
      });
      return;
    }
    // A later declaration with a label wins over an earlier bare mention, which is how
    // `A --> B` followed by `B[Done]` is read by mermaid itself.
    if (term.label !== null && standing.label === standing.id) {
      standing.label = term.label;
      standing.shape = term.shape;
    }
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (line === "" || line.startsWith("%%")) continue;
    if (direction === null) {
      const header = line.match(HEADER);
      if (header === null) return null;
      direction = (header[1] ?? "TD").toUpperCase();
      continue;
    }
    // Declarations this build does not draw. `subgraph` in particular groups nodes
    // visually, and drawing its members ungrouped would misstate the picture.
    if (/^(subgraph|end|classDef|class|style|linkStyle|click|direction)\b/i.test(line)) {
      return null;
    }
    const split = splitLinks(line.replace(/;\s*$/, ""));
    if (split === null) {
      const solo = parseTerm(line.replace(/;\s*$/, ""));
      if (solo === null) return null;
      note(solo);
      continue;
    }
    const terms = split.terms.map(parseTerm);
    if (terms.some((term) => term === null)) return null;
    for (const term of terms) note(term as NonNullable<(typeof terms)[number]>);
    split.links.forEach((link, index) => {
      const from = terms[index];
      const to = terms[index + 1];
      if (from === null || to === null || from === undefined || to === undefined) return;
      edges.push({
        from: from.id,
        to: to.id,
        label: link.label,
        dashed: link.raw.includes("."),
        arrow: link.arrow,
      });
    });
  }
  if (direction === null || nodes.size === 0) return null;
  return { direction, nodes: [...nodes.values()], edges };
}

// ------------------------------------------------------------------- the drawing

const CHAR_WIDTH = 6.6;
const NODE_PAD = 18;
const MIN_NODE_WIDTH = 54;
const NODE_HEIGHT = 34;
const RANK_GAP = 62;
const NODE_GAP = 22;
const MARGIN = 14;

interface Placed extends FlowNode {
  rank: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Longest-path ranking, with cycles broken at the first edge that would close one.
 *
 * A cyclic flowchart is perfectly legal mermaid (a retry loop is a cycle) and has no
 * layering, so the back edge is laid out as an edge and simply does not push its target
 * down a rank. The alternative -- refusing cyclic diagrams -- would refuse the loops
 * architecture documents are most often drawn to show.
 */
function rankNodes(chart: Flowchart): Map<string, number> {
  const rank = new Map<string, number>(chart.nodes.map((node) => [node.id, 0]));
  const outgoing = new Map<string, string[]>();
  for (const edge of chart.edges) {
    const standing = outgoing.get(edge.from);
    if (standing === undefined) outgoing.set(edge.from, [edge.to]);
    else standing.push(edge.to);
  }
  // Relax every edge |nodes| times, which settles any acyclic graph and is bounded on
  // a cyclic one. Cheap at the sizes a hand-written diagram reaches.
  for (let pass = 0; pass < chart.nodes.length; pass += 1) {
    let moved = false;
    for (const edge of chart.edges) {
      const from = rank.get(edge.from) ?? 0;
      const to = rank.get(edge.to) ?? 0;
      if (to < from + 1 && from + 1 <= chart.nodes.length) {
        rank.set(edge.to, from + 1);
        moved = true;
      }
    }
    if (!moved) break;
  }
  return rank;
}

function widthFor(label: string): number {
  return Math.max(MIN_NODE_WIDTH, Math.round(label.length * CHAR_WIDTH) + NODE_PAD * 2);
}

function place(chart: Flowchart): { placed: Placed[]; width: number; height: number } {
  const rank = rankNodes(chart);
  const horizontal = chart.direction === "LR" || chart.direction === "RL";
  const byRank = new Map<number, FlowNode[]>();
  for (const node of [...chart.nodes].sort((a, b) => a.seen - b.seen)) {
    const which = rank.get(node.id) ?? 0;
    const standing = byRank.get(which);
    if (standing === undefined) byRank.set(which, [node]);
    else standing.push(node);
  }
  const ranks = [...byRank.keys()].sort((a, b) => a - b);
  const placed: Placed[] = [];
  // The across-axis extent of the widest rank decides the canvas, so every rank can be
  // centred against it rather than against whatever the first rank happened to need.
  const acrossOf = (nodes: FlowNode[]): number =>
    horizontal
      ? nodes.length * NODE_HEIGHT + (nodes.length - 1) * NODE_GAP
      : nodes.reduce((total, node) => total + widthFor(node.label), 0) +
        (nodes.length - 1) * NODE_GAP;
  const widest = Math.max(...ranks.map((which) => acrossOf(byRank.get(which) ?? [])));

  let along = MARGIN;
  for (const which of ranks) {
    const group = byRank.get(which) ?? [];
    const deepest = horizontal
      ? Math.max(...group.map((node) => widthFor(node.label)))
      : NODE_HEIGHT;
    let across = MARGIN + (widest - acrossOf(group)) / 2;
    for (const node of group) {
      const width = widthFor(node.label);
      placed.push({
        ...node,
        rank: which,
        x: horizontal ? along : across,
        y: horizontal ? across : along,
        width,
        height: NODE_HEIGHT,
      });
      across += (horizontal ? NODE_HEIGHT : width) + NODE_GAP;
    }
    along += deepest + RANK_GAP;
  }
  const total = along - RANK_GAP + MARGIN;
  return {
    placed,
    width: horizontal ? total : widest + MARGIN * 2,
    height: horizontal ? widest + MARGIN * 2 : total,
  };
}

/** Where a straight line from `from`'s centre to `to`'s centre leaves `from`'s box. */
function border(from: Placed, to: Placed): { x: number; y: number } {
  const cx = from.x + from.width / 2;
  const cy = from.y + from.height / 2;
  const dx = to.x + to.width / 2 - cx;
  const dy = to.y + to.height / 2 - cy;
  if (dx === 0 && dy === 0) return { x: cx, y: cy };
  const scale = Math.min(
    dx === 0 ? Number.POSITIVE_INFINITY : from.width / 2 / Math.abs(dx),
    dy === 0 ? Number.POSITIVE_INFINITY : from.height / 2 / Math.abs(dy),
  );
  return { x: cx + dx * scale, y: cy + dy * scale };
}

function shapeFor(node: Placed): SVGElement {
  const common = { class: `mmd-node mmd-${node.shape}` };
  if (node.shape === "diamond") {
    const cx = node.x + node.width / 2;
    const cy = node.y + node.height / 2;
    return svgEl("polygon", {
      ...common,
      points: `${cx},${node.y} ${node.x + node.width},${cy} ${cx},${node.y + node.height} ${node.x},${cy}`,
    });
  }
  const radius =
    node.shape === "stadium" || node.shape === "circle"
      ? node.height / 2
      : node.shape === "round"
        ? 9
        : 3;
  return svgEl("rect", {
    ...common,
    x: node.x,
    y: node.y,
    width: node.width,
    height: node.height,
    rx: radius,
    ry: radius,
  });
}

/**
 * Draw a parsed flowchart as SVG.
 *
 * SVG built through `svgEl` rather than an HTML string, the same way KYO-113's typed-
 * edge graph is drawn: a label out of a document is text content and never markup, so
 * a document containing `<script>` in a node label is a node with an odd name.
 */
export function drawFlowchart(chart: Flowchart): SVGSVGElement {
  const { placed, width, height } = place(chart);
  const byId = new Map(placed.map((node) => [node.id, node]));
  const svg = svgEl("svg", {
    class: "mmd",
    viewBox: `0 0 ${width} ${height}`,
    width,
    height,
    role: "img",
    "aria-label": `flowchart, ${chart.nodes.length} nodes and ${chart.edges.length} edges`,
  });

  const marker = svgEl("marker", {
    id: "mmd-arrow",
    viewBox: "0 0 10 10",
    refX: 9,
    refY: 5,
    markerWidth: 6,
    markerHeight: 6,
    orient: "auto-start-reverse",
  });
  marker.append(svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "mmd-arrowhead" }));
  const defs = svgEl("defs", {});
  defs.append(marker);
  svg.append(defs);

  for (const edge of chart.edges) {
    const from = byId.get(edge.from);
    const to = byId.get(edge.to);
    if (from === undefined || to === undefined) continue;
    const start = border(from, to);
    const end = border(to, from);
    const line = svgEl("line", {
      class: edge.dashed ? "mmd-edge mmd-edge-dashed" : "mmd-edge",
      x1: start.x,
      y1: start.y,
      x2: end.x,
      y2: end.y,
    });
    if (edge.arrow) line.setAttribute("marker-end", "url(#mmd-arrow)");
    svg.append(line);
    if (edge.label !== "") {
      const label = svgEl("text", {
        class: "mmd-edge-label",
        x: (start.x + end.x) / 2,
        y: (start.y + end.y) / 2 - 3,
        "text-anchor": "middle",
      });
      label.textContent = edge.label;
      svg.append(label);
    }
  }

  for (const node of placed) {
    const group = svgEl("g", { class: "mmd-node-group", "data-node": node.id });
    group.append(shapeFor(node));
    const label = svgEl("text", {
      class: "mmd-label",
      x: node.x + node.width / 2,
      y: node.y + node.height / 2,
      "text-anchor": "middle",
      "dominant-baseline": "central",
    });
    label.textContent = node.label;
    group.append(label);
    svg.append(group);
  }
  return svg;
}

/** Draw `source`, or `null` for a diagram this build does not read. */
export function renderMermaid(source: string): SVGSVGElement | null {
  const chart = parseFlowchart(source);
  return chart === null ? null : drawFlowchart(chart);
}
