// The only thing the renderer can reach. Context isolation is on and the
// renderer is sandboxed, so every capability the shell has is listed here.

import { contextBridge, ipcRenderer } from "electron";
import {
  type ActivityView,
  type AppState,
  type AwaitingHuman,
  CHANNEL,
  type ClaudePane,
  type ControllerCommandOutcome,
  type ControllerState,
  type IdeaEdit,
  type KyoseiBridge,
  type LayoutSave,
  type LayoutState,
  type LinkDelivery,
  type LinkOrigin,
  type NewProjectInput,
  type PaneBounds,
  type PopoutContext,
  type PopoutPane,
  type ProjectFacts,
  type RecentForgetInput,
  type RemoteConnectInput,
  type RemoteFacts,
  type RemoteOpenInput,
  type ScratchpadSave,
  type ShellCommand,
  type StoreChange,
  type TabDragEndOutcome,
  type TabDragPoint,
  type TabDragStart,
  type TabDragStartOutcome,
  type TabHandoff,
  type TerminalAttachOutcome,
  type TerminalChunk,
  type TerminalExitInfo,
  type TerminalInput,
  type TerminalResize,
  type TerminalSize,
  type TreeGrouping,
  type TreeScope,
} from "../shared/protocol";

// The terminal's two inbound channels are listened to from the moment this preload
// runs, and not from the moment the pane asks for them (KYO-124).
//
// A view of a session is given what the session has already printed the instant it
// attaches, in the same synchronous step -- which is BEFORE `attachTerminal` has an
// xterm.js to write it into, because the call that attaches is the call that returns
// the pane its first screen. Anything sent in that window would land on a channel
// nobody was listening to yet and be gone: a popped-out terminal would open blank
// and stay blank until the shell next printed something, which at an idle prompt is
// never.
//
// So the listener is always on and the bytes wait here instead, in order, until the
// pane says where to put them. A handoff rather than a buffer with a policy: the
// queue is drained by the first subscription and never refilled while one is live.

/**
 * How many chunks may wait here for a pane that has not subscribed yet.
 *
 * A bound rather than an open queue, because the case it has to survive is a pane
 * whose `attachTerminal` threw before it subscribed: the shell behind it is alive
 * and may print forever, and a window that failed to draw a terminal must not also
 * become a slow memory leak. The handoff itself needs one or two entries.
 */
const EARLY_CHUNKS = 512;

const earlyData: TerminalChunk[] = [];
const earlyExits: TerminalExitInfo[] = [];
let terminalData: ((chunk: TerminalChunk) => void) | null = null;
let terminalExit: ((info: TerminalExitInfo) => void) | null = null;
let terminalFellBack: ((outcome: TerminalAttachOutcome) => void) | null = null;

ipcRenderer.on(CHANNEL.termData, (_event, chunk: TerminalChunk) => {
  if (terminalData !== null) {
    terminalData(chunk);
    return;
  }
  earlyData.push(chunk);
  if (earlyData.length > EARLY_CHUNKS) earlyData.shift();
});

ipcRenderer.on(CHANNEL.termExit, (_event, info: TerminalExitInfo) => {
  if (terminalExit === null) earlyExits.push(info);
  else terminalExit(info);
});

// Not buffered, and the difference is the message: a fall-back the pane never heard
// about is one line of status missing from a screen that still shows the right thing,
// while a chunk of output that never arrived is a hole in the reader's scrollback.
ipcRenderer.on(CHANNEL.termFellBack, (_event, outcome: TerminalAttachOutcome) => {
  terminalFellBack?.(outcome);
});

