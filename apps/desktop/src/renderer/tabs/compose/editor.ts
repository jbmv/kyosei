// The writable editor: CodeMirror 6 over the markdown source, a context-dependent
// ribbon, and the popup that makes `[[`, `#` and `[[<id>.` closed sets.
//
// Recovered from v2's `renderer/ideas/editor.ts` rather than rewritten from memory:
// that file's own header explains
// why CodeMirror and not a `<textarea>` or a rich-text editor (byte-exact round trip to
// a file `kyo` parses and git diffs), and why source mode rather than live preview
// (syntax stays visible and selectable). Both hold unchanged here.
//
// What KYO-234 adds to it:
//
//   THE THIRD CLOSED SET. `[[<record-id>.` offers that record's own child ids
//   (KYO-234.c5) -- `complete.ts`'s `matchChildIds`, resolved through
//   `options.getChildDocument` because the answer needs a bridge read the base
//   `Completions` payload does not carry.
//
//   CLICK-TO-FOLLOW. A typed `[[id]]` is clickable (Mod-click, so an ordinary click
//   still places the caret for editing): `options.onLinkClick` is told the bracketed
//   text verbatim. This editor does not parse it -- extracting the substring between
//   `[[` and `]]` is not reading the id's grammar, and the caller hands the raw string
//   to `context.followLink`, which is where that grammar lives (`kind.ts`'s own rule).
//
// KYO-150's keystroke-versus-programmatic distinction is carried through unchanged:
// `setText` (the app rendering) never arms a save, `applyEdit` (the human's own edit,
// including one made through a ribbon button) always does.

import {
  acceptCompletion,
  autocompletion,
  type Completion,
  type CompletionContext,
  type CompletionResult,
  closeCompletion,
  currentCompletions,
} from "@codemirror/autocomplete";
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentLess,
  indentMore,
} from "@codemirror/commands";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import {
  codeFolding,
  ensureSyntaxTree,
  foldGutter,
  foldKeymap,
  HighlightStyle,
  indentUnit,
  syntaxHighlighting,
  syntaxTree,
} from "@codemirror/language";
import {
  Annotation,
  type ChangeSpec,
  Compartment,
  countColumn,
  EditorState,
  type Extension,
  Prec,
  RangeSetBuilder,
  StateEffect,
  StateField,
  Transaction,
} from "@codemirror/state";
import {
  type Command,
  Decoration,
  type DecorationSet,
  EditorView,
  keymap,
  showTooltip,
  type Tooltip,
  ViewPlugin,
  type ViewUpdate,
} from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { lineChanges } from "../../../shared/merge3";
import type { Completions, RecordDocument } from "../../../shared/protocol";
import { type Child, el } from "../../dom";
import { type IconName, icon } from "../../icons";
import {
  applySuggestion,
  linkAt,
  matchChildIds,
  type Suggestion,
  suggestionsFor,
  type Trigger,
  triggerAt,
} from "./complete";

/** One button on the editor's ribbon. */
export interface RibbonButton {
  id: string;
  label: string;
  icon: IconName;
  tip: string;
  /** A button with nothing to act on says why, rather than acting on nothing. */
  disabledBecause?: string;
  run: () => void;
}

