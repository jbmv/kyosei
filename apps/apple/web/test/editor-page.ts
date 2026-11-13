// A page for `editor.test.ts`: one box built by `src/editor.ts`, in a holder of a given width
// set in the composer's font, with what each callback was handed kept in `window.calls`. Not a
// test file itself; `openPage` builds it as the page's script (KYO-909).
import type { EditorView } from "@codemirror/view";
import {
  createEditor, ghostLine, landingLine, marksOf, placeItem, removeItem, savedMarks, showLanding,
} from "../src/editor.ts";

interface Item { name: string }

const calls: Record<string, unknown>[] = [];
let view: EditorView | null = null;

function made(): EditorView {
  if (!view) throw new Error("no box: call ed.make first");
  return view;
}

/** The marker of a line that holds something: the names of its items. */
function drawMark(_view: EditorView, id: number, items: Item[]): HTMLElement {
  const mark = document.createElement("span");
  mark.className = "mark held";
  mark.dataset.id = String(id);
  mark.textContent = items.map((item) => item.name).join(",");
  return mark;
}

function drawGhost(_view: EditorView, id: number): HTMLElement {
  const ghost = document.createElement("span");
  ghost.className = "mark ghost";
  ghost.dataset.id = String(id);
  ghost.textContent = "+";
  return ghost;
}

const ed = {
  calls,
  /**
   * Builds a box of `text` and saved `marks`, `width` pixels wide, focuses it, and resolves
   * once CodeMirror has measured its lines.
   */
  make(text: string, marks: { line: number; items: Item[] }[] = [], width = 360): Promise<void> {
    view?.destroy();
    calls.length = 0;
    const holder = document.createElement("div");
    holder.id = "holder";
    holder.style.cssText = `width: ${width}px; font: 15px/1.45 -apple-system, system-ui, sans-serif; margin: 20px;`;
    document.body.replaceChildren(holder);
    view = createEditor<Item>({
      parent: holder, text, marks, placeholder: "Reply…", drawMark, drawGhost,
      onGutterMenu: (_view, line, id, event) => {
        calls.push({ kind: "gutterMenu", line, id, button: event.button });
        return true;
      },
      onFiles: (_view, files, how, pos) => {
        calls.push({ kind: "files", how, pos, names: files.map((file) => file.name) });
      },
      onPaste: (_view, pasted, from) => { calls.push({ kind: "paste", text: pasted, from }); },
    });
    view.focus();
    return ed.settle();
  },
  /** Resolves after two frames, by which CodeMirror has read the lines' heights and redrawn. */
  settle: () => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))),
  view: () => made(),
  text: () => made().state.doc.toString(),
  selection: () => [made().state.selection.main.from, made().state.selection.main.to],
  select(from: number, to = from): void {
    made().dispatch({ selection: { anchor: from, head: to } });
  },
  /** Whether the focus is in the box's editable content. */
  focused: () => made().contentDOM.contains(document.activeElement),
  /** The saved marks, each item by its name. */
  marks: () => savedMarks<Item>(made().state).map(({ line, items }) => ({ line, names: items.map((item) => item.name) })),
  lost: () => Array.from(marksOf<Item>(made().state).lost.keys()),
  place(line: number, name: string): void {
    placeItem<Item>(made(), line, { name });
  },
  /** Removes the item called `name` from line `line`, by the identity the box holds. */
  remove(line: number, name: string): void {
    const marks = marksOf<Item>(made().state);
    const item = (marks.items.get(marks.ids[line]) ?? []).find((held) => held.name === name);
    if (item) removeItem(made(), line, item);
  },
  landing: (on: boolean) => showLanding(made(), on),
  landingLine: () => landingLine(made().state),
  ghostLine: () => ghostLine(made().state),
};

Object.assign(window, { ed });
