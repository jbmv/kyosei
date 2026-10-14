// `kyosei://<project>/<id>` — the scheme, and the one place that knows an id's shape.
//
// KYO-230. Three surfaces resolve to the same thing and none of them knows how:
//
//   inside the app      a `[[KYO-226.g1]]` in an editor, a rendered record, a brief;
//   the claude tab      a link in the controller's own message, intercepted in the
//                       `WebContentsView` before Chromium can hand it to a browser;
//   anywhere on the Mac a link in a Chrome tab, a terminal, another app -- delivered
//                       by `open-url`, or on the argv of a launch.
//
// This file is the main-process half: the URL grammar, the resolver, and the typed
// request the renderer receives. The renderer half -- the record page's anchors and
// its clickable `[[id]]` links -- is KYO-233 and is deliberately not here.
//
// Electron-free, like `kyo.ts`, `store.ts`, `facts.ts`, `sessions.ts` and
// `shellenv.ts`: every branch below is exercisable from a plain Node check.
//
// ONE PLACE, AND WHY IT MATTERS MORE THAN USUAL. The id vocabulary is the CLI's
// (`src/kyosei/ids.py`), it grows -- KYO-137 added two child kinds and had to widen a
// regex that had been written out by hand, which is the drift law 3 exists to prevent
// -- and it is about to have several consumers in this app. So the shapes are a TABLE
// here, the matcher is derived from it, and the refusal is generated from it too: a
// shape this app does not know is refused by a sentence that lists what it does know,
// and that list cannot go stale against the matcher because it is the same array.

import type {
  LinkDelivery,
  LinkRefusal,
  OpenColumn,
  OpenSource,
  RemoteFacts,
  ResolvedTarget,
  TargetKind,
} from "../shared/protocol";
import { isOpenColumn } from "../shared/protocol";
import { addressLabel, sameServer } from "./kyoserve";

/** The scheme this app registers with the operating system. */
export const URL_SCHEME = "kyosei";

/**
 * One id shape the CLI mints.
 *
 * `opens` is the page a link to this shape lands on, or null for a shape that is real
 * and has no page in this app. The null entries are not an oversight and are not
 * folded into "unknown": a link id names an EDGE between two records and an event id
 * names a ledger row, so there is no single record either of them "is" -- resolving
 * one would need a store read, and this resolver is pure by design (`main.ts` holds
 * the store, this holds the grammar). A reader who clicks one gets a sentence saying
 * the shape is recognised and nothing opens it, which is a different and more useful
 * answer than "I do not know what that is".
 */
export interface IdShape {
  /** What the store calls this object. */
  name: string;
  /** Matches the whole id. Anchored here, so a caller cannot half-match. */
  pattern: RegExp;
  /** The page a link to it opens, or null when this app has none. */
  opens: TargetKind | null;
  /** An example, for the refusal. Written out, because a regex is not a sentence. */
  example: string;
}

/**
 * The child-object letters, and what each one is.
 *
 * Copied from `kyosei.ids.CHILD_KIND_BY_LETTER` and it is a COPY -- the CLI is Python
 * and this is TypeScript, so there is no importing it. What keeps the two honest is
 * that this app never mints an id and never validates one for the store: it reads ids
 * the CLI wrote, and a letter missing here costs a deep link and refuses loudly
 * (`unknown-shape`, naming everything it does know) rather than silently opening the
 * wrong thing.
 *
 * `c` is the criterion and `m` is the claim, which reads backwards until you know why:
 * `c` was taken by criterion before claims existed, and law 4 says an id never moves.
 */
export const CHILD_KIND_BY_LETTER: Readonly<Record<string, string>> = {
  c: "criterion",
  n: "note",
  g: "gate",
  t: "touch",
  m: "claim",
  v: "verification",
  e: "evidence",
  d: "dispatch",
  r: "report",
  f: "flag",
  x: "doc-ref",
  o: "docs-obligation",
};

const PREFIX = "[A-Z][A-Z0-9]{1,15}";
const SEQ = "[1-9][0-9]*";
const CHILD_LETTERS = Object.keys(CHILD_KIND_BY_LETTER).sort().join("");

