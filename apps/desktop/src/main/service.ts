// KYO-368: the store-facing seam -- and, since KYO-459, one implementation for every
// project.
//
// Every read and write `main.ts` makes that touches a project's Kyosei store --
// records, briefs, facts, docs, links, activity, sessions, ideas, scratchpad -- named
// once, here, behind one interface. `main.ts` calls this interface and never those
// files directly -- KYO-368.c2's check in `tests/seams.spec.ts` fails on a `main.ts`
// that still imports one of them.
//
// ONE TRANSPORT (KYO-459). Each store member takes a `ServedProject`: one project on
// one `kyo serve` (`kyoserve.ts`), this machine's or another's. There is no second
// implementation any more -- `localStoreService`, which read this machine's stores by
// `kyo` subprocess and SQLite, and the `Proxy` that sent every member to a dev box's
// app, are both gone, and with them the app's own server that answered that Proxy.
// A project on this machine and one on another machine are read by the same code, through
// the same routes, and differ only in the address.
//
// The members that are not a store's -- the app's own recents, the link grammar, the
// controller launcher -- stay here beside them so `main.ts` keeps its one import; they
// are plain local calls, and none of them is on any wire.

import type { StoreChangeKind } from "../shared/protocol";
import { activityFingerprint, readActivity } from "./activity";
import { controllerStatus, startController } from "./controller";
import { readDoc, readDocTree } from "./docs";
import {
  environmentRootOf,
  locateProject,
  type ProjectLocation,
  type ProjectState,
  probeProject,
  type RememberedProject,
  readCurrency,
  readProjectState,
  readStoreFacts,
  recentEntries,
  resolveProjectRoot,
  sameProject,
  windowsAtLaunch,
  withoutRecent,
  withRecent,
  withWindows,
  writeProjectState,
} from "./facts";
import {
  createIdea,
  deleteIdea,
  mkdirIdeas,
  moveIdea,
  openIdea,
  readCompletions,
  readIdeaTree,
  readProjectHome,
  readScratchpad,
  renameIdea,
  saveIdea,
  writeScratchpad,
} from "./ideas";
import type { ServedProject } from "./kyoserve";
import {
  decideClaudeClick,
  decideClaudeLink,
  followLink,
  type KnownProject,
  URL_SCHEME,
  urlFromArgv,
} from "./links";
import {
  readAwaitingHuman,
  readBases,
  readBrief,
  readBriefs,
  readLatestBrief,
  readLatestShiftNote,
  readRecord,
  readRecordDocument,
  readRecordsList,
  readRecordTree,
  readShiftNotes,
  runBase,
} from "./records";
import { CLAUDE_INDEX_URL, type PaneTarget, readSessionsOf, resolvePaneTarget } from "./sessions";

// Re-exported so `main.ts` can name these types without importing the files that
// declare them -- a `import type` of a forbidden file is still an import of it.
export type {
  KnownProject,
  PaneTarget,
  ProjectLocation,
  ProjectState,
  RememberedProject,
  ServedProject,
};

/**
 * Every store-facing read and write this app makes, plus the one subscription
 * (`subscribe`). KYO-368.c2.
 */
