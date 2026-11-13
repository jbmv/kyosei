// The read-only chat. It holds the objects `kyo serve` sent and draws them; it decides nothing
// about a session. Each object is one element, addressed by the object's own id, and a later
// version replaces it in place.
import { agentKind, shortModel } from "./agents";
import {
  type FilePlace, type Place, blockLines, drawnSpan, fileLine, lineWords, heldLine, lineAt, lineOf, nearest,
  placeAnchor, placeKey, recordLine as placedRecord, samePlace, sentenceAt, sourceIndex,
  sourceSpan, spanPlace,
} from "./marks";
import {
  type Answer, type ChatLink, type Entry, type Mapping, type Mention, type RootInfo, type TreeRow, HIGHLIGHT_CHARS,
  type FileSort, type FoundFile, type Recent, BY_NAME, SEARCH_MIN_CHARS, iconOf, kindWords, addressLink, answerOf, foundFiles, orderFound, recentEntries, dirKey, entriesOf, entryOf, fileKind, folderOf, foldersAbove, headOf, hrefPath, isDotfile, isPerSession,
  languageOf, lineCount, linkPlace, localPath, looksOutside, webAddresses, withLink, mentioned, nameOf, pathSpans, rootLabel,
  rootShown, servedRoots, serverPath, sizeWords, stands, treeRows, wholePath,
} from "./files";
import { highlight, render } from "./pipeline";
import { chipPieces } from "./chipwords";
import {
  type Item, type Line, type LinkTarget, type Narrowing, type RecordLocation, type RecordRow, type RecordSort,
  type RecordSortBy, awaitingIds, kindControlText, kindsOf, kindsPresent, pressed,
  dayOf, dispatchableIds, historyFrom, historyRows, located, locationFrom, LOCATIONS, membersOf, narrow, newestFirst,
  openPlans,
  parseLink, prefixOf, pressedSort, recordSortFrom, sortRecords, standingOf, storedFilters,
  briefPage, briefRows, notePage, noteRows, sessionAt,
  recordOf, recordPage, refParts, visit, when, withKinds,
} from "./records";
import {
  type History, canBack, canForward, current, fresh, stepBack, stepForward, visitPlace, without,
} from "./history";
import {
  type ProjectsFrame, anchorGoneNote, anchorOpens, choices, keepBoxes, keptBoxes, listNote,
  meetDraft, sessionLinks, typedChoice,
} from "./projects";
import {
  type Agreed, type DraftBox, type Scratch, agreedOn, boxId, boxSignature, mergeDraft, mergeNote, signatures,
} from "./drafts";
import { SendGate } from "./sending";
import {
  type AgendaFrame, type AgendaItem, type Change, type Refusal, type Row, agendaView, listAfter,
  refusalText,
} from "./agenda";
import { moveLines, sectionEnd } from "./outline";
import { Transaction } from "@codemirror/state";
import { EditorView, type ViewUpdate } from "@codemirror/view";
import {
  type FoldedLine, type LineMarks, createEditor, foldedLines, lineOfId, marksOf as lineMarks, placeItem, refreshMarks,
  removeEffect, removeItem, showLanding,
} from "./editor";
import { findSpans, fold, lastHolding, queryPhrase, scrollBy, step, tally } from "./search";
import {
  type Found, type Opened, type Role, chatKey, foundOf, groupBySession, openedEntries, plainWords, searchQuery,
} from "./searchhistory";
import { type StartFrame, startView } from "./start";
import { Terminal } from "@xterm/xterm";
import {
  TERMINAL_FONT, type TerminalSize, bytesOf, fontToFit, lineBreakKeys, viewSize,
} from "./terminal";

interface Turn { id: string; at: string; text: string; images?: { index: number }[] }
interface Reply { message_id: string; uuid?: string; at: string; text: string; thinking?: string | null }
interface Delivery {
  uuid: string; at: string; kind: string; text: string | null; sender: string | null;
  sender_id: string | null; handback: boolean; task_id: string | null; call_id: string | null;
  status: string | null; summary: string | null;
  usage?: { total_tokens: number | null; duration_ms: number | null } | null;
}
interface Subagent {
  agent_id: string; description: string; agent_type: string; tool_call_uuid: string;
  /** What the subagent's own transcript records, null before its first reply (KYO-726). */
  model?: string | null; effort?: string | null;
}
interface ToolCall {
  call_id: string; uuid: string; at: string; name: string; input: Record<string, unknown> | null;
  result: { text: string | null; is_error: boolean } | null;
}
interface CommandOutput { uuid: string; at: string; command: string; args: string; text: string }
interface Batch {
  reset?: boolean; turns?: Turn[]; replies?: Reply[]; tool_calls?: ToolCall[];
  command_outputs?: CommandOutput[]; subagents?: Subagent[]; deliveries?: Delivery[];
  facts?: Facts | null;
}
interface Facts {
  git_branch: string | null;
  context: { percent: number | null; tokens: number | null; window: number | null } | null;
  /** The prompt cache as Claude Code last reported it for this session (KYO-715). */
  cache?: { warm: boolean | null; expires_at: string | null; recache_tokens_if_cold: number | null } | null;
}
/** The Records search field. Other search fields share its class for their looks only. */
const RECORDS_SEARCH = "#records-side .records-search";

interface SessionRow {
  session_id: string; title: string | null; live: boolean; first_seen: string;
  remote_control_url: string | null;
  last_seen?: string | null; ended_at?: string | null;
  /** How many prompts the turn index holds for the session; null when it holds none. */
  prompts?: number | null;
}
interface Changes { added: number; removed: number; untracked: { files: number } }
interface Menu {
  project: string; current: string | null; sessions: { sessions: SessionRow[] };
  changes: Changes | null;
}

const chat = document.getElementById("chat")!;
const live = document.getElementById("live")!;
// The chat scrolls in its own pane, so that its scroll bar stands at the composer's divider
// and not at the window's edge, beyond the composer.
const scroller = document.getElementById("page")!;
const STICK_DISTANCE = 80;
const elements = new Map<string, HTMLElement>();
// State that start-up reads before the code that uses it is reached (KYO-835): which drawers
// are docked per view, how the Files list is sorted, and the starred files.
const DOCK_KEY = "docked";
let docked = stored<Record<string, boolean>>(DOCK_KEY, {}) ?? {};
const FILES_SORT_KEY = "files-sort";
let fileSort: FileSort = stored<FileSort>(FILES_SORT_KEY, BY_NAME) ?? BY_NAME;
/** The project's starred files (KYO-837), from the server, newest first. */
let starredFiles: Recent[] = [];
const rendered = new Map<string, string>();

function log(message: string): void {
  (window as any).webkit?.messageHandlers?.log?.postMessage(message);
}
window.addEventListener("error", (event) => log(`error: ${event.message}`));

function atBottom(): boolean {
  return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < STICK_DISTANCE;
}

/** True when the reader sees the very end of the chat; less than `atBottom`'s band. */
function atEnd(): boolean {
  return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 8;
}

/**
 * KYO-883: the chat does not follow new output down. Messages placed below the reader since
 * they last saw the end are counted on the arrow back to it, and the count clears there.
 */
let unseen = 0;

// A way back to the end of a chat that has been scrolled up, or landed in by a search, or
// that new messages have arrived below.
const jumpBottom = document.getElementById("jump-bottom")!;
const jumpCount = document.getElementById("jump-count")!;
function drawJump(): void {
  const end = atEnd();
  if (end) unseen = 0;
  jumpBottom.classList.toggle("shown", !end);
  jumpBottom.classList.toggle("counted", unseen > 0);
  jumpCount.textContent = unseen ? `New messages: ${unseen}` : "";
}
scroller.addEventListener("scroll", drawJump, { passive: true });
jumpBottom.addEventListener("click", () => {
  scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
});

/** Shows the end of the chat at once, as for a prompt the person has just sent. */
function toEnd(): void {
  scroller.scrollTop = scroller.scrollHeight;
  drawJump();
}

/**
 * When the main conversation last took a prompt, replied or called a tool. A subagent's work
 * and a background task's are not counted: they do not read the main conversation's cache.
 */
let lastMainActivity = "";

/**
 * When the followed session is no longer running, from the session list the menu carries
 * every few seconds (KYO-717); null while it runs or before the list is known.
 */
let endedAt: string | null = null;
let sessionEnded = false;

function drawEnded(): void {
  const row = menu?.sessions.sessions.find((item) => item.session_id === menu?.current);
  sessionEnded = !!row && !row.live;
  endedAt = sessionEnded ? row?.ended_at ?? row?.last_seen ?? "" : null;
  document.body.classList.toggle("ended", sessionEnded);
  drawEmptyChat();
  if (sessionEnded) document.getElementById("interrupt")!.hidden = true;
  drawWorking();
}

/** A prompt reached the session: the top bar's last activity is now (KYO-883). */
function sentNow(): void {
  const now = new Date().toISOString();
  if (now > lastMainActivity) lastMainActivity = now;
  drawActivity();
}

/** True once a batch of the followed session's turns has been drawn, even an empty one. */
let turnsLoaded = false;

/**
 * KYO-752: a live session whose chat has no turns says it is waiting for its first prompt,
 * so the empty window does not read as one that failed to load. It is not shown while the
 * turns are still loading or for a session that has ended.
 */
function drawEmptyChat(): void {
  const waiting = turnsLoaded && !sessionEnded && !!menu?.current && elements.size === 0 && !chat.childElementCount;
  document.getElementById("empty-chat")!.hidden = !waiting;
}

/** What the last press of Start orchestrator came to, until a session is live again. */
let startFrame: StartFrame | null = null;

/** KYO-719: the Start orchestrator panel, offered when the project has no live session. */
function drawStart(): void {
  const live = menu ? menu.sessions.sessions.some((row) => row.live) : null;
  // A later session that ends is offered a fresh button, not the last press's words.
  if (live) startFrame = null;
  const view = startView(live, startFrame);
  document.getElementById("start")!.hidden = !view.shown;
  document.getElementById("start-text")!.textContent = view.text;
  (document.getElementById("start-button") as HTMLButtonElement).disabled = !view.enabled;
}
const ACTIVITY_AMBER_MS = 30 * 60 * 1000;
const ACTIVITY_RED_MS = 45 * 60 * 1000;

/**
 * How long ago the session last did anything, in the top bar. The prompt cache is read on
 * every request, so this is also how long the cache has gone unread, and it turns amber and
 * then red as an hour's cache nears its end.
 */
