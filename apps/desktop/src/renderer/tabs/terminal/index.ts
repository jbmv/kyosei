// The terminal tab: an xterm.js screen bound to the pty the main process owns.
//
// xterm.js because the pane is a *real* shell (KYO-111.c4), and a real shell emits
// real terminal control sequences -- cursor movement, color, alternate screen, line
// editing from readline. A textarea with ANSI stripped would look right until the first
// `git log`, `less` or tab completion. The renderer holds no pty: it sends keystrokes
// and receives bytes over the bridge, which is the whole of its capability.
//
// ONE PER WINDOW (`singleton`). There is one pty behind this tab, and two screens on
// one pty would be two views fighting over its size -- which, in tmux mode, resizes
// every other client attached to the shared session too.
//
// ------------------------------------------------ `kyosei://` in the output (c7)
//
// A `kyosei://` URL printed into the pane is a hyperlink, and clicking it opens the
// record in this app. Through `Terminal.registerLinkProvider` and deliberately NOT
// `@xterm/addon-web-links`: that addon's regex is built for `http(s)://` and does not
// match a custom scheme at all, so adding it would produce a pane where every link
// works except this app's own (KYO-229.r1 disagreements[0], moved here as KYO-232.c7).
//
// The click hands the URL to `context.followLink`, which is the main process's
// `followLink` -- the same function the claude view's window-open handler calls, the
// same `LinkDelivery` back over `onOpenTarget`, the same refusals for an unknown
// project or an unknown id shape. The renderer never parses the id (KYO-230).

import { FitAddon } from "@xterm/addon-fit";
import { type IDisposable, type ILink, Terminal } from "@xterm/xterm";
import {
  type PaneSurface,
  type SessionPill,
  TERMINAL_POLL_MS,
  type TerminalAttachOutcome,
  type TerminalOffer,
  type TerminalOpen,
  type TerminalSessionsView,
} from "../../../shared/protocol";
import { el } from "../../dom";
import type { TabContext, TabInstance, TabKind } from "../kind";

/** The shell's terminal colors, so the pane matches the window around it. */
const THEME = {
  background: "#f1f3f6",
  foreground: "#16181d",
  cursor: "#16181d",
  selectionBackground: "#c7d7f5",
} as const;

/**
 * Every `kyosei://` run in a line of output.
 *
 * Stops at whitespace and at the characters that end a URL in prose -- a closing
 * bracket, a quote, a comma or a full stop at the end -- so a link inside `(see
 * kyosei://kyosei/KYO-232)` is the link and not the link plus the paren.
 */
