// The small amount of arithmetic and link-forming the Activity list needs (KYO-251),
// moved here from the retired `tabs/activity/links.ts` so `sidebar.ts` stays a paint
// function and these stay pure helpers a check can call directly.
//
// WHY THE LIST BUILDS THE URL ITSELF. `bridge.followLink` is the only way the
// renderer may act on an id -- it never parses one -- but nothing stops it FORMING
// the url a click should carry, because forming `kyosei://<project>/<id>` is not
// parsing an id's shape, it is naming the project the id already belongs to. The
// grammar itself stays in `main/links.ts`; this file reads no id apart, it prefixes.

/** The project host a `kyosei://` link needs: the id's own prefix, verbatim. */
export function projectOf(id: string): string {
  const dash = id.indexOf("-");
  return dash === -1 ? id : id.slice(0, dash);
}

/** The link a click on this id should follow -- `main/links.ts` resolves the rest. */
export function kyoseiUrlFor(id: string): string {
  return `kyosei://${projectOf(id)}/${id}`;
}

/** `1d 2h 3m 4s`, trimmed to the units that matter once the number is large. */
export function formatDuration(rawMs: number): string {
  const ms = Math.max(0, rawMs);
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86_400);
  const hours = Math.floor((totalSeconds % 86_400) / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;
  const parts: string[] = [];
  if (days > 0) parts.push(`${days}d`);
  if (days > 0 || hours > 0) parts.push(`${hours}h`);
  if (days > 0 || hours > 0 || minutes > 0) parts.push(`${minutes}m`);
  if (days === 0 && hours === 0) parts.push(`${seconds}s`);
  return parts.join(" ");
}

// ----------------------------------------------------------------- KYO-286
//
// A finished dispatch's run time is its end time minus its start time, not the time
// since it started. It was the latter, and the reason was in the data:
// `closedAt` is set by `kyo dispatch close` and nothing else, so a dispatch that
// reported and was never closed had no end time at all and every function below
// fell through to `now()` for it. `kyo worktrees --json` now says when a dispatch
// finished and which of three sources that is (`DispatchEnd`), and these read it.

/** The parts of a row every time helper here needs. `finished` is `kyo`'s answer
 * (KYO-286) and is absent on a row the CLI was never asked about. */
export interface TimedRow {
  createdAt: string;
  closedAt: string | null;
  finished?: { at: string | null; source: string | null; why: string } | null;
}

/**
 * When a dispatch's clock stopped, in milliseconds -- or null if it has not.
 *
 * `kyo`'s end time first, because only `kyo` can see the stop-audit ledger that
 * knows when a worker actually stopped; `closedAt` after it, which is all this app
 * had before and is still right for a row no `kyo worktrees` listing covers. Null
 * means the clock is genuinely still running; `NaN` means a timestamp that would
 * not parse, and the callers below tell those two apart rather than printing either.
 */
export function endedAtMs(row: TimedRow): number | null {
  const finished = row.finished?.at ?? null;
  if (finished !== null) return Date.parse(finished);
  if (row.closedAt !== null) return Date.parse(row.closedAt);
  return null;
}

/**
 * How long a dispatch has been running, or ran for.
 *
 * Its end (`endedAtMs`) when it has one; `now()` (injectable, for a check) while it
 * is still running. An unparseable timestamp answers the empty string rather than
 * `NaN` anything.
 */
export function elapsedFor(row: TimedRow, now: () => number = Date.now): string {
  const start = Date.parse(row.createdAt);
  if (Number.isNaN(start)) return "";
  const ended = endedAtMs(row);
  const endRaw = ended === null ? now() : ended;
  if (Number.isNaN(endRaw)) return "";
  return formatDuration(endRaw - start);
}

// ----------------------------------------------------------------- KYO-284
//
// A finished row more than a day old shows its date, and every row shows its token
// use beside the model ("opus · 160k tokens"). Both are pure functions of what the
// row already carries, so both live here and are checked directly rather than
// through a launched app.

/** One day, in milliseconds -- the age past which a finished row shows its date. */
export const ONE_DAY_MS = 86_400_000;

/** A local `YYYY-MM-DD`, which is what "just say the date" says here.
 *
 * Local rather than UTC, because a dispatch that closed at 9pm belongs to the day
 * the reader remembers it on; and ISO rather than a month name because every other
 * timestamp this app prints is ISO, and one list is not the place to invent a
 * second date format. */
function isoDate(at: number): string {
  const when = new Date(at);
  const month = `${when.getMonth() + 1}`.padStart(2, "0");
  const day = `${when.getDate()}`.padStart(2, "0");
  return `${when.getFullYear()}-${month}-${day}`;
}

/**
 * The time a FINISHED dispatch's first line shows.
 *
 * Its date once it closed more than a day ago, and what it has always shown --
 * how long it ran -- while it is younger than that. A row from last Tuesday saying
 * "4h 12m" is answering a question nobody asked of an old row; a row from an hour
 * ago saying "2026-01-01" is answering it worse. The run time is not lost: it
 * moves to the second line, beside the diff counts (KYO-284.c2).
 *
 * A row with no end at all (`endedAtMs` null) falls through to `elapsedFor`, which
 * runs the clock; a row whose end will not parse falls through to its empty-string
 * answer. KYO-286: the end is `kyo`'s `finished.at` before it is `closedAt`, so a
 * dispatch that reported and was never closed stops here rather than at `now()`.
 */