const bridge: KyoseiBridge = {
  getState: () => ipcRenderer.invoke(CHANNEL.hello),
  openWindow: (root?: string, remote?: RemoteOpenInput) =>
    ipcRenderer.invoke(CHANNEL.openWindow, root ?? null, remote ?? null),
  chooseProjectWindow: () => ipcRenderer.invoke(CHANNEL.windowChoose),
  focusWindow: (id: number) => ipcRenderer.invoke(CHANNEL.focusWindow, id),
  popOut: (pane: PopoutPane, context?: PopoutContext) =>
    ipcRenderer.invoke(CHANNEL.popOut, pane, context ?? {}),
  onState: (listener: (state: AppState) => void) => {
    const handler = (_event: unknown, state: AppState) => listener(state);
    ipcRenderer.on(CHANNEL.state, handler);
    return () => {
      ipcRenderer.off(CHANNEL.state, handler);
    };
  },

  // The application menu (KYO-246). Accelerators belong to the main process and the
  // workspace belongs to this renderer, so a menu item is a word sent here and acted
  // on in `shell.ts`. Nothing in this renderer can make the main process send one.
  onCommand: (listener: (command: ShellCommand) => void) => {
    const handler = (_event: unknown, command: ShellCommand) => listener(command);
    ipcRenderer.on(CHANNEL.command, handler);
    return () => {
      ipcRenderer.off(CHANNEL.command, handler);
    };
  },

  // A tab torn off its strip (KYO-246). The renderer reports the pointer in screen
  // coordinates; only the main process can move a window or see which one is under it.
  startTabDrag: (start: TabDragStart): Promise<TabDragStartOutcome> =>
    ipcRenderer.invoke(CHANNEL.tabDragStart, start),
  moveTabDrag: (point: TabDragPoint) => ipcRenderer.send(CHANNEL.tabDragMove, point),
  endTabDrag: (point: TabDragPoint): Promise<TabDragEndOutcome> =>
    ipcRenderer.invoke(CHANNEL.tabDragEnd, point),
  onTabDragHover: (listener: (at: TabDragPoint | null) => void) => {
    const handler = (_event: unknown, at: TabDragPoint | null) => listener(at);
    ipcRenderer.on(CHANNEL.tabDragHover, handler);
    return () => {
      ipcRenderer.off(CHANNEL.tabDragHover, handler);
    };
  },
  onTabReceived: (listener: (handoff: TabHandoff) => void) => {
    const handler = (_event: unknown, handoff: TabHandoff) => listener(handoff);
    ipcRenderer.on(CHANNEL.tabReceive, handler);
    return () => {
      ipcRenderer.off(CHANNEL.tabReceive, handler);
    };
  },

  // Deep links (KYO-230). One subscription and no request, which is the boundary:
  // there is no method here that takes a URL, so a renderer cannot ask the app to
  // follow one. The main process resolves every link -- from the OS, from a launch
  // argument, from a click inside the claude view -- and tells whoever is listening
  // what to open, or why nothing was opened.
  onOpenTarget: (listener: (delivery: LinkDelivery) => void) => {
    const handler = (_event: unknown, delivery: LinkDelivery) => listener(delivery);
    ipcRenderer.on(CHANNEL.openTarget, handler);
    return () => {
      ipcRenderer.off(CHANNEL.openTarget, handler);
    };
  },

  // KYO-232.c7. The one method here that takes a URL, and it is a `send` returning
  // nothing on purpose: the renderer hands over a string and learns what came of it
  // through `onOpenTarget` above, which is the same subscription a link clicked in the
  // claude view arrives on. Nothing navigates here, and nothing can: `followLink` in
  // `main/links.ts` refuses every scheme but `kyosei`.
  followLink: (url: string, origin?: LinkOrigin) =>
    ipcRenderer.send(CHANNEL.followLink, url, origin ?? null),

  // Layout memory per display configuration (KYO-232.c3). The key is the attached
  // screens' identities and it is computed in the main process, because `screen` is a
  // main-process module: a renderer cannot see which monitors are plugged in, which is
  // exactly why this is a seam and not a `localStorage` call.
  getLayout: (): Promise<LayoutState> => ipcRenderer.invoke(CHANNEL.layoutLoad),
  saveLayout: (save: LayoutSave) => ipcRenderer.send(CHANNEL.layoutSave, save),
  onLayoutChanged: (listener: (state: LayoutState) => void) => {
    const handler = (_event: unknown, state: LayoutState) => listener(state);
    ipcRenderer.on(CHANNEL.layoutChanged, handler);
    return () => {
      ipcRenderer.off(CHANNEL.layoutChanged, handler);
    };
  },

  // The claude.ai pane (KYO-186). Four requests and one subscription, and what is
  // absent is the boundary: there is no method here that takes a URL. The renderer
  // says WHERE to draw the view and can ask for it to be resolved again; which page
  // a logged-in session navigates to is decided in the main process, from the
  // store's own session file. A renderer that could name the page would be a
  // renderer that could point an authenticated view at anything.
  showClaude: (bounds: PaneBounds): Promise<ClaudePane> =>
    ipcRenderer.invoke(CHANNEL.claudeShow, bounds),
  setClaudeBounds: (bounds: PaneBounds) => ipcRenderer.send(CHANNEL.claudeBounds, bounds),
  hideClaude: () => ipcRenderer.invoke(CHANNEL.claudeHide),
  reloadClaude: (): Promise<ClaudePane> => ipcRenderer.invoke(CHANNEL.claudeReload),
  snapshotClaude: (): Promise<string | null> => ipcRenderer.invoke(CHANNEL.claudeSnapshot),
  uncoverClaude: (bounds: PaneBounds): Promise<ClaudePane | null> =>
    ipcRenderer.invoke(CHANNEL.claudeUncover, bounds),
  claimFocus: () => ipcRenderer.send(CHANNEL.claimFocus),
  shellBooted: () => ipcRenderer.send(CHANNEL.shellBooted),
  onTabDragShown: (listener: () => void) => {
    const handler = () => listener();
    ipcRenderer.on(CHANNEL.tabDragShown, handler);
    return () => {
      ipcRenderer.off(CHANNEL.tabDragShown, handler);
    };
  },
  onWindowBlur: (listener: () => void) => {
    const handler = () => listener();
    ipcRenderer.on(CHANNEL.windowBlur, handler);
    return () => {
      ipcRenderer.off(CHANNEL.windowBlur, handler);
    };
  },
  onClaudeState: (listener: (state: ClaudePane) => void) => {
    const handler = (_event: unknown, state: ClaudePane) => listener(state);
    ipcRenderer.on(CHANNEL.claudeState, handler);
    return () => {
      ipcRenderer.off(CHANNEL.claudeState, handler);
    };
  },
  // The controller row (KYO-383, KYO-460). Status and Start carry nothing: what runs is
  // `kyo controller` on the machine that holds the project, with its own command.
  controllerStatus: (): Promise<ControllerState> => ipcRenderer.invoke(CHANNEL.controllerStatus),
  startController: (): Promise<ControllerState> => ipcRenderer.invoke(CHANNEL.controllerStart),
  setControllerCommand: (command: string): Promise<ControllerCommandOutcome> =>
    ipcRenderer.invoke(CHANNEL.controllerCommand, command),

  // The live seams. Every one of them is a request the main process answers: the
  // renderer gets no filesystem, no subprocess and no database handle of its own,
  // which is what keeps "reads open the store directly" a statement about one
  // process rather than about the app in general.
  getFacts: () => ipcRenderer.invoke(CHANNEL.facts),
  getCurrency: () => ipcRenderer.invoke(CHANNEL.currency),

  // The project switcher (KYO-175). Two requests and one subscription, and none of
  // them writes to any store. Which project the app is reading is not asked for
  // here: `getFacts` carries it, and re-probes it on every call (c3).
  //
  // `openProject` is the only method on this object that takes an absolute path,
  // which is worth stating beside the ideas comment below rather than leaving to be
  // noticed: that comment's claim is about WRITES, and it is unchanged. This method
  // names a directory to RUN `kyo` in; the main process refuses it unless a Kyosei
  // project is already configured at or above it, and nothing it reaches can mutate
  // a store. There is deliberately no counterpart that migrates, rebuilds or repairs
  // the store that was opened -- KYO-79 decision 5 decided against exactly that, and
  // the absence of a method here is the mechanism behind it as the renderer can
  // observe it, not a description of one.
  chooseProject: () => ipcRenderer.invoke(CHANNEL.projectChoose),
  openProject: (root: string) => ipcRenderer.invoke(CHANNEL.projectOpen, root),
  // A dev box's project (KYO-370): the same shape as `openProject` -- a request to
  // move THIS window, refused with a sentence or answered with the facts -- and the
  // same boundary: an address goes in (KYO-459) -- no password, no token, nothing a
  // renderer could leak.
  connectRemote: (input: RemoteConnectInput) => ipcRenderer.invoke(CHANNEL.remoteConnect, input),
  openRemote: (input: RemoteOpenInput) => ipcRenderer.invoke(CHANNEL.remoteOpen, input),
  forgetRecent: (which: RecentForgetInput | null) =>
    ipcRenderer.invoke(CHANNEL.recentsForget, which),
  probeRemote: (input: RemoteConnectInput) => ipcRenderer.invoke(CHANNEL.remoteProbe, input),
  // New project (KYO-460): commands the main process runs on a machine, this one or a
  // dev box over the person's SSH. The renderer names the machine and a path; which
  // commands run is the main process's, and none of them takes text from here but
  // the path they act on.
  newProjectServers: () => ipcRenderer.invoke(CHANNEL.newProjectServers),
  listFolder: (server: RemoteFacts | null, path: string | null) =>
    ipcRenderer.invoke(CHANNEL.newProjectList, server, path),
  makeFolder: (server: RemoteFacts | null, path: string) =>
    ipcRenderer.invoke(CHANNEL.newProjectMkdir, server, path),
  createProject: (input: NewProjectInput) => ipcRenderer.invoke(CHANNEL.newProjectCreate, input),
  setSshHost: (server: RemoteFacts, sshHost: string) =>
    ipcRenderer.invoke(CHANNEL.sshHostSet, server, sshHost),
  onProjectChanged: (listener: (project: ProjectFacts) => void) => {
    const handler = (_event: unknown, project: ProjectFacts) => listener(project);
    ipcRenderer.on(CHANNEL.projectChanged, handler);
    return () => {
      ipcRenderer.off(CHANNEL.projectChanged, handler);
    };
  },

  // Records mode (KYO-112). Six reads and no write: this object is the complete
  // list of what a renderer in records mode can ask the world to do, so the
  // absence of a write here is the mechanism behind "read-only to a human", not a
  // description of one.
  getRecordTree: (grouping: TreeGrouping, scope?: TreeScope) =>
    ipcRenderer.invoke(CHANNEL.recordsTree, grouping, scope ?? "everything"),
  getBases: () => ipcRenderer.invoke(CHANNEL.recordsBases),
  runBase: (name: string) => ipcRenderer.invoke(CHANNEL.recordsRunBase, name),
  showRecord: (id: string) => ipcRenderer.invoke(CHANNEL.recordsShow, id),
  getBriefs: () => ipcRenderer.invoke(CHANNEL.recordsBriefs),
  getBrief: (id: string) => ipcRenderer.invoke(CHANNEL.recordsBrief, id),
  getLatestShiftNote: () => ipcRenderer.invoke(CHANNEL.latestShiftNote),
  getShiftNotes: () => ipcRenderer.invoke(CHANNEL.shiftNotes),

  // The v3 read model (KYO-231). Four reads and one subscription, and -- like every
  // block above -- no counterpart that writes. What each one returns is already
  // shaped for a tab to render: the obligation collapse is the CLI's own, the classes
  // on an activity row are the brief's, and a resolved link's anchor kind is
  // `links.ts`'s. A tab that re-derived any of them would be the second answer law 3
  // exists to prevent, one process further out.
  getRecordDocument: (id: string) => ipcRenderer.invoke(CHANNEL.recordDocument, id),
  getAwaitingHuman: () => ipcRenderer.invoke(CHANNEL.awaitingHuman),
  getRecordsList: (options?: { query?: string; base?: string }) =>
    ipcRenderer.invoke(CHANNEL.recordsList, options ?? {}),
  getActivity: (options?: { finished?: number }) =>
    ipcRenderer.invoke(CHANNEL.activity, options ?? {}),
  getLatestBrief: () => ipcRenderer.invoke(CHANNEL.latestBrief),
  onActivityChanged: (listener: (view: ActivityView) => void) => {
    const handler = (_event: unknown, view: ActivityView) => listener(view);
    ipcRenderer.on(CHANNEL.activityChanged, handler);
    return () => {
      ipcRenderer.off(CHANNEL.activityChanged, handler);
    };
  },
  onAwaitingChanged: (listener: (view: AwaitingHuman) => void) => {
    const handler = (_event: unknown, view: AwaitingHuman) => listener(view);
    ipcRenderer.on(CHANNEL.awaitingChanged, handler);
    return () => {
      ipcRenderer.off(CHANNEL.awaitingChanged, handler);
    };
  },
  // Somebody else wrote the store, or `ideas/` (KYO-310). A subscription and not a
  // read: the renderer cannot watch a filesystem from inside the sandbox, and there
  // is nothing here it could ask for instead -- which is why the list it is showing
  // used to be a snapshot of the moment it was opened.
  onStoreChanged: (listener: (change: StoreChange) => void) => {
    const handler = (_event: unknown, change: StoreChange) => listener(change);
    ipcRenderer.on(CHANNEL.storeChanged, handler);
    return () => {
      ipcRenderer.off(CHANNEL.storeChanged, handler);
    };
  },

  // Ideas mode (KYO-114). The first entries on this object that write, and the
  // shape of the two that do is the boundary: `saveScratchpad` takes TEXT (with the
  // version the tab edited it from, KYO-575) and no path (main knows the one file it means), and `saveIdea`/`createIdea` take a
  // path RELATIVE to `ideas/`. There is no method here that accepts an absolute
  // path, so the renderer has no vocabulary in which to ask for a write under
  // `kyosei/` — and `assertWritable` refuses one anyway, because a capability
  // surface that is safe only by what it cannot express is safe until somebody
  // adds a method.
  getScratchpad: () => ipcRenderer.invoke(CHANNEL.ideasScratchpad),
  saveScratchpad: (save: ScratchpadSave) => ipcRenderer.invoke(CHANNEL.ideasSaveScratchpad, save),
  getIdeaTree: () => ipcRenderer.invoke(CHANNEL.ideasTree),
  openIdea: (path: string) => ipcRenderer.invoke(CHANNEL.ideasOpen, path),
  saveIdea: (edit: IdeaEdit) => ipcRenderer.invoke(CHANNEL.ideasSave, edit),
  createIdea: (request: { folder: string; name: string; text: string }) =>
    ipcRenderer.invoke(CHANNEL.ideasCreate, request),
  mkdirIdeas: (request: { folder: string }) => ipcRenderer.invoke(CHANNEL.ideasMkdir, request),
  renameIdea: (request: { path: string; name: string }) =>
    ipcRenderer.invoke(CHANNEL.ideasRename, request),
  moveIdea: (request: { path: string; folder: string }) =>
    ipcRenderer.invoke(CHANNEL.ideasMove, request),
  deleteIdea: (request: { path: string }) => ipcRenderer.invoke(CHANNEL.ideasDelete, request),
  copyText: (text: string) => ipcRenderer.invoke(CHANNEL.copyText, text),
  getCompletions: () => ipcRenderer.invoke(CHANNEL.ideasCompletions),

  // Docs mode (KYO-139). Two reads, and the fact that this object stops here is the
  // mechanism behind "docs mode is view-only" as the renderer can observe it: there is
  // no method on `window.kyosei` that changes a document, so a pane cannot grow a save
  // button that works. KYO-136 decision 6 -- past the runtime seam.
  getDocTree: () => ipcRenderer.invoke(CHANNEL.docsTree),
  openDoc: (path: string) => ipcRenderer.invoke(CHANNEL.docsOpen, path),

  openTerminal: (size: TerminalSize) => ipcRenderer.invoke(CHANNEL.termOpen, size),
  sendTerminalInput: (input: TerminalInput) => ipcRenderer.send(CHANNEL.termInput, input),
  resizeTerminal: (resize: TerminalResize) => ipcRenderer.send(CHANNEL.termResize, resize),
  showTerminalSurface: (resize: TerminalResize) => ipcRenderer.invoke(CHANNEL.termShow, resize),
  onTerminalData: (listener: (chunk: TerminalChunk) => void) => {
    terminalData = listener;
    // Whatever arrived before the pane was ready for it, in the order it arrived.
    while (earlyData.length > 0) listener(earlyData.shift() as TerminalChunk);
    return () => {
      if (terminalData === listener) terminalData = null;
    };
  },
  onTerminalExit: (listener: (info: TerminalExitInfo) => void) => {
    terminalExit = listener;
    while (earlyExits.length > 0) listener(earlyExits.shift() as TerminalExitInfo);
    return () => {
      if (terminalExit === listener) terminalExit = null;
    };
  },
  onTerminalFellBack: (listener: (outcome: TerminalAttachOutcome) => void) => {
    terminalFellBack = listener;
    return () => {
      if (terminalFellBack === listener) terminalFellBack = null;
    };
  },

  // The session pills (KYO-283, KYO-293). Three members, and what is NOT here is the
  // boundary: no method on this object takes a tmux argument. The renderer asks for the
  // row, names a session it was handed, and asks for its own shell back -- so no
  // vocabulary exists here in which a pane could ask for a command against a tmux
  // server, whichever server that is.
  terminalSessions: () => ipcRenderer.invoke(CHANNEL.termSessions),
  attachTerminal: (session: string, size: TerminalSize) =>
    ipcRenderer.invoke(CHANNEL.termAttach, { session, ...size }),
  detachTerminal: (size: TerminalSize) => ipcRenderer.invoke(CHANNEL.termDetach, size),
};

contextBridge.exposeInMainWorld("kyosei", bridge);
