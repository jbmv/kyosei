// The window shell: the rail, the sidebar, the columns, and the four things that tie
// them to the main process (KYO-232).
//
//   layout memory       load the layout for the attached screens, save it when
//                       anything moves (c3)
//   the badges          the awaiting-human count and the running-dispatch count,
//                       pushed by main when they move (c6)
//   pop-out             a tab moved to a window of its own -- a whole shell window on
//                       the same project (KYO-246), or THE claude.ai window
//   deep links          a `kyosei://` delivery opens a tab in the column the link
//                       asked for (KYO-230)
//   menu commands       New Tab, Close Tab, Toggle Split and the rest, sent by the
//                       main process's menu and acted on here (KYO-246)
//   tabs handed over    a tab dropped on this window from another, or handed back
//                       by a transient window closing (KYO-246)

import type {
  AppState,
  AwaitingHuman,
  KyoCompatFacts,
  KyoseiBridge,
  LayoutSnapshot,
  LinkDelivery,
  LiveFacts,
  OfferedProject,
  PopoutPane,
  ProjectFacts,
  RemoteEntryFacts,
  RemoteFacts,
  ServeFacts,
  ShellCommand,
  TabHandoff,
} from "../../shared/protocol";
import {
  reprobeWhileDown,
  SERVE_REPROBE_MS,
  serveReprobeDue,
  serveStaleNotice,
} from "../../shared/protocol";
// How a row names a project and where it is served from (KYO-465.c2) -- and the status
// bar names this window's the same way (KYO-473.c5).
import { servedLabel } from "../../shared/served";
import { el } from "../dom";
import { icon } from "../icons";
import { onOverlayChange } from "../overlays";
import type { BadgeSource, TabContext } from "../tabs/kind";
import { tabKindNames } from "../tabs/registry";
import {
  buildColumns,
  type Columns,
  closeMenu,
  type MenuItem,
  showMenu,
  type TabRef,
} from "./columns";
import { openNewProjectDialog } from "./new-project-dialog";
import { buildRail, type RailItem, SIDEBAR_LISTS, type SidebarList } from "./rail";
import { openRemoteDialog } from "./remote-dialog";
import { buildSidebar, DEFAULT_SIDEBAR_WIDTH, type Sidebar } from "./sidebar";

/** How long the shell waits after a change before writing the layout down. */
const SAVE_AFTER_MS = 250;

/**
 * Which main-process pane kind carries a tab moved into a window of its own.
 *
 * Two of the kinds are backed by a scarce main-process object -- a pty, a
 * `WebContentsView` -- and `main.ts` keys the session hand-off and the redock off the
 * pane word. Everything else is drawn entirely in the renderer and is plain `tab`.
 */
function paneForKind(kind: string): PopoutPane {
  if (kind === "terminal") return "terminal";
  if (kind === "claude") return "claude";
  return "tab";
}

export interface Shell {
  root: HTMLElement;
  /** Everything the window learns about itself, redrawn. */
  render(state: AppState): void;
  start(): Promise<void>;
  dispose(): void;
}

