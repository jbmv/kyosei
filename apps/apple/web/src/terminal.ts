// The terminal drawer's arithmetic (KYO-721): the size a view states to the server, the font
// at which a pane sized by someone else still fits, and the bytes of a stream's frame. It
// touches no element and loads no emulator, so that it can be tested without a page.

/** The font size, in pixels, at which the drawer draws a pane it sizes itself. */
export const TERMINAL_FONT = 12;

/** The smallest font a pane wider than the drawer is drawn at before it scrolls instead. */
export const TERMINAL_MIN_FONT = 7;

/** The sizes `kyo serve` accepts for a view (`kyosei.serve_terminal`). */
const COLS = { least: 10, most: 500 };
const ROWS = { least: 3, most: 300 };

export interface TerminalSize { cols: number; rows: number }

/**
 * The columns and rows a view of `width` by `height` pixels holds, with cells of the given
 * size, or null when nothing has been measured yet or the view is too small to state.
 */
export function viewSize(
  width: number, height: number, cellWidth: number, cellHeight: number,
): TerminalSize | null {
  if (!(width > 0 && height > 0 && cellWidth > 0 && cellHeight > 0)) return null;
  const cols = Math.floor(width / cellWidth);
  const rows = Math.floor(height / cellHeight);
  if (cols < COLS.least || rows < ROWS.least) return null;
  return { cols: Math.min(cols, COLS.most), rows: Math.min(rows, ROWS.most) };
}

/**
 * The font size for a pane of `paneCols` columns in a view that holds `viewCols` at `base`.
 * A pane the view sized is drawn at `base`. A pane another terminal sized may be wider, and
 * is drawn smaller so that its lines are not cut, down to `least`.
 */
export function fontToFit(
  base: number, viewCols: number, paneCols: number, least: number = TERMINAL_MIN_FONT,
): number {
  if (!(viewCols > 0 && paneCols > viewCols)) return base;
  return Math.max(least, Math.floor((base * viewCols * 10) / paneCols) / 10);
}

/** The bytes of a `screen` or `output` frame, which carries them in base64. */
export function bytesOf(data: string): Uint8Array {
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

/**
 * What a key sends that an emulator would otherwise send as a plain Return: Shift-Return and
 * Option-Return are a line break in Claude Code's input box, which it reads as Escape then
 * Return. Null for every other key, which the emulator encodes itself.
 */
export function lineBreakKeys(
  event: { key: string; shiftKey: boolean; altKey: boolean; metaKey: boolean; ctrlKey: boolean },
): string | null {
  if (event.key !== "Enter" || event.metaKey || event.ctrlKey) return null;
  return event.shiftKey || event.altKey ? "\x1b\r" : null;
}