function drawActivity(): void {
  const label = document.getElementById("top-activity")!;
  let text = "";
  let level = "";
  let title = "";
  if (sessionEnded) {
    // KYO-883: the bottom bar says as little as it can.
    text = "Inactive";
  } else {
    const ago = lastMainActivity ? Date.now() - new Date(lastMainActivity).getTime() : NaN;
    const expires = facts?.cache?.expires_at;
    title = expires
      ? `The prompt cache expires at ${new Date(expires).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
      : "";
    if (Number.isFinite(ago)) {
      text = activityWords(ago);
      level = ago > ACTIVITY_RED_MS ? "red" : ago > ACTIVITY_AMBER_MS ? "amber" : "";
    }
  }
  label.textContent = text;
  label.className = level;
  label.title = title;
  // KYO-883: said in the app's bottom bar, where the project's name was.
  const sent = `${text}|${level}|${title}`;
  if (sent !== activitySent) {
    activitySent = sent;
    (window as any).webkit?.messageHandlers?.strip?.postMessage({ activity: { text, level, title } });
  }
}

/** What the bottom bar was last told of the activity, so it is told only of a change. */
let activitySent = "";

/** How long ago, as briefly as the bottom bar needs: under a minute, minutes, over an hour. */
function activityWords(ago: number): string {
  const minutes = Math.floor(Math.max(0, ago) / 60000);
  if (minutes < 1) return "< 1m ago";
  if (minutes >= 60) return "> 1h ago";
  return `${minutes}m ago`;
}
setInterval(drawActivity, 1000);

/**
 * Runs a change to the page and leaves the reader where they are (KYO-883): new output
 * below is offered by the arrow at the chat's foot, and is not scrolled to.
 */
function keepingPlace(change: () => void): void {
  change();
  drawJump();
}

/** Keys of the objects counted as messages on the arrow: prompts, replies, deliveries, output. */
const MESSAGE_KEY = /^[trdo]:/;

/** Whether objects placed now are new output, counted on the arrow, and not a session's first load. */
let countingNew = false;

function place(key: string, at: string, version: string, build: () => HTMLElement): void {
  if (rendered.get(key) === version) return;
  const element = build();
  element.dataset.key = key;
  element.dataset.at = at;
  const existing = elements.get(key);
  if (existing) {
    existing.replaceWith(element);
  } else {
    if (countingNew && MESSAGE_KEY.test(key)) unseen++;
    // Objects arrive nearly in order, so look from the end.
    let next: Element | null = null;
    for (let node = chat.lastElementChild; node; node = node.previousElementSibling) {
      if ((node as HTMLElement).dataset.at! <= at) break;
      next = node;
    }
    chat.insertBefore(element, next);
  }
  elements.set(key, element);
  rendered.set(key, version);
}

const ICONS = {
  anchor: '<circle cx="12" cy="5" r="2.4"/><path d="M12 7.4V21M8 11h8M4.5 13.5a7.5 7.5 0 0 0 15 0"/>',
  file: '<path d="M20 11.5l-8 8a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.4 8.4a1.7 1.7 0 0 1-2.4-2.4l7.6-7.6"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  held: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.2V12l3.2 2"/>',
  chat: '<path d="M5 5h14a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 19 16h-7.5L7 19.5V16H5a1.5 1.5 0 0 1-1.5-1.5v-8A1.5 1.5 0 0 1 5 5z"/>',
  records: '<rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M8 9h8M8 12.5h8M8 16h5"/>',
  folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.2h7a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  page: '<path d="M7 3.5h7l4 4V19a1.5 1.5 0 0 1-1.5 1.5h-9.5A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5z"/><path d="M14 3.5v4h4M8.5 12h7M8.5 15.5h7"/>',
  home: '<path d="M4 11.2L12 4l8 7.2"/><path d="M6 10v9.5h4.5V14h3v5.5H18V10"/>',
  back: '<path d="M14.5 5.5L8 12l6.5 6.5"/>',
  forward: '<path d="M9.5 5.5L16 12l-6.5 6.5"/>',
  up: '<path d="M5.5 14.5L12 8l6.5 6.5"/>',
  down: '<path d="M5.5 9.5L12 16l6.5-6.5"/>',
  close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  tag: '<path d="M3.5 11.6V4.5a1 1 0 0 1 1-1h7.1l8.9 8.9a1 1 0 0 1 0 1.4l-6.7 6.7a1 1 0 0 1-1.4 0z"/><circle cx="8" cy="8" r="1.3"/>',
  code: '<path d="M8.5 7.5L4 12l4.5 4.5M15.5 7.5L20 12l-4.5 4.5M13.5 5.5l-3 13"/>',
  image: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M4 17l5-4.5 3.5 3 3-2.5L20 17"/>',
  history: '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.2 4.8v3.6h3.6"/><path d="M12 8v4.3l2.8 1.7"/>',
};

/** A bare line icon in the colour of the text around it. */
function icon(kind: keyof typeof ICONS): SVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.innerHTML = ICONS[kind];
  return svg;
}

/**
 * Fills the three bare icons of a view's top bar, once, and wires them to what each does.
 * `can` says whether Back and Forward have somewhere to go, so the others are dimmed.
 */
/** Each view's Home button, which a press on the view's own name in the top bar also presses. */
const homeButtons = new Map<string, HTMLButtonElement>();

function navIcons(prefix: string, steps: { home: () => void; back: () => void; forward: () => void }): void {
  for (const kind of ["home", "back", "forward"] as const) {
    const button = document.getElementById(`${prefix}-${kind}`)! as HTMLButtonElement;
    button.replaceChildren(icon(kind));
    button.addEventListener("click", () => {
      if (!button.disabled) steps[kind]();
    });
    if (kind === "home") homeButtons.set(prefix, button);
  }
}

function drawNav(prefix: string, homeNow: boolean, history: History<unknown>): void {
  (document.getElementById(`${prefix}-home`) as HTMLButtonElement).disabled = homeNow;
  (document.getElementById(`${prefix}-back`) as HTMLButtonElement).disabled = !canBack(history);
  (document.getElementById(`${prefix}-forward`) as HTMLButtonElement).disabled = !canForward(history);
}

/**
 * `on your reply <uuid> "words..."`, as `kyo serve` writes it above an anchored paragraph. A
 * short selection (KYO-733) is `"<its sentence>..." at "<selection>"`, and a heading, list item,
 * table cell or code block ends with `(heading)` and the like. Group 2 is the quoted words,
 * group 3 the person's own selection when the line has one.
 */
const ANCHOR_LINE = /^on (?:your reply|my prompt) ([0-9a-f-]{36})(?: in session [0-9a-f-]{36})? (?:"(.*?)"(?: at "(.*)")?(?: \((?:heading|list item|table cell|code block)\))?|\(words unavailable\))$/;
/** The drawn reply each transcript record belongs to, where the server names one. */
const recordKeys = new Map<string, string>();

/** Goes to the place a sent anchor names: by its record, or failing that by its words. */
function revealRecord(uuid: string, words: string): void {
  const start = words.replace(/…$/, "");
  const key = recordKeys.get(uuid) ?? (elements.has(`t:${uuid}`) ? `t:${uuid}` : undefined)
    ?? [...sources].reverse().find(([, text]) => start && text.includes(start))?.[0];
  const holder = key ? elements.get(key) : undefined;
  if (!holder) return;
  const blocks = Array.from(holder.querySelectorAll<HTMLElement>("[data-source-line]"))
    .filter((block) => !block.querySelector("[data-source-line]"));
  const block = blocks.find((candidate) => start && candidate.textContent?.includes(start)) ?? holder;
  block.scrollIntoView({ block: "center", behavior: "smooth" });
  block.classList.add("revealed");
  window.setTimeout(() => block.classList.remove("revealed"), 1600);
}

/** The text each prompt and reply was drawn from, by its key, which an anchor measures against. */
const sources = new Map<string, string>();

const imageData = new Map<string, string>();

function drawImage(turn: Turn, index: number, session?: string): HTMLElement {
  const key = `${turn.id}/${index}`;
  const image = document.createElement("img");
  image.className = "turn-image";
  const data = imageData.get(key);
  if (data) {
    image.src = data;
  } else {
    image.dataset.image = key;
    // The History reader names its session; the chat's images are the live session's.
    post("image", session ? { turn: turn.id, index, session } : { turn: turn.id, index });
  }
  return image;
}

/** `attached <absolute path>`, as `kyo serve` writes it for a file on a paragraph (KYO-715). */
const ATTACHED_LINE = /^attached (?:.*\/)?(([0-9a-f]{64})\.(png|jpe?g|gif|webp|pdf))$/;

/** A sent attachment: the image itself, fetched by its id, or a chip naming any other file. */
function drawAttached(line: string, name: string, id: string, extension: string, session?: string): HTMLElement {
  if (extension === "pdf") {
    const chip = document.createElement("span");
    chip.className = "turn-anchor";
    chip.append(icon("file"), `PDF ${id.slice(0, 8)}`);
    chip.title = line;
    return chip;
  }
  const key = `a:${id}`;
  const image = document.createElement("img");
  image.className = "turn-image";
  image.title = line;
  image.alt = name;
  const data = imageData.get(key);
  if (data) {
    image.src = data;
  } else {
    image.dataset.image = key;
    // An attachment is kept with its session, so the History reader names the one it reads.
    post("image", session ? { attachment: id, session } : { attachment: id });
  }
  return image;
}

/**
 * The line `kyo serve` puts above a prompt it held across replies (KYO-730), drawn as a chip
 * like an anchor's: how long the prompt was held and the reply it was written before, which a
 * click goes to.
 */
function drawHeld(line: string): HTMLElement | null {
  const held = heldLine(line);
  if (!held) return null;
  const words = held.words.replace(/\.\.\.$/, "…");
  const later = held.later ? ` and ${held.later} later ${held.later === 1 ? "reply" : "replies"}` : "";
  const chip = document.createElement("span");
  chip.className = "turn-anchor turn-held";
  chip.append(icon("held"), `held ${held.held}, written before “${words}”${later}`);
  chip.title = line;
  chip.addEventListener("click", () => revealRecord(held.uuid, words));
  return chip;
}

/**
 * The line `kyo serve` puts above a paragraph anchored to a record or to a file (KYO-737),
 * drawn as an anchor to a reply is: a bare icon and words. A click opens the record at the
 * element, or the file at the lines, in its view.
 */
function drawPlaceLine(line: string): HTMLElement | null {
  const record = placedRecord(line);
  const file = record ? null : fileLine(line);
  if (!record && !file) return null;
  const chip = document.createElement("span");
  chip.className = "turn-anchor turn-place";
  chip.title = line;
  if (record) {
    const words = record.words.replace(/\.\.\.$/, "…");
    chip.dataset.place = placeKey({ kind: "record", id: record.id });
    chip.append(icon("records"), record.id, ...(words ? [" ", ...drawChipWords(words)] : []));
    chip.addEventListener("click", () => revealPlace({ kind: "record", id: record.id, words }));
  } else if (file) {
    const words = (file.selection ?? file.words).replace(/\.\.\.$/, "…");
    chip.dataset.place = placeKey({ kind: "path", ...file });
    chip.append(icon("page"), `${nameOf(file.path)} ${lineWords(file.lines)}`, ...(words ? [" ", ...drawChipWords(words)] : []));
    chip.addEventListener("click", () => revealPlace({
      kind: "path", root: file.root, path: file.path, lines: file.lines, words,
      ...(file.session ? { session: file.session } : {}),
    }));
  }
  return chip;
}

/** A chip's quoted words as inline markdown; the chip's title and click keep the source words (KYO-758). */
function drawChipWords(words: string): Node[] {
  return chipPieces(words).map((piece) => {
    let node: Node = document.createTextNode(piece.text);
    if (piece.code) {
      const code = document.createElement("code");
      code.append(node);
      node = code;
    }
    if (piece.italic) {
      const em = document.createElement("em");
      em.append(node);
      node = em;
    }
    if (piece.bold) {
      const strong = document.createElement("strong");
      strong.append(node);
      node = strong;
    }
    return node;
  });
}

function drawTurn(turn: Turn, session?: string): HTMLElement {
  const element = document.createElement("div");
  element.className = "turn";
  for (const { index } of turn.images ?? []) element.append(drawImage(turn, index, session));
  // The line the server puts above an anchored paragraph is drawn as a chip. The text
  // around it stays as it was sent, which is what an anchor on this prompt measures against.
  let plain: string[] = [];
  const flush = (last: boolean) => {
    if (plain.length) element.append(document.createTextNode(plain.join("\n") + (last ? "" : "\n")));
    plain = [];
  };
  for (const [index, line] of (turn.text ?? "").split("\n").entries()) {
    // Only the server writes above a prompt's first line, so the line is read there alone.
    const held = index === 0 ? drawHeld(line) : null;
    if (held) {
      element.append(held);
      continue;
    }
    // KYO-737: an anchor to a record or to lines of a file is a chip that opens the place.
    const place = drawPlaceLine(line);
    if (place) {
      flush(false);
      element.append(place);
      continue;
    }
    const anchor = line.match(ANCHOR_LINE);
    const attached = anchor ? null : line.match(ATTACHED_LINE);
    if (attached) {
      flush(false);
      element.append(drawAttached(line, attached[1], attached[2], attached[3], session));
      continue;
    }
    if (!anchor) {
      plain.push(line);
      continue;
    }
    flush(false);
    // The chip shows what the person selected: the `at` words when the line has them.
    const words = (anchor[3] ?? anchor[2] ?? "").replace(/\.\.\.$/, "…");
    const chip = document.createElement("span");
    chip.className = "turn-anchor";
    chip.append(icon("anchor"), ...(words ? drawChipWords(words) : ["words unavailable"]));
    chip.title = line;
    // The record the line names, by which a stale anchor finds its chip (KYO-712).
    chip.dataset.anchor = anchor[1];
    chip.addEventListener("click", () => revealRecord(anchor[1], words));
    element.append(chip);
  }
  flush(true);
  return element;
}

/**
 * A copy button in the corner of each code block (KYO-723). It holds an icon and no text, so
 * it adds nothing to what a selection copies or to the words an anchor measures.
 */
function addCopyButtons(holder: HTMLElement): void {
  for (const block of Array.from(holder.querySelectorAll<HTMLElement>("pre"))) {
    const code = block.querySelector("code") ?? block;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy-code";
    button.title = "Copy";
    button.append(icon("copy"));
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const text = (code.textContent ?? "").replace(/\n$/, "");
      const native = (window as any).webkit?.messageHandlers?.copy;
      if (native) native.postMessage({ text });
      else void navigator.clipboard?.writeText(text);
      button.replaceChildren(icon("check"));
      window.setTimeout(() => button.replaceChildren(icon("copy")), 1400);
    });
    block.append(button);
  }
}

function drawReply(reply: Reply): HTMLElement {
  const element = document.createElement("article");
  element.className = "reply";
  element.innerHTML = reply.text ? render(reply.text) : "";
  addCopyButtons(element);
  if (reply.thinking) {
    const thinking = document.createElement("div");
    thinking.className = "thinking";
    thinking.textContent = reply.thinking;
    element.prepend(thinking);
  }
  return element;
}

function drawDelivery(delivery: Delivery): HTMLElement {
  const element = document.createElement("details");
  element.className = "delivery";
  const head = document.createElement("summary");
  head.textContent = delivery.kind === "peer"
    ? "Message from subagent"
    : delivery.summary ?? "Task notification";
  element.append(head);
  if (delivery.text) {
    const body = document.createElement("div");
    body.className = "reply";
    body.innerHTML = render(delivery.text);
    element.append(body);
  }
  return element;
}

function summary(call: ToolCall): string {
  const input = call.input ?? {};
  for (const key of ["description", "command", "file_path", "pattern", "query", "prompt"]) {
    const value = input[key];
    if (typeof value === "string" && value) return value.split("\n")[0];
  }
  return "";
}

function drawToolCall(call: ToolCall): HTMLElement {
  const element = document.createElement("details");
  element.className = call.result?.is_error ? "tool failed" : "tool";
  const head = document.createElement("summary");
  const name = document.createElement("b");
  name.textContent = call.name;
  const what = document.createElement("span");
  what.textContent = summary(call);
  head.append(name, what);
  const body = document.createElement("pre");
  body.textContent =
    JSON.stringify(call.input, null, 2) + (call.result?.text ? `\n\n${call.result.text}` : "");
  element.append(head, body);
  return element;
}

interface Background {
  running: boolean; shells: number; agents: { type: string; description: string }[];
}
interface ScreenFrame { screen?: { state: string; background?: Background | null } | null }

const working = document.getElementById("working")!;
const workingText = document.getElementById("working-text")!;
const calls = new Map<string, ToolCall>();
let screenState: string | null = null;
let background: Background | null = null;
let workingLine: string | null = null;
let latestAt = "";

const TOOL_VERBS: Record<string, string> = {
  Bash: "Running a command", Read: "Reading a file", Edit: "Editing a file",
  Write: "Writing a file", Grep: "Searching", Glob: "Searching", Agent: "Starting an agent",
  WebFetch: "Fetching a page", WebSearch: "Searching the web", Skill: "Loading a skill",
};

function duration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function elapsed(since: string): string {
  return duration(Date.now() - Date.parse(since));
}

/** The tool being run, when the last call the session made has no result yet. */
function runningCall(): ToolCall | null {
  let last: ToolCall | null = null;
  for (const call of calls.values()) if (!last || call.at >= last.at) last = call;
  return last && !last.result ? last : null;
}

function backgroundWords(): string {
  if (!background?.running) return "";
  const parts: string[] = [];
  const agents = background.agents.length;
  if (agents) parts.push(`${agents} agent${agents === 1 ? "" : "s"}`);
  if (background.shells) parts.push(`${background.shells} shell${background.shells === 1 ? "" : "s"}`);
  return parts.length ? `${parts.join(", ")} in the background` : "working in the background";
}

function workingWords(): string {
  if (sessionEnded) return "";
  if (screenState === "permission-prompt") return "Waiting for permission in the terminal";
  if (screenState === "ask-user-question") return "Waiting for your answer in the terminal";
  let words = "";
  if (screenState === "working") {
    const call = runningCall();
    words = call
      ? `${TOOL_VERBS[call.name] ?? `Using ${call.name}`}  ${elapsed(call.at)}`
      : workingLine ?? `Working${latestAt ? `  ${elapsed(latestAt)}` : ""}`;
  }
  return [words, backgroundWords()].filter(Boolean).join("  ·  ");
}

/** What the line under the composer says while the session waits on the person. */
const WAITING = "Waiting for you…";
/** Whether the line last said the session waits on the person; null before any screen was read. */
let waitingSeen: boolean | null = null;

/**
 * The line under the composer, whole: what the session is doing and what runs in the
 * background, or that it waits on the person when it is live and nothing runs. Nothing is
 * said of an ended session, nor before the server has read a pane.
 */
function drawWorking(): void {
  const doing = workingWords();
  const idle = !doing && !sessionEnded && screenState !== null;
  const words = doing || (idle ? WAITING : "");
  // KYO-883: when the session comes to wait on the person, the app bounces its Dock icon if
  // another app is in front. The first state read is not a change, so opening does not bounce.
  if (idle && waitingSeen === false) post("attention", { waiting: true });
  // Work begun again before the person came back ends the bounce.
  if (!idle && waitingSeen === true && screenState !== null) post("attention", { waiting: false });
  if (screenState !== null) waitingSeen = idle;
  working.classList.toggle("idle", idle);
  if (working.hidden === !words && workingText.textContent === words) return;
  workingText.textContent = words;
  // KYO-883: the line keeps to one row, so the composer does not move; hovering shows it whole.
  workingText.title = words;
  working.hidden = !words;
}

// Only the times change here: rebuilding the rows each second would lose a click in progress.
setInterval(() => {
  drawWorking();
  document.querySelectorAll<HTMLElement>("[data-since]").forEach((time) => {
    time.textContent = elapsed(time.dataset.since!);
  });
}, 1000);

// ---------------------------------------------------- tool-call runs and the edited-files card

interface Change { added: number; removed: number; lines: { kind: string; text: string }[] }

const MAX_DIFF_CELLS = 4_000_000;
const openGroups = new Set<string>();
const openFiles = new Set<string>();
const openCards = new Set<string>();
const MAX_FILES_SHOWN = 3;

function linesOf(text: string): string[] {
  if (!text) return [];
  const lines = text.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/** A line diff by longest common subsequence, sized for one edit's text. */
function diffLines(before: string[], after: string[]): Change {
  const lines: Change["lines"] = [];
  if (before.length * after.length > MAX_DIFF_CELLS) {
    for (const text of before) lines.push({ kind: "del", text });
    for (const text of after) lines.push({ kind: "add", text });
  } else {
    const table = Array.from({ length: before.length + 1 }, () =>
      new Uint32Array(after.length + 1));
    for (let i = before.length - 1; i >= 0; i--) {
      for (let j = after.length - 1; j >= 0; j--) {
        table[i][j] = before[i] === after[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < before.length || j < after.length) {
      if (i < before.length && j < after.length && before[i] === after[j]) {
        lines.push({ kind: "same", text: before[i] });
        i++;
        j++;
      } else if (j < after.length && (i === before.length || table[i][j + 1] >= table[i + 1][j])) {
        lines.push({ kind: "add", text: after[j++] });
      } else {
        lines.push({ kind: "del", text: before[i++] });
      }
    }
  }
  return {
    added: lines.filter((line) => line.kind === "add").length,
    removed: lines.filter((line) => line.kind === "del").length,
    lines,
  };
}

const diffs = new Map<string, Change>();

function changeOf(call: ToolCall): Change | null {
  if (call.result?.is_error || (call.name !== "Edit" && call.name !== "Write")) return null;
  let change = diffs.get(call.call_id);
  if (!change) {
    const input = call.input ?? {};
    change = call.name === "Edit"
      ? diffLines(linesOf(String(input.old_string ?? "")), linesOf(String(input.new_string ?? "")))
      : diffLines([], linesOf(String(input.content ?? "")));
    diffs.set(call.call_id, change);
  }
  return change;
}

function pathOf(call: ToolCall): string {
  return String(call.input?.file_path ?? "");
}

function callOf(element: HTMLElement): ToolCall | undefined {
  return calls.get(element.dataset.key!.slice(2));
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

function runWords(run: ToolCall[]): string {
  if (run.length === 1 && typeof run[0].input?.description === "string") {
    return run[0].input.description;
  }
  const groups = new Map<string, Set<string>>();
  const add = (key: string, id: string) => {
    if (!groups.has(key)) groups.set(key, new Set());
    groups.get(key)!.add(id);
  };
  for (const call of run) {
    if (call.name === "Edit" || call.name === "Write") add("edited", pathOf(call));
    else if (call.name === "Read") add("read", pathOf(call));
    else if (call.name === "Bash") add("ran", call.call_id);
    else if (call.name === "Grep" || call.name === "Glob") add("searched", call.call_id);
    else if (call.name === "Agent") add("started", call.call_id);
    else add("used", call.call_id);
  }
  const words = [...groups].map(([verb, ids]) => {
    const count = ids.size;
    if (verb === "edited") return `edited ${plural(count, "file")}`;
    if (verb === "read") return `read ${plural(count, "file")}`;
    if (verb === "ran") return count === 1 ? "ran a command" : `ran ${count} commands`;
    if (verb === "searched") return `searched ${count === 1 ? "once" : `${count} times`}`;
    if (verb === "started") return count === 1 ? "started an agent" : `started ${count} agents`;
    return count === 1 ? "used a tool" : `used ${count} tools`;
  }).join(", ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function counts(added: number, removed: number): HTMLElement[] {
  const plus = document.createElement("span");
  plus.className = "added";
  plus.textContent = `+${added}`;
  const minus = document.createElement("span");
  minus.className = "removed";
  minus.textContent = `−${removed}`;
  return added || removed ? [plus, minus] : [];
}

function addGroupHead(run: HTMLElement[]): void {
  const first = run[0];
  const key = first.dataset.key!;
  const toolCalls = run.map(callOf).filter((call): call is ToolCall => Boolean(call));
  const open = openGroups.has(key);
  const changes = toolCalls.map(changeOf).filter((change): change is Change => Boolean(change));
  const head = document.createElement("div");
  head.className = open ? "group-head open" : "group-head";
  head.dataset.at = first.dataset.at;
  const label = document.createElement("span");
  label.className = "group-label";
  label.textContent = runWords(toolCalls);
  const chevron = document.createElement("span");
  chevron.className = "chevron";
  chevron.textContent = "›";
  head.append(
    label,
    ...counts(
      changes.reduce((sum, change) => sum + change.added, 0),
      changes.reduce((sum, change) => sum + change.removed, 0),
    ),
    chevron,
  );
  head.addEventListener("click", () => {
    if (openGroups.has(key)) openGroups.delete(key);
    else openGroups.add(key);
    arrange();
  });
  for (const element of run) element.hidden = !open;
  first.before(head);
}

function addFilesCard(after: HTMLElement, edits: ToolCall[]): void {
  const files = new Map<string, Change[]>();
  for (const call of edits) {
    const change = changeOf(call);
    if (!change) continue;
    if (!files.has(pathOf(call))) files.set(pathOf(call), []);
    files.get(pathOf(call))!.push(change);
  }
  if (!files.size) return;
  const card = document.createElement("div");
  card.className = "files";
  card.dataset.at = after.dataset.at;
  const title = document.createElement("div");
  title.className = "files-title";
  title.textContent = `Edited ${plural(files.size, "file")}`;
  card.append(title);
  let totalAdded = 0;
  let totalRemoved = 0;
  const expanded = openCards.has(after.dataset.key!);
  let shown = 0;
  for (const [path, changes] of files) {
    const added = changes.reduce((sum, change) => sum + change.added, 0);
    const removed = changes.reduce((sum, change) => sum + change.removed, 0);
    totalAdded += added;
    totalRemoved += removed;
    if (!expanded && shown === MAX_FILES_SHOWN) continue;
    shown += 1;
    const key = `${after.dataset.key}|${path}`;
    const row = document.createElement("div");
    row.className = "file-row";
    row.title = path;
    const name = document.createElement("span");
    name.className = "file-name";
    name.textContent = path.split("/").pop() ?? path;
    row.append(name, ...counts(added, removed));
    row.addEventListener("click", () => {
      if (openFiles.has(key)) openFiles.delete(key);
      else openFiles.add(key);
      arrange();
    });
    card.append(row);
    if (openFiles.has(key)) {
      const diff = document.createElement("pre");
      diff.className = "diff";
      for (const change of changes) {
        for (const line of change.lines) {
          const text = document.createElement("div");
          text.className = line.kind;
          text.textContent = `${line.kind === "add" ? "+" : line.kind === "del" ? "-" : " "} ${line.text}`;
          diff.append(text);
        }
      }
      card.append(diff);
    }
  }
  if (files.size > 1) {
    const total = document.createElement("div");
    total.className = "file-row total";
    const name = document.createElement("span");
    name.className = "file-name";
    const canExpand = files.size > MAX_FILES_SHOWN;
    name.textContent = !canExpand ? "Total" : expanded ? "Show fewer" : "See all";
    total.append(name, ...counts(totalAdded, totalRemoved));
    if (canExpand) {
      total.classList.add("expander");
      total.addEventListener("click", () => {
        if (openCards.has(after.dataset.key!)) openCards.delete(after.dataset.key!);
        else openCards.add(after.dataset.key!);
        arrange();
      });
    }
    card.append(total);
  }
  after.after(card);
}

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

function stampWords(at: string): string {
  const age = Date.now() - Date.parse(at);
  const when = new Date(at);
  if (age < MINUTE_MS) return "just now";
  if (age < HOUR_MS) {
    const minutes = Math.floor(age / MINUTE_MS);
    return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  }
  const time = when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return age < DAY_MS ? time : `${when.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`;
}

/** When a turn of work ended, under its last element. */
function addStamp(after: HTMLElement): void {
  const stamp = document.createElement("div");
  stamp.className = "stamp";
  stamp.dataset.at = after.dataset.at;
  stamp.dataset.stamp = after.dataset.at;
  stamp.textContent = stampWords(after.dataset.at!);
  stamp.title = new Date(after.dataset.at!).toLocaleString();
  after.after(stamp);
}

setInterval(() => {
  document.querySelectorAll<HTMLElement>("[data-stamp]").forEach((stamp) => {
    stamp.textContent = stampWords(stamp.dataset.stamp!);
  });
}, 30_000);

/** Lays the generated rows over the objects: nothing here is an object of the session. */
function arrange(): void {
  chat.querySelectorAll(".group-head, .files, .stamp").forEach((node) => node.remove());
  const children = Array.from(chat.children) as HTMLElement[];
  let run: HTMLElement[] = [];
  let edits: ToolCall[] = [];
  let last: HTMLElement | null = null;
  const endRun = () => {
    if (run.length) addGroupHead(run);
    run = [];
  };
  const endTurn = (finished: boolean) => {
    if (finished && last && !last.classList.contains("turn")) addStamp(last);
    if (finished && last && edits.length) addFilesCard(last, edits);
    edits = [];
  };
  for (const child of children) {
    if (child.classList.contains("tool")) {
      run.push(child);
      const call = callOf(child);
      if (call && changeOf(call)) edits.push(call);
    } else {
      endRun();
      // A subagent's message wakes the session, which starts a new turn of work.
      if (child.classList.contains("turn") || child.classList.contains("delivery")) endTurn(true);
    }
    last = child;
  }
  endRun();
  // The card closes a turn, so the turn still running has none yet.
  endTurn(screenState !== "working");
}

// ------------------------------------------------------------- the menu and the side panel

const subagents = new Map<string, Subagent>();
const openAgents = new Set<string>();
const agentCalls = new Map<string, ToolCall[]>();
const agentDispatches = new Map<string, { added: number; removed: number }>();
const requestedAgents = new Set<string>();
let showFinishedAgents = false;
let showFinishedTasks = false;
const deliveries: Delivery[] = [];
let facts: Facts | null = null;
let menu: Menu | null = null;

const SESSIONS_SHOWN = 4;
let allSessions = false;

function select(session: string): void {
  // The prompts being typed are the session's that is left, and are saved to it first.
  flushDraft();
  (window as any).webkit?.messageHandlers?.select?.postMessage({ session });
}

function sessionLabel(row: SessionRow): string {
  return row.title ?? new Date(row.first_seen).toLocaleString([], {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function drawMenu(): void {
  if (!menu) return;
  drawHistorySessions();
  const { current, sessions: { sessions } } = menu;
  const shown = allSessions ? sessions : sessions.slice(0, SESSIONS_SHOWN);
  const more = document.getElementById("sessions-more")!;
  more.hidden = sessions.length <= SESSIONS_SHOWN;
  more.textContent = allSessions ? "Show fewer" : "See all";
  document.getElementById("sessions")!.replaceChildren(
    ...shown.map((row) => {
      const item = document.createElement("div");
      item.className = row.session_id === current ? "session current" : "session";
      if (row.live) {
        const dot = document.createElement("span");
        dot.className = "live-dot";
        item.append(dot);
      }
      const label = document.createElement("span");
      label.className = "session-label";
      label.textContent = sessionLabel(row);
      item.append(label);
      item.addEventListener("click", () => select(row.session_id));
      return item;
    }),
  );
}

function setText(id: string, text: string): void {
  document.getElementById(id)!.textContent = text;
}

/**
 * A list's "N finished" line with its disclosure mark (KYO-751). The mark is one drawing, a
 * chevron centred in a square box, that is turned about the box's centre when the list is open;
 * swapping one text glyph for another put the open mark off the closed one's centre.
 */
function drawFinished(id: string, count: number, open: boolean): void {
  const mark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  mark.setAttribute("viewBox", "0 0 10 10");
  mark.setAttribute("class", open ? "disclosure open" : "disclosure");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M3.5 2 L6.5 5 L3.5 8");
  mark.append(path);
  document.getElementById(id)!.replaceChildren(`${count} finished `, mark);
}

function drawSide(): void {
  const session = menu?.sessions.sessions.find((row) => row.session_id === menu?.current);
  setText("top-title", session?.title ?? menu?.project ?? windowProject);
  drawActivity();
  drawAgents();

  const own = [...calls.values()].filter(isOwnBackgroundTask);
  const runningOwn = own.filter((call) => !callFinished(call));
  const ownLabel = (call: ToolCall) => String(call.input?.description ?? summary(call) ?? call.name);
  sideList("side-tasks", runningOwn.map((call) => ({ label: ownLabel(call), since: call.at })));
  const finishedOwn = own.filter(callFinished).reverse();
  sideList(
    "side-tasks-done",
    showFinishedTasks
      ? finishedOwn.map((call) => ({ label: ownLabel(call), took: tookWords(call) }))
      : [],
  );
  drawFinished("side-finished", finishedOwn.length, showFinishedTasks);
}

function tookWords(call: ToolCall): string | undefined {
  const done = deliveries.find((delivery) => delivery.call_id === call.call_id);
  return done ? duration(Date.parse(done.at) - Date.parse(call.at)) : undefined;
}

function drawAgentRow(agent: Subagent, running: boolean): HTMLElement[] {
  const row = document.createElement("div");
  row.className = running ? "side-task clickable running" : "side-task clickable";
  const name = document.createElement("span");
  name.className = "side-task-name";
  name.textContent = agent.description || agent.agent_type;
  name.title = name.textContent;
  row.append(name);
  const since = [...calls.values()].find((call) => call.uuid === agent.tool_call_uuid)?.at;
  if (running && since) {
    const time = document.createElement("span");
    time.className = "side-task-time";
    time.dataset.since = since;
    time.textContent = elapsed(since);
    row.append(time);
  }
  row.addEventListener("click", () => {
    if (openAgents.has(agent.agent_id)) openAgents.delete(agent.agent_id);
    else {
      openAgents.add(agent.agent_id);
      requestAgent(agent.agent_id);
    }
    drawAgents();
  });
  const rows: HTMLElement[] = [row];
  if (!running && !agentCalls.has(agent.agent_id) && !requestedAgents.has(agent.agent_id)) {
    requestedAgents.add(agent.agent_id);
    requestAgent(agent.agent_id);
  }
  // The second line: what it runs on, then its tokens, how long it took and its lines. It
  // does not wrap, so a long one is cut at the panel's edge.
  const words = agentMeta(agent, running);
  const meta = document.createElement("div");
  meta.className = running ? "agent-meta running" : "agent-meta";
  meta.append(words.parts.join("  ·  "));
  if (words.lines) {
    if (words.parts.length) meta.append("  ·  ");
    meta.append(...words.lines);
  }
  rows.push(meta);
  if (!openAgents.has(agent.agent_id)) return rows;
  const list = document.createElement("div");
  list.className = "agent-calls";
  const toolCalls = agentCalls.get(agent.agent_id);
  if (!toolCalls) list.textContent = "Loading";
  else if (!toolCalls.length) list.textContent = "No tool calls";
  for (const call of toolCalls ?? []) {
    const line = document.createElement("div");
    line.textContent = `${call.name}  ${summary(call)}`;
    line.title = line.textContent;
    if (call.result?.is_error) line.className = "failed";
    list.append(line);
  }
  return [...rows, list];
}

function tokenWords(total: number): string {
  if (total < 1000) return String(total);
  return total < 1_000_000
    ? `${Math.round(total / 1000)}k`
    : `${(total / 1_000_000).toFixed(1)}M`;
}

/** The words of a subagent's second line. A running one's tokens come from its transcript. */
function agentMeta(
  agent: Subagent, running: boolean,
): { parts: string[]; lines: HTMLElement[] | null } {
  const usage = deliveries.find((delivery) =>
    delivery.task_id === agent.agent_id && delivery.usage)?.usage;
  const dispatch = agentDispatches.get(agent.agent_id);
  const tokens = running
    ? (agentUsage.has(agent.agent_id) ? tokenWords(agentUsage.get(agent.agent_id)!) : null)
    : (usage?.total_tokens ? tokenWords(usage.total_tokens) : null);
  return {
    parts: [kindOf(agent), tokens && `${tokens} tokens`, usage?.duration_ms ? duration(usage.duration_ms) : null]
      .filter((part): part is string => Boolean(part)),
    lines: dispatch ? counts(dispatch.added, dispatch.removed) : null,
  };
}

/** `fable medium`, from what the subagent's route last stated, else what the turns listed. */
function kindOf(agent: Subagent): string {
  return agentKind({ ...agent, ...agentFacts.get(agent.agent_id) });
}

/** The model and effort each subagent's route last stated, which can be later than the list's. */
const agentFacts = new Map<string, { model: string | null; effort: string | null }>();

function drawAgents(): void {
  const agents = [...subagents.values()];
  const running = agents.filter((agent) => !agentFinished(agent));
  const finished = agents.filter(agentFinished).reverse();
  document.getElementById("side-agents")!.replaceChildren(
    ...running.flatMap((agent) => drawAgentRow(agent, true)),
  );
  document.getElementById("side-agents-done")!.replaceChildren(
    ...(showFinishedAgents ? finished.flatMap((agent) => drawAgentRow(agent, false)) : []),
  );
  drawFinished("side-agents-finished", finished.length, showFinishedAgents);
  drawRunningAgents(running, finished);
  // The app is told the count as well, for anything outside the page that states it.
  (window as any).webkit?.messageHandlers?.strip?.postMessage({
    subagents: running.length,
    agents: running.map((agent) => ({
      name: agent.description,
      type: agent.agent_type,
      model: shortModel(agentFacts.get(agent.agent_id)?.model ?? agent.model),
      effort: agentFacts.get(agent.agent_id)?.effort ?? agent.effort ?? "",
      since: [...calls.values()].find((call) => call.uuid === agent.tool_call_uuid)?.at ?? "",
      tokens: agentUsage.has(agent.agent_id) ? tokenWords(agentUsage.get(agent.agent_id)!) : "",
    })),
  });
}

/**
 * `Subagents: N` under the composer's Send, green while any runs. A press lists them, as
 * Chat's side bar did before it went (KYO-883): the running ones, then under a rule the
 * finished ones, newest first, each with what it ran on, its tokens, its time and its lines.
 */
function drawRunningAgents(running: Subagent[], finished: Subagent[]): void {
  const count = document.getElementById("running-agents-count")!;
  count.textContent = `Subagents: ${running.length}`;
  count.classList.toggle("running", running.length > 0);
  const list = document.getElementById("running-agents-list")!;
  if (!running.length && !finished.length) list.hidden = true;
  const row = (agent: Subagent, isRunning: boolean) => {
    const item = document.createElement("div");
    item.className = isRunning ? "running-agent running" : "running-agent";
    const words = agentMeta(agent, isRunning);
    const facts = span("running-agent-facts", words.parts.join("  ·  "));
    if (words.lines) {
      if (words.parts.length) facts.append("  ·  ");
      facts.append(...words.lines);
    }
    item.append(span("running-agent-name", agent.description || agent.agent_type), facts);
    return item;
  };
  const rule = document.createElement("hr");
  rule.className = "running-agents-rule";
  list.replaceChildren(
    ...running.map((agent) => row(agent, true)),
    ...(running.length && finished.length ? [rule] : []),
    ...finished.map((agent) => row(agent, false)),
  );
}

document.getElementById("running-agents-count")!.addEventListener("click", () => {
  const list = document.getElementById("running-agents-list")!;
  list.hidden = !list.hidden || !list.childElementCount;
  // A finished agent's lines and tokens are read when the list is first opened.
  if (!list.hidden) {
    for (const agent of subagents.values()) {
      if (agentFinished(agent) && !agentCalls.has(agent.agent_id) && !requestedAgents.has(agent.agent_id)) {
        requestedAgents.add(agent.agent_id);
        requestAgent(agent.agent_id);
      }
    }
  }
});
document.addEventListener("mousedown", (event) => {
  if (!document.getElementById("running-agents")!.contains(event.target as Node)) {
    document.getElementById("running-agents-list")!.hidden = true;
  }
});

/** Each subagent's latest turn in tokens, as the server last read its transcript. */
const agentUsage = new Map<string, number>();
const USAGE_REFRESH_MS = 15000;

// A running subagent's count grows, so it is asked for again while any is running.
setInterval(() => {
  for (const agent of subagents.values()) {
    if (!agentFinished(agent)) requestAgent(agent.agent_id);
  }
}, USAGE_REFRESH_MS);

function requestAgent(agent: string): void {
  (window as any).webkit?.messageHandlers?.agent?.postMessage({ agent });
}

function sideList(id: string, rows: { label: string; since?: string; took?: string }[]): void {
  document.getElementById(id)!.replaceChildren(
    ...rows.map(({ label, since, took }) => {
      const item = document.createElement("div");
      item.className = "side-task";
      const name = document.createElement("span");
      name.className = "side-task-name";
      name.textContent = label;
      name.title = label;
      item.append(name);
      if (since) {
        const time = document.createElement("span");
        time.className = "side-task-time";
        time.dataset.since = since;
        time.textContent = elapsed(since);
        item.append(time);
      } else if (took) {
        const time = document.createElement("span");
        time.className = "side-task-time";
        time.textContent = took;
        item.append(time);
      }
      return item;
    }),
  );
}

function agentFinished(agent: Subagent): boolean {
  return deliveries.some((delivery) =>
    delivery.task_id === agent.agent_id || (delivery.handback && delivery.sender_id === agent.agent_id));
}

function callFinished(call: ToolCall): boolean {
  return deliveries.some((delivery) => delivery.call_id === call.call_id);
}

/** This session's own background work. A subagent's is the subagent's, and is not listed. */
function isOwnBackgroundTask(call: ToolCall): boolean {
  return call.name === "Monitor" || (call.name === "Bash" && call.input?.run_in_background === true);
}

// The terminal drawer (KYO-721) is an emulator on the session's pane. The app carries the
// pane's bytes in from `kyo serve` and what is typed back out, since this page may reach
// nothing itself. Where the server refuses the stream, the drawer says why and shows the
// pane's text as it did before, scaled to fit rather than scrolled sideways.
const terminalPane = document.getElementById("terminal")!;
const terminalScreen = document.getElementById("terminal-screen")!;
const terminalNote = document.getElementById("terminal-note")!;
const terminalText = document.getElementById("terminal-text")!;
const TERMINAL_MAX_FONT = 13;
const TERMINAL_PADDING = 32;
const TERMINAL_RESIZE_MS = 120;
let terminalColumns = 0;
let emulator: Terminal | null = null;
/** The size of the pane as the server last painted it, or none before a first screen. */
let paneSize: TerminalSize | null = null;
/** The view's size as last stated to the server, so that it is stated once. */
let statedSize = "";
/** One cell at the base font, measured from a drawn screen; none before the first. */
let terminalCell: { width: number; height: number } | null = null;
let terminalResize: number | undefined;

function terminalIsOpen(): boolean {
  return document.body.classList.contains("terminal-open");
}

function showTerminalNote(text: string): void {
  terminalNote.textContent = text;
  terminalNote.hidden = !text;
}

/** Shows the emulator, or the pane's plain text when there is no stream to draw. */
function showEmulator(shown: boolean): void {
  terminalScreen.hidden = !shown;
  terminalText.hidden = shown;
  if (!shown) fitTerminal();
}

function theEmulator(): Terminal {
  if (emulator) return emulator;
  const styles = getComputedStyle(terminalPane);
  const made = new Terminal({
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    fontSize: TERMINAL_FONT,
    scrollback: 5000,
    theme: {
      background: styles.getPropertyValue("--terminal-background").trim(),
      foreground: styles.getPropertyValue("--terminal-text").trim(),
    },
  });
  made.open(terminalScreen);
  made.onData((data) => post("terminal", { action: "input", data }));
  made.attachCustomKeyEventHandler((event) => {
    const keys = lineBreakKeys(event);
    if (keys === null) return true;
    if (event.type === "keydown") post("terminal", { action: "input", data: keys });
    return false;
  });
  emulator = made;
  return made;
}

/**
 * Measures a cell from the screen the emulator has drawn. Only a screen at the base font is
 * measured, and only once it has been drawn: the emulator's first measure, made as it opens,
 * was a quarter too narrow in a page still loading its styles.
 */
function measureTerminalCell(): void {
  const screen = terminalScreen.querySelector<HTMLElement>(".xterm-screen");
  if (!emulator || !screen || emulator.options.fontSize !== TERMINAL_FONT) return;
  if (!screen.clientWidth || !screen.clientHeight) return;
  terminalCell = {
    width: screen.clientWidth / emulator.cols, height: screen.clientHeight / emulator.rows,
  };
}

/** The size the drawer would give the pane at the base font, once a cell has been measured. */
function terminalViewSize(): TerminalSize | null {
  if (!terminalCell) return null;
  return viewSize(
    terminalScreen.clientWidth, terminalScreen.clientHeight,
    terminalCell.width, terminalCell.height,
  );
}

/** Tells the server the view's size, when it is known and differs from the last told. */
function stateTerminalSize(action: "open" | "resize"): void {
  const size = terminalViewSize();
  const key = size ? `${size.cols}x${size.rows}` : "";
  if (action === "resize" && (!size || key === statedSize)) return;
  statedSize = key;
  post("terminal", { action, ...(size ?? {}) });
}

/** Draws the pane at its own size, in a font at which its columns fit the drawer. */
function fitEmulator(): void {
  if (!emulator || !paneSize) return;
  const view = terminalViewSize();
  emulator.options.fontSize = fontToFit(TERMINAL_FONT, view?.cols ?? 0, paneSize.cols);
  if (emulator.cols !== paneSize.cols || emulator.rows !== paneSize.rows) {
    emulator.resize(paneSize.cols, paneSize.rows);
  }
}

function terminalResized(): void {
  fitTerminal();
  if (!terminalIsOpen() || !emulator) return;
  if (terminalResize !== undefined) clearTimeout(terminalResize);
  terminalResize = window.setTimeout(() => {
    terminalResize = undefined;
    fitEmulator();
    stateTerminalSize("resize");
  }, TERMINAL_RESIZE_MS);
}

interface TerminalFrame {
  name: string;
  event?: { cols?: number; rows?: number; data?: string; text?: string; error?: string } | null;
}

/** One event of the terminal stream, or the app's word that it was refused or lost. */
function terminalEvent(frame: TerminalFrame): void {
  const event = frame.event ?? {};
  if (frame.name === "ready") {
    showTerminalNote("");
    showEmulator(true);
  } else if (frame.name === "screen" && event.cols && event.rows && event.data !== undefined) {
    const terminal = theEmulator();
    paneSize = { cols: event.cols, rows: event.rows };
    fitEmulator();
    terminal.reset();
    terminal.write(bytesOf(event.data));
    // The cursor is nearly always at the foot of a pane too tall for the drawer.
    terminalScreen.scrollTop = terminalScreen.scrollHeight;
    // A cell can be measured once a screen is drawn, and the first measure says what fits.
    requestAnimationFrame(() => {
      measureTerminalCell();
      fitEmulator();
      stateTerminalSize("resize");
    });
  } else if (frame.name === "output" && event.data !== undefined) {
    emulator?.write(bytesOf(event.data));
  } else if (frame.name === "closed") {
    showTerminalNote(event.text ?? "The terminal has closed.");
  } else if (frame.name === "refused") {
    showTerminalNote(event.error ?? "The server refused the terminal.");
    showEmulator(false);
  } else if (frame.name === "note") {
    showTerminalNote(event.text ?? "");
  }
}

function characterWidth(): number {
  const context = document.createElement("canvas").getContext("2d")!;
  context.font = "100px ui-monospace, SFMono-Regular, Menlo, monospace";
  return context.measureText("M").width / 100;
}

const CHARACTER_WIDTH = characterWidth();

function fitTerminal(): void {
  const width = terminalText.clientWidth - TERMINAL_PADDING;
  if (width <= 0 || !terminalColumns) return;
  const size = Math.min(TERMINAL_MAX_FONT, width / (terminalColumns * CHARACTER_WIDTH));
  terminalText.style.fontSize = `${size.toFixed(2)}px`;
}

window.addEventListener("resize", terminalResized);

// A pane larger than the drawer is moved by the wheel before the emulator's history is, since
// the history is reached again once the pane's last line is in view.
terminalPane.addEventListener("wheel", (event) => {
  const room = terminalScreen.scrollHeight - terminalScreen.clientHeight;
  const before = terminalScreen.scrollTop;
  if (terminalScreen.hidden || room <= 0) return;
  terminalScreen.scrollTop = Math.max(0, Math.min(room, before + event.deltaY));
  terminalScreen.scrollLeft += event.deltaX;
  if (terminalScreen.scrollTop === before && !event.deltaX) return;
  event.preventDefault();
  event.stopPropagation();
}, { passive: false, capture: true });

// Wheel movement over the drawer stays in the drawer, including when it has nothing to scroll.
// The emulator scrolls its own history before this is reached.
terminalPane.addEventListener("wheel", (event) => {
  if (!terminalText.hidden) terminalText.scrollTop += event.deltaY;
  event.preventDefault();
  event.stopPropagation();
}, { passive: false });

document.getElementById("side-finished")!.addEventListener("click", () => {
  showFinishedTasks = !showFinishedTasks;
  drawSide();
});

document.getElementById("side-agents-finished")!.addEventListener("click", () => {
  showFinishedAgents = !showFinishedAgents;
  drawAgents();
});

document.getElementById("terminal-toggle")!.addEventListener("click", () => {
  // KYO-883: from another view the button opens Chat with its terminal shown.
  if (currentView !== "chat") {
    showView("chat");
    if (document.body.classList.contains("terminal-open")) return;
  }
  if (!document.body.classList.toggle("terminal-open")) {
    post("terminal", { action: "close" });
    return;
  }
  fitTerminal();
  const terminal = theEmulator();
  // The app opens the stream, and opens it again when the window turns to another session.
  stateTerminalSize("open");
  terminal.focus();
});

document.getElementById("sessions-more")!.addEventListener("click", () => {
  allSessions = !allSessions;
  drawMenu();
});

// The terminal goes on showing a reply after it has landed, and alternates between showing
// it and not while a tool runs, so the reply in progress is filtered before it is drawn.
const LIVE_HOLD_MS = 1200;
const LIVE_COMPARED_CHARS = 80;
const landedKeys: string[] = [];
let liveText: string | null = null;
let liveClear: number | undefined;

function lettersAndDigits(text: string): string {
  return text.replace(/[^\p{L}\p{N}]/gu, "");
}

function alreadyLanded(text: string): boolean {
  const head = lettersAndDigits(text).slice(0, LIVE_COMPARED_CHARS);
  return Boolean(head) && landedKeys.some((key) => key.startsWith(head));
}

function clearLive(): void {
  if (liveClear !== undefined) clearTimeout(liveClear);
  liveClear = undefined;
  liveText = null;
  keepingPlace(() => {
    live.innerHTML = "";
  });
}

function drawCommandOutput(output: CommandOutput): HTMLElement {
  const element = document.createElement("article");
  element.className = "command";
  const head = document.createElement("div");
  head.className = "command-name";
  head.textContent = [output.command, output.args].filter(Boolean).join(" ");
  const body = document.createElement("div");
  body.className = "reply";
  body.innerHTML = render(output.text);
  element.append(head, body);
  return element;
}

const kyo = {
  clear(): void {
    endAnchorMode();
    turnsLoaded = false;
    dropSentPreview();
    unseen = 0;
    // Another session's state is no change of this one's, so it does not bounce the Dock.
    waitingSeen = null;
    drawEmptyChat();
    lastMainActivity = "";
    chat.replaceChildren();
    elements.clear();
    drawChatLinksSoon();
    rendered.clear();
    calls.clear();
    diffs.clear();
    subagents.clear();
    openAgents.clear();
    agentCalls.clear();
    agentDispatches.clear();
    requestedAgents.clear();
    deliveries.length = 0;
    // The files mentioned are the session's; what the server said of each path still stands.
    mentions = [];
    drawMentions();
    landedKeys.length = 0;
    terminalColumns = 0;
    facts = null;
    latestAt = "";
    clearLive();
    drawSide();
  },

  image(frame: { key: string; data: string | null }): void {
    const waiting = document.querySelectorAll<HTMLImageElement>(`img[data-image="${frame.key}"]`);
    if (!frame.data) {
      // The server did not return the file, so the prompt names it in a chip.
      waiting.forEach((image) => {
        const chip = document.createElement("span");
        chip.className = "turn-anchor";
        chip.append(icon("file"), image.alt || "attachment");
        chip.title = image.title;
        image.replaceWith(chip);
      });
      return;
    }
    imageData.set(frame.key, frame.data);
    waiting.forEach((image) => {
      image.src = frame.data!;
      image.removeAttribute("data-image");
    });
  },

  terminal(frame: { terminal?: { text: string; working_line: string | null } | null }): void {
    workingLine = frame.terminal?.working_line ?? null;
    const text = frame.terminal?.text ?? "This session has no terminal to show.";
    terminalText.textContent = text;
    terminalColumns = Math.max(terminalColumns, ...text.split("\n").map((line) => line.length));
    fitTerminal();
    drawWorking();
  },

  terminalEvent,

  facts(frame: { facts?: Facts | null }): void {
    if (frame.facts) facts = frame.facts;
    drawSide();
  },

  menu(data: Menu): void {
    // A list the app read for the project the window has left is not this one's.
    if (data.project !== windowProject) return;
    menu = data;
    followFilesSession(data.current);
    // A held anchor's title says whether its session runs, which this list tells.
    for (const editor of editors()) refreshMarks(editor);
    drawMenu();
    drawEnded();
    drawStart();
    drawSide();
    // A subagent still running has more tool calls each time.
    for (const id of openAgents) {
      const agent = subagents.get(id);
      if (agent && !agentFinished(agent)) requestAgent(id);
    }
  },

  agent(data: {
    agent_id: string; tool_calls: ToolCall[];
    dispatch?: { added: number; removed: number } | null;
    usage?: { total: number; latest_turn?: number } | null;
    model?: string | null; effort?: string | null;
  }): void {
    agentCalls.set(data.agent_id, data.tool_calls);
    if (data.model || data.effort) {
      agentFacts.set(data.agent_id, { model: data.model ?? null, effort: data.effort ?? null });
    }
    // The latest turn's size, the figure the terminal's agent row shows; the sum counts cache reads per turn.
    if (data.usage) agentUsage.set(data.agent_id, data.usage.latest_turn ?? data.usage.total);
    if (data.dispatch) agentDispatches.set(data.agent_id, data.dispatch);
    drawAgents();
  },

  /** Command-F, when the app's menu takes the key before the page's own handler sees it. */
  find(): void {
    openSearch();
  },

  /** Quick Look in the image menu was chosen: the app is handed the image last right-clicked. */
  quickLook(): void {
    if (quickLookTarget) post("quicklook", quickLookTarget);
  },

  /** Download Image in the image menu: the app saves the image last right-clicked to Downloads. */
  saveImage(): void {
    if (quickLookTarget) post("quicklook", { ...quickLookTarget, save: true });
  },

  /**
   * Files dragged from the Finder that are not images or PDFs (KYO-910). The editor takes no
   * file's contents and WebKit inserts no path into it, so the app reads the paths from the
   * drag and hands them here with the point they were dropped at, in the page's coordinates.
   * They are inserted, one to a line, where they fell in the box under the point; a drop
   * anywhere else inserts nothing.
   */
  dropPaths(frame: { paths: string[]; x: number; y: number }): void {
    const under = document.elementFromPoint(frame.x, frame.y)?.closest<HTMLElement>("#boxes .cm-editor");
    const editor = under ? EditorView.findFromDOM(under) : null;
    if (!editor || !frame.paths.length) return;
    const at = editor.posAtCoords({ x: frame.x, y: frame.y }) ?? editor.state.selection.main.head;
    const text = frame.paths.join("\n");
    editor.focus();
    editor.dispatch({
      changes: { from: at, insert: text }, selection: { anchor: at + text.length },
      scrollIntoView: true, userEvent: "input.drop",
    });
  },

  /**
   * The composer's boxes as the page's tests read them (KYO-910): each box's kind, id, text and
   * selection, and whether it has the focus. `select` focuses a box and sets its selection.
   */
  composer: {
    boxes(): { kind: string; id: string; text: string; from: number; to: number; focused: boolean }[] {
      return editors().map((editor) => ({
        kind: scratchOf.has(editor) ? "scratch" : "prompt",
        id: boxIds.get(editor) ?? "",
        text: textOf(editor),
        from: editor.state.selection.main.from,
        to: editor.state.selection.main.to,
        focused: editor.hasFocus,
      }));
    },
    select(index: number, from: number, to = from): void {
      const editor = editors()[index];
      editor.focus();
      editor.dispatch({ selection: { anchor: from, head: to }, scrollIntoView: true });
    },
    /** The lines (counted from 0) of box `index` whose sections are folded (KYO-913). */
    folds(index: number): number[] {
      return foldedLines(editors()[index].state).map((fold) => fold.line);
    },
    /** Where position `pos` of box `index` is drawn, as the editor's `coordsAtPos` gives it. */
    coords(index: number, pos: number): { left: number; top: number } | null {
      const rect = editors()[index].coordsAtPos(pos, 1);
      return rect && { left: rect.left, top: rect.top };
    },
  },

  apply(batch: Batch): void {
    // A session's first read lands at its end, as before; after that, output is not followed.
    const loading = batch.reset || !turnsLoaded;
    const follow = loading && atBottom();
    countingNew = !loading;
    let prompted = false;
    keepingPlace(() => {
      if (batch.reset) kyo.clear();
      // KYO-883: the person's prompt is activity too: the request it starts reads the cache.
      for (const { at } of [...(batch.turns ?? []), ...(batch.replies ?? []), ...(batch.tool_calls ?? [])]) {
        if (at > lastMainActivity) lastMainActivity = at;
      }
      for (const turn of batch.turns ?? []) {
        const version = `${turn.text}|${turn.images?.length ?? 0}`;
        sources.set(`t:${turn.id}`, turn.text ?? "");
        if (!elements.has(`t:${turn.id}`)) {
          prompted = true;
          landSent();
        }
        place(`t:${turn.id}`, turn.at, version, () => drawTurn(turn));
        linkPlaced(`t:${turn.id}`);
      }
      for (const reply of batch.replies ?? []) {
        if (reply.text) landedKeys.push(lettersAndDigits(reply.text));
        // A record with only tool use has nothing to show.
        if (reply.text || reply.thinking) {
          const version = `${reply.text}|${reply.thinking ?? ""}`;
          sources.set(`r:${reply.message_id}`, reply.text ?? "");
          if (reply.uuid) recordKeys.set(reply.uuid, `r:${reply.message_id}`);
          place(`r:${reply.message_id}`, reply.at, version, () => drawReply(reply));
          linkPlaced(`r:${reply.message_id}`);
        }
      }
      for (const delivery of batch.deliveries ?? []) {
        deliveries.push(delivery);
        place(`d:${delivery.uuid}`, delivery.at, delivery.text ?? "", () => drawDelivery(delivery));
        linkPlaced(`d:${delivery.uuid}`);
      }
      for (const call of batch.tool_calls ?? []) {
        const version = `${call.result?.text ?? ""}${call.result?.is_error ?? ""}`;
        calls.set(call.call_id, call);
        place(`c:${call.call_id}`, call.at, version, () => drawToolCall(call));
      }
      for (const output of batch.command_outputs ?? []) {
        place(`o:${output.uuid}`, output.at, output.text, () => drawCommandOutput(output));
        linkPlaced(`o:${output.uuid}`);
      }
      for (const agent of batch.subagents ?? []) subagents.set(agent.agent_id, agent);
      arrange();
      drawChatLinksSoon();
      paintAnchors();
      refreshSearch();
    });
    countingNew = false;
    // The person's own prompt is shown when it lands, as it was when they sent it.
    if (follow || (prompted && !loading)) {
      unseen = 0;
      toEnd();
    }
    if (batch.facts) facts = batch.facts;
    // The final text has arrived, so the in-progress copy goes at once.
    if (liveText !== null && alreadyLanded(liveText)) clearLive();
    drawSide();
    for (const list of [batch.turns, batch.replies, batch.tool_calls, batch.command_outputs]) {
      for (const item of list ?? []) if (item.at > latestAt) latestAt = item.at;
    }
    turnsLoaded = true;
    drawEmptyChat();
    drawWorking();
    log(`items ${elements.size}`);
  },

  /** The reply being drawn on the terminal now, or none. A gap in the stream is held briefly. */
  live(frame: { text: string | null }): void {
    const text = frame.text;
    if (text && !runningCall() && !alreadyLanded(text)) {
      if (liveClear !== undefined) clearTimeout(liveClear);
      liveClear = undefined;
      liveText = text;
      keepingPlace(() => {
        live.innerHTML = render(text);
      });
    } else if (liveText !== null && liveClear === undefined) {
      liveClear = window.setTimeout(clearLive, LIVE_HOLD_MS);
    }
  },

  ready(frame: ScreenFrame & { reply_in_progress?: string | { text: string } | null }): void {
    const value = frame.reply_in_progress;
    kyo.live({ text: typeof value === "string" ? value : value?.text ?? null });
    showHeld((frame as { held?: Held | null }).held ?? null);
    kyo.facts(frame as { facts?: Facts | null });
    kyo.terminal(frame as { terminal?: { text: string; working_line: string | null } | null });
    kyo.screen(frame);
  },

  /**
   * A held message's state: `{"outcome": "held" | "delivered" | "retracted", "id", "held"}`.
   * `held` is the message as the server keeps it, with why it has not been delivered.
   */
  held(frame: { outcome?: string; id?: string; held?: Held | null }): void {
    // A prompt delivered is activity now, before its turn is read from the transcript.
    if (frame.outcome === "delivered") sentNow();
    if (sentPreview && frame.id && sentPreview.id === frame.id) {
      // Every prompt goes by the server's queue; the preview stands while it is delivered.
      if (frame.outcome === "delivered") confirmSentPreview();
      else if (frame.outcome !== "held" || waitsOnSomething(frame.held)) dropSentPreview();
    }
    showHeld(frame.outcome === "held" ? frame.held ?? (frame.id ? { id: frame.id } : null) : null);
    if (frame.outcome === "delivered") composerNote.textContent = "";
  },

  /** The server took a message: `held` is its id, or an error to show. */
  sent(frame: { id?: string; error?: string }): void {
    if (frame.error) {
      composerNote.textContent = frame.error;
      dropSentPreview();
      // A refusal ends the send, so the prompt stays and Send can be pressed again.
      sendGate.end();
      drawSending();
      switchWhenFree();
      return;
    }
    composerNote.textContent = "";
    // The server queues every prompt and answers with its id; the preview waits for the
    // queue's word that it was delivered (KYO-883). An answer with no id was delivered.
    if (sentPreview && frame.id) {
      sentPreview.id = frame.id;
      sentPreview.grace = window.setTimeout(heldAfterAll, PREVIEW_GRACE_MS);
    } else if (!frame.id) {
      confirmSentPreview();
      sentNow();
    }
    showHeld(frame.id ? { id: frame.id } : null);
    // Only the prompt that was sent leaves; the others are still drafts.
    sendGate.end()?.remove();
    drawSending();
    ensureMain();
    paintAnchors();
    remember();
    // KYO-798: the sent prompt leaves the saved draft at once and not after a pause, so that
    // it leaves the composer of every other device showing this draft.
    flushDraft();
    switchWhenFree();
  },

  /**
   * A held message taken back, to edit. The server returns the prompt's own marks with it
   * (KYO-809), so the box is rebuilt from them on this device as on the one that sent it.
   */
  retracted(frame: { text: string; prompt?: { marks?: SentMarkBody[] } | null }): void {
    retracting = false;
    showHeld(null);
    const marks = restoredMarks(frame.prompt?.marks ?? []);
    // KYO-903: the prompt is where it was sent from, so the one taken back is the prompt
    // again, to be edited and sent. What was typed in the prompt since becomes a scratchpad
    // above it, and nothing typed is lost.
    const others = currentBoxes().map((box) => (box.scratch ? box : { ...box, scratch: { heading: "" } }));
    const restored: NamedBox = { id: sentId ?? boxId(), text: frame.text, marks };
    sentId = null;
    const list = [...others, restored];
    showTexts(list.map((box) => box.text), list);
  },

  /**
   * The server's draft for the composer: the followed session's while it is live, and the
   * project's when no session is live (KYO-763), which is where a session's unsent prompts
   * go when it ends and what the next session takes. None yet is `etag: null`. `turned` says
   * the composer came here from another draft of the project, whose prompts are the ones on
   * the screen.
   */
  draft(frame: { boxes: SavedBox[]; etag: string | null; turned?: boolean }): void {
    // A server from before boxes had ids sends none, and a box is then named here.
    const saved = frame.boxes.filter((box) => box.text || box.marks?.length || box.scratch?.heading)
      .map((box) => ({ ...box, id: box.id || boxId() }));
    // Saves still unanswered were of another draft, or of this one before it was read.
    posted.clear();
    agreed = agreedOn([]);
    seenEtag = frame.etag;
    // The server's draft carries each prompt's marks, so a prompt opens the same on any device.
    const met = scope === null
      ? { boxes: saved, merged: false }
      : meetDraft(kept, scope, saved, frame.etag === null ? [] : currentBoxes());
    if (frame.etag === null) {
      // Nothing saved here yet. Prompts this window kept for the project become the draft.
      // Prompts shown from another draft are saved there already: they stay on the screen,
      // and are saved here only once they are edited, so that looking at another session
      // does not copy them into its draft.
      if (!frame.turned) {
        adoptShelf();
        remember();
      }
      return;
    }
    loadingDraft = true;
    showTexts(met.boxes.map((box) => box.text), met.boxes);
    loadingDraft = false;
    agreed = agreedWith(saved, new Set(saved.map((box) => box.id)));
    keepCurrent();
    adoptShelf();
    // Prompts only this device held are now part of the draft, and are saved to it. A prompt
    // of an older draft that became a scratchpad on being read (KYO-903) is not an edit: each
    // device turns it the same way, and it is saved as one with the next edit.
    if (met.merged) remember();
  },

  /**
   * The server acknowledged the save the page numbered `seq` (KYO-798): what it held is now
   * the draft this window and the server agree on, which a change made elsewhere is merged
   * against.
   */
  draftSaved(frame: { seq?: number }): void {
    const held = frame.seq === undefined ? undefined : posted.get(frame.seq);
    if (!held) return;
    for (const seq of [...posted.keys()]) if (seq <= frame.seq!) posted.delete(seq);
    agreed = agreedOn(held);
  },

  /**
   * The draft on the screen was saved from another device (KYO-798): the stream announced it
   * and the app read it, or the server refused this window's save because another came
   * first. Prompts not edited here follow the server's draft, so one sent or deleted elsewhere
   * leaves and one typed elsewhere appears. Prompts with edits the server has not
   * acknowledged keep them, the person is told, and the merged draft is saved.
   */
  draftElsewhere(frame: { boxes: SavedBox[]; etag: string | null }): void {
    const remote = frame.boxes.filter((box) => box.text || box.marks?.length || box.scratch?.heading)
      .map((box) => ({ ...box, id: box.id || boxId() }));
    const merged = mergeDraft(agreed, currentBoxes(), remote);
    seenEtag = frame.etag;
    applyBoxes(merged.boxes);
    agreed = agreedWith(remote, new Set(merged.boxes.filter((box) => remote.includes(box)).map((box) => box.id)));
    keepCurrent();
    const note = mergeNote(merged);
    if (note) composerNote.textContent = note;
    if (merged.save) remember();
  },

  /**
   * The app is about to read the composer's draft from another place, as it does when the
   * followed session ends: a save that was waiting for a pause is sent first, to the draft
   * the prompts were written under.
   */
  flushDraft(): void {
    flushDraft();
  },

  /**
   * The app is about to quit (KYO-782): the save that was waiting for a pause, which the app
   * then makes itself and waits for, or null when everything typed has been sent to it. The
   * answer is the call's own value, so the app holds the text before it lets the process end.
   */
  pendingDraft(): { boxes: SavedBox[] } | null {
    if (draftSave === undefined) return null;
    clearTimeout(draftSave);
    draftSave = undefined;
    return { boxes: currentBoxes() };
  },

  /**
   * The prompts the app kept when it last quit without the server's answer to their save
   * (KYO-782). They are newer than the server's draft, so they replace what it gave, and are
   * saved to it after the usual pause.
   */
  restoreDraft(frame: { boxes: SavedBox[] }): void {
    const boxes = frame.boxes.filter((box) => box.text || box.marks?.length);
    loadingDraft = true;
    showTexts(boxes.map((box) => box.text), boxes);
    loadingDraft = false;
    remember();
  },

  /**
   * The followed session's task list (KYO-771), or that there is none to show, or why it
   * cannot be read. A frame that answers a save says `saved`, and the field the save came
   * from is then done with; any other frame is a change made elsewhere, and leaves what the
   * person is typing where it is.
   */
  agenda(frame: AgendaFrame): void {
    const session = (given: AgendaFrame | null) => (given?.state === "shown" ? given.session_id : null);
    if (session(frame) !== session(agendaFrame)) {
      agendaEditing = null;
      agendaSaving = null;
      agendaAdd.value = "";
      agendaNote.hidden = true;
    }
    if (frame.state === "shown" && frame.saved) {
      if (agendaSaving?.kind === "add") agendaAdd.value = "";
      if (agendaSaving?.kind === "edit") agendaEditing = null;
      agendaSaving = null;
      agendaNote.hidden = true;
    }
    agendaFrame = frame;
    const editing = agendaEditing;
    if (editing && !agendaItemsNow().some((item) => item.id === editing.id && item.author === "person")) {
      // The item being edited was removed elsewhere. The words typed for it are kept.
      if (!agendaAdd.value) agendaAdd.value = editing.text;
      agendaEditing = null;
    }
    drawAgenda();
  },

  /** A save of the list the server refused, or that never reached it. What was typed stays. */
  agendaRefused(refusal: Refusal): void {
    agendaSaving = null;
    agendaNote.textContent = refusalText(refusal);
    agendaNote.hidden = false;
    drawAgenda();
  },

  /** The server's answer to a pasted file: its id and path, or why it was refused. */
  attached(frame: { ref: string; id?: string; path?: string; error?: string }): void {
    const upload = uploads.get(frame.ref);
    if (!upload) return;
    uploads.delete(frame.ref);
    const { editor, item } = upload;
    if (frame.error || !frame.id) {
      // The file leaves its paragraph, and that is no step of the box's history to undo.
      const marks = marksOf(editor);
      const line = marks.ids.findIndex((id) => marks.items.get(id)?.includes(item));
      if (line >= 0) {
        editor.dispatch({
          effects: removeEffect.of({ pos: editor.state.doc.line(line + 1).from, item }),
          annotations: Transaction.addToHistory.of(false),
        });
      }
      composerNote.textContent = frame.error ?? "The file could not be attached.";
    } else {
      item.id = frame.id;
      item.path = frame.path;
      delete item.pending;
    }
    remember();
    refreshMarks(editor);
  },

  /** The session's sent prompts' anchors, from the store, which the chat keeps showing. */
  marks(frame: { marks?: SentMark[] }): void {
    sentMarks_ = frame.marks ?? [];
    paintAnchors();
  },

  /** The tags in use across the store, for the tag field to suggest. */
  tags(frame: { tags?: { tag: string }[] }): void {
    storeTags = (frame.tags ?? []).map((row) => row.tag);
  },

  note(frame: { text: string }): void {
    composerNote.textContent = frame.text;
    // A message that could not be taken back is answered with a note.
    if (retracting) {
      retracting = false;
      switchWhenFree();
    }
  },

  /**
   * The project the window shows, sent before any other frame of it (KYO-761); the empty
   * string is no project, and the list of them is then shown in place of the chat. `scope`
   * names the server and the project. Everything the page held of the project it showed
   * goes, and the composer takes the prompts that were left unsent in this one.
   */
  project(frame: { project?: string; scope?: string }): void {
    endAnchorMode();
    resetHistory();
    const leaving = scope;
    windowProject = frame.project ?? "";
    scope = frame.scope ?? windowProject;
    document.body.classList.toggle("no-project", !windowProject);
    if (scope !== leaving) {
      closeProjectList();
      // A save that was waiting is the project's that is left. The page sends it before it
      // asks for another project; when the app turned the window by itself there is no
      // session left to save it to, and the prompts are kept here all the same.
      if (draftSave !== undefined) clearTimeout(draftSave);
      draftSave = undefined;
      if (leaving !== null) keepBoxes(kept, leaving, currentBoxes());
      wanted = null;
      retracting = false;
      sendGate.end();
      coldConfirm = null;
      uploads.clear();
      hideMark();
      // Shown without a save: the server's draft for the session replaces these when the
      // session opens, and a save made before then would be of the wrong prompts.
      loadingDraft = true;
      const boxes = keptBoxes<SavedBox | string>(kept, scope, windowProject !== "")
        .map((box) => (typeof box === "string" ? { text: box } : box))
        .filter((box) => box.text || box.marks?.length);
      showTexts(boxes.map((box) => box.text), boxes);
      loadingDraft = false;
      drawSending();
    }
    kyo.clear();
    menu = null;
    startFrame = null;
    allSessions = false;
    document.getElementById("sessions")!.replaceChildren();
    document.getElementById("sessions-more")!.hidden = true;
    chatPlace = null;
    sentMarks_ = [];
    storeTags = [];
    paintAnchors();
    composerNote.textContent = "";
    showHeld(null);
    followFilesSession(null);
    // The terminal drawer shows the session's pane, which the app opens again for the next.
    emulator?.reset();
    showTerminalNote("");
    showEmulator(false);
    kyo.terminal({ terminal: null });
    kyo.screen({ screen: null });
    drawEnded();
    drawStart();
    drawSide();
    drawProjects();
  },

  /** The server's projects and their live sessions, sent when any of it changes (KYO-761). */
  projects(frame: ProjectsFrame): void {
    projectsFrame = frame;
    drawProjects();
  },

  /** File > Projects…: the list of projects, or back to the project the window was showing. */
  projectList(): void {
    toggleProjectList();
  },

  /** What a press of Start orchestrator came to (KYO-719). */
  start(frame: StartFrame): void {
    startFrame = frame;
    drawStart();
  },

  screen(frame: ScreenFrame): void {
    const state = frame.screen?.state ?? null;
    const changed = state !== screenState;
    screenState = state;
    background = frame.screen?.background ?? null;
    if (changed) keepingPlace(arrange);
    document.getElementById("interrupt")!.hidden = state !== "working" || sessionEnded;
    if (held) showHeld(held);
    drawWorking();
    drawSide();
  },
};

// ------------------------------------------------------------------------------ the composer

/**
 * KYO-910: each box's text is an editor built by `editor.ts` on CodeMirror 6, whose document is
 * the text as typed, which is what is saved and sent. The marks of its lines, and the history
 * that undoes their placing among the edits of the text, are kept in the editor's state. What
 * the page keeps of a box beside it is in the maps below, keyed by the box's `EditorView`.
 */
const pasted = new WeakMap<EditorView, string[]>();
const boxes = document.getElementById("boxes")!;
const composerNote = document.getElementById("composer-note")!;

/** The composer's editors, in the order of their boxes. */
function editors(): EditorView[] {
  return Array.from(boxes.querySelectorAll<HTMLElement>(":scope > .box .cm-editor"))
    .flatMap((element) => EditorView.findFromDOM(element) ?? []);
}

/** The text of a box, as typed. */
function textOf(editor: EditorView): string {
  return editor.state.doc.toString();
}

/** The box an editor stands in. */
function boxOf(editor: EditorView): HTMLElement {
  return editor.dom.closest<HTMLElement>(".box")!;
}

// A box taken out of the composer for good takes its editor with it: the editor's listeners on
// the window and the document go. A box moved within the composer is back before this runs.
new MutationObserver((records) => {
  for (const record of records) {
    for (const node of record.removedNodes) {
      if (!(node instanceof HTMLElement) || node.isConnected) continue;
      const element = node.querySelector<HTMLElement>(".cm-editor");
      if (element) EditorView.findFromDOM(element)?.destroy();
    }
  }
}).observe(boxes, { childList: true });

/** A button inside a box. It leaves the focus, and so the caret, in the text. */
function boxButton(className: string, label: string, act: () => void): HTMLButtonElement {
  const button = document.createElement("button");
  button.className = className;
  button.textContent = label;
  button.addEventListener("mousedown", (event) => event.preventDefault());
  button.addEventListener("click", act);
  return button;
}

// ---- marks: the gutter [+] beside a paragraph, which holds what is attached to it

/** One thing a mark holds. Attachments join these when they are built. */
interface TagItem { kind: "tag"; name: string }
/**
 * A place in the chat: the prompt or reply by its key, and a range of its text counted in
 * code points, as the store counts a span. `words` is what the chip shows.
 */
interface AnchorItem {
  kind: "anchor"; key: string; session: string; start: number; end: number; words: string;
  /** The range is the words that were selected, not the whole of the blocks they lie in. */
  exact?: boolean;
}
/** A file pasted into a paragraph, kept on the machine the session runs on. */
interface FileItem { kind: "file"; id: string; name: string; path?: string; thumb?: string; pending?: string }
/** KYO-737: a record or lines of a file are held as the places `marks.ts` describes. */
type Item = TagItem | AnchorItem | FileItem | Place;
/**
 * One prompt as it is saved. `id` is the box's own, shared by every device and the server
 * (KYO-798); a prompt kept before boxes had one has none until it is shown.
 */
interface SavedBox { id?: string; text: string; marks?: { line: number; items: Item[] }[]; scratch?: Scratch | null }
type NamedBox = SavedBox & DraftBox;

/** What the lines of a box hold, from its editor's state (KYO-910). */
function marksOf(editor: EditorView): LineMarks<Item> {
  return lineMarks<Item>(editor.state);
}

/** Each box's id (KYO-798), which it keeps for as long as it is a draft on any device. */
const boxIds = new WeakMap<EditorView, string>();
/**
 * KYO-913: the lines folded in each box, by the box's id, so that a box drawn again (by a
 * merge that changed it, or by Send to prompt) folds them again. Folds are this window's and
 * are not saved in the draft: the draft's shape, and its merge between devices, are unchanged.
 */
const foldsById = new Map<string, FoldedLine[]>();
let openMark: { editor: EditorView; id: number } | null = null;
const markPanel = document.createElement("div");
markPanel.className = "mark-panel";

const TAGS_KEY = "composer-tags";
let storeTags: string[] = [];

/** Tags to suggest: those on the prompts now, then those used on this device, newest first. */
function knownTags(): string[] {
  const here = editors().flatMap((editor) => Array.from(marksOf(editor).items.values()).flat())
    .flatMap((item) => (item.kind === "tag" ? [item.name] : []));
  return Array.from(new Set([...here, ...stored<string[]>(TAGS_KEY, []), ...storeTags]));
}

/** The paragraph the caret is in, or was in when the box was last used. */
function caretLine(editor: EditorView): number {
  return editor.state.doc.lineAt(editor.state.selection.main.head).number - 1;
}

/** Puts the caret at `at` in a box, focused, and shows it. */
function caretTo(editor: EditorView, at: number): void {
  editor.focus();
  editor.dispatch({ selection: { anchor: Math.min(at, editor.state.doc.length) }, scrollIntoView: true });
}

/**
 * The caret's line is shown by the editor, which scrolls each scrolling box around it by the
 * least that shows the line. A scratchpad's line is shown above the prompt, which covers the
 * foot of the column, and on the last line of a box the box's buttons are shown too.
 */
const showAbovePrompt = EditorView.scrollMargins.of((editor) => {
  if (!scratchOf.has(editor)) return null;
  const prompt = mainBox()?.getBoundingClientRect();
  const column = boxes.getBoundingClientRect();
  let bottom = prompt ? Math.max(0, column.bottom - prompt.top) : 0;
  const { state } = editor;
  if (state.doc.lineAt(state.selection.main.head).number === state.doc.lines) {
    bottom += Math.max(0, boxOf(editor).getBoundingClientRect().bottom - editor.dom.getBoundingClientRect().bottom);
  }
  return { bottom };
});

/** The icon of one thing a mark holds: a record's and a file's are their views'. */
/** Whether the project's session list shows a session as running; false before it is known. */
function sessionRuns(session: string): boolean {
  return menu?.sessions.sessions.find((row) => row.session_id === session)?.live ?? false;
}

function itemIcon(item: Item): keyof typeof ICONS {
  return item.kind === "record" ? "records" : item.kind === "path" ? "page" : item.kind;
}

/** What a paragraph's mark holds, one line for each thing: its title, and a fold's (KYO-913). */
function markTitle(items: Item[]): string {
  return items.map((item) => (item.kind === "tag" ? `#${item.name}`
    : item.kind === "file" ? `attached: ${item.name}`
    : item.kind === "anchor" && !anchorOpens(item.session, menu?.current)
      ? `anchor: ${item.words} (${sessionRuns(item.session) ? "another session" : "session ended"})`
    : `anchor: ${item.words}`)).join("\n");
}