export function buildShell(bridge: KyoseiBridge): Shell {
  const linkStrip = el("div", { class: "link-strip", data: { region: "link-strip" } });
  linkStrip.hidden = true;
  const storeLine = el("span", { class: "store-line", data: { region: "store-identity" } });
  const currencyLine = el("span", { class: "currency", data: { region: "currency" } });
  // KYO-473.c5: where this window's project is served from, always -- "KYO @ this
  // machine" or "KYO @ <host>:<port>", in the recents' own words (`servedLabel`).
  const servedLine = el("span", { class: "store-line", data: { region: "served-from" } });
  // KYO-449.c6: a kyo serve slow to answer says so here, and the window stays usable.
  const serveNote = el("span", {
    class: "store-line",
    data: { region: "serve-slow", state: "none" },
  });
  serveNote.style.display = "none";
  // KYO-550: a kyo serve running an older build than the one installed says so here --
  // a line, never an overlay, so the scratchpad and the records views stay usable.
  const staleNote = el("span", {
    class: "store-line",
    data: { region: "serve-stale", state: "none" },
  });
  staleNote.style.display = "none";
  // KYO-449.c1: the kyo this app runs, when it is not one this app reads.
  const kyoChip = el("button", {
    class: "window-chip",
    data: { region: "kyo-compat", state: "accepted", action: "kyo-compat" },
    attrs: { type: "button" },
    on: {
      click: () => {
        if (latestKyoCompat !== null) openKyoCompat(latestKyoCompat, null);
      },
    },
  });
  kyoChip.style.display = "none";
  const windowCluster = el("div", { class: "windows", data: { region: "windows" } });

  let saveTimer: number | null = null;
  let selectedList: SidebarList | null = null;
  /**
   * What the arrangement on screen is an arrangement OF: the project and the desk it
   * belongs to, learned from `getLayout` and from every `layoutChanged` push (KYO-334).
   *
   * Every save is stamped with these rather than left for the main process to infer
   * from the window's state when it lands, and `null` means "in between": the project
   * is moving and what is on screen belongs to neither side of it, so nothing is saved
   * until the new project's layout has been applied.
   */
  let layoutRoot: string | null = null;
  let layoutSignature: string | null = null;
  const unsubscribes: (() => void)[] = [];
  /**
   * What this window was opened to show (KYO-246): a layout seeded by the main
   * process for a window opened around one moved tab, or null to open on what the
   * attached screens remember. Known from the first `render`, before `start`.
   */
  let seed: LayoutSnapshot | null = null;
  /** True for a window whose layout is never the desk's (KYO-246). */
  let transient = false;

  /**
   * The switcher's own state, kept from the last `renderFacts` rather than re-asked
   * for on click: the menu has to open the instant the reader clicks, and `getFacts`
   * is a `kyo` subprocess (`rowsFor`'s own comment on the sidebar says the same thing
   * about that latency).
   */
  let latestRecents: OfferedProject[] = [];
  let latestProjectRoot: string | null = null;
  /** Dev boxes connected to before, and the one this window is on now (KYO-370). */
  let latestRemotes: RemoteEntryFacts[] = [];
  let latestRemote: { host: string; port: number } | null = null;

  /** A server as the connect dialog shows it: its address, as a person types it. */
  function addressOf(server: { host: string; port: number }): string {
    const loopback = /^(127\.|localhost$|::1$|\[::1\]$)/.test(server.host);
    if (loopback) return `${server.host}:${server.port}`;
    return server.port === 443 ? `https://${server.host}` : `https://${server.host}:${server.port}`;
  }

  function reportRefusal(choice: { refused: { root: string; problem: string } | null }): void {
    if (choice.refused !== null) {
      showStrip(`could not open ${choice.refused.root}: ${choice.refused.problem}`, "refusal");
    }
  }

  // ------------------------------------------- the recents, keyed by server (KYO-465)
  //
  // One project is (server, root): the same root on this machine and on a dev box is
  // two rows, and every row says where it is served from. Both menus that list them --
  // the rail's switcher and New Window's chooser -- build them here, so they cannot
  // disagree about what a recent is, and both carry the same way to forget one.

  function sameServerAs(a: RemoteFacts | null, b: RemoteFacts | null): boolean {
    if (a === null || b === null) return a === b;
    return a.host.toLowerCase() === b.host.toLowerCase() && a.port === b.port;
  }

  /** Is this the project this window is on -- the same root through the same server? */
  function isHere(root: string, server: RemoteFacts | null): boolean {
    return root === latestProjectRoot && sameServerAs(server, latestRemote);
  }

  /** The facts `forgetRecent` answers, taken as the menus' new lists. */
  function takeRecents(facts: ProjectFacts): void {
    latestRecents = facts.recents;
    latestRemotes = facts.remotes;
  }

  function forget(root: string, server: RemoteFacts | null): void {
    void bridge
      .forgetRecent({
        root,
        server: server === null ? null : { host: server.host, port: server.port },
      })
      .then(takeRecents);
  }

  /**
   * Every recent and remembered project but this window's own, as menu rows, each opening
   * through `open` and each removable. The recents first, in their order; then a dev
   * box's remembered projects not already among them. This machine's registry rows carry
   * no remove control: the CLI is that list's one writer (`kyo projects remove`, KYO-455).
   */
  function projectRows(open: (root: string, server: RemoteFacts | null) => void): {
    recents: MenuItem[];
    remotes: MenuItem[];
    removable: boolean;
  } {
    const recents: MenuItem[] = [];
    // "Clear" is offered whenever there is anything to forget -- the project open now
    // included, though it is never a row of its own.
    const removable = latestRecents.some((entry) => entry.recent) || latestRemotes.length > 0;
    for (const entry of latestRecents) {
      if (isHere(entry.root, entry.server)) continue;
      const label = servedLabel(entry.project, entry.root, entry.server);
      const server = entry.server;
      recents.push({
        label,
        tip: server === null ? entry.root : `${entry.root} on ${server.host}:${server.port}`,
        run: () => open(entry.root, server),
        ...(entry.recent
          ? {
              remove: {
                tip: `Remove ${label} from recents`,
                run: () => forget(entry.root, server),
              },
            }
          : {}),
      });
    }
    const remotes: MenuItem[] = [];
    for (const entry of latestRemotes) {
      const server = { host: entry.host, port: entry.port };
      if (isHere(entry.root, server)) continue;
      const listed = latestRecents.some(
        (held) => held.root === entry.root && sameServerAs(held.server, server),
      );
      if (listed) continue;
      const label = servedLabel(entry.project, entry.root, server);
      remotes.push({
        label,
        tip: `${entry.root} on ${entry.host}:${entry.port}`,
        run: () => open(entry.root, server),
        remove: { tip: `Remove ${label} from recents`, run: () => forget(entry.root, server) },
      });
    }
    return { recents, remotes, removable };
  }

  /** Forget every recent (KYO-465.c3); the project open now stays open. */
  function clearRecentsItem(): MenuItem {
    return {
      label: "Clear recent projects",
      tip: "Forget every recent project and remembered dev box project in this app",
      run: () => void bridge.forgetRecent(null).then(takeRecents),
    };
  }

  /**
   * The rail's project button: the account's own recents (KYO-175's own store, never
   * surfaced until now) plus the native picker, in one menu rather than the picker
   * alone -- a reader who works across a handful of projects was doing a full
   * directory-navigation dialog every time to get back to one they had open minutes
   * ago.
   */
  function onSwitchProject(event: MouseEvent): void {
    const rows = projectRows((root, server) => {
      flushBeforeLeaving();
      const opening =
        server === null
          ? bridge.openProject(root)
          : bridge.openRemote({ host: server.host, port: server.port, root });
      void opening.then(reportRefusal);
    });
    const items: MenuItem[] = [...rows.recents];
    if (rows.removable) items.push(clearRecentsItem());
    items.push({
      label: "Open other project…",
      run: () => {
        flushBeforeLeaving();
        void bridge.chooseProject().then(reportRefusal);
      },
    });
    items.push({ label: "New project…", run: () => openNewProject() });
    // A dev box's project (KYO-370): the ones connected to before, minus the one this
    // window is on, then the dialog for a new one. Below a separator because these
    // open a project on ANOTHER machine, and the label says so on every row.
    items.push({ separator: true });
    items.push(...rows.remotes);
    items.push({
      label: "Connect to a dev box…",
      run: () => {
        const last = latestRemote ?? latestRemotes[0] ?? null;
        openRemoteDialog(document.body, {
          bridge,
          initial: last === null ? {} : { address: addressOf(last) },
          onConnected: () => flushBeforeLeaving(),
          onCancel: () => undefined,
        });
      },
    });
    showMenu(event, items);
  }

  const columns: Columns = buildColumns({
    context: makeContext,
    onLayoutChanged: () => {
      scheduleSave();
      // KYO-278: the rows' open marks follow the tabs, not the other way round.
      sidebar.markOpen();
    },
    onPopOut: popOut,
    onTearOff: (ref, point, wholeWindow, grab) =>
      bridge.startTabDrag({ tab: ref, ...point, wholeWindow, grab }).then((outcome) => {
        if (outcome.refusal !== null) showStrip(outcome.refusal, "refusal");
        return outcome;
      }),
    onDragMove: (point) => bridge.moveTabDrag(point),
    onDragEnd: (point) => bridge.endTabDrag(point),
  });

  const sidebar: Sidebar = buildSidebar({
    bridge,
    columns,
    onLayoutChanged: scheduleSave,
    onOpenInWindow: (ref) =>
      void bridge.popOut(paneForKind(ref.kind), { tab: { kind: ref.kind, arg: ref.arg } }),
    // The head's collapse button (KYO-257) is the second click on the selected rail
    // item, so it leaves the rail in the same state that click does.
    onCollapse: () => {
      closeMenu();
      selectedList = null;
      sidebar.show(null);
      rail.setSelected(null);
      scheduleSave();
    },
  });

  const rail = buildRail({ onSelect: onRailSelect, onSwitchProject });

  /**
   * New project (KYO-460): a machine, a folder, then mkdir, kyo init and kyo controller
   * start there. The made project opens in a NEW window, so this one is left as it was.
   */
  function openNewProject(): void {
    // One dialog at a time: a second menu click while it is open does nothing.
    if (document.querySelector('[data-region="new-project-dialog"]') !== null) return;
    openNewProjectDialog(document.body, {
      bridge,
      onClose: () => undefined,
      onOpen: async (root, server) => {
        const choice = await bridge.openWindow(
          root,
          server === null ? undefined : { host: server.host, port: server.port, root },
        );
        return choice.refused === null ? null : choice.refused.problem;
      },
    });
  }

  /**
   * New Window (KYO-249): the project chooser, not a window. Every recent, this
   * window's own project, and the native picker; choosing opens a window ON that
   * project and changes nothing about this one.
   */
  function openWindowChooser(at: { clientX: number; clientY: number }): void {
    const rows = projectRows(
      (root, server) =>
        void bridge
          .openWindow(
            root,
            server === null ? undefined : { host: server.host, port: server.port, root },
          )
          .then(reportRefusal),
    );
    const items: MenuItem[] = [...rows.recents];
    if (latestProjectRoot !== null) {
      const here = latestRemote;
      const root = latestProjectRoot;
      items.unshift({
        label: `This project (${rail.projectLabel()}${here === null ? "" : ` @ ${here.host}`})`,
        tip: here === null ? root : `${root} on ${here.host}:${here.port}`,
        // On a dev box's project the new window opens on the same dev box (KYO-374.c1).
        run: () =>
          void bridge
            .openWindow(
              root,
              here === null ? undefined : { host: here.host, port: here.port, root },
            )
            .then(reportRefusal),
      });
    }
    // Remembered dev boxes (KYO-374.c1), each a window on that machine's project.
    items.push(...rows.remotes);
    if (rows.removable) items.push(clearRecentsItem());
    items.push({ separator: true });
    items.push({
      label: "Open other project…",
      run: () => void bridge.chooseProjectWindow().then(reportRefusal),
    });
    items.push({
      label: "Connect to a dev box…",
      run: () => {
        const last = latestRemote ?? latestRemotes[0] ?? null;
        openRemoteDialog(document.body, {
          bridge,
          newWindow: true,
          initial: last === null ? {} : { address: addressOf(last) },
          onConnected: () => undefined,
          onCancel: () => undefined,
        });
      },
    });
    showMenu(at, items);
  }

  // No title bar (KYO-253): the top row of the window is the rail's traffic-light
  // spacer, the sidebar's head and the columns' strips, so a torn-off tab lands at the
  // top edge the way Chrome's does. The app name, the store line and the window chips
  // live in the status bar, and the link strip sits just above it.
  const root = el("div", { class: "window", data: { region: "window" } }, [
    el("div", { class: "body", data: { region: "body" } }, [rail.root, sidebar.root, columns.root]),
    linkStrip,
    el("footer", { class: "statusbar", data: { region: "statusbar" } }, [
      el("span", { class: "app-name", data: { region: "app-name" }, text: "Kyosei" }),
      servedLine,
      storeLine,
      serveNote,
      staleNote,
      kyoChip,
      el("span", { class: "grow" }),
      currencyLine,
      windowCluster,
    ]),
  ]);

  // ------------------------------------------------------------ the tab context

  function makeContext(ref: TabRef, id: string): TabContext {
    const tabRef = ref;
    return {
      arg: ref.arg,
      bridge,
      openTab: (request) => columns.open(request),
      closeSelf: () => columns.closeById(id),
      navigate: (ref) => {
        if (columns.navigate(id, ref)) return;
        // This tab cannot become the thing (a protected kind): the browsing tab takes
        // it (KYO-281), rather than a new tab beside a terminal every time.
        columns.show(ref);
        // A home page that could not become the thing chosen (a singleton) has done
        // its job and goes, rather than staying behind the tab it opened.
        if (ref.kind !== "home" && tabRef.kind === "home") columns.closeById(id);
      },
      // The link is followed by the main process and comes back over `onOpenTarget`
      // with this origin on it, so `deliver` can show it in THIS tab (KYO-247).
      followLink: (url, options) =>
        bridge.followLink(url, { tabId: id, newTab: options?.newTab === true }),
      // The instance is the source of truth for its own title -- `TabInstance.title()`
      // -- so this asks the shell to re-ask, rather than being a second place the
      // string lives. A tab that loads its subject asynchronously calls it when the
      // answer lands.
      setTitle: () => columns.retitle(),
    };
  }

  // ------------------------------------------------------------ the rail

  function onRailSelect(item: RailItem): void {
    closeMenu();
    if (item.list !== undefined) {
      // "Clicking the selected icon again collapses it" (KYO-227).
      const next = selectedList === item.list ? null : item.list;
      selectedList = next;
      sidebar.show(next);
      rail.setSelected(next);
      scheduleSave();
      return;
    }
    if (item.opens !== undefined) columns.open(item.opens);
  }

  // ------------------------------------------------------------ pop-out

  /**
   * Move a tab into a window of its own (KYO-246).
   *
   * The main process opens the window -- a whole shell on this project, or the one
   * claude.ai window -- and this one closes its copy at once rather than waiting for
   * a state broadcast: the window is being opened to save the space this tab was
   * taking, and a column that goes on drawing it until an IPC round trip lands is a
   * visible flicker of the wrong layout. What comes back, and when, is the main
   * process's: a transient window hands its tabs to `onTabReceived` when it closes.
   *
   * The copy that leaves is closed BY ID (KYO-319). A record can be open in both
   * columns, and "close the tab showing this record" then closes whichever copy comes
   * first -- so popping the right-hand copy out used to close the left-hand one and
   * leave the right-hand one sitting in its strip, now duplicated into the new window.
   * The rule is that popping one copy out leaves the other open (KYO-319.n1), which
   * is what closing exactly the tab that was pointed at gives.
   */
  function popOut(ref: TabRef, tabId: string): void {
    void bridge.popOut(paneForKind(ref.kind), { tab: { kind: ref.kind, arg: ref.arg } });
    columns.closeById(tabId, { gaveAway: true });
  }

  /** Another window handed this one a tab: a drop, or a hand-back at close. */
  function onTabReceived(handoff: TabHandoff): void {
    columns.receive(handoff.tab, handoff.at);
  }

  // ------------------------------------------------------------ menu commands

  function onCommand(command: ShellCommand): void {
    closeMenu();
    switch (command) {
      case "new-tab":
        columns.newTab();
        return;
      case "close-tab":
        columns.closeActive();
        return;
      case "toggle-split":
        columns.toggleSplit();
        return;
      case "next-tab":
        columns.cycle(1);
        return;
      case "prev-tab":
        columns.cycle(-1);
        return;
      case "open-scratchpad":
        columns.open({ kind: "compose", arg: "scratchpad" });
        return;
      case "open-terminal":
        columns.open({ kind: "terminal", arg: "" });
        return;
      case "open-claude":
        columns.open({ kind: "claude", arg: "" });
        return;
      case "open-activity":
        // Activity is a sidebar list since KYO-251, so the menu shows the list.
        selectedList = "activity";
        sidebar.show("activity");
        rail.setSelected("activity");
        scheduleSave();
        return;
      case "open-brief":
        columns.open({ kind: "views", arg: "brief" });
        return;
      case "new-window": {
        // Anchored on the status bar's own "+", which is where a reader would click;
        // the menu clamps itself to the window, so it opens above the bar.
        const chip = windowCluster.querySelector<HTMLElement>('[data-action="open-window"]');
        const rect = chip?.getBoundingClientRect();
        openWindowChooser({ clientX: rect?.left ?? 40, clientY: rect?.top ?? 40 });
        return;
      }
      case "new-project":
        openNewProject();
        return;
      case "back":
        columns.go(-1);
        return;
      case "forward":
        columns.go(1);
        return;
    }
  }

  // ------------------------------------------------------------ deep links

  function showStrip(text: string, tone: string): void {
    linkStrip.replaceChildren(
      el("span", { class: "link-strip-text", text }),
      el("button", {
        class: "link-strip-close",
        tip: "Dismiss",
        on: {
          click: () => {
            linkStrip.hidden = true;
          },
        },
      }),
    );
    linkStrip.dataset.tone = tone;
    linkStrip.hidden = false;
  }

  /**
   * A delivery that arrived before the workspace existed, kept rather than dropped.
   *
   * The two halves of KYO-240.c1, and each one alone is a bug:
   *
   *   SUBSCRIBE EARLY. `deliverLaunchLink` (main/main.ts) sends the resolved target on
   *   the window's `ready-to-show`, and `ipcRenderer` DROPS an event that arrives with
   *   no listener on its channel. `start()` used to reach `bridge.onOpenTarget` only
   *   after `await bridge.getLayout()`, so on a loaded machine the link resolved, the
   *   main process printed that it had opened it, and no tab appeared -- observed once
   *   inside a full `npm run check` and diagnosed in `tests/record.spec.ts` (KYO-233.d1).
   *
   *   THEN HOLD IT. Subscribing above the awaits alone moves the bug rather than
   *   fixing it: `applyLayout` calls `columns.restore`, which CLOSES EVERY OPEN TAB
   *   before it rebuilds the saved ones, so a delivery that landed in the gap would
   *   open a tab the very next statement threw away -- the same missing tab, now with
   *   a race nobody could see in the log. So a delivery that arrives before the layout
   *   is on screen waits here and is replayed the moment it is.
   *
   * This is a queue and not a single slot because `open-url` can deliver more than one
   * URL (macOS hands over a multi-file open as several events), and the reader asked
   * for each of them.
   */
  let workspaceReady = false;
  const heldDeliveries: LinkDelivery[] = [];

  function onDelivery(delivery: LinkDelivery): void {
    if (!workspaceReady) {
      heldDeliveries.push(delivery);
      return;
    }
    deliver(delivery);
  }

  /** The workspace is on screen: replay whatever arrived while it was not. */
  function openWorkspaceToDeliveries(): void {
    workspaceReady = true;
    for (const delivery of heldDeliveries.splice(0, heldDeliveries.length)) deliver(delivery);
  }

  function deliver(delivery: LinkDelivery): void {
    if (!delivery.ok) {
      showStrip(delivery.refusal, "refusal");
      return;
    }
    const { target, column, url, origin, choice } = delivery.request;
    const ref: TabRef =
      target.kind === "record"
        ? { kind: "record", arg: target.id }
        : { kind: "views", arg: `${target.kind}:${target.id}` };
    // Followed from inside a tab, with no column asked for and no new tab asked for:
    // that tab shows it and keeps its history (KYO-247). Anything else opens a tab.
    const inPlace =
      origin !== undefined &&
      !origin.newTab &&
      column === "active" &&
      columns.navigate(origin.tabId, ref);
    // A link with no column and no new-tab ask follows the open policy (KYO-281): the
    // browsing tab shows it and is focused, never the terminal, claude or scratchpad.
    if (!inPlace) {
      if (column === "active" && origin?.newTab !== true) columns.show(ref);
      else columns.open({ ...ref, where: column, force: origin?.newTab === true });
    }
    // The "you were sent here" strip (KYO-227). Scrolling to and highlighting the
    // anchor is the record page's, which is stage 3 (KYO-233) -- so the strip names
    // the anchor rather than implying the page has moved to it.
    // When two connections serve the project, the strip says which one it went through
    // (KYO-463.c3).
    const through = choice === null || choice === undefined ? "" : ` (${choice})`;
    showStrip(
      target.anchor === null
        ? `Opened ${target.id} from ${url}${through}`
        : `Opened ${target.id} at ${target.anchorKind} ${target.anchor}, from ${url}${through}`,
      "sent-here",
    );
  }

  // ------------------------------------------------------------ layout memory

  function snapshotNow(): LayoutSnapshot {
    const columnState = columns.snapshot();
    return {
      columns: columnState.columns,
      activeColumn: columnState.activeColumn,
      sidebar: { item: selectedList, width: sidebar.width(), lists: sidebar.listStates() },
    };
  }

  function scheduleSave(): void {
    if (saveTimer !== null) window.clearTimeout(saveTimer);
    // Between `projectChanged` and the `layoutChanged` that follows it, what is on
    // screen is the OLD project's arrangement with its project-scoped tabs torn out --
    // an arrangement of nothing, which must not be written down as either project's
    // (KYO-334). The push that ends the switch turns saving back on.
    if (layoutRoot === null) return;
    saveTimer = window.setTimeout(saveNow, SAVE_AFTER_MS);
  }

  /** Write the arrangement down NOW, stamped with what it is an arrangement of. */
  function saveNow(): void {
    if (saveTimer !== null) window.clearTimeout(saveTimer);
    saveTimer = null;
    if (layoutRoot === null || layoutSignature === null) return;
    bridge.saveLayout({ root: layoutRoot, signature: layoutSignature, layout: snapshotNow() });
  }

  /** Forget a save that has not fired: what it describes is about to stop being true. */
  function dropPendingSave(): void {
    if (saveTimer !== null) window.clearTimeout(saveTimer);
    saveTimer = null;
  }

  /**
   * A switch this window is about to ask for: get the reader's arrangement of the
   * project they are LEAVING written down first (KYO-334).
   *
   * The main process moves `entry.projectRoot` the moment it accepts the switch, and a
   * save stamped with the old project after that is refused -- correctly, since nothing
   * over there can tell a pre-switch arrangement from a post-switch one. Flushing here,
   * before the request is sent, is what keeps the last 250ms of arranging: IPC from one
   * renderer is ordered, so this save is handled while the window is still on the old
   * project. It covers the switches the reader starts in this window; a switch that
   * arrives any other way falls back to the drop, which loses the arrangement rather
   * than misfiling it.
   */
  function flushBeforeLeaving(): void {
    saveNow();
  }

  function applyLayout(layout: LayoutSnapshot | null): void {
    if (layout === null) {
      // A configuration nobody has worked on yet: one column on the scratchpad, with
      // the records list showing. The app's two fast paths (KYO-227) are "get one
      // thought out" and "open the record the controller named", and this is both of
      // them one click away.
      columns.restore({
        columns: [
          { widthPercent: 100, tabs: [{ kind: "compose", arg: "scratchpad" }], activeTab: 0 },
        ],
        activeColumn: 0,
      });
      selectedList = "records";
      sidebar.setWidth(DEFAULT_SIDEBAR_WIDTH);
      sidebar.show("records");
      rail.setSelected("records");
      return;
    }
    columns.restore({ columns: layout.columns, activeColumn: layout.activeColumn });
    // A layout saved before KYO-248 may name the list by its old name.
    sidebar.setListStates(layout.sidebar.lists ?? {});
    const item = layout.sidebar.item === "briefs" ? "notes" : layout.sidebar.item;
    selectedList = (SIDEBAR_LISTS as readonly string[]).includes(item ?? "")
      ? (item as SidebarList)
      : null;
    sidebar.setWidth(layout.sidebar.width);
    sidebar.show(selectedList);
    rail.setSelected(selectedList);
  }

  // ------------------------------------------------ kyo serve is not running (KYO-459.c2)
  //
  // Every project is read through a kyo serve, and the app starts none itself (an app
  // that launches a server is an app that owns one, KYO-443). So when nothing answers
  // at the window's server, the window shows ONE state instead of a list of refusals:
  // what is not running, the two commands that start it, and Check again -- which asks
  // again in place, and draws the window as a first load draws it once it answers.
  // An overlay (`data-overlay`), so the claude view steps aside for it.
  //
  // KYO-449 adds the rest of what the window's server can be. Slow to answer (it took
  // the connection, c6) is not "not running": a note in the status bar, never the
  // overlay. Both are asked again every few seconds until the server answers (c8). A
  // server that answers as a release this app does not read, or as something that is
  // not Kyosei's kyo serve, is the compatibility overlay (c5), in KYO-440 c2's words.

  /** Asks again while the server is down or slow (KYO-449.c8); null when it is not. */
  let reprobe: { stop: () => void; readonly running: boolean } | null = null;

  function serveKey(serve: ServeFacts): string {
    if (!serve.running) return "down";
    if (serve.slow) return "slow";
    if (serve.compat !== null && serve.compat.state !== "accepted") {
      return `compat:${serve.compat.state}`;
    }
    return "ok";
  }

  /** One compatibility state's words, as regions named `<prefix>-title` and so on. */
  function compatWords(compat: KyoCompatFacts, prefix: string): HTMLElement[] {
    const parts: HTMLElement[] = [
      el("div", {
        class: "prompt-question",
        data: { region: `${prefix}-title` },
        text: compat.title,
      }),
      el("div", { class: "remote-hint", data: { region: `${prefix}-says` }, text: compat.says }),
    ];
    if (compat.command !== null) {
      parts.push(
        el("div", { class: "serve-down-command" }, [
          el("code", { data: { region: `${prefix}-command` }, text: compat.command }),
        ]),
      );
    }
    if (compat.route !== null) {
      parts.push(
        el("div", {
          class: "remote-hint",
          data: { region: `${prefix}-route` },
          text: compat.route,
        }),
      );
    }
    if (compat.after !== "") {
      parts.push(
        el("div", {
          class: "remote-hint",
          data: { region: `${prefix}-after` },
          text: compat.after,
        }),
      );
    }
    return parts;
  }

  function renderServe(serve: ServeFacts): void {
    const key = serveKey(serve);
    if (key === "down" || key === "slow") {
      if (reprobe === null || !reprobe.running) {
        reprobe = reprobeWhileDown({
          probe: () => bridge.getFacts(),
          stillDown: (facts) => !facts.serve.running || facts.serve.slow,
          onAnswer: renderFacts,
          everyMs: SERVE_REPROBE_MS,
          schedule: (run, ms) => window.setTimeout(run, ms),
          cancel: (handle) => window.clearTimeout(handle as number),
        });
      }
    } else {
      reprobe?.stop();
      reprobe = null;
    }
    const asked = new Date().toLocaleTimeString();
    // The slow note lives in the status bar: the window stays usable behind it.
    // `style.display`, not `hidden`: the status bar's classes set a display of their own.
    serveNote.style.display = key === "slow" ? "" : "none";
    serveNote.dataset.state = key === "slow" ? "slow" : "none";
    serveNote.textContent =
      key === "slow"
        ? `${serve.problem ?? `kyo serve at ${serve.address} is slow to answer`} (asked at ${asked}; asking again every ${SERVE_REPROBE_MS / 1000} s)`
        : "";
    const stale = serveStaleNotice(serve);
    staleNote.style.display = stale === null ? "none" : "";
    staleNote.dataset.state = stale === null ? "none" : "stale";
    staleNote.textContent = stale === null ? "" : `⚠ ${stale}`;
    if (stale === null) staleNote.removeAttribute("data-tip");
    else staleNote.setAttribute("data-tip", stale);
    const held = document.querySelector<HTMLElement>('[data-overlay="serve"]');
    const wanted = key === "ok" || key === "slow" ? null : key;
    if (held !== null && held.dataset.serveKey === wanted) {
      // The same state, asked again: say when, and keep what the reader is looking at.
      const state = held.querySelector<HTMLElement>('[data-region="serve-down-state"]');
      const check = held.querySelector<HTMLElement>('[data-region="serve-check-again"]');
      check?.removeAttribute("aria-disabled");
      if (state !== null) {
        state.dataset.state = "refused";
        state.textContent =
          key === "down"
            ? `Still nothing at ${serve.address} (asked at ${asked}).`
            : `Still the same at ${serve.address} (asked at ${asked}).`;
      }
      return;
    }
    held?.remove();
    if (wanted === null) {
      if (held !== null) {
        // Back: everything this window shows is read again, as on a project switch.
        sidebar.refresh();
        void bridge.getAwaitingHuman().then(renderAwaiting);
        void bridge.getActivity().then((view) => renderBadge("running-dispatches", view.running));
      }
      return;
    }
    const check = el("button", {
      class: "prompt-button prompt-ok",
      data: { region: "serve-check-again", action: "serve-check-again" },
      attrs: { type: "button" },
      text: "Check again",
    });
    const state = el("div", {
      class: "prompt-problem",
      data: { region: "serve-down-state", state: "none" },
    });
    let words: HTMLElement[];
    let region: string;
    if (key === "down") {
      region = "serve-down";
      const commands = serve.local
        ? serve.commands.map((command, index) =>
            el("div", { class: "serve-down-command" }, [
              el("code", { data: { region: "serve-down-command" }, text: command }),
              el("span", {
                class: "remote-hint",
                text:
                  index === 0
                    ? " — installs it to start at login, and starts it now"
                    : " — runs it in that terminal until you stop it",
              }),
            ]),
          )
        : [];
      words = [
        el("div", {
          class: "prompt-question",
          data: { region: "serve-down-title" },
          text: serve.local ? "kyo serve is not running" : `Nothing answered at ${serve.address}`,
        }),
        el("div", {
          class: "remote-hint",
          data: { region: "serve-down-why" },
          text: serve.local
            ? `This app reads every project through kyo serve, and nothing answered at ${serve.address}. Start it in a terminal on this machine:`
            : `This window's project is read through the kyo serve at ${serve.address}, and it did not answer: ${serve.problem ?? ""}`,
        }),
        ...commands,
        el("div", {
          class: "remote-hint",
          data: { region: "serve-down-reprobe" },
          text: `This window asks again every ${SERVE_REPROBE_MS / 1000} s, and clears this once it answers.`,
        }),
      ];
    } else {
      region = "serve-compat";
      words = serve.compat === null ? [] : compatWords(serve.compat, "serve-compat");
    }
    // KYO-473.c1: nothing answering here never blocks reaching ANOTHER machine's kyo
    // serve -- a laptop whose every project lives on a dev box runs no kyo serve of its
    // own. The connect dialog, opening the chosen project in a window of its own; this
    // one keeps asking its own server.
    const actions: HTMLElement[] = [];
    if (key === "down") {
      actions.push(
        el("button", {
          class: "prompt-button",
          data: { region: "serve-connect", action: "serve-connect" },
          attrs: { type: "button" },
          text: "Connect to a server…",
          on: {
            click: () => {
              const last = latestRemotes[0] ?? null;
              openRemoteDialog(document.body, {
                bridge,
                newWindow: true,
                initial: last === null ? {} : { address: addressOf(last) },
                onConnected: () => undefined,
                onCancel: () => undefined,
              });
            },
          },
        }),
      );
    }
    actions.push(check);
    const dialog = el(
      "div",
      { class: "prompt-dialog serve-down", attrs: { role: "alertdialog" } },
      [...words, state, el("div", { class: "prompt-actions" }, actions)],
    );
    const overlay = el(
      "div",
      {
        class: "prompt-overlay",
        data: {
          region,
          overlay: "serve",
          serveKey: key,
          state: serve.compat?.state ?? "not-running",
        },
      },
      [dialog],
    );
    check.addEventListener("click", () => {
      check.setAttribute("aria-disabled", "true");
      state.dataset.state = "pending";
      state.textContent = `Asking ${serve.address}…`;
      void bridge
        .getFacts()
        .then(renderFacts)
        .catch((error: unknown) => {
          check.removeAttribute("aria-disabled");
          state.dataset.state = "refused";
          state.textContent = String(error);
        });
    });
    document.body.append(overlay);
  }

  // ---------------------------------------------- the kyo this app runs (KYO-449.c1)
  //
  // The login PATH's kyo (a checkout's, unpacked) is what `kyo projects add`, the
  // terminal panes and a controller on this machine run -- not what the records views
  // read. So a kyo this app does not read is a status-bar chip, not an overlay: the
  // window stays usable, and the chip opens the state's words and Check again.

  function openKyoCompat(compat: KyoCompatFacts, asked: string | null): void {
    document.querySelector('[data-region="kyo-compat-dialog"]')?.remove();
    const state = el("div", {
      class: "prompt-problem",
      data: { region: "kyo-compat-state", state: asked === null ? "none" : "refused" },
      text: asked ?? "",
    });
    const check = el("button", {
      class: "prompt-button prompt-ok",
      data: { region: "kyo-check-again", action: "kyo-check-again" },
      attrs: { type: "button" },
      text: "Check again",
    });
    const close = el("button", {
      class: "prompt-button",
      data: { region: "kyo-compat-close", action: "kyo-compat-close" },
      attrs: { type: "button" },
      text: "Close",
    });
    const overlay = el(
      "div",
      {
        class: "prompt-overlay",
        data: { region: "kyo-compat-dialog", overlay: "kyo", state: compat.state },
      },
      [
        el("div", { class: "prompt-dialog serve-down", attrs: { role: "alertdialog" } }, [
          ...compatWords(compat, "kyo-compat"),
          state,
          el("div", { class: "prompt-actions" }, [close, check]),
        ]),
      ],
    );
    close.addEventListener("click", () => overlay.remove());
    check.addEventListener("click", () => {
      check.setAttribute("aria-disabled", "true");
      state.dataset.state = "pending";
      state.textContent = "Asking kyo version --json…";
      void bridge
        .getFacts()
        .then((facts) => {
          renderFacts(facts);
          overlay.remove();
          if (facts.kyoCompat.state !== "accepted") {
            openKyoCompat(
              facts.kyoCompat,
              `Still the same (asked at ${new Date().toLocaleTimeString()}).`,
            );
          }
        })
        .catch((error: unknown) => {
          check.removeAttribute("aria-disabled");
          state.dataset.state = "refused";
          state.textContent = String(error);
        });
    });
    document.body.append(overlay);
  }

  let latestKyoCompat: KyoCompatFacts | null = null;

  function renderKyoCompat(compat: KyoCompatFacts): void {
    latestKyoCompat = compat;
    kyoChip.style.display = compat.state === "accepted" ? "none" : "";
    kyoChip.dataset.state = compat.state;
    kyoChip.textContent = compat.state === "accepted" ? "" : `⚠ ${compat.title}`;
    kyoChip.setAttribute("data-tip", compat.where);
  }

  // ------------------------------------------------------------ facts and badges

  function renderFacts(facts: LiveFacts): void {
    renderServe(facts.serve);
    renderKyoCompat(facts.kyoCompat);
    const store = facts.store;
    storeLine.textContent = store.ok
      ? `${store.project ?? "?"} · ${store.records ?? 0} records · schema ${store.schemaVersion ?? "?"}`
      : (store.problem ?? "the store could not be read");
    storeLine.dataset.ok = String(store.ok);
    currencyLine.textContent = facts.currency.says;
    currencyLine.dataset.state = facts.currency.state;
    const project = facts.project;
    // A dev box's project carries its address in the caption (KYO-370): the same
    // prefix names a different store on each machine, and the switcher is where a
    // reader checks which one they are looking at.
    const name = project.project ?? "no project";
    const where =
      project.remote === null ? "" : ` on ${project.remote.host}:${project.remote.port}`;
    rail.setProject(name, `${project.root}${where} (${project.source})`, project.remote?.host);
    servedLine.textContent = servedLabel(project.project, project.root, project.remote);
    servedLine.setAttribute("data-tip", `${project.root}${where}`);
    latestRecents = project.recents;
    latestProjectRoot = project.root;
    latestRemotes = project.remotes;
    latestRemote = project.remote;
  }

  function renderBadge(source: BadgeSource, count: number): void {
    rail.setBadge(source, count);
  }

  function renderAwaiting(view: AwaitingHuman): void {
    renderBadge("awaiting-human", view.ok ? view.count : 0);
  }

  /**
   * Tab kinds whose content is a specific project's object, read once at open, with
   * nothing making them follow a later project switch the way the terminal and claude
   * panes do (`projectMoved` in main.ts owns those two explicitly).
   *
   * `record` just goes stale on screen: `tabs/record/index.ts` fetches
   * `getRecordDocument(asked)` once in `render()` and never again, so an open record
   * tab keeps showing the OLD project's record under the OLD project's id -- and
   * because ids are small per-project sequences, that id very often still resolves to
   * something in the NEW project, so the reader sees confidently wrong content with no
   * signal anything is off. `views` has the same one-shot-fetch shape for docs, the
   * records list, awaiting and briefs.
   *
   * `compose` is worse than stale: `saveScratchpad`/`saveIdea` resolve the project
   * fresh on every call (`projectCwd()` in main.ts), so a scratchpad or idea tab left
   * open across a switch does not just display old text -- its debounced autosave
   * (`tabs/compose/index.ts`) goes on firing and will write that old-project text into
   * the NEW project's `ideas/`, silently, the next time it fires. Closing the tab
   * cancels that timer (`compose`'s own `close()`) before it can.
   *
   * `terminal`, `claude` and `activity` are deliberately absent: the first two already
   * re-point themselves from the main process, and `activity` renders a live push
   * (`onActivityChanged`) rather than a document fetched once, so it reads correctly
   * for whichever project is now open without being closed.
   */
  const PROJECT_SCOPED_TAB_KINDS: ReadonlySet<string> = new Set(["record", "compose", "views"]);

  /**
   * Close every open tab whose content cannot outlive the project it was opened
   * against (KYO-175.c7's own reasoning, one layer up: a pane silently showing or
   * writing the previous project's state is the defect that record fixed for the
   * terminal, applied here to tabs instead of panes).
   *
   * ONE call per distinct reference, against a close that means "every copy"
   * (KYO-319). This used to call a `closeMatching` that closed the FIRST tab on a
   * reference, once per snapshot entry, and it came out right only because the snapshot
   * holds one entry per LIVE tab: a record open in both columns produced two calls, and
   * two calls closed two tabs. That is an accident of the loop rather than a property of
   * the close -- deduplicating here, as this now does, would have left the second copy
   * of the old project's record open with nothing saying so. The close says "every", so
   * this says each reference once, and neither depends on the other's shape.
   */
  function closeProjectScopedTabs(): void {
    const seen = new Set<string>();
    for (const column of columns.snapshot().columns) {
      for (const ref of column.tabs) {
        if (!PROJECT_SCOPED_TAB_KINDS.has(ref.kind)) continue;
        // A NUL between the two: a kind and an argument are both free text, and
        // "record" + "a:b" must not collide with "record:a" + "b".
        const key = `${ref.kind}\u0000${ref.arg}`;
        if (seen.has(key)) continue;
        seen.add(key);
        columns.closeEveryMatching(ref);
      }
    }
  }

  // ------------------------------------------------------------ lifecycle

  async function start(): Promise<void> {
    document.body.dataset.tabKinds = tabKindNames().join(",");

    // BEFORE THE FIRST AWAIT, and the only subscription that has to be (KYO-240.c1).
    // A deep link is a ONE-SHOT event with no second chance: nothing re-sends it and
    // nothing can be asked for it later, so a listener installed a round trip from now
    // is a listener that was not there when it mattered. The other four subscriptions
    // below stay where they are because each of them is a push of a fact this function
    // also READS -- the layout, the badges, the facts -- so an edge missed in the gap
    // is superseded by the read a few lines later rather than lost. `onDelivery` holds
    // what it receives until `applyLayout` has run; see its comment.
    unsubscribes.push(bridge.onOpenTarget(onDelivery));

    const layout = await bridge.getLayout();
    document.body.dataset.displaySignature = layout.signature;
    layoutRoot = layout.root;
    layoutSignature = layout.signature;
    document.body.dataset.transient = String(transient);
    // A window opened around one moved tab opens on that (KYO-246); every other
    // window opens on what this desk remembers.
    applyLayout(seed ?? layout.layout);
    openWorkspaceToDeliveries();

    unsubscribes.push(
      bridge.onLayoutChanged((next) => {
        document.body.dataset.displaySignature = next.signature;
        const moved = next.root !== layoutRoot || next.signature !== layoutSignature;
        layoutRoot = next.root;
        layoutSignature = next.signature;
        // A transient window is not the desk's arrangement and is not rearranged
        // when the desk changes; only a persistent one follows the screens.
        if (transient) return;
        // NOTHING THIS WINDOW DOES NOT ALREADY HAVE (KYO-334.c3). This push carries
        // what is on DISK for a project and desk, and when they are the ones already
        // on screen, disk is at best equal to the workspace and at worst older than
        // it -- `announceDisplays` fires on every screen event, including ones that
        // leave the signature identical. Restoring from it then is purely
        // destructive: `applyLayout` -> `columns.restore` closes every open tab and
        // rebuilds the SAVED ones, so a push landing in the 250ms after a tab was
        // closed puts that tab back, and the save that follows the restore makes it
        // permanent. Measured: forced in `project-switch.spec.ts`, the closed tab
        // came back 3 of 3 without this guard. A push that really does move the
        // window -- the project switch, a desk that changed -- is applied as before.
        if (!moved) return;
        // Whatever was pending belonged to where this window WAS; the main process
        // would refuse it now, and after the restore below it would describe a
        // workspace nobody arranged.
        dropPendingSave();
        applyLayout(next.layout);
      }),
      bridge.onCommand(onCommand),
      // The native window lost focus (KYO-257.c1): a menu closes as a macOS one does,
      // even when focus was in the claude view and the page's own `blur` never fired.
      bridge.onWindowBlur(closeMenu),
      // ...and a menu opened on this window while it is not focused -- a right-click
      // from another app, which macOS does not activate this one for -- takes focus, so
      // the window has a blur to lose when the reader clicks away (KYO-257.c1).
      onOverlayChange(() => {
        const menuOpen = document.querySelector('[data-region="context-menu"]') !== null;
        if (menuOpen && !document.hasFocus()) bridge.claimFocus();
      }),
      bridge.onTabReceived(onTabReceived),
      bridge.onTabDragHover((at) => columns.hover(at)),
      bridge.onTabDragShown(() => columns.tornShown()),
      bridge.onActivityChanged((view) => renderBadge("running-dispatches", view.running)),
      bridge.onAwaitingChanged(renderAwaiting),
      // Somebody else wrote the store, or `ideas/` (KYO-310): whichever list is
      // showing re-reads itself, keeping what the reader has done to it. Subscribed
      // here rather than inside the sidebar because this subscription's lifetime is
      // the WINDOW's -- it outlives every list the reader shows and hides, and is
      // released with the others in `dispose`.
      bridge.onStoreChanged((change) => {
        sidebar.followStore(change);
        // KYO-471: the events stream closing is the first sign kyo serve went away, so
        // the window asks at once and shows what the answer says (not running, or
        // slow); the re-probe loop that answer starts is what clears it (KYO-449.c8).
        if (serveReprobeDue(change, reprobe?.running === true)) {
          void bridge
            .getFacts()
            .then(renderFacts)
            .catch(() => undefined);
        }
      }),
      bridge.onProjectChanged(() => {
        // FIRST, before a line of this handler rearranges anything (KYO-334). The
        // window has already moved by the time this arrives, so a save still inside
        // its debounce describes the project just left, and the closes below would
        // turn a fired one into "the old project's workspace, half emptied, filed
        // under the new project". Held off until the `layoutChanged` that follows
        // says which project and desk the arrangement on screen belongs to.
        dropPendingSave();
        layoutRoot = null;
        layoutSignature = null;
        closeProjectScopedTabs();
        sidebar.refresh();
        void bridge.getFacts().then(renderFacts);
        void bridge.getAwaitingHuman().then(renderAwaiting);
        void bridge.getActivity().then((view) => renderBadge("running-dispatches", view.running));
      }),
    );

    const facts = await bridge.getFacts();
    renderFacts(facts);
    document.body.dataset.facts = "ready";

    // The two badges, read once and then pushed. Both reads also ARM the main
    // process's watch of that fact, which is why they are made here rather than left
    // to whichever tab happens to want the number.
    const [awaiting, activity] = await Promise.all([
      bridge.getAwaitingHuman(),
      bridge.getActivity(),
    ]);
    renderAwaiting(awaiting);
    renderBadge("running-dispatches", activity.running);
    document.body.dataset.badges = "ready";
  }

  return {
    root,
    render(state: AppState) {
      if (state.self.kind !== "shell") return;
      seed = state.self.seed;
      transient = state.self.transient;
      // A hidden spare says so (KYO-261), and stops saying so when a tear-off uses it.
      document.body.dataset.spare = String(state.self.spare);
      windowCluster.replaceChildren(
        ...state.windows.map((info) => {
          const chip = el("button", {
            class: "window-chip",
            data: { windowId: String(info.id), self: String(info.id === state.self.id) },
            tip: `Window ${info.id}`,
            on: { click: () => void bridge.focusWindow(info.id) },
          });
          chip.append(icon("window", { size: 13 }));
          return chip;
        }),
        el(
          "button",
          {
            class: "window-chip",
            data: { action: "open-window" },
            tip: "New window on a project (⌘N)",
            on: { click: (event) => openWindowChooser(event) },
          },
          [icon("plus", { size: 13 })],
        ),
      );
    },
    start,
    dispose() {
      for (const off of unsubscribes) off();
      columns.dispose();
    },
  };
}