/**
 * Every shape, in the order they are tried.
 *
 * Order matters exactly once and the rest is incidental: the single-letter run ids
 * (`-L`, `-E`, `-W`, `-B`, `-S`) have to be tried before nothing, because a record id
 * is digits after the dash and cannot collide with any of them. Listed in
 * `kyosei.ids`'s own table order so the two read side by side.
 */
export const ID_SHAPES: readonly IdShape[] = [
  {
    name: "record",
    pattern: new RegExp(`^${PREFIX}-${SEQ}$`),
    opens: "record",
    example: "KYO-12",
  },
  {
    name: "child object (criterion, gate, claim, note, evidence, flag, dispatch, report, touch, verification, doc-ref, docs obligation)",
    pattern: new RegExp(`^(${PREFIX}-${SEQ})\\.([${CHILD_LETTERS}])(${SEQ})$`),
    opens: "record",
    example: "KYO-12.c3",
  },
  {
    name: "shift note",
    pattern: new RegExp(`^${PREFIX}-S${SEQ}$`),
    opens: "shift-note",
    example: "KYO-S12",
  },
  {
    name: "session brief",
    pattern: new RegExp(`^${PREFIX}-B${SEQ}$`),
    opens: "brief",
    example: "KYO-B9",
  },
  {
    name: "typed link",
    pattern: new RegExp(`^${PREFIX}-L${SEQ}$`),
    opens: null,
    example: "KYO-L7",
  },
  {
    name: "ledger event",
    pattern: new RegExp(`^${PREFIX}-E${SEQ}$`),
    opens: null,
    example: "KYO-E41",
  },
  {
    name: "sweep run",
    pattern: new RegExp(`^${PREFIX}-W${SEQ}$`),
    opens: null,
    example: "KYO-W3",
  },
];

export type IdResolution =
  | { ok: true; target: ResolvedTarget }
  | { ok: false; outcome: Extract<LinkRefusal, "not-openable" | "unknown-shape">; refusal: string };

/** Every shape this resolver knows, as one sentence. Generated, never written out. */
export function shapesItKnows(): string {
  return ID_SHAPES.map((shape) => `${shape.example} (${shape.name})`).join(", ");
}

/**
 * Map any id the CLI mints onto the page that shows it, and the anchor on that page.
 *
 * Pure, total, and the only place in this app that knows an id has a shape. Three
 * outcomes and they are deliberately three rather than two -- see `IdShape.opens`.
 */
export function resolveId(raw: string): IdResolution {
  const id = raw.trim();
  for (const shape of ID_SHAPES) {
    const match = shape.pattern.exec(id);
    if (match === null) continue;
    if (shape.opens === null) {
      return {
        ok: false,
        outcome: "not-openable",
        refusal:
          `${id} is a ${shape.name} id, which this app recognises and has no page for: ` +
          "it names something that belongs to more than one record, or to none, so there " +
          "is no single page a link to it could open. Link to the record instead.",
      };
    }
    // The child case is the only one that carries an anchor, and the owning record is
    // capture group 1 -- read from the match rather than re-derived by slicing at the
    // dot, so the grammar is stated once.
    const owner = match[1];
    const letter = match[2];
    if (owner !== undefined && letter !== undefined) {
      return {
        ok: true,
        target: {
          kind: "record",
          id: owner,
          anchor: id,
          anchorKind: CHILD_KIND_BY_LETTER[letter] ?? null,
          asWritten: id,
        },
      };
    }
    return {
      ok: true,
      target: { kind: shape.opens, id, anchor: null, anchorKind: null, asWritten: id },
    };
  }
  return {
    ok: false,
    outcome: "unknown-shape",
    refusal:
      `${JSON.stringify(raw)} is not an id shape this app knows. The shapes it knows are: ` +
      `${shapesItKnows()}.`,
  };
}

// ------------------------------------------------------------------- the URL itself