/** A paragraph's mark in the gutter: one thing shows as what it is, and several as their number. */
function drawMark(editor: EditorView, id: number, items: Item[]): HTMLElement {
  const mark = boxButton("mark held", items.length > 1 ? String(items.length) : "", () => showMark(editor, id));
  if (items.length === 1) mark.append(icon(itemIcon(items[0])));
  mark.title = markTitle(items);
  return mark;
}

/** The ghost [+] on the paragraph under the pointer that holds nothing yet. */
function drawGhost(editor: EditorView, id: number): HTMLElement {
  const ghost = boxButton("mark ghost", "+", () => showMark(editor, id));
  ghost.title = "Attach to this paragraph";
  return ghost;
}

/**
 * What follows an update of a box: its scratchpad's heading, the composer's buttons, the chat's
 * highlights and the open panel follow its marks, and an edit of its text or marks is saved.
 */
function boxUpdated(update: ViewUpdate): void {
  const editor = update.view;
  if (update.docChanged) nameScratch(editor);
  const before = lineMarks<Item>(update.startState);
  const after = marksOf(editor);
  if (before !== after) {
    syncCreate();
    paintAnchors();
    if (openMark?.editor === editor) showMark(editor, openMark.id);
  }
  if (update.docChanged || before.items !== after.items) remember();
}

/**
 * While no box of the composer has the caret, an arrow in the gutter marks the paragraph an
 * anchor or an attachment made now would land on: the caret's paragraph of the box last
 * typed in, as `showAnchorMenu` chooses it. The editor draws it in the fold gutter, beside
 * whatever mark that paragraph holds and no longer over it (KYO-913), since without the caret
 * nothing else says where the next one goes.
 */
function drawLanding(): void {
  const all = editors();
  const active = document.activeElement;
  const typing = active instanceof Element && Boolean(active.closest("#boxes .cm-editor"));
  const target = typing ? null : lastEditor && all.includes(lastEditor) ? lastEditor : all[all.length - 1];
  for (const editor of all) showLanding(editor, editor === target);
}
// The focus has moved by the time a timer runs, which `focusout` alone does not promise.
document.addEventListener("focusin", () => drawLanding());
document.addEventListener("focusout", () => window.setTimeout(drawLanding, 0));

function hideMark(): void {
  openMark = null;
  markPanel.remove();
}

/** The list of what one mark holds, each removable, and a field that adds a tag. */
function showMark(editor: EditorView, id: number): void {
  const marks = marksOf(editor);
  const line = marks.ids.indexOf(id);
  if (line < 0) {
    hideMark();
    return;
  }
  const refocus = openMark?.editor === editor && openMark.id === id
    && markPanel.contains(document.activeElement);
  openMark = { editor, id };
  // Adding and removing are steps of the box's own history, as placing an anchor is (KYO-910).
  const lineNow = () => lineOfId(editor.state, id);
  const held = marks.items.get(id) ?? [];
  const chips = held.map((item) => {
    const chip = document.createElement("span");
    chip.className = "mark-item";
    if (item.kind === "tag") {
      chip.textContent = `#${item.name}`;
    } else if (item.kind === "file") {
      if (item.thumb) {
        const thumb = document.createElement("img");
        thumb.className = "mark-thumb";
        thumb.src = item.thumb;
        // Quick Look shows the whole image, which the server holds once it is uploaded.
        if (!item.pending) thumb.dataset.attachment = item.id;
        thumb.alt = item.name;
        chip.append(thumb);
      } else {
        chip.append(icon("file"));
      }
      chip.append(item.pending ? `${item.name} (uploading)` : item.name);
    } else if (item.kind === "anchor" && !anchorOpens(item.session, menu?.current)) {
      // KYO-763: an anchor carried here from another session has no place in this chat. It
      // is still sent as its line of words, and its chip takes no press.
      const place = boxButton("mark-anchor gone", item.words, () => {});
      place.disabled = true;
      place.prepend(icon("anchor"));
      chip.title = anchorGoneNote(sessionRuns(item.session));
      chip.classList.add("gone");
      chip.append(place);
    } else if (item.kind === "anchor") {
      // An anchor's chip goes to the place it names.
      const place = boxButton("mark-anchor", item.words, () => reveal(item));
      place.prepend(icon("anchor"));
      chip.append(place);
    } else {
      const place = boxButton("mark-anchor", item.words, () => revealPlace(item));
      place.prepend(icon(itemIcon(item)));
      chip.append(place);
    }
    chip.append(boxButton("mark-remove", "×", () => removeItem(editor, lineNow(), item)));
    return chip;
  });
  const field = document.createElement("input");
  field.placeholder = "Add a tag…";
  // The suggestions are drawn in the panel: the system's own list for a field opened in
  // the wrong place in the app's web view.
  const suggestions = document.createElement("div");
  suggestions.className = "tag-suggestions";
  const addTag = (name: string) => {
    store(TAGS_KEY, [name, ...stored<string[]>(TAGS_KEY, []).filter((used) => used !== name)].slice(0, 200));
    if (!held.some((item) => item.kind === "tag" && item.name === name)) placeItem<Item>(editor, lineNow(), { kind: "tag", name });
  };
  const suggest = () => {
    const typed = field.value.trim().replace(/^#/, "").toLowerCase();
    const seen = new Set(held.flatMap((item) => (item.kind === "tag" ? [item.name.toLowerCase()] : [])));
    const names = knownTags().filter((name) => {
      const lower = name.toLowerCase();
      if (seen.has(lower) || !lower.includes(typed)) return false;
      seen.add(lower);
      return true;
    }).slice(0, 8);
    suggestions.replaceChildren(...names.map((name) => boxButton("tag-suggestion", `#${name}`, () => addTag(name))));
  };
  field.addEventListener("input", suggest);
  field.addEventListener("keydown", (event) => {
    event.stopPropagation();
    if (event.key === "Escape") {
      hideMark();
      editor.focus();
    }
    const name = field.value.trim().replace(/^#/, "");
    if (event.key !== "Enter" || !name) return;
    event.preventDefault();
    addTag(name);
  });
  // KYO-823: the reverse of a right-click, a place in the chat chosen for this paragraph.
  const anchorButton = boxButton("mark-anchor-mode", "Anchor", () => startAnchorMode(editor, id));
  anchorButton.prepend(icon("anchor"));
  const divider = document.createElement("div");
  divider.className = "mark-divider";
  const actions: HTMLElement[] = [anchorButton];
  if (scratchOf.has(editor)) {
    // KYO-904: a scratchpad's paragraph, with what is nested under it, goes to the prompt.
    const move = boxButton("mark-to-prompt", "Send to prompt", () => sendToPrompt(editor, lineNow()));
    move.title = "Move this paragraph, with what is nested under it, to the end of the prompt";
    actions.push(move);
  }
  markPanel.replaceChildren(...chips, ...actions, divider, field, suggestions);
  suggest();
  // Below its paragraph's first row, in the box, which the panel is placed in.
  const box = boxOf(editor);
  const row = editor.lineBlockAt(editor.state.doc.line(line + 1).from);
  box.append(markPanel);
  markPanel.style.top = `${editor.documentTop + row.top - box.getBoundingClientRect().top - box.clientTop + 24}px`;
  if (refocus || !held.length) field.focus();
}

document.addEventListener("mousedown", (event) => {
  const target = event.target as HTMLElement;
  if (openMark && !markPanel.contains(target) && !target.closest(".mark")) hideMark();
  if (!anchorMenu.contains(target)) anchorMenu.remove();
});

// ---- anchors: a right-click in the chat attaches that place to a paragraph of a prompt

const ANCHOR_WORDS = 6;
const anchorMenu = document.createElement("div");
anchorMenu.id = "anchor-menu";
/** The prompt last typed in, which a new anchor goes to. */
let lastEditor: EditorView | null = null;

function firstWords(text: string, count: number): string {
  const words = text.trim().split(/\s+/);
  return words.slice(0, count).join(" ") + (words.length > count ? "…" : "");
}

/** A block's drawn text, and the text nodes that hold it. A reply's thinking is no part of it. */
interface Drawn { text: string; nodes: { node: Text; from: number }[] }

function drawnOf(block: HTMLElement): Drawn {
  const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
  const nodes: Drawn["nodes"] = [];
  let text = "";
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.parentElement?.closest(".thinking, .file-gutter")) continue;
    nodes.push({ node: node as Text, from: text.length });
    text += node.nodeValue ?? "";
  }
  return { text, nodes };
}

/** Where a place in the page lies in a block's drawn text. */
function drawnOffset(drawn: Drawn, node: Node, offset: number): number {
  const own = drawn.nodes.find((held) => held.node === node);
  if (own) return own.from + Math.min(offset, own.node.length);
  const point = document.createRange();
  point.setStart(node, offset);
  point.collapse(true);
  const next = drawn.nodes.find((held) => point.comparePoint(held.node, 0) >= 0);
  return next ? next.from : drawn.text.length;
}

/**
 * The part of a prompt's or reply's source that a block was drawn from, and where that part
 * begins. A block is stamped with its lines; the prompt or reply itself is the whole source.
 */
function sourceOf(block: HTMLElement, source: string): { base: number; text: string } {
  if (block.dataset.sourceLine === undefined) return { base: 0, text: source };
  const lines = source.split("\n");
  const offset = (line: number) => lines.slice(0, line).reduce((sum, text) => sum + text.length + 1, 0);
  const base = offset(Number(block.dataset.sourceLine));
  return { base, text: source.slice(base, Math.min(offset(Number(block.dataset.sourceEnd)), source.length)) };
}

/** The place in the page's text under a point of the window. */
function caretAt(x: number, y: number): { node: Node; offset: number } | null {
  const range = (document as any).caretRangeFromPoint?.(x, y) as Range | null | undefined;
  if (range) return { node: range.startContainer, offset: range.startOffset };
  const position = (document as any).caretPositionFromPoint?.(x, y);
  return position ? { node: position.offsetNode, offset: position.offset } : null;
}

/**
 * The text range a right-click names (KYO-729). A selection the person made is named as
 * selected, at the place it was made and not where the same words first occur. With no such
 * selection it is the sentence under the pointer. Text that reads differently from its source,
 * and a click outside any sentence, name the blocks they lie in.
 */
function spanAt(
  holder: HTMLElement, key: string, target: HTMLElement, pointer: { x: number; y: number }, own: boolean,
): Omit<AnchorItem, "kind" | "key" | "session"> | null {
  if (target.closest(".thinking")) return null;
  const source = sources.get(key)!;
  const points = (index: number) => Array.from(source.slice(0, index)).length;
  const span = (from: number, to: number, words: string, exact = false) =>
    ({ start: points(from), end: points(to), words: firstWords(words, ANCHOR_WORDS), exact });
  const selection = window.getSelection();
  const selected = own && selection && !selection.isCollapsed
    && holder.contains(selection.anchorNode) && holder.contains(selection.focusNode);
  const block = (node: Node | null) =>
    (node instanceof HTMLElement ? node : node?.parentElement)?.closest<HTMLElement>("[data-source-line]");
  /** The block a place is in, or the whole prompt or reply where it is in none. */
  const around = (node: Node | null) => {
    const found = block(node);
    return found && holder.contains(found) ? found : holder;
  };
  const picked = selected ? selection.toString().trim() : "";
  const range = selected ? selection.getRangeAt(0) : null;
  if (picked && range) {
    const from = around(range.startContainer);
    const part = sourceOf(from, source);
    const drawn = drawnOf(from);
    const at = drawnOffset(drawn, range.startContainer, range.startOffset);
    const found = nearest(source, picked, part.base + sourceIndex(part.text, drawn.text, at));
    if (found >= 0) return span(found, found + picked.length, picked, true);
  }
  const caret = selected || target.closest(".turn-anchor") ? null : caretAt(pointer.x, pointer.y);
  const under = caret && holder.contains(caret.node) ? around(caret.node) : null;
  // A prompt has no blocks and is measured whole; a reply outside its blocks is not.
  if (caret && under && (under !== holder || key.startsWith("t:"))) {
    const part = sourceOf(under, source);
    const drawn = drawnOf(under);
    const [from, to] = sentenceAt(drawn.text, drawnOffset(drawn, caret.node, caret.offset));
    const found = to > from ? sourceSpan(part.text, drawn.text, from, to) : null;
    if (found) return span(part.base + found[0], part.base + found[1], drawn.text.slice(from, to), true);
  }
  // Formatted text reads differently from its source, so the blocks it lies in are named.
  const first = range ? block(range.startContainer) : block(target);
  const last = range ? block(range.endContainer) : first;
  if (!first || !last || !holder.contains(first)) {
    return source.trim() ? span(0, source.length, picked || source) : null;
  }
  const lines = source.split("\n");
  const offset = (line: number) => lines.slice(0, line).reduce((sum, text) => sum + text.length + 1, 0);
  const from = offset(Number(first.dataset.sourceLine));
  const to = Math.min(offset(Number(last.dataset.sourceEnd)), source.length);
  const text = source.slice(from, to).trimEnd();
  return span(from, from + text.length, picked || first.textContent || text);
}

/**
 * Puts an anchor on a paragraph of a prompt: the one named, or the one the caret was in when
 * the box was left, which the editor keeps while the focus is elsewhere. The text is not
 * changed, so an empty paragraph takes the anchor as it is. The placing is a step of the box's
 * own history, so Command-Z takes it back first (KYO-780, KYO-910).
 */
function addAnchor(editor: EditorView, line: number | null, item: AnchorItem | Place): void {
  const marks = marksOf(editor);
  const at = line ?? caretLine(editor);
  const held = marks.items.get(marks.ids[at]) ?? [];
  const same = (other: Item) => (item.kind === "anchor"
    ? other.kind === "anchor" && other.key === item.key && other.start === item.start && other.end === item.end
    : (other.kind === "record" || other.kind === "path") && samePlace(other, item));
  if (!held.some(same)) placeItem<Item>(editor, at, item);
  // Typing goes on where it was.
  editor.focus();
}

function showAnchorMenu(x: number, y: number, item: AnchorItem | Place): void {
  const all = editors();
  const editor = lastEditor && all.includes(lastEditor) ? lastEditor : all[all.length - 1];
  const entry = (label: string, line: number | null, className = "") => {
    const button = boxButton(className, label, () => {
      anchorMenu.remove();
      addAnchor(editor, line, item);
    });
    return button;
  };
  // The first entry is the caret's paragraph, so the list names the others.
  const caret = caretLine(editor);
  const lines = textOf(editor).split("\n")
    .map((text, line) => ({ text, line }))
    .filter(({ text, line }) => text.trim() && line !== caret);
  const heading = document.createElement("div");
  heading.className = "anchor-heading";
  heading.textContent = "Anchor to";
  anchorMenu.replaceChildren(
    entry("Anchor", null, "anchor-new"),
    ...(lines.length ? [heading] : []),
    ...lines.map(({ text, line }) => entry(firstWords(text, 3), line)),
  );
  document.body.append(anchorMenu);
  anchorMenu.style.left = `${Math.min(x, window.innerWidth - anchorMenu.offsetWidth - 8)}px`;
  anchorMenu.style.top = `${Math.min(y, window.innerHeight - anchorMenu.offsetHeight - 8)}px`;
}

/**
 * The selection as the press that opens the menu found it. WebKit selects the word under a
 * secondary click before the menu's event, and that word is not a selection the person made.
 */
let pressedSelection: Range | null = null;
document.addEventListener("mousedown", () => {
  const selection = window.getSelection();
  pressedSelection = selection && !selection.isCollapsed && selection.rangeCount
    ? selection.getRangeAt(0).cloneRange() : null;
}, true);

/** Whether the selection now is the one the press found, and so the person's own. */
function ownSelection(): boolean {
  const selection = window.getSelection();
  if (!pressedSelection || !selection || selection.isCollapsed || !selection.rangeCount) return false;
  const now = selection.getRangeAt(0);
  return now.compareBoundaryPoints(Range.START_TO_START, pressedSelection) === 0
    && now.compareBoundaryPoints(Range.END_TO_END, pressedSelection) === 0;
}

chat.addEventListener("contextmenu", (event) => {
  const target = event.target as HTMLElement;
  // An image keeps WebKit's own menu, where the app adds Quick Look (KYO-883).
  if (target instanceof HTMLImageElement) return;
  const holder = target.closest<HTMLElement>("#chat > [data-key]");
  const key = holder?.dataset.key;
  const session = menu?.current;
  if (!holder || !key || !sources.has(key) || !session) return;
  const own = ownSelection();
  const span = spanAt(holder, key, target, { x: event.clientX, y: event.clientY }, own);
  // The word the click itself selected goes, or the next click would find it as the person's.
  if (!own) window.getSelection()?.removeAllRanges();
  if (!span) return;
  event.preventDefault();
  showAnchorMenu(event.clientX, event.clientY, { kind: "anchor", key, session, ...span });
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") anchorMenu.remove();
});

// ---- anchor mode (KYO-823): from a paragraph's + panel, the next press on a place in the
// chat, an open record or an open file anchors it to that paragraph, as a right-click would.

let anchorMode: { editor: EditorView; id: number } | null = null;
let anchorLit: Element[] = [];
/** Set by a press made in the mode, whose click must not also act on what it fell on. */
let anchorSwallow = false;
let anchorHoverFrame = 0;

/**
 * The pointer in the mode: a solid anchor, ringed in white so it shows on light and dark
 * alike, and grey over a place a press would not anchor (the maintainer, 2026-11-11).
 */