export interface StoreService {
  readonly activityFingerprint: typeof activityFingerprint;
  readonly readActivity: typeof readActivity;
  readonly readDoc: typeof readDoc;
  readonly readDocTree: typeof readDocTree;
  readonly environmentRootOf: typeof environmentRootOf;
  readonly locateProject: typeof locateProject;
  readonly probeProject: typeof probeProject;
  readonly readCurrency: typeof readCurrency;
  readonly readProjectState: typeof readProjectState;
  readonly readStoreFacts: typeof readStoreFacts;
  readonly recentEntries: typeof recentEntries;
  readonly resolveProjectRoot: typeof resolveProjectRoot;
  readonly withRecent: typeof withRecent;
  /** KYO-465: forget a recent, keep the windows, reopen each through its own server. */
  readonly sameProject: typeof sameProject;
  readonly withoutRecent: typeof withoutRecent;
  readonly withWindows: typeof withWindows;
  readonly windowsAtLaunch: typeof windowsAtLaunch;
  readonly writeProjectState: typeof writeProjectState;
  readonly createIdea: typeof createIdea;
  readonly deleteIdea: typeof deleteIdea;
  readonly mkdirIdeas: typeof mkdirIdeas;
  readonly moveIdea: typeof moveIdea;
  readonly openIdea: typeof openIdea;
  readonly readCompletions: typeof readCompletions;
  readonly readIdeaTree: typeof readIdeaTree;
  readonly readProjectHome: typeof readProjectHome;
  readonly readScratchpad: typeof readScratchpad;
  readonly renameIdea: typeof renameIdea;
  readonly saveIdea: typeof saveIdea;
  readonly writeScratchpad: typeof writeScratchpad;
  readonly decideClaudeClick: typeof decideClaudeClick;
  readonly decideClaudeLink: typeof decideClaudeLink;
  readonly followLink: typeof followLink;
  readonly urlFromArgv: typeof urlFromArgv;
  /** The scheme this app registers with the operating system (`links.ts`'s own). */
  readonly urlScheme: string;
  readonly readAwaitingHuman: typeof readAwaitingHuman;
  readonly readBases: typeof readBases;
  readonly readBrief: typeof readBrief;
  readonly readBriefs: typeof readBriefs;
  readonly readLatestBrief: typeof readLatestBrief;
  readonly readLatestShiftNote: typeof readLatestShiftNote;
  readonly readRecord: typeof readRecord;
  readonly readRecordDocument: typeof readRecordDocument;
  readonly readRecordsList: typeof readRecordsList;
  readonly readRecordTree: typeof readRecordTree;
  readonly readShiftNotes: typeof readShiftNotes;
  readonly runBase: typeof runBase;
  /** `kyo sessions --json`, asked of the project's kyo serve (KYO-459). */
  readonly readSessionsOf: typeof readSessionsOf;
  readonly resolvePaneTarget: typeof resolvePaneTarget;
  /** The page that lists the account's own Remote Control sessions (`sessions.ts`'s own). */
  readonly claudeIndexUrl: string;
  /**
   * The controller launcher (KYO-383, KYO-460): `kyo controller` on this machine, for a
   * project on it. A dev box's runs over the person's SSH (`ssh.ts`), never through
   * any server.
   */
  readonly controllerStatus: typeof controllerStatus;
  readonly startController: typeof startController;
  /**
   * Follow one project's change stream -- kyo serve's events -- until the returned
   * function is called. `kind` is pushed for the caller to decide who hears it: this
   * interface has no notion of a window, so delivery stays in `main.ts`.
   */
  subscribe(project: ServedProject, onChanged: (kind: StoreChangeKind) => void): () => void;
}

export const storeService: StoreService = {
  activityFingerprint,
  readActivity,
  readDoc,
  readDocTree,
  environmentRootOf,
  locateProject,
  probeProject,
  readCurrency,
  readProjectState,
  readStoreFacts,
  recentEntries,
  resolveProjectRoot,
  withRecent,
  sameProject,
  withoutRecent,
  withWindows,
  windowsAtLaunch,
  writeProjectState,
  createIdea,
  deleteIdea,
  mkdirIdeas,
  moveIdea,
  openIdea,
  readCompletions,
  readIdeaTree,
  readProjectHome,
  readScratchpad,
  renameIdea,
  saveIdea,
  writeScratchpad,
  decideClaudeClick,
  decideClaudeLink,
  followLink,
  urlFromArgv,
  urlScheme: URL_SCHEME,
  readAwaitingHuman,
  readBases,
  readBrief,
  readBriefs,
  readLatestBrief,
  readLatestShiftNote,
  readRecord,
  readRecordDocument,
  readRecordsList,
  readRecordTree,
  readShiftNotes,
  runBase,
  readSessionsOf,
  resolvePaneTarget,
  claudeIndexUrl: CLAUDE_INDEX_URL,
  controllerStatus,
  startController,
  subscribe(project, onChanged) {
    return project.subscribe(onChanged);
  },
};