export interface ParsedUrl {
  /** The authority: which project this link is about, as the link writer spelled it. */
  project: string;
  /** The id, undecoded of nothing -- `%20` and friends are resolved here. */
  id: string;
  /** `?column=` when the link carried one, else "active". */
  column: OpenColumn;
}

export type UrlParse =
  | { ok: true; url: ParsedUrl }
  | { ok: false; outcome: Extract<LinkRefusal, "not-our-scheme" | "malformed">; refusal: string };

/** Is this a link this app is supposed to take? Cheap, for an interception hot path. */
export function isKyoseiUrl(raw: string): boolean {
  return raw.trimStart().toLowerCase().startsWith(`${URL_SCHEME}:`);
}

/**
 * Read one `kyosei://<project>/<id>` into its parts.
 *
 * `new URL` rather than a regex, because a URL arriving from `open-url` has been
 * through the operating system and a URL arriving from a web page has been through
 * Chromium: both may percent-encode, and a hand-rolled split would read `KYO%2D12` as
 * an id with a literal `%` in it. What is NOT delegated is the meaning of the parts --
 * the authority is the project and the first path segment is the id, and an extra
 * segment is refused rather than ignored, because a link that means something this
 * app does not implement must not silently open the prefix of it.
 */
export function parseKyoseiUrl(raw: string): UrlParse {
  if (!isKyoseiUrl(raw)) {
    return {
      ok: false,
      outcome: "not-our-scheme",
      refusal: `${JSON.stringify(raw)} is not a ${URL_SCHEME}:// link`,
    };
  }
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch (error) {
    return {
      ok: false,
      outcome: "malformed",
      refusal: `${JSON.stringify(raw)} is not a URL (${(error as Error).message})`,
    };
  }
  const segments = url.pathname.split("/").filter((part) => part !== "");
  const project = decodeURIComponent(url.hostname);
  if (project === "" || segments.length === 0) {
    return {
      ok: false,
      outcome: "malformed",
      refusal:
        `${JSON.stringify(raw)} names no project and id. A link is ` +
        `\`${URL_SCHEME}://<project>/<id>\`, for example \`${URL_SCHEME}://kyosei/KYO-12.c3\`.`,
    };
  }
  if (segments.length > 1) {
    return {
      ok: false,
      outcome: "malformed",
      refusal:
        `${JSON.stringify(raw)} carries ${segments.length} path segments, and a link is ` +
        `\`${URL_SCHEME}://<project>/<id>\` -- one id and no more. Opening its first ` +
        "segment would be this app acting on a link it does not understand.",
    };
  }
  const asked = url.searchParams.get("column");
  return {
    ok: true,
    url: {
      project,
      id: decodeURIComponent(segments[0] as string),
      column: isOpenColumn(asked) ? asked : "active",
    },
  };
}

// ------------------------------------------------------- which project, and the ask

/**
 * A project this app could open a link against: how it is named, where it is, and
 * WHICH SERVER it is served from (KYO-463).
 *
 * The connection is part of the identity, not a detail of it. A project is (server,
 * name): two machines' `kyo serve`s can both serve a store whose prefix is `KYO` and
 * whose root is `~/code/kyosei`, and before this field a link followed from a window
 * on machine A's project matched machine B's copy by prefix and opened there -- the
 * same id, the wrong store, nothing saying so.
 */
export interface KnownProject {
  /** The store's own record-id prefix, when one is known. */
  prefix: string | null;
  /** The project root, on the machine that serves it. */
  root: string;
  /** The kyo serve it is read through: null for this machine's own. */
  connection: RemoteFacts | null;
  /** Is this the project the asking window (or, with none, the app) has open? */
  current: boolean;
}

/** How a sentence names a project's connection. */
export function connectionLabel(connection: RemoteFacts | null): string {
  return connection === null
    ? "this machine's kyo serve"
    : `the kyo serve at ${addressLabel(connection)}`;
}

