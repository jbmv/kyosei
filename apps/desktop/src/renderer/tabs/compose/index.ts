// The compose tab (KYO-234): the scratchpad and every idea file as an editable tab on
// CodeMirror (KYO-123), with autosave and registry-backed autocomplete. The
// scratchpad's selection can become an idea FILE (KYO-252); nothing here makes a
// record -- that is the controller's act, and KYO-439 removed the idea tab's
// record promotion (`kyo promote` from the app) end to end.
//
// `arg` is `scratchpad` (the shell's own rail item opens it with that literal string,
// `shell/rail.ts`) or `idea:<path relative to ideas/>` (the sidebar's ideas list,
// `shell/sidebar.ts`) -- anything that is not the idea prefix is read as the
// scratchpad, matching both.
//
// KYO-150's rule, restated because everything below exists to keep it: a save is
// armed only by the HUMAN's own edit -- a keystroke, a paste, an undo, a ribbon
// button that rewrites text on their behalf -- and never by the app putting text on
// screen. `editor.ts`'s `onChange` origin is how that distinction survives from
// CodeMirror's own transactions to this file's debounce.
//
// KYO-575: THE TAB OWNS ITS VERSION. Two windows on one project, or one on each of two
// machines, can hold the same file. Each tab keeps the text and version (kyo serve's
// ETag) it loaded or last saved -- `base` and `version` below -- and every save names
// them, so a stale tab's save is refused by kyo serve rather than written over the
// other's; main then merges the two three ways and writes the merge (`merge3.ts`).
// A tab with no unsaved edits follows its file instead: when the events stream says
// it changed, the tab re-reads it and puts it on screen as the app, which arms no save.

import { merge3 } from "../../../shared/merge3";
import {
  composeReloadDue,
  type IdeaProperty,
  type RecordDocument,
  type SaveResult,
  type StoreChange,
} from "../../../shared/protocol";
import { el } from "../../dom";
import type { TabContext, TabInstance, TabKind } from "../kind";
import { type ChangeOrigin, composeEditor, type EditorHandle } from "./editor";
import { nameFrom, openIdeaDialog } from "./idea-dialog";
import { ensureComposeStyles } from "./styles";

const IDEA_PREFIX = "idea:";

const STAMP_FORMAT = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

/** An ISO stamp as the reader's own locale writes a date and time. */
function formatStamp(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : STAMP_FORMAT.format(date);
}

/** How long after the human's last keystroke this tab writes the disk (KYO-150). */
const SAVE_DEBOUNCE_MS = 600;

