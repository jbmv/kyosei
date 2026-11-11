// The History view's logic (KYO-825), searching what was said: what a search asks, how its answer is read, and which
// element of the chat a result opens. The view is drawn in chat.ts.

/** The shortest text the History view asks the server about. */
export const HISTORY_MIN_CHARS = 2;
/** Characters an excerpt keeps on each side of its first match: three or four lines. */
export const HISTORY_CONTEXT = 220;

/** Who wrote a message, as the transcript search names it. */
export type Role = "person" | "assistant";

/** One piece of an excerpt, matched or not. */
export interface Piece { text: string; match: boolean }

/** One message the search found. */
export interface Found {
  session: string;
  /** The transcript record: a prompt's id, or a reply's record uuid. */
  message: string;
  /** A reply's message id, by which the chat keys it; null for a prompt. */
  messageId: string | null;
  role: Role;
  at: string;
  excerpt: Piece[];
}

/** What to ask for: the text, and a role only when one of the two is chosen. */
export function searchQuery(
  text: string, roles: ReadonlySet<Role>, cursor: string | null,
): { q: string; role?: Role; cursor?: string; context: number } | null {
  const q = text.trim();
  if (q.length < HISTORY_MIN_CHARS || roles.size === 0) return null;
  return {
    q,
    ...(roles.size === 1 ? { role: [...roles][0] } : {}),
    ...(cursor ? { cursor } : {}),
    context: HISTORY_CONTEXT,
  };
}

/** The results of a search answer, and the cursor for the next page, or null when not one. */
export function foundOf(document: unknown): { found: Found[]; cursor: string | null } | null {
  const results = (document as { results?: unknown })?.results;
  if (!Array.isArray(results)) return null;
  const found: Found[] = [];
  for (const result of results as Record<string, unknown>[]) {
    if (typeof result?.session_id !== "string" || typeof result.message !== "string") continue;
    const role = result.role === "assistant" ? "assistant" : "person";
    const pieces = Array.isArray(result.excerpt) ? result.excerpt as Record<string, unknown>[] : [];
    found.push({
      session: result.session_id,
      message: result.message,
      messageId: typeof result.message_id === "string" ? result.message_id : null,
      role,
      at: typeof result.at === "string" ? result.at : "",
      excerpt: pieces.filter((piece) => typeof piece?.text === "string")
        .map((piece) => ({ text: piece.text as string, match: piece.match === true })),
    });
  }
  const more = (document as { more?: unknown }).more === true;
  const cursor = (document as { cursor?: unknown }).cursor;
  return { found, cursor: more && typeof cursor === "string" ? cursor : null };
}

/** The key of the chat element a found message is drawn as, by the chat's own keys. */
export function chatKey(found: Pick<Found, "message" | "messageId" | "role">): string {
  return found.role === "person" || !found.messageId ? `t:${found.message}` : `r:${found.messageId}`;
}

/** An excerpt as plain words, for the list of opened results. */
export function plainWords(excerpt: Piece[], limit = 140): string {
  const words = excerpt.map((piece) => piece.text).join("").replace(/\s+/g, " ").trim();
  return words.length > limit ? `${words.slice(0, limit - 1)}…` : words;
}

/** An opened result, as `history/recent` lists it. */
export interface Opened { session: string; message: string; words: string; opened_at: string }

/** The entries of a `history/recent` answer, newest first, or null when it is not one. */
export function openedEntries(document: unknown): Opened[] | null {
  const entries = (document as { entries?: unknown })?.entries;
  if (!Array.isArray(entries)) return null;
  return (entries as Record<string, unknown>[]).filter(
    (entry): entry is Record<string, unknown> & Opened =>
      typeof entry?.session === "string" && typeof entry.message === "string"
      && typeof entry.opened_at === "string")
    .map((entry) => ({ ...entry, words: typeof entry.words === "string" ? entry.words : "" }));
}

/**
 * The results gathered by session (KYO-836), each session once, in the order its first
 * result came, which is newest first; each result keeps its index in the list.
 */
export function groupBySession(found: Found[]): { session: string; items: { found: Found; index: number }[] }[] {
  const groups = new Map<string, { found: Found; index: number }[]>();
  found.forEach((item, index) => {
    groups.set(item.session, [...(groups.get(item.session) ?? []), { found: item, index }]);
  });
  return [...groups].map(([session, items]) => ({ session, items }));
}