function anchorCursor(colour: string): string {
  const shape = (paint: string, width: number) =>
    `<g fill='none' stroke='${paint}' stroke-width='${width}' stroke-linecap='round' stroke-linejoin='round'>`
    + `<circle cx='12' cy='5' r='2.2'/><path d='M12 7.2V21M7.5 11h9M4.5 13.5a7.5 7.5 0 0 0 15 0'/></g>`
    + `<g fill='${paint}'><path d='M2.6 12.2l2-2.4 2 2.4z'/><path d='M17.4 12.2l2-2.4 2 2.4z'/></g>`;
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='26' height='26' viewBox='-1 -1 26 26'>`
    + `${shape("white", 5.4)}${shape(colour, 2.8)}</svg>`;
  return `url("data:image/svg+xml;utf8,${encodeURIComponent(svg)}") 13 13, crosshair`;
}

function startAnchorMode(editor: EditorView, id: number): void {
  hideMark();
  anchorMode = { editor, id };
  document.body.style.setProperty("--anchor-cursor", anchorCursor("#1d1d1f"));
  document.body.style.setProperty("--anchor-cursor-off", anchorCursor("#a3a3a3"));
  document.body.classList.add("anchor-mode");
}

function clearAnchorHover(): void {
  showRanges("anchor-preview", []);
  for (const element of anchorLit) element.classList.remove("anchor-lit");
  anchorLit = [];
  document.body.classList.remove("anchor-off");
}

function endAnchorMode(): void {
  if (!anchorMode) return;
  anchorMode = null;
  clearAnchorHover();
  document.body.classList.remove("anchor-mode");
}

/**
 * What a press at `point` on `target` would anchor, found as a right-click finds it, and what
 * shows it: a range of a prompt or reply, or the record's part or the file's block. Null for
 * a place that cannot be anchored.
 */
function anchorPlaceAt(
  target: Element, point: { x: number; y: number }, own: boolean,
): { item: AnchorItem | Place; range: Range | null; elements: Element[] } | null {
  const holder = target.closest<HTMLElement>("#chat > [data-key]");
  if (holder && chat.contains(holder)) {
    const key = holder.dataset.key;
    const session = menu?.current;
    if (!key || !sources.has(key) || !session) return null;
    // Between a reply's blocks a right-click takes the whole reply; a press in the mode
    // takes words, so that space is not a place to anchor.
    if (!own && key.startsWith("r:") && !target.closest("[data-source-line]")) return null;
    const span = spanAt(holder, key, target as HTMLElement, point, own);
    if (!span) return null;
    const item: AnchorItem = { kind: "anchor", key, session, ...span };
    const range = wordsRange(item);
    // Where the words are not drawn as they are written, the blocks they lie in are lit,
    // as a sent anchor's are: never more than the press would take.
    return { item, range, elements: range ? [] : anchoredBlocks(item) };
  }
  if (recordArticle.contains(target)) {
    // A press outside the record's parts, on its metadata or summary, takes the whole record.
    const part = target.closest<HTMLElement>("[data-record-ref]");
    const head = recordArticle.querySelector<HTMLElement>(".record-head[data-record-ref]");
    const element = part ?? head;
    const id = element?.dataset.recordRef;
    if (!element || !id) return null;
    if (!part) {
      const title = recordArticle.querySelector("h1")?.textContent ?? "";
      const words = title.trim() ? `${id} ${firstWords(title, ANCHOR_WORDS)}` : id;
      return { item: { kind: "record", id, words }, range: null, elements: [recordArticle] };
    }
    const text = element.classList.contains("record-head")
      ? recordArticle.querySelector("h1")?.textContent ?? ""
      : element.querySelector(".rec-text")?.textContent ?? "";
    const words = text.trim() ? `${id} ${firstWords(text, ANCHOR_WORDS)}` : id;
    return { item: { kind: "record", id, words }, range: null, elements: [element] };
  }
  const file = openFile;
  if (file && fileArticle.contains(target)) {
    const place = filePlaceAt(file, target, point, own);
    if (!place) return null;
    const session = isPerSession(fileRoots, file.root) ? filesSession : null;
    const element = target.closest("[data-source-line], .file-line, p, li, pre, tr, h1, h2, h3, h4, blockquote")
      ?? target;
    return {
      item: { kind: "path", root: file.root, path: file.path, ...place, ...(session ? { session } : {}) },
      range: null, elements: [element],
    };
  }
  return null;
}

document.addEventListener("mousemove", (event) => {
  if (!anchorMode || !(event.target instanceof Element)) return;
  const target = event.target;
  const point = { x: event.clientX, y: event.clientY };
  cancelAnimationFrame(anchorHoverFrame);
  anchorHoverFrame = requestAnimationFrame(() => {
    if (!anchorMode) return;
    const found = anchorPlaceAt(target, point, false);
    clearAnchorHover();
    if (found?.range) showRanges("anchor-preview", [found.range], 2);
    else if (found?.elements.length) {
      anchorLit = found.elements;
      for (const element of anchorLit) element.classList.add("anchor-lit");
    } else if (!found) {
      document.body.classList.add("anchor-off");
    }
  });
}, { passive: true });

document.addEventListener("mousedown", (event) => {
  if (!anchorMode || event.button !== 0) return;
  anchorSwallow = true;
  // A press inside a selection the person made keeps it, to be anchored as it is.
  const selection = window.getSelection();
  if (selection && !selection.isCollapsed && selection.rangeCount) {
    const inside = Array.from(selection.getRangeAt(0).getClientRects()).some((rect) =>
      event.clientX >= rect.left && event.clientX <= rect.right
      && event.clientY >= rect.top && event.clientY <= rect.bottom);
    if (inside) event.preventDefault();
  }
}, true);

document.addEventListener("mouseup", (event) => {
  if (!anchorMode || event.button !== 0 || !(event.target instanceof Element)) return;
  // A press on a paragraph's + ends the mode, and opens that panel as a press would.
  if (event.target.closest(".mark")) {
    anchorSwallow = false;
    endAnchorMode();
    return;
  }
  const selection = window.getSelection();
  const own = !!selection && !selection.isCollapsed && selection.rangeCount > 0;
  const startNode = own ? selection!.getRangeAt(0).startContainer : null;
  const from = own
    ? (startNode instanceof Element ? startNode : startNode?.parentElement) ?? event.target
    : event.target;
  const found = anchorPlaceAt(from, { x: event.clientX, y: event.clientY }, own);
  if (!found) return;
  const { editor, id } = anchorMode;
  endAnchorMode();
  const line = lineOfId(editor.state, id);
  if (line < 0) return;
  window.getSelection()?.removeAllRanges();
  addAnchor(editor, line, found.item);
  caretTo(editor, editor.state.doc.line(line + 1).to);
}, true);

document.addEventListener("click", (event) => {
  if (!anchorSwallow) return;
  anchorSwallow = false;
  event.preventDefault();
  event.stopPropagation();
}, true);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && anchorMode) {
    event.preventDefault();
    endAnchorMode();
  }
}, true);

function anchorsHeld(): AnchorItem[] {
  return editors().flatMap((editor) => Array.from(marksOf(editor).items.values()).flat())
    .filter((item): item is AnchorItem => item.kind === "anchor");
}

/** The blocks of a drawn prompt or reply that an anchor's range covers. */
function anchoredBlocks(item: AnchorItem): HTMLElement[] {
  const holder = elements.get(item.key);
  const source = sources.get(item.key);
  if (!holder || source === undefined) return [];
  if (item.key.startsWith("t:")) return [holder];
  const lineAt = (point: number) => Array.from(source).slice(0, point).join("").split("\n").length - 1;
  const from = lineAt(item.start);
  const to = lineAt(Math.max(item.start, item.end - 1));
  const blocks = Array.from(holder.querySelectorAll<HTMLElement>("[data-source-line]"))
    .filter((block) => !block.querySelector("[data-source-line]"))
    .filter((block) => Number(block.dataset.sourceLine) <= to && Number(block.dataset.sourceEnd) > from);
  return blocks.length ? blocks : [holder];
}

/** A sent paragraph and where it was anchored, as `GET sessions/<id>/marks` gives it. */
interface SentMark {
  note: string; text: string | null; tags: string[];
  anchors: {
    role?: string; message?: string; uuid?: string; start?: number; end?: number; stale?: boolean;
    /** KYO-737: set on an anchor to a record (`id`) or to a file (`root`, `path`, `lines`). */
    kind?: string; id?: string; root?: string; path?: string; lines?: number[];
  }[];
}
let sentMarks_: SentMark[] = [];
/** What is drawn for the sent marks, so the mouse can find the paragraph behind a place. */
let sentPlaces: { mark: SentMark; range: Range | null; blocks: HTMLElement[] }[] = [];

function sentItems(): { mark: SentMark; item: AnchorItem }[] {
  return sentMarks_.flatMap((mark) => mark.anchors.flatMap((anchor) => {
    if (anchor.kind || anchor.stale || anchor.start === undefined || anchor.end === undefined) return [];
    const key = anchor.message ? `r:${anchor.message}` : `t:${anchor.uuid}`;
    const item: AnchorItem = {
      kind: "anchor", key, session: "", start: anchor.start, end: anchor.end, words: "", exact: true,
    };
    return [{ mark, item }];
  }));
}

/**
 * Where an anchor's words are drawn, if they lie within one of its blocks (KYO-729). The
 * place is the one the anchor's offsets name, so that the same words earlier in the block are
 * not marked in their stead. An anchor to the whole of its blocks has no such place unless
 * its words are drawn as written, and is shown by the bar beside them.
 */
function wordsRange(item: AnchorItem): Range | null {
  const source = sources.get(item.key);
  if (!item.exact || source === undefined) return null;
  const units = (point: number) => Array.from(source).slice(0, point).join("").length;
  const start = units(item.start);
  const end = units(item.end);
  const blocks = anchoredBlocks(item);
  for (const block of blocks) {
    const part = sourceOf(block, source);
    const from = start - part.base;
    const to = end - part.base;
    if (from < 0 || to > part.text.length || to <= from) continue;
    const whole = !part.text.slice(0, from).trim() && !part.text.slice(to).trim();
    const drawn = drawnOf(block);
    const span = drawn.nodes.length
      ? drawnSpan(part.text, drawn.text, from, to, blocks.length === 1 && !whole) : null;
    if (!span) continue;
    const place = (offset: number, last: boolean) => {
      const hit = drawn.nodes.findLast((held) => (last ? held.from < offset : held.from <= offset))
        ?? drawn.nodes[0];
      return [hit.node, offset - hit.from] as const;
    };
    const range = document.createRange();
    range.setStart(...place(span[0], false));
    range.setEnd(...place(span[1], true));
    return range;
  }
  return null;
}

/**
 * A sent anchor to a record or a file that the server now gives as stale is drawn so: the
 * element is gone from the store, or the file's lines no longer hold the words (KYO-737).
 */
function paintStalePlaces(): void {
  const stale = new Set(sentMarks_.flatMap((mark) => mark.anchors)
    .filter((anchor) => anchor.kind && anchor.stale).map((anchor) => placeKey(anchor as { kind: string })));
  for (const chip of Array.from(chat.querySelectorAll<HTMLElement>(".turn-place"))) {
    const gone = stale.has(chip.dataset.place ?? "");
    chip.classList.toggle("stale", gone);
    const line = (chip.title ?? "").replace(/\n\(stale:.*$/s, "");
    chip.title = gone ? `${line}\n(stale: this place no longer holds what was anchored)` : line;
  }
  paintStaleAnchors();
}

/** The paragraph a sent prompt's chip stands above: the text after it, up to the next line's end. */
function paragraphUnder(chip: HTMLElement): string {
  for (let node = chip.nextSibling; node; node = node.nextSibling) {
    if (node instanceof Text && node.data.trim()) return node.data.trim().split("\n")[0];
  }
  return "";
}

const STALE_WORDS = 8;

/**
 * KYO-712: a sent paragraph whose anchor to a reply or a prompt the server gives as stale has
 * lost its place: the record is no longer in the transcript, or no longer holds the words. No
 * highlight can be drawn for it, so the chip above the paragraph in the sent prompt says so
 * in words, and its title names the paragraph. The words are the stylesheet's, so that they
 * are no part of the prompt's text, which anchors and the search measure. A paragraph sent to
 * another session has no chip in this chat and is not shown here.
 */
function paintStaleAnchors(): void {
  const stale = new Map<string, SentMark[]>();
  for (const mark of sentMarks_) {
    for (const anchor of mark.anchors) {
      if (anchor.kind || !anchor.stale || !anchor.uuid) continue;
      stale.set(anchor.uuid, [...(stale.get(anchor.uuid) ?? []), mark]);
    }
  }
  for (const chip of Array.from(chat.querySelectorAll<HTMLElement>(".turn-anchor[data-anchor]"))) {
    const marks = stale.get(chip.dataset.anchor ?? "");
    const line = (chip.title ?? "").replace(/\n\(stale:.*$/s, "");
    chip.classList.toggle("stale", Boolean(marks));
    if (!marks) {
      chip.title = line;
      continue;
    }
    // Several paragraphs can be anchored to one record: the one under this chip is named.
    const under = paragraphUnder(chip);
    const mark = marks.find((held) => under && held.text?.split("\n")[0].trim() === under) ?? marks[0];
    const paragraph = firstWords(under || mark.text || "", STALE_WORDS);
    chip.title = `${line}\n(stale: the place${paragraph ? ` "${paragraph}" was anchored to` : " anchored"} is no longer in the transcript as it was)`;
  }
}

/** Opens the record at the element, or the file at the lines, that a place names. */
function revealPlace(place: Place): void {
  if (place.kind === "record") {
    const record = recordOf(place.id);
    if (record) openRecord({ record, ref: place.id });
    return;
  }
  showFile(place.root, place.path, place.lines[0], null, false, place.lines[1]);
}

/**
 * Shows in the chat what the unsent prompts are anchored to: selected words are highlighted,
 * and an anchor to whole blocks puts a quiet bar beside them.
 */
function paintAnchors(): void {
  for (const block of Array.from(chat.querySelectorAll(".anchored, .anchored-sent"))) {
    block.classList.remove("anchored", "anchored-sent");
  }
  paintStalePlaces();
  const ranges: Range[] = [];
  const sentRanges: Range[] = [];
  sentPlaces = sentItems().map(({ mark, item }) => {
    const range = wordsRange(item);
    const blocks = range ? [] : anchoredBlocks(item);
    if (range) sentRanges.push(range);
    for (const block of blocks) block.classList.add("anchored-sent");
    return { mark, range, blocks };
  });
  for (const item of anchorsHeld()) {
    const range = wordsRange(item);
    if (range) ranges.push(range);
    else for (const block of anchoredBlocks(item)) block.classList.add("anchored");
  }
  showRanges("anchored", ranges);
  showRanges("anchored-sent", sentRanges);
}

const paintedRanges = new Map<string, any>();

/**
 * Gives a named highlight these ranges and no others. Each name keeps one highlight, and
 * the ranges are taken out of it and put into it (KYO-780), so that the engine is told of
 * every range that goes. A removed anchor was reported to stay highlighted in the app's web
 * view; Chrome does not show that, and replacing the registry's entry, as was done before,
 * is a suspected cause that has not been confirmed.
 */
function showRanges(name: string, ranges: Range[], priority = 0): void {
  const highlights = (CSS as any).highlights;
  if (!highlights) return;
  let highlight = paintedRanges.get(name);
  if (!highlight) {
    highlight = new (window as any).Highlight();
    highlight.priority = priority;
    paintedRanges.set(name, highlight);
  }
  highlight.clear();
  for (const range of ranges) highlight.add(range);
  if (highlights.get(name) !== highlight) highlights.set(name, highlight);
}

// ------------------------------------- search in the chat, a record and a file (KYO-741, KYO-754)

/**
 * What is searched is the drawn text of each prompt and reply, not its source, so that every
 * hit marked is words the person can see; a markdown mark exists only in the source. A
 * reply's thinking and the tool calls are not searched. The drawn text and its folded copy
 * are kept for each element, which is replaced when its text changes, so a key typed walks
 * the elements' texts and not the document.
 *
 * A hit is kept as its place in the drawn text, and its range is made only when it is to be
 * painted. Only the hits in the part of the page on screen are painted, and none inside a
 * fold that is shut (KYO-754): the app's web view was measured stalling for about a
 * millisecond for each range of a highlight that lies in a shut fold, so a query of one
 * letter over a record with a folded brief, which is thousands of ranges, held the app for
 * seconds at each key. The count is of every hit, painted or not.
 */
const searchIndex = new WeakMap<HTMLElement, { drawn: Drawn; folded: string }>();
const SEARCH_DEBOUNCE_MS = 120;
/** The most hits painted at once. A window holds fewer; this bounds a page that hit-testing misreads. */
const PAINTED_HITS = 1000;
/** How long one turn of drawing a record's unopened folds may run before it yields. */
const FOLD_SLICE_MS = 30;
const findBar = document.getElementById("find")!;
const findField = document.getElementById("find-field") as HTMLInputElement;
const findCount = document.getElementById("find-count")!;
interface Hit { key: string; drawn: Drawn; from: number; to: number }
/** Where a hit stands, so that it is found again when the text around it is drawn anew. */
interface HitPlace { key: string; from: number; node: Node; offset: number }
let hits: Hit[] = [];
let currentHit = -1;
let searchTimer: number | undefined;
/**
 * The open record's page or the open file, read once and kept until it changes: either is
 * drawn over again whole, and a link made in it later splits its text, so a watch on the
 * two articles drops what was read.
 */
let pageIndex: { element: HTMLElement; drawn: Drawn; folded: string } | null = null;
let pageWatch: MutationObserver | null = null;
let foldTimer: number | undefined;
let paintFrame: number | undefined;

function watchPages(): void {
  if (pageWatch) return;
  pageWatch = new MutationObserver(() => {
    pageIndex = null;
    if (findBar.hidden) return;
    drawFoldsSoon();
    refreshSearch();
  });
  for (const article of [recordArticle, fileArticle]) {
    pageWatch.observe(article, { childList: true, subtree: true, characterData: true });
  }
}

/**
 * The text of a record's fold not yet opened is searched too, and so is drawn, shut. A brief
 * is long and a record can hold several, so they are drawn a slice at a time between the
 * keys typed and not inside the search a key starts; the watch then searches what was drawn.
 */
function drawFoldsSoon(): void {
  if (foldTimer !== undefined || findBar.hidden || currentView !== "records") return;
  const waiting = () => Array.from(recordArticle.querySelectorAll("details")).filter((fold) => unfilled.has(fold));
  if (!waiting().length) return;
  foldTimer = window.setTimeout(() => {
    foldTimer = undefined;
    if (findBar.hidden || currentView !== "records") return;
    const began = performance.now();
    for (const fold of waiting()) {
      unfilled.get(fold)?.();
      if (performance.now() - began > FOLD_SLICE_MS) break;
    }
    drawFoldsSoon();
  }, 0);
}

/** What the view showing is searched over: the chat's replies, the open record or the open file. */
function searchable(): HTMLElement[] {
  if (currentView === "records") return openRecordDoc && !recordArticle.hidden ? [recordArticle] : [];
  if (currentView === "files") return openFile?.text != null && !fileArticle.hidden ? [fileArticle] : [];
  if (currentView === "history") return historyReader.hidden ? [] : [historyReader];
  return Array.from(chat.children).filter((node): node is HTMLElement => {
    const key = (node as HTMLElement).dataset?.key;
    return !!key && (key.startsWith("t:") || key.startsWith("r:"));
  });
}

function indexed(element: HTMLElement) {
  if (element === recordArticle || element === fileArticle) {
    if (pageIndex?.element !== element) {
      const drawn = drawnOf(element);
      pageIndex = { element, drawn, folded: fold(drawn.text) };
    }
    return pageIndex;
  }
  let entry = searchIndex.get(element);
  if (!entry) {
    const drawn = drawnOf(element);
    entry = { drawn, folded: fold(drawn.text) };
    searchIndex.set(element, entry);
  }
  return entry;
}

/** The range that the drawn characters `from` to `to` of an element's text make. */
function drawnRange(drawn: Drawn, from: number, to: number): Range | null {
  const first = drawn.nodes[lastHolding(drawn.nodes.length, (index) => drawn.nodes[index].from <= from)];
  const last = drawn.nodes[lastHolding(drawn.nodes.length, (index) => drawn.nodes[index].from < to)];
  if (!first || !last) return null;
  const range = document.createRange();
  range.setStart(first.node, Math.min(from - first.from, first.node.length));
  range.setEnd(last.node, Math.min(to - last.from, last.node.length));
  return range;
}

const rangeOf = (hit: Hit) => drawnRange(hit.drawn, hit.from, hit.to);

/** Where the current hit stands, to find it again after the page has been read anew. */
function currentPlace(): HitPlace | undefined {
  const hit = hits[currentHit];
  const range = hit && rangeOf(hit);
  return range ? { key: hit.key, from: hit.from, node: range.startContainer, offset: range.startOffset } : undefined;
}

/** The hit at a place kept from before: at the same text node where it is still drawn, else at the same count of characters. */
function hitAt(place: HitPlace): number {
  let drawn: Drawn | undefined;
  let from = place.from;
  return hits.findIndex((hit) => {
    if (hit.key !== place.key) return false;
    if (hit.drawn !== drawn) {
      drawn = hit.drawn;
      const held = drawn.nodes.find((own) => own.node === place.node);
      from = held ? held.from + place.offset : place.from;
    }
    return hit.from === from;
  });
}

/** Finds every hit of the query, and which is the current one. What is marked is `paintHits`'s. */
function runSearch(keep?: HitPlace): void {
  const phrase = queryPhrase(findField.value);
  const found: Hit[] = [];
  if (phrase) {
    for (const element of searchable()) {
      const { drawn, folded } = indexed(element);
      const key = element.dataset.key ?? element.id;
      for (const [from, to] of findSpans(drawn.text, phrase, folded)) found.push({ key, drawn, from, to });
    }
  }
  // In the chat the newest is first: the last hit of the page is the first.
  hits = currentView === "chat" ? found.reverse() : found;
  const kept = keep ? hitAt(keep) : -1;
  currentHit = kept >= 0 ? kept : hits.length ? Math.min(Math.max(currentHit, 0), hits.length - 1) : -1;
  if (keep && kept < 0 && hits.length) currentHit = 0;
}

/** The fold that hides a place in the page: one that is shut, other than by its own label. */
function shutFoldOver(node: Node): HTMLDetailsElement | null {
  for (let element = node.parentElement; element; element = element.parentElement) {
    if (!(element instanceof HTMLDetailsElement) || element.open) continue;
    const label = element.firstElementChild;
    if (!(label instanceof HTMLElement && label.tagName === "SUMMARY" && label.contains(node))) return element;
  }
  return null;
}

function searchPane(): HTMLElement {
  return currentView === "records" ? recordsPane : currentView === "files" ? filesPane
    : currentView === "history" ? historyPane : scroller;
}

/** The top and bottom of the part of the window the searched text is read in. */
function searchBand(): { top: number; bottom: number } {
  const box = searchPane().getBoundingClientRect();
  const bar = document.getElementById("top")?.getBoundingClientRect().bottom ?? 0;
  return { top: Math.max(box.top, bar), bottom: Math.min(box.bottom, window.innerHeight) };
}

/**
 * The hits in the part of the page on screen, as the first of them and the one after the
 * last, counted in the page's order. The two ends of what is on screen are read as places in
 * the text, and the hits between them found by halving, so nothing is measured for each hit.
 * Where the ends cannot be read, the hits around the current one are given.
 */
function hitsOnScreen(): [number, number] {
  const count = hits.length;
  const around: [number, number] = [
    Math.max(0, currentHit - PAINTED_HITS / 2), Math.min(count, Math.max(currentHit, 0) + PAINTED_HITS / 2)];
  const box = searchPane().getBoundingClientRect();
  const band = searchBand();
  if (band.bottom - band.top < 2 || box.right - box.left < 2) return around;
  const first = caretAt(box.left + 1, band.top + 1);
  const last = caretAt(box.right - 1, band.bottom - 1);
  if (!first || !last) return around;
  // The chat's hits are held newest first, which is the page's order turned round.
  const inOrder = (index: number) => hits[currentView === "chat" ? count - 1 - index : index];
  try {
    /** Where a place in the page lies against a hit: before it, in it or after it. */
    const side = (index: number, place: { node: Node; offset: number }) =>
      rangeOf(inOrder(index))?.comparePoint(place.node, place.offset) ?? 0;
    const low = lastHolding(count, (index) => side(index, first) > 0) + 1;
    const high = lastHolding(count, (index) => side(index, last) >= 0) + 1;
    return currentView === "chat" ? [count - high, count - low] : [low, high];
  } catch {
    // A place read from outside the page's own tree compares with nothing in it.
    return around;
  }
}

/** Shows the count, and marks the hits on screen and the current one. */
function paintHits(): void {
  findCount.textContent = tally(currentHit, hits.length, !findBar.hidden && findField.value.trim() !== "");
  if (!(CSS as any).highlights) return;
  const ranges: Range[] = [];
  const [low, high] = hits.length ? hitsOnScreen() : [0, 0];
  for (let at = low; at < high && ranges.length < PAINTED_HITS; at++) {
    if (at === currentHit) continue;
    const range = rangeOf(hits[at]);
    if (!range) continue;
    const shut = shutFoldOver(range.startContainer);
    if (shut) {
      // Every hit the same fold hides follows this one, and is passed over in one step.
      const hidden = (index: number) => {
        const within = rangeOf(hits[at + index])?.startContainer;
        return !!within && shut.contains(within) && shutFoldOver(within) !== null;
      };
      at += Math.max(lastHolding(high - at, hidden), 0);
      continue;
    }
    ranges.push(range);
  }
  showRanges("found", ranges);
  const current = hits[currentHit] ? rangeOf(hits[currentHit]) : null;
  showRanges("found-current", current ? [current] : [], 1);
}

/** The page has moved under the window, or a fold has opened or shut: the hits now on screen are marked. */
function paintHitsSoon(): void {
  if (findBar.hidden || !hits.length || paintFrame !== undefined) return;
  paintFrame = requestAnimationFrame(() => {
    paintFrame = undefined;
    if (!findBar.hidden) paintHits();
  });
}

/** New text has been drawn: the marks are laid again, the current hit staying where it was. */
function refreshSearch(): void {
  if (findBar.hidden) return;
  runSearch(currentPlace());
  paintHits();
}

function moveToHit(forward: boolean): void {
  if (searchTimer !== undefined) {
    window.clearTimeout(searchTimer);
    searchTimer = undefined;
    runSearch();
    showHit();
    return;
  }
  currentHit = step(currentHit, hits.length, forward);
  showHit();
}

/** Brings the current hit into the window and marks what is then on screen. */
function showHit(): void {
  const range = hits[currentHit] ? rangeOf(hits[currentHit]) : null;
  if (range) {
    // A hit inside a fold the page keeps shut opens it, so that it is seen (KYO-754).
    for (let node: Node | null = range.startContainer; node; node = node.parentNode) {
      if (node instanceof HTMLDetailsElement && !node.open) node.open = true;
    }
    const rect = range.getBoundingClientRect();
    const band = searchBand();
    searchPane().scrollTop += scrollBy(rect.top, rect.bottom, band.top + 24, band.bottom - 24);
  }
  paintHits();
}

const FIND_PLACEHOLDERS: Record<string, string> = {
  chat: "Search this chat", records: "Search this record", files: "Search this file",
  history: "Search this session",
};

function openSearch(): void {
  if (currentView === "records" ? recordArticle.hidden : currentView === "files" ? fileArticle.hidden
    : currentView === "history" ? historyReader.hidden : false) return;
  watchPages();
  findField.placeholder = FIND_PLACEHOLDERS[currentView] ?? "Search";
  findField.setAttribute("aria-label", findField.placeholder);
  findBar.hidden = false;
  // KYO-883: Chat's second row is shown only to hold the search.
  document.body.classList.add("finding");
  findField.focus();
  findField.select();
  drawFoldsSoon();
  if (findField.value) refreshSearch();
}

function closeSearch(): void {
  if (findBar.hidden) return;
  if (searchTimer !== undefined) window.clearTimeout(searchTimer);
  searchTimer = undefined;
  if (foldTimer !== undefined) window.clearTimeout(foldTimer);
  foldTimer = undefined;
  findBar.hidden = true;
  document.body.classList.remove("finding");
  hits = [];
  currentHit = -1;
  paintHits();
}

// The hits marked are those on screen, so they are marked again when the page moves under
// the window, when the window changes size, and when a fold opens or shuts.
for (const pane of ["page", "view-records", "view-files"]) {
  document.getElementById(pane)?.addEventListener("scroll", paintHitsSoon, { passive: true });
}
window.addEventListener("resize", paintHitsSoon);
document.addEventListener("toggle", paintHitsSoon, true);

for (const [id, forward] of [["find-prev", false], ["find-next", true]] as const) {
  const button = document.getElementById(id)!;
  button.replaceChildren(icon(forward ? "down" : "up"));
  // The click leaves the focus in the field, where Return and Shift-Return go on stepping.
  button.addEventListener("mousedown", (event) => event.preventDefault());
  button.addEventListener("click", () => moveToHit(forward));
}
findField.addEventListener("input", () => {
  if (searchTimer !== undefined) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    searchTimer = undefined;
    currentHit = -1;
    runSearch();
    showHit();
  }, SEARCH_DEBOUNCE_MS);
});
// A press closes the bar, for a reader who does not know Escape does.
const findClose = document.getElementById("find-close")!;
findClose.replaceChildren(icon("close"));
findClose.addEventListener("click", () => closeSearch());
findField.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    moveToHit(!event.shiftKey);
  } else if (event.key === "Escape") {
    event.preventDefault();
    closeSearch();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key.toLowerCase() === "f" && event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey) {
    if (event.target instanceof Node && terminalPane.contains(event.target)) return;
    event.preventDefault();
    openSearch();
  } else if (event.key === "Escape" && !findBar.hidden) {
    closeSearch();
  }
});

const markTip = document.createElement("div");
markTip.id = "mark-tip";

/** Over a place a sent prompt was anchored to, shows the paragraph written against it. */
chat.addEventListener("mousemove", (event) => {
  const { clientX: x, clientY: y } = event;
  const inside = (rect: DOMRect) => x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
  const target = event.target as Node;
  const hits = sentPlaces.filter((place) =>
    place.range ? Array.from(place.range.getClientRects()).some(inside)
      : place.blocks.some((block) => block.contains(target)));
  if (!hits.length) {
    markTip.remove();
    return;
  }
  markTip.replaceChildren(...hits.map(({ mark }) => {
    const row = document.createElement("div");
    row.textContent = mark.text || "(no words)";
    if (mark.tags.length) {
      const tags = document.createElement("small");
      tags.textContent = mark.tags.map((tag) => `#${tag}`).join(" ");
      row.append(tags);
    }
    return row;
  }));
  document.body.append(markTip);
  markTip.style.left = `${Math.min(x + 12, window.innerWidth - markTip.offsetWidth - 8)}px`;
  markTip.style.top = `${Math.min(y + 16, window.innerHeight - markTip.offsetHeight - 8)}px`;
});
chat.addEventListener("mouseleave", () => markTip.remove());

/** Brings an anchor's place into view and shows which it is. */
function reveal(item: AnchorItem): void {
  const blocks = anchoredBlocks(item);
  const words = wordsRange(item)?.startContainer.parentElement;
  (words ?? blocks[0])?.scrollIntoView({ block: "center", behavior: "smooth" });
  for (const block of blocks) {
    block.classList.add("revealed");
    window.setTimeout(() => block.classList.remove("revealed"), 1600);
  }
}

/** A box for one prompt: the whole of one message, with its own Send and Delete. */
/**
 * KYO-903: the scratchpads, each by its editor, with the field holding its heading. A box
 * that is not here is the prompt, the one box that is sent.
 */
const scratchOf = new WeakMap<EditorView, { heading: HTMLInputElement; box: HTMLElement }>();

/** The prompt's box: the one that is not a scratchpad. */
function mainBox(): HTMLElement | null {
  return boxes.querySelector<HTMLElement>(":scope > .box.main");
}

/** The prompt's editor: the one that is not a scratchpad's. */
function mainEditor(): EditorView | undefined {
  return editors().find((editor) => !scratchOf.has(editor));
}

/** Create prompt is offered while the prompt is empty, since it would replace what is there. */
function syncCreate(): void {
  const main = mainEditor();
  const full = Boolean(main && isDraft(main));
  for (const button of boxes.querySelectorAll<HTMLButtonElement>(".box-create")) {
    button.disabled = full;
    button.title = full
      ? "The prompt already holds something. Send or clear it first."
      : "Move this scratchpad into the prompt";
  }
}

/** A scratchpad's heading field shows the first words of its text until it is given one. */
function nameScratch(editor: EditorView): void {
  const held = scratchOf.get(editor);
  if (held) held.heading.placeholder = textOf(editor).split("\n").find((line) => line.trim())?.trim().slice(0, 80) || "Scratchpad";
}

/**
 * Keeps the composer to one prompt, the last box, with the scratchpads above it (KYO-903).
 * Among boxes that are not scratchpads, an empty one beside another leaves; of several that
 * hold something, as in a draft saved before there were scratchpads or after a merge that
 * kept two versions, the last stays the prompt and the others become scratchpads. With none,
 * an empty prompt is added. It says whether a box became a scratchpad.
 */
function ensureMain(): boolean {
  const mains = editors().filter((editor) => !scratchOf.has(editor));
  const drafts = mains.filter(isDraft);
  const keep = drafts.length ? drafts[drafts.length - 1] : mains[mains.length - 1];
  let changed = false;
  for (const editor of mains) {
    if (editor === keep) continue;
    const box = boxOf(editor);
    if (openMark?.editor === editor) hideMark();
    if (isDraft(editor)) {
      const held = savedBox(editor);
      const fresh = addBox(held.text, held.marks, held.id, { heading: "" });
      box.replaceWith(boxOf(fresh));
      changed = true;
    } else {
      box.remove();
    }
  }
  const main = boxOf(keep ?? addBox());
  if (main !== boxes.lastElementChild) boxes.append(main);
  syncCreate();
  return changed;
}

/**
 * KYO-904: moves the section that begins at `line` of a scratchpad to the end of the prompt,
 * with what each of its lines holds. Both boxes are drawn again from what they then hold; a
 * scratchpad left with nothing leaves.
 */
function sendToPrompt(editor: EditorView, line: number): void {
  const main = mainEditor();
  if (!main || line < 0) return;
  hideMark();
  const from = savedBox(editor);
  const to = savedBox(main);
  const end = sectionEnd(textOf(editor), line);
  const moved = moveLines(from, to, line, end);
  // KYO-913: the section's folds go with it, and the folds below it move up.
  const folds = foldsById.get(from.id) ?? [];
  const lines = moved.target.text.split("\n");
  const start = lines.length - (end - line);
  foldsById.set(from.id, folds.filter((fold) => fold.line < line || fold.line >= end)
    .map((fold) => (fold.line >= end ? { ...fold, line: fold.line - (end - line) } : fold)));
  foldsById.set(to.id, [...(foldsById.get(to.id) ?? []), ...folds.filter((fold) => fold.line >= line && fold.line < end)
    .map((fold) => ({ line: start + fold.line - line, text: lines[start + fold.line - line] }))]);
  const scratch = from.scratch ?? { heading: "" };
  const emptied = !moved.source.text.trim() && !moved.source.marks?.length && !scratch.heading;
  const old = boxOf(editor);
  if (emptied) old.remove();
  else old.replaceWith(boxOf(addBox(moved.source.text, moved.source.marks, from.id, scratch)));
  boxOf(main).remove();
  const prompt = addBox(moved.target.text, moved.target.marks, to.id);
  ensureMain();
  paintAnchors();
  remember();
  caretTo(prompt, prompt.state.doc.length);
}

/** Moves a scratchpad's words and marks into the prompt, which is empty (KYO-903). */
function createPrompt(editor: EditorView): void {
  const main = mainEditor();
  if (main && isDraft(main)) return;
  const held = savedBox(editor);
  if (openMark?.editor === editor || (main && openMark?.editor === main)) hideMark();
  if (main) boxOf(main).remove();
  boxOf(editor).remove();
  const prompt = addBox(held.text, held.marks, held.id);
  ensureMain();
  paintAnchors();
  remember();
  caretTo(prompt, prompt.state.doc.length);
}

/**
 * Files pasted into a box, or dropped on it (KYO-910). A paste attaches every file to the
 * caret's paragraph. A drop attaches an image or a PDF to the paragraph it fell on; any other
 * file dropped is the app's to name, which it inserts as its path through `kyo.dropPaths`.
 */
function filesIn(editor: EditorView, files: File[], how: "paste" | "drop", pos: number): void {
  const line = editor.state.doc.lineAt(pos).number - 1;
  const taken = how === "paste" ? files
    : files.filter((file) => file.type.startsWith("image/") || file.type === "application/pdf");
  if (!taken.length) return;
  if (how === "drop") editor.focus();
  for (const file of taken) attachFile(editor, file, line);
}

function addBox(
  text = "", saved: SavedBox["marks"] = [], id: string = boxId(), scratch: Scratch | null = null,
): EditorView {
  const box = document.createElement("div");
  box.className = scratch ? `box scratch${scratch.collapsed ? " collapsed" : ""}` : "box main";
  const body = document.createElement("div");
  body.className = "box-body";
  box.append(body);
  // The box is placed before its editor is built, so the editor measures its font at once.
  if (scratch) boxes.insertBefore(box, mainBox());
  else boxes.append(box);
  const editor = createEditor<Item>({
    parent: body,
    text,
    marks: saved,
    placeholder: scratch ? "Notes…" : "Reply…",
    drawMark,
    drawGhost,
    // KYO-904: a right-click in a scratchpad's gutter opens the panel of the paragraph under
    // the pointer, which holds Send to prompt.
    onGutterMenu: (view, _line, lineId, event) => {
      if (!scratchOf.has(view) || lineId === undefined) return false;
      event.preventDefault();
      showMark(view, lineId);
      return true;
    },
    onFiles: filesIn,
    // A paste is sent as a paste.
    onPaste: (view, pastedText) => pasted.set(view, [...(pasted.get(view) ?? []), pastedText]),
    onUpdate: boxUpdated,
    // KYO-913: a fold's placeholder titles what its hidden marks hold, and the box's folds are
    // kept by its id, so that the box drawn again folds them again.
    describeItems: markTitle,
    folds: foldsById.get(id),
    onFolds: (_view, lines) => {
      if (lines.length) foldsById.set(id, lines);
      else foldsById.delete(id);
    },
    extensions: [showAbovePrompt],
  });
  boxIds.set(editor, id);
  editor.contentDOM.addEventListener("focus", () => { lastEditor = editor; });
  let armed: number | undefined;
  const disarm = () => {
    if (armed !== undefined) clearTimeout(armed);
    armed = undefined;
    remove.textContent = "Delete";
  };
  // A box leaves only by this button (KYO-727): an empty one that closed when the focus left
  // it was gone before an anchor could be chosen for it in the chat. Delete asks once more
  // when the box holds words or marks, then removes this prompt and leaves the others.
  const remove = boxButton("box-delete", "Delete", () => {
    if (armed === undefined && (editor.state.doc.length || marksOf(editor).items.size)) {
      remove.textContent = "Delete?";
      armed = window.setTimeout(disarm, 3000);
      return;
    }
    disarm();
    const wasPrompt = !scratchOf.has(editor);
    foldsById.delete(id);
    box.remove();
    ensureMain();
    // The prompt that was deleted leaves an empty one, which takes the caret.
    if (wasPrompt) mainEditor()?.focus();
    // KYO-780: what the box was anchored to is no longer anchored.
    paintAnchors();
    remember();
  });
  // Bare text in the accent colour, then the keys that do the same.
  const send = boxButton("box-send", "", () => attemptSend(editor));
  const word = document.createElement("span");
  const keys = document.createElement("span");
  keys.className = "send-keys";
  keys.textContent = "⌘↩";
  send.append(word, keys);
  word.textContent = sendGate.current ? "Sending…" : "Send";
  send.disabled = sendGate.current !== null;
  send.title = "Send (⌘↩)";
  const actions = document.createElement("div");
  actions.className = "box-actions";
  if (scratch) {
    // KYO-903: a scratchpad is headed by its name, which is saved and never sent, and is
    // collapsed to it. It has no Send: Create prompt moves it into the prompt.
    const heading = document.createElement("input");
    heading.type = "text";
    heading.className = "scratch-heading";
    heading.maxLength = 120;
    heading.value = scratch.heading ?? "";
    heading.setAttribute("aria-label", "Scratchpad heading");
    heading.addEventListener("input", remember);
    heading.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.metaKey) return;
      event.preventDefault();
      box.classList.remove("collapsed");
      toggle.setAttribute("aria-expanded", "true");
      editor.focus();
    });
    const toggle = boxButton("scratch-toggle", "", () => {
      const collapsed = box.classList.toggle("collapsed");
      toggle.setAttribute("aria-expanded", String(!collapsed));
      if (collapsed && openMark?.editor === editor) hideMark();
      // A box drawn while it was collapsed had no size to lay its lines out in.
      if (!collapsed) editor.requestMeasure();
      remember();
    });
    toggle.title = "Collapse or open this scratchpad";
    toggle.setAttribute("aria-expanded", String(!scratch.collapsed));
    const head = document.createElement("div");
    head.className = "scratch-head";
    head.append(toggle, heading);
    scratchOf.set(editor, { heading, box });
    const create = boxButton("box-create", "Create prompt", () => createPrompt(editor));
    actions.append(remove, create);
    box.prepend(head);
    box.append(actions);
    nameScratch(editor);
  } else {
    actions.append(remove, send);
    box.append(actions);
  }
  syncCreate();
  paintAnchors();
  queueMicrotask(drawLanding);
  return editor;
}

const SHELF_KEY = "composer-shelf";
const WIDTH_KEY = "composer-width";
interface Shelved { at: string; texts: string[] }

function stored<T>(key: string, fallback: T): T {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : (JSON.parse(value) as T);
  } catch {
    return fallback;
  }
}

function store(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {}
}

function currentTexts(): string[] {
  return editors().map(textOf).filter(Boolean);
}

/** One prompt as it is saved: its id, its words, and its marks by the line each is on. */
function savedBox(editor: EditorView): NamedBox {
  const marks = marksOf(editor);
  const scratch = scratchOf.get(editor);
  return {
    id: boxIds.get(editor)!,
    ...(scratch ? {
      scratch: {
        heading: scratch.heading.value,
        ...(scratch.box.classList.contains("collapsed") ? { collapsed: true } : {}),
      },
    } : {}),
    text: textOf(editor),
    marks: marks.ids.flatMap((id, line) => {
      // A file still uploading is not saved: nothing would finish it after a relaunch.
      const items = marks.items.get(id)?.filter((item) => !(item.kind === "file" && item.pending));
      return items?.length ? [{ line, items }] : [];
    }),
  };
}

/** A box that is a draft: a prompt with an anchor and no words yet is still one. */
function isDraft(editor: EditorView): boolean {
  return Boolean(editor.state.doc.length || marksOf(editor).items.size || scratchOf.get(editor)?.heading.value);
}

/** Each prompt with its marks. */
function currentBoxes(): NamedBox[] {
  return editors().filter(isDraft).map(savedBox);
}

/** The draft this window and the server last agreed on (KYO-798); none before one is read. */
let agreed: Agreed = agreedOn([]);
/** The saves the server has not answered yet, by the number each was posted under. */
const posted = new Map<number, NamedBox[]>();
let saveSeq = 0;
/**
 * The etag of the server's draft this window was last handed. A save states it, so that the
 * app can tell a save asked for before the page had merged a newer draft, which it drops:
 * the merge asks for its own.
 */
let seenEtag: string | null = null;

/** Asks the app to save the prompts, and keeps what was asked until the server answers. */
function postDraft(): void {
  const held = currentBoxes();
  saveSeq += 1;
  posted.set(saveSeq, held);
  post("saveDraft", { boxes: held, seq: saveSeq, seen: seenEtag });
}

/**
 * The agreed draft once `server` has met this window. A box whose id is in `taken` is shown
 * as the server sent it, and is compared from now on as this window writes it. Any other is
 * one this window kept its own edit of, and is compared with the server's copy.
 */
function agreedWith(server: NamedBox[], taken: Set<string>): Agreed {
  const drawn = signatures(currentBoxes());
  return {
    server: signatures(server),
    local: new Map(server.map((box) =>
      [box.id, (taken.has(box.id) ? drawn.get(box.id) : undefined) ?? boxSignature(box)])),
  };
}

/**
 * Shows these prompts in place of the ones on the screen, leaving alone each box that
 * already holds what is wanted: its caret, its selection and its own undo history stay. A
 * box with no words is no part of a draft and stays too.
 */
function applyBoxes(wanted: NamedBox[]): void {
  const before = editors();
  const focused = before.find((editor) => editor.hasFocus);
  const caret = focused ? {
    id: boxIds.get(focused)!, anchor: focused.state.selection.main.anchor, head: focused.state.selection.main.head,
  } : null;
  const have = new Map(before.map((editor) => [boxIds.get(editor)!, editor]));
  const ids = new Set(wanted.map((box) => box.id));
  const kept = new Set<EditorView>();
  const order: Element[] = [];
  const wasLoading = loadingDraft;
  loadingDraft = true;
  for (const box of wanted) {
    const shown = have.get(box.id);
    const editor = shown && boxSignature(savedBox(shown)) === boxSignature(box)
      ? shown : addBox(box.text, box.marks, box.id, box.scratch ?? null);
    kept.add(editor);
    order.push(boxOf(editor));
  }
  for (const editor of before) {
    if (kept.has(editor)) continue;
    if (!isDraft(editor) && !ids.has(boxIds.get(editor)!)) {
      order.push(boxOf(editor));
      continue;
    }
    if (openMark?.editor === editor) hideMark();
    boxOf(editor).remove();
  }
  // Only a box that is out of its place is moved, since moving one takes the focus from it.
  let next = boxes.querySelector(":scope > .box");
  for (const box of order) {
    if (box === next) next = next.nextElementSibling;
    else boxes.insertBefore(box, next);
  }
  ensureMain();
  loadingDraft = wasLoading;
  paintAnchors();
  const again = caret && editors().find((editor) => boxIds.get(editor) === caret.id);
  if (again && !again.hasFocus) {
    const length = again.state.doc.length;
    again.focus();
    again.dispatch({ selection: { anchor: Math.min(caret.anchor, length), head: Math.min(caret.head, length) } });
  }
}

/**
 * The window's project and the name its unsent prompts are kept under, which the app states
 * in its `project` frame (KYO-761); `scope` is null until the first one arrives.
 */
let windowProject = "";
let scope: string | null = null;