export interface EditorHandle {
  root: HTMLElement;
  /** The editor itself, for the callers that need more than this interface. */
  view: EditorView;
  /**
   * Render text into the box: replace the whole document with something the app
   * decided, not something the human typed (KYO-150).
   *
   * This is the app putting a document on screen -- the file a read just returned, or
   * the empty box that stands in while that read is out. It is NOT an edit, so it does
   * not reach `onChange` as one: the disk is not behind the screen after it, and
   * nothing about it asks for a save.
   *
   * When the app rewrites the document ON THE HUMAN'S BEHALF -- a ribbon button that
   * removes a promoted selection -- that IS their edit, and `applyEdit` is the one to
   * call.
   */
  setText(text: string): void;
  /**
   * Rewrite the whole document as the human's own edit (KYO-150).
   *
   * Identical to `setText` except in what it says about itself: this reaches
   * `onChange` exactly as a keystroke does, because a ribbon button that rewrites the
   * document is the human editing it through a button instead of a keyboard.
   */
  applyEdit(text: string): void;
  /**
   * Put a text the APP read on screen -- the file changed elsewhere, or a save came back
   * merged (KYO-575) -- as the smallest line-level change rather than a whole-document
   * replace, so the caret stays in the line it was in when that line is unchanged, and
   * the line at the top of the view stays at the top.
   *
   * Like `setText` it is the app rendering, not the human editing: it does not arm a
   * save (KYO-150), and it is kept out of the undo history -- an undo that removed the
   * other window's text would be the human deleting it without having seen it go.
   */
  reconcile(text: string): void;
  text(): string;
  /** How many times the buffer has changed, from any source. */
  revision(): number;
  /**
   * Replace the buffer with text that was read asynchronously -- but only if nothing
   * has written into it since `issuedAt`. Returns false when the replacement was
   * refused.
   */
  adopt(text: string, issuedAt: number): boolean;
  /** The reader's current selection, or the empty string. */
  selection(): string;
  /** Put the caret at an offset. */
  setCaret(offset: number): void;
  focus(): void;
  hasFocus(): boolean;
  setRibbon(buttons: RibbonButton[]): void;
  /** The record/tag completion sets. Null until they land. */
  setCompletions(sets: Completions | null): void;
  setSaveState(state: SaveState, detail?: string): void;
}

export interface EditorElement extends HTMLElement {
  kyoseiEditor?: EditorHandle;
}

/** What the header says about the human's text right now. */
export type SaveState = "clean" | "dirty" | "saving" | "saved" | "failed" | "readonly";

const SAVE_LABEL: Record<SaveState, string> = {
  clean: "no changes",
  dirty: "unsaved",
  saving: "saving…",
  saved: "saved",
  failed: "NOT saved",
  readonly: "read-only",
};

/**
 * Who moved the document (KYO-150). `"user"` is the human -- a keystroke, a paste, an
 * undo, a ribbon button that rewrites text on their behalf; the screen is now ahead of
 * the disk and a save is owed. `"programmatic"` is the app rendering into the box, and
 * nothing is owed.
 */
export type ChangeOrigin = "user" | "programmatic";

const programmaticWrite = Annotation.define<true>();

const originOf = (update: ViewUpdate): ChangeOrigin =>
  update.transactions.every(
    (transaction) => !transaction.docChanged || transaction.annotation(programmaticWrite) === true,
  )
    ? "programmatic"
    : "user";

export interface EditorOptions {
  /** Told on every document change, with who caused it (KYO-150). */
  onChange(text: string, origin: ChangeOrigin): void;
  /** Told when the reader asks for a save now (⌘S / Ctrl-S). */
  onSaveNow(): void;
  onSelectionChange?(): void;
  onBlur?(): void;
  /**
   * A `[[id]]` was Mod-clicked. `id` is exactly the bracketed text, unparsed --
   * `context.followLink` is where the grammar lives, not this editor (KYO-234.c5).
   */
  onLinkClick?(id: string): void;
  /**
   * Resolve a record's document for the child-id popup (KYO-234.c5). May be called
   * more than once for the same id across a session; caching is the caller's.
   */
  getChildDocument?(recordId: string): Promise<RecordDocument | null>;
}

const revisionField = StateField.define<number>({
  create: () => 0,
  update: (value, transaction) => (transaction.docChanged ? value + 1 : value),
});

const setCompletionSets = StateEffect.define<Completions | null>();

const completionSets = StateField.define<Completions | null>({
  create: () => null,
  update(value, transaction) {
    for (const effect of transaction.effects) {
      if (effect.is(setCompletionSets)) return effect.value;
    }
    return value;
  },
});

function triggerAtCursor(state: EditorState): Trigger | null {
  const range = state.selection.main;
  if (!range.empty) return null;
  const line = state.doc.lineAt(range.head);
  const trigger = triggerAt(line.text.slice(0, range.head - line.from), range.head - line.from);
  return trigger === null ? null : { ...trigger, start: trigger.start + line.from };
}

interface ClosedSetCompletion extends Completion {
  value: string;
}