/** Every project a link's `<project>` names, the one it resolves to first. */
function matchesOf(asked: string, known: readonly KnownProject[]): KnownProject[] {
  const wanted = asked.trim().toLowerCase();
  if (wanted === "") return [];
  const names = (project: KnownProject): string[] => {
    const base = project.root.split("/").filter((part) => part !== "");
    const last = base[base.length - 1];
    return [project.prefix ?? "", last ?? ""].filter((name) => name !== "");
  };
  // The open project first, so a prefix two projects share resolves to the one the
  // reader is looking at; then every project on the SAME connection as it, so a link
  // from a window on a server stays on that server (KYO-463.c2, c3); then the rest, in
  // the order given.
  const current = known.find((project) => project.current) ?? null;
  const rank = (project: KnownProject): number => {
    if (project.current) return 0;
    if (current !== null && sameServer(project.connection, current.connection)) return 1;
    return 2;
  };
  return [...known]
    .map((project, index) => ({ project, index }))
    .sort((a, b) => rank(a.project) - rank(b.project) || a.index - b.index)
    .map(({ project }) => project)
    .filter((project) => names(project).some((name) => name.toLowerCase() === wanted));
}

/**
 * Does this link's `<project>` name one of the projects this app knows?
 *
 * Matched against the store's own prefix AND the project directory's name, both
 * case-insensitively, because both are things a person writing a link by hand would
 * reach for and neither is more correct than the other. A link that names neither is
 * REFUSED with the list -- never opened against whatever happens to be open, which
 * would put a reader in front of the right id in the wrong project with nothing
 * saying so.
 */
export function matchProject(asked: string, known: readonly KnownProject[]): KnownProject | null {
  return matchesOf(asked, known)[0] ?? null;
}

/**
 * When a link's project is served by more than one connection, the sentence saying
 * which one it was opened through and which it was not (KYO-463.c3); null when only one
 * connection serves it.
 */
export function connectionChoice(asked: string, known: readonly KnownProject[]): string | null {
  const matches = matchesOf(asked, known);
  const chosen = matches[0];
  if (chosen === undefined) return null;
  const others = matches.filter((project) => !sameServer(project.connection, chosen.connection));
  if (others.length === 0) return null;
  const why = chosen.current
    ? "the window it came from is on it"
    : known.some((project) => project.current && sameServer(project.connection, chosen.connection))
      ? "the window it came from reads that server"
      : "no window it came from says otherwise";
  const seen = new Set<string>();
  const also: string[] = [];
  for (const project of others) {
    const label = connectionLabel(project.connection);
    if (seen.has(label)) continue;
    seen.add(label);
    also.push(label);
  }
  return `opened through ${connectionLabel(chosen.connection)} (${why}); ${asked} is also served by ${also.join(" and ")}`;
}

/** How a project this app does not know is refused, naming the ones it does. */
export function unknownProjectRefusal(asked: string, known: readonly KnownProject[]): string {
  const listed = known
    .map(
      (project) =>
        `${project.prefix ?? "(no prefix)"} at ${project.root}` +
        (project.connection === null ? "" : ` on ${addressLabel(project.connection)}`),
    )
    .join("; ");
  return (
    `no project called ${JSON.stringify(asked)} is open in this app or in its recent ` +
    `projects, so this link was not followed. ${
      listed === "" ? "This app has no project open." : `The projects it knows are: ${listed}.`
    } Open that project and follow the link again.`
  );
}

/**
 * The whole of following a link, from the string to the request -- or to a sentence.
 *
 * One function with four callers (`open-url`, the launch argv, the claude view's two
 * hooks), because "what does this link mean" must not have four answers. Every failure
 * is a named outcome carrying prose a human can act on; nothing here throws and
 * nothing falls back to opening something else.
 */
export function followLink(
  raw: string,
  known: readonly KnownProject[],
  source: OpenSource,
): LinkDelivery {
  const parsed = parseKyoseiUrl(raw);
  if (!parsed.ok) {
    return { ok: false, request: null, outcome: parsed.outcome, refusal: parsed.refusal };
  }
  const project = matchProject(parsed.url.project, known);
  if (project === null) {
    return {
      ok: false,
      request: null,
      outcome: "unknown-project",
      refusal: unknownProjectRefusal(parsed.url.project, known),
    };
  }
  const resolved = resolveId(parsed.url.id);
  if (!resolved.ok) {
    return { ok: false, request: null, outcome: resolved.outcome, refusal: resolved.refusal };
  }
  return {
    ok: true,
    refusal: null,
    request: {
      target: resolved.target,
      column: parsed.url.column,
      projectRoot: project.root,
      connection: project.connection,
      choice: connectionChoice(parsed.url.project, known),
      url: raw.trim(),
      source,
    },
  };
}