/** The window's storage, named inside each call: where it is denied, naming it throws. */
const kept = {
  getItem: (key: string) => localStorage.getItem(key),
  setItem: (key: string, value: string) => localStorage.setItem(key, value),
  removeItem: (key: string) => localStorage.removeItem(key),
};

/** Keeps the boxes in this window's storage, under the project they were written for. */
function keepCurrent(): void {
  if (scope !== null) keepBoxes(kept, scope, currentBoxes());
}

/** Sends a save that was waiting for a pause, when the window is about to leave its session. */
function flushDraft(): void {
  if (draftSave === undefined) return;
  clearTimeout(draftSave);
  draftSave = undefined;
  postDraft();
}

const DRAFT_SAVE_DELAY_MS = 700;
let draftSave: number | undefined;
let loadingDraft = false;

/**
 * Prompts once kept on the Drafts shelf become prompts in the composer, where unsent prompts
 * are the drafts. It runs after the session's saved draft has loaded, which replaces the boxes.
 */
function adoptShelf(): void {
  const shelf = stored<Shelved[]>(SHELF_KEY, []);
  if (!shelf.length) return;
  const have = new Set(currentTexts());
  const adopted = shelf.map((draft) => draft.texts.join("\n\n")).filter((text) => !have.has(text));
  if (adopted.length) showTexts([...currentTexts(), ...adopted]);
  try {
    localStorage.removeItem(SHELF_KEY);
  } catch {}
}

/** Keeps the boxes in this window's storage at once, and on the server after a pause. */
function remember(): void {
  if (loadingDraft) return;
  keepCurrent();
  if (draftSave !== undefined) clearTimeout(draftSave);
  draftSave = window.setTimeout(() => {
    draftSave = undefined;
    postDraft();
  }, DRAFT_SAVE_DELAY_MS);
}

/**
 * Shows these prompts. A prompt whose text is already known keeps its marks, which come from
 * what is on the screen or from `kept`.
 */
function showTexts(texts: string[], kept: SavedBox[] = currentBoxes()): void {
  hideMark();
  // The session's list is the first thing in this column and is not a prompt.
  for (const box of boxes.querySelectorAll(":scope > .box")) box.remove();
  // A prompt whose text is known keeps its id with its marks, each known prompt once.
  const left = [...kept];
  for (const text of texts) {
    const at = left.findIndex((box) => box.text === text);
    const known = at < 0 ? undefined : left.splice(at, 1)[0];
    addBox(text, known?.marks, known?.id || boxId(), known?.scratch ?? null);
  }
  ensureMain();
  remember();
}

/**
 * KYO-883: the image a right-click was on, which Quick Look in the image menu shows. An image
 * the page holds whole goes as its data; a chip's thumbnail is small, so the app fetches the
 * attachment it stands for.
 */
type QuickLookTarget = { data: string; name: string; attachment?: string };
let quickLookTarget: QuickLookTarget | null = null;
document.addEventListener("contextmenu", (event) => {
  const image = event.target instanceof HTMLImageElement ? event.target : null;
  const name = image?.alt || "image";
  quickLookTarget = !image ? null
    : !image.src.startsWith("data:") ? null
      // A chip's thumbnail is shown if the server no longer has its attachment for this session.
      : image.dataset.attachment ? { attachment: image.dataset.attachment, name, data: image.src }
        : { data: image.src, name };
}, true);

/** One mark of a prompt as `send-prompt` takes it and the server returns it (KYO-809). */
interface SentMarkBody {
  line: number; anchors?: Record<string, any>[]; tags?: string[]; attachments?: string[];
}

/**
 * A held prompt's marks as the composer holds them, the inverse of `sentMarks`. The server
 * keeps what was sent, which has no words for a chip to show, so each chip is worded from
 * what its anchor names.
 */
function restoredMarks(marks: SentMarkBody[]): { line: number; items: Item[] }[] {
  return marks.flatMap((mark) => {
    const items: Item[] = [];
    for (const anchor of mark.anchors ?? []) {
      if (anchor.kind === "record") {
        items.push({ kind: "record", id: anchor.id, words: anchor.id });
      } else if (anchor.kind === "file") {
        const name = String(anchor.path).split("/").pop();
        items.push({
          kind: "path", root: anchor.root, path: anchor.path, lines: anchor.lines,
          ...(anchor.chars ? { chars: anchor.chars } : {}),
          ...(anchor.session ? { session: anchor.session } : {}),
          words: `${name}:${anchor.lines?.[0]}`,
        });
      } else if (anchor.kind === "turn") {
        items.push({
          kind: "anchor", key: anchor.message ? `r:${anchor.message}` : `t:${anchor.uuid}`,
          session: anchor.session, start: anchor.start, end: anchor.end,
          words: "an earlier message", exact: true,
        });
      }
    }
    for (const name of mark.tags ?? []) items.push({ kind: "tag", name });
    for (const id of mark.attachments ?? []) items.push({ kind: "file", id, name: "attachment" });
    return items.length ? [{ line: mark.line, items }] : [];
  });
}

/** A new prompt is made on purpose: an empty box is reused before another is added. */
/** The + button: a new scratchpad above the prompt, with the caret in its heading (KYO-903). */
function newPrompt(): void {
  const editor = addBox("", [], boxId(), { heading: "" });
  syncCreate();
  scratchOf.get(editor)!.heading.focus();
}

function post(name: string, body: unknown): void {
  (window as any).webkit?.messageHandlers?.[name]?.postMessage(body);
}

/** One prompt's text, split at what was pasted, since a paste is sent as a paste. */
function messageSegments(editor: EditorView): { kind: string; text: string }[] {
  const segments: { kind: string; text: string }[] = [];
  const add = (kind: string, text: string) => {
    const last = segments[segments.length - 1];
    if (!text) return;
    if (last?.kind === kind) last.text += text;
    else segments.push({ kind, text });
  };
  let rest = textOf(editor);
  for (const piece of pasted.get(editor) ?? []) {
    const at = rest.indexOf(piece);
    if (at < 0) continue;
    add("typed", rest.slice(0, at));
    add("pasted", piece);
    rest = rest.slice(at + piece.length);
  }
  add("typed", rest);
  return segments;
}

/** The prompt whose Send was answered with the cold-cache warning, until it is pressed again. */
let coldConfirm: EditorView | null = null;

const uploads = new Map<string, { editor: EditorView; item: FileItem }>();
let nextUpload = 1;
const THUMB_PX = 96;

/** A small picture of an image for the mark's panel, or nothing for what cannot be drawn. */
async function thumbnail(file: File): Promise<string | undefined> {
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, THUMB_PX / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.7);
  } catch {
    return undefined;
  }
}

/**
 * Puts a file on a paragraph, the caret's unless another is named, and sends it to the
 * session's machine. The attaching is a step of the box's history, as an anchor's is (KYO-910).
 */
async function attachFile(editor: EditorView, file: File, line = caretLine(editor)): Promise<void> {
  const ref = `upload-${nextUpload++}`;
  const item: FileItem = { kind: "file", id: "", name: file.name || "pasted image", pending: ref };
  placeItem<Item>(editor, line, item);
  uploads.set(ref, { editor, item });
  item.thumb = await thumbnail(file);
  refreshMarks(editor);
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let at = 0; at < bytes.length; at += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(at, at + 0x8000));
  }
  post("attach", { ref, type: file.type, name: item.name, data: btoa(binary) });
}

/** A prompt's marks as the server takes them: the line, its anchors and its tags. */
function sentMarks(editor: EditorView): unknown[] {
  const marks = marksOf(editor);
  return marks.ids.flatMap((id, line) => {
    const items = marks.items.get(id) ?? [];
    if (!items.length) return [];
    const anchors = items.flatMap((item) => {
      if (item.kind === "record" || item.kind === "path") return [placeAnchor(item)];
      if (item.kind !== "anchor") return [];
      const place = { kind: "turn", session: item.session, start: item.start, end: item.end };
      // A reply is named by its message, which may join several records; a prompt by its own.
      return [item.key.startsWith("r:")
        ? { ...place, message: item.key.slice(2) }
        : { ...place, uuid: item.key.slice(2) }];
    });
    const tags = items.flatMap((item) => (item.kind === "tag" ? [item.name] : []));
    const attachments = items.flatMap((item) => (item.kind === "file" && item.id ? [item.id] : []));
    return [{ line, anchors, tags, ...(attachments.length ? { attachments } : {}) }];
  });
}

/**
 * The box whose prompt the server has been asked to take, until it answers (KYO-724). Send
 * posts nothing while one is held here: a prompt pressed three times in a second was posted
 * three times, since nothing recorded the first press until the server's answer came back.
 */
const sendGate = new SendGate<HTMLElement>();

/** Every Send reads as sending, and takes no press, from the press until the server answers. */
function drawSending(): void {
  const busy = sendGate.current !== null;
  for (const button of boxes.querySelectorAll<HTMLButtonElement>(".box-send")) {
    button.disabled = busy;
    button.firstElementChild!.textContent = busy ? "Sending…" : "Send";
  }
}

function attemptSend(editor: EditorView): void {
  // A prompt already on its way to the server is not posted again.
  if (sendGate.current) return;
  const segments = messageSegments(editor);
  if (!segments.length) {
    // A prompt needs some words: the server sends text, and a mark rides on a line of it.
    if (marksOf(editor).items.size) composerNote.textContent = "Add a few words to send this.";
    return;
  }
  if (heldId) {
    composerNote.textContent = "One message is already waiting. Edit it first.";
    return;
  }
  const waiting = Array.from(marksOf(editor).items.values()).flat()
    .some((item) => item.kind === "file" && item.pending);
  if (waiting) {
    composerNote.textContent = "A file is still uploading.";
    return;
  }
  // After the cache has expired the whole context is written again, so Send asks once more.
  const cache = facts?.cache;
  const expired = cache?.expires_at ? new Date(cache.expires_at).getTime() : NaN;
  if (expired < Date.now() && coldConfirm !== editor) {
    const tokens = cache?.recache_tokens_if_cold;
    const went = new Date(expired).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    composerNote.textContent = `The cache went cold at ${went}. Sending rewrites ${
      tokens ? `about ${tokenWords(tokens)} tokens` : "the whole context"}. Send again to confirm.`;
    coldConfirm = editor;
    window.setTimeout(() => { if (coldConfirm === editor) coldConfirm = null; }, 15000);
    return;
  }
  coldConfirm = null;
  if (!sendGate.begin(boxOf(editor))) return;
  sentId = boxIds.get(editor) ?? null;
  composerNote.textContent = "Sending…";
  drawSending();
  // A prompt sent while the session works is held, and is shown in the held box instead.
  if (screenState !== "working") showSentPreview(segments.map((segment) => segment.text).join(""));
  post("send", { segments, marks: sentMarks(editor) });
}

/**
 * KYO-883: a prompt sent straight to an idle session is drawn in the chat at the press,
 * dimmed, rather than when the transcript has it about half a second later. The server's
 * answer undims it, and the transcript's own turn takes its place when it lands.
 */
let sentPreview: { element: HTMLElement; text: string; id?: string; drop?: number; grace?: number } | null = null;
/**
 * How long a queued prompt may wait to be delivered and still count as sent straight away.
 * The server says nothing that tells a prompt about to be typed into an idle session from
 * one waiting for the turn to end, so one not delivered by then is held: its preview goes,
 * and the held box offers Edit and Send now (KYO-883).
 */
const PREVIEW_GRACE_MS = 2500;

/** The id of the prompt last sent from this window, which it has again if it is taken back. */
let sentId: string | null = null;

/** A queued prompt the server says waits on something, or will not try again: a real hold. */
function waitsOnSomething(message: Held | null | undefined): boolean {
  return !!message && (message.retrying === false || Boolean(message.reason && message.detail));
}
/** How long a confirmed preview waits for its turn before it is taken away. */
const SENT_PREVIEW_MS = 60_000;

function showSentPreview(text: string): void {
  dropSentPreview();
  const at = new Date().toISOString();
  const element = drawTurn({ id: "sending", at, text });
  element.classList.add("sending");
  element.dataset.at = at;
  chat.append(element);
  sentPreview = { element, text };
  drawEmptyChat();
  toEnd();
}

function confirmSentPreview(): void {
  if (!sentPreview) return;
  if (sentPreview.grace !== undefined) clearTimeout(sentPreview.grace);
  sentPreview.grace = undefined;
  sentPreview.element.classList.remove("sending");
  sentPreview.drop = window.setTimeout(dropSentPreview, SENT_PREVIEW_MS);
}

/** The previewed prompt was not delivered in the grace period: it is held, and shown as held. */
function heldAfterAll(): void {
  if (!sentPreview?.element.classList.contains("sending")) return;
  dropSentPreview();
  if (held) showHeld(held);
}

function dropSentPreview(): void {
  if (!sentPreview) return;
  if (sentPreview.drop !== undefined) clearTimeout(sentPreview.drop);
  if (sentPreview.grace !== undefined) clearTimeout(sentPreview.grace);
  sentPreview.element.remove();
  sentPreview = null;
}

/**
 * A new prompt of the person's has reached the chat while a preview stands: it is the one
 * just sent, so the preview goes. Its text is not compared, since the server writes lines
 * into a prompt it delivers (an anchor above each anchored paragraph, the held line at the top).
 */
function landSent(): void {
  dropSentPreview();
}

const heldBox = document.getElementById("held")!;
const heldText = document.getElementById("held-text")!;

/** The held message as the server states it: `retrying` is false once it will not be tried again. */
interface Held { id: string; reason?: string | null; detail?: string | null; retrying?: boolean }

let held: Held | null = null;
let heldId: string | null = null;

/**
 * What the held box says. A message the server will not try again is named as that, with
 * what to do about it (KYO-810): a prompt was once typed into the terminal's input box, left
 * unsubmitted there, and shown here as sent. One that waits on something other than the
 * turn says what it waits on, in the server's words.
 */
function heldWords(message: Held): string {
  if (message.retrying === false) {
    return message.reason === "not-submitted"
      ? "Typed into the session's terminal, but not submitted. Open the terminal and press Enter "
        + "there, or choose Edit to take the prompt back."
      : "Not delivered, and part of it may have reached the session's terminal. Look at the "
        + "terminal, then choose Edit to take the prompt back.";
  }
  if (message.reason && message.detail) return `Waiting: ${message.detail}`;
  return screenState === "working" ? "Waiting for the turn to finish" : "Sending…";
}

function showHeld(message: Held | null): void {
  held = message;
  heldId = message?.id ?? null;
  // A prompt drawn in the chat at the press is not shown as held too, unless it waits (KYO-883).
  const previewed = !!message && sentPreview?.id === message.id && !waitsOnSomething(message);
  heldBox.hidden = !message || previewed;
  const stopped = message?.retrying === false;
  // Send now (KYO-722) is offered only while the turn runs; an idle screen delivers by itself.
  document.getElementById("held-send-now")!.hidden = !message || stopped || screenState !== "working";
  heldBox.classList.toggle("problem", stopped || Boolean(message?.reason && message.detail));
  heldText.textContent = message ? heldWords(message) : "";
  heldText.title = message?.detail ?? "";
}

// ------------------------------------------------------- the session's task list (KYO-771)

const agendaPane = document.getElementById("agenda")!;
const agendaWhy = document.getElementById("agenda-why")!;
const agendaList = document.getElementById("agenda-items")!;
const agendaAdd = document.getElementById("agenda-add") as HTMLInputElement;
/** The list row the add field is drawn in, at the end of the person's group (KYO-781.c3). */
const agendaAddRow = document.createElement("li");
agendaAddRow.className = "agenda-add-row";
agendaAddRow.append(agendaAdd);
const agendaNote = document.getElementById("agenda-note")!;
const agendaCompleted = document.getElementById("agenda-completed")!;
agendaCompleted.addEventListener("click", () => {
  agendaShowCompleted = !agendaShowCompleted;
  drawAgenda();
});
/** The list as the app last sent it. */
let agendaFrame: AgendaFrame | null = null;
/** The person's item whose text is open for editing, with the words typed so far. */
let agendaEditing: { id: string; text: string } | null = null;
/** The change whose save is out. Its answer clears the field the change was typed in. */
let agendaSaving: Change | null = null;
/** Whether the checked items are drawn; the list opens without them. */
let agendaShowCompleted = false;

function agendaItemsNow(): AgendaItem[] {
  return agendaFrame?.state === "shown" ? agendaFrame.items : [];
}

/** Saves the list with `change` made to it; false when there was nothing to save. */
function saveAgenda(change: Change): boolean {
  const items = listAfter(agendaItemsNow(), change);
  if (items === null) return false;
  agendaSaving = change;
  agendaNote.hidden = true;
  post("saveAgenda", { items });
  return true;
}

function agendaRow(row: Row): HTMLElement {
  const item = document.createElement("li");
  item.className = `agenda-item${row.mine ? " mine" : ""}${row.checked ? " checked" : ""}`;
  item.dataset.id = row.id;
  const check = document.createElement("input");
  check.type = "checkbox";
  check.checked = row.checked;
  // The orchestrator's item shows its state and takes no press.
  check.disabled = !row.mine;
  check.setAttribute("aria-label", `${row.checked ? "Uncheck" : "Check"}: ${row.text}`);
  check.addEventListener("change", () => {
    saveAgenda({ kind: "check", id: row.id, checked: check.checked });
  });
  item.append(check);
  if (row.mine && agendaEditing?.id === row.id) {
    const field = document.createElement("input");
    field.type = "text";
    field.className = "agenda-edit";
    field.maxLength = 500;
    field.value = agendaEditing.text;
    field.setAttribute("aria-label", "Edit this item");
    const finish = (save: boolean) => {
      const typed = agendaEditing;
      if (!typed || typed.id !== row.id) return;
      // An edit that changes nothing, or is given up, closes; one that is saved stays open
      // with its words until the server has answered.
      if (save && saveAgenda({ kind: "edit", id: row.id, text: typed.text })) return;
      agendaEditing = null;
      drawAgenda();
    };
    field.addEventListener("input", () => {
      if (agendaEditing?.id === row.id) agendaEditing.text = field.value;
    });
    field.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.isComposing) {
        event.preventDefault();
        finish(true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        finish(false);
      }
    });
    field.addEventListener("blur", () => {
      // Leaving the field with the words unchanged closes it. Changed words stay in it.
      if (agendaEditing?.id === row.id && agendaEditing.text === row.text && !agendaSaving) finish(false);
    });
    item.append(field);
  } else {
    const text = document.createElement("span");
    text.className = "agenda-text";
    text.textContent = row.text;
    // A path in a reminder is a link to Files, as one in a reply is (2026-11-11). The path
    // links are declared further down the module, so they are made once it has run.
    queueMicrotask(() => linkQuietly(text));
    if (row.mine) {
      text.title = "Edit";
      text.addEventListener("click", (event) => {
        // A press on a link follows it; the rest of the words open them to edit.
        if (event.target instanceof Element && event.target.closest("a")) return;
        agendaEditing = { id: row.id, text: row.text };
        drawAgenda();
        agendaList.querySelector<HTMLInputElement>(".agenda-edit")?.focus();
      });
    }
    item.append(text);
  }
  const whose = document.createElement("span");
  whose.className = "agenda-whose";
  whose.textContent = row.whose;
  item.append(whose);
  if (row.mine) {
    const remove = document.createElement("button");
    remove.className = "agenda-remove";
    remove.textContent = "×";
    remove.title = "Remove";
    remove.setAttribute("aria-label", `Remove: ${row.text}`);
    remove.addEventListener("click", () => saveAgenda({ kind: "remove", id: row.id }));
    item.append(remove);
  }
  return item;
}

function drawAgenda(): void {
  const view = agendaView(agendaFrame, agendaShowCompleted);
  agendaPane.hidden = !view.shown;
  agendaWhy.hidden = view.why === null;
  agendaWhy.textContent = view.why ?? "";
  agendaAdd.hidden = !view.canAdd;
  // A list redrawn under the caret keeps the caret: a change made elsewhere arrives while
  // the person is typing.
  const open = agendaList.querySelector<HTMLInputElement>(".agenda-edit");
  const caret = open && document.activeElement === open
    ? [open.selectionStart ?? open.value.length, open.selectionEnd ?? open.value.length] : null;
  const drawn = view.rows.map(agendaRow);
  // The person's field for adding stands at the end of their group, above the divider: it is
  // their part of the list (KYO-781.c3). Moving it drops its focus, which is given back.
  const adding = document.activeElement === agendaAdd
    ? [agendaAdd.selectionStart ?? agendaAdd.value.length, agendaAdd.selectionEnd ?? agendaAdd.value.length] : null;
  const mineEnd = view.dividerAt ?? drawn.length;
  if (view.dividerAt !== null) {
    const divider = document.createElement("li");
    divider.className = "agenda-divider";
    divider.setAttribute("role", "separator");
    drawn.splice(view.dividerAt, 0, divider);
  }
  agendaAddRow.hidden = !view.canAdd;
  drawn.splice(mineEnd, 0, agendaAddRow);
  agendaList.replaceChildren(...drawn);
  if (adding) {
    agendaAdd.focus();
    agendaAdd.setSelectionRange(adding[0], adding[1]);
  }
  agendaCompleted.hidden = view.completed === 0;
  agendaCompleted.textContent = `${view.showCompleted ? "Hide" : "Show"} completed (${view.completed})`;
  agendaCompleted.setAttribute("aria-expanded", String(view.showCompleted));
  const again = agendaList.querySelector<HTMLInputElement>(".agenda-edit");
  if (caret && again) {
    again.focus();
    again.setSelectionRange(caret[0], caret[1]);
  }
}

agendaAdd.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.isComposing) return;
  event.preventDefault();
  // The words stay in the field until the server has taken them, and are sent once.
  if (agendaSaving?.kind === "add") return;
  saveAgenda({ kind: "add", text: agendaAdd.value });
});

/** Whether a held message has been asked back and the server has not answered. */
let retracting = false;
/** How long a switch waits on an answer to a retraction before it goes ahead. */
const RETRACT_WAIT_MS = 5000;

document.getElementById("held-edit")!.addEventListener("click", () => {
  if (!heldId) return;
  retracting = true;
  window.setTimeout(() => {
    if (!retracting) return;
    retracting = false;
    switchWhenFree();
  }, RETRACT_WAIT_MS);
  post("retract", { id: heldId });
});

document.getElementById("held-send-now")!.addEventListener("click", () => {
  if (heldId) post("sendNow", { id: heldId });
});

document.getElementById("interrupt")!.addEventListener("click", () => post("interrupt", {}));

const COMPOSER_MIN = 300;
const CHAT_MIN = 420;
const band = document.getElementById("band")!;

function setComposerWidth(width: number): void {
  const clamped = Math.max(COMPOSER_MIN, Math.min(width, window.innerWidth - CHAT_MIN));
  document.documentElement.style.setProperty("--composer-width", `${clamped}px`);
  store(WIDTH_KEY, clamped);
  terminalResized();
}

band.addEventListener("mousedown", (event) => {
  event.preventDefault();
  const move = (moved: MouseEvent) => setComposerWidth(window.innerWidth - moved.clientX);
  const up = () => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
    document.body.style.userSelect = "";
  };
  document.body.style.userSelect = "none";
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
});

const savedWidth = stored<number | null>(WIDTH_KEY, null);
if (savedWidth !== null) setComposerWidth(savedWidth);

// One empty prompt until the app names the project, whose unsent prompts then replace it.
// It is shown without a save, which would replace the session's draft with nothing.
loadingDraft = true;
showTexts([]);
loadingDraft = false;
const newPromptButton = document.getElementById("new-prompt")!;
// The click leaves the focus where it is until the new prompt takes it.
newPromptButton.addEventListener("mousedown", (event) => event.preventDefault());
newPromptButton.addEventListener("click", newPrompt);
// Cmd-Return sends the prompt being typed in, or the only one there is.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || !event.metaKey) return;
  // A key typed in the terminal is the terminal's.
  if (event.target instanceof Node && terminalPane.contains(event.target)) return;
  // KYO-903, KYO-904: only the prompt is sent, and not from a scratchpad, where the key
  // does nothing: the person goes to the prompt to send it.
  if (event.target instanceof Element && event.target.closest(".box.scratch")) return;
  const target = mainEditor();
  if (!target) return;
  event.preventDefault();
  attemptSend(target);
});

document.getElementById("start-button")!.addEventListener("click", () => post("start", {}));

// ------------------------------------------------------------------- the views (KYO-732)

/**
 * The views of the bar above the top bar. Each replaces the pane below it. A view is one
 * entry here, a pane in the page marked `data-view`, and whatever it shows in the top bar and
 * the side bar, marked the same way. The chat's pane is the page itself, which every other
 * pane lies over, so the transcript stays drawn and keeps filling while another view shows.
 */
const VIEWS: { id: string; name: string; icon: keyof typeof ICONS; entered?: () => void }[] = [
  { id: "chat", name: "Chat", icon: "chat" },
  { id: "records", name: "Records", icon: "records", entered: () => enterRecords() },
  { id: "files", name: "Files", icon: "folder", entered: () => enterFiles() },
  { id: "history", name: "History", icon: "history", entered: () => enterHistory() },
];
let currentView = "chat";
/** Where the transcript was when another view replaced it. */
let chatPlace: { top: number; following: boolean } | null = null;

function drawViews(): void {
  document.getElementById("view-items")!.replaceChildren(...VIEWS.map((view) => {
    const item = document.createElement("button");
    item.className = "view-item";
    if (view.id === currentView) item.setAttribute("aria-current", "page");
    item.append(icon(view.icon), view.name);
    // The open view's own name goes to its home, as its Home button does (2026-11-11).
    item.addEventListener("click", () => {
      if (view.id === currentView) homeButtons.get(view.id)?.click();
      else showView(view.id);
    });
    return item;
  }));
}

function showView(id: string): void {
  if (id === currentView) return;
  if (currentView === "chat") chatPlace = { top: scroller.scrollTop, following: atBottom() };
  for (const view of VIEWS) document.body.classList.toggle(`view-${view.id}`, view.id === id);
  closeSearch();
  currentView = id;
  document.querySelectorAll<HTMLElement>("[data-view]").forEach((element) => {
    element.hidden = element.dataset.view !== id;
  });
  drawViews();
  if (id === "chat" && chatPlace) {
    // Nothing was redrawn, so the transcript is where it was left: at its end if it was
    // being followed, which the stream kept extending, and otherwise at the same place.
    scroller.scrollTop = chatPlace.following ? scroller.scrollHeight : chatPlace.top;
  }
  applyDocks();
  VIEWS.find((view) => view.id === id)?.entered?.();
}

// ------------------------------------------------------- the project switcher (KYO-761)

const projectButton = document.getElementById("project-button")!;
const projectName = document.getElementById("project-name")!;
const projectPopup = document.getElementById("project-popup")!;
let projectsFrame: ProjectsFrame | null = null;
/** The project asked for while the composer waits on the server, to turn to once it answers. */
let wanted: { name: string; session: string | null } | null = null;

/**
 * Turns the window to another project, or to a session a link names. The prompts being typed
 * are saved to the session that is left first. While a prompt is on its way to the server,
 * or a held one is being taken back, the answer belongs to this project's composer, so the
 * switch waits for it.
 */
function chooseProject(name: string, session: string | null = null): void {
  closeProjects();
  // The list shown from File > Projects… closes on the project the window already shows.
  if (name === windowProject && !session) closeProjectList();
  if (sendGate.current !== null || retracting) {
    wanted = { name, session };
    return;
  }
  wanted = null;
  if (name === windowProject && !session) return;
  flushDraft();
  post("project", session ? { name, session } : { name });
}

function switchWhenFree(): void {
  if (!wanted || sendGate.current !== null || retracting) return;
  chooseProject(wanted.name, wanted.session);
}

/** The rows of the server's projects, for the dropdown and for the list in place of the chat. */
function projectRows(): HTMLElement[] {
  const rows: HTMLElement[] = [];
  const note = listNote(projectsFrame);
  if (note) {
    const line = document.createElement("div");
    line.className = "project-note";
    line.textContent = note;
    rows.push(line);
  }
  for (const choice of choices(projectsFrame)) {
    const row = document.createElement("button");
    row.className = "project-row";
    row.dataset.project = choice.name;
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", String(choice.current));
    const name = document.createElement("span");
    name.className = "project-row-name";
    name.textContent = choice.name;
    row.append(name);
    if (choice.openable) {
      row.addEventListener("click", () => chooseProject(choice.name));
    } else {
      // Listed with its problem, and it takes no press.
      row.setAttribute("aria-disabled", "true");
      row.tabIndex = -1;
      const problem = document.createElement("span");
      problem.className = "project-row-problem";
      problem.textContent = `Cannot be opened: ${choice.problem}`;
      row.append(problem);
    }
    rows.push(row);
    // KYO-883: with Chat's side bar gone, the window's project lists its live sessions here,
    // when it has more than one, so the chat can follow another.
    if (choice.current) rows.push(...liveSessionRows());
  }
  return rows;
}

function liveSessionRows(): HTMLElement[] {
  const live = (menu?.sessions.sessions ?? []).filter((row) => row.live);
  if (live.length < 2) return [];
  return live.map((row) => {
    const item = document.createElement("button");
    item.className = "project-row project-session";
    item.dataset.session = row.session_id;
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(row.session_id === menu?.current));
    const name = document.createElement("span");
    name.className = "project-row-name";
    name.textContent = sessionLabel(row);
    item.append(name);
    item.addEventListener("click", () => {
      closeProjects();
      showView("chat");
      select(row.session_id);
    });
    return item;
  });
}

function projectsOpen(): boolean {
  return !projectPopup.hidden;
}

function closeProjects(): void {
  if (!projectsOpen()) return;
  projectPopup.hidden = true;
  projectButton.setAttribute("aria-expanded", "false");
}

/**
 * KYO-766: File > Projects… shows the list of projects in place of everything under the top
 * bar, as a first run does, and the same item or Escape returns to what was showing. Nothing
 * of the project is cleared, so its chat, composer and view stand as they were.
 */
function projectListShown(): boolean {
  return document.body.classList.contains("pick-project");
}

function closeProjectList(): void {
  document.body.classList.remove("pick-project");
}

function toggleProjectList(): void {
  // With no project the list already stands there.
  if (!windowProject) return;
  if (projectListShown()) {
    closeProjectList();
    return;
  }
  closeProjects();
  document.body.classList.add("pick-project");
  post("projects", { want: "list" });
  focusProject(windowProject, document.getElementById("projects-list")!)
    || focusProject(null, document.getElementById("projects-list")!);
}

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !projectListShown()) return;
  event.preventDefault();
  closeProjectList();
}, true);

function openProjects(): void {
  projectPopup.hidden = false;
  projectButton.setAttribute("aria-expanded", "true");
  // The list is read again each time it is opened; the rows shown meanwhile are the last read.
  post("projects", { want: "list" });
  focusProject(windowProject) || focusProject(null);
}

/** Moves the focus to a project's row in the open list, or to its first row that takes one. */
function focusProject(name: string | null, within: HTMLElement = projectPopup): boolean {
  const rows = [...within.querySelectorAll<HTMLElement>(".project-row:not([aria-disabled])")];
  const row = name === null ? rows[0] : rows.find((item) => item.dataset.project === name);
  row?.focus();
  return !!row;
}

/** The dropdown, the session links and the list in place of the chat, from the last frames. */
function drawProjects(): void {
  projectName.textContent = windowProject || "Choose a project";
  const focused = document.activeElement instanceof HTMLElement
    ? document.activeElement.dataset.project ?? null : null;
  const holder = document.activeElement?.closest<HTMLElement>("#project-popup, #projects-list");
  projectPopup.replaceChildren(...projectRows());
  document.getElementById("projects-list")!.replaceChildren(...projectRows());
  // A list redrawn while it is in use keeps the row the keys were on.
  if (holder && focused) focusProject(focused, holder);
  document.getElementById("session-links")!.replaceChildren(
    ...sessionLinks(projectsFrame).map((link) => {
      const item = document.createElement("button");
      item.className = "session-link";
      if (link.current) item.setAttribute("aria-current", "true");
      item.textContent = link.project;
      item.title = link.title
        ? `${link.project}: ${link.title}` : `${link.project}'s live session`;
      item.addEventListener("click", () => {
        // A link follows the session, which is read in the chat.
        showView("chat");
        chooseProject(link.project, link.session);
      });
      return item;
    }),
  );
}

projectButton.addEventListener("click", () => (projectsOpen() ? closeProjects() : openProjects()));
document.addEventListener("mousedown", (event) => {
  if (!(event.target instanceof Node)) return;
  if (!document.getElementById("project-switch")!.contains(event.target)) closeProjects();
});
// The arrows and a typed letter move among the rows of whichever list holds the focus, and
// Escape closes the dropdown; Return and Space press the row, as they do any button.
for (const list of [projectPopup, document.getElementById("projects-list")!]) {
  list.addEventListener("keydown", (event) => {
    const rows = [...list.querySelectorAll<HTMLElement>(".project-row:not([aria-disabled])")];
    const at = rows.indexOf(document.activeElement as HTMLElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      rows[(at + step + rows.length) % rows.length]?.focus();
    } else if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey && event.key !== " ") {
      const name = typedChoice(choices(projectsFrame), event.key, rows[at]?.dataset.project ?? null);
      if (name) focusProject(name, list);
    }
  });
}
document.getElementById("project-switch")!.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !projectsOpen()) return;
  event.stopPropagation();
  closeProjects();
  projectButton.focus();
});
projectButton.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowDown") return;
  event.preventDefault();
  if (projectsOpen()) focusProject(windowProject) || focusProject(null);
  else openProjects();
});

// --------------------------------------------------------------------- Records (KYO-732)

const FILTERS_KEY = "records-filters";
const REVEAL_MS = 1600;
const recordsPane = document.getElementById("view-records")!;
const recordsNote = document.getElementById("records-note")!;
const recordsLanding = document.getElementById("records-landing")!;
const recordArticle = document.getElementById("record")!;
const recordsList = document.getElementById("records-list")!;
const recordsHistoryList = document.getElementById("records-history")!;
const recordsTable = document.getElementById("records-table")!;
const recordsHomeSearch = document.getElementById("records-home-search") as HTMLInputElement;

let recordRows: RecordRow[] = [];
/** The shift notes and the briefs as rows of the home's table, and the answers they came from (KYO-869). */
let noteList: RecordRow[] = [];
let briefList: RecordRow[] = [];
let notesAnswer: unknown = null;
let briefsAnswer: unknown = null;
/** Every row a list can name: the records, then the shift notes and the briefs. */
function listedRows(): RecordRow[] {
  return [...recordRows, ...noteList, ...briefList];
}
let readyRecords = new Set<string>();
let awaitingRecords = new Set<string>();
/** Whether the lists have been asked for, and whether an answer has come. */
let listsAsked = false;
let listsRead = false;
let listsProblem: string | null = null;
// KYO-861: only the filter the drawer has a control for is read from what was stored.
const narrowing: Narrowing = { ...storedFilters(stored<unknown>(FILTERS_KEY, null)), search: "" };
const HOME_LOCATION_KEY = "records-home-location";
const HOME_SORT_KEY = "records-home-sort";
/** The Records home (KYO-862): the Location chosen, the column sorted by, and its own search. */
let homeLocation: RecordLocation = locationFrom(stored<unknown>(HOME_LOCATION_KEY, null));
let homeSort: RecordSort = recordSortFrom(stored<unknown>(HOME_SORT_KEY, null));
let homeSearch = "";
const HOME_PLAN_KEY = "records-home-plan";
/** The plan whose members the table shows in place of a Location's records, or null. */
let homePlan: string | null = stored<string | null>(HOME_PLAN_KEY, null);
let viewedRecords: string[] = [];
/** The records opened, in order, with the landing page as home (KYO-738). */
let recordPlaces: History<string> = fresh();
/** The one record that is open, or null on the landing page. */
let openRecordId: string | null = null;
/** The open record as the server last sent it, and the text it was sent as. */
let openRecordDoc: Record<string, unknown> | null = null;
let openRecordText = "";
let openRecordProblem: string | null = null;
/** The records whose dispatches cover the open one, as `kyo show --json` printed them. */
const coveringDocs = new Map<string, Record<string, unknown>>();
const coveringAsked = new Set<string>();
/** An element of the open record to scroll to once it is drawn, such as a criterion. */
let revealRef: string | null = null;
/** The folded texts a reader has opened on the record's page, by item and label. */
const openFolds = new Set<string>();
let landingPlace = 0;

function recordPrefixes(): Set<string> {
  const prefixes = new Set<string>();
  for (const id of [openRecordId, recordRows[0]?.id, recordRows.at(-1)?.id]) {
    const prefix = id ? prefixOf(id) : null;
    if (prefix) prefixes.add(prefix);
  }
  return prefixes;
}

function recordHref(ref: string): string {
  return `kyosei://${encodeURIComponent(menu?.project ?? "project")}/${ref}`;
}

/** A text with the record ids in it drawn as links. */
function linked(text: string, prefixes = recordPrefixes()): DocumentFragment {
  const fragment = document.createDocumentFragment();
  for (const part of refParts(text, prefixes)) {
    if (!part.link) {
      fragment.append(part.text);
      continue;
    }
    const link = document.createElement("a");
    link.className = "ref";
    link.href = recordHref(part.link.ref);
    link.textContent = part.text;
    fragment.append(link);
  }
  return fragment;
}

/** Markdown drawn as the chat draws it, with the record ids in its text made links. */
function prose(source: string): HTMLElement {
  const holder = document.createElement("div");
  holder.className = "reply";
  holder.innerHTML = render(source);
  addCopyButtons(holder);
  const prefixes = recordPrefixes();
  const walker = document.createTreeWalker(holder, NodeFilter.SHOW_TEXT);
  const texts: Text[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!(node.parentElement?.closest("a, button"))) texts.push(node as Text);
  }
  for (const text of texts) {
    const parts = refParts(text.data, prefixes);
    if (parts.some((part) => part.link)) text.replaceWith(linked(text.data, prefixes));
  }
  // KYO-736: a file path on a record's page is a link by the same rule as one in a reply.
  linkQuietly(holder);
  return holder;
}

function kindLabel(kind: string): HTMLElement {
  const label = document.createElement("span");
  label.className = "kind";
  label.dataset.kind = kind;
  label.textContent = kind;
  return label;
}

function span(className: string, text: string): HTMLElement {
  const element = document.createElement("span");
  element.className = className;
  element.textContent = text;
  return element;
}

