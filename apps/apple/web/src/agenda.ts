// The session's task list (KYO-771), as the composer draws it above its prompts: what the
// bubble shows for each frame the app hands the page, and the list a change by the person
// saves. It holds no state and touches no element, so that it can be tested without a page.
//
// The list is kept by `kyo serve`. The person and the orchestrator both write it, and the
// server refuses a device's change to an item of the orchestrator's, so nothing here offers
// one: an orchestrator's item is drawn with its state and takes no press.

export interface AgendaItem {
  id: string;
  text: string;
  author: "person" | "orchestrator";
  checked: boolean;
}

/** What the app says of the followed session's list. */
export type AgendaFrame =
  /** No live session is followed, so there is no list to show. */
  | { state: "absent" }
  /** The list could not be read, for a reason the person can act on. */
  | { state: "unavailable"; why: string }
  /** The list as the server holds it. `saved` says this is the answer to a save. */
  | { state: "shown"; session_id: string; items: AgendaItem[]; etag: string | null; saved?: boolean };

/** One change the person makes from the bubble. */
export type Change =
  | { kind: "add"; text: string }
  | { kind: "edit"; id: string; text: string }
  | { kind: "check"; id: string; checked: boolean }
  | { kind: "remove"; id: string };

/** An item as a save sends it: the server reads `id`, `text` and `checked` and nothing else. */
export interface SentItem {
  id?: string;
  text: string;
  checked?: boolean;
}

export interface Row {
  id: string;
  text: string;
  checked: boolean;
  /** Whether the person wrote it, and so may edit, check and remove it here. */
  mine: boolean;
  /** Whose it is, as the bubble says it. */
  whose: string;
}

export interface AgendaView {
  /** Whether the bubble is drawn at all. */
  shown: boolean;
  /** Why the list is not shown, in the bubble's one line; null when it is. */
  why: string | null;
  /**
   * The rows to draw, the person's first and then the orchestrator's, each group in the
   * list's own order. A checked item is left out unless completed items are being shown, and
   * then follows the unchecked ones of its group.
   */
  rows: Row[];
  /**
   * The index in `rows` the divider is drawn before: where the orchestrator's rows begin, when
   * there are any. The person's field for adding an item stands just above it, at the end of
   * the person's group, since it is theirs (KYO-781.c3); null when the orchestrator has no rows.
   */
  dividerAt: number | null;
  /** How many checked items the list holds, of whoever's; the control states it. */
  completed: number;
  /** Whether the checked items are drawn. */
  showCompleted: boolean;
  /** Whether the person can add an item. */
  canAdd: boolean;
}

/** The longest text the server takes for one item. */
export const MAX_TEXT = 500;

/** The bubble for the last frame the app sent; null before any has arrived. */
export function agendaView(frame: AgendaFrame | null, showCompleted = false): AgendaView {
  const none = { rows: [], dividerAt: null, completed: 0, showCompleted: false, canAdd: false };
  if (!frame || frame.state === "absent") return { shown: false, why: null, ...none };
  if (frame.state === "unavailable") return { shown: true, why: frame.why, ...none };
  const row = (item: AgendaItem): Row => ({
    id: item.id,
    text: item.text,
    checked: item.checked,
    mine: item.author === "person",
    whose: item.author === "person" ? "you" : "orchestrator",
  });
  const completed = frame.items.filter((item) => item.checked).length;
  // Within a group the list's own order holds: the unchecked items, then, when they are
  // shown, the checked ones.
  const group = (author: AgendaItem["author"]): Row[] => {
    const own = frame.items.filter((item) => item.author === author);
    return [...own.filter((item) => !item.checked), ...(showCompleted ? own.filter((item) => item.checked) : [])].map(row);
  };
  const mine = group("person");
  const theirs = group("orchestrator");
  return {
    shown: true,
    why: null,
    rows: [...mine, ...theirs],
    dividerAt: theirs.length > 0 ? mine.length : null,
    completed,
    showCompleted,
    canAdd: true,
  };
}

/** One line of typed text as an item holds it, or null when it holds nothing. */
export function itemText(typed: string): string | null {
  const text = typed.split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_TEXT);
  return text || null;
}

/**
 * The whole list to save after `change`, or null when the change is not the person's to make
 * or changes nothing. Every other item is sent as it was read, which is what the server asks
 * of an item of the orchestrator's.
 */
export function listAfter(items: AgendaItem[], change: Change): SentItem[] | null {
  const sent: SentItem[] = items.map((item) => ({ id: item.id, text: item.text, checked: item.checked }));
  if (change.kind === "add") {
    const text = itemText(change.text);
    return text === null ? null : [...sent, { text }];
  }
  const index = items.findIndex((item) => item.id === change.id);
  if (index < 0 || items[index].author !== "person") return null;
  if (change.kind === "remove") return sent.filter((_, at) => at !== index);
  if (change.kind === "check") {
    if (items[index].checked === change.checked) return null;
    sent[index].checked = change.checked;
    return sent;
  }
  const text = itemText(change.text);
  if (text === null || text === items[index].text) return null;
  sent[index].text = text;
  return sent;
}

/** What the server answered to a save it refused, as the app hands it to the page. */
export interface Refusal {
  status: number;
  /** The server's sentence, when it sent one. */
  error?: string | null;
}

/**
 * What the bubble says of a refused save. A stale save is the common one: the orchestrator or
 * another device saved first, the list on the screen is now the newer one, and what the
 * person typed is still in its field.
 */
export function refusalText(refusal: Refusal): string {
  if (refusal.status === 412) {
    return "The list changed before this was saved. It is shown as it is now, and your change was not made; press Return to make it again.";
  }
  if (refusal.status === 0) return "The server was not reached, so the list was not saved.";
  return refusal.error ?? `The list was not saved (HTTP ${refusal.status}).`;
}
