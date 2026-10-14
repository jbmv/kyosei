// KYO-259.c3: where a torn-off tab can land. These launch nothing -- `tabdrag.ts` is
// Electron-free so the strip hit test can be driven point by point.

import { expect, test } from "@playwright/test";
import { columnsLeft, RAIL_PX, type StripCandidate, stripAt } from "../src/main/tabdrag";
import type { LayoutSnapshot } from "../src/shared/protocol";

const BAND = 46;

/** A layout with only the part the hit test reads. */
function layout(item: string | null, width: number): LayoutSnapshot {
  return { sidebar: { item, width } } as unknown as LayoutSnapshot;
}

function windowAt(
  value: string,
  x: number,
  options: Partial<Omit<StripCandidate<string>, "value" | "bounds">> = {},
): StripCandidate<string> {
  return {
    value,
    bounds: { x, y: 100, width: 1200, height: 800 },
    visible: options.visible ?? true,
    focused: options.focused ?? false,
    layout: options.layout ?? layout("ideas", 280),
  };
}

test("the sidebar head is not a strip: a point over IDEAS and its collapse button lands nowhere", () => {
  const win = windowAt("shell", 0);
  // Over the rail's traffic-light spacer, the sidebar title, and the collapse button
  // at the head's right edge -- all in the top row, none of them a strip.
  for (const x of [20, RAIL_PX + 20, RAIL_PX + 270]) {
    expect(stripAt([win], x, 110, BAND), `x=${x}`).toBeUndefined();
  }
  // The first pixel of the columns is.
  expect(stripAt([win], RAIL_PX + 280, 110, BAND)).toBe("shell");
});

test("with the sidebar collapsed the strip starts right after the rail", () => {
  const win = windowAt("shell", 0, { layout: layout(null, 280) });
  expect(columnsLeft(layout(null, 280))).toBe(RAIL_PX);
  expect(stripAt([win], RAIL_PX - 1, 110, BAND)).toBeUndefined();
  expect(stripAt([win], RAIL_PX + 1, 110, BAND)).toBe("shell");
});

test("a window with no saved layout yet is judged by the rail alone", () => {
  expect(columnsLeft(null)).toBe(RAIL_PX);
  expect(stripAt([{ ...windowAt("fresh", 0), layout: null }], RAIL_PX + 5, 110, BAND)).toBe(
    "fresh",
  );
});

test("below the band, hidden windows, and overlaps", () => {
  const a = windowAt("a", 0);
  expect(stripAt([a], 600, 100 + BAND, BAND)).toBeUndefined();
  expect(stripAt([a], 600, 100 - 8, BAND)).toBe("a");
  expect(stripAt([{ ...a, visible: false }], 600, 110, BAND)).toBeUndefined();
  const b = windowAt("b", 200);
  expect(stripAt([a, b], 700, 110, BAND)).toBe("b");
  expect(stripAt([{ ...a, focused: true }, b], 700, 110, BAND)).toBe("a");
});