/** One record in a list. In the side bar the id and kind stand over the title. */
function recordRow(row: RecordRow, place: "side" | "landing"): HTMLElement {
  const item = document.createElement("div");
  item.className = "rec-row";
  if (row.id === openRecordId) item.classList.add("current");
  if (standingOf(row) !== "open") item.classList.add("closed");
  item.dataset.open = row.id;
  const id = span("rec-id", row.id);
  const title = span("rec-title", row.title);
  if (place === "side") {
    const head = document.createElement("div");
    head.className = "rec-row-head";
    head.append(id, kindLabel(row.kind));
    item.append(head, title);
  } else {
    // A row of the home's table (KYO-862): id and title under Name, then one cell a column.
    item.append(
      id, title, kindLabel(row.kind), statusCell(row),
      span("rec-day", dayOf(row.updated_at ?? row.created_at)));
  }
  return item;
}

/** A row's status in the home's table, marked when the record is still outstanding (KYO-883). */
function statusCell(row: RecordRow): HTMLElement {
  const cell = span("rec-status", standingOf(row));
  if (UNSETTLED.has(standingOf(row))) cell.classList.add("outstanding");
  return cell;
}

function emptyRow(text: string): HTMLElement {
  const row = document.createElement("div");
  row.className = "rec-empty";
  row.textContent = text;
  return row;
}

/**
 * Replaces a list's rows and leaves it scrolled where it was. A list that already holds
 * exactly these rows is left alone, so a draw that changes nothing costs no layout (KYO-833).
 */
function fill(holder: HTMLElement, rows: HTMLElement[]): void {
  const held = holder.children;
  if (held.length === rows.length && rows.every((row, index) => held[index] === row)) return;
  const place = holder.scrollTop;
  holder.replaceChildren(...rows);
  holder.scrollTop = place;
}

/**
 * The rows drawn for the list the server last sent, kept between draws (KYO-833). A filter
 * or a search changes which records are shown and nothing a row says, so each record's row
 * is made once for each of the two lists and put back as it is. A new list from the server
 * is new row objects, which are drawn anew.
 */
const keptRows = { side: new WeakMap<RecordRow, HTMLElement>(), landing: new WeakMap<RecordRow, HTMLElement>() };

function keptRow(row: RecordRow, place: "side" | "landing"): HTMLElement {
  let item = keptRows[place].get(row);
  if (!item) {
    item = recordRow(row, place);
    keptRows[place].set(row, item);
  }
  item.classList.toggle("current", row.id === openRecordId);
  return item;
}

/** The list newest first, sorted once for each list the server sends. */
let sortedRows: { from: RecordRow[]; rows: RecordRow[] } | null = null;

function rowsNewestFirst(): RecordRow[] {
  if (sortedRows?.from !== recordRows) sortedRows = { from: recordRows, rows: newestFirst(recordRows) };
  return sortedRows.rows;
}

/**
 * The Kind controls beside the two search fields (KYO-785). The checked kinds are the ones
 * the search text names with `kind:`, so the field and the dropdown are one state.
 */
/** The search text a Kind control stands beside: the home's, or the drawer's (KYO-862). */
function kindSearch(control: Element): { get: () => string; set: (text: string) => void } {
  return control.closest("#records-landing")
    ? { get: () => homeSearch, set: (text) => { homeSearch = text; } }
    : { get: () => narrowing.search, set: (text) => { narrowing.search = text; } };
}

function drawKindControls(): void {
  const present = kindsPresent(recordRows);
  document.querySelectorAll<HTMLElement>(".kind-control").forEach((control) => {
    const search = kindSearch(control);
    const label = kindControlText(search.get(), present);
    const checked = kindsOf(search.get());
    const button = control.querySelector<HTMLButtonElement>(".kind-button")!;
    button.textContent = label.text;
    button.title = label.unknown.length
      ? `No record has the kind ${label.unknown.join(", ")}, so none is shown.` : "";
    control.classList.toggle("active", label.active);
    control.querySelector<HTMLElement>(".kind-clear")!.hidden = !label.active;
    const menu = control.querySelector<HTMLElement>(".kind-menu")!;
    if (menu.dataset.kinds !== present.join(",")) {
      menu.dataset.kinds = present.join(",");
      menu.replaceChildren(...present.map((kind) => {
        const row = document.createElement("label");
        const box = document.createElement("input");
        box.type = "checkbox";
        box.value = kind;
        box.addEventListener("change", () => {
          const now = kindsOf(search.get());
          search.set(withKinds(
            search.get(), box.checked ? [...now.filter((k) => k !== kind), kind] : now.filter((k) => k !== kind)));
          drawRecordLists();
        });
        row.append(box, span("kind", kind));
        (row.lastChild as HTMLElement).dataset.kind = kind;
        return row;
      }));
    }
    menu.querySelectorAll<HTMLInputElement>("input").forEach((box) => { box.checked = checked.includes(box.value); });
  });
}

/** The side bar's list and history, the landing page's groups, and the filters' state. */
function drawRecordLists(): void {
  document.querySelectorAll<HTMLButtonElement>(".chip[data-filter]").forEach((chip) => {
    const name = chip.dataset.filter as "open" | "dispatchable" | "awaiting";
    chip.setAttribute("aria-pressed", String(narrowing[name]));
  });
  document.querySelectorAll<HTMLInputElement>(RECORDS_SEARCH).forEach((field) => {
    if (field.value !== narrowing.search) field.value = narrowing.search;
  });
  drawKindControls();
  const shown = narrow(rowsNewestFirst(), narrowing, readyRecords, awaitingRecords);
  const nothing = !listsRead ? "Reading the records." : "No record matches.";
  // KYO-862: the drawer holds Awaiting you, Recents and All, and the field, Kind and Open
  // only narrow all three. Awaiting you is not shown when nothing in it is left.
  const waiting = shown.filter((row) => awaitingRecords.has(row.id));
  const awaitingList = document.getElementById("records-awaiting")!;
  awaitingList.hidden = !waiting.length;
  document.getElementById("records-awaiting-head")!.hidden = !waiting.length;
  fill(awaitingList, waiting.map((row) => {
    const item = recordRow(row, "side");
    item.classList.toggle("current", row.id === openRecordId);
    return item;
  }));
  const narrowed = narrowing.open || narrowing.search.trim() !== "";
  const recent = narrow(historyRows(viewedRecords, listedRows()), narrowing, readyRecords, awaitingRecords);
  fill(
    recordsHistoryList,
    recent.length
      ? recent.map((row) => recordRow(row, "side"))
      : [emptyRow(viewedRecords.length && narrowed ? nothing : "No record has been viewed.")]);
  fill(recordsList, shown.length ? shown.map((row) => keptRow(row, "side")) : [emptyRow(nothing)]);

  drawRecordsHome(nothing);

  recordsNote.hidden = !listsProblem;
  recordsNote.textContent = listsProblem ? `The records could not be read. ${listsProblem}` : "";
  drawNav("records", openRecordId === null, recordPlaces);
  document.getElementById("records-count")!.textContent = openRecordId !== null || !listsRead
    ? ""
    : homeLocation === "Shift notes" && !chosenPlan()
      ? plural(homeRows().length, "shift note")
      : homeLocation === "Briefs" && !chosenPlan()
        ? plural(homeRows().length, "brief")
        : homeRows().length === recordRows.length
          ? plural(recordRows.length, "record")
          : `${homeRows().length} of ${plural(recordRows.length, "record")}`;
}

/** The table's rows: the chosen Location's records that the home's search leaves, sorted. */
function homeRows(): RecordRow[] {
  const plan = chosenPlan();
  const inLocation = plan
    ? membersOf(rowsNewestFirst(), plan)
    : located(rowsNewestFirst(), readyRecords, awaitingRecords, { notes: noteList, briefs: briefList })[homeLocation];
  const found = narrow(
    inLocation, { open: false, dispatchable: false, awaiting: false, search: homeSearch }, readyRecords);
  return sortRecords(found, homeSort);
}

/** The plan chosen in the side bar while it is still one of the open plans; otherwise the Location stands. */
function chosenPlan(): string | null {
  return homePlan && openPlans(recordRows).some((row) => row.id === homePlan) ? homePlan : null;
}

/** A record named in a short list of the home's side bar: its id over nothing, then its title. */
function homeSideRow(row: RecordRow): HTMLElement {
  const item = document.createElement("div");
  item.className = "rec-row";
  item.dataset.open = row.id;
  item.title = row.title ? `${row.id} ${row.title}` : row.id;
  item.append(span("rec-id", row.id), " ", span("rec-title", row.title));
  return item;
}

const HOME_SIDE_ROWS = 10;

/** The records the chat names, newest mention first, each once. */
function mentionedRecords(): RecordRow[] {
  const ids: string[] = [];
  for (const link of chatLinks) {
    const id = link.kind === "record" ? parseLink(link.href)?.record : null;
    if (id && !ids.includes(id)) ids.push(id);
  }
  return historyRows(ids, listedRows());
}

/** One choice of a home's Location dropdown (KYO-883): its words, its count, and what it sets. */
interface PlaceChoice { label: string; count?: string; data: Record<string, string>; current: boolean }

/**
 * Draws a home's Location dropdown (KYO-883). The button holds every choice's words in one
 * cell with the shown one seen, so it is as wide as the widest and keeps that width; `shown`
 * may be words no choice has, such as a plan's id. `extra` closes the menu, after a rule.
 */
function drawPlaceControl(control: HTMLElement, choices: PlaceChoice[], shown: string, extra: HTMLElement[] = []): void {
  const signature = JSON.stringify([choices, shown, extra.map((item) => item.outerHTML)]);
  if (control.dataset.drawn === signature) return;
  control.dataset.drawn = signature;
  const sizer = span("place-sizer", "");
  const words = [...new Set([...choices.map((choice) => choice.label), shown])];
  sizer.append(...words.map((word) => span(word === shown ? "shown" : "", word)));
  const button = control.querySelector<HTMLButtonElement>(".place-button")!;
  button.replaceChildren(sizer);
  button.title = shown;
  const rows = choices.map((choice) => {
    const row = document.createElement("button");
    row.type = "button";
    row.setAttribute("role", "menuitemradio");
    row.setAttribute("aria-checked", String(choice.current));
    Object.assign(row.dataset, choice.data);
    row.append(span("place-check", choice.current ? "✓" : ""), span("place-label", choice.label));
    if (choice.count !== undefined) row.append(span("location-count", choice.count));
    return row;
  });
  control.querySelector(".place-menu")!.replaceChildren(
    ...rows, ...(extra.length ? [document.createElement("hr"), ...extra] : []));
}

document.querySelectorAll<HTMLElement>(".place-control").forEach((control) => {
  const button = control.querySelector<HTMLButtonElement>(".place-button")!;
  const menu = control.querySelector<HTMLElement>(".place-menu")!;
  const shut = () => { menu.hidden = true; button.setAttribute("aria-expanded", "false"); };
  button.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    button.setAttribute("aria-expanded", String(!menu.hidden));
  });
  // A choice closes the menu; what it sets is done by the handler its data names.
  menu.addEventListener("click", (event) => {
    if ((event.target as Element).closest("button")) shut();
  });
  control.addEventListener("keydown", (event) => { if (event.key === "Escape") shut(); });
  document.addEventListener("mousedown", (event) => { if (!control.contains(event.target as Node)) shut(); });
});

/** The Records home (KYO-862): Locations with their counts, Recents, Mentioned and the table. */
function drawRecordsHome(nothing: string): void {
  const places = located(rowsNewestFirst(), readyRecords, awaitingRecords, { notes: noteList, briefs: briefList });
  fill(document.getElementById("records-locations")!, LOCATIONS.map((name) => {
    const row = document.createElement("div");
    row.className = name === homeLocation && !chosenPlan() ? "location-row current" : "location-row";
    row.dataset.location = name;
    row.append(span("tree-name", name), span("location-count", listsRead ? String(places[name].length) : ""));
    return row;
  }));
  // KYO-883: the same Locations in the dropdown beside the search; a chosen plan is named by its id.
  const plan = chosenPlan();
  drawPlaceControl(document.getElementById("records-place")!, LOCATIONS.map((name) => ({
    label: name,
    count: listsRead ? String(places[name].length) : "",
    data: { location: name },
    current: name === homeLocation && !plan,
  })), plan ?? homeLocation);
  const plans = openPlans(rowsNewestFirst());
  fill(document.getElementById("records-plans")!, plans.length ? plans.map((plan) => {
    const row = document.createElement("div");
    row.className = plan.id === chosenPlan() ? "location-row current" : "location-row";
    row.dataset.plan = plan.id;
    row.title = `${plan.id} ${plan.title}`;
    // KYO-883: the plan's id before its title, as the record lists name a record.
    const name = span("tree-name", "");
    name.append(span("rec-id", plan.id), " ", plan.title);
    row.append(name, span("location-count", String(membersOf(recordRows, plan.id).length)));
    return row;
  }) : [emptyRow(listsRead ? "No plan is open." : "")]);
  const short = (rows: RecordRow[], none: string) => rows.length
    ? rows.slice(0, HOME_SIDE_ROWS).map(homeSideRow) : [emptyRow(none)];
  fill(document.getElementById("records-home-recent")!,
    short(historyRows(viewedRecords, listedRows()), "None yet."));
  fill(document.getElementById("records-home-mentioned")!, short(mentionedRecords(), "None yet."));
  document.querySelectorAll<HTMLElement>(".records-head [data-record-sort]").forEach((button) => {
    if (button.dataset.recordSort === homeSort.by) {
      button.setAttribute("aria-sort", homeSort.descending ? "descending" : "ascending");
    } else button.removeAttribute("aria-sort");
  });
  if (recordsHomeSearch.value !== homeSearch) recordsHomeSearch.value = homeSearch;
  const rows = listsRead ? homeRows() : [];
  // KYO-883: a plan's members still outstanding are marked in red, as the plan's outline marks them.
  document.getElementById("records-landing")!.classList.toggle("plan-chosen", chosenPlan() !== null);
  fill(recordsTable, rows.length
    ? rows.map((row) => keptRow(row, "landing"))
    : [emptyRow(!listsRead ? nothing : homeSearch.trim() ? "No record matches." : "None.")]);
}

/** A Location chosen in the side bar or the dropdown beside the search (KYO-883). */
function onLocationChosen(event: Event): void {
  const row = (event.target as Element).closest<HTMLElement>("[data-location]");
  if (!row) return;
  homeLocation = locationFrom(row.dataset.location);
  homePlan = null;
  store(HOME_LOCATION_KEY, homeLocation);
  store(HOME_PLAN_KEY, null);
  drawRecordLists();
  recordsPane.scrollTop = 0;
}
document.getElementById("records-locations")!.addEventListener("click", onLocationChosen);
document.querySelector("#records-place .place-menu")!.addEventListener("click", onLocationChosen);

document.getElementById("records-plans")!.addEventListener("click", (event) => {
  const row = (event.target as Element).closest<HTMLElement>("[data-plan]");
  if (!row) return;
  homePlan = row.dataset.plan ?? null;
  store(HOME_PLAN_KEY, homePlan);
  drawRecordLists();
  recordsPane.scrollTop = 0;
});

document.querySelector(".records-head")!.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLElement>("[data-record-sort]");
  if (!button) return;
  homeSort = pressedSort(homeSort, button.dataset.recordSort as RecordSortBy);
  store(HOME_SORT_KEY, homeSort);
  drawRecordLists();
});

recordsHomeSearch.addEventListener("input", () => {
  homeSearch = recordsHomeSearch.value;
  drawRecordLists();
});
recordsHomeSearch.addEventListener("keydown", (event) => {
  if (event.key === "Escape") recordsHomeSearch.blur();
});

/** The open record's outline in the right drawer: its sections, each listing its items. */
/** The sections the person folded; every other one lists its items (KYO-846). */
const foldedOutline = new Set<string>();
/** The states of an item that is still to be dealt with: an open gate, a member under way. */
const UNSETTLED = new Set(["open", "working", "built", "verified"]);
function drawRecordOutline(): void {
  const holder = document.getElementById("record-outline")!;
  if (openRecordId === null || !openRecordDoc) {
    holder.replaceChildren();
    return;
  }
  const rows: HTMLElement[] = [];
  const title = recordArticle.querySelector("h1");
  if (title) {
    const row = document.createElement("div");
    row.className = "outline-row";
    row.textContent = openRecordId;
    row.addEventListener("click", () => title.scrollIntoView({ block: "start" }));
    rows.push(row);
  }
  for (const heading of Array.from(recordArticle.querySelectorAll<HTMLElement>("h2"))) {
    const name = heading.firstChild?.textContent ?? heading.textContent ?? "";
    const items: HTMLElement[] = [];
    for (let next = heading.nextElementSibling; next && next.tagName !== "H2"; next = next.nextElementSibling) {
      if (next.classList.contains("rec-item")) items.push(next as HTMLElement);
    }
    const line = document.createElement("div");
    line.className = "outline-section";
    if (!foldedOutline.has(name)) line.classList.add("open");
    const caret = span("chevron", items.length ? "›" : "");
    const label = document.createElement("div");
    label.className = "outline-row";
    label.textContent = `${name} ${heading.querySelector("small")?.textContent ?? ""}`.trim();
    label.addEventListener("click", () => heading.scrollIntoView({ block: "start" }));
    line.append(caret, label);
    const list = document.createElement("div");
    list.className = "outline-items";
    list.hidden = foldedOutline.has(name);
    for (const item of items) {
      // One line: the item's short id, as much of its text as fits, and its state at the
      // end, in red while the item is still open (a gate, a plan's member).
      const row = document.createElement("div");
      row.className = "outline-row outline-item";
      const ref = (item.dataset.ref ?? "").replace(`${openRecordId}.`, "");
      const state = item.querySelector(".rec-state")?.textContent ?? "";
      const text = (item.querySelector(":scope > .rec-text")?.textContent ?? "").trim().split("\n")[0];
      if (ref) row.append(span("outline-ref", text ? `${ref}:` : ref));
      if (text) row.append(span("outline-text", text));
      if (state) row.append(span(UNSETTLED.has(state) ? "outline-state open" : "outline-state", state));
      if (!row.childNodes.length) row.textContent = firstWords(item.textContent ?? "", 6);
      row.title = [ref, text, state].filter(Boolean).join(" · ");
      row.addEventListener("click", () => item.scrollIntoView({ block: "center" }));
      list.append(row);
    }
    caret.addEventListener("click", () => {
      if (foldedOutline.has(name)) foldedOutline.delete(name);
      else foldedOutline.add(name);
      line.classList.toggle("open", !foldedOutline.has(name));
      list.hidden = foldedOutline.has(name);
    });
    rows.push(line, list);
  }
  holder.replaceChildren(...rows);
}

function recordLine(line: Line, wide: boolean): HTMLElement {
  const row = document.createElement("div");
  row.className = wide ? "rec-line wide" : "rec-line";
  // An entry of a report is anchored by its own id, as a criterion is (KYO-757), and is
  // found by that id when a link or a chip names it.
  if (line.ref) row.dataset.recordRef = row.dataset.ref = line.ref;
  const label = span("rec-label", "");
  label.append(linked(line.label));
  const text = span(line.ref ? "rec-line-text rec-text" : "rec-line-text", "");
  text.append(linked(line.text));
  row.append(label, text);
  return row;
}

function recordItem(section: string, item: Item): HTMLElement {
  const element = document.createElement("div");
  element.className = "rec-item";
  // What a prompt can cite carries its id, for the composer to anchor to.
  if (item.ref && item.citable) element.dataset.recordRef = item.ref;
  if (item.ref) element.dataset.ref = item.ref;
  const head = document.createElement("div");
  head.className = "rec-item-head";
  if (item.ref) head.append(span("ref-id", item.ref));
  if (item.state) head.append(span(`rec-state ${item.tone ?? "plain"}`, item.state));
  if (item.meta?.length) {
    const meta = span("rec-meta", "");
    meta.append(linked(item.meta.join(" · ")));
    head.append(meta);
  }
  if (head.childNodes.length) element.append(head);
  if (item.text) {
    // Paths and one-line names are drawn as they are; sentences are markdown, as written.
    const plain = section === "Touches" || section === "Members" || section === "Links";
    const text = plain ? document.createElement("div") : prose(item.text);
    if (plain) text.append(linked(item.text));
    text.classList.add("rec-text");
    if (plain) text.classList.add("plain");
    element.append(text);
  }
  if (item.attached) {
    // KYO-856: evidence that names a composer attachment draws the file, fetched by its id
    // through the attachment route of the session it is kept under.
    const { line, name, id, extension, session } = item.attached;
    const holder = document.createElement("div");
    holder.className = "rec-text rec-attached";
    holder.append(drawAttached(line, name, id, extension, session));
    element.append(holder);
  }
  // A superseded report keeps its lines, folded under a label that says so.
  let into: HTMLElement = element;
  if (item.collapsed && item.lines?.length) {
    const key = `${item.ref ?? section}/${item.collapsed}`;
    const details = document.createElement("details");
    details.className = "rec-superseded";
    const summary = document.createElement("summary");
    summary.textContent = item.collapsed;
    details.append(summary);
    details.open = openFolds.has(key);
    details.addEventListener("toggle", () => {
      if (details.open) openFolds.add(key);
      else openFolds.delete(key);
    });
    element.append(details);
    into = details;
  }
  for (const line of item.lines ?? []) {
    if (line.group) {
      const heading = document.createElement("div");
      heading.className = "rec-group";
      heading.textContent = line.group;
      into.append(heading);
    }
    into.append(recordLine(line, section === "Reports" && line.label.includes(" · ")));
  }
  for (const folded of item.folded ?? []) {
    const key = `${item.ref ?? section}/${folded.label}`;
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = folded.label;
    details.append(summary);
    // A brief is long, so it is drawn when it is first opened.
    const fill = () => {
      unfilled.delete(details);
      if (details.childElementCount === 1) details.append(prose(folded.text));
    };
    unfilled.set(details, fill);
    const draw = () => {
      if (details.open) fill();
      if (details.open) openFolds.add(key);
      else openFolds.delete(key);
    };
    details.addEventListener("toggle", draw);
    if (openFolds.has(key)) {
      details.open = true;
      draw();
    }
    element.append(details);
  }
  // The paths in what is drawn as it was written: a touch, a reason, a report's evidence.
  linkQuietly(element);
  return element;
}

/** A fold whose body is not yet drawn, with what draws it: it is drawn when first opened, and a search draws it shut (KYO-754). */
const unfilled = new WeakMap<Element, () => void>();

function drawRecord(): void {
  recordsLanding.hidden = openRecordId !== null;
  recordArticle.hidden = openRecordId === null;
  // The drawer is away on the home and back beside a record, read or still being read (KYO-862).
  applyDocks();
  if (openRecordId === null) {
    recordArticle.replaceChildren();
    drawRecordOutline();
    return;
  }
  const place = recordsPane.scrollTop;
  if (!openRecordDoc) {
    recordArticle.replaceChildren(emptyRow(
      openRecordProblem
        ? `${openRecordId} could not be read. ${openRecordProblem}`
        : `Reading ${openRecordId}.`));
    return;
  }
  const titles = new Map(recordRows.map((row) => [row.id, row.title]));
  // A shift note and a brief are no records and are drawn as pages of their own (KYO-869).
  const isNote = openRecordDoc.object === "shift-note";
  const isBrief = !isNote && typeof openRecordDoc.run === "object" && openRecordDoc.run !== null;
  const handoffFor = isNote
    ? ((notesAnswer as { notes?: { id?: string; handoff_for?: string | null }[] } | null)?.notes ?? [])
      .find((note) => note.id === openRecordId)?.handoff_for ?? null
    : null;
  const page = isNote ? notePage(openRecordDoc, handoffFor)
    : isBrief ? briefPage(openRecordDoc) : recordPage(openRecordDoc, titles, coveringDocs);
  // The session a note was written in, or a brief opened, as a link to it in History.
  const pageSession = isNote ? sessionAt(briefsAnswer, openRecordDoc.created_at)
    : isBrief ? ((openRecordDoc.run as { session_id?: string | null }).session_id ?? null) : null;
  const head = document.createElement("div");
  head.className = "record-head";
  head.dataset.recordRef = page.id;
  head.append(span("ref-id", page.id), kindLabel(page.kind), span("rec-status", page.status));
  const title = document.createElement("h1");
  title.textContent = page.title;
  const facts = document.createElement("dl");
  facts.className = "record-facts";
  for (const fact of page.facts) {
    const term = document.createElement("dt");
    term.textContent = fact.label;
    const value = document.createElement("dd");
    value.append(linked(fact.text));
    facts.append(term, value);
  }
  if (pageSession) {
    const term = document.createElement("dt");
    term.textContent = "session";
    const value = document.createElement("dd");
    const link = document.createElement("a");
    link.className = "session-link";
    link.href = "#";
    link.textContent = sessionTitle(pageSession);
    link.title = "Open this session in History";
    link.addEventListener("click", (event) => {
      event.preventDefault();
      showView("history");
      openMessage(pageSession, "", "");
    });
    value.append(link);
    facts.append(term, value);
  }
  const parts: HTMLElement[] = [head, title, facts];
  if (page.description) parts.push(prose(page.description));
  for (const section of page.sections) {
    const heading = document.createElement("h2");
    const count = document.createElement("small");
    count.textContent = section.count;
    heading.append(section.name, count);
    parts.push(heading, ...section.items.map((item) => recordItem(section.name, item)));
  }
  if (page.none.length) {
    const none = document.createElement("div");
    none.className = "record-none";
    none.textContent = `This record has no ${page.none.join(", ").toLowerCase()}.`;
    parts.push(none);
  }
  recordArticle.replaceChildren(...parts);
  drawRecordOutline();
  applyDocks();
  recordsPane.scrollTop = place;
  if (revealRef) {
    const target = [...recordArticle.querySelectorAll<HTMLElement>("[data-ref]")]
      .find((element) => element.dataset.ref === revealRef);
    revealRef = null;
    if (target) {
      // A line of a superseded report is inside its fold, which is opened to show it.
      const fold = target.closest("details");
      if (fold) fold.open = true;
      target.scrollIntoView({ block: "center" });
      target.classList.add("revealed");
      setTimeout(() => target.classList.remove("revealed"), REVEAL_MS);
    }
  }
}

function enterRecords(): void {
  if (!listsAsked) {
    listsAsked = true;
    post("records", { want: "lists" });
  }
  drawRecordLists();
  drawRecord();
}

/**
 * Opens a record in the Records view, in place of the one that was open: one record is
 * open at a time, whichever list, page or chat link it was opened from.
 */
function openRecord(target: LinkTarget, stepping = false): void {
  if (!stepping) recordPlaces = visitPlace(recordPlaces, target.record);
  if (openRecordId === null) landingPlace = recordsPane.scrollTop;
  const changed = openRecordId !== target.record;
  if (changed) {
    openRecordId = target.record;
    openRecordDoc = null;
    openRecordText = "";
    openRecordProblem = null;
    openFolds.clear();
    coveringDocs.clear();
    coveringAsked.clear();
  }
  revealRef = target.ref !== target.record ? target.ref : null;
  viewedRecords = visit(viewedRecords, target.record);
  post("records", { want: "history", ids: viewedRecords });
  post("records", { want: "record", id: target.record });
  showView("records");
  drawRecordLists();
  drawRecord();
  if (changed) recordsPane.scrollTop = 0;
}

/** Back to the landing page, where it was left; the places behind stay. */
function closeRecord(stepping = false): void {
  if (!stepping) recordPlaces = visitPlace(recordPlaces, null);
  openRecordId = null;
  openRecordDoc = null;
  openRecordText = "";
  openRecordProblem = null;
  revealRef = null;
  drawRecordLists();
  drawRecord();
  recordsPane.scrollTop = landingPlace;
}

/** Shows the place the history now stands on, without adding to it. */
function showRecordPlace(): void {
  const place = current(recordPlaces);
  if (place === null) closeRecord(true);
  else openRecord({ record: place, ref: place }, true);
}

const recordFrames = {
  /** The project the Records view shows, and the history the app kept for it. */
  records(frame: { project?: string; history?: unknown }): void {
    recordRows = [];
    noteList = [];
    briefList = [];
    notesAnswer = briefsAnswer = null;
    readyRecords = new Set();
    awaitingRecords = new Set();
    listsRead = false;
    listsProblem = null;
    viewedRecords = historyFrom(frame.history);
    recordPlaces = fresh();
    openRecordId = null;
    openRecordDoc = null;
    openRecordText = "";
    landingPlace = 0;
    // A view already showing reads the new project; one not yet opened reads when it is.
    listsAsked = currentView === "records";
    if (listsAsked) post("records", { want: "lists" });
    drawRecordLists();
    drawRecord();
  },

  /** `kyo list`, `kyo next` and the gates waiting on the person, each null when unread. */
  recordLists(frame: {
    list?: { records?: RecordRow[] } | null; next?: unknown; gates?: unknown; error?: string | null;
    notes?: unknown; briefs?: unknown;
  }): void {
    // The shift log and the briefs, each left as it was when its read failed.
    if (frame.notes) {
      notesAnswer = frame.notes;
      noteList = noteRows(frame.notes);
    }
    if (frame.briefs) {
      briefsAnswer = frame.briefs;
      briefList = briefRows(frame.briefs);
    }
    listsProblem = frame.error ?? null;
    // A read that failed leaves the lists as they were.
    if (Array.isArray(frame.list?.records)) {
      recordRows = frame.list.records;
      listsRead = true;
    }
    if (frame.next) readyRecords = dispatchableIds(frame.next);
    if (frame.gates) awaitingRecords = awaitingIds(frame.gates);
    drawRecordLists();
    // A link or a member on the open page is named by the list's titles.
    if (openRecordDoc) drawRecord();
    // So is a record linked in the chat, in the chat's side bar.
    drawChatLinks();
  },

  /** One record as `kyo show --json` prints it, or why it could not be read. */
  record(frame: { id: string; document?: Record<string, unknown> | null; error?: string | null }): void {
    // A record whose dispatch covers the open one: its report is drawn on the open page.
    if (frame.id !== openRecordId && coveringAsked.has(frame.id)) {
      if (frame.document) {
        coveringDocs.set(frame.id, frame.document);
        if (openRecordDoc) drawRecord();
      }
      return;
    }
    if (frame.id !== openRecordId) return;
    if (!frame.document) {
      // A refresh that failed leaves the page as it was.
      if (!openRecordDoc) {
        openRecordProblem = frame.error ?? "The server sent no record.";
        drawRecord();
      }
      return;
    }
    const text = JSON.stringify(frame.document);
    if (text === openRecordText && !revealRef) return;
    openRecordDoc = frame.document;
    openRecordText = text;
    openRecordProblem = null;
    // The dispatches of other records that cover this one hold the report it is owed.
    const covered = (frame.document as { covered_by_dispatches?: { primary_record_id?: unknown }[] })
      .covered_by_dispatches;
    for (const entry of Array.isArray(covered) ? covered : []) {
      const primary = entry?.primary_record_id;
      if (typeof primary === "string" && primary !== openRecordId && !coveringAsked.has(primary)) {
        coveringAsked.add(primary);
        post("records", { want: "record", id: primary });
      }
    }
    drawRecord();
  },

  /** The project's event stream reported a change: read again what is being shown. */
  recordsChanged(frame: { kind?: string }): void {
    if (frame.kind && frame.kind !== "store") return;
    filesChanged();
    if (listsAsked) post("records", { want: "lists" });
    if (openRecordId !== null) post("records", { want: "record", id: openRecordId });
  },
};

// The find bar's field and the History and Files search fields are drawn as this one is and
// are no filter of the record lists (KYO-754, KYO-851).
document.querySelectorAll<HTMLInputElement>(RECORDS_SEARCH).forEach((field) => {
  field.addEventListener("input", () => {
    narrowing.search = field.value;
    drawRecordLists();
  });
  field.addEventListener("keydown", (event) => {
    if (event.key === "Escape") field.blur();
  });
});

document.querySelectorAll<HTMLElement>(".kind-control").forEach((control) => {
  const button = control.querySelector<HTMLButtonElement>(".kind-button")!;
  const menu = control.querySelector<HTMLElement>(".kind-menu")!;
  const shut = () => { menu.hidden = true; button.setAttribute("aria-expanded", "false"); };
  button.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    button.setAttribute("aria-expanded", String(!menu.hidden));
  });
  control.querySelector(".kind-clear")!.addEventListener("click", () => {
    const search = kindSearch(control);
    search.set(withKinds(search.get(), []));
    shut();
    drawRecordLists();
  });
  control.addEventListener("keydown", (event) => { if (event.key === "Escape") shut(); });
  document.addEventListener("mousedown", (event) => { if (!control.contains(event.target as Node)) shut(); });
});

document.querySelectorAll<HTMLButtonElement>(".chip[data-filter]").forEach((chip) => {
  chip.addEventListener("click", () => {
    const name = chip.dataset.filter as "open" | "dispatchable" | "awaiting";
    Object.assign(narrowing, pressed(narrowing, name));
    store(FILTERS_KEY, {
      open: narrowing.open, dispatchable: narrowing.dispatchable, awaiting: narrowing.awaiting,
    });
    drawRecordLists();
  });
});

navIcons("records", {
  home: () => closeRecord(),
  back: () => { recordPlaces = stepBack(recordPlaces); showRecordPlace(); },
  forward: () => { recordPlaces = stepForward(recordPlaces); showRecordPlace(); },
});

// KYO-737: a right-click on a citable element of the open record, or a selection within
// one, offers Anchor as the chat does. The anchor is to the element, by its id.
recordArticle.addEventListener("contextmenu", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  const element = target?.closest<HTMLElement>("[data-record-ref]");
  const id = element?.dataset.recordRef;
  const own = ownSelection();
  if (!own) window.getSelection()?.removeAllRanges();
  if (!element || !id) return;
  event.preventDefault();
  const text = element.classList.contains("record-head")
    ? recordArticle.querySelector("h1")?.textContent ?? ""
    : element.querySelector(".rec-text")?.textContent ?? "";
  const words = text.trim() ? `${id} ${firstWords(text, ANCHOR_WORDS)}` : id;
  showAnchorMenu(event.clientX, event.clientY, { kind: "record", id, words });
});

// A record is opened by a row of a list, a link on a record's page, or a kyosei:// link in
// the chat, which the page would otherwise have nowhere to follow.
document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  const link = target?.closest<HTMLAnchorElement>("a[href]");
  const href = link?.getAttribute("href") ?? "";
  if (/^\s*kyosei:/i.test(href)) {
    event.preventDefault();
    const found = parseLink(href);
    if (found) openRecord(found);
    return;
  }
  const row = target?.closest<HTMLElement>("[data-open]");
  if (!row?.dataset.open) return;
  // Dragging across a row to select its id is not a press of the row.
  const selection = getSelection();
  if (selection && !selection.isCollapsed && row.contains(selection.anchorNode)) return;
  openRecord({ record: row.dataset.open, ref: row.dataset.open });
});

// ------------------------------------------------------- file paths as links (KYO-736)

/** What the server made of each path asked, and when it said so. */
const pathAnswers = new Map<string, { answer: Answer; at: number }>();
/** The paths asked and not yet answered, those still to ask, and how many are out. */
const pathsAsked = new Set<string>();
const pathQueue: string[] = [];
let pathsOut = 0;
const PATHS_AT_ONCE = 4;
/** Set when the server refuses the device's token, after which nothing more is asked. */
let pathsRefused = false;
/** The drawn elements that hold a path still unanswered, by the path. */
const pathHolders = new Map<string, Set<HTMLElement>>();
/** Whether a drawn element's files count as mentioned in the session. */
const pathContext = new WeakMap<HTMLElement, { mention: boolean }>();
const relinkWaiting = new Set<HTMLElement>();
let relinkTimer: number | undefined;
let mentions: Mention[] = [];
let chatLinks: ChatLink[] = [];
let allLinks = false;
const LINKS_SHOWN = 6;
const PATH_SKIPPED = "a, button, pre, summary, textarea, .katex, .thinking";

function knownPath(given: string): Answer | null {
  const known = pathAnswers.get(given);
  return known && stands(known.answer, known.at, Date.now()) ? known.answer : null;
}

function sendPathQuestions(): void {
  while (pathsOut < PATHS_AT_ONCE && pathQueue.length) {
    // The path asked last is the one drawn last, which is the one nearest the reader.
    const given = pathQueue.pop()!;
    pathsOut += 1;
    post("files", { want: "resolve", given });
  }
}

/** Asks the server where a path is, once, and notes the element to draw again when it says. */
function askPath(given: string, holder: HTMLElement): void {
  if (pathsRefused) return;
  if (!pathHolders.has(given)) pathHolders.set(given, new Set());
  pathHolders.get(given)!.add(holder);
  if (pathsAsked.has(given)) return;
  pathsAsked.add(given);
  pathQueue.push(given);
  sendPathQuestions();
}

function pathTitle(answer: Answer): string {
  if (answer.kind === "file") {
    return `${rootLabel(answer.root)}: ${answer.path || "/"}${answer.line ? `, line ${answer.line}` : ""}`;
  }
  return answer.kind === "outside" ? `${answer.path}\nOutside the folders the server serves` : "";
}

/** Makes a link of an answered path, and counts a served file as mentioned. */
function dressPathLink(link: HTMLAnchorElement, given: string, answer: Answer, holder: HTMLElement): void {
  link.setAttribute("href", "#");
  link.dataset.given = given;
  link.className = answer.kind === "file" ? "path-link" : "path-link outside";
  link.title = pathTitle(answer);
  if (answer.kind === "file" && pathContext.get(holder)?.mention && answer.type !== "dir") {
    mentions = mentioned(mentions, {
      root: answer.root, path: answer.path, line: answer.line,
      at: holder.dataset.at ?? holder.closest<HTMLElement>("[data-at]")?.dataset.at ?? "",
      entry: entryOf(answer),
    });
    drawMentionsSoon();
  }
}

/**
 * Draws the file paths in an element as links (KYO-736): the words that read as a path, in
 * text and in code spans, and the markdown links whose address has no scheme. A path is asked
 * of the server once and drawn as a link only when the server places it, so the element is
 * drawn first and linked when the answer comes. Fenced code blocks are left as they are.
 */
function linkPaths(holder: HTMLElement): void {
  for (const link of Array.from(holder.querySelectorAll<HTMLAnchorElement>("a[data-local]"))) {
    const given = hrefPath(link.dataset.local ?? "");
    if (!given) continue;
    const answer = knownPath(given);
    if (!answer) {
      askPath(given, holder);
    } else if (answer.kind !== "none") {
      link.removeAttribute("data-local");
      dressPathLink(link, given, answer, holder);
    }
  }
  // A bare file name such as README.md is made a web address by the renderer, since `.md`
  // and `.py` are also countries' domains. When the server holds a file of that name, it is
  // the file that was meant.
  for (const link of Array.from(holder.querySelectorAll<HTMLAnchorElement>('a[href^="http://"]'))) {
    const given = link.textContent ?? "";
    if (link.getAttribute("href") !== `http://${given}` || !/^[\w.+-]+\.[A-Za-z0-9]+$/.test(given)) continue;
    const answer = knownPath(given);
    if (!answer) askPath(given, holder);
    else if (answer.kind === "file") dressPathLink(link, given, answer, holder);
  }
  const walker = document.createTreeWalker(holder, NodeFilter.SHOW_TEXT);
  const texts: Text[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.parentElement?.closest(PATH_SKIPPED)) texts.push(node as Text);
  }
  for (const text of texts) {
    const data = text.data;
    if (!data.includes("/")) continue;
    const whole = text.parentElement?.tagName === "CODE" ? wholePath(data) : null;
    const from = whole ? data.indexOf(whole) : 0;
    const spans = whole ? [{ from, to: from + whole.length, text: whole }] : pathSpans(data);
    const parts: (string | HTMLElement)[] = [];
    let at = 0;
    let made = false;
    for (const span of spans) {
      const answer = knownPath(span.text);
      if (!answer) {
        askPath(span.text, holder);
        continue;
      }
      if (answer.kind === "none" || (answer.kind === "outside" && !looksOutside(answer.path))) continue;
      const link = document.createElement("a");
      link.textContent = data.slice(span.from, span.to);
      dressPathLink(link, span.text, answer, holder);
      parts.push(data.slice(at, span.from), link);
      at = span.to;
      made = true;
    }
    if (made) text.replaceWith(...parts, data.slice(at));
  }
}

