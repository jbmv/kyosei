// A draft that changed on another device meets the prompts in this window (KYO-798). The
// server announces a save, the app reads the draft, and the page merges it here, box by box.
// Every box carries an id that each device and the server share, so the merge can tell a box
// edited elsewhere from one added, and one sent or deleted elsewhere from one written here.
// Nothing here touches the page, so that node can test it.

/** One prompt as it is saved: its id, its words, and whatever the composer keeps with them. */
export interface DraftBox { id: string; text: string; marks?: unknown; scratch?: Scratch | null }

/**
 * What makes a box a scratchpad and not the prompt (KYO-903): the heading the person gave it,
 * which names it and is never sent, and whether it is collapsed to that heading.
 */
export interface Scratch { heading: string; collapsed?: boolean }

/** A new id for a box made in this window. */
export function boxId(): string {
  const random = globalThis.crypto;
  // `randomUUID` is offered to secure contexts only, and the app's page may not be one.
  if (typeof random?.randomUUID === "function") return random.randomUUID();
  const bytes = random.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** A value as text, with the keys of each object in one order, whoever wrote the object. */
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return `{${Object.keys(object).filter((key) => object[key] !== undefined).sort()
      .map((key) => `${JSON.stringify(key)}:${canonical(object[key])}`).join(",")}}`;
  }
  return JSON.stringify(value ?? null);
}

/**
 * What a box holds, to compare two copies of it. A draft passes through the app, which writes
 * an object's keys in an order of its own, and a box with no marks is saved with an empty
 * list or with none.
 */
export function boxSignature(box: { text: string; marks?: unknown; scratch?: Scratch | null }): string {
  const marks = Array.isArray(box.marks) && box.marks.length ? box.marks : [];
  // A scratchpad differs from a prompt of the same words, and by its heading and whether it
  // is collapsed, so each of those is saved and merged as an edit of the text is (KYO-903).
  if (box.scratch) return canonical([box.text, marks, [box.scratch.heading ?? "", Boolean(box.scratch.collapsed)]]);
  return canonical([box.text, marks]);
}

/** Each box's signature by its id. */
export function signatures(boxes: DraftBox[]): Map<string, string> {
  return new Map(boxes.map((box) => [box.id, boxSignature(box)]));
}

/**
 * The draft this window and the server last agreed on: what the server last gave, or the
 * last save it acknowledged. `local` is each box as this window would save it untouched and
 * `server` as the server sent it. The two can differ in how a mark is written, and each is
 * compared with its own side only.
 */
export interface Agreed { local: Map<string, string>; server: Map<string, string> }

export function agreedOn(local: DraftBox[], server: DraftBox[] = local): Agreed {
  return { local: signatures(local), server: signatures(server) };
}

export interface Merged<T extends DraftBox> {
  /** The prompts to show, in order. */
  boxes: T[];
  /** This window held edits the server had not acknowledged. */
  unsaved: boolean;
  /** How many prompts were edited both here and elsewhere, and are shown in both versions. */
  both: number;
  /** How many prompts edited here were sent or deleted elsewhere, and are kept. */
  kept: number;
  /** The result differs from the server's draft, and is to be saved. */
  save: boolean;
}

/**
 * The prompts to show when the draft changed elsewhere. `local` is what the window shows and
 * `remote` the server's draft.
 *
 * A box this window has not edited since the agreed draft follows the server: it takes the
 * server's words, and leaves when the server no longer holds it, as it does once it was sent
 * or deleted on another device. A box edited here keeps this window's words. When the same
 * box was edited elsewhere too, the other version is added after it under a new id, and when
 * it was removed elsewhere the edited box stays, so neither device's words are lost. A box
 * removed here stays removed unless it was edited elsewhere meanwhile. A box added elsewhere
 * is placed after the box it follows in the server's draft.
 */
export function mergeDraft<T extends DraftBox>(
  agreed: Agreed, shown: T[], remote: T[], mint: () => string = boxId,
): Merged<T> {
  // An empty box the server never held carries nothing typed: it is the composer's place to
  // type, not an edit. When the server's draft holds a prompt, that prompt takes its place,
  // rather than standing above an empty box beside it.
  const placeholder = (box: T) => boxSignature(box) === boxSignature({ text: "" }) && !agreed.server.has(box.id);
  const local = remote.length ? shown.filter((box) => !placeholder(box)) : shown;
  const theirs = new Map(remote.map((box) => [box.id, box]));
  const here = new Set(shown.map((box) => box.id));
  const edited = (box: T) => agreed.local.get(box.id) !== boxSignature(box);
  const unsaved = local.some(edited) || [...agreed.local.keys()].some((id) => !here.has(id));
  if (!unsaved) {
    return { boxes: remote, unsaved, both: 0, kept: 0, save: false };
  }
  const boxes: T[] = [];
  let both = 0;
  let kept = 0;
  for (const mine of local) {
    const other = theirs.get(mine.id);
    const was = agreed.server.get(mine.id);
    if (!edited(mine)) {
      // Not edited here: the server's copy, or gone with it. One the server never held and
      // does not hold now was added here and is kept.
      if (other) boxes.push(other);
      else if (was === undefined) boxes.push(mine);
      continue;
    }
    boxes.push(mine);
    if (!other) {
      if (was !== undefined) kept += 1;
    } else if (boxSignature(other) !== boxSignature(mine) && boxSignature(other) !== was) {
      boxes.push({ ...other, id: mint() });
      both += 1;
    }
  }
  // What the server holds and this window does not: added elsewhere, or removed here.
  remote.forEach((other, index) => {
    if (here.has(other.id)) return;
    const was = agreed.server.get(other.id);
    if (was !== undefined && was === boxSignature(other)) return;
    let at = 0;
    for (let before = index - 1; before >= 0; before -= 1) {
      const found = boxes.findIndex((box) => box.id === remote[before].id);
      if (found >= 0) {
        at = found + 1;
        // Past a version added beside the box, and past boxes placed there a moment ago.
        while (at < boxes.length && !here.has(boxes[at].id) && !theirs.has(boxes[at].id)) at += 1;
        break;
      }
    }
    boxes.splice(at, 0, other);
  });
  const save = boxes.length !== remote.length
    || boxes.some((box, index) => box.id !== remote[index].id || boxSignature(box) !== boxSignature(remote[index]));
  return { boxes, unsaved, both, kept, save };
}

/** What the person is told after a merge, or nothing when no edit of theirs was involved. */
export function mergeNote(merged: { unsaved: boolean; both: number; kept: number }): string {
  if (!merged.unsaved) return "";
  if (merged.both) {
    return "A prompt was edited here and on another device. Both versions are kept, the other device's after this one's.";
  }
  if (merged.kept) {
    return "A prompt edited here was sent or deleted on another device. It is kept here.";
  }
  return "This draft also changed on another device. What was typed here is kept.";
}
