// The tab registry: what `build.mjs` discovered, checked and indexed (KYO-232.c5).
//
// `registry.generated.ts` is written by the build from the DIRECTORIES under this
// one -- see `build.mjs`'s `generateTabRegistry`. It is gitignored, because it is
// output: a stage 3 worker who adds `tabs/record/` and runs the build gets a diff
// holding their own directory and nothing else, which is what "edits no shared file"
// has to mean to be worth stating.
//
// This file is the half that is NOT generated, and it exists for one reason: the
// generated list carries the directory name beside the module, so the promise
// `kind.ts` makes -- "your `kind` is exactly the directory name" -- is CHECKED here
// rather than trusted. A module whose `kind` disagrees with its directory would
// otherwise register under a name no rail item, saved layout or `openTab` call could
// reach, and the tab would simply never appear, with nothing saying why.

import type { TabKind } from "./kind";
import { DISCOVERED } from "./registry.generated";

/** Every kind the build found, by name, in directory order. */
const BY_KIND = new Map<string, TabKind>();

/**
 * The refusal, held rather than thrown where it is found.
 *
 * Thrown from this module's top level it would escape the bundle's own evaluation --
 * before `renderer.ts`'s `start()` exists to catch it -- and the window would come up
 * blank with nothing on `document.body` saying why. Held here and thrown from the
 * first accessor, it reaches the one catch this renderer has and lands on
 * `body[data-error]`, which is where a human and a check both look (law 6).
 */
let refusal: Error | null = null;

for (const entry of DISCOVERED) {
  if (entry.module.kind !== entry.directory) {
    refusal ??= new Error(
      `tabs: src/renderer/tabs/${entry.directory}/index.ts exports kind ` +
        `"${entry.module.kind}", but the registry discovers a kind by its DIRECTORY ` +
        `name (see src/renderer/tabs/kind.ts). Rename the directory to ` +
        `"${entry.module.kind}", or set \`kind: "${entry.directory}"\` on the module.`,
    );
    continue;
  }
  if (BY_KIND.has(entry.directory)) {
    refusal ??= new Error(`tabs: two directories claim the kind "${entry.directory}"`);
    continue;
  }
  BY_KIND.set(entry.directory, entry.module);
}

function checked(): Map<string, TabKind> {
  if (refusal !== null) throw refusal;
  return BY_KIND;
}

/** Every discovered kind, in the order the build enumerated the directories. */
export function tabKinds(): TabKind[] {
  return [...checked().values()];
}

/** One kind by name, or undefined for a kind this build does not have. */
export function tabKind(kind: string): TabKind | undefined {
  return checked().get(kind);
}

/** The discovered kind names, sorted -- what the shell writes to `data-tab-kinds`. */
export function tabKindNames(): string[] {
  return [...checked().keys()].sort();
}
