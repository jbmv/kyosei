// The read-only chat. It holds the objects `kyo serve` sent and draws them; it decides nothing
// about a session. Each object is one element, addressed by the object's own id, and a later
// version replaces it in place.
import { render } from "./pipeline";

interface Turn { id: string; at: string; text: string; images?: { index: number }[] }
interface Reply { message_id: string; at: string; text: string; thinking?: string | null }
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
}
interface SessionRow {
  session_id: string; title: string | null; live: boolean; first_seen: string;
  remote_control_url: string | null;
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

function drawTurn(turn: Turn): HTMLElement {
  const element = document.createElement("div");
  element.className = "turn";
  for (const { index } of turn.images ?? []) element.append(drawImage(turn, index));
  if (turn.text) element.append(document.createTextNode(turn.text));
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

const SIDE_CLOSED_KEY = "side-closed";
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
  setText("side-project", menu?.project ?? "");
  setText("side-branch", facts?.git_branch ?? "");

  const percent = facts?.context?.percent ?? null;
  setText("side-context", percent === null ? "unknown" : `${percent}%`);
  (document.getElementById("context-fill") as HTMLElement).style.width = `${percent ?? 0}%`;

  const changes = menu?.changes;
  // A clean tree has nothing to report, so the section is left out, as claude.ai does.
  document.getElementById("side-changes")!.hidden =
    !changes || !(changes.added || changes.removed || changes.untracked.files);
  setText("side-added", `+${changes?.added ?? 0}`);
  setText("side-removed", `−${changes?.removed ?? 0}`);
  setText("side-untracked", changes?.untracked.files ? `${plural(changes.untracked.files, "new file")}` : "");

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
}

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

try {
  document.body.classList.toggle("side-closed", localStorage.getItem(SIDE_CLOSED_KEY) === "1");
} catch {}

document.getElementById("side-toggle")!.addEventListener("click", () => {
  const closed = document.body.classList.toggle("side-closed");
  try {
    localStorage.setItem(SIDE_CLOSED_KEY, closed ? "1" : "0");
  } catch {}
});

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

  image(frame: { key: string; data: string }): void {
    imageData.set(frame.key, frame.data);
    document.querySelectorAll<HTMLImageElement>(`img[data-image="${frame.key}"]`).forEach((image) => {
      image.src = frame.data;
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
    menu = data;
    drawMenu();
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
  }): void {
    agentCalls.set(data.agent_id, data.tool_calls);
    if (data.dispatch) agentDispatches.set(data.agent_id, data.dispatch);
    drawAgents();
  },

  apply(batch: Batch): void {
    keepingPlace(() => {
      if (batch.reset) kyo.clear();
      for (const turn of batch.turns ?? []) {
        const version = `${turn.text}|${turn.images?.length ?? 0}`;
        place(`t:${turn.id}`, turn.at, version, () => drawTurn(turn));
      }
      for (const reply of batch.replies ?? []) {
        if (reply.text) landedKeys.push(lettersAndDigits(reply.text));
        // A record with only tool use has nothing to show.
        if (reply.text || reply.thinking) {
          const version = `${reply.text}|${reply.thinking ?? ""}`;
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
    kyo.facts(frame as { facts?: Facts | null });
    kyo.terminal(frame as { terminal?: { text: string; working_line: string | null } | null });
    kyo.screen(frame);
  },

  screen(frame: ScreenFrame): void {
    const state = frame.screen?.state ?? null;
    const changed = state !== screenState;
    screenState = state;
    background = frame.screen?.background ?? null;
    if (changed) keepingPlace(arrange);
    drawWorking();
    drawSide();
  },
};

(window as any).kyo = kyo;