/** Links a placed transcript element's paths, once for each time it is built. */
function linkPlaced(key: string): void {
  const element = elements.get(key);
  if (!element || pathContext.has(element)) return;
  pathContext.set(element, { mention: true });
  linkPaths(element);
}

/** Links the paths of an element of a record's page or a file, which mention nothing. */
function linkQuietly(holder: HTMLElement): void {
  pathContext.set(holder, { mention: false });
  linkPaths(holder);
}

function relinkSoon(holders: Iterable<HTMLElement>): void {
  for (const holder of holders) relinkWaiting.add(holder);
  if (relinkTimer !== undefined) return;
  relinkTimer = window.setTimeout(() => {
    relinkTimer = undefined;
    const waiting = [...relinkWaiting];
    relinkWaiting.clear();
    let inChat = false;
    for (const holder of waiting) {
      linkPaths(holder);
      inChat ||= chat.contains(holder);
    }
    // A link splits the text it is made in, which the painted anchors were measured against.
    if (inChat) {
      paintAnchors();
      drawChatLinksSoon();
    }
  }, 0);
}

function copyText(text: string): void {
  const native = (window as any).webkit?.messageHandlers?.copy;
  if (native) native.postMessage({ text });
  else void navigator.clipboard?.writeText(text);
}

// ---- a path outside every root: what the app can still do with it

const pathMenu = document.createElement("div");
pathMenu.id = "path-menu";
/** The server's machine is this one, and the mapping Settings holds for when it is not. */
let sameMachine = false;
let pathMappings: Mapping[] = [];
/** The place on this machine the open menu's path would have, while the app checks for it. */
let menuPlace: string | null = null;

function showPathMenu(x: number, y: number, path: string): void {
  const heading = document.createElement("div");
  heading.className = "anchor-heading path-menu-path";
  heading.textContent = path;
  pathMenu.replaceChildren(
    heading,
    boxButton("", "Copy path", () => {
      pathMenu.remove();
      copyText(path);
    }),
  );
  document.body.append(pathMenu);
  pathMenu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - pathMenu.offsetWidth - 8))}px`;
  pathMenu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - pathMenu.offsetHeight - 8))}px`;
  // Reveal in Finder is offered once the app says the file is on this machine.
  menuPlace = localPath(path, sameMachine, pathMappings);
  if (menuPlace) post("files", { want: "exists", path: menuPlace });
}

document.addEventListener("mousedown", (event) => {
  if (!(event.target instanceof Node) || !pathMenu.contains(event.target)) pathMenu.remove();
}, true);

// KYO-883: the open file's path in the top bar offers the path's menu: Copy path, and Reveal
// in Finder when the file is on this machine.
document.getElementById("files-path")!.addEventListener("contextmenu", (event) => {
  if (!openFile) return;
  event.preventDefault();
  showPathMenu(event.clientX, event.clientY,
    serverPath(fileRoots, openFile.root, openFile.path) ?? openFile.path);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") pathMenu.remove();
});

// ----------------------------------------------------------------------- Files (KYO-736)

const FILES_ROOT_KEY = "files-root";
const FILES_DOTFILES_KEY = "files-dotfiles";
const FILES_FOLDED_KEY = "files-search-folded";
/** How often the open file is asked for again, and every how many times the tree is too. */
const FILE_REFRESH_MS = 3000;
const TREE_REFRESH_EVERY = 4;
/** One line of a source file, in pixels, which a line's place is measured by. */
const SOURCE_LINE_PX = 19;
const SOURCE_PADDING_PX = 12;
const filesPane = document.getElementById("view-files")!;
const filesNote = document.getElementById("files-note")!;
const filesLanding = document.getElementById("files-landing")!;
const fileArticle = document.getElementById("file")!;

interface OpenFile {
  root: string;
  path: string;
  line: number | null;
  /** The last line of the span the file was opened at, when it is more than one (KYO-737). */
  lineEnd: number | null;
  /** Set until the line asked for has been scrolled to. */
  lineWaits: boolean;
  entry: Entry | null;
  /** Whether the server has been asked for the file, or need not be. */
  asked: boolean;
  etag: string | null;
  text: string | null;
  data: string | null;
  mediaType: string;
  problem: string | null;
  /** Whether the reader asked for all of a long text. */
  whole: boolean;
}

let filesProject: string | null = null;
/** The roots document as the server sent it, and the roots it gives the followed session. */
let rootsDocument: unknown = null;
let fileRoots: RootInfo[] = [];
/** The server's sentence for a root whose tree it refused, shown in the tree's place. */
const rootProblems = new Map<string, string>();
/** The session whose scratchpad the Scratch root shows. */
let filesSession: string | null = null;
let rootsAsked = false;
let rootsRead = false;
let rootsProblem: string | null = null;
let chosenRoot = stored<string>(FILES_ROOT_KEY, "code");
let showDotfiles = stored<boolean>(FILES_DOTFILES_KEY, false) === true;
/** The Files search (KYO-814): the text asked about, and each root's answer as it comes. */
let filesQuery = "";
const filesFound = new Map<string, { files: FoundFile[]; more: boolean } | "waiting" | { problem: string }>();
/** Which of the search's two sections are folded, and which contents files show their lines. */
const foldedSections = new Set<string>(stored<string[]>(FILES_FOLDED_KEY, []) ?? []);
const openFound = new Set<string>();
let filesSearchTimer: number | undefined;
/** The files opened in the app for the project, on any device, newest first (KYO-814). */
let recentFiles: Recent[] = [];
const RECENT_SHOWN = 12;
const listings = new Map<string, Entry[]>();
const listingTexts = new Map<string, string>();
const openDirs = new Set<string>();
const askedDirs = new Set<string>();
let openFile: OpenFile | null = null;
/** The files opened, in order, with the file browser as home (KYO-738). */
let fileHistory: History<{ root: string; path: string }> = fresh();
const sameFile = (a: { root: string; path: string }, b: { root: string; path: string }) =>
  a.root === b.root && a.path === b.path;
/** The images a markdown file shows, as data, by root and path. */
const fileImages = new Map<string, string>();
let filesTicks = 0;

function shownRoot(): string | null {
  return rootShown(chosenRoot, fileRoots);
}

/** A request for a root's tree or bytes, which names the session for a root that has one each. */
function askFiles(want: "tree" | "blob", root: string, path: string, etag?: string | null): void {
  const session = isPerSession(fileRoots, root) ? filesSession : null;
  post("files", { want, root, path, session, ...(want === "blob" ? { etag: etag ?? null } : {}) });
}

function askDir(root: string, path: string): void {
  askedDirs.add(dirKey(root, path));
  askFiles("tree", root, path);
}

/**
 * The followed session changed: the Scratch root is that session's directory, so what was
 * read of another session's is dropped and the chip's tree is asked for again.
 */
function followFilesSession(session: string | null): void {
  if (session === filesSession) return;
  filesSession = session;
  for (const root of servedRoots(rootsDocument, "any").filter((info) => info.per_session)) {
    const prefix = `${root.name}:`;
    for (const held of [listings, listingTexts, rootProblems]) {
      for (const key of [...held.keys()]) if (key.startsWith(prefix) || key === root.name) held.delete(key);
    }
    for (const held of [openDirs, askedDirs]) {
      for (const key of [...held]) if (key.startsWith(prefix)) held.delete(key);
    }
    if (openFile?.root === root.name) openFile = null;
    fileHistory = without(fileHistory, (place) => place.root === root.name);
  }
  if (!openFile) fileHistory = visitPlace(fileHistory, null, sameFile);
  fileRoots = servedRoots(rootsDocument, filesSession);
  const shown = shownRoot();
  if (currentView === "files" && shown && !askedDirs.has(dirKey(shown, ""))) askDir(shown, "");
  drawFilesSide();
  drawFile();
}

function openDir(root: string, path: string): void {
  if (path) openDirs.add(dirKey(root, path));
  if (!askedDirs.has(dirKey(root, path))) askDir(root, path);
}

function entryAt(root: string, path: string): Entry | null {
  return listings.get(dirKey(root, folderOf(path)))?.find((entry) => entry.path === path) ?? null;
}

function modifiedWords(entry: Entry | null): string {
  if (typeof entry?.mtime_ns !== "number") return "";
  return when(new Date(entry.mtime_ns / 1e6).toISOString()) ?? "";
}

const isStarred = (root: string, path: string) => starredFiles.some((item) => item.root === root && item.path === path);
const FILE_ICONS = { folder: "folder", document: "page", code: "code", image: "image", file: "page" } as const;

/** A row of the Files home's list, in Finder's manner: name, date modified, size and kind (KYO-835). */
function columnRow(root: string, row: TreeRow): HTMLElement {
  const item = treeRow(root, row);
  const { entry } = row;
  const name = document.createElement("div");
  name.className = "file-name";
  name.style.paddingLeft = `${row.depth * 16}px`;
  const glyph = icon(FILE_ICONS[iconOf(entry)]);
  if (entry.type === "dir") glyph.classList.add("folder");
  name.append(...Array.from(item.childNodes), glyph);
  name.insertBefore(glyph, name.querySelector(".tree-name"));
  if (entry.type !== "dir" && (root === "code" || root === "store")) {
    const star = document.createElement("button");
    const on = isStarred(root, entry.path);
    star.className = on ? "file-star on" : "file-star";
    star.textContent = on ? "★" : "☆";
    star.title = on ? "Unstar" : "Star";
    star.dataset.star = entry.path;
    star.dataset.starRoot = root;
    name.append(star);
  }
  item.style.paddingLeft = "";
  item.replaceChildren(
    name,
    span("file-cell", modifiedWords(entry)),
    span("file-cell", entry.type === "dir" ? "--" : sizeWords(entry.size)),
    span("file-cell", kindWords(entry)));
  return item;
}

function treeRow(root: string, row: TreeRow): HTMLElement {
  const { entry } = row;
  const item = document.createElement("div");
  item.className = "tree-row";
  item.style.paddingLeft = `${8 + row.depth * 14}px`;
  item.title = entry.path;
  if (isDotfile(entry.name)) item.classList.add("dot");
  if (entry.type === "dir") {
    item.dataset.dir = entry.path;
    item.setAttribute("role", "button");
    item.setAttribute("aria-expanded", String(row.open));
    if (row.open) item.classList.add("open");
  } else {
    item.dataset.file = entry.path;
    if (openFile?.root === root && openFile.path === entry.path) item.classList.add("current");
  }
  item.append(span("chevron", entry.type === "dir" ? "›" : ""), span("tree-name", entry.name));
  if (row.waiting) item.append(span("tree-waiting", "reading"));
  return item;
}

function mentionRow(item: Mention, index: number): HTMLElement {
  const row = document.createElement("div");
  row.className = "mention-row";
  row.dataset.mention = String(index);
  row.title = pathTitle({ kind: "file", name: item.entry.name, type: "file", ...item });
  const folder = folderOf(item.path);
  row.append(
    span("mention-name", `${item.entry.name}${item.line ? `:${item.line}` : ""}`),
    span("mention-place", `${rootLabel(item.root)}${folder ? ` · ${folder}` : ""}`));
  return row;
}

/** The files mentioned in the session, newest first, in Files' side bar. */
function drawMentions(): void {
  const rows = mentions.map(mentionRow);
  fill(
    document.getElementById("files-mentioned")!,
    rows.length ? rows : [emptyRow("No file has been mentioned.")]);
}

/**
 * The link an anchor of a prompt or a reply is, when it is one the side bar lists: a path the
 * server has placed, a record or a web address. A path still unanswered is not yet a link.
 */
function anchorLink(anchor: HTMLAnchorElement, at: string): ChatLink | null {
  if (anchor.classList.contains("path-link")) {
    const given = anchor.dataset.given ?? "";
    const answer = given ? pathAnswers.get(given)?.answer : null;
    if (answer?.kind === "file" && answer.type !== "dir") {
      const folder = folderOf(answer.path);
      return {
        key: `file:${answer.root}:${answer.path}`, kind: "file",
        name: `${nameOf(answer.path)}${answer.line ? `:${answer.line}` : ""}`,
        place: `${rootLabel(answer.root)}${folder ? ` · ${folder}` : ""}`, at, href: "#", given,
      };
    }
    if (answer?.kind === "outside") {
      return {
        key: `outside:${answer.path}`, kind: "outside", name: nameOf(answer.path),
        place: "Outside the served folders", at, href: "#", given,
      };
    }
    return null;
  }
  if (anchor.dataset.local !== undefined) return null;
  const href = anchor.getAttribute("href") ?? "";
  return addressLink(href, anchor.textContent ?? "", at, parseLink(href)?.ref ?? null);
}

/**
 * The links in the chat's own words (2026-11-11): the person's prompts and the assistant's
 * replies as drawn, not a subagent's hand-back, a command's output or a reply's thinking.
 */
function readChatLinks(): ChatLink[] {
  let list: ChatLink[] = [];
  for (const [key, element] of elements) {
    if (!(key.startsWith("t:") || key.startsWith("r:")) || !element.isConnected) continue;
    const at = element.dataset.at ?? "";
    for (const anchor of Array.from(element.querySelectorAll<HTMLAnchorElement>("a[href]"))) {
      if (anchor.closest(".thinking")) continue;
      const item = anchorLink(anchor, at);
      if (item) list = withLink(list, item);
    }
    // A prompt is drawn as plain text, so an address the person wrote out is no anchor.
    if (key.startsWith("t:")) {
      for (const address of webAddresses(sources.get(key) ?? "")) {
        const item = addressLink(address, address, at, null);
        if (item) list = withLink(list, item);
      }
    }
  }
  return list;
}

function linkRow(item: ChatLink): HTMLElement {
  const row = document.createElement("a");
  row.className = item.given ? "mention-row side-link path-link" : "mention-row side-link";
  row.setAttribute("href", item.href);
  if (item.given) row.dataset.given = item.given;
  row.dataset.kind = item.kind;
  row.title = item.kind === "url" ? item.href : item.place;
  // A record is named by its title, read from the record list, which is asked for once a
  // link needs it; until it answers the row says Record.
  let place = item.place;
  if (item.kind === "record") {
    const id = parseLink(item.href)?.record;
    const title = id ? recordRows.find((row) => row.id === id)?.title : undefined;
    if (title) place = row.title = title;
    else if (!listsAsked) {
      listsAsked = true;
      post("records", { want: "lists" });
    }
  }
  row.append(span("mention-name", item.name), span("mention-place", place));
  return row;
}

/** The chat's links, newest first, in the chat's side bar. */
function drawChatLinks(): void {
  chatLinks = readChatLinks();
  const short = allLinks ? chatLinks : chatLinks.slice(0, LINKS_SHOWN);
  document.getElementById("side-linked")!.replaceChildren(
    ...(short.length ? short.map(linkRow) : [emptyRow("None yet.")]));
  const more = document.getElementById("side-linked-more")!;
  more.hidden = chatLinks.length <= LINKS_SHOWN;
  more.textContent = allLinks ? "Show fewer" : `See all ${chatLinks.length}`;
  // The Records home lists the records the chat names (KYO-862).
  if (listsRead) drawRecordsHome("");
}

let linksTimer: number | undefined;
function drawChatLinksSoon(): void {
  if (linksTimer !== undefined) return;
  linksTimer = window.setTimeout(() => {
    linksTimer = undefined;
    drawChatLinks();
  }, 250);
}

let mentionsTimer: number | undefined;
function drawMentionsSoon(): void {
  if (mentionsTimer !== undefined) return;
  mentionsTimer = window.setTimeout(() => {
    mentionsTimer = undefined;
    drawMentions();
  }, 0);
}

/** The root chips, the dotfiles toggle, the tree and the top bar's words. */
function drawFilesSide(): void {
  const root = shownRoot();
  document.querySelectorAll<HTMLElement>(".files-roots").forEach((holder) => {
    holder.replaceChildren(...fileRoots.map((info) => {
      const chip = document.createElement("button");
      chip.className = "chip";
      chip.dataset.root = info.name;
      chip.setAttribute("aria-pressed", String(info.name === root));
      chip.textContent = rootLabel(info.name);
      if (info.path) chip.title = info.path;
      return chip;
    }));
  });
  document.querySelectorAll<HTMLElement>(".files-dots").forEach((toggle) => {
    toggle.textContent = showDotfiles ? "Hide dotfiles" : "Show dotfiles";
    toggle.setAttribute("aria-pressed", String(showDotfiles));
    toggle.hidden = !root;
  });
  const rows = root ? treeRows(root, listings, openDirs, showDotfiles) : [];
  const sorted = root ? treeRows(root, listings, openDirs, showDotfiles, fileSort) : [];
  const nothing = rootsProblem
    ? "The files could not be read."
    : root && rootProblems.has(root) ? rootProblems.get(root)!
    : !rootsRead || (root && !listings.has(dirKey(root, "")))
      ? "Reading the files."
      : "This folder shows no file.";
  document.querySelectorAll<HTMLElement>(".files-tree").forEach((holder) => {
    const columns = holder.classList.contains("files-columns");
    const shown = columns ? sorted : rows;
    fill(holder, shown.length
      ? shown.map((row) => (columns ? columnRow(root!, row) : treeRow(root!, row)))
      : [emptyRow(nothing)]);
  });
  drawFilesHome(root);
  drawNav("files", !openFile, fileHistory);
  // KYO-883: the root and the path inside it are set apart by space, not a slash that reads
  // as part of the path.
  const pathBar = document.getElementById("files-path")!;
  const pathWords = openFile ? `${rootLabel(openFile.root)}\u0000${openFile.path}` : "";
  if (pathBar.dataset.shown !== pathWords) {
    pathBar.dataset.shown = pathWords;
    pathBar.replaceChildren(...(openFile
      ? [span("files-path-root", rootLabel(openFile.root)), span("files-path-file", openFile.path)] : []));
  }
  pathBar.title = openFile
    ? serverPath(fileRoots, openFile.root, openFile.path) ?? openFile.path : "";
  const entry = openFile?.entry ?? null;
  setText("files-meta", openFile
    ? [sizeWords(entry?.size), modifiedWords(entry)].filter(Boolean).join(" · ")
    : root && rootsRead ? fileRoots.find((info) => info.name === root)?.path ?? "" : "");
}

function fileNote(text: string): HTMLElement {
  const note = document.createElement("div");
  note.className = "file-note";
  note.textContent = text;
  return note;
}

/** A markdown file drawn as a reply is, with its links and images read against its folder. */
function fileMarkdown(file: OpenFile, text: string): HTMLElement {
  const holder = document.createElement("div");
  holder.className = "reply file-body";
  holder.innerHTML = render(text);
  addCopyButtons(holder);
  const folder = folderOf(file.path);
  for (const link of Array.from(holder.querySelectorAll<HTMLAnchorElement>("a[data-local]"))) {
    const place = linkPlace(folder, link.dataset.local ?? "");
    if (place?.kind !== "inside") continue;
    link.removeAttribute("data-local");
    link.setAttribute("href", "#");
    link.className = "path-link";
    link.dataset.inside = place.path;
    if (place.line) link.dataset.line = String(place.line);
    link.title = `${rootLabel(file.root)}: ${place.path}`;
  }
  for (const image of Array.from(holder.querySelectorAll<HTMLImageElement>("img[data-local]"))) {
    const place = linkPlace(folder, image.dataset.local ?? "");
    if (place?.kind !== "inside") continue;
    const key = dirKey(file.root, place.path);
    const data = fileImages.get(key);
    if (data) {
      image.src = data;
    } else {
      image.dataset.blob = key;
      askFiles("blob", file.root, place.path);
    }
  }
  // A path written out in the file's text is a link by the same rule as one in a reply.
  linkQuietly(holder);
  return holder;
}

/** A source or text file, read-only: its line numbers beside its text, coloured by its type. */
function fileSource(file: OpenFile, text: string): HTMLElement {
  const box = document.createElement("div");
  box.className = "file-source";
  const count = Math.max(1, lineCount(text));
  const gutter = document.createElement("pre");
  gutter.className = "file-gutter";
  gutter.setAttribute("aria-hidden", "true");
  gutter.textContent = Array.from({ length: count }, (_, index) => index + 1).join("\n");
  const body = document.createElement("pre");
  body.className = "file-code";
  const code = document.createElement("code");
  const coloured = text.length <= HIGHLIGHT_CHARS ? highlight(text, languageOf(file.path)) : null;
  if (coloured === null) {
    code.textContent = text;
  } else {
    code.className = "hljs";
    code.innerHTML = coloured;
  }
  body.append(code);
  box.append(gutter, body);
  if (file.line && file.line <= count) {
    const mark = document.createElement("div");
    mark.className = "file-line-mark";
    mark.style.top = `${SOURCE_PADDING_PX + (file.line - 1) * SOURCE_LINE_PX}px`;
    const last = Math.min(Math.max(file.lineEnd ?? file.line, file.line), count);
    mark.style.height = `${(last - file.line + 1) * SOURCE_LINE_PX}px`;
    box.append(mark);
  }
  return box;
}

function fileFacts(file: OpenFile, entry: Entry): HTMLElement {
  const box = document.createElement("div");
  box.className = "file-facts";
  const name = document.createElement("h1");
  name.textContent = entry.name || nameOf(file.path);
  const facts = document.createElement("dl");
  facts.className = "record-facts";
  const rows: [string, string][] = [
    ["type", entry.type === "file" || !entry.type ? entry.media_type ?? file.mediaType : entry.type],
    ["size", sizeWords(entry.size)],
    ["modified", modifiedWords(entry)],
    ["path", serverPath(fileRoots, file.root, file.path) ?? file.path],
  ];
  for (const [label, text] of rows) {
    if (!text) continue;
    const term = document.createElement("dt");
    term.textContent = label;
    const value = document.createElement("dd");
    value.textContent = text;
    facts.append(term, value);
  }
  box.append(name, facts, fileNote("This type of file is not shown here."));
  return box;
}

/** Scrolls to the line the file was opened at, once its text is drawn. */
function showFileLine(file: OpenFile): void {
  if (!file.lineWaits || !file.line || file.text === null) return;
  file.lineWaits = false;
  const mark = fileArticle.querySelector<HTMLElement>(".file-line-mark");
  if (mark) {
    mark.scrollIntoView({ block: "center" });
    return;
  }
  const line = file.line - 1;
  const last = Math.max(file.lineEnd ?? file.line, file.line) - 1;
  const stamped = Array.from(fileArticle.querySelectorAll<HTMLElement>("[data-source-line]"));
  const block = stamped
    .filter((candidate) =>
      Number(candidate.dataset.sourceLine) <= line && line < Number(candidate.dataset.sourceEnd))
    .pop();
  if (!block) return;
  // A span of several blocks shows each innermost block it covers.
  const covered = last > line
    ? stamped.filter((candidate) => !candidate.querySelector("[data-source-line]")
      && Number(candidate.dataset.sourceLine) <= last && Number(candidate.dataset.sourceEnd) > line)
    : [];
  block.scrollIntoView({ block: "center" });
  for (const shown of covered.length ? covered : [block]) {
    shown.classList.add("revealed");
    setTimeout(() => shown.classList.remove("revealed"), REVEAL_MS);
  }
}

/** The pane: the open file, or the tree when none is open. A redraw keeps the place read. */
function drawFile(): void {
  filesNote.hidden = !rootsProblem;
  filesNote.textContent = rootsProblem ? `The files could not be read. ${rootsProblem}` : "";
  filesLanding.hidden = openFile !== null;
  fileArticle.hidden = openFile === null;
  const file = openFile;
  if (!file) {
    fileArticle.replaceChildren();
    drawFileOutline();
    drawFileStar();
    return;
  }
  const top = filesPane.scrollTop;
  const left = fileArticle.querySelector(".file-code")?.scrollLeft ?? 0;
  const name = nameOf(file.path);
  const parts: HTMLElement[] = [];
  if (file.problem) parts.push(fileNote(`${name} could not be read. ${file.problem}`));
  if (file.text !== null) {
    // A line beyond the head of a long file is in the rest of it.
    const head = headOf(file.text);
    if (!head.whole && file.line && file.lineWaits && file.line > lineCount(head.text)) file.whole = true;
    const shown = file.whole ? file.text : head.text;
    const kind = fileKind({ name, media_type: file.mediaType, text: true });
    parts.push(kind === "markdown" ? fileMarkdown(file, shown) : fileSource(file, shown));
    if (!head.whole && !file.whole) {
      const more = document.createElement("div");
      more.className = "file-more";
      const button = document.createElement("button");
      button.className = "text-button";
      button.textContent = "Show the whole file";
      button.addEventListener("click", () => {
        file.whole = true;
        drawFile();
      });
      more.append(
        `The first ${lineCount(head.text).toLocaleString()} of ${lineCount(file.text).toLocaleString()} lines are shown. `,
        button);
      parts.push(more);
    }
  } else if (file.data) {
    const image = document.createElement("img");
    image.className = "file-image";
    image.alt = name;
    image.src = file.data;
    parts.push(image, fileNote(
      [name, file.mediaType, sizeWords(file.entry?.size)].filter(Boolean).join(" · ")));
  } else if (file.entry && file.asked && !file.problem) {
    parts.push(fileFacts(file, file.entry));
  } else if (!file.problem) {
    parts.push(fileNote(`Reading ${name}.`));
  }
  fileArticle.replaceChildren(...parts);
  filesPane.scrollTop = top;
  const code = fileArticle.querySelector(".file-code");
  if (code) code.scrollLeft = left;
  showFileLine(file);
  drawFileOutline();
  drawFileStar();
}

/**
 * Asks for the open file's bytes. A file that is neither text nor an image is drawn from its
 * entry and is not fetched, and a file opened by its path alone waits for its folder's
 * listing, which says what it is.
 */
function askOpenFile(listed = false): void {
  const file = openFile;
  if (!file || file.asked) return;
  file.entry ??= entryAt(file.root, file.path);
  if (!file.entry && !listed && !listings.has(dirKey(file.root, folderOf(file.path)))) return;
  file.asked = true;
  if (file.entry && fileKind(file.entry) === "other") return;
  askFiles("blob", file.root, file.path);
}

function showTreeRow(path: string): void {
  for (const row of Array.from(document.querySelectorAll<HTMLElement>(".files-tree .tree-row"))) {
    if ((row.dataset.file ?? row.dataset.dir) === path) row.scrollIntoView({ block: "nearest" });
  }
}

/**
 * Opens a file in the Files view, in place of the one that was open, at a line when one is
 * given; a folder is opened in the tree. `entry` says what the file is when the opener knows.
 */
function showFile(
  root: string, path: string, line: number | null = null, entry: Entry | null = null, stepping = false,
  lineEnd: number | null = null,
): void {
  chosenRoot = root;
  store(FILES_ROOT_KEY, root);
  openDir(root, "");
  for (const folder of foldersAbove(path)) openDir(root, folder);
  const folder = entry?.type === "dir" || path === "";
  if (folder) {
    // A folder is shown in the tree, which the pane holds while no file is open.
    if (path) openDir(root, path);
    if (openFile) {
      if (!stepping) fileHistory = visitPlace(fileHistory, null, sameFile);
      openFile = null;
    }
  } else {
    const same = openFile?.root === root && openFile.path === path;
    if (!same) {
      if (!stepping) fileHistory = visitPlace(fileHistory, { root, path }, sameFile);
      if (!stepping) noteOpened(root, path);
      fileImages.clear();
      openFile = {
        root, path, line, lineEnd, lineWaits: line !== null, entry: entry ?? entryAt(root, path),
        asked: false, etag: null, text: null, data: null, mediaType: entry?.media_type ?? "",
        problem: null, whole: false,
      };
    } else if (openFile) {
      openFile.line = line;
      openFile.lineEnd = lineEnd;
      openFile.lineWaits = line !== null;
    }
    if (!same) filesPane.scrollTop = 0;
  }
  showView("files");
  askOpenFile();
  drawFilesSide();
  drawFile();
  showTreeRow(path);
}

/** Shows the place the history now stands on, without adding to it: a file, or the browser. */
function showFilePlace(): void {
  const place = current(fileHistory);
  if (place) {
    showFile(place.root, place.path, null, null, true);
    return;
  }
  openFile = null;
  drawFilesSide();
  drawFile();
  filesPane.scrollTop = 0;
}

/** Home: the file browser as when no file is open, with the files opened kept behind. */

/** The Files home's fixed column: the roots as Locations, then Recents, Mentioned and Starred. */
function drawFilesHome(root: string | null): void {
  const locations = fileRoots.map((info) => {
    const row = document.createElement("div");
    row.className = info.name === root ? "location-row current" : "location-row";
    row.dataset.root = info.name;
    if (info.path) row.title = info.path;
    row.append(icon("folder"), span("tree-name", rootLabel(info.name)));
    return row;
  });
  fill(document.getElementById("files-locations")!, locations.length ? locations : [emptyRow("Reading the files.")]);
  const list = (items: Recent[], none: string) => items.length
    ? items.slice(0, 10).map(recentRow) : [emptyRow(none)];
  fill(document.getElementById("files-home-recent")!, list(recentFiles, "None yet."));
  fill(document.getElementById("files-home-mentioned")!,
    mentions.length ? mentions.slice(0, 10).map(mentionRow) : [emptyRow("None yet.")]);
  fill(document.getElementById("files-home-starred")!, list(starredFiles, "Star a file from its row."));
  // KYO-883: the roots in the dropdown beside the search, and Show dotfiles at its foot.
  const dots = document.createElement("button");
  dots.type = "button";
  dots.className = "place-dots";
  dots.setAttribute("role", "menuitemcheckbox");
  dots.setAttribute("aria-checked", String(showDotfiles));
  dots.append(span("place-check", showDotfiles ? "✓" : ""), span("place-label", "Show dotfiles"));
  drawPlaceControl(document.getElementById("files-place")!, fileRoots.map((info) => ({
    label: rootLabel(info.name), data: { root: info.name }, current: info.name === root,
  })), root ? rootLabel(root) : "Files", [dots]);
  document.querySelectorAll<HTMLElement>(".files-head [data-sort]").forEach((button) => {
    if (button.dataset.sort === fileSort.by) {
      button.setAttribute("aria-sort", fileSort.descending ? "descending" : "ascending");
    } else button.removeAttribute("aria-sort");
  });
}

function toggleStar(root: string, path: string): void {
  post("files", { want: isStarred(root, path) ? "unstar" : "star", root, path });
}

/** The open file's star in the top bar, for a file of the code or the store root. */
function drawFileStar(): void {
  const button = document.getElementById("files-star")!;
  const file = openFile;
  button.hidden = !file || (file.root !== "code" && file.root !== "store");
  if (!file) return;
  const on = isStarred(file.root, file.path);
  button.classList.toggle("on", on);
  button.textContent = on ? "★" : "☆";
  button.title = on ? "Unstar this file" : "Star this file";
}
document.getElementById("files-star")!.addEventListener("click", () => {
  if (openFile) toggleStar(openFile.root, openFile.path);
});

/** The open markdown file's headings in the right drawer, set in by level, each folding what is under it. */
const foldedHeadings = new Set<string>();
function drawFileOutline(): void {
  const holder = document.getElementById("file-outline")!;
  const headings = openFile && !fileArticle.hidden
    ? Array.from(fileArticle.querySelectorAll<HTMLElement>(".file-body h1, .file-body h2, .file-body h3, .file-body h4"))
    : [];
  const levels = headings.map((heading) => Number(heading.tagName.slice(1)));
  const rows: HTMLElement[] = [];
  let hiddenBelow = Infinity;
  headings.forEach((heading, index) => {
    const level = levels[index];
    if (level <= hiddenBelow) hiddenBelow = Infinity;
    const key = `${openFile!.root}:${openFile!.path}#${index}`;
    const hasChildren = levels[index + 1] !== undefined && levels[index + 1] > level;
    const line = document.createElement("div");
    line.className = "heading-row";
    line.style.paddingLeft = `${(level - 1) * 12}px`;
    line.hidden = level > hiddenBelow;
    const caret = span("chevron", hasChildren ? "›" : "");
    const folded = foldedHeadings.has(key);
    if (hasChildren && !folded) line.classList.add("open");
    caret.addEventListener("click", () => {
      if (foldedHeadings.has(key)) foldedHeadings.delete(key);
      else foldedHeadings.add(key);
      drawFileOutline();
    });
    const label = document.createElement("div");
    label.className = "outline-row";
    label.textContent = heading.textContent ?? "";
    label.addEventListener("click", () => heading.scrollIntoView({ block: "start" }));
    line.append(caret, label);
    rows.push(line);
    if (hasChildren && folded && hiddenBelow === Infinity) hiddenBelow = level;
  });
  holder.replaceChildren(...rows);
  applyDocks();
}

/** Records an opened file on the server, for the Recently opened list on every device. */
function noteOpened(root: string, path: string): void {
  if (root !== "code" && root !== "store") return;
  if (recentFiles[0]?.root === root && recentFiles[0]?.path === path) return;
  post("files", { want: "opened", root, path });
}

/** A text with each case-insensitive occurrence of `query` marked. */
function marked(text: string, query: string): DocumentFragment {
  const fragment = document.createDocumentFragment();
  const lower = text.toLowerCase();
  const needle = query.toLowerCase();
  let at = 0;
  for (let found = lower.indexOf(needle); needle && found >= 0; found = lower.indexOf(needle, at)) {
    fragment.append(text.slice(at, found));
    const mark = document.createElement("mark");
    mark.textContent = text.slice(found, found + needle.length);
    fragment.append(mark);
    at = found + needle.length;
  }
  fragment.append(text.slice(at));
  return fragment;
}

/** Asks every served code and store root about the field's text, one root at a time. */
function searchFiles(text: string): void {
  const query = text.trim();
  if (query === filesQuery) return;
  filesQuery = query;
  filesFound.clear();
  openFound.clear();
  if (query.length >= SEARCH_MIN_CHARS) {
    for (const info of fileRoots) {
      if (info.name !== "code" && info.name !== "store") continue;
      filesFound.set(info.name, "waiting");
      post("files", { want: "search", q: query, root: info.name });
    }
  }
  drawFound();
}

function foundRow(className: string, root: string, path: string, line: number | null): HTMLElement {
  const row = document.createElement("div");
  row.className = className;
  row.dataset.foundRoot = root;
  row.dataset.foundPath = path;
  if (line !== null) row.dataset.foundLine = String(line);
  return row;
}

/**
 * The search's answers, grouped by root, in place of the tree and lists: in the side bar and
 * on the page Files shows with no file open, each of which has the field.
 */
function drawFound(): void {
  const searching = filesQuery.length >= SEARCH_MIN_CHARS;
  for (const place of [document.getElementById("files-side")!, filesLanding]) {
    place.classList.toggle("searching", searching);
    const holder = place.querySelector<HTMLElement>(".files-found")!;
    holder.hidden = !searching;
    if (searching) holder.replaceChildren(...foundRows());
  }
}

function note(text: string): HTMLElement {
  return Object.assign(document.createElement("div"), { className: "found-note", textContent: text });
}

/** A section's head, which folds the section when pressed (KYO-818). */
function foundHead(section: string, label: string): HTMLElement {
  const head = document.createElement("h4");
  head.className = "found-head";
  head.dataset.foundSection = section;
  head.setAttribute("aria-expanded", String(!foldedSections.has(section)));
  head.append(span("chevron", "›"), label);
  return head;
}

function foundFileRow(file: FoundFile, contents: boolean): HTMLElement {
  const first = contents ? file.lines[0]?.line ?? null : null;
  const row = foundRow("found-file", file.root, file.path, first);
  const key = `${file.root}:${file.path}`;
  if (contents) {
    const toggle = span("chevron found-toggle", "›");
    toggle.dataset.foundToggle = key;
    if (openFound.has(key)) row.classList.add("open");
    row.append(toggle);
  }
  const words = document.createElement("div");
  words.className = "found-words";
  const name = span("mention-name", "");
  name.append(file.byName ? marked(nameOf(file.path), filesQuery) : nameOf(file.path));
  const folder = folderOf(file.path);
  const place = span("mention-place", "");
  place.append(`${rootLabel(file.root)}${folder ? " · " : ""}`);
  if (folder) place.append(file.byName ? marked(folder, filesQuery) : folder);
  if (contents) place.append(` · ${file.count === 1 ? "1 line" : `${file.count} lines`}`);
  words.append(name, place);
  row.append(words);
  return row;
}

/**
 * The search's answers in two sections across every root (KYO-818): the files whose path holds
 * the text, then those whose contents do, one row each, its lines shown when its arrow is
 * pressed. `orderFound` puts each section in order.
 */
function foundRows(): HTMLElement[] {
  const rows: HTMLElement[] = [];
  const files: FoundFile[] = [];
  let waiting = false;
  let more = false;
  for (const [root, answer] of filesFound) {
    if (answer === "waiting") waiting = true;
    else if ("problem" in answer) rows.push(note(`${rootLabel(root)}: ${answer.problem}`));
    else {
      files.push(...answer.files);
      more ||= answer.more;
    }
  }
  const { names, contents } = orderFound(files, filesQuery, recentFiles);
  rows.push(foundHead("names", `Names · ${names.length}`));
  if (!foldedSections.has("names")) {
    if (!names.length) rows.push(note(waiting ? "Searching…" : "No file is named so."));
    for (const file of names) rows.push(foundFileRow(file, false));
  }
  rows.push(foundHead("contents", `Contents · ${contents.length} ${contents.length === 1 ? "file" : "files"}`));
  if (!foldedSections.has("contents")) {
    if (!contents.length) rows.push(note(waiting ? "Searching…" : "No file holds it."));
    for (const file of contents) {
      rows.push(foundFileRow(file, true));
      if (!openFound.has(`${file.root}:${file.path}`)) continue;
      for (const { line, text } of file.lines) {
        const item = foundRow("found-line", file.root, file.path, line);
        item.append(span("found-at", String(line)), marked(text.trim(), filesQuery));
        rows.push(item);
      }
      if (file.count > file.lines.length) {
        const more = note(`and ${file.count - file.lines.length} more in the file`);
        more.classList.add("found-more");
        rows.push(more);
      }
    }
  }
  if (waiting && (names.length || contents.length)) rows.push(note("Still searching…"));
  if (more) rows.push(note("More were found than are shown; a longer search narrows them."));
  return rows;
}

