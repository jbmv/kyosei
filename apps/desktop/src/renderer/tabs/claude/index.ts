// The claude tab: the Remote Control web view, as a tab (KYO-186, KYO-232.c4).
//
// The renderer owns a RECTANGLE and a caption; the main process owns the page. That
// split is forced rather than chosen -- claude.ai sends `x-frame-options: SAMEORIGIN`,
// so the content cannot be in this document at all, and a `WebContentsView` is separate
// top-level contents positioned over the window.
//
// So everything here is about keeping those two in step: telling main where the
// rectangle is whenever it moves, taking the view away when the tab is not the one
// showing, and rendering the one thing the view itself cannot say -- which session the
// pane resolved to, and why.
//
// ONE PER WINDOW (`singleton`), for a different reason from the terminal's. A pty is
// scarce because it is one process with one output stream. A logged-in web view is not
// scarce that way -- the login lives in the shared `persist:claude-ai` partition -- but
// `main.ts` keeps ONE view per hosting window and positions it by absolute bounds, so
// two tabs in one window would be two rectangles claiming one view.

import { trustOffer } from "../../../shared/controller-trust";
import {
  type ClaudePane,
  CONTROLLER_POLL_MS,
  type ControllerState,
} from "../../../shared/protocol";
import { el } from "../../dom";
import { icon } from "../../icons";
import { onOverlayChange, overlayOpen } from "../../overlays";
import type { TabContext, TabInstance, TabKind } from "../kind";

/** What the caption says for each resolution, and whether it reads as a problem. */
function caption(state: ClaudePane): { text: string; tone: string } {
  if (state.problem !== null) {
    return { text: `could not load: ${state.problem}`, tone: "problem" };
  }
  if (state.deepLinked) return { text: state.why, tone: "linked" };
  // Not a failure, and worded so it does not read as one. KYO-186 is explicit that the
  // account's own session list is what the feature IS on a machine where Claude Code
  // runs somewhere else -- which is the arrangement it was filed for.
  return { text: `your sessions — ${state.why}`, tone: "index" };
}

/**
 * Ask for a line of text over the page, the way the sidebar's prompts do (KYO-250): the
 * same elements and classes, and `data-overlay`, so the claude view steps aside while
 * it is open (`overlays.ts`) instead of drawing over it. Resolves null on cancel.
 */
function askText(question: string, initial: string): Promise<string | null> {
  return new Promise((resolve) => {
    const input = el("input", {
      class: "prompt-input",
      data: { region: "prompt-input" },
      attrs: { type: "text", value: initial },
    });
    input.value = initial;
    const finish = (answer: string | null): void => {
      overlay.remove();
      resolve(answer);
    };
    const overlay = el(
      "div",
      { class: "prompt-overlay", data: { region: "prompt", overlay: "prompt" } },
      [
        el("div", { class: "prompt-dialog" }, [
          el("div", { class: "prompt-question", text: question }),
          input,
          el("div", { class: "prompt-actions" }, [
            el("button", {
              class: "prompt-button",
              text: "Cancel",
              data: { region: "prompt-cancel" },
              attrs: { type: "button" },
              on: { click: () => finish(null) },
            }),
            el("button", {
              class: "prompt-button prompt-ok",
              text: "OK",
              data: { region: "prompt-ok" },
              attrs: { type: "button" },
              on: { click: () => finish(input.value) },
            }),
          ]),
        ]),
      ],
    );
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") finish(input.value);
      if (event.key === "Escape") finish(null);
    });
    document.body.append(overlay);
    input.focus();
    input.select();
  });
}

/**
 * Show or hide one of the controller row's controls. An inline `display` rather than
 * the `hidden` attribute: `.pane-action` sets `display: grid`, which beats `[hidden]`,
 * and the change button stayed on screen beside a running controller (seen in the
 * check's own screenshot).
 */
function showIf(node: HTMLElement, visible: boolean): void {
  node.style.display = visible ? "" : "none";
}