/** A title suggestion from a block of prose: its first non-blank line, heading marks stripped. */
function firstLine(text: string): string {
  const line = text.split("\n").find((candidate) => candidate.trim() !== "") ?? "";
  return line.replace(/^#+\s*/, "").trim();
}

const kind: TabKind = {
  kind: "compose",
  label: "Compose",
  home: [
    {
      order: 10,
      label: "Scratchpad",
      detail: "Get a thought out; promote it to an idea later",
      icon: "pencil",
      arg: "scratchpad",
    },
  ],
  open(context: TabContext): TabInstance {
    ensureComposeStyles();
    const isIdea = context.arg.startsWith(IDEA_PREFIX);
    const ideaPath = isIdea ? context.arg.slice(IDEA_PREFIX.length) : null;
    let title = isIdea ? (ideaPath?.split("/").pop() ?? ideaPath ?? "") : "Scratchpad";

    const root = el("div", {
      class: "compose-pane",
      data: {
        region: "compose-pane",
        composeKind: isIdea ? "idea" : "scratchpad",
        state: "loading",
      },
    });
    root.append(el("div", { class: "empty", text: "…" }));

    let closed = false;
    let editor: EditorHandle | null = null;
    let host: HTMLElement | null = null;
    let dirty = false;
    let saveTimer: number | null = null;
    let currentProperties: IdeaProperty[] = [];
    /** The file as this tab loaded or last saved it, and that version (KYO-575). */
    let base = "";
    let version: string | null = null;
    /** A save is out; another asked for meanwhile runs when it lands, never alongside. */
    let saving = false;
    let saveAgain = false;
    /** A re-read of the file is out (KYO-575.c3); one heard meanwhile runs after it. */
    let reloading = false;
    let reloadAgain = false;
    /** The file changed while a save was out: looked at again once it lands. */
    let followAfterSave = false;
    let unsubscribe: (() => void) | null = null;
    let projectPrefix: string | null = null;
    /** An idea file's folder and stamps, for the line under the ribbon (KYO-257). */
    let meta: { folder: string; createdAt: string | null; modifiedAt: string | null } | null = null;
    let metaLine: HTMLElement | null = null;
    const childDocCache = new Map<string, Promise<RecordDocument | null>>();

    context.bridge
      .getFacts()
      .then((live) => {
        projectPrefix = live.project.project;
      })
      .catch(() => {
        // A link click before this lands falls through `followLinkId`'s own empty-
        // project guard below; nothing here is load-bearing enough to retry.
      });

    function getChildDocument(recordId: string): Promise<RecordDocument | null> {
      let cached = childDocCache.get(recordId);
      if (cached === undefined) {
        cached = context.bridge
          .getRecordDocument(recordId)
          .then((doc) => (doc.ok ? doc : null))
          .catch(() => null);
        childDocCache.set(recordId, cached);
      }
      return cached;
    }

    /**
     * A `[[id]]` was Mod-clicked (KYO-234.c5). The bracketed text is handed to
     * `context.followLink` unparsed -- this tab never reads what shape an id is,
     * `main/links.ts` does.
     */
    function followLinkId(id: string): void {
      if (projectPrefix === null || projectPrefix === "") return;
      context.followLink(`kyosei://${projectPrefix}/${id}`);
    }

    function showProblem(text: string): void {
      if (closed) return;
      root.dataset.state = "problem";
      root.replaceChildren(
        el("div", { class: "empty", data: { region: "compose-problem" }, text }),
      );
    }

    function cancelSaveTimer(): void {
      if (saveTimer !== null) {
        window.clearTimeout(saveTimer);
        saveTimer = null;
      }
    }

    async function doSave(): Promise<void> {
      cancelSaveTimer();
      if (!dirty || editor === null || closed) return;
      // One save at a time per tab: two out at once would name the same version, and
      // the second would be refused by the first and merged against this tab's own text.
      if (saving) {
        saveAgain = true;
        return;
      }
      saving = true;
      try {
        await saveOnce(editor);
      } finally {
        saving = false;
      }
      if (closed) return;
      if (saveAgain) {
        saveAgain = false;
        if (dirty) {
          void doSave();
          return;
        }
      }
      if (followAfterSave) {
        followAfterSave = false;
        void followChange();
      }
    }

    async function saveOnce(handle: EditorHandle): Promise<void> {
      const sent = handle.text();
      handle.setSaveState("saving");
      const result: SaveResult = isIdea
        ? await context.bridge.saveIdea({
            path: ideaPath ?? "",
            body: sent,
            properties: currentProperties,
            base,
            version,
          })
        : await context.bridge.saveScratchpad({ text: sent, base, version });
      if (closed || editor === null) return;
      if (!result.ok) {
        handle.setSaveState("failed", result.problem ?? "the write failed");
        return;
      }
      base = result.base ?? base;
      version = result.version;
      if (result.properties !== null) currentProperties = result.properties;
      // Typed while the save was out: those keystrokes are not on disk yet, and the
      // keystroke that made them scheduled the save that will put them there.
      const typedSince = handle.text() !== sent;
      let detail: string | undefined;
      if (result.merged !== null) {
        // The file had changed since this tab's version and main merged the two. The
        // merge is put on screen as the app (no save armed: it is already written),
        // and anything typed while the save was out is carried onto it.
        const stored = result.body ?? sent;
        const now = handle.text();
        handle.reconcile(typedSince ? merge3(sent, now, stored, "this window").text : stored);
        detail =
          result.merged.conflicts === 0
            ? "merged with a change saved in another window or machine"
            : `merged with a change saved elsewhere; ${result.merged.conflicts} overlap(s) kept between ━━ lines`;
        notice(
          result.merged.conflicts === 0
            ? "This file was changed in another window or machine since this one loaded it. Both sets of changes were kept; nothing overlapped."
            : "This file was changed in another window or machine while this one had unsaved text. Both versions are kept between ━━ marker lines: the other one first, this window's below it.",
        );
      }
      dirty = typedSince;
      handle.setSaveState(dirty ? "dirty" : "saved", detail);
      if (meta !== null) {
        meta.modifiedAt = new Date().toISOString();
        paintMeta();
      }
    }

    /**
     * The idea file's folder, created and modified, as one muted line under the
     * ribbon. `created` is the file system's birth time, so the tip says
     * what it means for a file that arrived by clone or copy.
     */
    function paintMeta(): void {
      if (editor === null || meta === null || closed) return;
      const stamp = (label: "created" | "modified", iso: string | null): HTMLElement =>
        el("span", {
          text: `${label} ${iso === null ? "unknown" : formatStamp(iso)}`,
          data: { meta: label, iso: iso ?? "" },
          tip:
            label === "created"
              ? "When this file appeared on this disk (a cloned or copied file says when it landed)"
              : "When this file was last written",
        });
      const line = el("div", { class: "idea-meta", data: { region: "idea-meta" } }, [
        el("span", {
          text: meta.folder === "" ? "ideas/" : `ideas/${meta.folder}/`,
          data: { meta: "folder" },
        }),
        stamp("created", meta.createdAt),
        stamp("modified", meta.modifiedAt),
      ]);
      if (metaLine?.isConnected) metaLine.replaceWith(line);
      else editor.root.firstElementChild?.after(line);
      metaLine = line;
    }

    /**
     * The events stream said something moved (KYO-575.c3). A tab with no unsaved edits
     * whose file it was re-reads the file and shows it; one with unsaved edits leaves
     * its text alone, since the save it owes merges with whatever changed.
     */
    function onStoreChanged(change: StoreChange): void {
      if (editor === null || closed) return;
      if (composeReloadDue({ changed: change.kind, isIdea, dirty, saving })) {
        void followChange();
      } else if (
        saving &&
        composeReloadDue({ changed: change.kind, isIdea, dirty, saving: false })
      ) {
        followAfterSave = true;
      }
    }

    async function followChange(): Promise<void> {
      if (reloading) {
        reloadAgain = true;
        return;
      }
      reloading = true;
      try {
        do {
          reloadAgain = false;
          if (editor === null || closed || dirty || saving) return;
          const revision = editor.revision();
          const read = isIdea
            ? await context.bridge.openIdea(ideaPath ?? "")
            : await context.bridge.getScratchpad();
          // The human typed while the read was out: their text wins the screen, and the
          // save they armed merges with the file.
          if (editor === null || closed || dirty || saving || editor.revision() !== revision) {
            return;
          }
          if (!read.ok || read.version === version) continue;
          if ("body" in read) {
            base = read.base;
            currentProperties = read.properties;
            editor.reconcile(read.body);
            if (meta !== null) {
              meta.modifiedAt = read.modifiedAt;
              paintMeta();
            }
          } else {
            base = read.text;
            editor.reconcile(read.text);
          }
          version = read.version;
          // The app put this on screen; nothing is owed (KYO-150).
          editor.setSaveState("clean", "reloaded: changed in another window or machine");
        } while (reloadAgain);
      } finally {
        reloading = false;
      }
    }

    function scheduleSave(): void {
      cancelSaveTimer();
      saveTimer = window.setTimeout(() => {
        void doSave();
      }, SAVE_DEBOUNCE_MS);
    }

    function onChange(_text: string, origin: ChangeOrigin): void {
      // KYO-150: the app putting text on screen is not the human editing it. Only a
      // "user" origin arms the save -- see `editor.ts`'s own `originOf`.
      if (origin !== "user") return;
      dirty = true;
      editor?.setSaveState("dirty");
      scheduleSave();
    }

    function updateRibbon(): void {
      if (editor === null) return;
      const selection = editor.selection();
      if (!isIdea) {
        // The scratchpad (KYO-252): ONE button, and it makes an idea file. Records are
        // the controller's to make.
        editor.setRibbon([
          {
            id: "promote-selection",
            label: "Promote to idea",
            icon: "plus",
            tip: "Select text, then save it as a new idea file",
            disabledBecause: selection.trim() === "" ? "select some text first" : undefined,
            run: () => beginIdea(),
          },
        ]);
        return;
      }
      // An idea file (KYO-439): no buttons. Promoting an idea into a record is the
      // controller's act -- the app cannot know how to write a record's structure --
      // so the idea tab offers nothing that would make one.
      editor.setRibbon([]);
    }

    /**
     * The scratchpad's promotion (KYO-252): the selection becomes an idea file. The
     * folders offered are the vault's own; the text leaves the scratchpad when the
     * dialog's checkbox says so, and only if the buffer still holds exactly what was
     * promoted.
     */
    function beginIdea(): void {
      if (editor === null) return;
      const range = {
        from: editor.view.state.selection.main.from,
        to: editor.view.state.selection.main.to,
      };
      const text = editor.selection();
      if (text.trim() === "") return;
      void context.bridge.getIdeaTree().then((tree) => {
        if (editor === null || closed) return;
        const folders = tree.ok ? ["", ...tree.folders.filter((f) => f !== "")] : [""];
        openIdeaDialog(root, {
          bridge: context.bridge,
          text,
          suggestedName: nameFrom(firstLine(text)),
          folders,
          onSaved: (result, remove) => {
            if (editor === null || closed) return;
            if (remove) {
              const current = editor.text();
              if (current.slice(range.from, range.to) === text) {
                editor.applyEdit(current.slice(0, range.from) + current.slice(range.to));
              }
            }
            editor.setSaveState("saved", `saved ${result.relativePath}; path copied`);
            notice(`Saved ${result.relativePath} — path copied to the clipboard.`);
          },
          onCancel: () => {},
        });
      });
    }

    /** A line under the ribbon that says what just happened, and goes away. */
    function notice(text: string): void {
      const line = el("div", { class: "compose-notice", data: { region: "compose-notice" }, text });
      root.prepend(line);
      setTimeout(() => line.remove(), 8000);
    }

    function buildEditor(initialText: string): void {
      editor = composeEditor({
        onChange,
        onSaveNow: () => {
          void doSave();
        },
        onSelectionChange: updateRibbon,
        onBlur: () => {
          void doSave();
        },
        onLinkClick: followLinkId,
        getChildDocument,
      });
      root.dataset.state = "ready";
      root.replaceChildren(editor.root);
      editor.setText(initialText);
      editor.setSaveState("clean");
      updateRibbon();
      context.bridge
        .getCompletions()
        .then((completions) => {
          if (!closed) editor?.setCompletions(completions);
        })
        .catch(() => {
          // The `[[`/`#` popup offers nothing until this lands or is retried by
          // reopening the tab; there is no partial completion set worth showing.
        });
    }

    async function init(): Promise<void> {
      if (isIdea) {
        const doc = await context.bridge.openIdea(ideaPath ?? "");
        if (closed) return;
        if (!doc.ok) {
          showProblem(doc.problem ?? `${ideaPath ?? ""} could not be read`);
          return;
        }
        currentProperties = doc.properties;
        base = doc.base;
        version = doc.version;
        title = (ideaPath ?? doc.path).split("/").pop() ?? doc.path;
        context.setTitle(title);
        buildEditor(doc.body);
        const path = ideaPath ?? doc.path;
        meta = {
          folder: path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "",
          createdAt: doc.createdAt,
          modifiedAt: doc.modifiedAt,
        };
        paintMeta();
      } else {
        const pad = await context.bridge.getScratchpad();
        if (closed) return;
        if (!pad.ok) {
          showProblem(pad.problem ?? "the scratchpad could not be read");
          return;
        }
        base = pad.text;
        version = pad.version;
        buildEditor(pad.text);
      }
      if (!closed) unsubscribe = context.bridge.onStoreChanged(onStoreChanged);
    }

    void init().catch((error: unknown) => showProblem(`kyosei: ${String(error)}`));

    /**
     * Mirror `root`'s own `data-state` onto the shell's tab-body element.
     *
     * `render`'s `host` is the shell's, not this tab's -- it is what `columns.ts`
     * hands over and reparents `root` into, and it never otherwise carries this
     * tab's state. KYO-232's own placeholder set `data-state` directly on that
     * element (there was no inner wrapper yet), and `shell.spec.ts` still asserts
     * `[data-tab-kind="compose"][data-state="ready"]` against it -- so the state is
     * kept mirrored up rather than only on `root`, and a `MutationObserver` is what
     * makes that hold regardless of whether `render` was called before or after
     * `root`'s state has already changed.
     */
    let stateObserver: MutationObserver | null = null;
    function syncHostState(): void {
      if (host !== null) host.dataset.state = root.dataset.state ?? "";
    }

    return {
      title: () => title,
      render(hostEl: HTMLElement) {
        host = hostEl;
        host.replaceChildren(root);
        syncHostState();
        stateObserver?.disconnect();
        stateObserver = new MutationObserver(syncHostState);
        stateObserver.observe(root, { attributes: true, attributeFilter: ["data-state"] });
      },
      close() {
        closed = true;
        cancelSaveTimer();
        unsubscribe?.();
        unsubscribe = null;
        stateObserver?.disconnect();
        stateObserver = null;
        host = null;
      },
    };
  },
};

export default kind;