export function finishedWhen(row: TimedRow, now: () => number = Date.now): string {
  const ended = endedAtMs(row);
  const closed = ended === null ? Number.NaN : ended;
  if (Number.isNaN(closed)) return elapsedFor(row, now);
  const since = now() - closed;
  if (since > ONE_DAY_MS) return isoDate(closed);
  // Younger than a day: WHEN it ran, not how long -- the run time is line two's
  // ("ran 3h 5m"), and the same figure on both lines read as two clocks (KYO-284.r1).
  return `${formatDuration(since)} ago`;
}

/**
 * The tip under every time this list draws: when it started, when it ended, and --
 * KYO-286.c1 -- which of `kyo`'s three sources that end time came from.
 *
 * The source is never named here. `why` is the CLI's own sentence about the answer
 * it gave, carried through the way `tokensTip` carries its one, so this file holds
 * no second account of a precedence it does not implement. The only words below
 * that are this app's are the ones for a row `kyo worktrees --json` never covered:
 * that row still falls back to `closedAt`, and saying so is not a claim about a
 * source, it is the absence of one.
 */
export function whenTip(row: TimedRow): string {
  const started = `started ${row.createdAt}`;
  const finished = row.finished ?? null;
  if (finished !== null && finished.at !== null) {
    return `${started}, finished ${finished.at} — ${finished.why}`;
  }
  if (finished !== null && row.closedAt === null) return `${started} — ${finished.why}`;
  if (row.closedAt === null) return `${started}; still running`;
  return (
    `${started}, closed ${row.closedAt} — kyo said nothing about when this dispatch ` +
    "finished, so this row falls back to the close"
  );
}

/** A token count at a glance: `940`, `160k`, `1.4M`. Rounded, never precise --
 * the exact four counts are on the row's own tip, and a 9-digit number in a
 * sidebar column is a number nobody reads. */
export function roundedTokens(total: number): string {
  const count = Math.max(0, Math.round(total));
  if (count < 1_000) return `${count}`;
  if (count < 1_000_000) return `${Math.round(count / 1_000)}k`;
  return `${(count / 1_000_000).toFixed(1)}M`;
}

/**
 * What the row says about token use: `190k tokens`, or `tokens unknown`.
 *
 * `known` is the only thing branched on, and it is `kyo`'s word rather than a test
 * of whether the total is zero: a dispatch nothing could be matched to and a
 * dispatch that genuinely cost nothing are different facts, and the CLI is the only
 * thing that can tell them apart (KYO-284.c1/c4).
 *
 * KYO-292.c2: the figure is `latestTurnTokens` -- the size of the worker's LAST
 * turn, which is what claude.ai's agent card shows, so the two can be read against
 * each other: the card shows a running worker's figure, and only this row keeps it
 * after the worker has finished.
 * It replaces OUTPUT tokens under the bare word "tokens", which was a fiftieth of
 * what one real worker took in and is the reading that opened this record.
 *
 * It is NOT a running total, and that was measured rather than assumed: a finished
 * worker's card read 189,568 where its last message read 187,726 and every
 * cumulative sum was already past it (190,141 without cache reads). A figure
 * smaller than a running total cannot be that running total.
 *
 * The number is `kyo`'s (`latest_turn_tokens` on the CLI's own answer); nothing
 * here adds counts up, so there is exactly one place that decides what "tokens"
 * means on a row and it is the place that reads the transcript.
 */
export function tokensLabel(spend: { known: boolean; latestTurnTokens: number } | null): string {
  if (spend === null || !spend.known) return "tokens unknown";
  return `${roundedTokens(spend.latestTurnTokens)} tokens`;
}

/** The whole of what `kyo` said about a row's tokens, for its tip. Every number
 * is on the tip so the rounded one in the row never has to be trusted alone. */
export function tokensTip(
  spend: {
    known: boolean;
    why: string;
    model: string | null;
    inputTokens: number;
    cacheCreationInputTokens: number;
    cacheReadInputTokens: number;
    outputTokens: number;
    latestTurnTokens: number;
    totalTokens: number;
    messages: number;
  } | null,
): string {
  if (spend === null) return "kyo said nothing about this dispatch's token use";
  if (!spend.known) return spend.why;
  const model = spend.model ?? "a model the transcript did not name";
  const parts = [
    `${spend.latestTurnTokens} tokens on ${model} in this worker's latest turn — all ` +
      "four counts of its last message, which is what claude.ai's agent card shows " +
      "(the headline). It can read about a percent under the card, because the " +
      "transcript records that message's output tokens mid-stream",
    `then, across all ${spend.messages} of its messages: ${spend.inputTokens} input`,
    `${spend.cacheCreationInputTokens} cache creation`,
    `${spend.cacheReadInputTokens} cache read`,
    `${spend.outputTokens} output`,
    `${spend.totalTokens} in all, which is what it used altogether`,
  ];
  return `${parts.join(" · ")} — ${spend.why}`;
}
