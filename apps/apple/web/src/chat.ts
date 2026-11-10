// The read-only chat. It holds the objects `kyo serve` sent and draws them; it decides nothing
// about a session. Each object is one element, addressed by the object's own id, and a later
// version replaces it in place.
import { agentKind, shortModel } from "./agents";
import {
  type FilePlace, type Place, blockLines, drawnSpan, fileLine, heldLine, lineAt, lineOf, nearest,
  placeAnchor, placeKey, recordLine as placedRecord, samePlace, scrollToShow, sentenceAt, shiftLines, sourceIndex,
  sourceSpan, spanPlace,
} from "./marks";
import {
  type Answer, type Entry, type Mapping, type Mention, type RootInfo, type TreeRow, HIGHLIGHT_CHARS,
  answerOf, dirKey, entriesOf, entryOf, fileKind, folderOf, foldersAbove, headOf, hrefPath, isDotfile, isPerSession,
  languageOf, lineCount, linkPlace, localPath, looksOutside, mentioned, nameOf, pathSpans, rootLabel,
  rootShown, servedRoots, serverPath, sizeWords, stands, treeRows, wholePath,
} from "./files";
import { highlight, render } from "./pipeline";
import {
  type Item, type Line, type LinkTarget, type Narrowing, type RecordRow, awaitingIds, byKind, pressed,
  dispatchableIds, historyFrom, historyRows, isFolded, narrow, newestFirst, parseLink, prefixOf,
  recordOf, recordPage, refParts, visit, when,
} from "./records";
import {
  type History, canBack, canForward, current, fresh, stepBack, stepForward, visitPlace, without,
} from "./history";
import { SendGate } from "./sending";
import { findSpans, fold, queryTerms, step, tally } from "./search";
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
interface SessionRow {
  session_id: string; title: string | null; live: boolean; first_seen: string;
  remote_control_url: string | null;
  last_seen?: string | null; ended_at?: string | null;
}
interface Changes { added: number; removed: number; untracked: { files: number } }
interface Menu {
  project: string; current: string | null; sessions: { sessions: SessionRow[] };
  changes: Changes | null;
}

const chat = document.getElementById("chat")!;
const live = document.getElementById("live")!;
const scroller = document.scrollingElement!;
const STICK_DISTANCE = 80;
const elements = new Map<string, HTMLElement>();
const rendered = new Map<string, string>();

function log(message: string): void {
  (window as any).webkit?.messageHandlers?.log?.postMessage(message);
}
window.addEventListener("error", (event) => log(`error: ${event.message}`));

function atBottom(): boolean {
  return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < STICK_DISTANCE;
}

/**
 * When the main conversation last replied or called a tool. A subagent's work and a
 * background task's are not counted: they do not read the main conversation's cache.
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
  if (sessionEnded) document.getElementById("interrupt")!.hidden = true;
  drawWorking();
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
  if (sessionEnded) {
    const when = endedAt ? new Date(endedAt) : null;
    label.title = "";
    label.className = "";
    label.textContent = when && Number.isFinite(when.getTime())
      ? `Session ended ${when.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`
      : "Session ended";
    return;
  }
  const ago = lastMainActivity ? Date.now() - new Date(lastMainActivity).getTime() : NaN;
  const expires = facts?.cache?.expires_at;
  label.title = expires
    ? `The prompt cache expires at ${new Date(expires).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
    : "";
  if (!Number.isFinite(ago)) {
    label.textContent = "";
    return;
  }
  const seconds = Math.max(0, Math.floor(ago / 1000));
  const minutes = Math.floor(seconds / 60);
  const words = minutes >= 60
    ? `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`
    : `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
  label.textContent = `Last activity: ${words} ago`;
  label.className = ago > ACTIVITY_RED_MS ? "red" : ago > ACTIVITY_AMBER_MS ? "amber" : "";
}
setInterval(drawActivity, 1000);

/** Runs a change to the page, following it down only if the reader was at the end. */
function keepingPlace(change: () => void): void {
  const follow = atBottom();
  change();
  if (follow) scroller.scrollTop = scroller.scrollHeight;
}