/**
 * The `kyosei://` link on a launch's own argv, if there is one.
 *
 * macOS delivers a URL to a RUNNING app through `open-url` and to a cold one through
 * the same event once `ready` fires -- but Windows and Linux put it on argv, and so
 * does `electron . kyosei://...` during development. Both routes exist, so both are
 * read, and `followLink` is what they share.
 */
export function urlFromArgv(argv: readonly string[]): string | null {
  return argv.find((arg) => isKyoseiUrl(arg)) ?? null;
}

// ---------------------------------------------- interception inside the claude view
//
// The claude tab renders claude.ai in a `WebContentsView`, and a `kyosei://` link in
// the controller's own message is the single biggest thing this record buys: a click
// there must land on the record in this app, never in a browser.
//
// TWO HOOKS, because Chromium has two paths and a link takes whichever it likes.
// `setWindowOpenHandler` catches a `target=_blank` or a `window.open`;
// `will-navigate` catches a plain click that would navigate the view itself. The
// claude view already had the first, sending everything to the real browser -- which
// for a `kyosei://` link means handing the OS a URL this very app registered, so it
// would round-trip out to `launchd` and back in through `open-url`. It works and it
// is wrong: the trip is visible, it can raise another copy of the app, and it loses
// which view the click came from.
//
// Both hooks are built here rather than in `main.ts` so that the decision -- ours, or
// the browser's -- is one function a check can drive without a Chromium.

/** What a click inside the claude view should do. */
export type ClaudeLinkDecision =
  | { action: "open-in-app"; delivery: LinkDelivery }
  | { action: "let-the-browser-have-it" };

/**
 * Decide what to do with a URL a page inside the claude view is trying to reach.
 *
 * Only `kyosei://` is taken. Everything else -- every claude.ai navigation, every
 * link to anywhere on the web -- is the browser's, exactly as before: this view
 * renders a first-party site and this app has no business deciding where else it may
 * go. A refused `kyosei://` link is still `open-in-app`, carrying its refusal: the
 * click was unambiguously meant for this app, so telling the reader why it did not
 * work is the answer, and handing a malformed link to a browser is not.
 */
export function decideClaudeLink(url: string, known: readonly KnownProject[]): ClaudeLinkDecision {
  if (!isKyoseiUrl(url)) return { action: "let-the-browser-have-it" };
  return { action: "open-in-app", delivery: followLink(url, known, "claude-view") };
}

// ------------------------------------------ what a message's TEXT names (KYO-263)
//
// The two hooks above catch a NAVIGATION, and in claude.ai there is never one to
// catch: what the site leaves of a `kyosei:` markdown link is an anchor with the
// words and NO href at all -- `<a target="_blank" rel="noreferrer">KYO-263</a>`,
// as copied from the rendered page (KYO-263) -- so a click on it is a
// click on styled text that does nothing in the page either.
//
// What reads that text is the preload KYO-321 decided (`claude-preload.ts`): one
// capture-phase click listener that sends the clicked anchor's own text here. The
// gesture is a single left click and there is no selection in it; an earlier
// right-click menu is gone, because claude.ai raises its OWN menu and a
// native one can never appear there (KYO-263.c3's verdict).
//
// A bare id and a bare `kyosei://` URL in plain text stay unclickable, and that is
// the site's doing as well: neither is an anchor, so there is no element to click
// (KYO-263). `referencesInText` still reads both, because the text INSIDE the
// anchor is whichever of the two the writer spelled.