function recentRow(item: Recent): HTMLElement {
  const row = foundRow("mention-row", item.root, item.path, null);
  const folder = folderOf(item.path);
  const opened = when(item.opened_at);
  row.append(
    span("mention-name", nameOf(item.path)),
    span("mention-place", `${rootLabel(item.root)}${folder ? ` · ${folder}` : ""}${opened ? ` · ${opened}` : ""}`));
  return row;
}

/** The project's recently opened files, newest first, in Files' side bar. */
function drawRecent(): void {
  const rows = recentFiles.slice(0, RECENT_SHOWN).map(recentRow);
  fill(document.getElementById("files-recent")!, rows.length ? rows : [emptyRow("No file opened yet.")]);
}

function filesHome(): void {
  fileHistory = visitPlace(fileHistory, null, sameFile);
  showFilePlace();
}

function refreshOpenFile(): void {
  const file = openFile;
  if (!file?.asked || (file.entry && fileKind(file.entry) === "other" && file.text === null && !file.data)) return;
  askFiles("blob", file.root, file.path, file.etag);
}

function refreshTree(): void {
  if (rootsProblem || !rootsRead) {
    post("files", { want: "roots" });
    return;
  }
  const root = shownRoot();
  if (!root) return;
  askDir(root, "");
  for (const key of openDirs) {
    if (key.startsWith(`${root}:`)) askDir(root, key.slice(root.length + 1));
  }
  if (openFile && openFile.root !== root) askDir(openFile.root, folderOf(openFile.path));
}

function enterFiles(): void {
  post("files", { want: "recent" });
  post("files", { want: "starred" });
  if (!rootsAsked) {
    rootsAsked = true;
    post("files", { want: "roots" });
  } else {
    refreshOpenFile();
    refreshTree();
  }
  drawFilesSide();
  drawFile();
}

// The file routes send no event when a file changes, so the view asks again while it is
// shown: the open file by its tag, which the server answers without the bytes when nothing
// changed, and less often the folders that are open.
setInterval(() => {
  if (currentView !== "files" || document.hidden) return;
  filesTicks += 1;
  refreshOpenFile();
  if (filesTicks % TREE_REFRESH_EVERY === 0) refreshTree();
}, FILE_REFRESH_MS);

const fileFrames = {
  /**
   * The project whose files the view shows, whether the server's machine is this one, and
   * the path mapping Settings holds. It is sent again when Settings are saved, and what the
   * server refused before is then asked again.
   */
  files(frame: { project?: string; same_machine?: boolean; mappings?: Mapping[] }): void {
    sameMachine = frame.same_machine === true;
    pathMappings = Array.isArray(frame.mappings) ? frame.mappings : [];
    if (frame.project !== filesProject) {
      filesProject = frame.project ?? null;
      pathAnswers.clear();
      pathHolders.clear();
      mentions = [];
      listings.clear();
      listingTexts.clear();
      rootProblems.clear();
      openDirs.clear();
      openFile = null;
      fileHistory = fresh();
      fileImages.clear();
    }
    rootsDocument = null;
    fileRoots = [];
    rootsRead = false;
    rootsProblem = null;
    askedDirs.clear();
    pathsRefused = false;
    pathsAsked.clear();
    pathQueue.length = 0;
    pathsOut = 0;
    for (const given of pathHolders.keys()) {
      pathsAsked.add(given);
      pathQueue.push(given);
    }
    sendPathQuestions();
    rootsAsked = currentView === "files";
    if (rootsAsked) post("files", { want: "roots" });
    if (openFile) {
      openFile.asked = false;
      askOpenFile(true);
    }
    drawFilesSide();
    drawFile();
    drawMentions();
  },

  /** `GET .../files`, or the server's own sentence when it refused. */
  filesRoots(frame: { document?: unknown; error?: string | null }): void {
    if ((frame.document as { ok?: unknown } | null)?.ok === true) {
      rootsDocument = frame.document;
      fileRoots = servedRoots(rootsDocument, filesSession);
      rootsRead = true;
      rootsProblem = null;
      const root = shownRoot();
      if (root && !askedDirs.has(dirKey(root, ""))) askDir(root, "");
    } else if (!rootsRead || frame.error) {
      // A read that could not reach the server leaves the roots as they were.
      rootsProblem = frame.error ?? "The server sent no roots.";
    }
    drawFilesSide();
    drawFile();
  },

  /** One folder's listing. A folder that is gone is closed. */
  filesTree(frame: {
    root: string; path: string; session?: string | null; status?: number; document?: unknown;
    error?: string | null;
  }): void {
    // An answer about another session's scratchpad is not this one's.
    if (frame.session && frame.session !== filesSession) return;
    const key = dirKey(frame.root, frame.path);
    const entries = entriesOf(frame.document);
    const file = openFile;
    const holdsFile = !!file && file.root === frame.root && folderOf(file.path) === frame.path;
    if (frame.path === "") {
      // A refused root says why in the tree's place; the sentence names the remedy.
      if (entries || !frame.status) rootProblems.delete(frame.root);
      else rootProblems.set(frame.root, frame.error ?? "The server refused that.");
    }
    if (!entries) {
      if (frame.path === "" && frame.status) {
        listings.delete(key);
        listingTexts.delete(key);
      } else if (frame.status === 404) {
        listings.delete(key);
        listingTexts.delete(key);
        openDirs.delete(key);
      } else if (!listings.has(key)) {
        askedDirs.delete(key);
      }
      if (holdsFile) askOpenFile(true);
      drawFilesSide();
      if (holdsFile) drawFile();
      return;
    }
    const text = JSON.stringify(entries);
    const changed = listingTexts.get(key) !== text;
    listings.set(key, entries);
    listingTexts.set(key, text);
    if (holdsFile && file) {
      const entry = entries.find((item) => item.path === file.path) ?? null;
      const drawnFromEntry = file.asked && file.text === null && !file.data;
      if (entry) file.entry = entry;
      if (!file.asked) askOpenFile(true);
      else if (changed && drawnFromEntry) drawFile();
    }
    if (changed || holdsFile) drawFilesSide();
  },

  /**
   * One file: its text, or an image as data, with its tag. Status 304 says the copy the
   * page holds is current.
   */
  filesBlob(frame: {
    root: string; path: string; session?: string | null; status: number; etag?: string | null;
    media_type?: string | null; size?: number | null; text?: string | null; data?: string | null;
    error?: string | null;
  }): void {
    if (frame.session && frame.session !== filesSession) return;
    const key = dirKey(frame.root, frame.path);
    if (frame.status === 200 && frame.data) {
      fileImages.set(key, frame.data);
      document.querySelectorAll<HTMLImageElement>("img[data-blob]").forEach((image) => {
        if (image.dataset.blob !== key) return;
        image.src = frame.data!;
        image.removeAttribute("data-blob");
      });
    }
    const file = openFile;
    if (!file || file.root !== frame.root || file.path !== frame.path || frame.status === 304) return;
    if (frame.status === 200) {
      file.etag = frame.etag ?? null;
      file.text = typeof frame.text === "string" ? frame.text : null;
      file.data = file.text === null ? frame.data ?? null : null;
      file.mediaType = frame.media_type ?? file.mediaType;
      file.problem = null;
      file.entry ??= {
        name: nameOf(file.path), path: file.path, type: "file", media_type: file.mediaType,
        size: frame.size ?? undefined, text: file.text !== null,
      };
      if (typeof frame.size === "number") file.entry = { ...file.entry, size: frame.size };
    } else if (frame.status === 0 && (file.text !== null || file.data)) {
      // The server could not be reached; what is shown stays until it can.
      return;
    } else {
      file.problem = frame.error ?? "The server could not be reached.";
    }
    drawFilesSide();
    drawFile();
  },

  /** One root's answer to the Files search, kept only while its text is still the one asked. */
  filesSearched(frame: { q: string; root: string; status?: number; document?: unknown; error?: string | null }): void {
    if (frame.q !== filesQuery || !filesFound.has(frame.root)) return;
    const files = frame.status === 200 ? foundFiles(frame.root, frame.document) : null;
    filesFound.set(frame.root, files
      ? { files, more: (frame.document as { more?: unknown })?.more === true }
      : { problem: frame.error || "The server did not answer the search." });
    drawFound();
  },

  /** The project's starred files, read or just changed (KYO-837). */
  filesStarred(frame: { status?: number; document?: unknown }): void {
    const entries = frame.status === 200 ? recentEntries(frame.document) : null;
    if (!entries) return;
    starredFiles = entries;
    drawFilesSide();
    drawFileStar();
  },

  /** The project's recently opened files, read or just added to. */
  filesRecent(frame: { status?: number; document?: unknown }): void {
    const entries = frame.status === 200 ? recentEntries(frame.document) : null;
    if (!entries) return;
    recentFiles = entries;
    drawRecent();
  },

  /** Where one path is, by the resolve route. A refusal of the token ends the asking. */
  filesResolved(frame: { given: string; status?: number; document?: unknown }): void {
    pathsOut = Math.max(0, pathsOut - 1);
    pathsAsked.delete(frame.given);
    if (frame.status === 401 || frame.status === 403) {
      pathsRefused = true;
      pathQueue.length = 0;
      pathsAsked.clear();
      return;
    }
    const answer = answerOf(frame.given, frame.document);
    const holders = pathHolders.get(frame.given);
    pathHolders.delete(frame.given);
    if (answer) {
      pathAnswers.set(frame.given, { answer, at: Date.now() });
      if (holders && answer.kind !== "none") relinkSoon(holders);
    }
    sendPathQuestions();
  },

  /** Whether a path is a file on this machine, which the open menu then offers to reveal. */
  filesExists(frame: { path: string; exists: boolean }): void {
    if (!frame.exists || frame.path !== menuPlace || !pathMenu.isConnected) return;
    if (pathMenu.querySelector(".path-reveal")) return;
    pathMenu.append(boxButton("path-reveal", "Reveal in Finder", () => {
      pathMenu.remove();
      post("files", { want: "reveal", path: frame.path });
    }));
  },
};

/** The store changed, which is where the plans are: read again what the view shows. */
function filesChanged(): void {
  if (currentView !== "files") return;
  refreshOpenFile();
  refreshTree();
}

document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const link = target.closest<HTMLAnchorElement>("a.path-link, a[data-local]");
  if (link) {
    // Such a link has no address the page could follow.
    event.preventDefault();
    if (link.dataset.inside !== undefined && openFile) {
      showFile(openFile.root, link.dataset.inside, Number(link.dataset.line) || null);
      return;
    }
    const answer = link.dataset.given ? pathAnswers.get(link.dataset.given)?.answer : null;
    if (answer?.kind === "file") showFile(answer.root, answer.path, answer.line, entryOf(answer));
    else if (answer?.kind === "outside") showPathMenu(event.clientX, event.clientY, answer.path);
    return;
  }
  const star = target.closest<HTMLElement>("[data-star]");
  if (star?.dataset.star && star.dataset.starRoot) {
    toggleStar(star.dataset.starRoot, star.dataset.star);
    return;
  }
  const sortBy = target.closest<HTMLElement>(".files-head [data-sort]");
  if (sortBy) {
    const by = sortBy.dataset.sort as FileSort["by"];
    fileSort = { by, descending: fileSort.by === by ? !fileSort.descending : by === "modified" };
    store(FILES_SORT_KEY, fileSort);
    drawFilesSide();
    return;
  }
  const chip = target.closest<HTMLElement>(".files-roots [data-root], #files-locations [data-root], #files-place [data-root]");
  if (chip?.dataset.root) {
    chosenRoot = chip.dataset.root;
    store(FILES_ROOT_KEY, chosenRoot);
    openDir(chosenRoot, "");
    drawFilesSide();
    return;
  }
  if (target.closest(".files-dots, .place-dots")) {
    showDotfiles = !showDotfiles;
    store(FILES_DOTFILES_KEY, showDotfiles);
    drawFilesSide();
    return;
  }
  const row = target.closest<HTMLElement>(".files-tree .tree-row");
  const root = shownRoot();
  if (row && root) {
    if (row.dataset.dir !== undefined) {
      const key = dirKey(root, row.dataset.dir);
      if (openDirs.has(key)) openDirs.delete(key);
      else openDir(root, row.dataset.dir);
      drawFilesSide();
    } else if (row.dataset.file !== undefined) {
      showFile(root, row.dataset.file, null, entryAt(root, row.dataset.file));
    }
    return;
  }
  const section = target.closest<HTMLElement>("[data-found-section]");
  if (section?.dataset.foundSection) {
    const name = section.dataset.foundSection;
    if (foldedSections.has(name)) foldedSections.delete(name);
    else foldedSections.add(name);
    store(FILES_FOLDED_KEY, [...foldedSections]);
    drawFound();
    return;
  }
  const toggle = target.closest<HTMLElement>("[data-found-toggle]");
  if (toggle?.dataset.foundToggle) {
    const key = toggle.dataset.foundToggle;
    if (openFound.has(key)) openFound.delete(key);
    else openFound.add(key);
    drawFound();
    return;
  }
  const found = target.closest<HTMLElement>("[data-found-root]");
  if (found?.dataset.foundRoot && found.dataset.foundPath) {
    const line = Number(found.dataset.foundLine) || null;
    showFile(found.dataset.foundRoot, found.dataset.foundPath, line, entryAt(found.dataset.foundRoot, found.dataset.foundPath));
    return;
  }
  const mention = target.closest<HTMLElement>("[data-mention]");
  const item = mention ? mentions[Number(mention.dataset.mention)] : undefined;
  if (item) showFile(item.root, item.path, item.line, item.entry);
});

// The two fields are one search: what is typed in one is shown in the other.
const filesSearchFields = Array.from(document.querySelectorAll<HTMLInputElement>(".files-search"));
for (const field of filesSearchFields) {
  field.addEventListener("input", () => {
    for (const other of filesSearchFields) if (other !== field) other.value = field.value;
    window.clearTimeout(filesSearchTimer);
    filesSearchTimer = window.setTimeout(() => searchFiles(field.value), 250);
  });
  field.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    for (const each of filesSearchFields) each.value = "";
    window.clearTimeout(filesSearchTimer);
    searchFiles("");
  });
}

navIcons("files", {
  home: filesHome,
  back: () => { fileHistory = stepBack(fileHistory); showFilePlace(); },
  forward: () => { fileHistory = stepForward(fileHistory); showFilePlace(); },
});

/**
 * The lines of the open file that a right-click names (KYO-737), in the file's source
 * whatever is drawn. A selection the person made names its own lines and, where its words are
 * found in the source, itself; with none, a rendered markdown file gives the block under the
 * pointer and a source file the line. Null where there is nothing to name.
 */
function filePlaceAt(
  file: OpenFile, target: Element, pointer: { x: number; y: number }, own: boolean,
): Pick<FilePlace, "lines" | "chars" | "words"> | null {
  const text = file.text;
  if (text === null) return null;
  const selection = window.getSelection();
  const range = own && selection && !selection.isCollapsed && fileArticle.contains(selection.anchorNode)
    && fileArticle.contains(selection.focusNode) ? selection.getRangeAt(0) : null;
  const picked = range ? selection!.toString().trim() : "";
  const code = fileArticle.querySelector<HTMLElement>(".file-code code");
  if (code) {
    // A source file is drawn as its text, so a place in the page is a place in the file.
    const drawn = drawnOf(code);
    if (range && picked && code.contains(range.startContainer) && code.contains(range.endContainer)) {
      const span = spanPlace(
        text, drawnOffset(drawn, range.startContainer, range.startOffset),
        drawnOffset(drawn, range.endContainer, range.endOffset));
      return span && { ...span, words: firstWords(picked, ANCHOR_WORDS) };
    }
    const caret = caretAt(pointer.x, pointer.y);
    if (!caret) return null;
    const gutter = fileArticle.querySelector<HTMLElement>(".file-gutter");
    let lines: [number, number] | null = null;
    if (gutter?.contains(caret.node)) {
      // The gutter holds one number to a line.
      const number = lineOf(gutter.textContent ?? "", drawnOffset(drawnOf(gutter), caret.node, caret.offset));
      lines = lineAt(text, text.split("\n").slice(0, number).reduce((sum, row) => sum + row.length + 1, 0));
    } else if (code.contains(caret.node)) {
      lines = lineAt(text, drawnOffset(drawn, caret.node, caret.offset));
    }
    return lines && { lines, words: firstWords(text.split("\n")[lines[0] - 1], ANCHOR_WORDS) };
  }
  const holder = fileArticle.querySelector<HTMLElement>(".file-body");
  if (!holder) return null;
  const block = (node: Node | null) => {
    const found = (node instanceof HTMLElement ? node : node?.parentElement)
      ?.closest<HTMLElement>("[data-source-line]");
    return found && holder.contains(found) ? found : null;
  };
  if (range && picked) {
    const first = block(range.startContainer);
    const last = block(range.endContainer) ?? first;
    if (!first || !last) return null;
    const part = sourceOf(first, text);
    const drawn = drawnOf(first);
    const at = drawnOffset(drawn, range.startContainer, range.startOffset);
    const found = nearest(text, picked, part.base + sourceIndex(part.text, drawn.text, at));
    const words = firstWords(picked, ANCHOR_WORDS);
    // Words drawn as they are written are the selection itself; formatted ones name their blocks.
    const span = found >= 0 ? spanPlace(text, found, found + picked.length) : null;
    if (span) return { ...span, words };
    const lines = blockLines(text, Number(first.dataset.sourceLine), Number(last.dataset.sourceEnd));
    return lines && { lines, words };
  }
  const under = block(target);
  if (!under) return null;
  const lines = blockLines(text, Number(under.dataset.sourceLine), Number(under.dataset.sourceEnd));
  return lines && { lines, words: firstWords(under.textContent ?? "", ANCHOR_WORDS) };
}

// KYO-737: a selection in the open file, or a right-click on a block of it, offers Anchor as
// the chat does.
fileArticle.addEventListener("contextmenu", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  const file = openFile;
  const own = ownSelection();
  const place = file && target
    ? filePlaceAt(file, target, { x: event.clientX, y: event.clientY }, own) : null;
  if (!own) window.getSelection()?.removeAllRanges();
  if (!file || !place) return;
  event.preventDefault();
  const session = isPerSession(fileRoots, file.root) ? filesSession : null;
  showAnchorMenu(event.clientX, event.clientY, {
    kind: "path", root: file.root, path: file.path, ...place, ...(session ? { session } : {}),
  });
});
document.getElementById("side-linked-more")!.addEventListener("click", () => {
  allLinks = !allLinks;
  drawChatLinks();
});

drawViews();
drawProjects();
drawRecordLists();
drawFilesSide();
drawFile();
drawMentions();
drawRecent();
drawChatLinks();

// ---------------------------------------------------------------- History (KYO-825)

/** The text searched, the chips on, the results so far and the next page's cursor. */
let historyText = "";
const historyRoles = new Set<Role>(["person", "assistant"]);
let historyFound: Found[] = [];
let historyCursor: string | null = null;
let historyProblem: string | null = null;
let historyWaiting = false;
/** Each request's number; an answer to an older one is not drawn. */
let historyAsk = 0;
let historyTimer: number | undefined;
let historyOpened: Opened[] = [];
const historyField = document.querySelector<HTMLInputElement>(".history-search")!;
const historyResults = document.getElementById("history-results")!;

function resetHistory(): void {
  historyText = "";
  historyField.value = "";
  historyFound = [];
  historyCursor = null;
  historyProblem = null;
  historyWaiting = false;
  historyOpened = [];
  readerPlaces = fresh<ReaderPlace>();
  readerSession = null;
  showReaderPlace();
  drawOpened();
}

function enterHistory(): void {
  post("history", { want: "recent" });
  showReaderPlace();
  drawOpened();
  if (current(readerPlaces) === null) historyField.focus();
}

/** Asks for the field's text, from the first page or, with `more`, the next. */
function askHistory(more = false): void {
  const query = searchQuery(historyText, historyRoles, more ? historyCursor : null);
  historyAsk += 1;
  if (!more) {
    historyFound = [];
    historyCursor = null;
  }
  historyProblem = null;
  historyWaiting = query !== null;
  if (query) post("history", { want: "search", ask: historyAsk, ...query });
  drawHistory();
}

function sessionTitle(session: string): string {
  const row = menu?.sessions.sessions.find((item) => item.session_id === session);
  if (row) return sessionLabel(row);
  return `session ${session.slice(0, 8)}`;
}

function resultBlock(found: Found, index: number): HTMLElement {
  const block = document.createElement("div");
  block.className = "history-result";
  block.dataset.result = String(index);
  const head = document.createElement("div");
  head.className = "history-head";
  head.append(
    span("history-who", found.role === "person" ? "You" : "Claude"),
    span("history-when", when(found.at) ?? ""));
  const text = document.createElement("div");
  text.className = "history-text";
  for (const piece of found.excerpt) {
    if (piece.match) text.append(Object.assign(document.createElement("mark"), { textContent: piece.text }));
    else text.append(piece.text);
  }
  block.append(head, text);
  return block;
}

function historyNote(text: string): HTMLElement {
  return Object.assign(document.createElement("div"), { className: "history-note", textContent: text });
}

/** Which session groups of the results are open; all start folded (KYO-836). */
const openSessionGroups = new Set<string>();

function groupBox(session: string, items: { found: Found; index: number }[]): HTMLElement {
  const box = document.createElement("section");
  box.className = "history-group";
  box.dataset.session = session;
  if (openSessionGroups.has(session)) box.classList.add("open");
  const head = document.createElement("button");
  head.className = "history-group-head";
  head.dataset.group = session;
  const newest = items[0].found.at;
  head.append(
    span("chevron", "›"),
    span("history-group-date", when(newest) ?? ""),
    span("history-group-title", sessionTitle(session)),
    span("history-group-count", `${items.length} ${items.length === 1 ? "match" : "matches"}`));
  const body = document.createElement("div");
  body.className = "history-group-body";
  body.append(...items.map(({ found, index }) => resultBlock(found, index)));
  box.append(head, body);
  return box;
}

function drawHistory(): void {
  const rows: HTMLElement[] = groupBySession(historyFound).map(({ session, items }) => groupBox(session, items));
  const asked = searchQuery(historyText, historyRoles, null) !== null;
  if (historyProblem) rows.push(historyNote(historyProblem));
  else if (historyWaiting && !historyFound.length) rows.push(historyNote("Searching…"));
  else if (asked && !historyFound.length) rows.push(historyNote("Nothing said in this project holds it."));
  else if (!asked && historyText.trim()) rows.push(historyNote("Type two letters or more, with You or Claude on."));
  historyResults.replaceChildren(...rows);
  const more = document.getElementById("history-more")!;
  more.hidden = !historyCursor || historyWaiting;
  if (current(readerPlaces) === null) {
    const sessions = new Set(historyFound.map((found) => found.session)).size;
    document.getElementById("history-count")!.textContent = asked && !historyWaiting
      ? `${historyFound.length}${historyCursor ? "+" : ""} ${historyFound.length === 1 && !historyCursor ? "message" : "messages"}`
        + (sessions ? ` in ${sessions} ${sessions === 1 ? "session" : "sessions"}` : "")
      : "";
  }
  document.querySelectorAll<HTMLElement>(".history-filters [data-role]").forEach((chip) => {
    chip.setAttribute("aria-pressed", String(historyRoles.has(chip.dataset.role as Role)));
  });
}

function drawOpened(): void {
  const rows = historyOpened.map((entry, index) => {
    const row = document.createElement("div");
    row.className = "mention-row";
    row.dataset.opened = String(index);
    row.append(
      span("mention-name", entry.words || "(no words)"),
      span("mention-place", `${sessionTitle(entry.session)} · ${when(entry.opened_at) ?? ""}`));
    return row;
  });
  fill(document.getElementById("history-recent")!, rows.length ? rows : [emptyRow("No result opened yet.")]);
}

// ---- drawers (KYO-835): the side bar and the outline slide out under the pointer and stay
// when their icon is pressed, each view and each device keeping its own choice.


function applyDocks(): void {
  document.body.classList.toggle("view-chat", currentView === "chat");
  // KYO-883: a home page shows its own lists in the view's one drawer, which docks as it does
  // beside an open record or file, so a narrow screen can put it away.
  const home = (currentView === "records" && openRecordId === null)
    || (currentView === "files" && openFile === null);
  document.body.classList.toggle("home-page", home);
  document.body.classList.toggle("dock-left", currentView !== "chat" && docked[`left:${currentView}`] === true);
  document.body.classList.toggle("dock-right", docked[`right:${currentView}`] === true);
  document.body.classList.toggle("has-outline",
    (currentView === "history" && !historyReader.hidden)
    || (currentView === "records" && openRecordId !== null && !recordArticle.hidden)
    || (currentView === "files" && openFile !== null && !fileArticle.hidden
      && document.getElementById("file-outline")!.childElementCount > 0));
}

function toggleDock(side: "left" | "right"): void {
  const key = `${side}:${currentView}`;
  docked = { ...docked, [key]: !docked[key] };
  store(DOCK_KEY, docked);
  applyDocks();
}

document.getElementById("menu-icon")!.addEventListener("click", () => toggleDock("left"));
document.getElementById("outline-icon")!.addEventListener("click", () => toggleDock("right"));

// ---- the History reader (KYO-836): a result opens its session here, never in the chat.

interface ReaderPlace { session: string; message: string; key: string }
const historyPane = document.getElementById("view-history")!;
const historyReader = document.getElementById("history-reader")!;
let readerPlaces: History<ReaderPlace> = fresh<ReaderPlace>();
/** The session the reader holds, and its messages by the chat's keys. */
let readerSession: string | null = null;
const readerElements = new Map<string, HTMLElement>();
let readerTurns: Turn[] = [];
let readerAsk = 0;
const sameReaderPlace = (a: ReaderPlace, b: ReaderPlace) => a.session === b.session && a.message === b.message;

/** Opens a message in the History pane: its session read whole, the message lit and faded. */
function openMessage(session: string, message: string, key: string): void {
  readerPlaces = visitPlace(readerPlaces, { session, message, key }, sameReaderPlace);
  showReaderPlace();
}

function showReaderPlace(): void {
  const place = current(readerPlaces);
  drawNav("history", place === null, readerPlaces);
  document.getElementById("history-home-page")!.hidden = place !== null;
  historyReader.hidden = place === null;
  document.getElementById("history-session-side")!.hidden = place === null;
  applyDocks();
  closeSearch();
  if (!place) {
    drawHistory();
    return;
  }
  if (readerSession !== place.session) {
    readerSession = place.session;
    readerElements.clear();
    readerTurns = [];
    historyReader.replaceChildren(historyNote("Reading the session…"));
    document.getElementById("history-outline")!.replaceChildren();
    for (const id of ["history-tags", "history-anchors", "history-attachments"]) {
      document.getElementById(id)!.replaceChildren();
    }
    readerAsk += 1;
    post("history", { want: "turns", session: place.session, ask: readerAsk });
    post("history", { want: "marks", session: place.session, ask: readerAsk });
    document.getElementById("history-count")!.textContent = sessionTitle(place.session);
    return;
  }
  document.getElementById("history-count")!.textContent = sessionTitle(place.session);
  revealInReader(place);
}

/** The reader's element for a message, by the chat's key or, for a reply, its record uuid. */
function readerElement(place: Pick<ReaderPlace, "message" | "key">): HTMLElement | undefined {
  // A session opened from the list names no message, and is read from its start.
  if (!place.message) return undefined;
  return readerElements.get(place.key) ?? readerElements.get(`t:${place.message}`)
    ?? [...readerElements.values()].find((element) => element.dataset.uuid === place.message);
}

function revealInReader(place: Pick<ReaderPlace, "message" | "key">): void {
  const element = readerElement(place);
  if (!element) return;
  element.scrollIntoView({ block: "center" });
  element.classList.remove("history-flash");
  void element.offsetWidth;
  element.classList.add("history-flash");
  element.addEventListener("animationend", () => element.classList.remove("history-flash"), { once: true });
  document.querySelectorAll("#history-outline .outline-row.current").forEach((row) => row.classList.remove("current"));
  document.querySelector(`#history-outline [data-key="${CSS.escape(element.dataset.hkey ?? "")}"]`)?.classList.add("current");
}

/** The session's prompts and replies, oldest first, drawn as the chat draws them. */
function drawReader(turns: Turn[], replies: Reply[]): void {
  readerElements.clear();
  readerTurns = turns;
  const items = [
    ...turns.filter((turn) => turn.text?.trim() || turn.images?.length).map((turn) => ({ at: turn.at, key: `t:${turn.id}`, uuid: turn.id, person: true,
      draw: () => drawTurn(turn, readerSession ?? undefined), text: turn.text })),
    ...replies.filter((reply) => reply.text?.trim()).map((reply) => ({ at: reply.at, key: `r:${reply.message_id}`, uuid: reply.uuid ?? "", person: false,
      draw: () => drawReply({ ...reply, thinking: null }), text: reply.text })),
  ].sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
  const outline: HTMLElement[] = [];
  historyReader.replaceChildren(...items.map((item) => {
    const element = item.draw();
    element.dataset.hkey = item.key;
    element.dataset.uuid = item.uuid;
    readerElements.set(item.key, element);
    const row = document.createElement("div");
    row.className = `outline-row ${item.person ? "person" : "assistant"}`;
    row.dataset.key = item.key;
    // An attached file's line is the server's, not the person's words.
    const words = item.text.split("\n").filter((line) => !ATTACHED_LINE.test(line)).join(" ");
    row.textContent = firstWords(words.replace(/\s+/g, " ").trim(), 8) || "(attachment)";
    row.title = when(item.at) ?? "";
    outline.push(row);
    return element;
  }));
  if (!items.length) historyReader.append(historyNote("This session holds no prompt or reply."));
  document.getElementById("history-outline")!.replaceChildren(...outline);
  drawAttachments();
}

function sideRow(name: string, place: string, onPress: () => void): HTMLElement {
  const row = document.createElement("div");
  row.className = "mention-row";
  row.append(span("mention-name", name), span("mention-place", place));
  row.addEventListener("click", onPress);
  return row;
}

function setKind(id: string, rows: HTMLElement[]): void {
  document.getElementById(id)!.replaceChildren(...(rows.length ? rows : [emptyRow("None.")]));
  document.getElementById(`${id}-count`)!.textContent = rows.length ? String(rows.length) : "";
}

function drawAttachments(): void {
  const rows: HTMLElement[] = [];
  for (const turn of readerTurns) {
    // A pasted image is the turn's own; a file attached to a paragraph is a line of its text.
    const images = turn.images?.length ?? 0;
    const files = (turn.text ?? "").split("\n").filter((line) => ATTACHED_LINE.test(line)).length;
    if (!images && !files) continue;
    const parts = [
      ...(images ? [`${images} ${images === 1 ? "image" : "images"}`] : []),
      ...(files ? [`${files} attached ${files === 1 ? "file" : "files"}`] : []),
    ];
    rows.push(sideRow(parts.join(", "), `in your prompt · ${when(turn.at) ?? ""}`,
      () => revealInReader({ message: turn.id, key: `t:${turn.id}` })));
  }
  setKind("history-attachments", rows);
}

/** The project's sessions in the History side bar, newest first, each opening in the reader. */
function drawHistorySessions(): void {
  const sessions = [...(menu?.sessions.sessions ?? [])]
    .sort((a, b) => (a.first_seen < b.first_seen ? 1 : a.first_seen > b.first_seen ? -1 : 0));
  const rows = sessions.map((row) => {
    const count = typeof row.prompts === "number"
      ? `${row.prompts} ${row.prompts === 1 ? "prompt" : "prompts"} · ` : "";
    const item = sideRow(sessionLabel(row), `${count}${when(row.first_seen) ?? ""}`,
      () => openMessage(row.session_id, "", ""));
    item.dataset.session = row.session_id;
    if (row.live) item.classList.add("live");
    return item;
  });
  fill(document.getElementById("history-sessions")!, rows.length ? rows : [emptyRow("No session yet.")]);
}

/**
 * Where a sent anchor is in the reader. The server gives a reply's anchor its `message`, as
 * the chat keys a reply, and a prompt's its record `uuid`; one whose place was lost has the
 * record's uuid alone, which `readerElement` still finds.
 */
function anchorPlace(anchor: SentMark["anchors"][number]): Pick<ReaderPlace, "message" | "key"> {
  return anchor.message
    ? { message: anchor.message, key: `r:${anchor.message}` }
    : { message: anchor.uuid ?? "", key: `t:${anchor.uuid ?? ""}` };
}

/** The session's sent tags and anchors, each opening the place it belongs to. */
function drawReaderMarks(marks: SentMark[]): void {
  const byTag = new Map<string, SentMark[]>();
  const anchors: HTMLElement[] = [];
  for (const mark of marks) {
    for (const tag of mark.tags ?? []) byTag.set(tag, [...(byTag.get(tag) ?? []), mark]);
    for (const anchor of mark.anchors ?? []) {
      if (anchor.kind && anchor.kind !== "turn") continue;
      const reply = anchor.message !== undefined || (anchor.role !== undefined && anchor.role !== "person");
      anchors.push(sideRow(
        firstWords((mark.text ?? mark.note ?? "").trim() || "(no words)", 8),
        reply ? "on a reply" : "on a prompt",
        () => revealInReader(anchorPlace(anchor))));
    }
  }
  const tags = [...byTag].sort(([a], [b]) => a.localeCompare(b)).map(([tag, found]) => {
    const first = found[0].anchors?.find((anchor) => !anchor.kind || anchor.kind === "turn");
    return sideRow(`#${tag}`, `${found.length} ${found.length === 1 ? "note" : "notes"}`,
      () => { if (first) revealInReader(anchorPlace(first)); });
  });
  setKind("history-tags", tags);
  setKind("history-anchors", anchors);
}

document.getElementById("history-outline")!.addEventListener("click", (event) => {
  const row = (event.target as Element).closest<HTMLElement>("[data-key]");
  const element = row ? readerElements.get(row.dataset.key!) : undefined;
  if (element) revealInReader({ message: element.dataset.uuid ?? "", key: row!.dataset.key! });
});

historyField.addEventListener("input", () => {
  historyText = historyField.value;
  window.clearTimeout(historyTimer);
  historyTimer = window.setTimeout(() => askHistory(), 300);
});
historyField.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  historyField.value = historyText = "";
  window.clearTimeout(historyTimer);
  askHistory();
});
document.querySelectorAll<HTMLElement>(".history-filters [data-role]").forEach((chip) => {
  chip.addEventListener("click", () => {
    const role = chip.dataset.role as Role;
    if (historyRoles.has(role)) historyRoles.delete(role);
    else historyRoles.add(role);
    askHistory();
  });
});
document.getElementById("history-more")!.addEventListener("click", () => askHistory(true));
historyResults.addEventListener("click", (event) => {
  const group = (event.target as Element).closest<HTMLElement>("[data-group]");
  if (group) {
    const session = group.dataset.group!;
    if (openSessionGroups.has(session)) openSessionGroups.delete(session);
    else openSessionGroups.add(session);
    group.closest(".history-group")!.classList.toggle("open", openSessionGroups.has(session));
    return;
  }
  const block = (event.target as Element).closest<HTMLElement>("[data-result]");
  const found = block ? historyFound[Number(block.dataset.result)] : undefined;
  if (!found) return;
  post("history", { want: "opened", session: found.session, message: found.message, words: plainWords(found.excerpt) });
  openMessage(found.session, found.message, chatKey(found));
});
document.getElementById("history-recent")!.addEventListener("click", (event) => {
  const row = (event.target as Element).closest<HTMLElement>("[data-opened]");
  const entry = row ? historyOpened[Number(row.dataset.opened)] : undefined;
  if (!entry) return;
  post("history", { want: "opened", session: entry.session, message: entry.message, words: entry.words });
  openMessage(entry.session, entry.message, `t:${entry.message}`);
});
// Command-Shift-F opens History with its field focused; Command-F stays the page's find.
document.addEventListener("keydown", (event) => {
  if (event.metaKey && event.shiftKey && !event.altKey && !event.ctrlKey && event.key.toLowerCase() === "f") {
    event.preventDefault();
    historyFrames.history();
  }
});

const historyFrames = {
  /** Command-Shift-F, when the app's menu takes the key before the page sees it. */
  history(): void {
    if (currentView !== "history") showView("history");
    if (current(readerPlaces) !== null) {
      readerPlaces = visitPlace(readerPlaces, null, sameReaderPlace);
      showReaderPlace();
    }
    historyField.focus();
    historyField.select();
  },

  historySearched(frame: { ask: number; status?: number; document?: unknown; error?: string | null }): void {
    if (frame.ask !== historyAsk) return;
    historyWaiting = false;
    const read = frame.status === 200 ? foundOf(frame.document) : null;
    if (!read) {
      historyProblem = frame.error || "The server did not answer the search.";
    } else {
      historyFound = [...historyFound, ...read.found];
      historyCursor = read.cursor;
    }
    drawHistory();
  },

  historyTurns(frame: { ask: number; session: string; status?: number; document?: unknown; error?: string | null }): void {
    if (frame.ask !== readerAsk || frame.session !== readerSession) return;
    if (frame.status !== 200) {
      historyReader.replaceChildren(historyNote(frame.error || "The server did not read the session."));
      return;
    }
    const document_ = frame.document as { turns?: Turn[]; replies?: Reply[] };
    drawReader(document_.turns ?? [], document_.replies ?? []);
    const place = current(readerPlaces);
    if (place && place.session === frame.session) {
      if (place.message) revealInReader(place);
      else historyPane.scrollTop = 0;
    }
  },

  historyMarks(frame: { ask: number; session: string; status?: number; document?: unknown }): void {
    if (frame.ask !== readerAsk || frame.session !== readerSession || frame.status !== 200) return;
    drawReaderMarks(((frame.document as { marks?: SentMark[] })?.marks) ?? []);
  },

  historyRecent(frame: { status?: number; document?: unknown }): void {
    const entries = frame.status === 200 ? openedEntries(frame.document) : null;
    if (!entries) return;
    historyOpened = entries;
    drawOpened();
  },
};

navIcons("history", {
  home: () => { readerPlaces = visitPlace(readerPlaces, null, sameReaderPlace); showReaderPlace(); },
  back: () => { readerPlaces = stepBack(readerPlaces); showReaderPlace(); },
  forward: () => { readerPlaces = stepForward(readerPlaces); showReaderPlace(); },
});
drawNav("history", true, readerPlaces);
drawHistory();
drawOpened();

(window as any).kyo = Object.assign(kyo, recordFrames, fileFrames, historyFrames);