function place(key: string, at: string, version: string, build: () => HTMLElement): void {
  if (rendered.get(key) === version) return;
  const element = build();
  element.dataset.key = key;
  element.dataset.at = at;
  const existing = elements.get(key);
  if (existing) {
    existing.replaceWith(element);
  } else {
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
  tag: '<path d="M3.5 11.6V4.5a1 1 0 0 1 1-1h7.1l8.9 8.9a1 1 0 0 1 0 1.4l-6.7 6.7a1 1 0 0 1-1.4 0z"/><circle cx="8" cy="8" r="1.3"/>',
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
function navIcons(prefix: string, steps: { home: () => void; back: () => void; forward: () => void }): void {
  for (const kind of ["home", "back", "forward"] as const) {
    const button = document.getElementById(`${prefix}-${kind}`)!;
    button.replaceChildren(icon(kind));
    button.addEventListener("click", () => {
      if (!(button as HTMLButtonElement).disabled) steps[kind]();
    });
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

function drawImage(turn: Turn, index: number): HTMLElement {
  const key = `${turn.id}/${index}`;
  const image = document.createElement("img");
  image.className = "turn-image";
  const data = imageData.get(key);
  if (data) {
    image.src = data;
  } else {
    image.dataset.image = key;
    (window as any).webkit?.messageHandlers?.image?.postMessage({ turn: turn.id, index });
  }
  return image;
}

/** `attached <absolute path>`, as `kyo serve` writes it for a file on a paragraph (KYO-715). */
const ATTACHED_LINE = /^attached (?:.*\/)?(([0-9a-f]{64})\.(png|jpe?g|gif|webp|pdf))$/;

/** A sent attachment: the image itself, fetched by its id, or a chip naming any other file. */
function drawAttached(line: string, name: string, id: string, extension: string): HTMLElement {
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
    (window as any).webkit?.messageHandlers?.image?.postMessage({ attachment: id });
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
    chip.append(icon("records"), words ? `${record.id} ${words}` : record.id);
    chip.addEventListener("click", () => revealPlace({ kind: "record", id: record.id, words }));
  } else if (file) {
    const words = (file.selection ?? file.words).replace(/\.\.\.$/, "…");
    const span = file.lines[0] === file.lines[1] ? `${file.lines[0]}` : `${file.lines[0]}-${file.lines[1]}`;
    chip.dataset.place = placeKey({ kind: "path", ...file });
    chip.append(icon("page"), `${nameOf(file.path)}:${span}${words ? ` ${words}` : ""}`);
    chip.addEventListener("click", () => revealPlace({
      kind: "path", root: file.root, path: file.path, lines: file.lines, words,
      ...(file.session ? { session: file.session } : {}),
    }));
  }
  return chip;
}

function drawTurn(turn: Turn): HTMLElement {
  const element = document.createElement("div");
  element.className = "turn";
  for (const { index } of turn.images ?? []) element.append(drawImage(turn, index));
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
      element.append(drawAttached(line, attached[1], attached[2], attached[3]));
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
    chip.append(icon("anchor"), words || "words unavailable");
    chip.title = line;
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

function drawWorking(): void {
  const words = workingWords();
  if (working.hidden === !words && workingText.textContent === words) return;
  keepingPlace(() => {
    workingText.textContent = words;
    working.hidden = !words;
  });
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
  (window as any).webkit?.messageHandlers?.select?.postMessage({ session });
}

function sessionLabel(row: SessionRow): string {
  return row.title ?? new Date(row.first_seen).toLocaleString([], {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function drawMenu(): void {
  if (!menu) return;
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

function drawSide(): void {
  const session = menu?.sessions.sessions.find((row) => row.session_id === menu?.current);
  setText("top-title", session?.title ?? menu?.project ?? "");
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
  setText("side-finished", `${finishedOwn.length} finished ${showFinishedTasks ? "⌄" : "›"}`);
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
  setText("side-agents-finished", `${finished.length} finished ${showFinishedAgents ? "⌄" : "›"}`);
  // The status bar lists the running ones: what each is, and since when.
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
    lastMainActivity = "";
    chat.replaceChildren();
    elements.clear();
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
    // Another project's press says nothing about this one.
    if (menu && menu.project !== data.project) startFrame = null;
    menu = data;
    followFilesSession(data.current);
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

  apply(batch: Batch): void {
    keepingPlace(() => {
      if (batch.reset) kyo.clear();
      for (const { at } of [...(batch.replies ?? []), ...(batch.tool_calls ?? [])]) {
        if (at > lastMainActivity) lastMainActivity = at;
      }
      for (const turn of batch.turns ?? []) {
        const version = `${turn.text}|${turn.images?.length ?? 0}`;
        sources.set(`t:${turn.id}`, turn.text ?? "");
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
      paintAnchors();
      refreshSearch();
    });
    if (batch.facts) facts = batch.facts;
    // The final text has arrived, so the in-progress copy goes at once.
    if (liveText !== null && alreadyLanded(liveText)) clearLive();
    drawSide();
    for (const list of [batch.turns, batch.replies, batch.tool_calls, batch.command_outputs]) {
      for (const item of list ?? []) if (item.at > latestAt) latestAt = item.at;
    }
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
    showHeld((frame as { held?: { id: string } | null }).held?.id ?? null);
    kyo.facts(frame as { facts?: Facts | null });
    kyo.terminal(frame as { terminal?: { text: string; working_line: string | null } | null });
    kyo.screen(frame);
  },

  /** A held message's state: `{"outcome": "held" | "delivered" | "retracted", "id"}`. */
  held(frame: { outcome?: string; id?: string }): void {
    showHeld(frame.outcome === "held" ? frame.id ?? null : null);
    if (frame.outcome === "delivered") composerNote.textContent = "";
  },

  /** The server took a message: `held` is its id, or an error to show. */
  sent(frame: { id?: string; error?: string }): void {
    if (frame.error) {
      composerNote.textContent = frame.error;
      // A refusal ends the send, so the prompt stays and Send can be pressed again.
      sendGate.end();
      drawSending();
      return;
    }
    composerNote.textContent = "";
    showHeld(frame.id ?? null);
    // Only the prompt that was sent leaves; the others are still drafts.
    sendGate.end()?.remove();
    drawSending();
    if (!textAreas().length) addBox();
    remember();
  },

  /** A held message taken back, to edit. */
  retracted(frame: { text: string }): void {
    showHeld(null);
    const held = stored<SavedBox | null>(HELD_KEY, null);
    showTexts([frame.text, ...currentTexts()], [...(held ? [held] : []), ...currentBoxes()]);
  },

  /** The server's draft for a session just opened: none yet is `etag: null`. */
  draft(frame: { boxes: SavedBox[]; etag: string | null }): void {
    if (frame.etag === null) {
      // Nothing saved for this session: what is in the boxes becomes its draft.
      adoptShelf();
      remember();
      return;
    }
    loadingDraft = true;
    // The server's draft carries each prompt's marks, so a prompt opens the same on any device.
    const saved = frame.boxes.filter((box) => box.text || box.marks?.length);
    showTexts(saved.map((box) => box.text), saved);
    loadingDraft = false;
    store(CURRENT_KEY, currentBoxes());
    adoptShelf();
  },

  /** The server's answer to a pasted file: its id and path, or why it was refused. */
  attached(frame: { ref: string; id?: string; path?: string; error?: string }): void {
    const upload = uploads.get(frame.ref);
    if (!upload) return;
    uploads.delete(frame.ref);
    const { area, item } = upload;
    const marks = marksOf.get(area);
    if (!marks) return;
    if (frame.error || !frame.id) {
      for (const [id, items] of marks.items) {
        const rest = items.filter((other) => other !== item);
        if (rest.length) marks.items.set(id, rest);
        else marks.items.delete(id);
      }
      composerNote.textContent = frame.error ?? "The file could not be attached.";
    } else {
      item.id = frame.id;
      item.path = frame.path;
      delete item.pending;
    }
    remember();
    drawMarks(area);
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
    if (heldId) showHeld(heldId);
    drawWorking();
    drawSide();
  },
};

// ------------------------------------------------------------------------------ the composer

const pasted = new WeakMap<HTMLTextAreaElement, string[]>();
const boxes = document.getElementById("boxes")!;
const composerNote = document.getElementById("composer-note")!;

/**
 * Makes a box as tall as its text. The text is measured with the box's height released, and
 * for that moment the composer has less to scroll over: a view near the end of the released
 * box is moved up to the shorter end and is not moved back when the height returns. Each key
 * typed in the last box so put its last lines under the composer's edge (KYO-727), so the
 * composer's place is put back once the height is set.
 */
function fitBox(area: HTMLTextAreaElement): void {
  const place = boxes.scrollTop;
  area.style.height = "auto";
  area.style.height = `${area.scrollHeight}px`;
  boxes.scrollTop = place;
}

function textAreas(): HTMLTextAreaElement[] {
  return Array.from(boxes.querySelectorAll("textarea"));
}

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
interface SavedBox { text: string; marks?: { line: number; items: Item[] }[] }

interface Marks {
  /** The text the ids were last matched to. */
  text: string;
  /** One id for each line of the text. */
  ids: number[];
  items: Map<number, Item[]>;
  /** What a removed line held, by that line's text, so undoing or pasting it back restores it. */
  lost: Map<string, Item[]>;
  gutter: HTMLElement;
  mirror: HTMLElement;
  ghost: HTMLButtonElement;
}

const marksOf = new WeakMap<HTMLTextAreaElement, Marks>();
let nextLine = 1;
let openMark: { area: HTMLTextAreaElement; id: number } | null = null;
const markPanel = document.createElement("div");
markPanel.className = "mark-panel";

const TAGS_KEY = "composer-tags";
let storeTags: string[] = [];

/** Tags to suggest: those on the prompts now, then those used on this device, newest first. */
function knownTags(): string[] {
  const here = textAreas().flatMap((area) => Array.from(marksOf.get(area)?.items.values() ?? []).flat())
    .flatMap((item) => (item.kind === "tag" ? [item.name] : []));
  return Array.from(new Set([...here, ...stored<string[]>(TAGS_KEY, []), ...storeTags]));
}

/** Carries each mark to where its paragraph went in an edit. */
function trackEdit(area: HTMLTextAreaElement): void {
  const marks = marksOf.get(area)!;
  const before = marks.text.split("\n");
  const shift = shiftLines(marks.text, area.value, marks.ids, () => nextLine++, area.selectionStart);
  for (const [from, into] of shift.merged) {
    const moved = marks.items.get(from);
    if (moved) marks.items.set(into, [...(marks.items.get(into) ?? []), ...moved]);
    marks.items.delete(from);
  }
  for (const id of shift.dropped) {
    const held = marks.items.get(id);
    const text = before[marks.ids.indexOf(id)];
    if (held && text) marks.lost.set(text, held);
    marks.items.delete(id);
  }
  const after = area.value.split("\n");
  shift.ids.forEach((id, line) => {
    // A removed paragraph that is back, by undo or by paste, takes up what it held.
    const back = after[line] && !marks.items.has(id) ? marks.lost.get(after[line]) : undefined;
    if (!back) return;
    marks.items.set(id, back);
    marks.lost.delete(after[line]);
  });
  marks.ids = shift.ids;
  marks.text = area.value;
}

/** Where a line sits beside the text, read from a hidden copy that wraps as the text does. */
function lineTop(marks: Marks, line: number): number {
  return (marks.mirror.children[line] as HTMLElement | undefined)?.offsetTop ?? 0;
}

/** The paragraph the caret is in, or was in when the box was last used. */
function caretLine(area: HTMLTextAreaElement): number {
  return lineOf(area.value, area.selectionStart);
}

const CARET_MARGIN = 8;

/**
 * Scrolls the composer by the least that shows the line the caret is on, and not at all
 * while that line is in view. The line is found in the hidden copy of the text, by the
 * character beside the caret. On the last line of a box the box's buttons are shown too.
 */
function showCaret(area: HTMLTextAreaElement): void {
  const marks = marksOf.get(area)!;
  const caret = area.selectionEnd;
  const start = area.value.lastIndexOf("\n", caret - 1) + 1;
  const node = marks.mirror.children[lineOf(area.value, caret)]?.firstChild;
  if (!(node instanceof Text)) return;
  // The character after the caret, or at a paragraph's end the one before it.
  const from = Math.max(0, Math.min(caret - start, node.length - 1));
  const range = document.createRange();
  range.setStart(node, from);
  range.setEnd(node, Math.min(from + 1, node.length));
  const line = range.getBoundingClientRect();
  if (!line.height) return;
  const view = boxes.getBoundingClientRect();
  const lastLine = area.getBoundingClientRect().bottom - line.bottom < line.height / 2;
  const bottom = lastLine ? area.closest(".box")!.getBoundingClientRect().bottom : line.bottom;
  boxes.scrollTop += scrollToShow(
    line.top - CARET_MARGIN, bottom + CARET_MARGIN, view.top, view.bottom,
  );
}

/** The icon of one thing a mark holds: a record's and a file's are their views'. */
function itemIcon(item: Item): keyof typeof ICONS {
  return item.kind === "record" ? "records" : item.kind === "path" ? "page" : item.kind;
}

function drawMarks(area: HTMLTextAreaElement): void {
  const marks = marksOf.get(area)!;
  marks.mirror.replaceChildren(...area.value.split("\n").map((text) => {
    const line = document.createElement("div");
    line.textContent = text || "\u200b";
    return line;
  }));
  const held = marks.ids.flatMap((id, line) => {
    const items = marks.items.get(id) ?? [];
    if (!items.length) return [];
    // One thing shows as what it is, and several as their number.
    const mark = boxButton("mark held", items.length > 1 ? String(items.length) : "", () => showMark(area, id));
    if (items.length === 1) mark.append(icon(itemIcon(items[0])));
    mark.title = items.map((item) => (item.kind === "tag" ? `#${item.name}`
      : item.kind === "file" ? `attached: ${item.name}` : `anchor: ${item.words}`)).join("\n");
    mark.style.top = `${lineTop(marks, line)}px`;
    return [mark];
  });
  marks.ghost.hidden = true;
  marks.gutter.replaceChildren(marks.ghost, ...held);
  paintAnchors();
  if (openMark?.area === area) {
    if (marks.ids.includes(openMark.id)) showMark(area, openMark.id);
    else hideMark();
  }
}

/** The ghost [+] follows the mouse, on the paragraphs that hold nothing yet. */
function followMouse(area: HTMLTextAreaElement, event: MouseEvent): void {
  const marks = marksOf.get(area)!;
  const y = event.clientY - marks.mirror.getBoundingClientRect().top;
  const lines = Array.from(marks.mirror.children) as HTMLElement[];
  const line = lines.findIndex((element) => y < element.offsetTop + element.offsetHeight);
  const id = marks.ids[line < 0 ? lines.length - 1 : line];
  marks.ghost.hidden = id === undefined || Boolean(marks.items.get(id)?.length);
  marks.ghost.dataset.id = String(id);
  marks.ghost.style.top = `${lineTop(marks, marks.ids.indexOf(id))}px`;
}

function hideMark(): void {
  openMark = null;
  markPanel.remove();
}

/** The list of what one mark holds, each removable, and a field that adds a tag. */
function showMark(area: HTMLTextAreaElement, id: number): void {
  const marks = marksOf.get(area)!;
  const refocus = openMark?.area === area && openMark.id === id
    && markPanel.contains(document.activeElement);
  openMark = { area, id };
  const change = (items: Item[]) => {
    if (items.length) marks.items.set(id, items);
    else marks.items.delete(id);
    remember();
    drawMarks(area);
  };
  const held = marks.items.get(id) ?? [];
  const chips = held.map((item, index) => {
    const chip = document.createElement("span");
    chip.className = "mark-item";
    if (item.kind === "tag") {
      chip.textContent = `#${item.name}`;
    } else if (item.kind === "file") {
      if (item.thumb) {
        const thumb = document.createElement("img");
        thumb.className = "mark-thumb";
        thumb.src = item.thumb;
        chip.append(thumb);
      } else {
        chip.append(icon("file"));
      }
      chip.append(item.pending ? `${item.name} (uploading)` : item.name);
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
    chip.append(boxButton("mark-remove", "×", () => change(held.filter((_, at) => at !== index))));
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
    if (!held.some((item) => item.kind === "tag" && item.name === name)) change([...held, { kind: "tag", name }]);
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
      area.focus();
    }
    const name = field.value.trim().replace(/^#/, "");
    if (event.key !== "Enter" || !name) return;
    event.preventDefault();
    addTag(name);
  });
  markPanel.replaceChildren(...chips, field, suggestions);
  suggest();
  markPanel.style.top = `${marks.gutter.offsetTop + lineTop(marks, marks.ids.indexOf(id)) + 24}px`;
  area.closest(".box")!.append(markPanel);
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
let lastArea: HTMLTextAreaElement | null = null;

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
    if (node.parentElement?.closest(".thinking")) continue;
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
 * the box was left, which a text area keeps while the focus is elsewhere. The text is not
 * changed, so an empty paragraph takes the anchor as it is.
 */
function addAnchor(area: HTMLTextAreaElement, line: number | null, item: AnchorItem | Place): void {
  const marks = marksOf.get(area)!;
  const id = marks.ids[line ?? caretLine(area)];
  const held = marks.items.get(id) ?? [];
  const same = (other: Item) => (item.kind === "anchor"
    ? other.kind === "anchor" && other.key === item.key && other.start === item.start && other.end === item.end
    : (other.kind === "record" || other.kind === "path") && samePlace(other, item));
  if (!held.some(same)) marks.items.set(id, [...held, item]);
  // Typing goes on where it was.
  area.focus();
  remember();
  drawMarks(area);
}

function showAnchorMenu(x: number, y: number, item: AnchorItem | Place): void {
  const areas = textAreas();
  const area = lastArea && areas.includes(lastArea) ? lastArea : areas[areas.length - 1];
  const entry = (label: string, line: number | null, className = "") => {
    const button = boxButton(className, label, () => {
      anchorMenu.remove();
      addAnchor(area, line, item);
    });
    return button;
  };
  // The first entry is the caret's paragraph, so the list names the others.
  const caret = caretLine(area);
  const lines = area.value.split("\n")
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

function anchorsHeld(): AnchorItem[] {
  return textAreas().flatMap((area) => Array.from(marksOf.get(area)?.items.values() ?? []).flat())
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
  const highlights = (CSS as any).highlights;
  if (!highlights) return;
  if (ranges.length) highlights.set("anchored", new (window as any).Highlight(...ranges));
  else highlights.delete("anchored");
  if (sentRanges.length) highlights.set("anchored-sent", new (window as any).Highlight(...sentRanges));
  else highlights.delete("anchored-sent");
}

// ------------------------------------------------------------ search in the chat (KYO-741)

/**
 * What is searched is the drawn text of each prompt and reply, not its source, so that every
 * hit marked is words the person can see; a markdown mark exists only in the source. A
 * reply's thinking and the tool calls are not searched. The drawn text and its folded copy
 * are kept for each element, which is replaced when its text changes, so a key typed walks
 * the elements' texts and not the document.
 */
const searchIndex = new WeakMap<HTMLElement, { drawn: Drawn; folded: string }>();
const SEARCH_DEBOUNCE_MS = 120;
const findBar = document.getElementById("find")!;
const findField = document.getElementById("find-field") as HTMLInputElement;
const findCount = document.getElementById("find-count")!;
interface Hit { key: string; from: number; range: Range }
let hits: Hit[] = [];
let currentHit = -1;
let searchTimer: number | undefined;

function searchable(): HTMLElement[] {
  return Array.from(chat.children).filter((node): node is HTMLElement => {
    const key = (node as HTMLElement).dataset?.key;
    return !!key && (key.startsWith("t:") || key.startsWith("r:"));
  });
}

function indexed(element: HTMLElement) {
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
  const first = drawn.nodes.findLast((held) => held.from <= from);
  const last = drawn.nodes.findLast((held) => held.from < to);
  if (!first || !last) return null;
  const range = document.createRange();
  range.setStart(first.node, Math.min(from - first.from, first.node.length));
  range.setEnd(last.node, Math.min(to - last.from, last.node.length));
  return range;
}

/** Marks every hit of the query and the current one, and shows the count. */
function runSearch(keep?: { key: string; from: number }): void {
  const terms = queryTerms(findField.value);
  const found: Hit[] = [];
  if (terms.length) {
    for (const element of searchable()) {
      const { drawn, folded } = indexed(element);
      for (const [from, to] of findSpans(drawn.text, terms, folded)) {
        const range = drawnRange(drawn, from, to);
        if (range) found.push({ key: element.dataset.key!, from, range });
      }
    }
  }
  // Newest first: the last hit of the page is the first.
  hits = found.reverse();
  const kept = keep ? hits.findIndex((hit) => hit.key === keep.key && hit.from === keep.from) : -1;
  currentHit = kept >= 0 ? kept : hits.length ? Math.min(Math.max(currentHit, 0), hits.length - 1) : -1;
  if (keep && kept < 0 && hits.length) currentHit = 0;
  paintHits();
}

function paintHits(): void {
  findCount.textContent = tally(currentHit, hits.length, !findBar.hidden && findField.value.trim() !== "");
  const highlights = (CSS as any).highlights;
  if (!highlights) return;
  const rest = hits.filter((_, index) => index !== currentHit).map((hit) => hit.range);
  if (rest.length) highlights.set("found", new (window as any).Highlight(...rest));
  else highlights.delete("found");
  if (currentHit >= 0) {
    const current = new (window as any).Highlight(hits[currentHit].range);
    current.priority = 1;
    highlights.set("found-current", current);
  } else {
    highlights.delete("found-current");
  }
}

/** New text has been drawn: the marks are laid again, the current hit staying where it was. */
function refreshSearch(): void {
  if (findBar.hidden) return;
  const at = hits[currentHit];
  runSearch(at ? { key: at.key, from: at.from } : undefined);
}

function moveToHit(forward: boolean): void {
  if (searchTimer !== undefined) {
    window.clearTimeout(searchTimer);
    searchTimer = undefined;
    runSearch();
    if (currentHit >= 0) {
      showHit();
      return;
    }
  }
  currentHit = step(currentHit, hits.length, forward);
  paintHits();
  showHit();
}

function showHit(): void {
  const hit = hits[currentHit];
  if (!hit) return;
  const rect = hit.range.getBoundingClientRect();
  const top = (document.getElementById("top")?.getBoundingClientRect().bottom ?? 0) + 24;
  const bottom = window.innerHeight - 24;
  if (rect.top < top || rect.bottom > bottom) {
    scroller.scrollTop += rect.top - (top + bottom) / 2 + rect.height / 2;
  }
}

function openSearch(): void {
  if (currentView !== "chat") return;
  findBar.hidden = false;
  findField.focus();
  findField.select();
  if (findField.value) runSearch();
}

function closeSearch(): void {
  if (findBar.hidden) return;
  if (searchTimer !== undefined) window.clearTimeout(searchTimer);
  searchTimer = undefined;
  findBar.hidden = true;
  hits = [];
  currentHit = -1;
  paintHits();
}

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
  if (event.key.toLowerCase() === "f" && event.metaKey && !event.ctrlKey && !event.altKey) {
    if (currentView !== "chat") return;
    if (event.target instanceof Node && terminalPane.contains(event.target)) return;
    event.preventDefault();
    openSearch();
  } else if (event.key === "Escape" && !findBar.hidden && currentView === "chat") {
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
function addBox(text = "", saved: SavedBox["marks"] = []): HTMLTextAreaElement {
  const box = document.createElement("div");
  box.className = "box";
  const area = document.createElement("textarea");
  area.rows = 1;
  area.placeholder = "Reply…";
  area.value = text;
  const gutter = document.createElement("div");
  gutter.className = "box-gutter";
  const mirror = document.createElement("div");
  mirror.className = "box-mirror";
  const ghost = boxButton("mark ghost", "+", () => showMark(area, Number(ghost.dataset.id)));
  ghost.title = "Attach to this paragraph";
  const ids = text.split("\n").map(() => nextLine++);
  const marks: Marks = {
    text, ids, gutter, mirror, ghost, lost: new Map(),
    items: new Map(saved.filter((mark) => mark.line < ids.length && mark.items.length)
      .map((mark) => [ids[mark.line], mark.items])),
  };
  marksOf.set(area, marks);
  const words = document.createElement("div");
  words.className = "box-text";
  words.append(area, mirror);
  const body = document.createElement("div");
  body.className = "box-body";
  body.append(gutter, words);
  body.addEventListener("mousemove", (event) => followMouse(area, event));
  body.addEventListener("mouseleave", () => { ghost.hidden = true; });
  // The height was fitted to the text as it wrapped at one width. At another the box is too
  // short or too tall, and a box shorter than its text scrolls the text inside itself when
  // the caret goes to the hidden part (KYO-727). A long draft restored into an empty
  // composer meets this at once where scroll bars take room: its height is what makes the
  // composer's scroll bar appear, and the bar narrows it.
  let fittedWidth = -1;
  new ResizeObserver(() => {
    if (words.clientWidth !== fittedWidth) {
      fittedWidth = words.clientWidth;
      fitBox(area);
    }
    drawMarks(area);
  }).observe(words);
  area.addEventListener("keydown", (event) => listKeys(area, event));
  area.addEventListener("focus", () => { lastArea = area; });
  area.addEventListener("paste", (event) => {
    const files = Array.from(event.clipboardData?.files ?? []);
    if (files.length) {
      // A pasted file belongs to the paragraph the caret is in.
      event.preventDefault();
      for (const file of files) attachFile(area, file);
      return;
    }
    const text = event.clipboardData?.getData("text/plain");
    if (text) pasted.set(area, [...(pasted.get(area) ?? []), text]);
  });
  // An image or PDF dragged in is attached. Any other file is left to the text area, which
  // takes its path as text.
  area.addEventListener("drop", (event) => {
    const files = Array.from(event.dataTransfer?.files ?? [])
      .filter((file) => file.type.startsWith("image/") || file.type === "application/pdf");
    if (!files.length) return;
    event.preventDefault();
    area.focus();
    for (const file of files) attachFile(area, file);
  });
  area.addEventListener("input", () => {
    fitBox(area);
    trackEdit(area);
    drawMarks(area);
    showCaret(area);
    remember();
  });
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
    if (armed === undefined && (area.value || marks.items.size)) {
      remove.textContent = "Delete?";
      armed = window.setTimeout(disarm, 3000);
      return;
    }
    disarm();
    box.remove();
    if (!textAreas().length) addBox().focus();
    remember();
  });
  // Bare text in the accent colour, then the keys that do the same.
  const send = boxButton("box-send", "", () => attemptSend(area));
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
  actions.append(remove, send);
  box.append(body, actions);
  boxes.append(box);
  fitBox(area);
  drawMarks(area);
  return area;
}

const CURRENT_KEY = "composer-current";
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
  return textAreas().map((area) => area.value).filter(Boolean);
}

/** Each prompt with its marks, by the line each is on. */
function currentBoxes(): SavedBox[] {
  // A prompt with an anchor and no words yet is still a draft.
  return textAreas().filter((area) => area.value || marksOf.get(area)!.items.size).map((area) => {
    const marks = marksOf.get(area)!;
    return {
      text: area.value,
      marks: marks.ids.flatMap((id, line) => {
        // A file still uploading is not saved: nothing would finish it after a relaunch.
        const items = marks.items.get(id)?.filter((item) => !(item.kind === "file" && item.pending));
        return items?.length ? [{ line, items }] : [];
      }),
    };
  });
}

/** What this window last kept. Before marks it kept the texts alone. */
function storedBoxes(): SavedBox[] {
  return stored<(SavedBox | string)[]>(CURRENT_KEY, [])
    .map((box) => (typeof box === "string" ? { text: box } : box));
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
  store(CURRENT_KEY, currentBoxes());
  if (draftSave !== undefined) clearTimeout(draftSave);
  draftSave = window.setTimeout(() => {
    draftSave = undefined;
    post("saveDraft", { boxes: currentBoxes() });
  }, DRAFT_SAVE_DELAY_MS);
}

/**
 * Shows these prompts. A prompt whose text is already known keeps its marks, which come from
 * what is on the screen or from `kept`.
 */
function showTexts(texts: string[], kept: SavedBox[] = currentBoxes()): void {
  hideMark();
  boxes.replaceChildren();
  for (const text of texts) addBox(text, kept.find((box) => box.text === text)?.marks);
  if (!texts.length) addBox();
  remember();
}

/** A new prompt is made on purpose: an empty box is reused before another is added. */
function newPrompt(): void {
  (textAreas().find((area) => !area.value) ?? addBox()).focus();
}

function post(name: string, body: unknown): void {
  (window as any).webkit?.messageHandlers?.[name]?.postMessage(body);
}

/** One prompt's text, split at what was pasted, since a paste is sent as a paste. */
function messageSegments(area: HTMLTextAreaElement): { kind: string; text: string }[] {
  const segments: { kind: string; text: string }[] = [];
  const add = (kind: string, text: string) => {
    const last = segments[segments.length - 1];
    if (!text) return;
    if (last?.kind === kind) last.text += text;
    else segments.push({ kind, text });
  };
  let rest = area.value;
  for (const piece of pasted.get(area) ?? []) {
    const at = rest.indexOf(piece);
    if (at < 0) continue;
    add("typed", rest.slice(0, at));
    add("pasted", piece);
    rest = rest.slice(at + piece.length);
  }
  add("typed", rest);
  return segments;
}

const HELD_KEY = "composer-held";
/** The prompt whose Send was answered with the cold-cache warning, until it is pressed again. */
let coldConfirm: HTMLTextAreaElement | null = null;

const uploads = new Map<string, { area: HTMLTextAreaElement; item: FileItem }>();
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

/** Puts a pasted file on the caret's paragraph and sends it to the session's machine. */
async function attachFile(area: HTMLTextAreaElement, file: File): Promise<void> {
  const marks = marksOf.get(area)!;
  const id = marks.ids[caretLine(area)];
  const ref = `upload-${nextUpload++}`;
  const item: FileItem = { kind: "file", id: "", name: file.name || "pasted image", pending: ref };
  marks.items.set(id, [...(marks.items.get(id) ?? []), item]);
  uploads.set(ref, { area, item });
  drawMarks(area);
  item.thumb = await thumbnail(file);
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let at = 0; at < bytes.length; at += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(at, at + 0x8000));
  }
  post("attach", { ref, type: file.type, name: item.name, data: btoa(binary) });
  drawMarks(area);
}

/** A prompt's marks as the server takes them: the line, its anchors and its tags. */
function sentMarks(area: HTMLTextAreaElement): unknown[] {
  const marks = marksOf.get(area)!;
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

function attemptSend(area: HTMLTextAreaElement): void {
  // A prompt already on its way to the server is not posted again.
  if (sendGate.current) return;
  const segments = messageSegments(area);
  if (!segments.length) {
    // A prompt needs some words: the server sends text, and a mark rides on a line of it.
    if (marksOf.get(area)!.items.size) composerNote.textContent = "Add a few words to send this.";
    return;
  }
  if (heldId) {
    composerNote.textContent = "One message is already waiting. Edit it first.";
    return;
  }
  const waiting = Array.from(marksOf.get(area)!.items.values()).flat()
    .some((item) => item.kind === "file" && item.pending);
  if (waiting) {
    composerNote.textContent = "A file is still uploading.";
    return;
  }
  // After the cache has expired the whole context is written again, so Send asks once more.
  const cache = facts?.cache;
  const expired = cache?.expires_at ? new Date(cache.expires_at).getTime() : NaN;
  if (expired < Date.now() && coldConfirm !== area) {
    const tokens = cache?.recache_tokens_if_cold;
    const went = new Date(expired).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    composerNote.textContent = `The cache went cold at ${went}. Sending rewrites ${
      tokens ? `about ${tokenWords(tokens)} tokens` : "the whole context"}. Send again to confirm.`;
    coldConfirm = area;
    window.setTimeout(() => { if (coldConfirm === area) coldConfirm = null; }, 15000);
    return;
  }
  coldConfirm = null;
  if (!sendGate.begin(area.closest<HTMLElement>(".box")!)) return;
  composerNote.textContent = "Sending…";
  drawSending();
  // Kept so that a message taken back to edit returns with its marks.
  const box = currentBoxes().find((saved) => saved.text === area.value);
  if (box) store(HELD_KEY, box);
  post("send", { segments, marks: sentMarks(area) });
}

const heldBox = document.getElementById("held")!;
const heldText = document.getElementById("held-text")!;
let heldId: string | null = null;

function showHeld(id: string | null): void {
  heldId = id;
  heldBox.hidden = !id;
  // Send now (KYO-722) is offered only while the turn runs; an idle screen delivers by itself.
  document.getElementById("held-send-now")!.hidden = !id || screenState !== "working";
  heldText.textContent = screenState === "working"
    ? "Waiting for the turn to finish"
    : "Sending…";
}

document.getElementById("held-edit")!.addEventListener("click", () => {
  if (heldId) post("retract", { id: heldId });
});

document.getElementById("held-send-now")!.addEventListener("click", () => {
  if (heldId) post("sendNow", { id: heldId });
});

document.getElementById("interrupt")!.addEventListener("click", () => post("interrupt", {}));

const LIST_ITEM = /^(\s*)(?:(\d+)\.|([-*]))\s(.*)$/;
const INDENT = "  ";

/** Replaces a range by the editing command, which keeps the box's undo history. */
function replaceRange(area: HTMLTextAreaElement, from: number, to: number, text: string): void {
  const before = area.value;
  area.setSelectionRange(from, to);
  const done = text
    ? document.execCommand("insertText", false, text)
    : document.execCommand("delete");
  if (!done || area.value === before) {
    area.setRangeText(text, from, to, "end");
    area.dispatchEvent(new Event("input"));
  }
}

/** Numbered and bulleted lists: Return continues one, and an empty item ends it. */
function listKeys(area: HTMLTextAreaElement, event: KeyboardEvent): void {
  if (event.metaKey || area.selectionStart !== area.selectionEnd) return;
  const caret = area.selectionStart;
  const start = area.value.lastIndexOf("\n", caret - 1) + 1;
  const line = area.value.slice(start, caret);
  const item = line.match(LIST_ITEM);
  if (event.key === "Tab" && item) {
    event.preventDefault();
    const nextEnd = area.value.indexOf("\n", caret);
    const end = nextEnd < 0 ? area.value.length : nextEnd;
    const whole = area.value.slice(start, end);
    if (event.shiftKey) {
      if (whole.startsWith(INDENT)) replaceRange(area, start, end, whole.slice(INDENT.length));
    } else {
      const renumbered = item[2] ? whole.replace(/\d+\./, "1.") : whole;
      replaceRange(area, start, end, INDENT + renumbered);
    }
    return;
  }
  if (event.key !== "Enter" || !item) return;
  const [, indent, number, bullet, content] = item;
  if (!content) {
    // Return or Shift-Return on an empty item ends the list, on that same line.
    event.preventDefault();
    replaceRange(area, start, caret, "");
    return;
  }
  if (event.shiftKey) return;
  event.preventDefault();
  const marker = number ? `${Number(number) + 1}.` : bullet;
  replaceRange(area, caret, caret, `\n${indent}${marker} `);
}

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

const startBoxes = storedBoxes().filter((box) => box.text || box.marks?.length);
showTexts(startBoxes.map((box) => box.text), startBoxes);
const newPromptButton = document.getElementById("new-prompt")!;
// The click leaves the focus where it is until the new prompt takes it.
newPromptButton.addEventListener("mousedown", (event) => event.preventDefault());
newPromptButton.addEventListener("click", newPrompt);
// Cmd-Return sends the prompt being typed in, or the only one there is.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || !event.metaKey) return;
  // A key typed in the terminal is the terminal's.
  if (event.target instanceof Node && terminalPane.contains(event.target)) return;
  const areas = textAreas();
  const focused = areas.find((area) => area === document.activeElement);
  const target = focused ?? (areas.length === 1 ? areas[0] : undefined);
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
];
let currentView = "chat";
/** Where the transcript was when another view replaced it. */
let chatPlace: { top: number; following: boolean } | null = null;

function drawViews(): void {
  document.getElementById("views")!.replaceChildren(...VIEWS.map((view) => {
    const item = document.createElement("button");
    item.className = "view-item";
    if (view.id === currentView) item.setAttribute("aria-current", "page");
    item.append(icon(view.icon), view.name);
    item.addEventListener("click", () => showView(view.id));
    return item;
  }));
}

function showView(id: string): void {
  if (id === currentView) return;
  if (currentView === "chat") chatPlace = { top: scroller.scrollTop, following: atBottom() };
  for (const view of VIEWS) document.body.classList.toggle(`view-${view.id}`, view.id === id);
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
  VIEWS.find((view) => view.id === id)?.entered?.();
}

// --------------------------------------------------------------------- Records (KYO-732)

const FILTERS_KEY = "records-filters";
const FOLDS_KEY = "records-folds";
const REVEAL_MS = 1600;
const recordsPane = document.getElementById("view-records")!;
const recordsNote = document.getElementById("records-note")!;
const recordsLanding = document.getElementById("records-landing")!;
const recordArticle = document.getElementById("record")!;
const recordsList = document.getElementById("records-list")!;
const recordsHistoryList = document.getElementById("records-history")!;

let recordRows: RecordRow[] = [];
let readyRecords = new Set<string>();
let awaitingRecords = new Set<string>();
/** Whether the lists have been asked for, and whether an answer has come. */
let listsAsked = false;
let listsRead = false;
let listsProblem: string | null = null;
const narrowing: Narrowing = {
  open: false, dispatchable: false, awaiting: false, search: "",
  ...stored<Partial<Narrowing>>(FILTERS_KEY, {}),
};
narrowing.search = "";
let kindFolds = stored<Record<string, boolean>>(FOLDS_KEY, {});
let viewedRecords: string[] = [];
/** The records opened, in order, with the landing page as home (KYO-738). */
let recordPlaces: History<string> = fresh();
/** The one record that is open, or null on the landing page. */
let openRecordId: string | null = null;
/** The open record as the server last sent it, and the text it was sent as. */
let openRecordDoc: Record<string, unknown> | null = null;
let openRecordText = "";
let openRecordProblem: string | null = null;
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
  if (row.status && row.status !== "open") item.classList.add("closed");
  item.dataset.open = row.id;
  const id = span("rec-id", row.id);
  const title = span("rec-title", row.title);
  if (place === "side") {
    const head = document.createElement("div");
    head.className = "rec-row-head";
    head.append(id, kindLabel(row.kind));
    item.append(head, title);
  } else {
    item.append(id, title);
    if (row.status && row.status !== "open") item.append(span("rec-status", row.status));
  }
  return item;
}

function emptyRow(text: string): HTMLElement {
  const row = document.createElement("div");
  row.className = "rec-empty";
  row.textContent = text;
  return row;
}

/** Replaces a list's rows and leaves it scrolled where it was. */
function fill(holder: HTMLElement, rows: HTMLElement[]): void {
  const place = holder.scrollTop;
  holder.replaceChildren(...rows);
  holder.scrollTop = place;
}

/** The side bar's list and history, the landing page's groups, and the filters' state. */
function drawRecordLists(): void {
  document.querySelectorAll<HTMLButtonElement>(".chip[data-filter]").forEach((chip) => {
    const name = chip.dataset.filter as "open" | "dispatchable" | "awaiting";
    chip.setAttribute("aria-pressed", String(narrowing[name]));
  });
  document.querySelectorAll<HTMLInputElement>(".records-search").forEach((field) => {
    if (field.value !== narrowing.search) field.value = narrowing.search;
  });
  const shown = narrow(newestFirst(recordRows), narrowing, readyRecords, awaitingRecords);
  const nothing = !listsRead ? "Reading the records." : "No record matches.";
  fill(recordsList, shown.length ? shown.map((row) => recordRow(row, "side")) : [emptyRow(nothing)]);
  fill(
    recordsHistoryList,
    viewedRecords.length
      ? historyRows(viewedRecords, recordRows).map((row) => recordRow(row, "side"))
      : [emptyRow("No record has been viewed.")]);

  const groups: HTMLElement[] = [];
  for (const group of byKind(shown)) {
    const folded = isFolded(group.kind, group.rows.length, kindFolds);
    const head = document.createElement("div");
    head.className = folded ? "kind-head" : "kind-head open";
    head.setAttribute("role", "button");
    head.setAttribute("aria-expanded", String(!folded));
    head.append(span("chevron", "›"), kindLabel(group.kind), span("kind-count", String(group.rows.length)));
    head.addEventListener("click", () => {
      kindFolds = { ...kindFolds, [group.kind]: !folded };
      store(FOLDS_KEY, kindFolds);
      drawRecordLists();
    });
    groups.push(head);
    if (!folded) groups.push(...group.rows.map((row) => recordRow(row, "landing")));
  }
  document.getElementById("records-groups")!.replaceChildren(
    ...(groups.length ? groups : [emptyRow(nothing)]));

  recordsNote.hidden = !listsProblem;
  recordsNote.textContent = listsProblem ? `The records could not be read. ${listsProblem}` : "";
  drawNav("records", openRecordId === null, recordPlaces);
  document.getElementById("records-count")!.textContent = openRecordId !== null || !listsRead
    ? ""
    : shown.length === recordRows.length
      ? plural(recordRows.length, "record")
      : `${shown.length} of ${plural(recordRows.length, "record")}`;
}

function recordLine(line: Line, wide: boolean): HTMLElement {
  const row = document.createElement("div");
  row.className = wide ? "rec-line wide" : "rec-line";
  const label = span("rec-label", "");
  label.append(linked(line.label));
  const text = span("rec-line-text", "");
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
  for (const line of item.lines ?? []) {
    element.append(recordLine(line, section === "Reports" && line.label.includes(" · ")));
  }
  for (const folded of item.folded ?? []) {
    const key = `${item.ref ?? section}/${folded.label}`;
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = folded.label;
    details.append(summary);
    // A brief is long, so it is drawn when it is first opened.
    const draw = () => {
      if (details.open && details.childElementCount === 1) details.append(prose(folded.text));
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

function drawRecord(): void {
  recordsLanding.hidden = openRecordId !== null;
  recordArticle.hidden = openRecordId === null;
  if (openRecordId === null) {
    recordArticle.replaceChildren();
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
  const page = recordPage(openRecordDoc, titles);
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
  recordsPane.scrollTop = place;
  if (revealRef) {
    const target = [...recordArticle.querySelectorAll<HTMLElement>("[data-ref]")]
      .find((element) => element.dataset.ref === revealRef);
    revealRef = null;
    if (target) {
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
  }): void {
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
  },

  /** One record as `kyo show --json` prints it, or why it could not be read. */
  record(frame: { id: string; document?: Record<string, unknown> | null; error?: string | null }): void {
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

document.querySelectorAll<HTMLInputElement>(".records-search").forEach((field) => {
  field.addEventListener("input", () => {
    narrowing.search = field.value;
    drawRecordLists();
  });
  field.addEventListener("keydown", (event) => {
    if (event.key === "Escape") field.blur();
  });
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
let allMentions = false;
const MENTIONS_SHOWN = 6;
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
    if (inChat) paintAnchors();
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
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") pathMenu.remove();
});

// ----------------------------------------------------------------------- Files (KYO-736)

const FILES_ROOT_KEY = "files-root";
const FILES_DOTFILES_KEY = "files-dotfiles";
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

/** The files mentioned in the session, newest first, in the chat's side bar and in Files'. */
function drawMentions(): void {
  const rows = mentions.map(mentionRow);
  fill(
    document.getElementById("files-mentioned")!,
    rows.length ? rows : [emptyRow("No file has been mentioned.")]);
  const short = allMentions ? mentions : mentions.slice(0, MENTIONS_SHOWN);
  document.getElementById("side-mentioned")!.replaceChildren(
    ...(short.length ? short.map(mentionRow) : [emptyRow("None yet.")]));
  const more = document.getElementById("side-mentioned-more")!;
  more.hidden = mentions.length <= MENTIONS_SHOWN;
  more.textContent = allMentions ? "Show fewer" : `See all ${mentions.length}`;
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
  const nothing = rootsProblem
    ? "The files could not be read."
    : root && rootProblems.has(root) ? rootProblems.get(root)!
    : !rootsRead || (root && !listings.has(dirKey(root, "")))
      ? "Reading the files."
      : "This folder shows no file.";
  document.querySelectorAll<HTMLElement>(".files-tree").forEach((holder) => {
    fill(holder, rows.length ? rows.map((row) => treeRow(root!, row)) : [emptyRow(nothing)]);
  });
  drawNav("files", !openFile, fileHistory);
  setText("files-path", openFile ? `${rootLabel(openFile.root)} / ${openFile.path}` : "");
  document.getElementById("files-path")!.title = openFile
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
  const chip = target.closest<HTMLElement>(".files-roots [data-root]");
  if (chip?.dataset.root) {
    chosenRoot = chip.dataset.root;
    store(FILES_ROOT_KEY, chosenRoot);
    openDir(chosenRoot, "");
    drawFilesSide();
    return;
  }
  if (target.closest(".files-dots")) {
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
  const mention = target.closest<HTMLElement>("[data-mention]");
  const item = mention ? mentions[Number(mention.dataset.mention)] : undefined;
  if (item) showFile(item.root, item.path, item.line, item.entry);
});

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
document.getElementById("side-mentioned-more")!.addEventListener("click", () => {
  allMentions = !allMentions;
  drawMentions();
});

drawViews();
drawRecordLists();
drawFilesSide();
drawFile();
drawMentions();

(window as any).kyo = Object.assign(kyo, recordFrames, fileFrames);