function optionFor(suggestion: Suggestion): ClosedSetCompletion {
  return {
    label: suggestion.label,
    detail: suggestion.detail,
    value: suggestion.value,
    apply: (view: EditorView) => {
      const trigger = triggerAtCursor(view.state);
      if (trigger === null) return;
      const text = view.state.doc.toString();
      const caret = view.state.selection.main.head;
      const applied = applySuggestion(text, caret, trigger, suggestion);
      const tailStart = text.length - (applied.text.length - applied.caret);
      view.dispatch({
        changes: {
          from: trigger.start,
          to: tailStart,
          insert: applied.text.slice(trigger.start, applied.caret),
        },
        selection: { anchor: applied.caret },
        userEvent: "input.complete",
      });
    },
  };
}

/**
 * The only completion source this editor has, registered through `override` so nothing
 * else -- not CodeMirror's word completion, not markdown's HTML tag completion -- can
 * offer anything here (KYO-55). Async for the child case, which needs a bridge read the
 * other two do not.
 */
function makeClosedSetSource(
  getChildDocument: EditorOptions["getChildDocument"],
): (context: CompletionContext) => CompletionResult | Promise<CompletionResult | null> | null {
  return (context: CompletionContext) => {
    const trigger = triggerAtCursor(context.state);
    if (trigger === null) return null;
    if (trigger.kind === "child") {
      const recordId = trigger.recordId ?? "";
      if (recordId === "" || getChildDocument === undefined) return null;
      return getChildDocument(recordId).then((doc) => {
        const suggestions = matchChildIds(doc, recordId, trigger.query);
        if (suggestions.length === 0) return null;
        return {
          from: trigger.start,
          to: context.pos,
          options: suggestions.map(optionFor),
          filter: false,
        };
      });
    }
    const suggestions = suggestionsFor(trigger, context.state.field(completionSets));
    if (suggestions.length === 0) return null;
    return {
      from: trigger.start,
      to: context.pos,
      options: suggestions.map(optionFor),
      filter: false,
    };
  };
}

/** The popup that says a reference does not exist, rather than offering a guess. */
function noMatchTooltip(state: EditorState): Tooltip | null {
  const trigger = triggerAtCursor(state);
  // The child case is async and cannot answer synchronously; CodeMirror's own "no
  // completions" state (nothing shown) stands in for it rather than a second, racier
  // copy of the fetch here.
  if (trigger === null || trigger.kind === "child") return null;
  if (suggestionsFor(trigger, state.field(completionSets)).length > 0) return null;
  return {
    pos: state.selection.main.head,
    above: false,
    arrow: false,
    create: () => ({
      dom: el(
        "div",
        {
          class: "complete-popup complete-popup-empty",
          data: { region: "completion-popup", kind: trigger.kind, count: "0" },
        },
        [
          el("div", { class: "complete-empty", data: { region: "completion-empty" } }, [
            el("div", {
              text:
                trigger.kind === "record"
                  ? `No record matches "${trigger.query}".`
                  : `No tag in ideas/ matches "${trigger.query}".`,
            }),
            el("div", {
              class: "complete-why",
              text:
                trigger.kind === "record"
                  ? "Only records that exist in the store are offered, so a link to one " +
                    "that does not cannot be inserted here by accident (KYO-55)."
                  : "Tags are whatever already appears across ideas/ — there is no tag " +
                    "registry in this version.",
            }),
          ]),
        ],
      ),
    }),
  };
}

const noMatchField = StateField.define<Tooltip | null>({
  create: noMatchTooltip,
  update: (_value, transaction) => noMatchTooltip(transaction.state),
  provide: (field) => showTooltip.from(field),
});

const markdownHighlight = HighlightStyle.define([
  { tag: tags.heading1, fontSize: "1.5em", fontWeight: "700", lineHeight: "1.4" },
  { tag: tags.heading2, fontSize: "1.3em", fontWeight: "700", lineHeight: "1.4" },
  { tag: tags.heading3, fontSize: "1.15em", fontWeight: "700" },
  { tag: tags.heading4, fontWeight: "700" },
  { tag: tags.heading5, fontWeight: "700" },
  { tag: tags.heading6, fontWeight: "700" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: tags.strong, fontWeight: "700" },
  { tag: tags.strikethrough, textDecoration: "line-through" },
  { tag: tags.link, color: "var(--accent, #2563eb)" },
  { tag: tags.url, color: "var(--accent, #2563eb)", textDecoration: "underline" },
  { tag: tags.monospace, color: "var(--code-fg, #a4262c)" },
  { tag: tags.quote, color: "var(--muted, #6b7280)" },
  { tag: tags.labelName, color: "var(--muted, #6b7280)" },
  { tag: tags.contentSeparator, color: "var(--muted-2, #9ca3af)" },
  { tag: tags.processingInstruction, color: "var(--muted-2, #9ca3af)", fontWeight: "400" },
]);