/**
 * How much of a piece of text is scanned.
 *
 * The text arriving here is untrusted -- it is whatever a page put inside an anchor --
 * and this runs on the main process's thread, between a click and a record opening.
 * Clipped rather than refused, because the beginning of a piece of text is the part
 * that names what the reader clicked.
 */
export const MAX_SCANNED_TEXT = 4_000;

/** How many references one piece of text may name. Past this it is prose, not a link. */
export const MAX_REFERENCES = 8;

/** One thing a piece of text names, and what opening it would do. */
export interface TextReference {
  /** Exactly the text that was matched, as the message spelled it. */
  asWritten: string;
  /** A `kyosei://` URL that survived as plain text, or a bare id. */
  form: "url" | "bare-id";
  /** The link this resolves to -- what `followLink` is given, unchanged. */
  url: string;
  /** Where it would land. */
  target: ResolvedTarget;
  /** The project it names: the URL's authority, or the id's own prefix. */
  project: KnownProject;
}

/**
 * The shape of a `kyosei://` URL inside running prose.
 *
 * Deliberately greedy about what a URL may contain and deliberately stupid about
 * where it ends: whitespace, and the brackets and quotes that wrap a URL in a
 * sentence. Trailing sentence punctuation is trimmed below rather than excluded
 * here, because `KYO-12.c3` ends in a digit and `KYO-12.` does not.
 */
const URL_IN_TEXT = new RegExp(`${URL_SCHEME}://[^\\s<>"'\`\\]\\)]+`, "gi");

/**
 * The shape of a bare id inside running prose.
 *
 * Looser than `ID_SHAPES` on purpose and NOT a second grammar: everything it matches
 * is handed to `resolveId`, which is the authority, so a candidate like `KYO-012` or
 * `KYO-1.z9` is found here and refused there. What this adds is only the boundary --
 * a `KYO-12` inside `XKYO-12` or `KYO-12a` is not a reference to anything.
 */
const ID_IN_TEXT = new RegExp(
  `(?<![A-Za-z0-9_-])${PREFIX}-[A-Z]?[0-9]+(?:\\.[a-z][0-9]+)?(?![A-Za-z0-9_-])`,
  "g",
);

