// The read-only chat. It holds the objects `kyo serve` sent and draws them; it decides nothing
// about a session. Each object is one element, addressed by the object's own id, and a later
// version replaces it in place.
import { shiftLines } from "./marks";
import { render } from "./pipeline";
import { type StartFrame, startView } from "./start";

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

/** `on your reply <uuid> "words..."`, as `kyo serve` writes it above an anchored paragraph. */
const ANCHOR_LINE = /^on (?:your reply|my prompt) ([0-9a-f-]{36})(?: in session [0-9a-f-]{36})? (?:"(.*)"|\(words unavailable\))$/;
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
  for (const line of (turn.text ?? "").split("\n")) {
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
    const words = (anchor[2] ?? "").replace(/\.\.\.$/, "…");
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

function drawReply(reply: Reply): HTMLElement {
  const element = document.createElement("article");
  element.className = "reply";
  element.innerHTML = reply.text ? render(reply.text) : "";
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
  row.className = "side-task clickable";
  const name = document.createElement("span");
  name.className = "side-task-name";
  name.textContent = agent.description || agent.agent_type;
  name.title = name.textContent;
  row.append(name);
  const since = [...calls.values()].find((call) => call.uuid === agent.tool_call_uuid)?.at;
  if (running && agentUsage.has(agent.agent_id)) {
    const tokens = document.createElement("span");
    tokens.className = "side-task-time";
    tokens.textContent = tokenWords(agentUsage.get(agent.agent_id)!);
    row.append(tokens);
  }
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
  if (!running) {
    if (!agentCalls.has(agent.agent_id) && !requestedAgents.has(agent.agent_id)) {
      requestedAgents.add(agent.agent_id);
      requestAgent(agent.agent_id);
    }
    const words = agentMeta(agent);
    if (words) {
      const meta = document.createElement("div");
      meta.className = "agent-meta";
      meta.append([words.tokens, words.took].filter(Boolean).join("  ·  "));
      if (words.lines) {
        if (words.tokens || words.took) meta.append("  ·  ");
        meta.append(...words.lines);
      }
      rows.push(meta);
    }
  }
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

function agentMeta(
  agent: Subagent,
): { tokens: string | null; took: string | null; lines: HTMLElement[] | null } | null {
  const usage = deliveries.find((delivery) =>
    delivery.task_id === agent.agent_id && delivery.usage)?.usage;
  const dispatch = agentDispatches.get(agent.agent_id);
  if (!usage?.total_tokens && !usage?.duration_ms && !dispatch) return null;
  return {
    tokens: usage?.total_tokens ? tokenWords(usage.total_tokens) : null,
    took: usage?.duration_ms ? duration(usage.duration_ms) : null,
    lines: dispatch ? counts(dispatch.added, dispatch.removed) : null,
  };
}

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

// The terminal's width is the running session's and is not ours to change, so its text is
// scaled to fit the drawer rather than scrolled sideways.
const terminalPane = document.getElementById("terminal")!;
const TERMINAL_MAX_FONT = 13;
const TERMINAL_PADDING = 32;
let terminalColumns = 0;

function characterWidth(): number {
  const context = document.createElement("canvas").getContext("2d")!;
  context.font = "100px ui-monospace, SFMono-Regular, Menlo, monospace";
  return context.measureText("M").width / 100;
}

const CHARACTER_WIDTH = characterWidth();

function fitTerminal(): void {
  const width = terminalPane.clientWidth - TERMINAL_PADDING;
  if (width <= 0 || !terminalColumns) return;
  const size = Math.min(TERMINAL_MAX_FONT, width / (terminalColumns * CHARACTER_WIDTH));
  terminalPane.style.fontSize = `${size.toFixed(2)}px`;
}

window.addEventListener("resize", fitTerminal);

// Wheel movement over the drawer stays in the drawer, including when it has nothing to scroll.
terminalPane.addEventListener("wheel", (event) => {
  terminalPane.scrollTop += event.deltaY;
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
  document.body.classList.toggle("terminal-open");
  fitTerminal();
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
    terminalPane.textContent = text;
    terminalColumns = Math.max(terminalColumns, ...text.split("\n").map((line) => line.length));
    fitTerminal();
    drawWorking();
  },

  facts(frame: { facts?: Facts | null }): void {
    if (frame.facts) facts = frame.facts;
    drawSide();
  },

  menu(data: Menu): void {
    // Another project's press says nothing about this one.
    if (menu && menu.project !== data.project) startFrame = null;
    menu = data;
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
  }): void {
    agentCalls.set(data.agent_id, data.tool_calls);
    // The latest turn's size, the figure the terminal's agent row shows; the sum counts cache reads per turn.
    if (data.usage) agentUsage.set(data.agent_id, data.usage.latest_turn ?? data.usage.total);
    if (data.dispatch) agentDispatches.set(data.agent_id, data.dispatch);
    drawAgents();
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
      }
      for (const reply of batch.replies ?? []) {
        if (reply.text) landedKeys.push(lettersAndDigits(reply.text));
        // A record with only tool use has nothing to show.
        if (reply.text || reply.thinking) {
          const version = `${reply.text}|${reply.thinking ?? ""}`;
          sources.set(`r:${reply.message_id}`, reply.text ?? "");
          if (reply.uuid) recordKeys.set(reply.uuid, `r:${reply.message_id}`);
          place(`r:${reply.message_id}`, reply.at, version, () => drawReply(reply));
        }
      }
      for (const delivery of batch.deliveries ?? []) {
        deliveries.push(delivery);
        place(`d:${delivery.uuid}`, delivery.at, delivery.text ?? "", () => drawDelivery(delivery));
      }
      for (const call of batch.tool_calls ?? []) {
        const version = `${call.result?.text ?? ""}${call.result?.is_error ?? ""}`;
        calls.set(call.call_id, call);
        place(`c:${call.call_id}`, call.at, version, () => drawToolCall(call));
      }
      for (const output of batch.command_outputs ?? []) {
        place(`o:${output.uuid}`, output.at, output.text, () => drawCommandOutput(output));
      }
      for (const agent of batch.subagents ?? []) subagents.set(agent.agent_id, agent);
      arrange();
      paintAnchors();
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
      return;
    }
    composerNote.textContent = "";
    showHeld(frame.id ?? null);
    // Only the prompt that was sent leaves; the others are still drafts.
    sending?.remove();
    sending = null;
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

function fitBox(area: HTMLTextAreaElement): void {
  area.style.height = "auto";
  area.style.height = `${area.scrollHeight}px`;
}

function textAreas(): HTMLTextAreaElement[] {
  return Array.from(boxes.querySelectorAll("textarea"));
}

/** A button inside a box. It leaves the focus in the text, so an empty box does not close under it. */
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
type Item = TagItem | AnchorItem | FileItem;
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
    if (items.length === 1) mark.append(icon(items[0].kind));
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
    } else {
      // An anchor's chip goes to the place it names.
      const place = boxButton("mark-anchor", item.words, () => reveal(item));
      place.prepend(icon("anchor"));
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

/** The text range a right-click names: the selection if the text holds it, else its blocks. */
function spanAt(holder: HTMLElement, key: string, target: HTMLElement): Omit<AnchorItem, "kind" | "key" | "session"> | null {
  if (target.closest(".thinking")) return null;
  const source = sources.get(key)!;
  const points = (index: number) => Array.from(source.slice(0, index)).length;
  const span = (from: number, to: number, words: string, exact = false) =>
    ({ start: points(from), end: points(to), words: firstWords(words, ANCHOR_WORDS), exact });
  const selection = window.getSelection();
  const selected = selection && !selection.isCollapsed
    && holder.contains(selection.anchorNode) && holder.contains(selection.focusNode);
  const picked = selected ? selection.toString().trim() : "";
  const found = picked ? source.indexOf(picked) : -1;
  if (found >= 0) return span(found, found + picked.length, picked, true);
  // Formatted text reads differently from its source, so the blocks it lies in are named.
  const block = (node: Node | null) =>
    (node instanceof HTMLElement ? node : node?.parentElement)?.closest<HTMLElement>("[data-source-line]");
  const range = selected ? selection.getRangeAt(0) : null;
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

/** Puts an anchor on a paragraph of a prompt, or on a new paragraph at its end. */
function addAnchor(area: HTMLTextAreaElement, line: number | null, item: AnchorItem): void {
  const marks = marksOf.get(area)!;
  area.focus();
  if (line === null && area.value) replaceRange(area, area.value.length, area.value.length, "\n");
  const id = marks.ids[line ?? marks.ids.length - 1];
  const held = marks.items.get(id) ?? [];
  const same = (other: Item) => other.kind === "anchor" && other.key === item.key
    && other.start === item.start && other.end === item.end;
  if (!held.some(same)) marks.items.set(id, [...held, item]);
  if (line === null) area.setSelectionRange(area.value.length, area.value.length);
  remember();
  drawMarks(area);
}

function showAnchorMenu(x: number, y: number, item: AnchorItem): void {
  const areas = textAreas();
  const area = lastArea && areas.includes(lastArea) ? lastArea : areas[areas.length - 1];
  const entry = (label: string, line: number | null, className = "") => {
    const button = boxButton(className, label, () => {
      anchorMenu.remove();
      addAnchor(area, line, item);
    });
    return button;
  };
  const caretLine = area.value.slice(0, area.selectionStart).split("\n").length - 1;
  const lines = area.value.split("\n")
    .map((text, line) => ({ text, line }))
    .filter(({ text }) => text.trim())
    .sort((a, b) => Number(b.line === caretLine) - Number(a.line === caretLine));
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

chat.addEventListener("contextmenu", (event) => {
  const target = event.target as HTMLElement;
  const holder = target.closest<HTMLElement>("#chat > [data-key]");
  const key = holder?.dataset.key;
  const session = menu?.current;
  if (!holder || !key || !sources.has(key) || !session) return;
  const span = spanAt(holder, key, target);
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
  anchors: { role: string; message?: string; uuid?: string; start?: number; end?: number; stale?: boolean }[];
}
let sentMarks_: SentMark[] = [];
/** What is drawn for the sent marks, so the mouse can find the paragraph behind a place. */
let sentPlaces: { mark: SentMark; range: Range | null; blocks: HTMLElement[] }[] = [];

function sentItems(): { mark: SentMark; item: AnchorItem }[] {
  return sentMarks_.flatMap((mark) => mark.anchors.flatMap((anchor) => {
    if (anchor.stale || anchor.start === undefined || anchor.end === undefined) return [];
    const key = anchor.message ? `r:${anchor.message}` : `t:${anchor.uuid}`;
    const item: AnchorItem = {
      kind: "anchor", key, session: "", start: anchor.start, end: anchor.end, words: "", exact: true,
    };
    return [{ mark, item }];
  }));
}

/** Where an anchor's selected words are drawn, if they lie within one of its blocks. */
function wordsRange(item: AnchorItem): Range | null {
  const source = sources.get(item.key);
  if (!item.exact || source === undefined) return null;
  const words = Array.from(source).slice(item.start, item.end).join("");
  for (const block of anchoredBlocks(item)) {
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
    const nodes: { node: Text; from: number }[] = [];
    let text = "";
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      nodes.push({ node: node as Text, from: text.length });
      text += node.nodeValue ?? "";
    }
    const at = text.indexOf(words);
    if (at < 0) continue;
    const place = (offset: number, end: boolean) => {
      const hit = nodes.findLast(({ from }) => (end ? from < offset : from <= offset)) ?? nodes[0];
      return [hit.node, offset - hit.from] as const;
    };
    const range = document.createRange();
    range.setStart(...place(at, false));
    range.setEnd(...place(at + words.length, true));
    return range;
  }
  return null;
}

/**
 * Shows in the chat what the unsent prompts are anchored to: selected words are highlighted,
 * and an anchor to whole blocks puts a quiet bar beside them.
 */
function paintAnchors(): void {
  for (const block of Array.from(chat.querySelectorAll(".anchored, .anchored-sent"))) {
    block.classList.remove("anchored", "anchored-sent");
  }
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

/** A box for one prompt: the whole of one message, with its own Send and Clear. */
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
  new ResizeObserver(() => drawMarks(area)).observe(words);
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
    remember();
  });
  // An empty box closes up when you leave it, unless it is the only one or holds a mark.
  area.addEventListener("blur", (event) => {
    if (markPanel.contains(event.relatedTarget as Node | null)) return;
    if (!area.value && !marks.items.size && textAreas().length > 1) box.remove();
  });
  let armed: number | undefined;
  const disarm = () => {
    if (armed !== undefined) clearTimeout(armed);
    armed = undefined;
    clear.textContent = "Clear";
  };
  // Clear asks once more, then removes this prompt and leaves the others.
  const clear = boxButton("box-clear", "Clear", () => {
    if (armed === undefined) {
      if (!area.value) return;
      clear.textContent = "Clear?";
      armed = window.setTimeout(disarm, 3000);
      return;
    }
    disarm();
    box.remove();
    if (!textAreas().length) addBox().focus();
    remember();
  });
  const send = boxButton("box-send", "Send", () => attemptSend(area));
  send.title = "Send (⌘↩)";
  const actions = document.createElement("div");
  actions.className = "box-actions";
  actions.append(clear, send);
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
  const line = area.value.slice(0, area.selectionStart).split("\n").length - 1;
  const id = marks.ids[line];
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

/** The box whose prompt the server has been asked to take, until it answers. */
let sending: HTMLElement | null = null;

function attemptSend(area: HTMLTextAreaElement): void {
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
  composerNote.textContent = "Sending…";
  sending = area.closest<HTMLElement>(".box");
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
  fitTerminal();
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
// Keeping the focus where it is stops the click from closing an empty box under it.
newPromptButton.addEventListener("mousedown", (event) => event.preventDefault());
newPromptButton.addEventListener("click", newPrompt);
// Cmd-Return sends the prompt being typed in, or the only one there is.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || !event.metaKey) return;
  const areas = textAreas();
  const focused = areas.find((area) => area === document.activeElement);
  const target = focused ?? (areas.length === 1 ? areas[0] : undefined);
  if (!target) return;
  event.preventDefault();
  attemptSend(target);
});

document.getElementById("start-button")!.addEventListener("click", () => post("start", {}));

(window as any).kyo = kyo;