const LIST_PREFIX = /^([\s>]*)(?:[-*+]|\d+[.)])(\s+)(?=\S)/;

function hangingIndents(view: EditorView): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  for (const { from, to } of view.visibleRanges) {
    for (let pos = from; pos <= to; ) {
      const line = view.state.doc.lineAt(pos);
      const match = LIST_PREFIX.exec(line.text);
      if (match !== null) {
        const columns = countColumn(line.text.slice(0, match[0].length), view.state.tabSize);
        builder.add(
          line.from,
          line.from,
          Decoration.line({
            attributes: {
              style: `padding-left:${columns}ch;text-indent:-${columns}ch`,
              "data-hanging": String(columns),
            },
          }),
        );
      }
      pos = line.to + 1;
    }
  }
  return builder.finish();
}

const hangingIndentPlugin = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    constructor(view: EditorView) {
      this.decorations = hangingIndents(view);
    }
    update(update: ViewUpdate) {
      if (update.docChanged || update.viewportChanged) {
        this.decorations = hangingIndents(update.view);
      }
    }
  },
  { decorations: (plugin) => plugin.decorations },
);

const popupLabels = ViewPlugin.fromClass(
  class {
    readonly watcher: MutationObserver;
    constructor(readonly view: EditorView) {
      this.watcher = new MutationObserver(() => this.label());
      this.watcher.observe(view.dom, { childList: true });
      this.schedule();
    }
    update() {
      this.schedule();
    }
    destroy() {
      this.watcher.disconnect();
    }
    schedule() {
      this.label();
      this.view.requestMeasure({ read: () => null, write: () => this.label() });
    }
    label() {
      const dom = this.view.dom.querySelector<HTMLElement>(".cm-tooltip-autocomplete");
      if (dom === null) return;
      const trigger = triggerAtCursor(this.view.state);
      dom.setAttribute("data-region", "completion-popup");
      dom.setAttribute("data-count", String(currentCompletions(this.view.state).length));
      if (trigger !== null) dom.setAttribute("data-kind", trigger.kind);
    }
  },
);

function shiftHeadings(delta: 1 | -1): Command {
  return (view: EditorView) => {
    const state = view.state;
    const changes: ChangeSpec[] = [];
    for (const range of state.selection.ranges) {
      const from = state.doc.lineAt(range.from).from;
      const to = state.doc.lineAt(range.to).to;
      const tree = ensureSyntaxTree(state, to, 5_000) ?? syntaxTree(state);
      tree.iterate({
        from,
        to,
        enter: (node) => {
          const match = /^ATXHeading([1-6])$/.exec(node.name);
          if (match === null) return;
          const level = Number(match[1]);
          const mark = node.node.firstChild;
          if (mark === null || mark.name !== "HeaderMark") return;
          if (delta === 1) {
            if (level >= 6) return;
            changes.push({ from: mark.from, insert: "#" });
          } else {
            if (level <= 1) return;
            changes.push({ from: mark.from, to: mark.from + 1 });
          }
        },
      });
    }
    if (changes.length === 0) return false;
    view.dispatch(state.update({ changes, userEvent: "input.shiftHeading" }));
    return true;
  };
}

