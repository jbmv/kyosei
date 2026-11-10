// The places a view has shown, in order, and where in them it now stands (KYO-738). The Files
// and Records views share this one rule: a list of places and a position, where showing a
// place after stepping back drops the places that were ahead, as a browser does. A place of
// `null` is the view's home (the file browser, the records landing page); home is a place like
// any other, so Back from the first thing opened from home returns to home.

export interface History<T> {
  /** Every place shown, oldest first. The first is always home. */
  places: (T | null)[];
  /** Index into `places` of the place now shown. */
  at: number;
}

/** The most places kept; the oldest are forgotten past it. */
export const HISTORY_LIMIT = 50;

/** A history at home with nothing behind or ahead. */
export function fresh<T>(): History<T> {
  return { places: [null], at: 0 };
}

/** The place now shown, or null when it is home. */
export function current<T>(history: History<T>): T | null {
  return history.places[history.at] ?? null;
}

export function canBack<T>(history: History<T>): boolean {
  return history.at > 0;
}

export function canForward<T>(history: History<T>): boolean {
  return history.at < history.places.length - 1;
}

/**
 * Shows `place` (null for home): what was ahead is dropped and the place is added after the
 * current one. Showing the place already shown changes nothing.
 */
export function visitPlace<T>(
  history: History<T>, place: T | null, same: (a: T, b: T) => boolean = Object.is,
): History<T> {
  const now = current(history);
  if (place === null ? now === null : now !== null && same(now, place)) return history;
  const kept = [...history.places.slice(0, history.at + 1), place];
  const over = Math.max(0, kept.length - HISTORY_LIMIT);
  return { places: kept.slice(over), at: kept.length - 1 - over };
}

/** One place back, or the same history when there is none. */
export function stepBack<T>(history: History<T>): History<T> {
  return canBack(history) ? { places: history.places, at: history.at - 1 } : history;
}

/** One place forward, or the same history when there is none. */
export function stepForward<T>(history: History<T>): History<T> {
  return canForward(history) ? { places: history.places, at: history.at + 1 } : history;
}

/**
 * The history without the places `gone` names, for places that no longer exist. Home stays;
 * the position stays on the same place, or on the nearest earlier one when that was removed.
 */
export function without<T>(history: History<T>, gone: (place: T) => boolean): History<T> {
  const places: (T | null)[] = [];
  let at = 0;
  history.places.forEach((place, index) => {
    if (place !== null && gone(place)) return;
    places.push(place);
    if (index <= history.at) at = places.length - 1;
  });
  return { places, at };
}