/** The trailing characters a sentence puts on a URL, which are not part of it. */
const TRAILING = /[.,;:!?'"`)\]}>]+$/;

/**
 * Every record reference a piece of text names, in the order the text names them.
 *
 * Two passes, URLs first, because a `kyosei://kyo/KYO-12` contains a bare `KYO-12`
 * and one click must not offer to open the same record twice. The URL pass masks
 * what it consumed so the id pass cannot see inside it.
 *
 * Nothing that does not resolve becomes a reference -- an unknown project, an id shape
 * this app has no page for (`KYO-L7`), a prefix no open project answers to. A click in
 * that view lands on ordinary prose far more often than on a record id, so text that
 * names nothing resolves to nothing and the click is left exactly as it was. The
 * refusals `followLink` writes are for a `kyosei://` link that was CLICKED as a link.
 */
export function referencesInText(
  raw: string,
  known: readonly KnownProject[],
): readonly TextReference[] {
  if (raw === "") return [];
  const text = raw.length > MAX_SCANNED_TEXT ? raw.slice(0, MAX_SCANNED_TEXT) : raw;
  const found: TextReference[] = [];
  const seen = new Set<string>();
  const add = (reference: TextReference): void => {
    if (seen.has(reference.url) || found.length >= MAX_REFERENCES) return;
    seen.add(reference.url);
    found.push(reference);
  };
  // Pass one: the URLs, and where each one sat, so pass two can skip those spans.
  const spans: Array<[number, number]> = [];
  for (const match of text.matchAll(URL_IN_TEXT)) {
    const at = match.index;
    if (at === undefined) continue;
    const asWritten = match[0].replace(TRAILING, "");
    spans.push([at, at + asWritten.length]);
    const parsed = parseKyoseiUrl(asWritten);
    if (!parsed.ok) continue;
    const project = matchProject(parsed.url.project, known);
    if (project === null) continue;
    const resolved = resolveId(parsed.url.id);
    if (!resolved.ok) continue;
    add({
      asWritten,
      form: "url",
      url: asWritten,
      target: resolved.target,
      project,
    });
  }
  // Pass two: the bare ids, which is what a claude.ai message actually carries.
  for (const match of text.matchAll(ID_IN_TEXT)) {
    const at = match.index;
    if (at === undefined) continue;
    if (spans.some(([from, to]) => at >= from && at < to)) continue;
    const asWritten = match[0];
    const resolved = resolveId(asWritten);
    if (!resolved.ok) continue;
    // A bare id carries its project in its own prefix and nowhere else. Matched
    // against the projects this app knows rather than resolved against whichever one
    // happens to be open: opening the right id in the wrong project is the one
    // failure `matchProject` exists to prevent, and a bare id is the one place where
    // guessing would be easy.
    const prefix = asWritten.slice(0, asWritten.indexOf("-"));
    const project = matchProject(prefix, known);
    if (project === null) continue;
    add({
      asWritten,
      form: "bare-id",
      url: `${URL_SCHEME}://${encodeURIComponent(prefix)}/${encodeURIComponent(asWritten)}`,
      target: resolved.target,
      project,
    });
  }
  return found;
}

// ------------------------------------ what the preload's click means (KYO-263.c5)
//
// `claude-preload.ts` sends one message and this decides what it is worth. The whole
// decision is here, and not in `main.ts`, for the reason every other branch in this
// file is here: the input arrives from a page this app did not write, so every arm --
// a message from the wrong contents, text that is not a string, text that names
// nothing -- has to be drivable without a Chromium to fail with a line number.

/**
 * The longest clicked text this app reads.
 *
 * The preload caps what it sends; this caps what is believed, because the cap on the
 * sending side is in a renderer and a renderer's guarantees end at its own process.
 * An anchor's text is a few words -- the element measured on claude.ai holds one id --
 * so anything past this is not the gesture.
 */
export const MAX_CLICKED_TEXT = 200;

/** What a click the preload reported turns into: one record, or nothing at all. */
export type ClaudeClickDecision =
  | { action: "open"; reference: TextReference }
  | { action: "drop"; why: string };

/** What the main process knows about the message, before any of it is believed. */
export interface ClaudeClick {
  /** `event.sender.id` -- which `webContents` sent this. */
  senderId: number;
  /** The ids of the claude views this app owns right now. */
  claudeViewIds: readonly number[];
  /** The anchor's text, exactly as it arrived: unknown, because a page sent it. */
  text: unknown;
}

/**
 * Decide what a reported click opens -- or why it opens nothing.
 *
 * THE SENDER CHECK IS FIRST AND IS NOT A FORMALITY. `ipcMain` hands every renderer in
 * the app the same channel, and the one contents this message may come from is the
 * claude view's: the preload that sends it is attached to that view alone, so a
 * message from anywhere else is either a bug or a page reaching for a capability it
 * was never given. Dropped with a sentence rather than trusted.
 *
 * Everything after it treats the text as what it is -- a string a remote page chose --
 * so a non-string, an empty one, and one that names no record this app can open are
 * three named drops and not one silent `return`. Only the FIRST reference is opened:
 * a click is one gesture and the anchor the reader clicked names one record; a piece
 * of text that names several is prose that happens to be inside an anchor.
 */
export function decideClaudeClick(
  click: ClaudeClick,
  known: readonly KnownProject[],
): ClaudeClickDecision {
  if (!click.claudeViewIds.includes(click.senderId)) {
    return {
      action: "drop",
      why: `webContents ${click.senderId} is not a claude view of this app`,
    };
  }
  if (typeof click.text !== "string") {
    return { action: "drop", why: `the clicked text is a ${typeof click.text}, not a string` };
  }
  const text = click.text.trim().slice(0, MAX_CLICKED_TEXT);
  if (text === "") return { action: "drop", why: "the clicked element carried no text" };
  const reference = referencesInText(text, known)[0];
  if (reference === undefined) {
    return { action: "drop", why: `${JSON.stringify(text)} names no record this app can open` };
  }
  return { action: "open", reference };
}