const LINK_PATTERN = /kyosei:\/\/[^\s"'<>`]*[^\s"'<>`.,;:!?)\]}]/g;

/** The links in one line of the buffer, as xterm's 1-based ranges. */
function linksIn(
  line: string,
  row: number,
  activate: (url: string, newTab: boolean) => void,
): ILink[] {
  const found: ILink[] = [];
  LINK_PATTERN.lastIndex = 0;
  for (const match = { value: LINK_PATTERN.exec(line) }; match.value !== null; ) {
    const text = match.value[0];
    const start = match.value.index;
    found.push({
      // xterm columns are 1-based and `end` is inclusive.
      range: { start: { x: start + 1, y: row }, end: { x: start + text.length, y: row } },
      text,
      decorations: { underline: true, pointerCursor: true },
      activate: (event, url) => {
        event.preventDefault();
        activate(url, event.metaKey || event.ctrlKey);
      },
    });
    match.value = LINK_PATTERN.exec(line);
  }
  return found;
}

interface Attached {
  dispose(): void;
}

/**
 * The strip above the screen, and everything the reader can click in this pane.
 *
 * DRAWN, NEVER DECIDED (KYO-283). Every pill's name, order, liveness and highlight
 * comes out of one main-process answer (`TerminalSessionsView`), and a click sends
 * back a NAME. The renderer cannot see a tmux server, so a pill it composed itself
 * would be a guess with a click on it -- and a click that could name a tmux command
 * would be a renderer holding a capability nothing else in this app gives it.
 *
 * The status line below it is KYO-115's, unchanged in its reasoning: DOM above the
 * screen rather than text written into it, because tmux repaints the whole screen the
 * moment a client attaches and anything this app had written there would be erased by
 * the session it was describing. Every word in it still comes from the main process.
 */
class SessionStrip {
  readonly root: HTMLElement;
  private readonly row: HTMLElement;
  private readonly text: HTMLElement;
  private readonly tag: HTMLElement;
  private readonly bare: HTMLButtonElement;
  private line: HTMLElement | null = null;

  constructor(
    private readonly onPill: (name: string) => void,
    private readonly onBare: () => void,
  ) {
    this.tag = el("span", { class: "term-status-tag mono", text: "bare shell" });
    this.text = el("span", { class: "term-status-text" });
    // Furthest left, and outside the scrolling row so a machine with a dozen sessions
    // cannot push it off the edge.
    this.bare = el("button", {
      class: "term-bare",
      text: "bare shell",
      attrs: { type: "button" },
      data: { region: "terminal-bare" },
      tip:
        "Show this pane's own login shell -- a screen of its own, kept between visits " +
        "-- and detach this pane's tmux client. The session and everything in it keep " +
        "running.",
      on: { click: () => this.onBare() },
    });
    this.row = el("div", { class: "term-pills", data: { region: "terminal-pills" } });
    this.root = el("div", { class: "term-strip", data: { region: "terminal-strip" } }, [
      this.bare,
      this.row,
    ]);
  }

  /** The status line, which sits under the strip and says what the pane IS. */
  statusLine(opened: TerminalOpen): HTMLElement {
    this.line = el(
      "div",
      { class: "term-status", data: { region: "terminal-status", terminalMode: opened.mode } },
      [this.tag, this.text],
    );
    this.say(opened.notice, opened.mode, opened.session);
    return this.line;
  }

  say(notice: string, mode: "bare" | "tmux", session: string | null): void {
    this.text.textContent = notice;
    this.tag.textContent = mode === "tmux" ? (session ?? "tmux") : "bare shell";
    if (this.line === null) return;
    this.line.dataset.terminalMode = mode;
    this.line.setAttribute("data-tip", notice);
    if (session === null) delete this.line.dataset.terminalSession;
    else this.line.dataset.terminalSession = session;
  }

  draw(view: TerminalSessionsView): void {
    this.row.dataset.terminalPills = String(view.pills.length);
    this.row.replaceChildren(
      ...view.pills.map((pill) => this.pill(pill)),
      ...(view.pills.length === 0
        ? [
            el("span", {
              class: "term-pills-empty",
              text: view.problem ?? "no orchestrator sessions running",
            }),
          ]
        : []),
    );
    this.bare.disabled = view.attached === null;
  }

  /**
   * A dev box's pane has no bare shell -- its project is on the other machine -- so the
   * leftmost control only detaches the ssh client there (KYO-474).
   */
  overSsh(): void {
    this.bare.textContent = "detach";
    this.bare.setAttribute(
      "data-tip",
      "Detach this pane from the session on the dev box. The session, and claude in " +
        "it, keep running there; Reconnect attaches again.",
    );
  }

  private pill(pill: SessionPill): HTMLElement {
    const detail = pill.live
      ? `${pill.clients} client${pill.clients === 1 ? "" : "s"} attached`
      : "no orchestrator session running -- Start orchestrator starts one";
    return el("button", {
      class: "term-pill",
      text: pill.name,
      attrs: { type: "button" },
      data: {
        region: "terminal-pill",
        terminalPill: pill.name,
        // Three independent facts, three attributes: the project's own pill is set
        // apart whether or not the pane is in it, and the pane's own session is
        // highlighted whether or not it is the project's (KYO-283.c2).
        terminalPillProject: String(pill.project),
        terminalPillLive: String(pill.live),
        terminalPillCurrent: String(pill.current),
      },
      tip: `${pill.name} -- ${detail}`,
      on: { click: () => this.onPill(pill.name) },
    });
  }
}

/**
 * What the pane offers when it is not attached (KYO-474.c4): one button -- Start
 * controller where the project has no session, Reconnect where there is one to go back
 * to or ssh itself failed -- and, beneath it, what ssh, kyo or tmux said, verbatim.
 *
 * Drawn, never decided: the offer is the main process's, and the buttons run the same
 * two acts the rest of the app already has -- `startController` (the claude tab's
 * KYO-460 flow) and `attachTerminal` (a pill click).
 */
class OfferPanel {
  readonly root: HTMLElement;
  private readonly why: HTMLElement;
  private readonly said: HTMLElement;
  private readonly button: HTMLButtonElement;
  private offer: TerminalOffer | null = null;

  constructor(
    private readonly onReconnect: (session: string) => void,
    private readonly onStart: (session: string, done: (problem: string | null) => void) => void,
  ) {
    this.button = el("button", {
      class: "term-offer-action",
      attrs: { type: "button" },
      data: { region: "terminal-offer-action" },
      on: { click: () => this.act() },
    });
    // The status line above is one line and ellipsised; the sentence that explains why
    // the pane is not attached is the one a reader has to be able to read whole.
    this.why = el("div", { class: "term-offer-why", data: { region: "terminal-offer-why" } });
    this.said = el("pre", { class: "term-offer-said mono", data: { region: "terminal-said" } });
    this.root = el("div", { class: "term-offer", data: { region: "terminal-offer" } }, [
      el("div", { class: "term-offer-head" }, [this.why, this.button]),
      this.said,
    ]);
    this.show(null);
  }

  show(offer: TerminalOffer | null, notice: string | null = null): void {
    if (notice !== null) this.why.textContent = notice;
    this.offer = offer;
    this.root.hidden = offer === null;
    this.button.disabled = false;
    if (offer === null) {
      delete this.root.dataset.terminalOffer;
      return;
    }
    this.root.dataset.terminalOffer = offer.action;
    this.button.dataset.terminalOffer = offer.action;
    this.button.textContent = offer.action === "start" ? "Start orchestrator" : "Reconnect";
    this.said.hidden = offer.said === null || offer.said === "";
    this.said.textContent = offer.said ?? "";
  }

  private act(): void {
    const offer = this.offer;
    if (offer === null) return;
    this.button.disabled = true;
    if (offer.action === "reconnect") {
      this.onReconnect(offer.session);
      return;
    }
    this.button.textContent = "Starting…";
    this.onStart(offer.session, (problem) => {
      // Refused: the CLI's own words, and the same offer again.
      this.show({ ...offer, said: problem }, "Start orchestrator did not start a session.");
    });
  }
}

/**
 * One of the pane's two screens: an xterm of its own, in a box of its own.
 *
 * TWO xterms and not one (KYO-293). The pane has two ptys -- the tmux client and the
 * bare login shell -- and each owes the reader its own screen and its own scrollback:
 * the whole point of the bare shell is that a command left running in it keeps running
 * and keeps printing while the session is on show, and a single xterm fed by both would
 * interleave them into one unreadable buffer. Shown one at a time with `hidden`, so the
 * one that is off screen keeps every row it has.
 */
class PaneScreenView {
  readonly box: HTMLElement;
  readonly term: Terminal;
  private readonly fit: FitAddon;

  constructor(readonly surface: PaneSurface) {
    this.box = el("div", {
      class: "term-surface",
      data: { region: "terminal-surface", terminalSurface: surface },
    });
    this.term = new Terminal({
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      fontSize: 12,
      lineHeight: 1.25,
      cursorBlink: true,
      theme: { ...THEME },
      scrollback: 5000,
      allowProposedApi: false,
    });
    this.fit = new FitAddon();
    this.term.loadAddon(this.fit);
  }

  open(): void {
    this.term.open(this.box);
    this.measure();
  }

  /** Fit to the box, and answer with the size -- null when there is no layout yet. */
  measure(): { cols: number; rows: number } | null {
    if (this.box.clientWidth === 0 || this.box.clientHeight === 0) return null;
    try {
      this.fit.fit();
    } catch {
      return null;
    }
    return { cols: this.term.cols, rows: this.term.rows };
  }

  set shown(value: boolean) {
    this.box.hidden = !value;
  }

  dispose(): void {
    this.term.dispose();
  }
}

async function attach(
  host: HTMLElement,
  screen: HTMLElement,
  context: TabContext,
): Promise<Attached> {
  const views: Record<PaneSurface, PaneScreenView> = {
    client: new PaneScreenView("client"),
    bare: new PaneScreenView("bare"),
  };
  screen.replaceChildren(views.client.box, views.bare.box);
  // Both open, both hidden: xterm needs a laid-out element to measure a cell, and a
  // screen first opened at the moment it is shown would flash at 80x24 on the way in.
  // `showing` below picks which one is not hidden, before anything is painted.
  views.client.shown = false;
  views.bare.shown = false;
  views.client.open();
  views.bare.open();

  // Registered before the ptys open, so the first screen a re-attached tmux session
  // paints is already linkified. On both screens: a `kyosei://` URL is as clickable in
  // the bare shell's output as in the session's.
  const providers: IDisposable[] = [views.client, views.bare].map((view) =>
    view.term.registerLinkProvider({
      provideLinks(row, callback) {
        const line = view.term.buffer.active.getLine(row - 1);
        if (line === undefined) {
          callback(undefined);
          return;
        }
        const links = linksIn(line.translateToString(true), row, (url, newTab) =>
          context.followLink(url, { newTab }),
        );
        // What the last asked-about row held. Written to the DOM because it is the only
        // observable difference between "xterm never asked" and "xterm asked and the
        // click went nowhere" -- two failures with nothing else to tell them apart, and
        // the check for c7 has to be able to say which it met.
        host.dataset.terminalLinks = String(links.length);
        callback(links.length === 0 ? undefined : links);
      },
    }),
  );

  const first = views.bare.measure() ?? { cols: 80, rows: 24 };
  const opened = await context.bridge.openTerminal(first);
  if (!opened.ok && opened.remote !== undefined) {
    // KYO-370.c4, narrowed by KYO-474: this window is on a dev box's project and has no
    // SSH host to reach it with. Said in prose where the pane would be, with where to
    // set one, not as a red line in an empty terminal.
    for (const provider of providers) provider.dispose();
    views.client.dispose();
    views.bare.dispose();
    host.dataset.terminal = "remote";
    host.replaceChildren(
      el("div", { class: "empty term-remote", data: { region: "terminal-remote" } }, [
        el("div", { class: "term-remote-title", text: "No terminal here" }),
        el("div", {
          class: "term-remote-why",
          text: opened.problem ?? "this dev box has no SSH host to reach it with",
        }),
      ]),
    );
    return { dispose: () => undefined };
  }
  if (!opened.ok) {
    // Law 6 as it applies to a pane: a terminal that silently shows nothing is
    // indistinguishable from a terminal waiting for input.
    views.bare.shown = true;
    views.bare.term.writeln(
      `\x1b[31mkyosei: ${opened.problem ?? "the shell could not be started"}\x1b[0m`,
    );
    host.dataset.terminal = "failed";
    return {
      dispose: () => {
        for (const provider of providers) provider.dispose();
        views.client.dispose();
        views.bare.dispose();
      },
    };
  }
  host.dataset.terminal = "live";
  host.dataset.terminalCwd = opened.cwd ?? "";
  host.dataset.terminalMode = opened.mode;
  host.dataset.terminalSession = opened.session ?? "";
  // Which pty this screen is a view of (KYO-124). Two windows showing one session and
  // two windows holding one each are indistinguishable from outside without it, and
  // "the same session, moved" is the whole promise of moving the tab to a window.
  host.dataset.terminalPty = opened.sessionId ?? "";
  host.dataset.terminalProjectSession = opened.projectSession ?? "";
  host.dataset.terminalSocket = opened.socket ?? "";
  // Where the session lives: this machine's controller server, or a dev box's over the
  // person's own SSH (KYO-474).
  host.dataset.terminalWhere = opened.where ?? "local";
  host.dataset.terminalSshHost = opened.sshHost ?? "";

  /**
   * Put one of the two screens on show.
   *
   * Three acts, and the main process performs two of them: the hidden screen has no
   * layout, so it cannot be measured while it is hidden and cannot be resized while it
   * is unmeasured -- it comes back at whatever size it was hidden at, which after a
   * window resize is wrong. `showTerminalSurface` is that size arriving, and it is also
   * what makes a tmux client REPAINT (tmux draws on SIGWINCH, KYO-258).
   */
  let showing: PaneSurface = opened.showing;
  const show = (surface: PaneSurface): void => {
    showing = surface;
    views.client.shown = surface === "client";
    views.bare.shown = surface === "bare";
    host.dataset.terminalSurface = surface;
    const size = views[surface].measure() ?? { cols: 80, rows: 24 };
    void context.bridge.showTerminalSurface({ surface, ...size });
    views[surface].term.focus();
  };
  show(opened.showing);

  // ------------------------------------------------- the pills (KYO-283, KYO-293)
  //
  // Re-read at three moments, and the three are the criterion's: when the pane opens,
  // when its attachment changes -- which is the moment the reader caused, so waiting a
  // tick for it would look broken -- and on the cadence this app already polls the
  // board with. The interval is cleared by `dispose`, so a closed tab stops asking; a
  // pane that kept polling after its tab went would be one `list-sessions` per five
  // seconds forever.
  let refreshing = false;
  const refresh = async (): Promise<void> => {
    if (refreshing) return;
    refreshing = true;
    try {
      const view = await context.bridge.terminalSessions();
      strip.draw(view);
      host.dataset.terminalMode = view.attached === null || showing === "bare" ? "bare" : "tmux";
      host.dataset.terminalSession = showing === "bare" ? "" : (view.attached ?? "");
    } catch {
      // The window went away mid-question; there is nothing left to draw on.
    } finally {
      refreshing = false;
    }
  };
  const acted = (outcome: TerminalAttachOutcome): void => {
    strip.say(outcome.notice, outcome.mode, outcome.session);
    offers.show(outcome.offer ?? null, outcome.notice);
    host.dataset.terminalAction = outcome.action;
    host.dataset.terminalMode = outcome.mode;
    host.dataset.terminalSession = outcome.mode === "tmux" ? (outcome.session ?? "") : "";
    show(outcome.surface);
    void refresh();
  };
  const sizeNow = () => views[showing].measure() ?? { cols: 80, rows: 24 };
  const strip = new SessionStrip(
    (name) => void context.bridge.attachTerminal(name, sizeNow()).then(acted),
    () => void context.bridge.detachTerminal(sizeNow()).then(acted),
  );
  const offers = new OfferPanel(
    (session) => void context.bridge.attachTerminal(session, sizeNow()).then(acted),
    (session, refused) => {
      void context.bridge
        .startController()
        .then((state) => {
          if (state.status.running) {
            return context.bridge.attachTerminal(session, sizeNow()).then(acted);
          }
          refused(state.status.problem ?? state.status.why);
          return undefined;
        })
        .catch((error: unknown) => refused(String(error)));
    },
  );
  if (opened.where === "ssh") strip.overSsh();
  host.insertBefore(strip.root, screen);
  host.insertBefore(strip.statusLine(opened), screen);
  host.insertBefore(offers.root, screen);
  offers.show(opened.offer ?? null, opened.notice);
  // The row the main process saw at the instant the pane opened, drawn now rather than
  // one poll later: c1 asks for the project's pill highlighted from the first paint.
  if (opened.sessions !== null) strip.draw(opened.sessions);
  void refresh();
  const ticker = setInterval(() => void refresh(), TERMINAL_POLL_MS);

  const offData = context.bridge.onTerminalData(({ surface, data }) => {
    views[surface].term.write(data);
  });
  const offExit = context.bridge.onTerminalExit(({ surface, exitCode }) => {
    // Only the bare shell's exit is the reader ending something. The client's exit is a
    // detach -- theirs, or the session's end -- and `onTerminalFellBack` is the message
    // that says which, after the main process has asked the server.
    if (surface !== "bare") return;
    views.bare.term.writeln(`\r\n\x1b[90m[shell exited with ${exitCode}]\x1b[0m`);
    host.dataset.terminal = "exited";
  });
  const offFellBack = context.bridge.onTerminalFellBack(acted);
  const inputs = [views.client, views.bare].map((view) =>
    view.term.onData((data) => context.bridge.sendTerminalInput({ surface: view.surface, data })),
  );

  const observer = new ResizeObserver(() => {
    // A hidden tab has no size, and fitting to it would tell the pty its screen is one
    // column wide -- which a tmux session shared with another client would resize
    // EVERYONE to. So "hidden" has to mean "stop measuring", not "measure zero". The
    // hidden SURFACE is the same case one level down, and `measure` answers null for
    // both: what brings it back to the right size is `show` above.
    const size = views[showing].measure();
    if (size === null) return;
    context.bridge.resizeTerminal({ surface: showing, ...size });
  });
  observer.observe(screen);

  return {
    dispose() {
      clearInterval(ticker);
      observer.disconnect();
      for (const provider of providers) provider.dispose();
      for (const input of inputs) input.dispose();
      offData();
      offExit();
      offFellBack();
      views.client.dispose();
      views.bare.dispose();
    },
  };
}

const kind: TabKind = {
  kind: "terminal",
  label: "Terminal",
  singleton: true,
  home: [
    {
      order: 30,
      label: "Terminal",
      detail: "This project's orchestrator session -- here, or on its dev box over SSH",
      icon: "terminal",
    },
  ],
  open(context: TabContext): TabInstance {
    let attached: Attached | null = null;
    let offProject: (() => void) | null = null;
    let closed = false;

    /**
     * Give the tab a shell, and give it another one whenever the project moves.
     *
     * The restart is not optional politeness (KYO-175.c7). `startSession` captures the
     * project root as the pty's `cwd` and derives the tmux session name from the
     * project's home, both once, at open -- and `projectMoved` ends this app's ptys.
     * Without this the tab would sit at `[shell exited]`, and before the main-process
     * half existed it would have gone on claiming to be in a project it was not in.
     */
    const start = (host: HTMLElement): void => {
      const screen = el("div", { class: "term-screen", data: { region: "terminal-screen" } });
      host.replaceChildren(screen);
      host.dataset.terminal = "starting";
      void attach(host, screen, context)
        .then((handle) => {
          if (closed) {
            handle.dispose();
            return;
          }
          attached = handle;
        })
        .catch((error: unknown) => {
          host.dataset.terminal = "failed";
          host.replaceChildren(el("div", { class: "empty", text: `kyosei: ${String(error)}` }));
        });
    };

    return {
      title: () => "Terminal",
      render(host) {
        host.classList.add("term-host");
        start(host);
        offProject?.();
        offProject = context.bridge.onProjectChanged(() => {
          attached?.dispose();
          attached = null;
          start(host);
        });
      },
      close() {
        closed = true;
        offProject?.();
        offProject = null;
        attached?.dispose();
        attached = null;
      },
    };
  },
};

export default kind;