export function composeEditor(options: EditorOptions): EditorHandle {
  const ribbon = el("div", { class: "ribbon idea-ribbon", data: { region: "editor-ribbon" } });
  const saveChip = el("span", {
    class: "save-state",
    text: SAVE_LABEL.clean,
    data: { region: "save-state", state: "clean" },
  });

  const surface = el("div", { class: "idea-surface", data: { region: "idea-surface" } });

  const editable = new Compartment();
  let readOnly = false;
  let tabLeaves = false;

  const tabIsTheExit = (): boolean => {
    const armed = tabLeaves;
    tabLeaves = false;
    return armed;
  };

  const paneKeys = Prec.highest(
    keymap.of([
      {
        key: "Mod-s",
        run: () => {
          options.onSaveNow();
          return true;
        },
      },
      {
        key: "Escape",
        run: (view) => {
          if (closeCompletion(view)) return true;
          tabLeaves = true;
          return false;
        },
      },
      {
        key: "Tab",
        run: (view) => {
          if (acceptCompletion(view)) return true;
          if (tabIsTheExit()) return false;
          return indentMore(view);
        },
        shift: (view) => {
          if (tabIsTheExit()) return false;
          return indentLess(view);
        },
      },
      { key: "Mod-Alt-ArrowRight", run: shiftHeadings(1) },
      { key: "Mod-Alt-ArrowLeft", run: shiftHeadings(-1) },
    ]),
  );

  const extensions: Extension[] = [
    paneKeys,
    EditorState.lineSeparator.of("\n"),
    EditorView.lineWrapping,
    EditorView.contentAttributes.of({
      "data-region": "idea-editor",
      "aria-label": "The document",
      "aria-keyshortcuts": "Escape Tab",
      spellcheck: "false",
    }),
    revisionField,
    completionSets,
    noMatchField,
    history(),
    foldGutter({
      markerDOM: (open) =>
        el("span", {
          class: "fold-marker",
          text: open ? "⌄" : "›",
          tip: open ? "fold this section" : "unfold this section",
          data: { "fold-marker": open ? "open" : "closed" },
        }),
    }),
    codeFolding({
      placeholderDOM: (_view, onclick) =>
        el("span", {
          class: "cm-foldPlaceholder",
          text: "…",
          tip: "unfold",
          data: { region: "fold-placeholder" },
          on: { click: onclick },
        }),
    }),
    indentUnit.of("  "),
    markdown({ base: markdownLanguage, completeHTMLTags: false }),
    syntaxHighlighting(markdownHighlight),
    hangingIndentPlugin,
    popupLabels,
    autocompletion({
      override: [makeClosedSetSource(options.getChildDocument)],
      closeOnBlur: false,
      tooltipClass: () => "complete-popup",
      optionClass: () => "complete-row",
      icons: false,
      addToOptions: [
        {
          render: (completion: Completion) =>
            el("span", { data: { "complete-value": (completion as ClosedSetCompletion).value } }, [
              el("span", { class: "complete-label mono", text: String(completion.label) }),
              el("span", { class: "complete-detail", text: completion.detail ?? "" }),
            ]),
          position: 20,
        },
      ],
    }),
    keymap.of([...defaultKeymap, ...historyKeymap, ...foldKeymap]),
    editable.of([]),
    EditorView.domEventHandlers({
      keydown: (event) => {
        if (event.key !== "Escape" && event.key !== "Tab") tabLeaves = false;
        return false;
      },
      /**
       * Mod-click follows a `[[id]]` (KYO-234.c5). A plain click still places the
       * caret -- this is source mode, and text under a link is still text a writer
       * may want to edit -- so the modifier is what turns a click into a follow.
       */
      click: (event, view) => {
        if (!(event.metaKey || event.ctrlKey)) return false;
        if (options.onLinkClick === undefined) return false;
        const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
        if (pos === null) return false;
        const id = linkAt(view.state.doc.toString(), pos);
        if (id === null) return false;
        options.onLinkClick(id);
        return true;
      },
      /**
       * A blur that reverses itself inside a tick is not the writer leaving.
       *
       * CodeMirror refocuses its own content element when it re-syncs the DOM, which
       * raises a `focusout` and a `focusin` about a millisecond apart; taken at face
       * value that is a departure, so it is confirmed a tick later instead of assumed
       * (v2's own KYO-117 finding, carried over unchanged).
       */
      blur: (_event, view) => {
        window.setTimeout(() => {
          if (view.hasFocus) return;
          closeCompletion(view);
          options.onBlur?.();
        }, 25);
        return false;
      },
    }),
    EditorView.updateListener.of((update: ViewUpdate) => {
      if (update.docChanged) {
        root.setAttribute("data-revision", String(update.state.field(revisionField)));
        options.onChange(update.state.doc.toString(), originOf(update));
      }
      if (update.selectionSet) options.onSelectionChange?.();
    }),
  ];

  const view = new EditorView({ state: EditorState.create({ extensions }), parent: surface });

  const header = el("div", { class: "idea-header", data: { region: "editor-ribbon-bar" } }, [
    ribbon,
    el("div", { class: "grow" }),
    saveChip,
  ]);

  const root: EditorElement = el(
    "div",
    {
      class: "idea-editor",
      data: { region: "idea-editor-pane", completions: "pending", revision: "0" },
    },
    [header, surface],
  );

  const replaceAll = (text: string, origin: ChangeOrigin = "programmatic") => {
    const anchor = Math.min(view.state.selection.main.anchor, text.length);
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: text },
      selection: { anchor },
      annotations: origin === "programmatic" ? programmaticWrite.of(true) : [],
    });
    closeCompletion(view);
  };

  const handle: EditorHandle = {
    root,
    view,
    setText(text: string) {
      replaceAll(text);
    },
    applyEdit(text: string) {
      replaceAll(text, "user");
    },
    reconcile(text: string) {
      const current = view.state.doc.toString();
      if (current === text) return;
      const changes = view.state.changes(lineChanges(current, text));
      // The line at the top of the scroller, and how far into it the view is scrolled,
      // followed through the change: what keeps a reload from jumping the page.
      const scroller = view.scrollDOM;
      const visibleTop = scroller.getBoundingClientRect().top - view.documentTop;
      const topLine = view.lineBlockAtHeight(Math.max(0, visibleTop));
      const within = visibleTop - topLine.top;
      const anchor = changes.mapPos(topLine.from, -1);
      view.dispatch({
        changes,
        annotations: [programmaticWrite.of(true), Transaction.addToHistory.of(false)],
      });
      closeCompletion(view);
      view.requestMeasure({
        read: () => view.lineBlockAt(anchor).top,
        write: (top) => {
          const now = scroller.getBoundingClientRect().top - view.documentTop;
          scroller.scrollTop += top + within - now;
        },
      });
    },
    text() {
      return view.state.doc.toString();
    },
    revision() {
      return view.state.field(revisionField);
    },
    adopt(text: string, issuedAt: number) {
      if (view.state.field(revisionField) !== issuedAt) return false;
      if (view.state.doc.toString() !== text) replaceAll(text);
      else closeCompletion(view);
      return true;
    },
    selection() {
      const range = view.state.selection.main;
      return view.state.doc.sliceString(range.from, range.to);
    },
    setCaret(offset: number) {
      const at = Math.max(0, Math.min(offset, view.state.doc.length));
      view.dispatch({ selection: { anchor: at } });
    },
    focus() {
      view.focus();
    },
    hasFocus() {
      return view.hasFocus;
    },
    setRibbon(buttons: RibbonButton[]) {
      const children: Child[] = buttons.map((button) => {
        const disabled = button.disabledBecause !== undefined;
        return el(
          "button",
          {
            class: disabled ? "ribbon-btn ribbon-btn-off" : "ribbon-btn",
            tip: button.disabledBecause ?? button.tip,
            data: { "ribbon-button": button.id, enabled: String(!disabled) },
            attrs: { type: "button", "aria-disabled": String(disabled) },
            on: {
              click: () => {
                if (!disabled) button.run();
              },
            },
          },
          [icon(button.icon, { size: 13 }), el("span", { text: button.label })],
        );
      });
      ribbon.replaceChildren(...(children.filter(Boolean) as Node[]));
      ribbon.setAttribute("data-buttons", String(buttons.length));
    },
    setCompletions(next: Completions | null) {
      view.dispatch({ effects: setCompletionSets.of(next) });
      root.setAttribute("data-completions", next === null ? "pending" : "ready");
    },
    setSaveState(state: SaveState, detail?: string) {
      saveChip.textContent = SAVE_LABEL[state];
      saveChip.setAttribute("data-state", state);
      saveChip.setAttribute("data-tip", detail ?? "");
      const locked = state === "readonly";
      if (locked === readOnly) return;
      readOnly = locked;
      root.setAttribute("data-readonly", String(locked));
      view.dispatch({
        effects: editable.reconfigure(
          locked ? [EditorState.readOnly.of(true), EditorView.editable.of(false)] : [],
        ),
      });
    },
  };

  root.kyoseiEditor = handle;
  return handle;
}