const kind: TabKind = {
  kind: "claude",
  label: "Claude",
  singleton: true,
  home: [
    {
      order: 40,
      label: "Claude",
      detail: "This project's orchestrator session on claude.ai",
      icon: "chat",
    },
  ],
  open(context: TabContext): TabInstance {
    let shown = false;
    let closed = false;
    /** Stepped aside for an overlay: the view is off the window and a still is in its place. */
    let covered = false;
    let settling = false;
    let observer: ResizeObserver | null = null;
    let off: (() => void) | null = null;
    let offOverlay: (() => void) | null = null;
    let frame: HTMLElement | null = null;
    let status: HTMLElement | null = null;
    // The controller launcher's row (KYO-383): whether this project's controller
    // session runs on the machine that holds it, Start, and the command Start runs.
    let controllerRow: HTMLElement | null = null;
    let controllerText: HTMLElement | null = null;
    let controllerCommand: HTMLElement | null = null;
    let controllerStart: HTMLButtonElement | null = null;
    let controllerChange: HTMLButtonElement | null = null;
    // KYO-567: shown while the session waits on Claude Code's trust prompt, and opens
    // the terminal tab where the prompt is. It answers nothing itself (KYO-568).
    let controllerTrust: HTMLButtonElement | null = null;
    let controller: ControllerState | null = null;
    let poll: number | null = null;
    let asking = false;

    const boundsOf = (): { x: number; y: number; width: number; height: number } => {
      const rect = frame?.getBoundingClientRect();
      return {
        x: Math.round(rect?.left ?? 0),
        y: Math.round(rect?.top ?? 0),
        width: Math.round(rect?.width ?? 1),
        height: Math.round(rect?.height ?? 1),
      };
    };

    const render = (state: ClaudePane): void => {
      if (status === null || closed) return;
      const { text, tone } = caption(state);
      status.textContent = text;
      status.dataset.outcome = state.outcome;
      status.dataset.tone = tone;
      status.dataset.deepLinked = String(state.deepLinked);
      // The full reason on hover, always, even when the caption already shows it: the
      // caption is one line and these sentences name paths.
      status.dataset.tip = `${state.url} — ${state.why}`;
    };

    /**
     * Draw the controller row from one answer (KYO-383).
     *
     * Running: one line saying so, and nothing to press. Not running: the line names
     * the machine, Start is offered, and the program it would run is shown -- with a
     * way to change the command only in a window on that machine, because the
     * command is that machine's own preference and no route writes it.
     */
    const renderController = (state: ControllerState): void => {
      if (closed || controllerRow === null || controllerText === null) return;
      controller = state;
      const { status: now, editable } = state;
      controllerRow.dataset.controllerRunning = String(now.running);
      controllerRow.dataset.controllerSession = now.session ?? "";
      controllerRow.dataset.controllerMachine = now.machine;
      const trust = trustOffer(now);
      controllerRow.dataset.controllerTrust = String(trust !== null);
      controllerText.textContent =
        trust !== null
          ? trust.text
          : now.problem === null
            ? now.why
            : `${now.why} — ${now.problem}`;
      controllerText.dataset.tone =
        now.problem !== null
          ? "problem"
          : trust !== null
            ? "waiting"
            : now.running
              ? "linked"
              : "index";
      controllerText.dataset.tip =
        now.recorded === null
          ? now.why
          : `${now.why}. sessions.json records ${now.recorded.sessionId} here as ${now.recorded.state}.`;
      if (controllerCommand !== null) {
        showIf(controllerCommand, !(now.running || now.program === null));
        controllerCommand.textContent = `runs: ${now.program ?? now.command}`;
        controllerCommand.dataset.tip = editable
          ? `Start runs this in a tmux session named ${now.session ?? "after the project"}, in ${now.root}, on ${now.machine}. The command is this machine's own preference${now.commandIsDefault ? " (the default; claude --chrome needs Chrome on this machine)" : ""}; --remote-control ${now.session ?? "<prefix>"} is appended, which names the session in claude.ai's list.`
          : `Start runs this on ${now.machine}. The command is that machine's own preference: change it from a window on ${now.machine}.`;
      }
      if (controllerStart !== null) {
        showIf(controllerStart, !(now.running || !now.canStart));
        controllerStart.disabled = false;
      }
      if (controllerChange !== null) showIf(controllerChange, !(now.running || !editable));
      if (controllerTrust !== null) {
        showIf(controllerTrust, trust !== null);
        if (trust !== null) {
          controllerTrust.textContent = trust.action;
          controllerTrust.dataset.tip = trust.tip;
        }
      }
    };

    /** Ask again, unless the tab is out of sight or a start is in flight. */
    const askController = (): void => {
      if (closed || asking) return;
      if (frame !== null && !(frame.isConnected && frame.clientWidth > 0)) return;
      asking = true;
      void context.bridge
        .controllerStatus()
        .then(renderController)
        .catch(() => undefined)
        .finally(() => {
          asking = false;
        });
    };

    const startController = (): void => {
      if (controllerStart !== null) controllerStart.disabled = true;
      asking = true;
      void context.bridge
        .startController()
        .then(renderController)
        .catch(() => undefined)
        .finally(() => {
          asking = false;
          if (controllerStart !== null) controllerStart.disabled = false;
        });
    };

    const changeCommand = async (): Promise<void> => {
      const now = controller?.status;
      if (now === undefined) return;
      const answer = await askText(
        `The command Start runs on ${now.machine}, in ${now.root}. ` +
          `"--remote-control ${now.session ?? "<prefix>"}" is appended. ` +
          "Leave it empty for the default, claude --chrome, which needs Chrome on this machine.",
        now.commandIsDefault ? "" : now.command,
      );
      if (answer === null || closed) return;
      const outcome = await context.bridge.setControllerCommand(answer);
      renderController(outcome.state);
      if (!outcome.ok && controllerText !== null && outcome.problem !== null) {
        controllerText.textContent = `could not change the command: ${outcome.problem}`;
        controllerText.dataset.tone = "problem";
      }
    };

    /**
     * Show the view when this tab has a rectangle, hide it when it has none.
     *
     * A tab the reader has flicked away from is `hidden`, which gives it a zero-sized
     * box -- and a `WebContentsView` left at its old bounds would go on painting over
     * whichever tab IS showing, because it is a sibling of the whole page rather than
     * a child of this element. So the size is what decides, and the ResizeObserver
     * that reports it is the one thing watching.
     */
    const follow = (): void => {
      if (closed || frame === null) return;
      const visible = frame.isConnected && frame.clientWidth > 0 && frame.clientHeight > 0;
      if (covered) {
        // The view is already off the window; uncovering puts it back at whatever the
        // bounds are by then, so all that moves here is whether it should come back.
        shown = visible;
        void settle();
        return;
      }
      if (visible && !shown) {
        shown = true;
        // Coming into sight is a reason to ask about the controller now, not a poll later.
        askController();
        void context.bridge.showClaude(boundsOf()).then((state) => {
          if (state !== null && !closed) render(state);
        });
        return;
      }
      if (!visible && shown) {
        shown = false;
        // Detached, never destroyed: hiding the tab must not throw away the page the
        // reader was on, exactly as hiding the terminal does not kill its pty.
        void context.bridge.hideClaude();
        return;
      }
      if (visible) context.bridge.setClaudeBounds(boundsOf());
    };

    /**
     * Step aside while a menu or prompt is open, and come back when none is.
     *
     * The view is composited above this whole document (see `overlays.ts`), so a menu
     * over this column would be drawn under the page and its clicks would go to
     * claude.ai. While one is open the view is taken off the window and a still of it
     * sits in the frame, so the column does not blank for the length of a right-click.
     *
     * One loop, never two: opening and closing a menu quickly would otherwise interleave
     * a snapshot with an uncover and leave the view off the window with nothing open.
     * Each step re-reads what is wanted, so the last event always wins.
     */
    const settle = async (): Promise<void> => {
      if (settling) return;
      settling = true;
      try {
        for (;;) {
          const want = shown && !closed && overlayOpen();
          if (want === covered) return;
          if (want) {
            const still = await context.bridge.snapshotClaude();
            if (frame !== null && still !== null) frame.style.backgroundImage = `url("${still}")`;
            frame?.setAttribute("data-covered", "true");
            covered = true;
            await context.bridge.hideClaude();
          } else {
            covered = false;
            if (shown && !closed) {
              const state = await context.bridge.uncoverClaude(boundsOf());
              if (state !== null && !closed) render(state);
            }
            // Cleared after the view is back, not before: the still sits under the live
            // view for that moment rather than the frame showing empty.
            if (frame !== null) frame.style.backgroundImage = "";
            frame?.removeAttribute("data-covered");
          }
        }
      } finally {
        settling = false;
      }
    };

    return {
      title: () => "Claude",
      render(host) {
        const reload = el("button", {
          class: "pane-action",
          data: { action: "reload-claude" },
          tip: "Resolve again and reload",
          on: {
            click: () => {
              void context.bridge.reloadClaude().then((state) => {
                if (state !== null && !closed) render(state);
              });
            },
          },
        });
        reload.append(icon("popout", { size: 13 }));
        status = el("span", { class: "claude-status", data: { region: "claude-status" } });
        frame = el("div", { class: "claude-frame", data: { region: "claude-frame" } });
        // How to open a record a message names (KYO-263.c6). It belongs HERE because
        // nothing else can say it: what claude.ai leaves of a `kyosei:` markdown link
        // is an anchor with the words and no href, so it LOOKS like a link that does
        // nothing, and a reader has no way to discover that it now opens the record.
        // The sentence says "linked" rather than "an id" because that distinction is
        // the whole of what works: a bare id in plain text is not an element to click.
        const hint = el("span", {
          class: "claude-hint",
          data: { region: "claude-open-hint" },
          tip: "A record id written as a markdown link with a kyosei:// target is clickable here — claude.ai keeps the link and drops its href, and the app reads the click. A bare id or a bare kyosei:// URL in plain text is not a link at all, so nothing opens it.",
        });
        hint.textContent = "click a linked record id to open it";
        controllerText = el("span", {
          class: "claude-status",
          data: { region: "claude-controller-status" },
        });
        controllerCommand = el("span", {
          class: "claude-hint",
          data: { region: "claude-controller-command" },
        });
        showIf(controllerCommand, false);
        controllerChange = el("button", {
          class: "pane-action",
          data: { region: "claude-controller-change" },
          tip: "Change the command Start runs on this machine",
          on: { click: () => void changeCommand() },
        });
        controllerChange.append(icon("pencil", { size: 13 }));
        showIf(controllerChange, false);
        controllerStart = el("button", {
          class: "term-pill",
          text: "Start",
          data: { region: "claude-controller-start" },
          tip: "Start this project's orchestrator session in tmux, on the machine that holds the project",
          on: { click: startController },
        });
        showIf(controllerStart, false);
        controllerTrust = el("button", {
          class: "term-pill",
          text: "Open terminal",
          data: { region: "claude-controller-trust" },
          tip: "Open the terminal tab, where Claude Code's prompt is waiting",
          on: {
            click: () => {
              const offer = controller === null ? null : trustOffer(controller.status);
              if (offer !== null) context.openTab({ ...offer.tab });
            },
          },
        });
        showIf(controllerTrust, false);
        controllerRow = el("div", { class: "claude-head", data: { region: "claude-controller" } }, [
          controllerText,
          el("span", { class: "grow" }),
          controllerCommand,
          controllerChange,
          controllerStart,
          controllerTrust,
        ]);
        host.classList.add("claude-host");
        host.replaceChildren(
          el("div", { class: "claude-head" }, [
            status,
            el("span", { class: "grow" }),
            hint,
            reload,
          ]),
          controllerRow,
          frame,
        );

        observer?.disconnect();
        observer = new ResizeObserver(follow);
        observer.observe(frame);
        window.addEventListener("resize", follow);
        window.addEventListener("scroll", follow, true);
        off?.();
        off = context.bridge.onClaudeState((state) => {
          if (!closed) render(state);
        });
        offOverlay?.();
        offOverlay = onOverlayChange(() => void settle());
        follow();
        // Asked now and then on an interval, never pushed: the answer is tmux's, and a
        // program that ends with `/exit` announces it to nobody. KYO-383.c4's "within
        // one poll" is this interval.
        if (poll !== null) window.clearInterval(poll);
        poll = window.setInterval(askController, CONTROLLER_POLL_MS);
        askController();
      },
      close() {
        closed = true;
        if (poll !== null) window.clearInterval(poll);
        poll = null;
        observer?.disconnect();
        observer = null;
        window.removeEventListener("resize", follow);
        window.removeEventListener("scroll", follow, true);
        off?.();
        off = null;
        offOverlay?.();
        offOverlay = null;
        if (shown) {
          shown = false;
          void context.bridge.hideClaude();
        }
      },
    };
  },
};

export default kind;
