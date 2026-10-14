// The live seams, checked without launching a window.
//
// These run under the same Playwright runner as the smoke suite -- one harness for
// this directory, per KYO-110's shape -- but they launch nothing: the resolver,
// the argv guard, the read-only database read and the currency mapping are all in
// Electron-free modules on purpose, so they can be driven directly and can fail
// with a line number instead of a screenshot.
//
// Every fixture path here contains a space (see `fixture.ts`).

import { execFileSync, spawnSync } from "node:child_process";
import {
  chmodSync,
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { basename, delimiter, dirname, isAbsolute, join, relative, sep } from "node:path";
import { expect, test } from "@playwright/test";
import { readActivity } from "../src/main/activity";
import { readDoc, readDocTree } from "../src/main/docs";
import {
  CURRENCY_COMMAND,
  currencyFrom,
  DIRECTION_TO_STATE,
  LOCATE_COMMAND,
  locateProject,
  probeProject,
  readCurrency,
  readProjectState,
  readStoreFacts,
  resolveProjectRoot,
  windowsAtLaunch,
  withoutRecent,
  withRecent,
  withWindows,
  writeProjectState,
} from "../src/main/facts";
import {
  assertIdeasRead,
  createIdea,
  IDEAS_READ_COMMANDS,
  mergeAuthor,
  openIdea,
  readCompletions,
  readIdeaTree,
  readProjectHome,
  readScratchpad,
  saveIdea,
  writeScratchpad,
} from "../src/main/ideas";
import {
  assertAppMayRun,
  assertNoProseArguments,
  checkKyo,
  commandLine,
  describeResolution,
  findCliRoot,
  INSTALL_KIND_LABELS,
  INSTALL_KYO_COMMAND,
  judgeKyoVersion,
  KYO_ACCEPTS,
  kyoCompatFrom,
  proseFileArgs,
  resolveKyo,
  runKyo,
  runKyoJson,
  shellQuote,
  upgradeRoute,
} from "../src/main/kyo";
import {
  clientFor,
  PROBE_TIMEOUT_MS,
  ServeClient,
  ServedProject,
  serveFactsFrom,
} from "../src/main/kyoserve";
import { URL_SCHEME } from "../src/main/links";
import { offeredEntries } from "../src/main/projects";
import {
  assertRecordsRead,
  classOf,
  coveringDispatchViews,
  dispatchViews,
  KYO_WRITING_COMMANDS,
  RECORDS_READ_COMMANDS,
  readAwaitingHuman,
  readBases,
  readBoardClasses,
  readBrief,
  readBriefs,
  readLatestBrief,
  readRecord,
  readRecordDocument,
  readRecordsList,
  readRecordTree,
  runBase,
} from "../src/main/records";
import { forgetRemote, readRemotes, rememberRemote } from "../src/main/remote";
import {
  appendShellEnvLog,
  between,
  captured,
  captureLoginEnvironment,
  ENV_BEGIN,
  ENV_END,
  ENV_SCRIPT,
  findOnPath,
  LOGIN_SHELL_ENV,
  LOGIN_SHELL_FLAGS,
  parseEnvBlock,
  RETRIABLE_OUTCOMES,
  resolveTools,
  SERVER_TOOLS,
  SHELL_ENV_LOG_KEEP_LINES,
  SHELL_ENV_LOG_MAX_BYTES,
  SHELL_ENV_OUTCOMES,
  type ShellEnvironment,
  shellEnvLogLine,
  shellEnvLogPath,
  terminalEnvironment,
  worthAskingAgain,
} from "../src/main/shellenv";
import { CONFIG_NAME, DB_NAME, KYOSEI_DIR } from "../src/main/store";
import { dropTarget, openedFrom, projectOfWindow, spareFits } from "../src/main/tabdrag";
import {
  askedAboutOneProject,
  attachedSession,
  BARE_PANE_NOTICE,
  CLI_NAMES,
  CONTROLLER_SOCKET_ENV,
  CONTROLLER_SOCKET_NAME,
  clientAttachArgs,
  clientGoneNotice,
  controllerJoinCommand,
  controllerJoinOverSsh,
  controllerSocketPath,
  controllerSocketSpelled,
  controllerTmuxEnv,
  findTmux,
  launchOrigin,
  noTmuxReason,
  noticeFor,
  type PaneAttachState,
  parseClients,
  parseSessions,
  planPaneAttach,
  planPaneBare,
  planTmux,
  removeRetiredCli,
  resolveTmux,
  retiredCliLinkDirs,
  retryingCapture,
  sessionPills,
  TMUX_ENV,
  TMUX_PATH_PLACEHOLDER,
  TMUX_SUBCOMMANDS_REFUSED,
  tmuxArgs,
  tmuxDefaultTmpdir,
  WILL_ASK_AGAIN,
} from "../src/main/terminal";
import { readProperties, readSections, stubRecordIds } from "../src/shared/ideafile";
import {
  applyChanges,
  lineChanges,
  MERGE_MARKER_END,
  MERGE_MARKER_PREFIX,
  MERGE_MARKER_THEIRS,
  merge3,
  mergeMarkerMine,
} from "../src/shared/merge3";
import {
  ATTACH_ACTIONS,
  BOARD_CLASSES,
  BOARD_FILTER_LABELS,
  BOARD_FILTER_ORDER,
  boardFilterLabel,
  boardFilterUnavailable,
  boardFilterValue,
  CHANNEL,
  composeReloadDue,
  GIT_FACTS_OUTCOMES,
  groupingField,
  LIST_FOLLOWS,
  listingStatus,
  parseWindowShow,
  reprobeWhileDown,
  SERVE_REPROBE_MS,
  SERVE_RESTART_COMMAND,
  STORE_CURRENCY_STATES,
  STORE_FACING_CHANNELS,
  type StoreChangeKind,
  serveReprobeDue,
  serveStaleNotice,
  storeReloadDue,
  TREE_GROUPING_FIELDS,
  TREE_GROUPINGS,
  WINDOW_LOCAL_CHANNELS,
  WINDOW_SHOW_SWITCH,
  WINDOW_SHOWS,
} from "../src/shared/protocol";
import { servedLabel, servedWhere } from "../src/shared/served";
import {
  FIXTURE_IDEAS,
  FIXTURE_IDEAS_OTHER,
  type Fixture,
  hideExportedRecord,
  kyo,
  makeFixture,
  makeRecordsFixture,
  readAllocations,
  removeFixture,
  scratchpadPathFor,
  setAllocation,
  setSchemaVersion,
  sqliteScalar,
  startKyoServe,
  stopKyoServe,
  writeStoreFile,
} from "./fixture";

const APP_ROOT = join(__dirname, "..");
const SRC_ROOT = join(APP_ROOT, "src");

function testResolution() {
  // An empty login PATH: the working tree answers in a checkout whatever the runner's
  // PATH holds, and this keeps an installed `kyo` from ever being the one a check ran.
  return resolveKyo({ startDir: __dirname, path: "" });
}

/** A directory holding an executable `kyo` stub, as a `uv tool install` would leave one. */
function pathWithKyo(label: string): string {
  const bin = join(tempDirWithSpace(label), "bin");
  mkdirSync(bin, { recursive: true });
  writeFileSync(join(bin, "kyo"), "#!/bin/sh\necho stub\n", "utf8");
  chmodSync(join(bin, "kyo"), 0o755);
  return bin;
}

/**
 * The store `kyo` reports for `checkout`, derived WITHOUT asking `kyo` (KYO-371).
 *
 * In the main checkout that is the checkout itself. In a dispatch worktree it is not,
 * on purpose: `kyosei.db` is gitignored, `git worktree add` checks out tracked files
 * only, and KYO-342 resolves a linked worktree with no database of its own, through
 * git's common dir, to the main checkout's store -- so `kyo where` there answers
 * `store_root` = the main checkout while `code_root` stays the worktree. Measured from
 * KYO-371.d1's worktree: `store_root` "/Users/me/coding-projects/kyosei",
 * `code_root` the worktree, `store_via_main_checkout` set. The resolver's `cliRoot` is
 * the worktree in both places; only the STORE moves.
 *
 * The same three conditions `_store_through_main_checkout` tests, read here from the
 * filesystem and git, so a check comparing against this is a second derivation of the
 * rule rather than the CLI's answer compared with itself: no database here, a linked
 * worktree, and a main checkout carrying both a config and a database. Any one of them
 * missing and the answer is the checkout -- which is every run in the main checkout,
 * where these checks compare against exactly what they compared against before.
 */
/** Where the CLI keeps a store's database: for a check to name, never for the app to open. */
function dbPathFor(storeRoot: string): string {
  return join(storeRoot, KYOSEI_DIR, DB_NAME);
}

function configPathFor(storeRoot: string): string {
  return join(storeRoot, KYOSEI_DIR, CONFIG_NAME);
}

/**
 * The store a split-layout checkout's `.kyosei` link names, resolved as kyo resolves it
 * (`~` against the home directory, a relative path against the link's own directory), or
 * null when the checkout carries no link. This repository itself has been split since
 * KYO-630, so its store is not inside the checkout.
 */
function linkedStoreFor(checkout: string): string | null {
  const link = join(checkout, ".kyosei");
  if (!existsSync(link)) return null;
  const line = readFileSync(link, "utf8")
    .split("\n")
    .find((entry) => /^store:\s*/.test(entry));
  if (line === undefined) return null;
  let target = line.replace(/^store:\s*/, "").trim();
  if (target === "~" || target.startsWith("~/")) target = join(homedir(), target.slice(1));
  const resolved = isAbsolute(target) ? target : join(checkout, target);
  return existsSync(resolved) ? realpathSync(resolved) : null;
}

function storeRootFor(checkout: string): string {
  const linked = linkedStoreFor(checkout);
  if (linked !== null) return linked;
  if (existsSync(dbPathFor(checkout))) return checkout;
  const git = (...args: string[]) =>
    execFileSync("git", args, { cwd: checkout, encoding: "utf8" }).trim();
  const gitDir = git("rev-parse", "--absolute-git-dir");
  const common = git("rev-parse", "--path-format=absolute", "--git-common-dir");
  if (realpathSync(common) === realpathSync(gitDir)) return checkout;
  const main = realpathSync(dirname(common));
  const inTree = relative(
    realpathSync(git("rev-parse", "--show-toplevel")),
    realpathSync(checkout),
  );
  const candidate = join(main, inTree);
  const linkedFromMain = linkedStoreFor(candidate);
  if (linkedFromMain !== null) return linkedFromMain;
  return existsSync(configPathFor(candidate)) && existsSync(dbPathFor(candidate))
    ? candidate
    : checkout;
}

function tempDirWithSpace(label: string): string {
  return mkdtempSync(join(tmpdir(), `kyosei ${label} `));
}

function everySourceFile(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) everySourceFile(path, found);
    else if (entry.name.endsWith(".ts")) found.push(path);
  }
  return found;
}

// ------------------------------------------------------- KYO-111.c1: resolver

test("the resolver names two sources and picks the working tree in a checkout", () => {
  const resolution = testResolution();
  expect(resolution.candidates.map((source) => source.name)).toEqual([
    "working-tree",
    "login-path",
  ]);
  expect(resolution.source.name).toBe("working-tree");
  expect(resolution.cliRoot).not.toBeNull();
  // The working tree it found is this repository's checkout, source and all.
  expect(existsSync(join(resolution.cliRoot ?? "", "src", "kyosei", "__main__.py"))).toBe(true);
  expect(resolution.source.program ?? "").toContain(".venv");
  expect(resolution.source.baseArgs).toEqual(["-m", "kyosei"]);

  const stated = describeResolution(resolution);
  expect(stated.chosen).toBe("working-tree");
  expect(stated.label).toBe("working tree");
  expect(stated.detail).toContain("-m kyosei");
});

test("a change under src/kyosei/ is live with no rebuild in between", async () => {
  // The point of decision 3, made checkable: the resolver's chosen invocation
  // executes the checkout's *source files*. Nothing is compiled, copied or
  // installed between `src/kyosei/` and what the app runs, so reading the module
  // the resolver would import out of the checkout is the same file the CLI runs.
  const resolution = testResolution();
  const module = join(resolution.cliRoot ?? "", "src", "kyosei", "__main__.py");
  expect(existsSync(module)).toBe(true);
  const version = await runKyoJson(resolution, ["docs", "check"], {
    cwd: resolution.cliRoot ?? APP_ROOT,
  });
  // `root` is the STORE root, which in a dispatch worktree is the main checkout's
  // (KYO-342, KYO-371) -- the checkout itself everywhere else.
  expect(version.data?.root).toBe(storeRootFor(resolution.cliRoot ?? APP_ROOT));
});

test("the working-tree source refuses by name when the venv is missing", () => {
  const root = tempDirWithSpace("no venv");
  mkdirSync(join(root, "src", "kyosei"), { recursive: true });
  writeFileSync(join(root, "src", "kyosei", "__main__.py"), "", "utf8");

  const resolution = resolveKyo({ startDir: root, path: join(root, "empty bin") });
  const workingTree = resolution.candidates[0];
  expect(workingTree?.unavailable ?? "").toContain("make test");
  expect(workingTree?.unavailable ?? "").toContain(root);
  // Both sources unavailable: the app states that, rather than choosing one anyway,
  // and its one detail line names *both* refusals rather than only the first tried.
  const stated = describeResolution(resolution);
  expect(stated.chosen).toBeNull();
  expect(commandLine(resolution)).toBe("");
  expect(stated.detail).toContain("make test");
  expect(stated.detail).toContain(INSTALL_KYO_COMMAND);
});

test("outside a checkout the resolver runs the kyo on the login PATH, and only that one (KYO-450)", () => {
  // A packed app: no `src/kyosei/__main__.py` anywhere above it. KYO-443 removed
  // the runtime it used to ship, so the second source is the `kyo` the user installed,
  // found on the LOGIN shell's PATH exactly as `command -v` would find it.
  const packed = tempDirWithSpace("packed app");
  const bin = pathWithKyo("login path");
  const elsewhere = pathWithKyo("not on path");

  const found = resolveKyo({ startDir: packed, path: `${join(packed, "none")}:${bin}` });
  expect(found.cliRoot).toBeNull();
  expect(found.source.name).toBe("login-path");
  expect(found.source.program).toBe(join(bin, "kyo"));
  // The installed script IS the CLI: nothing goes in front of the caller's arguments,
  // so no interpreter path is ever constructed for it.
  expect(found.source.baseArgs).toEqual([]);
  expect(commandLine(found, ["version"])).toBe(`${shellQuote(join(bin, "kyo"))} version`);
  expect(describeResolution(found).chosen).toBe("login-path");

  // A `kyo` that exists, but in a directory the login PATH does not name, is not
  // found: nothing is probed beyond PATH, because a `kyo` the hooks do not run is not
  // one this app may run either.
  const notOnPath = resolveKyo({ startDir: packed, path: join(packed, "none") });
  expect(existsSync(join(elsewhere, "kyo"))).toBe(true);
  expect(notOnPath.source.program).toBeNull();
  const refusal = notOnPath.candidates[1]?.unavailable ?? "";
  expect(refusal).toContain("no `kyo` on the PATH your login shell sets up");
  expect(refusal).toContain(INSTALL_KYO_COMMAND);
  // ...and the remedy it names clears it: a `kyo` on that PATH is then the answer.
  const installed = resolveKyo({ startDir: packed, path: `${join(packed, "none")}:${elsewhere}` });
  expect(installed.source.program).toBe(join(elsewhere, "kyo"));

  // No login PATH at all (the capture failed) says why, rather than searching the
  // process's own PATH -- launchd's minimum from a Finder launch (KYO-229).
  const blind = resolveKyo({ startDir: packed, path: null, pathProblem: "the shell timed out" });
  expect(blind.source.program).toBeNull();
  expect(blind.candidates[1]?.unavailable ?? "").toContain("the shell timed out");
  expect(blind.candidates[1]?.unavailable ?? "").toContain(INSTALL_KYO_COMMAND);

  // The resolver's source names are the protocol's, and none of them is a bundle.
  expect(describeResolution(found).candidates.map((c) => c.name)).toEqual([
    "working-tree",
    "login-path",
  ]);
});

test("a kyo on the login PATH does not outrank the working tree (KYO-79 decision 3)", () => {
  // The case a packaging change breaks silently. A developer running the app from this
  // checkout has BOTH -- the working tree and their installed `kyo` -- and an inverted
  // order would quietly run their edits under `src/kyosei/` against whatever build
  // was last installed.
  const root = tempDirWithSpace("both sources");
  mkdirSync(join(root, "src", "kyosei"), { recursive: true });
  writeFileSync(join(root, "src", "kyosei", "__main__.py"), "", "utf8");
  const venv = join(root, ".venv", "bin");
  mkdirSync(venv, { recursive: true });
  writeFileSync(join(venv, "python"), "#!/bin/sh\n", "utf8");
  chmodSync(join(venv, "python"), 0o755);

  const resolution = resolveKyo({ startDir: root, path: pathWithKyo("installed kyo") });
  // Both are available -- that is the point of the fixture -- and the working tree
  // is still the one chosen.
  expect(resolution.candidates.map((source) => source.unavailable)).toEqual([null, null]);
  expect(resolution.source.name).toBe("working-tree");
  expect(resolution.source.program).toBe(join(venv, "python"));
  expect(describeResolution(resolution).chosen).toBe("working-tree");
});

test("the pack ships no Python runtime, and re-reads the bundle to prove it (KYO-450.c1)", () => {
  // KYO-443: the CPython runtime KYO-176 staged into `Contents/Resources/` is gone,
  // with every step that built it. What a check cannot build a bundle to observe is
  // held in the source: nothing provisions an interpreter or copies one in, and
  // `verifyBundle` fails the pack on any Python it finds in the finished bundle.
  const buildScript = readFileSync(join(APP_ROOT, "build.mjs"), "utf8");
  for (const gone of [
    "uv python install",
    "extraResource:",
    "RUNTIME_DIR_NAME",
    "stageRuntime",
    "compileRuntime",
    "assertSchemaAgreement",
  ]) {
    expect(buildScript, `build.mjs still carries ${gone}`).not.toContain(gone);
  }
  expect(buildScript).toContain("await assertNoPythonRuntime(resources)");
  // Nothing in the app's own source names the retired runtime either.
  for (const file of everySourceFile(SRC_ROOT)) {
    expect(readFileSync(file, "utf8"), file).not.toContain("kyo-runtime");
  }

  // KYO-230.c1, the same shape one constant over: the scheme the packaged bundle
  // claims is `links.ts`'s, and `build.mjs` is a plain `.mjs` that cannot import it.
  // Two spellings of one word in two files with nothing but this holding them
  // together -- and the consequence of a drift is a link clicked in Chrome that opens
  // nothing, which nobody would notice until they tried one.
  expect(buildScript).toContain(`const URL_SCHEME = ${JSON.stringify(URL_SCHEME)};`);
  // ...and the option that turns it into `CFBundleURLTypes` is actually passed, and
  // the pack re-reads the built plist for it. `verifyBundle` is where that happens; a
  // check here cannot build a bundle, so what it holds is that the assertion exists.
  expect(buildScript).toContain("schemes: [URL_SCHEME]");
  expect(buildScript).toContain("CFBundleURLTypes");

  // `npm run pack`, landing under `release/`, ignored -- the packing route
  // KYO-176.c6 asks for.
  const manifest = JSON.parse(readFileSync(join(APP_ROOT, "package.json"), "utf8"));
  expect(manifest.scripts.pack).toBe("node build.mjs --pack");
  expect(buildScript).toContain('const release = join(root, "release");');
  expect(readFileSync(join(APP_ROOT, "..", "..", ".gitignore"), "utf8")).toContain(
    "apps/desktop/release/",
  );
});

test("a pack says how it will sign, and refuses what it cannot honour (KYO-386)", () => {
  // `build.mjs --sign-plan` is the pack's first decision with nothing built after it:
  // the same `signingPlan` a pack runs, from this environment and this keychain. The
  // cases asserted here are the ones that answer the same on any machine, whatever its
  // keychain holds.
  const planWith = (extra: Record<string, string>) => {
    const env: Record<string, string> = {};
    for (const [key, value] of Object.entries(process.env)) {
      if (value === undefined) continue;
      if (key === "KYOSEI_SIGN_IDENTITY" || NOTARY_ENV.includes(key)) continue;
      env[key] = value;
    }
    return spawnSync(process.execPath, ["build.mjs", "--sign-plan"], {
      cwd: APP_ROOT,
      env: { ...env, ...extra },
      encoding: "utf8",
    });
  };

  // Ad-hoc, asked for: exactly the pack before KYO-386, said in one line.
  const adhoc = planWith({ KYOSEI_SIGN_IDENTITY: "-" });
  expect(adhoc.status, adhoc.stderr).toBe(0);
  const said = JSON.parse(adhoc.stdout);
  expect(said.mode).toBe("adhoc");
  expect(said.line).toContain("ad-hoc");
  expect(said.line).not.toContain("\n");

  // An identity the keychain does not hold is refused by name -- and the remedy the
  // refusal names, unsetting it, clears it on any machine: the keychain's only
  // Developer ID identity where there is one, ad-hoc where there is none.
  const missing = planWith({
    KYOSEI_SIGN_IDENTITY: "Developer ID Application: Nobody (ZZZZZZZZZZ)",
  });
  expect(missing.status).not.toBe(0);
  expect(missing.stderr).toContain("KYOSEI_SIGN_IDENTITY");
  expect(missing.stderr).toContain("or unset it");
  const unset = planWith({});
  expect(unset.status, unset.stderr).toBe(0);
  expect(["adhoc", "signed"]).toContain(JSON.parse(unset.stdout).mode);

  // Notarization asked of a pack that would be ad-hoc is refused before anything is
  // built, naming the variable and the kind of identity Apple requires.
  const notary = planWith({ KYOSEI_SIGN_IDENTITY: "-", KYOSEI_NOTARY_PROFILE: "any-profile" });
  expect(notary.status).not.toBe(0);
  expect(notary.stderr).toContain("KYOSEI_NOTARY_PROFILE");
  expect(notary.stderr).toContain("Developer ID Application");

  // What a check cannot build a bundle to observe, held in the source the way the
  // test above holds `CFBundleURLTypes`: the pack signs through packager's `osxSign`,
  // asks for the seal of the finished bundle, and runs the signed binary as Node.
  // (Until KYO-450 it also compiled a bundled runtime's bytecode and asked for the
  // seal a second time after that runtime had run; there is no runtime now.)
  const buildScript = readFileSync(join(APP_ROOT, "build.mjs"), "utf8");
  expect(buildScript).toContain("osxSign: osxSignOptions(plan)");
  expect(buildScript.match(/await assertSignature\(bundlePath, plan\)/g)).toHaveLength(1);
  expect(buildScript).toContain("assertRunsAsNode(bundlePath)");
  expect(buildScript).toContain('"--strict"');
  // No secret passes through the pack: notarytool is handed a keychain profile's NAME,
  // or an API key file's PATH (KYO-399), and never an Apple ID, a password or a team.
  for (const secretFlag of ["--apple-id", "--password", "--team-id"]) {
    expect(buildScript).not.toContain(secretFlag);
  }
  expect(buildScript).toContain('"--keychain-profile"');
  // ...and the entitlements file stays out of the shipped asar.
  expect(buildScript).toContain("/^\\/entitlements\\.mac\\.plist$/");
});

/** Every variable that names a notary credential; stripped before each plan is asked. */
const NOTARY_ENV = [
  "KYOSEI_NOTARY_PROFILE",
  "KYOSEI_NOTARY_KEY",
  "KYOSEI_NOTARY_KEY_ID",
  "KYOSEI_NOTARY_ISSUER",
];

test("a pack notarizes with an App Store Connect API key file, named by path (KYO-399)", () => {
  // The route that works from ssh and a tmux server, where the keychain profile answers
  // "No Keychain password item found". A fake key in a temp directory: nothing here
  // calls Apple, and the sentinel proves the file's contents never reach any output.
  const dir = mkdtempSync(join(tmpdir(), "kyo-399-"));
  const sentinel = "FAKE-P8-CONTENTS-MUST-NEVER-PRINT";
  const key = join(dir, "key dir", "AuthKey_TESTKEY123.p8");
  mkdirSync(dirname(key));
  writeFileSync(key, `-----BEGIN PRIVATE KEY-----\n${sentinel}\n-----END PRIVATE KEY-----\n`);
  const full = {
    KYOSEI_NOTARY_KEY: key,
    KYOSEI_NOTARY_KEY_ID: "TESTKEY123",
    KYOSEI_NOTARY_ISSUER: "00000000-1111-2222-3333-444444444444",
  };
  const cleanEnv = () => {
    const env: Record<string, string> = {};
    for (const [name, value] of Object.entries(process.env)) {
      if (value === undefined || name === "KYOSEI_SIGN_IDENTITY" || NOTARY_ENV.includes(name))
        continue;
      env[name] = value;
    }
    return env;
  };
  const ask = (flag: string, extra: Record<string, string>) => {
    const done = spawnSync(process.execPath, ["build.mjs", flag], {
      cwd: APP_ROOT,
      env: { ...cleanEnv(), ...extra },
      encoding: "utf8",
    });
    expect(`${done.stdout}${done.stderr}`).not.toContain(sentinel);
    return done;
  };
  try {
    // c1: the three variables name the key route, and the same arguments go to submit,
    // log and history -- the path, the key id, the issuer.
    const plan = ask("--notary-plan", full);
    expect(plan.status, plan.stderr).toBe(0);
    const said = JSON.parse(plan.stdout);
    expect(said.route).toBe("key");
    expect(said.args).toEqual([
      "--key",
      key,
      "--key-id",
      "TESTKEY123",
      "--issuer",
      full.KYOSEI_NOTARY_ISSUER,
    ]);
    // c3: the line names the key id and the path; the check command is this route's,
    // quoted so the path with a space in it survives being pasted.
    expect(said.line).toContain("TESTKEY123");
    expect(said.line).toContain(key);
    expect(said.check).toBe(
      `xcrun notarytool history --key '${key}' --key-id TESTKEY123 --issuer ${full.KYOSEI_NOTARY_ISSUER}`,
    );
    const source = readFileSync(join(APP_ROOT, "build.mjs"), "utf8");
    expect(source).toContain('["notarytool", "log", answer.id, ...credentialArgs]');
    expect(source).toMatch(/"submit",\s+zip,\s+\.\.\.credentialArgs,/);
    expect(source).toMatch(
      /Check \$\{describeNotary\(credential\)\} with \\`\$\{notaryCheckCommand\(credential\)\}/,
    );
    // c4: the key is named by path and never opened, copied or written by the pack.
    expect(source).not.toMatch(/(readFileSync|readFile|copyFileSync|cp|cpSync)\([^)]*\.key\b/);

    // The profile route is unchanged: same variable, same flag, same check command.
    const profile = ask("--notary-plan", { KYOSEI_NOTARY_PROFILE: "kyosei-notary" });
    expect(profile.status, profile.stderr).toBe(0);
    const byProfile = JSON.parse(profile.stdout);
    expect(byProfile.args).toEqual(["--keychain-profile", "kyosei-notary"]);
    expect(byProfile.check).toBe("xcrun notarytool history --keychain-profile kyosei-notary");
    expect(JSON.parse(ask("--notary-plan", {}).stdout).route).toBeNull();

    // c1: both routes at once is refused, naming the two; each remedy it names clears it.
    const both = ask("--notary-plan", { ...full, KYOSEI_NOTARY_PROFILE: "kyosei-notary" });
    expect(both.status).not.toBe(0);
    expect(both.stderr).toContain("KYOSEI_NOTARY_PROFILE");
    expect(both.stderr).toContain("KYOSEI_NOTARY_KEY_ID");
    expect(both.stderr).toContain("keychain profile");
    expect(both.stderr).toContain("API key");
    expect(ask("--notary-plan", full).status).toBe(0);
    expect(ask("--notary-plan", { KYOSEI_NOTARY_PROFILE: "kyosei-notary" }).status).toBe(0);

    // c2: each partial set is refused, naming exactly what is missing; setting the
    // missing variables clears it, and so does unsetting the ones that were set.
    for (const missing of Object.keys(full)) {
      const partial: Record<string, string> = { ...full };
      delete partial[missing];
      const refused = ask("--notary-plan", partial);
      expect(refused.status, missing).not.toBe(0);
      expect(refused.stderr).toContain(`and ${missing} is not`);
      expect(refused.stderr).toContain(`Set ${missing}`);
    }
    const onlyId = ask("--notary-plan", { KYOSEI_NOTARY_KEY_ID: "TESTKEY123" });
    expect(onlyId.stderr).toContain("KYOSEI_NOTARY_KEY and KYOSEI_NOTARY_ISSUER are not");
    expect(ask("--notary-plan", {}).status).toBe(0);

    // c2: a path that names no file, or a directory, is refused naming the variable;
    // pointing it at the .p8 clears it.
    for (const wrong of [join(dir, "AuthKey_NOPE.p8"), dir]) {
      const refused = ask("--notary-plan", { ...full, KYOSEI_NOTARY_KEY: wrong });
      expect(refused.status).not.toBe(0);
      expect(refused.stderr).toContain("KYOSEI_NOTARY_KEY=");
      expect(refused.stderr).toContain("names no file");
    }

    // ...and all of it is refused before signing starts: the sign plan, the pack's
    // first step, refuses the same partial set on any machine.
    const signing = ask("--sign-plan", { KYOSEI_SIGN_IDENTITY: "-", KYOSEI_NOTARY_KEY: key });
    expect(signing.status).not.toBe(0);
    expect(signing.stderr).toContain("KYOSEI_NOTARY_KEY_ID and KYOSEI_NOTARY_ISSUER are not");
    // A complete key asked of an ad-hoc pack is refused like the profile is, naming
    // the route by key id and path.
    const adhoc = ask("--sign-plan", { KYOSEI_SIGN_IDENTITY: "-", ...full });
    expect(adhoc.status).not.toBe(0);
    expect(adhoc.stderr).toContain("Developer ID Application");
    expect(adhoc.stderr).toContain(`the App Store Connect API key TESTKEY123 at ${key}`);

    // Where this keychain can sign with a Developer ID, the plan line itself names the
    // route. Elsewhere the lines above hold `describeNotary`, which that line prints.
    const unsigned = JSON.parse(ask("--sign-plan", {}).stdout);
    if (unsigned.mode === "signed" && unsigned.line.includes("not notarized")) {
      const signed = ask("--sign-plan", full);
      expect(signed.status, signed.stderr).toBe(0);
      expect(JSON.parse(signed.stdout).line).toContain(
        `notarized with the App Store Connect API key TESTKEY123 at ${key}`,
      );
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

/**
 * The files that may start a process, and the one seam each of them is.
 *
 * This was a single name -- `kyo.ts` -- until KYO-229, and what it was protecting was
 * never "one process in the whole app": it was that there is exactly ONE route from
 * this app to the CLI, so a second call site cannot construct its own interpreter path
 * and quietly run a different `kyo`. That property is unchanged and is asserted
 * separately below; what changed is that two other things this app must now do are
 * genuinely subprocesses of their own, neither of them a route to `kyo`:
 *
 *   `shellenv.ts` runs the user's own `$SHELL` once, to ask what environment a
 *   terminal on this machine has (KYO-229). It is the answer to a question that
 *   cannot be answered any other way -- a Finder-launched app inherits `launchd`'s
 *   four-directory PATH and every guess about what is missing from it is the
 *   hand-maintained list KYO-185.c4 refuses.
 *
 * An allowlist with a reason per entry, rather than a widened regex, because the
 * reason is the thing that has to survive: an entry added without one is an entry
 * nobody argued for.
 */
const MAY_START_A_PROCESS: Record<string, string> = {
  "main/kyo.ts": "the CLI: the resolver and the one invocation (KYO-111.c1)",
  "main/shellenv.ts": "the user's own login shell, once, for its environment (KYO-229.c1)",
  // KYO-283. The pane's own shell did this until the pills existed, and a script typed
  // into a pty cannot ANSWER a question: "which sessions are alive on this socket",
  // "which session is this pane's client in", "detach this client" are reads and acts
  // whose result the app has to have. Every argv goes through `tmuxArgs`, which names
  // the socket, and a check below fails on one that does not (KYO-283.c5).
  "main/tmuxctl.ts": "tmux, about the sessions on this app's own socket (KYO-283.c2)",
  // KYO-390. Whether a session hook's recorded process is still that session, and what
  // this machine is called: `ps` and `scutil`, read-only, one question each.
  "main/procfacts.ts": "ps and scutil, for a process's name and this machine's (KYO-390)",
  // KYO-460. The app changes a machine only by running ordinary CLI commands there
  // (KYO-452): ssh to a dev box, and the folder listing and `mkdir` New project runs
  // here. This machine's own `kyo` still goes through `runKyo`; the runner is a
  // parameter, and every check drives a stand-in ssh (`controller.spec.ts`).
  "main/ssh.ts": "ssh to a dev box, and New project's listing and mkdir here (KYO-460.c1)",
};

test("no call site outside the resolver constructs an interpreter path", () => {
  const allowed = new Set(
    Object.keys(MAY_START_A_PROCESS).map((relative) => join(SRC_ROOT, ...relative.split("/"))),
  );
  const offenders: string[] = [];
  const interpreters: string[] = [];
  for (const file of everySourceFile(SRC_ROOT)) {
    const text = readFileSync(file, "utf8");
    // `.venv` is the only way to name THIS project's interpreter, and it stays
    // confined to `kyo.ts` whatever else is allowed to spawn -- that is the half of
    // this guard KYO-229 did not touch.
    if (file !== join(SRC_ROOT, "main", "kyo.ts") && /\.venv/.test(text)) interpreters.push(file);
    if (allowed.has(file)) continue;
    if (/node:child_process/.test(text)) offenders.push(file);
  }
  expect(offenders).toEqual([]);
  expect(interpreters).toEqual([]);
  // Every allowed file really does spawn, so this cannot pass by an entry having gone
  // stale -- an allowlist naming a file that no longer needs it is a licence nobody
  // is using and the next author would inherit.
  for (const [relative, why] of Object.entries(MAY_START_A_PROCESS)) {
    const text = readFileSync(join(SRC_ROOT, ...relative.split("/")), "utf8");
    expect(/node:child_process/.test(text), `${relative} no longer spawns (${why})`).toBe(true);
  }
  // And the other seams are not second routes to the CLI: none of them names `kyo`,
  // `-m kyosei`, or an interpreter.
  for (const name of ["shellenv.ts", "tmuxctl.ts", "procfacts.ts"]) {
    const text = readFileSync(join(SRC_ROOT, "main", name), "utf8");
    expect(text, `${name} names the CLI's module`).not.toContain("-m kyosei");
    expect(text, `${name} reaches the resolver`).not.toContain("resolveKyo");
    expect(text, `${name} reaches the one invocation`).not.toContain("runKyo");
  }
});

// ------------------------------------------------- KYO-368: the store-facing seam

test("every CHANNEL entry is store-facing or window-local, and never both (KYO-368.c1)", () => {
  const store = new Set<string>(STORE_FACING_CHANNELS);
  const window = new Set<string>(WINDOW_LOCAL_CHANNELS);
  // Neither list names a channel that does not exist -- a stale entry would let a
  // renamed or removed `CHANNEL` key look covered by coincidence.
  const known = new Set(Object.keys(CHANNEL));
  const stale = [...store, ...window].filter((key) => !known.has(key));
  expect(stale).toEqual([]);
  // No entry sits in both -- a channel would then answer two questions at once, and
  // KYO-369's server would not know whether to expose it.
  const overlap = [...store].filter((key) => window.has(key));
  expect(overlap).toEqual([]);
  // And no entry sits in neither -- the property the criterion actually asks for.
  const uncovered = Object.keys(CHANNEL).filter((key) => !store.has(key) && !window.has(key));
  expect(uncovered).toEqual([]);
});

test("main.ts holds no direct call into the store-facing modules -- only service.ts does (KYO-368.c2)", () => {
  // The eight files KYO-368.c2 names, plus `records.ts`: the reads that back
  // `recordsTree`, `recordsBases`, `recordsShow`, `recordsBriefs`, `recordsBrief`,
  // `shiftNotes`, `latestShiftNote`, `recordDocument`, `awaitingHuman`,
  // `recordsList`, `latestBrief` and `runBase` -- eight of the channels
  // `STORE_FACING_CHANNELS` above names -- are exactly as much "the store" as the
  // eight the criterion spells out by name, and a check that let `main.ts` import
  // `records.ts` straight would leave exactly the hole KYO-368.c2 exists to close.
  const forbidden = ["store", "facts", "docs", "links", "activity", "sessions", "ideas", "records"];
  const main = readFileSync(join(SRC_ROOT, "main", "main.ts"), "utf8");
  for (const name of forbidden) {
    expect(main, `main.ts imports ./${name} directly`).not.toMatch(
      new RegExp(`from "\\./${name}"`),
    );
  }
  // The allowlist is not stale: `service.ts` really does import every one of them
  // but `store` (which since KYO-459 is the layout's names and nothing that reads),
  // so this cannot pass because main.ts and service.ts both stopped needing a file
  // that used to matter.
  const service = readFileSync(join(SRC_ROOT, "main", "service.ts"), "utf8");
  for (const name of forbidden) {
    if (name === "store") continue;
    expect(service, `service.ts no longer imports ./${name}`).toMatch(
      new RegExp(`from "\\./${name}"`),
    );
  }
  // And `main.ts`'s handlers for the store-facing channels really do delegate to the
  // service instead of answering from nothing: every one of them goes through a
  // `StoreService` -- the module's `storeService` for what is this machine's, or the
  // window's own `service` / `scope.service` since KYO-370, which is the local one or
  // the dev box's. Either spelling is the seam; a bare module call is not.
  const throughService = (member: string) =>
    new RegExp(`\\b(storeService|service|scope\\.service)\\.${member}\\(`);
  expect(main).toMatch(throughService("readRecordTree"));
  expect(main).toMatch(throughService("readDocTree"));
  expect(main).toMatch(throughService("readIdeaTree"));
  expect(main).toMatch(throughService("readActivity"));
  expect(main).toContain("storeService.followLink(");
  // The subscription: `storeChanged` is wired through the interface, not a second,
  // parallel `storewatch.ts` import main.ts keeps for itself.
  expect(main).not.toMatch(/from "\.\/storewatch"/);
  expect(main).toMatch(throughService("subscribe"));
  // KYO-459: every project is read through `kyoserve.ts`'s client, which `main.ts`
  // reaches through the service's `ServedProject` -- never a `fetch` of its own.
  expect(main).not.toMatch(/\bfetch\(/);
});

// ------------------------------------------- KYO-111.c5: quoting and prose files

test("shellQuote survives spaces and quotes, checked by a real shell", () => {
  const nasty = "/tmp/dispatch worktrees/kyo-111/it's here/x y";
  const echoed = execFileSync("/bin/sh", ["-c", `printf '%s' ${shellQuote(nasty)}`], {
    encoding: "utf8",
  });
  expect(echoed).toBe(nasty);
});

test("the argv guard refuses prose as an argument and names the file flag", () => {
  expect(() => assertNoProseArguments(["new", "bug", "--title", "one line"])).not.toThrow();
  let message = "";
  try {
    assertNoProseArguments(["edit", "KYO-1", "--body", "first line\nsecond line"]);
  } catch (error) {
    message = (error as Error).message;
  }
  expect(message).toContain("--<flag>-file");
  expect(message).toContain("KYO-91");
});

test("proseFileArgs is the route that works, and yields a --*-file pair", () => {
  const dir = join(tempDirWithSpace("prose"), "outbox");
  const [flag, path] = proseFileArgs("why", "a reason\nover two lines", dir);
  expect(flag).toBe("--why-file");
  expect(readFileSync(path, "utf8")).toBe("a reason\nover two lines\n");
  // And what it produced is itself acceptable argv.
  expect(() => assertNoProseArguments(["verify", "KYO-1.c1", flag, path])).not.toThrow();
});

test("an invocation carries a project path containing spaces through unmangled", async () => {
  const fixture = await makeFixture(1);
  try {
    const { data } = await runKyoJson(fixture.resolution, ["docs", "check"], { cwd: fixture.root });
    // Not "contains" -- exactly equal. A path that lost a word to word-splitting
    // would still be a plausible-looking prefix.
    expect(data?.root).toBe(fixture.root);
    expect(fixture.root).toContain(" ");
  } finally {
    removeFixture(fixture);
  }
});

test("a non-zero exit still yields its JSON payload rather than nothing", async () => {
  const fixture = await makeFixture(1);
  try {
    const { code, data } = await runKyoJson(fixture.resolution, ["show", "FIX-999"], {
      cwd: fixture.root,
    });
    expect(code).not.toBe(0);
    expect(data?.ok).toBe(false);
    expect(typeof data?.error).toBe("string");
  } finally {
    removeFixture(fixture);
  }
});

// --------------------------------- KYO-459: the facts are the CLI's, through kyo serve

test("the store's facts are the CLI's count, asked of kyo serve, and the app opens no database", async () => {
  const fixture = await makeFixture(3);
  try {
    const facts = await readStoreFacts(fixture.project);
    expect(facts.ok).toBe(true);
    // The CLI's own count and schema -- compared here with the database itself, which
    // a check may read and the app may not.
    expect(String(facts.records)).toBe(
      sqliteScalar(dbPathFor(fixture.root), "SELECT count(*) AS n FROM records"),
    );
    expect(String(facts.schemaVersion)).toBe(
      sqliteScalar(dbPathFor(fixture.root), "SELECT value FROM meta WHERE key = 'schema_version'"),
    );
    expect(facts.records).toBe(3);
    expect(facts.project).toBe("FIX");
  } finally {
    removeFixture(fixture);
  }
  // No source file under src/ opens SQLite: the schema has one reader, the CLI (law 3).
  const readers = everySourceFile(SRC_ROOT).filter((file) =>
    /node:sqlite|DatabaseSync/.test(readFileSync(file, "utf8")),
  );
  expect(readers).toEqual([]);
});

test("the store path is resolved by asking the CLI, not by re-deriving it", async () => {
  const fixture = await makeFixture(2);
  try {
    const location = await locateProject(fixture.project);
    expect(location.problem).toBeNull();
    expect(location.storeRoot).toBe(fixture.root);
    expect(location.layout).toBe("in-repo");

    const facts = await readStoreFacts(fixture.project);
    expect(facts.ok).toBe(true);
    expect(facts.project).toBe("FIX");
    expect(facts.records).toBe(2);
    expect(facts.dbPath).toBe(dbPathFor(fixture.root));
    expect(facts.schemaVersion).toBeGreaterThan(0);
  } finally {
    removeFixture(fixture);
  }
});

/**
 * How many subprocesses this app has started for the CLI, all-time in this process.
 *
 * `runKyo` counts its own invocations on `globalThis` (KYO-203.c3), and these checks
 * run in the same process that imported it -- no Electron, no `app.evaluate`, so the
 * counter is simply readable. It is the ONE place a check can count real `kyo` runs
 * without guessing at a call site above it, which is what KYO-184.c3 ("runs fewer
 * `kyo` invocations per call") has to be measured at: a locator that quietly grew a
 * second command back would be invisible to every assertion about the ANSWER and
 * loud here.
 */
function kyoRuns(): number {
  return (globalThis as { __kyoInvocations?: number }).__kyoInvocations ?? 0;
}

test("a project outside git still locates, in ONE call, without inventing a branch", async () => {
  // KYO-184.c4. This is the state that used to need a second command: the guard
  // this file borrowed as a locator asked git for the branch before it reported
  // anything, so outside a repository it failed outright and `kyo docs check --json`
  // stood behind it as a fallback. `kyo where` places the project here itself, and
  // leaves the branch null -- absent, not guessed -- so the fallback is gone rather
  // than kept beside the new command.
  const fixture = await makeFixture({ records: 1, git: false });
  try {
    const before = kyoRuns();
    const location = await locateProject(fixture.project);
    expect(location.problem).toBeNull();
    expect(location.storeRoot).toBe(fixture.root);
    expect(location.branch).toBeNull();
    expect(location.canOpen).toBe(true);
    // One `kyo`, not two: the count is over the one seam this app starts a process
    // at (`runKyo`), so a fallback quietly reintroduced would show up here.
    expect(kyoRuns() - before).toBe(1);

    const facts = await readStoreFacts(fixture.project);
    expect(facts.ok).toBe(true);
    expect(facts.records).toBe(1);
    expect(facts.branch).toBeNull();
  } finally {
    removeFixture(fixture);
  }
});

test("one call answers where the project is AND whether this build can open it", async () => {
  // KYO-184.c1/c3. The two questions this file used to ask two commands, asked
  // once. `probeProject` -- the switcher's own read -- is handed that one answer
  // and runs nothing further.
  const fixture = await makeFixture(2);
  try {
    const before = kyoRuns();
    const location = await locateProject(fixture.project);
    expect(kyoRuns() - before).toBe(1);
    expect(location.storeRoot).toBe(fixture.root);
    expect(location.repoRoot).toBe(fixture.root);
    expect(location.project).toBe("FIX");
    expect(location.dbPath).toBe(dbPathFor(fixture.root));
    expect(location.canOpen).toBe(true);
    expect(location.storeProblem).toBeNull();
    expect(location.mismatch).toBeNull();
    expect(LOCATE_COMMAND).toContain("kyo where");

    const probed = kyoRuns();
    const probe = await probeProject(fixture.project, location);
    expect(kyoRuns() - probed).toBe(0);
    expect(probe.located).toBe(true);
    expect(probe.project).toBe("FIX");
    expect(probe.mismatch).toBeNull();

    // And the same envelope feeds the store read without a second locate: the one
    // request it makes is the count's own (`kyo list`, KYO-459), and the facts handler
    // hands it the list it already asked for the currency, so that costs none either.
    const shared = kyoRuns();
    const facts = await readStoreFacts(fixture.project, location);
    expect(kyoRuns() - shared).toBe(1);
    expect(facts.records).toBe(2);
  } finally {
    removeFixture(fixture);
  }
});

test("a store this build cannot open is located anyway, carrying the CLI's payload", async () => {
  // KYO-184.c1/c2. The store is stamped with a schema_version later than the build
  // speaks, which is exactly the state KYO-178's payload exists for. One call still
  // answers where the project is, and the refusal arrives as data -- carried by
  // `kyo where` from `SchemaMismatch`'s own payload, never re-derived from prose.
  const fixture = await makeFixture(1);
  try {
    const build = Number(
      sqliteScalar(dbPathFor(fixture.root), "SELECT value FROM meta WHERE key = 'schema_version'"),
    );
    setSchemaVersion(fixture, build + 1);

    const before = kyoRuns();
    const location = await locateProject(fixture.project);
    expect(kyoRuns() - before).toBe(1);
    expect(location.storeRoot).toBe(fixture.root);
    expect(location.project).toBe("FIX");
    expect(location.canOpen).toBe(false);
    expect(location.mismatch?.standing).toBe("store-ahead-of-app");
    expect(location.mismatch?.direction).toBe("db-ahead-of-build");
    expect(location.mismatch?.olderSide).toBe("build");
    expect(location.mismatch?.from).toBe(LOCATE_COMMAND);
    // Every number in it comes from the CLI, including this build's own.
    expect(location.mismatch?.dbSchemaVersion).toBeGreaterThan(
      location.mismatch?.buildSchemaVersion ?? 0,
    );
    expect(location.mismatch?.says ?? "").not.toBe("");

    // And the switcher's read is the same answer, at no further cost.
    const probe = await probeProject(fixture.project, location);
    expect(probe.located).toBe(true);
    expect(probe.mismatch).toEqual(location.mismatch);
  } finally {
    removeFixture(fixture);
  }
});

test("a directory the server does not serve reports that, rather than an empty store", async () => {
  const fixture = await makeFixture(0);
  try {
    const nowhere = tempDirWithSpace("no project");
    const facts = await readStoreFacts(clientFor(fixture.serve.address, true).project(nowhere));
    expect(facts.ok).toBe(false);
    expect(facts.records).toBeNull();
    // The remedy is named: the CLI's registry writer, for that directory.
    expect(facts.problem ?? "").toContain(`kyo projects add ${nowhere}`);
  } finally {
    removeFixture(fixture);
  }
});

test("a store whose database is missing says so instead of showing zero", async () => {
  const fixture = await makeFixture(1);
  try {
    execFileSync("/bin/sh", ["-c", `rm -f ${shellQuote(dbPathFor(fixture.root))}*`]);
    const facts = await readStoreFacts(fixture.project);
    expect(facts.ok).toBe(false);
    expect(facts.records).toBeNull();
    // The remedy is named, and it is the one that clears this state.
    expect(facts.problem ?? "").toContain("kyo rebuild");
    // The location is still known, because the CLI still answered.
    expect(facts.storeRoot).toBe(fixture.root);
  } finally {
    removeFixture(fixture);
  }
});

// ------------------------------------------------- KYO-111.c3: store currency

test("the four currency states all come back, produced by the CLI", async () => {
  const fixture = await makeFixture(2);
  try {
    const ask = () => readCurrency(fixture.project);

    const inStep = await ask();
    expect(inStep.state).toBe("current");
    expect(inStep.direction).toBe("in-step");
    expect(inStep.problem).toBeNull();
    expect(inStep.from).toBe(CURRENCY_COMMAND);

    setAllocation(fixture, "record", 99);
    const exportAhead = await ask();
    expect(exportAhead.state).toBe("export-ahead");
    expect(exportAhead.direction).toBe("export-ahead");
    expect(exportAhead.remedy).toContain("kyo rebuild");

    setAllocation(fixture, "record", 1);
    const dbAhead = await ask();
    expect(dbAhead.state).toBe("database-ahead");
    expect(dbAhead.direction).toBe("db-ahead");
    expect(dbAhead.remedy).toContain("kyo export --refresh");

    // Back in step, then break it the other way: counters agree, record sets do not.
    setAllocation(fixture, "record", Number(inStepCounter(fixture)));
    hideExportedRecord(fixture, "FIX-2");
    const cannotTell = await ask();
    expect(cannotTell.state).toBe("cannot-tell");
    expect(cannotTell.direction).toBe("cannot-tell");
    expect(cannotTell.says).toContain("cannot tell");

    // The distinction c3 names: "current" and "cannot tell" are different answers,
    // both reached, neither guessed.
    expect(cannotTell.state).not.toBe(inStep.state);
  } finally {
    removeFixture(fixture);
  }
});

/** The `record` counter the fixture had while it was in step. */
function inStepCounter(fixture: Fixture): number {
  return Number(
    sqliteScalar(
      dbPathFor(fixture.root),
      "SELECT next_value FROM allocations WHERE scope = 'record'",
    ),
  );
}

test("the direction map is a lookup, and an unknown direction is not guessed", () => {
  // Every direction `kyosei.currency` can name, mapped once. If the CLI ever adds a
  // fifth, this table does not silently absorb it -- `readCurrency` returns
  // "unrecognised", which is a statement about the app, not about the store.
  expect(Object.keys(DIRECTION_TO_STATE).sort()).toEqual([
    "cannot-tell",
    "db-ahead",
    "export-ahead",
    "in-step",
  ]);
  expect(DIRECTION_TO_STATE["something-new"]).toBeUndefined();
});

test("the app runs no comparison of its own -- the seam words appear only as data", () => {
  // `kyosei.currency` compares `_allocations.json` against the `allocations` table.
  // Nothing in this app may read either: if it did, it would be a second answer to
  // the question `kyo list --json` already answers (KYO-74, law 3).
  const offenders: string[] = [];
  for (const file of everySourceFile(SRC_ROOT)) {
    const text = readFileSync(file, "utf8");
    if (/_allocations\.json|FROM allocations|next_value/.test(text)) offenders.push(file);
  }
  expect(offenders).toEqual([]);
});

test("the userData directory the two builds share is decided, not inherited", () => {
  // KYO-189.c2. The decision is to SHARE, and the manifest is where it would be
  // reversed: setting `productName` gives the packaged build its own directory, its
  // own recents and its own caches, orphaning whatever the user already had. This
  // fails if that line appears, so the reversal is a decision somebody makes rather
  // than a one-line edit nobody notices.
  const manifest = JSON.parse(readFileSync(join(APP_ROOT, "package.json"), "utf8")) as Record<
    string,
    unknown
  >;
  expect(manifest.name).toBe("@kyosei/desktop");
  expect(manifest.productName).toBeUndefined();
  // And the reasoning is where a reader looks for it, not only in a commit message.
  const readme = readFileSync(join(APP_ROOT, "README.md"), "utf8");
  expect(readme).toContain("Two builds, one userData directory");
  expect(readme).toContain("productName");
});

// ------------- KYO-450: an earlier build's command line tools, removed at launch
//
// Every check here is handed its own HOME and its own userData. Nothing below ever
// names a real `~/.local/bin`, `/usr/local/bin` or userData directory: the cleanup
// takes the link directories and the userData as arguments precisely so that it can
// be driven over temporary ones, and a check that reached for the real thing would be
// one bad assertion away from unlinking the developer's own `kyo`.

/** What KYO-315 left on a machine: this app's per-build shims, and links to them. */
function earlierBuildLeftovers(label: string): {
  home: string;
  userData: string;
  linkDirs: string[];
  shim: string;
} {
  const home = tempDirWithSpace(label);
  const userData = join(home, "Library", "Application Support", "@kyosei", "desktop");
  const shim = join(userData, "cli", "0123456789ab");
  mkdirSync(shim, { recursive: true });
  mkdirSync(join(userData, "bin"), { recursive: true });
  for (const name of CLI_NAMES) {
    writeFileSync(join(shim, name), "#!/bin/sh\nexec /gone/kyo-runtime/bin/python3 -m kyosei\n");
    chmodSync(join(shim, name), 0o755);
    writeFileSync(join(userData, "bin", name), "#!/bin/sh\n");
  }
  // The three directories KYO-315 chose between, with `/usr/local/bin` stood in for by
  // a temporary one: the real one is never read here.
  const [local, bin] = retiredCliLinkDirs(home);
  const linkDirs = [local ?? "", bin ?? "", join(home, "usr local bin")];
  for (const dir of linkDirs) mkdirSync(dir, { recursive: true });
  return { home, userData, linkDirs, shim };
}

test("first launch removes only the links into this app's own userData/cli, and says once (KYO-450.c3)", () => {
  const { home, userData, linkDirs, shim } = earlierBuildLeftovers("retired cli");
  const [local, bin, usrLocal] = linkDirs as [string, string, string];
  // Ours: what "Install Command Line Tools…" wrote into ~/.local/bin.
  for (const name of CLI_NAMES) symlinkSync(join(shim, name), join(local, name));
  // Not ours, and every one of them must survive: a `uv tool install`'s real file, a
  // link to somewhere else, a link into ANOTHER userData's cli/ (a second install of
  // this app pointed elsewhere by KYOSEI_USER_DATA), and a link into ours that sits in
  // a directory KYO-315 never linked into.
  writeFileSync(join(bin, "kyo"), "#!/bin/sh\necho uv tool\n");
  const uvTool = join(home, ".local", "share", "uv", "tools", "kyosei", "bin", "kyosei");
  mkdirSync(dirname(uvTool), { recursive: true });
  writeFileSync(uvTool, "#!/bin/sh\n");
  symlinkSync(uvTool, join(bin, "kyosei"));
  const otherUserData = join(home, "other user data", "cli", "fedcba987654");
  mkdirSync(otherUserData, { recursive: true });
  symlinkSync(join(otherUserData, "kyo"), join(usrLocal, "kyo"));
  const elsewhere = join(home, "somewhere else");
  mkdirSync(elsewhere, { recursive: true });
  symlinkSync(join(shim, "kyo"), join(elsewhere, "kyo"));

  const first = removeRetiredCli({ userData, linkDirs });
  expect(first.removedLinks.sort()).toEqual(CLI_NAMES.map((name) => join(local, name)).sort());
  for (const name of CLI_NAMES) expect(existsSync(join(local, name))).toBe(false);
  expect(readFileSync(join(bin, "kyo"), "utf8")).toContain("uv tool");
  expect(lstatSync(join(bin, "kyosei")).isSymbolicLink()).toBe(true);
  expect(lstatSync(join(usrLocal, "kyo")).isSymbolicLink()).toBe(true);
  expect(lstatSync(join(elsewhere, "kyo")).isSymbolicLink()).toBe(true);
  // The shim directories were this app's own files, and go with the links.
  expect(first.removedDirs.sort()).toEqual([join(userData, "bin"), join(userData, "cli")].sort());
  expect(existsSync(join(userData, "cli"))).toBe(false);
  expect(existsSync(join(userData, "bin"))).toBe(false);
  expect(first.problems).toEqual([]);
  // Said: which links went, that nothing else was touched, and the one command.
  expect(first.message).not.toBeNull();
  for (const path of first.removedLinks) expect(first.message).toContain(path);
  expect(first.message).toContain("Nothing else on your PATH was touched");
  expect(first.message).toContain(INSTALL_KYO_COMMAND);

  // ...once: the next launch finds nothing of this app's and says nothing.
  const second = removeRetiredCli({ userData, linkDirs });
  expect(second).toEqual({ removedLinks: [], removedDirs: [], problems: [], message: null });
});

test("a launch with nothing of an earlier build's on PATH says nothing to the reader (KYO-450.c3)", () => {
  const { userData, linkDirs } = earlierBuildLeftovers("no links");
  // A dev build that never ran Install Command Line Tools still had its pane shims;
  // they are removed, and that is this app's own business, not a dialog.
  const quiet = removeRetiredCli({ userData, linkDirs });
  expect(quiet.removedLinks).toEqual([]);
  expect(quiet.removedDirs.length).toBe(2);
  expect(quiet.message).toBeNull();
  // The directories KYO-315 chose between, in its order, from the HOME it is given.
  expect(retiredCliLinkDirs("/Users/somebody")).toEqual([
    "/Users/somebody/.local/bin",
    "/Users/somebody/bin",
    "/usr/local/bin",
  ]);
});

test("the app wires the cleanup at launch and ships no command-line-tools action (KYO-450)", () => {
  const main = readFileSync(join(SRC_ROOT, "main", "main.ts"), "utf8");
  expect(main).toContain("removeEarlierBuildsCli();");
  expect(main).toContain("removeRetiredCli({");
  expect(main).not.toContain('Install Command Line Tools…",');
  // No module writes a `kyo` shim or links one onto PATH any more.
  for (const file of everySourceFile(SRC_ROOT)) {
    const text = readFileSync(file, "utf8");
    expect(text, file).not.toContain("symlinkSync");
    expect(text, file).not.toContain("writeKyoShims");
  }
});

test("both READMEs describe the installed kyo and the claude view's gesture", () => {
  // KYO-450.c4 (KYO-315.c3 before it) and KYO-263.c6: the same commit, and the two
  // places a new user reads.
  // A grep rather than a judgment -- what it buys is that the feature cannot ship with
  // the documents silent about it, which is the failure these criteria name.
  //
  // Read with the line wrapping taken out -- a markdown document wraps at whatever
  // column it wraps at, and a phrase check that passes or fails on where the newline
  // landed is a check about the formatter rather than about what the document says.
  const flatten = (text: string): string => text.replace(/\s+/g, " ");
  const appReadme = flatten(readFileSync(join(APP_ROOT, "README.md"), "utf8"));
  // The root README's desktop section moved, text intact, to its reference page when the
  // README became a one-screen front page (KYO-516); the check follows the section.
  const repoReference = flatten(
    readFileSync(
      join(APP_ROOT, "..", "..", "docs", "reference", "topics", "serve-and-desktop.md"),
      "utf8",
    ),
  );
  for (const [name, text] of [
    ["apps/desktop/README.md", appReadme],
    ["docs/reference/topics/serve-and-desktop.md", repoReference],
  ] as const) {
    // KYO-450.c4: the app runs the `kyo` the user installed, and says how to get one.
    expect(text, `${name} does not name the install command`).toContain("uv tool install kyosei");
    expect(text, `${name} does not say the app ships no kyo of its own`).toMatch(
      /no (Python|runtime|kyo) of its own|ships no (Python|kyo|runtime)/i,
    );
    // The gesture as it now is: a click, on the one form that is clickable. The old
    // wording ("select the id and right-click it") described a menu that can never
    // appear, so a document still carrying it is a document describing a feature this
    // app does not have -- which is why this matches the click rather than only
    // asserting the claude view is mentioned.
    expect(text, `${name} does not say a linked record id opens with a click`).toMatch(
      /click.{0,120}markdown link|markdown link.{0,120}click/is,
    );
    expect(text, `${name} still describes the select-and-right-click gesture`).not.toMatch(
      /select.{0,80}right-click/is,
    );
    expect(text, `${name} does not say what claude.ai does to the link`).toContain("href");
    // The boundary -- a reader who finds a script running in claude.ai has to be able to
    // read what bounds it. Stated in the document's own words, not by citing the
    // decision record (KYO-321), which a public reader cannot open.
    expect(text, `${name} does not state the preload's boundary`).toMatch(
      /preload.{0,200}sandbox|sandbox.{0,200}preload/is,
    );
  }
});

// ---------------------------------------- KYO-378.c7: the tools on a PATH, as `command -v`

test("the headless server finds its tools on the PATH it was given, as `command -v` would (KYO-378.c7)", () => {
  const dir = mkdtempSync(join(tmpdir(), "kyosei tools "));
  try {
    const first = join(dir, "first");
    const second = join(dir, "second");
    mkdirSync(first);
    mkdirSync(second);
    // Not executable in the first directory, executable in the second: the second wins.
    writeFileSync(join(first, "tmux"), "#!/bin/sh\n", "utf8");
    writeFileSync(join(second, "tmux"), "#!/bin/sh\n", { encoding: "utf8", mode: 0o755 });
    writeFileSync(join(first, "claude"), "#!/bin/sh\n", { encoding: "utf8", mode: 0o755 });
    const path = `${first}${delimiter}${second}`;
    expect(findOnPath("tmux", path)).toBe(join(second, "tmux"));
    expect(findOnPath("claude", path)).toBe(join(first, "claude"));
    expect(findOnPath("kyo", path)).toBeNull();
    expect(Object.keys(resolveTools(path))).toEqual([...SERVER_TOOLS]);
    expect(resolveTools(path)).toEqual({
      kyo: null,
      tmux: join(second, "tmux"),
      claude: join(first, "claude"),
    });
    expect(resolveTools("")).toEqual({ kyo: null, tmux: null, claude: null });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the claude view's preload does exactly one thing, and its source says so", () => {
  // KYO-263.c5's own words: a check fails if the boundary is widened. The boundary is
  // KYO-321's and it is about what this file MAY NOT do, so it is asserted against the
  // file's source -- there is no runtime observation of "nothing was exposed" that a
  // check could make from outside the page, and the one it can make from inside is in
  // `links.spec.ts`, against the running app.
  //
  // Read with its comments removed, because the file's own commentary NAMES the things
  // it must not do (that is what a boundary written down looks like) and a grep over
  // the prose would fail on the sentence saying the property holds. Crude on purpose:
  // a `//` inside a string literal would be stripped too, and this file has none.
  const preload = readFileSync(join(SRC_ROOT, "main", "claude-preload.ts"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");

  // Nothing exposed to the page. `contextBridge` is the only API that can put a value
  // in a sandboxed, context-isolated page's world, so its absence IS the property.
  expect(preload, "the claude preload reaches for contextBridge").not.toContain("contextBridge");
  // No DOM mutation: it reads one element's text and writes nothing back.
  for (const write of [
    "innerHTML",
    "outerHTML",
    "textContent =",
    "appendChild",
    "insertBefore",
    "setAttribute",
    "removeAttribute",
    "classList",
    "document.write",
    "createElement",
    "style.",
  ]) {
    expect(preload, `the claude preload writes to the DOM (${write})`).not.toContain(write);
  }
  // One channel, one way. `invoke` would mean a reply the page's world could observe,
  // and a second channel would be a second thing this preload does.
  expect(preload).toContain("ipcRenderer.send(CHANNEL.claudeTextClick");
  expect(preload.match(/ipcRenderer\./g) ?? []).toHaveLength(1);
  expect(preload).not.toContain("ipcRenderer.invoke");
  expect(preload).not.toContain("ipcRenderer.on");
  // The click is read, never taken: the page behaves exactly as it did before.
  expect(preload).not.toContain("preventDefault");
  expect(preload).not.toContain("stopPropagation");

  // ...and it is attached to the claude view and to nothing else, sandboxed, with the
  // build emitting it as its own bundle. Three files have to agree for the feature to
  // exist at all, and each of them is a place it could be silently undone.
  const main = readFileSync(join(SRC_ROOT, "main", "main.ts"), "utf8");
  expect(main).toContain('const CLAUDE_PRELOAD = join(__dirname, "claude-preload.js");');
  // Read out of the claude view's OWN `webPreferences` rather than out of the file, so
  // that "it is this view that is sandboxed and carries this preload" is what is held:
  // the shell's windows have a `preload:` line too, and it is the other file.
  const prefs = main.slice(main.indexOf("partition: CLAUDE_PARTITION"));
  const claudePrefs = prefs.slice(0, prefs.indexOf("});"));
  expect(claudePrefs).toContain("preload: CLAUDE_PRELOAD,");
  expect(claudePrefs).toContain("sandbox: true,");
  expect(claudePrefs).toContain("contextIsolation: true,");
  expect(claudePrefs).not.toContain("preload: PRELOAD,");
  const buildScript = readFileSync(join(APP_ROOT, "build.mjs"), "utf8");
  expect(buildScript, "build.mjs does not bundle the claude preload").toContain(
    'entryPoints: [join(src, "main", "claude-preload.ts")]',
  );
  expect(buildScript).toContain('outfile: join(dist, "main", "claude-preload.js")');

  // And the menu that KYO-263 shipped first is gone rather than left beside it: it can
  // never appear, because claude.ai raises its own context menu (KYO-263.c3).
  expect(main, "the claude view still registers a context-menu listener").not.toContain(
    'webContents.on("context-menu"',
  );
});

// ----------------------------------- KYO-185: a remedy its reader can perform
//
// KYO-229 gives every one of these functions a third argument -- the login shell's
// environment -- and the two branches it selects are not interchangeable: with a
// capture the app has searched the reader's own PATH and says so, and without one it
// is back in exactly the position KYO-185 wrote these sentences for. So the checks
// below state which world they are in rather than inheriting it from the machine
// running them, for the same reason `envFor` states `KYOSEI_TMUX` on every launch in
// `smoke.spec.ts`: this process has a `$SHELL`, so a defaulted argument here would
// silently test the OTHER branch on a developer's desk.

/** A login-shell capture that did not happen, so KYO-185's wording is the one under test. */
const NO_CAPTURE: ShellEnvironment = {
  env: null,
  shell: "/bin/zsh",
  outcome: "shell-would-not-run",
  why: "this check says so",
  tookMs: 0,
};

/** A capture that did, carrying a PATH with nothing on it -- KYO-229's wording. */
function withCapture(path: string, shell = "/bin/zsh"): ShellEnvironment {
  return {
    env: { PATH: path },
    shell,
    outcome: "captured",
    why: "this check says so",
    tookMs: 1,
  };
}

test("the no-tmux message names the instrument the reader actually has", () => {
  const terminal = { tty: true, platform: "darwin" };
  const finder = { tty: false, platform: "darwin" };

  // Told apart from how the process was started, never by asking (c2).
  expect(launchOrigin(terminal)).toBe("terminal");
  expect(launchOrigin(finder)).toBe("gui");

  const fromShell = resolveTmux({ PATH: "/nowhere at all" }, terminal, NO_CAPTURE).reason ?? "";
  const fromFinder = resolveTmux({ PATH: "/nowhere at all" }, finder, NO_CAPTURE).reason ?? "";

  // A shell is in the loop: the export is a real remedy and stays.
  expect(fromShell).toContain(`export ${TMUX_ENV}=`);
  expect(fromShell).not.toContain("launchctl");

  // No shell is in the loop: the reader is told the form that works from the Finder,
  // and told to relaunch, because that is what `launchctl setenv` requires.
  expect(fromFinder).toContain(`launchctl setenv ${TMUX_ENV}`);
  expect(fromFinder).not.toContain(`export ${TMUX_ENV}=`);
  expect(fromFinder).toContain("relaunch");

  // c4: the two things "no tmux" can mean stay distinguishable, and the GUI message
  // is the one that must not claim to know which -- its PATH is not the reader's.
  expect(fromFinder).toContain("different thing from tmux not being installed");
  expect(fromShell).toContain("not installed, or it is not on the PATH you launched from");

  // c4 again, the other half: nothing anywhere synthesises a PATH. Those two
  // directories appear in a sentence addressed to a person and nowhere else.
  for (const file of everySourceFile(SRC_ROOT)) {
    const text = readFileSync(file, "utf8");
    for (const guess of ["/opt/homebrew/bin", "/usr/local/bin"]) {
      if (!text.includes(guess)) continue;
      // Present only inside `noTmuxReason`'s prose, never assigned to a PATH.
      expect(new RegExp(`PATH[^\\n]*${guess}`).test(text), `${file} builds a PATH`).toBe(false);
    }
  }

  // Off, and mis-named: both name an *undo*, and both get the right instrument too.
  expect(resolveTmux({ [TMUX_ENV]: "off" }, finder, NO_CAPTURE).reason ?? "").toContain(
    `launchctl unsetenv ${TMUX_ENV}`,
  );
  expect(resolveTmux({ [TMUX_ENV]: "off" }, terminal, NO_CAPTURE).reason ?? "").toContain(
    `unset ${TMUX_ENV}`,
  );

  // A platform with no `launchctl` is told what has to be true rather than a command
  // that does not exist there.
  const linuxDesktop = resolveTmux(
    { PATH: "/nowhere at all" },
    { tty: false, platform: "linux" },
    NO_CAPTURE,
  );
  expect(linuxDesktop.reason ?? "").not.toContain("launchctl");
  expect(linuxDesktop.reason ?? "").toContain("environment this app is launched from");
});

// ------------------------------- KYO-229: what the app is entitled to claim, and why

test("with the login shell's environment in hand the message stops hedging", () => {
  const finder = { tty: false, platform: "darwin" };
  const empty = { PATH: "/nowhere at all" };

  // KYO-185's sentence, unchanged, for the launch that has no capture: the app has
  // searched an environment it cannot call the reader's, and says which of the two
  // things it cannot tell apart.
  const hedged = resolveTmux(empty, finder, NO_CAPTURE).reason ?? "";
  expect(hedged).toContain("different thing from tmux not being installed");

  // With a capture the PATH searched IS the reader's, so the hedge would now be a
  // false modesty rather than honesty -- and c3's two sentences are both there.
  const sure = resolveTmux(empty, finder, withCapture("/nowhere at all", "/bin/fish")).reason ?? "";
  expect(sure).not.toContain("different thing from tmux not being installed");
  expect(sure).toContain("/bin/fish");
  expect(sure).toContain("brew install tmux");
  expect(sure).toContain("ends when this app quits");

  // ...and the reader is still told how to point the app at a tmux the shell cannot
  // see, because "installed somewhere else" is a real state and KYO-185's variable is
  // still the answer to it.
  expect(sure).toContain(TMUX_ENV);

  // The remedy clears the error, on the standing rule: a tmux on the CAPTURED PATH is
  // found, with no `KYOSEI_TMUX` set anywhere (c1's "without any KYOSEI_TMUX setting").
  const dir = tempDirWithSpace("captured tmux");
  try {
    const tmux = join(dir, "tmux");
    writeFileSync(tmux, "#!/bin/sh\nexit 0\n", "utf8");
    chmodSync(tmux, 0o755);
    const environment = withCapture(dir);
    // The environment a pty would be spawned with: the captured PATH wins over the
    // minimal one this "launch" carries, and every other variable of the launch's own
    // survives -- which is what keeps `KYOSEI_TMUX` and friends working.
    const merged = terminalEnvironment(
      { PATH: "/usr/bin:/bin", KYOSEI_SOMETHING: "kept" },
      environment,
    );
    expect(merged.PATH).toBe(dir);
    expect(merged.KYOSEI_SOMETHING).toBe("kept");
    const found = resolveTmux(merged, finder, environment);
    expect(found.binary).toBe(tmux);
    expect(found.reason).toBeNull();
    expect(planTmux("FIX", null, merged, finder, environment).session).toBe("FIX");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the login shell is asked once, for its environment, and nothing else", () => {
  // The command is `$SHELL -ilc <one constant script>` and every clause of that is
  // load-bearing: `-l` sources the profile a terminal window sources, `-i` sources
  // the interactive files where Homebrew's shellenv, nvm and pyenv actually live, and
  // the script is a CONSTANT -- nothing is interpolated into it, so no project path
  // ever reaches a shell here.
  expect(LOGIN_SHELL_FLAGS).toBe("-ilc");
  expect(ENV_SCRIPT).toContain("/usr/bin/env");
  expect(ENV_SCRIPT).toContain(ENV_BEGIN);
  expect(ENV_SCRIPT).toContain(ENV_END);
  expect(ENV_SCRIPT).not.toMatch(/\$\{/);

  // A real shell, asked for real: the one place this is measured rather than reasoned
  // about. `/bin/sh` because it is on every machine this can run on.
  const real = captureLoginEnvironment({ [LOGIN_SHELL_ENV]: "/bin/sh" });
  expect(real.outcome).toBe("captured");
  expect(real.shell).toBe("/bin/sh");
  expect(real.env?.PATH ?? "").not.toBe("");
  expect(real.why).not.toBe("");

  // Turned off by the same word `KYOSEI_TMUX` takes, and the outcome says so rather
  // than looking like a failure.
  const off = captureLoginEnvironment({ [LOGIN_SHELL_ENV]: "off", SHELL: "/bin/sh" });
  expect(off.outcome).toBe("turned-off");
  expect(off.env).toBeNull();
  expect(off.why).toContain(LOGIN_SHELL_ENV);

  // A shell that will not run is an outcome carrying a sentence, never a throw: the
  // app still has to open a pane on that machine.
  const broken = captureLoginEnvironment({ [LOGIN_SHELL_ENV]: "/no/such/shell at all" });
  expect(broken.outcome).toBe("shell-would-not-run");
  expect(broken.env).toBeNull();
  expect(broken.why).toContain("/no/such/shell at all");

  // A shell that HANGS is a different outcome from one that will not run, because the
  // two need different sentences -- one of them names a startup file to go and look
  // at. Driven with a stub that ignores its arguments and sleeps, which is what a
  // `.zshrc` blocking on a network mount looks like from here.
  const slowDir = tempDirWithSpace("slow login shell");
  try {
    const slow = join(slowDir, "shell");
    writeFileSync(slow, "#!/bin/sh\nsleep 30\n", "utf8");
    chmodSync(slow, 0o755);
    const hangs = captureLoginEnvironment({ [LOGIN_SHELL_ENV]: slow }, { timeoutMs: 300 });
    expect(hangs.outcome).toBe("timed-out");
    expect(hangs.env).toBeNull();
    expect(hangs.why).toContain("startup files");
    // Bounded: a profile that never returns costs the reader the capture, not the app.
    expect(hangs.tookMs).toBeLessThan(10_000);
  } finally {
    rmSync(slowDir, { recursive: true, force: true });
  }
  expect([...SHELL_ENV_OUTCOMES]).toContain("timed-out");

  // A shell that runs and prints its own chatter around the block is read correctly:
  // only what sits between the markers is the environment.
  const noisy = `motd line\n${ENV_BEGIN}\nA=1\nB=two\nlines\n${ENV_END}\ntrailing\n`;
  expect(parseEnvBlock(between(noisy) ?? "")).toEqual({ A: "1", B: "two\nlines" });
  // ...and a run that produced neither marker is an outcome rather than a parse of
  // whatever came back.
  expect(between("nothing useful at all")).toBeNull();
});

// --------------------- KYO-266: one bad second must not cost the whole app session

/**
 * A login shell that fails the first time it is asked and answers every time after.
 *
 * Which is the failure KYO-266 records: a capture came back with nothing while the
 * machine was short of memory, and the same shell answered in under a second from a
 * terminal minutes later. It counts its own runs in a file
 * beside itself, so a check can say how many shells were actually started rather than
 * inferring it from the answers.
 */
function shellThatFailsOnce(dir: string, recoveredPath: string): string {
  const shell = join(dir, "stub login shell");
  writeFileSync(
    shell,
    [
      "#!/bin/sh",
      'runs="$0.runs"',
      'n=$(cat "$runs" 2>/dev/null || echo 0)',
      "n=$((n + 1))",
      'printf %s "$n" > "$runs"',
      // The failed first ask: no environment, no markers, a non-zero exit.
      'if [ "$n" = 1 ]; then exit 1; fi',
      `printf '%s\\n' ${ENV_BEGIN}`,
      `printf 'PATH=%s\\n' ${shellQuote(recoveredPath)}`,
      `printf '%s\\n' ${ENV_END}`,
      "",
    ].join("\n"),
    "utf8",
  );
  chmodSync(shell, 0o755);
  return shell;
}

test("a capture that failed is asked again, and a pane after it is planned from the answer", () => {
  // KYO-266.c1 and c2, in the order the app meets them: the ask, and then the seam a
  // pane is planned at. Driven with a stub shell rather than a stub capture, because
  // the thing under test is the rule the app runs `captureLoginEnvironment` under.
  const dir = tempDirWithSpace("login shell retry");
  try {
    const recoveredBin = join(dir, "recovered bin");
    mkdirSync(recoveredBin, { recursive: true });
    const tmux = join(recoveredBin, "tmux");
    writeFileSync(tmux, "#!/bin/sh\nexit 0\n", "utf8");
    chmodSync(tmux, 0o755);
    const shell = shellThatFailsOnce(dir, recoveredBin);
    const ask = retryingCapture(() => captureLoginEnvironment({ [LOGIN_SHELL_ENV]: shell }));

    // The failure. Before this record it was the app's answer for the rest of its life.
    const first = ask();
    expect(captured(first)).toBe(false);
    expect(first.outcome).toBe("shell-would-not-run");
    expect(worthAskingAgain(first)).toBe(true);

    // c1: the next caller gets a shell asked again, not the cached failure.
    const second = ask();
    expect(captured(second)).toBe(true);
    expect(second.env?.PATH).toBe(recoveredBin);

    // ...and a SUCCESS is still kept for the process, exactly as KYO-229 left it: the
    // third ask starts no shell at all, which the stub's own counter says.
    const third = ask();
    expect(third).toBe(second);
    expect(readFileSync(`${shell}.runs`, "utf8")).toBe("2");

    // c2: what a pane opened after the recovery is planned from. The launch
    // environment stands in for `launchd`'s -- an empty directory rather than the
    // literal four, so the assertion is about this app and not about whether the
    // machine running the check happens to have a tmux in `/usr/bin`.
    const launchBin = join(dir, "launchd minimum");
    mkdirSync(launchBin, { recursive: true });
    const launch = { PATH: launchBin, KYOSEI_SOMETHING: "kept" };
    const finder = { tty: false, platform: "darwin" };

    const bare = terminalEnvironment(launch, first);
    expect(bare.PATH).toBe(launchBin);
    const before = planTmux("FIX", null, bare, finder, first);
    expect(before.binary).toBeNull();
    expect(before.session).toBeNull();

    const recovered = terminalEnvironment(launch, second);
    // The recovered capture's PATH, not the launch's -- and everything the launch was
    // told to carry is still carried.
    expect(recovered.PATH).toBe(recoveredBin);
    expect(recovered.KYOSEI_SOMETHING).toBe("kept");
    const after = planTmux("FIX", null, recovered, finder, second);
    expect(after.binary).toBe(tmux);
    expect(after.session).toBe("FIX");
    expect(after.reason).toBeNull();
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the pane notice for a failed capture names the next pane, not a relaunch", () => {
  // KYO-266.c3. The earlier wording sent a reader to `.zshrc` and then to a relaunch,
  // because those were the only two things it talked about.
  const finder = { tty: false, platform: "darwin" };
  const terminal = { tty: true, platform: "darwin" };

  for (const facts of [finder, terminal]) {
    const reason = noTmuxReason(facts, NO_CAPTURE);
    expect(reason).toContain(WILL_ASK_AGAIN);
    // ...and it reaches the surface a reader actually has, which is the pane notice.
    expect(noticeFor("bare", null, reason)).toContain(WILL_ASK_AGAIN);
  }
  // The remedy is a pane, and the expensive one is explicitly not required.
  expect(WILL_ASK_AGAIN).toContain("open another pane");
  expect(WILL_ASK_AGAIN).toContain("do not have to relaunch the app");

  // The sentence is not a claim the app is free to make: it is promised for exactly
  // the outcomes the memo re-asks, and both read `worthAskingAgain`. Every outcome,
  // both halves, so neither can drift away from the other.
  for (const outcome of SHELL_ENV_OUTCOMES) {
    const environment: ShellEnvironment = {
      env: null,
      shell: "/bin/zsh",
      outcome,
      why: "this check says so",
      tookMs: 0,
    };
    let asks = 0;
    const ask = retryingCapture(() => {
      asks += 1;
      return environment;
    });
    ask();
    ask();
    expect(asks === 2, `${outcome} is re-asked`).toBe(worthAskingAgain(environment));
    expect(noTmuxReason(finder, environment).includes(WILL_ASK_AGAIN), `${outcome} says so`).toBe(
      worthAskingAgain(environment),
    );
  }

  // The rule, written out: the outcomes where a shell was actually tried. The two
  // that are absent are decided by this process's own environment and by the
  // platform, and their own sentences already name the change that helps.
  expect([...RETRIABLE_OUTCOMES].sort()).toEqual([
    "shell-said-nothing-we-could-read",
    "shell-would-not-run",
    "timed-out",
  ]);
  expect(noTmuxReason(finder, withCapture("/nowhere at all"))).not.toContain(WILL_ASK_AGAIN);
});

test("what the capture did is written where a Finder launch keeps it", () => {
  // KYO-266.c4. The outcome went to stdout, which a Finder launch discards -- so the
  // one launch whose PATH is worth asking about is the one that kept no record, and
  // the diagnosis this record came from had to infer a timeout from the clock.
  const dir = tempDirWithSpace("user data");
  try {
    const path = shellEnvLogPath(dir);
    expect(basename(path)).toBe("shell-env.log");
    const failed: ShellEnvironment = {
      env: null,
      shell: "/bin/zsh",
      outcome: "timed-out",
      why: "this check says so",
      tookMs: 3001,
    };
    const worked: ShellEnvironment = {
      env: { PATH: "/recovered", API_TOKEN: "hunter2" },
      shell: "/bin/zsh",
      outcome: "captured",
      why: "this check says so",
      tookMs: 812,
    };
    expect(appendShellEnvLog(dir, failed, 1, new Date("2026-01-01T09:00:00.000Z"))).toBeNull();
    expect(appendShellEnvLog(dir, worked, 2, new Date("2026-01-01T09:28:00.000Z"))).toBeNull();

    const entries = readFileSync(path, "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
    // Appended, so the failed ask and the recovery are both there in order -- which is
    // the shape of this record itself, unreadable from one line.
    expect(entries.map((entry) => entry.outcome)).toEqual(["timed-out", "captured"]);
    expect(entries[1]).toEqual({
      at: "2026-01-01T09:28:00.000Z",
      outcome: "captured",
      shell: "/bin/zsh",
      tookMs: 812,
      attempt: 2,
    });
    // No variable's VALUE, on any outcome: what this file reads is the likeliest place
    // on a developer's machine for a token to be, and a log that never holds one
    // cannot leak one.
    expect(readFileSync(path, "utf8")).not.toContain("hunter2");

    // Bounded, because the retry made it per-pane rather than per-launch on a machine
    // whose shell stays broken. The newest lines are the ones kept.
    const line = shellEnvLogLine(failed, 1);
    writeFileSync(path, line.repeat(Math.ceil(SHELL_ENV_LOG_MAX_BYTES / line.length) + 10), "utf8");
    expect(statSync(path).size).toBeGreaterThan(SHELL_ENV_LOG_MAX_BYTES);
    expect(appendShellEnvLog(dir, worked, 3)).toBeNull();
    const kept = readFileSync(path, "utf8").trim().split("\n");
    expect(kept.length).toBe(SHELL_ENV_LOG_KEEP_LINES + 1);
    expect(JSON.parse(kept[kept.length - 1] as string).attempt).toBe(3);

    // A directory it cannot write costs the next diagnosis its history and this
    // reader nothing: the problem comes back, it is never thrown.
    const blocked = join(dir, "not a directory");
    writeFileSync(blocked, "", "utf8");
    expect(appendShellEnvLog(blocked, failed, 1)).not.toBeNull();

    // And where it lands is wired to the only file that can ask Electron for
    // `userData`, which is checked in the source because it cannot be checked here.
    expect(readFileSync(join(SRC_ROOT, "main", "main.ts"), "utf8")).toContain(
      'logShellEnvironmentIn(app.getPath("userData"))',
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("nothing in this app ends the tmux session it attached to", () => {
  // KYO-229.c2 stated as a mechanism rather than as an absence. The app owns the
  // per-project session -- it starts it, attaches, re-attaches on a project switch --
  // and owning it means nothing here takes it down. `endSessions` kills this app's own
  // PTYs, which in tmux mode are CLIENTS, and that is a detach.
  expect([...TMUX_SUBCOMMANDS_REFUSED]).toContain("kill-session");
  for (const file of everySourceFile(SRC_ROOT)) {
    const text = readFileSync(file, "utf8");
    for (const refused of TMUX_SUBCOMMANDS_REFUSED) {
      // Named in `TMUX_SUBCOMMANDS_REFUSED`'s own declaration and its comment, which
      // is the one place the words are allowed to appear.
      if (file === join(SRC_ROOT, "main", "terminal.ts")) continue;
      expect(text.includes(refused), `${file} names \`tmux ${refused}\``).toBe(false);
    }
  }
  // And the pane says so, so a reader can tell the two shells apart before they trust
  // one with a controller session.
  const notice = noticeFor("tmux", "kyosei-a-project", null);
  expect(notice).toContain("never ends it");
  expect(notice).toContain("quitting");
});

test("the remedy the GUI message names clears the error it is printed for", () => {
  // KYO-185.c3 -- the standing rule ("every error message naming a remedy has a test
  // proving the remedy clears the error"), applied to the audience it had never been
  // applied to.
  //
  // The boundary, stated rather than papered over: `launchctl setenv KYOSEI_TMUX X`
  // is not run here. It is user-global state -- it would set that variable for every
  // GUI application this user launches afterwards, and a failed run would leave it
  // set -- so what is executed is its EFFECT, which is the whole of what it does:
  // `KYOSEI_TMUX=X` in the environment of a subsequently launched GUI app. What is
  // NOT assumed is the content: the variable name and the value are parsed back out
  // of the sentence the app printed, so a message naming the wrong variable, or the
  // wrong path, fails here.
  const dir = tempDirWithSpace("tmux elsewhere");
  try {
    const tmux = join(dir, "tmux");
    writeFileSync(tmux, "#!/bin/sh\nexit 0\n", "utf8");
    chmodSync(tmux, 0o755);
    const finder = { tty: false, platform: "darwin" };
    // A PATH with no tmux on it: the Finder-launched case, and the error.
    const empty = { PATH: join(dir, "nothing here") };

    const broken = resolveTmux(empty, finder);
    expect(broken.binary).toBeNull();
    expect(planTmux("FIX", null, empty, finder).session).toBeNull();

    // Read the remedy off the message, exactly as a person would.
    const named = /launchctl setenv ([^\s`]+) ([^\s`]+)/.exec(broken.reason ?? "");
    expect(named, "the GUI message names no `launchctl setenv` to follow").not.toBeNull();
    const [, variable, placeholder] = named ?? [];
    expect(variable).toBe(TMUX_ENV);
    expect(placeholder).toBe(TMUX_PATH_PLACEHOLDER);

    // Follow it, with the real path in place of the placeholder the message shows.
    const afterRelaunch = { ...empty, [String(variable)]: tmux };
    const fixed = resolveTmux(afterRelaunch, finder);
    expect(fixed.binary).toBe(tmux);
    expect(fixed.reason).toBeNull();
    expect(planTmux("FIX", null, afterRelaunch, finder).session).toBe("FIX");

    // The terminal case's remedy still clears it too -- the half that was already
    // covered, kept covered beside the half that was not.
    const fromShell = resolveTmux(empty, { tty: true, platform: "darwin" });
    const exported = /export (\S+)=(\S+)/.exec(fromShell.reason ?? "");
    expect(exported).not.toBeNull();
    expect(exported?.[1]).toBe(TMUX_ENV);
    expect(
      resolveTmux({ ...empty, [TMUX_ENV]: tmux }, { tty: true, platform: "darwin" }).binary,
    ).toBe(tmux);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------- the repo it runs in

test("against this repository the CLI reports the checkout it was resolved from", async () => {
  const resolution = testResolution();
  const root = resolution.cliRoot ?? APP_ROOT;
  const served = await serveCheckout(root);
  try {
    const location = await locateProject(served.project);
    expect(location.problem).toBeNull();
    // The code is always this checkout's; the store is too, except in a dispatch
    // worktree, where KYO-342 reads it through the main checkout (KYO-371).
    const store = storeRootFor(root);
    expect(location.storeRoot).toBe(store);
    expect(location.repoRoot).toBe(root);
    expect(location.branch ?? "").not.toBe("");
    expect(LOCATE_COMMAND).toContain("kyo where");
    // The locate is read-only: it opens no store and writes nothing. Watched in this
    // checkout only: from a worktree the main checkout's `kyosei/` is a live store its
    // controller may be writing (a `-wal` appearing mid-check is not this locate).
    const before = readdirSync(join(store, "kyosei"));
    await locateProject(served.project);
    expect(readdirSync(join(store, "kyosei"))).toEqual(before);
  } finally {
    await served.stop();
  }
});

/**
 * A kyo serve of this check's own over THIS checkout (KYO-459): a throwaway store home
 * whose registry lists it, so the real `~/kyosei` and the real server are never asked.
 * The store is read read-only, as kyo serve reads every store.
 */
async function serveCheckout(
  root: string,
): Promise<{ project: ServedProject; stop: () => Promise<void> }> {
  const home = tempDirWithSpace("checkout home");
  writeFileSync(join(home, "projects.json"), `${JSON.stringify({ projects: [{ root }] })}\n`);
  const serve = await startKyoServe(home);
  return {
    project: clientFor(serve.address, true).project(root),
    stop: async () => {
      await stopKyoServe(serve);
      rmSync(home, { recursive: true, force: true });
    },
  };
}

test("findCliRoot stops at the checkout and returns null outside one", () => {
  expect(findCliRoot(__dirname)).toBe(testResolution().cliRoot);
  expect(findCliRoot(tempDirWithSpace("outside"))).toBeNull();
});

test("runKyo starts no shell: an argv word is never re-split", async () => {
  const fixture = await makeFixture(1);
  try {
    // A title a shell would mangle: `$HOME` would expand, the quotes would vanish
    // and the glob would match. It is stored verbatim, because there is no shell
    // between the argv array and the process.
    const title = "a title with $HOME and 'quotes' and * in it";
    await runKyo(fixture.resolution, ["new", "bug", "--title", title], { cwd: fixture.root });
    const shown = await runKyoJson(fixture.resolution, ["show", "FIX-2"], { cwd: fixture.root });
    expect(shown.data?.title).toBe(title);
  } finally {
    removeFixture(fixture);
  }
});

// ================================================== records mode (KYO-112)

// ------------------------------------- KYO-112.c6: records mode constructs no write

test("the records-mode allowlist admits seven read commands and no writer", () => {
  // An allowlist, not a denylist: a command the CLI grows tomorrow is refused here
  // until somebody adds it on purpose, which is the property "read-only to a human"
  // needs. The seven are the ones this mode's surfaces actually run; `list` is
  // `kyo base`'s engine with no base named (KYO-365), and `briefs`, `shift-notes` and
  // `gates` are the ledgers the app read by SQL until KYO-459 (KYO-454's commands).
  expect([...RECORDS_READ_COMMANDS].sort()).toEqual([
    "base",
    "briefs",
    "gates",
    "list",
    "next",
    "shift-notes",
    "show",
  ]);
  for (const command of RECORDS_READ_COMMANDS) {
    expect(KYO_WRITING_COMMANDS).not.toContain(command);
  }
});

test("every command that writes is refused by name, `brief` loudest of all", () => {
  for (const command of ["new", "edit", "move", "gate", "claim", "verify", "report", "sweep"]) {
    let message = "";
    try {
      assertRecordsRead([command, "FIX-1"]);
    } catch (error) {
      message = (error as Error).message;
    }
    expect(message, `\`kyo ${command}\` was not refused`).toContain(
      `may not run \`kyo ${command}\``,
    );
    expect(message).toContain("writes to the store");
  }
  // `kyo brief` is the trap this check exists for. It reads like a report, it is the
  // surface a controller is told to read instead of the records, and every run of it
  // INSERTs a row in `brief_runs` -- so it is the one command records mode most
  // needs to not call, and the one most likely to be added by mistake.
  expect(KYO_WRITING_COMMANDS).toContain("brief");
  let briefRefusal = "";
  try {
    assertRecordsRead(["brief"]);
  } catch (error) {
    briefRefusal = (error as Error).message;
  }
  expect(briefRefusal).toContain("may not run `kyo brief`");
});

test("`kyo brief` really does write, and `kyo next` really does not", async () => {
  // The premise behind the allowlist, checked against the tool rather than asserted
  // from a reading of it. If `kyo brief` ever stops writing, this fails and the
  // comment justifying the whole calendar-tool design becomes reviewable again.
  const fixture = await makeFixture(1);
  try {
    const briefRuns = () =>
      Number(sqliteScalar(dbPathFor(fixture.root), "SELECT count(*) AS n FROM brief_runs"));
    const before = briefRuns();
    await runKyo(fixture.resolution, ["brief", "--json"], { cwd: fixture.root });
    expect(briefRuns(), "`kyo brief` did not write a brief_runs row").toBe(before + 1);

    const events = () =>
      Number(sqliteScalar(dbPathFor(fixture.root), "SELECT count(*) AS n FROM events"));
    const eventsBefore = events();
    const afterBrief = briefRuns();
    await runKyoJson(fixture.resolution, ["next"], { cwd: fixture.root });
    expect(briefRuns()).toBe(afterBrief);
    expect(events()).toBe(eventsBefore);
  } finally {
    removeFixture(fixture);
  }
});

test("no records-mode read reaches `kyo` except through the allowlist", async () => {
  // The guard is at the one place arguments are assembled, so this drives the real
  // readers and asserts they only ever produced allowlisted argv -- rather than
  // grepping for call sites, which would pass the day somebody adds a new one.
  const fixture = await makeRecordsFixture();
  try {
    const before = readdirSync(join(fixture.root, "kyosei", "export"));
    const eventCount = () =>
      sqliteScalar(dbPathFor(fixture.root), "SELECT count(*) AS n FROM events");
    const eventsBefore = eventCount();

    await readBases(fixture.project);
    await runBase(fixture.project, "everything");
    await readBoardClasses(fixture.project);
    await readRecordTree(fixture.project, "kind");
    await readRecord(fixture.project, "FIX-1");
    await readBriefs(fixture.project);
    await readBrief(fixture.project, "FIX-B2");

    // Every one of those is a read: the ledger did not move and the export did not
    // change. `kyo brief` in the same position would have failed both.
    expect(eventCount()).toBe(eventsBefore);
    expect(readdirSync(join(fixture.root, "kyosei", "export"))).toEqual(before);
  } finally {
    removeFixture(fixture);
  }
});

test("the tree delegates every ribbon grouping to the engine, not just the declared one", async () => {
  // KYO-120.c3. `everything` declares `group-by: kind`; before the flag, that was the
  // only one of the ribbon's three the tree could delegate and the other two were the
  // app's port. Now all three are `kyo base run --group-by`, so the tree's answer no
  // longer changes implementation depending on which pill the reader pressed.
  const fixture = await makeRecordsFixture();
  try {
    for (const grouping of TREE_GROUPINGS) {
      const tree = await readRecordTree(fixture.project, grouping);
      expect(tree.ok, tree.refusal ?? "").toBe(true);
      expect(tree.groups.length, `${grouping} produced no groups`).toBeGreaterThan(0);
      // The receipt: the command the tree actually ran carries the ribbon's field --
      // `groupingField`, which for the status pill is `standing` and not the pill's
      // own word (KYO-121).
      expect(tree.command, `${grouping} was not asked of the engine`).toContain(
        `--group-by ${groupingField(grouping)}`,
      );
      // And it is the engine's answer, not a coincidence: a run of a saved base
      // declaring that same grouping produces the identical array.
      const declared = { kind: "everything", status: "by-standing", scope: "by-scope" }[grouping];
      const engine = await runBase(fixture.project, declared);
      expect(engine.ok, engine.refusal ?? "").toBe(true);
      expect(tree.groups, `${grouping} disagrees with the base that declares it`).toEqual(
        engine.groups,
      );
    }
  } finally {
    removeFixture(fixture);
  }
});

test("a grouping the engine refuses is the tree's refusal, not a grouping done anyway", async () => {
  // The other half of delegating: the tree has no fallback rule of its own any more,
  // so an engine refusal has to arrive as a refusal. `gate_tags` is the field the
  // engine refuses (a row with two tags belongs to two groups) and it reaches
  // `runBase` here directly, since the ribbon's own three are all legal.
  const fixture = await makeRecordsFixture();
  try {
    const refused = await runBase(fixture.project, "everything", "gate_tags");
    expect(refused.ok).toBe(false);
    expect(refused.groups).toBeNull();
    // The CLI's own sentence, verbatim -- the app writes no second wording of it.
    expect(refused.refusal).toContain("gate_tags");
    expect(refused.command).toContain("--group-by gate_tags");
  } finally {
    removeFixture(fixture);
  }
});

// ---------------------------------------- KYO-112.c4: the Base engine and refusals

test("the bases come from the CLI's own listing of bases.yml", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const catalogue = await readBases(fixture.project);
    expect(catalogue.ok).toBe(true);
    expect(catalogue.bases.map((base) => base.name).sort()).toEqual(fixture.bases);
    const everything = catalogue.bases.find((base) => base.name === "everything");
    expect(everything?.groupBy).toBe("kind");
    expect(everything?.source).toBe("records");
    expect(everything?.columns).toContain("gates_open");
    const openWork = catalogue.bases.find((base) => base.name === "open-work");
    expect(openWork?.filters).toEqual({ status: "open" });
  } finally {
    removeFixture(fixture);
  }
});

test("a base the CLI refuses comes back with the CLI's message, not an empty view", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const refused = await runBase(fixture.project, "no-such-base");
    expect(refused.ok).toBe(false);
    expect(refused.rows).toEqual([]);
    // Verbatim, and the parts that make it actionable are all still in it: the
    // declared names, and the entry that would make this query legal.
    expect(refused.refusal ?? "").toContain("no-such-base");
    expect(refused.refusal ?? "").toContain("bases.yml");
    for (const name of fixture.bases) expect(refused.refusal ?? "").toContain(name);
    expect(refused.refusal ?? "").not.toBe("");
  } finally {
    removeFixture(fixture);
  }
});

test("a bases.yml the CLI cannot read is reported as the CLI reported it", async () => {
  const fixture = await makeRecordsFixture();
  try {
    writeStoreFile(fixture, "bases.yml", "bases:\n  broken:\n    source: records\n");
    const catalogue = await readBases(fixture.project);
    expect(catalogue.ok).toBe(false);
    expect(catalogue.bases).toEqual([]);
    expect(catalogue.refusal ?? "").not.toBe("");
    // The CLI names the missing key; a paraphrase would lose the remedy.
    expect(catalogue.refusal ?? "").toContain("summary");
  } finally {
    removeFixture(fixture);
  }
});

// ------------------------------------------- KYO-112.c5: the record, read-only

test("a record comes back with its gates, claims, criteria and dispatches", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const detail = await readRecord(fixture.project, "FIX-1");
    expect(detail.ok).toBe(true);
    expect(detail.kind).toBe("bug");
    expect(detail.scope).toBe("alpha");
    expect(detail.gates.length).toBe(1);
    expect(detail.gates[0]?.open).toBe(true);
    expect(detail.gates[0]?.detail).toContain("upstream release");
    expect(detail.claims.length).toBe(1);
    expect(detail.claims[0]?.level).toBe("verified");
    expect(detail.criteria.length).toBe(2);

    const missing = await readRecord(fixture.project, "FIX-999");
    expect(missing.ok).toBe(false);
    expect(missing.refusal ?? "").not.toBe("");
  } finally {
    removeFixture(fixture);
  }
});

test("finished-but-not-moved is its own class, off the CLI's own computation", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const board = await readBoardClasses(fixture.project);
    expect(board.problem).toBeNull();

    // The fixture puts FIX-3 in exactly KYO-87's state and FIX-5 in `ready`, so the
    // distinction this criterion is about is asserted over two real records rather
    // than over an empty set. FIX-4 is KYO-570's `blocked` -- FIX-1, still open,
    // `blocks` it -- and KYO-571 is the app reading that as its own class rather than
    // letting it fall to `none`.
    expect([...board.settledNotMoved]).toEqual(["FIX-3"]);
    expect([...board.ready]).toEqual(["FIX-5"]);
    expect([...board.blocked]).toEqual(["FIX-4"]);
    expect(classOf(board, "FIX-3")).toBe("settled-not-moved");
    expect(classOf(board, "FIX-5")).toBe("ready");
    expect(classOf(board, "FIX-4")).toBe("blocked");
    expect(classOf(board, "FIX-3")).not.toBe(classOf(board, "FIX-5"));
    // The remedy is the CLI's own sentence, carried, not composed here.
    expect(board.needs.get("FIX-3")).toBe("kyo move FIX-3 verified");

    // And it reaches the record surface as the class plus that remedy.
    const detail = await readRecord(fixture.project, "FIX-3");
    expect(detail.boardClass).toBe("settled-not-moved");
    expect(detail.needs).toBe("kyo move FIX-3 verified");
    expect(detail.status).toBe("open");
    // Finished, but emphatically not `done`: the status never moved, which is the
    // whole point -- a UI that read this as done would be inventing the move.
    expect(detail.criteria.every((criterion) => criterion.state === "met")).toBe(true);

    // The classes are disjoint by construction in the CLI, and `classOf` resolves
    // them in the CLI's own order -- settled first, because KYO-87's whole complaint
    // was finished work appearing in the list that means "hand this out next".
    for (const id of board.settledNotMoved) {
      expect(classOf(board, id)).toBe("settled-not-moved");
      expect(board.ready.has(id), `${id} is in both ready and settled`).toBe(false);
    }
    for (const id of board.ready) expect(classOf(board, id)).toBe("ready");
    for (const id of board.blocked) {
      expect(classOf(board, id)).toBe("blocked");
      expect(board.ready.has(id), `${id} is in both ready and blocked`).toBe(false);
    }
    expect(classOf(board, "FIX-999")).toBe("none");

    // The record surface carries the blocked class too.
    expect((await readRecord(fixture.project, "FIX-4")).boardClass).toBe("blocked");

    // And the class the CLI names a remedy for carries the CLI's remedy, not ours.
    for (const [id, needs] of board.needs) {
      expect(needs).toContain(`kyo move ${id}`);
    }
  } finally {
    removeFixture(fixture);
  }
});

test("a dispatch's current report is the one its report_id names", () => {
  // Driven through the shaping function over `kyo show --json`'s own field names,
  // rather than through a live dispatch, because a dispatch carrying MORE than one
  // report is precisely the state KYO-90 was filed about and precisely the state
  // `kyo report` refuses to create today (`dispatch-already-terminal`). The
  // rendering rule has to be right before the store can produce the case.
  const [dispatch] = dispatchViews(
    [
      {
        id: "FIX-1.d1",
        seq: 1,
        state: "reported",
        branch: "fix-1",
        worked_from_sha: "abc",
        created_at: "t0",
        // The store's own answer to "which account of this dispatch counts".
        report_id: "FIX-1.r2",
      },
    ],
    [
      {
        id: "FIX-1.r1",
        seq: 1,
        dispatch_id: "FIX-1.d1",
        author: "w",
        created_at: "t1",
        worked_from_sha: "abc",
        body: { commits: [1] },
      },
      {
        id: "FIX-1.r2",
        seq: 2,
        dispatch_id: "FIX-1.d1",
        author: "w",
        created_at: "t2",
        worked_from_sha: "abc",
        body: { commits: [1, 2, 3] },
      },
      // A report belonging to a different dispatch must not be picked up here.
      {
        id: "FIX-2.r1",
        seq: 1,
        dispatch_id: "FIX-2.d1",
        author: "w",
        created_at: "t3",
        worked_from_sha: "abc",
        body: {},
      },
    ],
  );
  expect(dispatch?.currentReport?.id).toBe("FIX-1.r2");
  expect(dispatch?.currentReport?.current).toBe(true);
  expect(dispatch?.superseded.map((report) => report.id)).toEqual(["FIX-1.r1"]);
  for (const report of dispatch?.superseded ?? []) expect(report.current).toBe(false);

  // Not "the newest": picking by recency would be this app deciding which account is
  // authoritative, and the store has already decided.
  const [byId] = dispatchViews(
    [
      {
        id: "FIX-1.d1",
        seq: 1,
        state: "reported",
        worked_from_sha: "abc",
        created_at: "t0",
        report_id: "FIX-1.r1",
      },
    ],
    [
      {
        id: "FIX-1.r1",
        seq: 1,
        dispatch_id: "FIX-1.d1",
        author: "w",
        created_at: "t1",
        worked_from_sha: "abc",
        body: {},
      },
      {
        id: "FIX-1.r2",
        seq: 2,
        dispatch_id: "FIX-1.d1",
        author: "w",
        created_at: "t2",
        worked_from_sha: "abc",
        body: {},
      },
    ],
  );
  expect(byId?.currentReport?.id).toBe("FIX-1.r1");

  // A dispatch that has not reported has no current report, and says so rather than
  // borrowing one.
  const [none] = dispatchViews(
    [{ id: "FIX-1.d1", seq: 1, state: "running", worked_from_sha: "abc", created_at: "t0" }],
    [],
  );
  expect(none?.currentReport).toBeNull();
  expect(none?.superseded).toEqual([]);
});

test("a dispatch carries the records it formally covers, and derives them nowhere (KYO-296)", () => {
  // The seam under the record page's `also formally covers` line -- the line the
  // terminal has printed all along and the app dropped. Driven through the shaping
  // function over `kyo show --json`'s own field names rather than through the app,
  // because the desktop suite launches hidden windows and what decides the line's
  // CONTENT is this function: the page's only remaining decision is to draw nothing
  // for an empty list.
  const [covering] = dispatchViews(
    [
      {
        id: "FIX-1.d1",
        seq: 1,
        state: "reported",
        branch: "fix-1",
        worked_from_sha: "abc",
        created_at: "t0",
        // The CLI's own per-dispatch key, straight off `covers_json`. Two of them,
        // because `--also-covers` is repeatable and a renderer that showed the first
        // would be dropping a record the worker formally spoke for.
        covers: ["FIX-2", "FIX-3"],
      },
    ],
    [],
  );
  expect(covering?.covers).toEqual(["FIX-2", "FIX-3"]);

  // A dispatch sent about one record says so with an empty list, and so does a CLI
  // older than this build that carries no such key at all: both are what makes the
  // page draw no line rather than an empty one.
  const [empty] = dispatchViews(
    [
      {
        id: "FIX-1.d1",
        seq: 1,
        state: "running",
        worked_from_sha: "abc",
        created_at: "t0",
        covers: [],
      },
    ],
    [],
  );
  expect(empty?.covers).toEqual([]);
  const [absent] = dispatchViews(
    [{ id: "FIX-1.d1", seq: 1, state: "running", worked_from_sha: "abc", created_at: "t0" }],
    [],
  );
  expect(absent?.covers).toEqual([]);
});

test("a covered record's covering dispatches are carried, not derived (KYO-291)", () => {
  // The seam under the record page's `covered by` section. Driven over `kyo show
  // --json`'s own field names, because the point of the section is that the CLI's
  // backwards walk over `covers_json` is the ONLY thing that knows the relation: the
  // covering dispatch hangs off a record this page never read, so its row is not in
  // this document and no amount of looking at `dispatches` here could find it.
  const covering = coveringDispatchViews([
    {
      id: "FIX-1.d1",
      state: "reported",
      primary_record_id: "FIX-1",
      report_id: "FIX-1.r1",
    },
    {
      id: "FIX-3.d1",
      state: "running",
      primary_record_id: "FIX-3",
      // Still running, so nothing has reported. A null, not an absence.
      report_id: null,
    },
  ]);
  expect(covering.map((entry) => entry.id)).toEqual(["FIX-1.d1", "FIX-3.d1"]);
  expect(covering[0]?.primaryRecordId).toBe("FIX-1");
  expect(covering[0]?.reportId).toBe("FIX-1.r1");
  expect(covering[1]?.state).toBe("running");
  expect(covering[1]?.reportId).toBeNull();

  // A record nothing covers, and a CLI older than this build that carries no such key
  // at all: both are the empty list, which is what makes the page draw no section
  // rather than fail to render.
  expect(coveringDispatchViews([])).toEqual([]);
});

// ------------------------------------------------ KYO-112.c3: the session briefs

test("the session briefs are read from the store, newest first", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const catalogue = await readBriefs(fixture.project);
    expect(catalogue.ok).toBe(true);
    expect(catalogue.briefs.length).toBe(2);
    expect(catalogue.briefs.map((run) => run.id)).toEqual(["FIX-B2", "FIX-B1"]);
    expect(catalogue.briefs[0]?.sessionStart).toBe(true);
    // Every surface painting store facts carries the currency (KYO-74).
    expect(STORE_CURRENCY_STATES).toContain(catalogue.currency.state);
  } finally {
    removeFixture(fixture);
  }
});

test("a brief opens as what the store kept: its window of shift notes and events", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const doc = await readBrief(fixture.project, "FIX-B2");
    expect(doc.ok).toBe(true);
    expect(doc.run?.id).toBe("FIX-B2");
    expect(doc.previousBriefId).toBe("FIX-B1");
    // The shift note written between the two briefs, verbatim and not paraphrased.
    expect(doc.shiftNotes.map((note) => note.id)).toEqual(["FIX-S1"]);
    expect(doc.shiftNotes[0]?.text).toContain("surface verbatim");
    expect(doc.shiftNotes[0]?.redactedAt).toBeNull();
    // The window is the previous brief's position to this one's, so the events are
    // the ones this brief closed over and not the whole ledger.
    expect(doc.events.length).toBeGreaterThan(0);
    for (const event of doc.events) {
      expect(event.seq).toBeGreaterThan(doc.run?.lastEventSeq === undefined ? -1 : 0);
      expect(event.seq).toBeLessThanOrEqual(doc.run?.lastEventSeq ?? 0);
    }
    // The first brief has no predecessor, and says so rather than inventing one.
    const first = await readBrief(fixture.project, "FIX-B1");
    expect(first.ok).toBe(true);
    expect(first.previousBriefId).toBeNull();

    const nope = await readBrief(fixture.project, "FIX-B99");
    expect(nope.ok).toBe(false);
    expect(nope.problem ?? "").toContain("FIX-B99");
  } finally {
    removeFixture(fixture);
  }
});

// -------------------------------------------------- KYO-112.c6: currency, always

test("every records-mode payload carries a currency verdict, cannot-tell included", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const states = async () => [
      (await runBase(fixture.project, "everything")).currency,
      (await readRecordTree(fixture.project, "kind")).currency,
      (await readRecord(fixture.project, "FIX-1")).currency,
      (await readBriefs(fixture.project)).currency,
    ];
    for (const currency of await states()) {
      expect(currency.state).toBe("current");
      expect(currency.direction).toBe("in-step");
    }

    // Now break the seam so the two sides disagree in a way the CLI calls
    // undecidable, and check every records surface reports it. "Cannot tell" is the
    // direction a surface is most tempted to render as "fine".
    setAllocation(
      fixture,
      "record",
      Number(
        sqliteScalar(
          dbPathFor(fixture.root),
          "SELECT next_value FROM allocations WHERE scope = 'record'",
        ),
      ),
    );
    hideExportedRecord(fixture, "FIX-2");
    for (const currency of await states()) {
      expect(currency.state).toBe("cannot-tell");
      expect(currency.direction).toBe("cannot-tell");
      expect(currency.says).toContain("cannot tell");
    }

    // And the other two directions, so all four reach these surfaces.
    setAllocation(fixture, "record", 99);
    expect((await runBase(fixture.project, "everything")).currency.state).toBe("export-ahead");
    setAllocation(fixture, "record", 1);
    expect((await runBase(fixture.project, "everything")).currency.state).toBe("database-ahead");
  } finally {
    removeFixture(fixture);
  }
});

test("currencyFrom maps the CLI's word and refuses to guess at an unknown one", () => {
  expect(currencyFrom({ direction: "in-step", says: "s", remedy: "" }, "x").state).toBe("current");
  expect(currencyFrom({ direction: "cannot-tell", says: "s", remedy: "" }, "x").state).toBe(
    "cannot-tell",
  );
  // A word this build does not know is "unrecognised" -- a statement about the app --
  // and emphatically not folded into `cannot-tell`, which is a verdict about the store.
  const unknown = currencyFrom({ direction: "sideways", says: "", remedy: "" }, "x");
  expect(unknown.state).toBe("unrecognised");
  expect(unknown.direction).toBe("sideways");
  expect(currencyFrom(null, "kyo whatever --json").state).toBe("unavailable");
});

// ================================================== the renderings (KYO-113)
//
// The bubble rendering's bucketing and the graph's edge reading and layering are
// pure functions over rows the engine returned, so they are driven here directly --
// with a line number when they fail, rather than a screenshot of a dot that is the
// wrong size. What a *reader* sees is checked in the smoke suite.

// ------------------------------------------------- KYO-113.c1: three buckets, never more

// ---------------------------------------- KYO-113.c2/c4: the edges, off the same run

// =============================================== ideas mode (KYO-114), driven directly
//
// The mode's two halves are checked in different places for a reason. Everything
// below is the half that has no window in it -- where the per-project home comes
// from, what the tree is, what the port of `ideas.py` says, and above all what the
// app refuses to write. The half that needs a real editor with a real caret is in
// `smoke.spec.ts`.

test("the per-project home is asked of `kyo`, not re-derived from a rule", async () => {
  // The same shape as the store locator: the app must not own a copy of
  // `config.store_home()` + `project_home_name()`. Proved by MOVING the answer --
  // `KYOSEI_HOME` is the CLI's variable, so a fixture that sets it and gets a
  // scratchpad path inside itself is a fixture whose app asked rather than derived.
  const fixture = await makeRecordsFixture();
  try {
    const home = await readProjectHome(fixture.project);
    expect(home.problem, home.problem ?? "").toBeNull();
    expect(home.home).toBe(join(fixture.home, basename(fixture.root)));

    const pad = await readScratchpad(fixture.project);
    expect(pad.path).toBe(scratchpadPathFor(fixture));

    // And the fact that makes the location the record's location: it is outside
    // `ideas/` and outside the store, so promotion and sweep cannot see it.
    expect(pad.path?.startsWith(fixture.root)).toBe(false);
    expect(pad.path).not.toContain(`${sep}ideas${sep}`);
  } finally {
    removeFixture(fixture);
  }
});

test("the scratchpad is created on the first save and not before", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const path = scratchpadPathFor(fixture);
    // Reading is not creating: opening ideas mode in a project must not leave a file
    // behind in the human's home directory.
    const first = await readScratchpad(fixture.project);
    expect(first.ok).toBe(true);
    expect(first.fresh).toBe(true);
    expect(first.text).toBe("");
    expect(existsSync(path)).toBe(false);

    const written = await writeScratchpad(fixture.project, {
      text: "todo: a thing\n",
      base: first.text,
      version: first.version,
    });
    expect(written.ok, written.problem ?? "").toBe(true);
    expect(written.path).toBe(path);
    expect(readFileSync(path, "utf8")).toBe("todo: a thing\n");

    const again = await readScratchpad(fixture.project);
    expect(again.fresh).toBe(false);
    expect(again.text).toBe("todo: a thing\n");
  } finally {
    removeFixture(fixture);
  }
});

// ======================== KYO-575: two editors of one file, each with its own version
//
// With the scratchpad open in two windows, or on two machines, a window that had not
// updated could be typed in, and its save overwrote what the other session had written.
// Two causes, both confirmed failing against the code before
// this record: one scratchpad ETag per PROCESS (window B saved with the version window
// A's save had just left), and `saveIdea` re-reading the file at save time and sending
// THAT ETag. Each check below is two editors: two reads of one file, two saves.

const lines = (text: string) => text.split("\n");
const markers = (text: string) =>
  lines(text).filter((line) => line.startsWith(MERGE_MARKER_PREFIX));

test("two scratchpad editors each save against their own version: the stale save is merged, not written over (KYO-575.c1)", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const path = scratchpadPathFor(fixture);
    const fresh = await readScratchpad(fixture.project);
    expect(fresh.version).toBeNull();
    const seeded = await writeScratchpad(fixture.project, {
      text: "a list\n",
      base: fresh.text,
      version: fresh.version,
    });
    expect(seeded.ok, seeded.problem ?? "").toBe(true);

    // Window A and window B both load the version just written.
    const a = await readScratchpad(fixture.project);
    const b = await readScratchpad(fixture.project);
    expect(a.version).not.toBeNull();
    expect(b.version).toBe(a.version);

    const savedA = await writeScratchpad(fixture.project, {
      text: `${a.text}typed in A\n`,
      base: a.text,
      version: a.version,
    });
    expect(savedA.ok, savedA.problem ?? "").toBe(true);
    expect(savedA.merged).toBeNull();
    expect(savedA.version).not.toBe(a.version);

    // B never heard A's save. Against the per-process map this save went out with the
    // version A's save left behind, was accepted, and "typed in A" was gone.
    const savedB = await writeScratchpad(fixture.project, {
      text: `${b.text}typed in B\n`,
      base: b.text,
      version: b.version,
    });
    expect(savedB.ok, savedB.problem ?? "").toBe(true);
    const onDisk = readFileSync(path, "utf8");
    expect(onDisk).toBe("a list\ntyped in A\ntyped in B\n");
    // Two appends at one place collide on no existing line: no marker (merge3's rule 3).
    expect(savedB.merged).toEqual({ conflicts: 0 });
    expect(savedB.body).toBe(onDisk);
    expect(savedB.base).toBe(onDisk);
    // ...and B's next save names the version its merge left, so it is not merged again.
    const againB = await writeScratchpad(fixture.project, {
      text: `${onDisk}more from B\n`,
      base: savedB.base ?? "",
      version: savedB.version,
    });
    expect(againB.merged).toBeNull();
    expect(readFileSync(path, "utf8")).toBe(`${onDisk}more from B\n`);
  } finally {
    removeFixture(fixture);
  }
});

test("a scratchpad created in another window since this one read it is merged, not replaced (KYO-575.c1)", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const a = await readScratchpad(fixture.project);
    const b = await readScratchpad(fixture.project);
    expect([a.fresh, b.fresh]).toEqual([true, true]);
    await writeScratchpad(fixture.project, { text: "A first\n", base: "", version: null });
    const savedB = await writeScratchpad(fixture.project, {
      text: "B second\n",
      base: "",
      version: null,
    });
    expect(savedB.ok, savedB.problem ?? "").toBe(true);
    expect(readFileSync(scratchpadPathFor(fixture), "utf8")).toBe("A first\nB second\n");
  } finally {
    removeFixture(fixture);
  }
});

test("a refused scratchpad save is merged three ways: clean apart, both kept between marked lines where they overlap (KYO-575.c4)", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const path = scratchpadPathFor(fixture);
    await writeScratchpad(fixture.project, {
      text: "one\ntwo\nthree\nfour\n",
      base: "",
      version: null,
    });
    const loaded = await readScratchpad(fixture.project);

    // Apart: elsewhere changed line one, this tab changed line four. No marker.
    writeFileSync(path, "ONE elsewhere\ntwo\nthree\nfour\n", "utf8");
    const apart = await writeScratchpad(fixture.project, {
      text: "one\ntwo\nthree\nFOUR here\n",
      base: loaded.text,
      version: loaded.version,
    });
    expect(apart.ok, apart.problem ?? "").toBe(true);
    expect(apart.merged).toEqual({ conflicts: 0 });
    expect(readFileSync(path, "utf8")).toBe("ONE elsewhere\ntwo\nthree\nFOUR here\n");

    // Overlap: both changed line two. Both kept, elsewhere's above, this tab's below.
    const reloaded = await readScratchpad(fixture.project);
    writeFileSync(path, reloaded.text.replace("two", "two, said elsewhere"), "utf8");
    const overlap = await writeScratchpad(fixture.project, {
      text: reloaded.text.replace("two", "two, said here"),
      base: reloaded.text,
      version: reloaded.version,
    });
    expect(overlap.ok, overlap.problem ?? "").toBe(true);
    expect(overlap.merged).toEqual({ conflicts: 1 });
    const merged = readFileSync(path, "utf8");
    expect(lines(merged)).toEqual([
      "ONE elsewhere",
      MERGE_MARKER_THEIRS,
      "two, said elsewhere",
      mergeMarkerMine(mergeAuthor()),
      "two, said here",
      MERGE_MARKER_END,
      "three",
      "FOUR here",
      "",
    ]);
    // The old remedy -- "Close and reopen it", which discarded the typing -- is gone.
    expect(readFileSync(join(SRC_ROOT, "main", "ideas.ts"), "utf8")).not.toContain(
      "Close and reopen",
    );
  } finally {
    removeFixture(fixture);
  }
});

test("an idea file saves against the version its tab loaded, not one re-read at save time (KYO-575.c1, c4)", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const relative = "shared.md";
    const path = join(fixture.root, "ideas", relative);
    writeFileSync(path, "---\ntags:\n  - one\n---\n\n# Shared\n\nfirst\nsecond\nthird\n", "utf8");
    const tab = await openIdea(fixture.project, relative);
    expect(tab.ok, tab.problem ?? "").toBe(true);
    expect(tab.version).not.toBeNull();
    expect(tab.base).toBe(readFileSync(path, "utf8"));

    // Another editor -- a window, a machine, Obsidian -- changes the last line. Against
    // the re-read in saveIdea, this tab's save took THAT version and wrote over it.
    writeFileSync(path, tab.base.replace("third", "third, from elsewhere"), "utf8");
    const apart = await saveIdea(fixture.project, {
      path: relative,
      body: tab.body.replace("first", "first, from this tab"),
      properties: tab.properties,
      base: tab.base,
      version: tab.version,
    });
    expect(apart.ok, apart.problem ?? "").toBe(true);
    expect(apart.merged).toEqual({ conflicts: 0 });
    const clean = readFileSync(path, "utf8");
    expect(clean).toContain("first, from this tab");
    expect(clean).toContain("third, from elsewhere");
    expect(clean.startsWith("---\ntags:\n  - one\n---\n")).toBe(true);
    expect(markers(clean)).toEqual([]);
    expect(apart.body).toBe(splitBody(clean));

    // Overlap on one line: both kept between the marked lines, elsewhere's first.
    const again = await openIdea(fixture.project, relative);
    writeFileSync(path, again.base.replace("second", "second, from elsewhere"), "utf8");
    const overlap = await saveIdea(fixture.project, {
      path: relative,
      body: again.body.replace("second", "second, from this tab"),
      properties: again.properties,
      base: again.base,
      version: again.version,
    });
    expect(overlap.ok, overlap.problem ?? "").toBe(true);
    expect(overlap.merged).toEqual({ conflicts: 1 });
    const kept = lines(readFileSync(path, "utf8"));
    const at = kept.indexOf(MERGE_MARKER_THEIRS);
    expect(kept.slice(at, at + 5)).toEqual([
      MERGE_MARKER_THEIRS,
      "second, from elsewhere",
      mergeMarkerMine(mergeAuthor()),
      "second, from this tab",
      MERGE_MARKER_END,
    ]);
    expect(kept).toContain("first, from this tab");
    expect(kept).toContain("third, from elsewhere");
  } finally {
    removeFixture(fixture);
  }
});

/** An idea file's body as `splitFrontmatter` reads it, for the checks above. */
function splitBody(text: string): string {
  const match = /^---\n[\s\S]*?\n---\n/.exec(text);
  return match === null ? text : text.slice(match[0].length);
}

test("merge3: one side's change is taken, a deletion included; an identical change once; two inserts at one place both, theirs first (KYO-575.c4)", () => {
  const base = "a\nb\nc\nd\ne";
  // Only one side changed a region: that change, either side.
  expect(merge3(base, "a\nB\nc\nd\ne", base, "here")).toEqual({
    text: "a\nB\nc\nd\ne",
    conflicts: 0,
  });
  expect(merge3(base, "a\nB\nc\nd\ne", "a\nb\nc\nD\ne", "here")).toEqual({
    text: "a\nB\nc\nD\ne",
    conflicts: 0,
  });
  // A line one side deleted stays deleted when the other did not touch it.
  expect(merge3(base, "a\nb\nc\nd\ne\nnew", "a\nc\nd\ne", "here").text).toBe("a\nc\nd\ne\nnew");
  // An identical change on both sides is taken once.
  expect(merge3(base, "a\nX\nc\nd\ne", "a\nX\nc\nd\ne\n", "here")).toEqual({
    text: "a\nX\nc\nd\ne\n",
    conflicts: 0,
  });
  // Two insertions at one place, no existing line changed: both, theirs first.
  expect(merge3(base, "a\nb\nmine\nc\nd\ne", "a\nb\ntheirs\nc\nd\ne", "here")).toEqual({
    text: "a\nb\ntheirs\nmine\nc\nd\ne",
    conflicts: 0,
  });
  // A missing final newline, a CRLF and a blank line survive as the bytes they were.
  expect(merge3("x\r\ny", "x\r\ny\r\n\r\nz", "w\r\nx\r\ny", "here").text).toBe(
    "w\r\nx\r\ny\r\n\r\nz",
  );
});

test("merge3: an overlap keeps both texts, the other side's above, between three marked lines that name this side (KYO-575.c4)", () => {
  const result = merge3("a\nb\nc", "a\nmine 1\nmine 2\nc", "a\ntheirs\nc", "the window on devbox");
  expect(result.conflicts).toBe(1);
  expect(lines(result.text)).toEqual([
    "a",
    MERGE_MARKER_THEIRS,
    "theirs",
    "━━ then the one from the window on devbox, saved after it ━━",
    "mine 1",
    "mine 2",
    MERGE_MARKER_END,
    "c",
  ]);
  // One side deleted a region the other changed: still an overlap, and the change stays.
  const gone = merge3("a\nb\nc", "a\nb, edited\nc", "a\nc", "here");
  expect(gone.conflicts).toBe(1);
  expect(lines(gone.text)).toContain("b, edited");
  // Plain text a person reads and deletes: not git's markers.
  for (const marker of [MERGE_MARKER_THEIRS, mergeMarkerMine("x"), MERGE_MARKER_END]) {
    expect(marker).not.toMatch(/^(<<<<<<<|=======|>>>>>>>)/);
    expect(marker.startsWith(MERGE_MARKER_PREFIX)).toBe(true);
  }
});

test("merge3 never drops a line either side wrote, over thousands of random edits (KYO-575.c4)", () => {
  let seed = 575;
  const next = (n: number) => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed % n;
  };
  const vocabulary = ["a", "b", "c", "", "d", "e e"];
  const random = () => Array.from({ length: next(9) }, () => vocabulary[next(6)] ?? "").join("\n");
  let made = 0;
  const edit = (text: string): string => {
    const out = text.split("\n");
    for (let step = next(3); step >= 0; step -= 1) {
      const at = next(out.length + 1);
      const op = next(3);
      made += 1;
      if (op === 0) out.splice(at, 0, `added ${made}`);
      else if (op === 1 && out.length > 1) out.splice(Math.min(at, out.length - 1), 1);
      else out[Math.min(at, out.length - 1)] = `changed ${made}`;
    }
    return out.join("\n");
  };
  const inOrder = (wanted: string[], within: string[]) => {
    let i = 0;
    for (const line of within) if (i < wanted.length && line === wanted[i]) i += 1;
    return i === wanted.length;
  };
  for (let round = 0; round < 3000; round += 1) {
    const base = random();
    const mine = edit(base);
    const theirs = edit(base);
    const merged = merge3(base, mine, theirs, "here");
    const kept = lines(merged.text).filter((line) => !line.startsWith(MERGE_MARKER_PREFIX));
    for (const side of [mine, theirs]) {
      const written = lines(side).filter((line) => /^(added|changed) /.test(line));
      expect(inOrder(written, kept), JSON.stringify({ base, mine, theirs, merged })).toBe(true);
    }
    expect(markers(merged.text).length).toBe(merged.conflicts * 3);
    // And the line changes a reload dispatches reproduce the text they were made for.
    expect(applyChanges(mine, lineChanges(mine, theirs))).toBe(theirs);
  }
});

test("a compose tab follows its own file's change only, and never over unsaved edits (KYO-575.c3)", () => {
  const due = (
    changed: Parameters<typeof composeReloadDue>[0]["changed"],
    isIdea: boolean,
    dirty = false,
    saving = false,
  ) => composeReloadDue({ changed, isIdea, dirty, saving });
  expect(due("scratchpad", false)).toBe(true);
  expect(due("ideas", true)).toBe(true);
  expect(due("ideas", false)).toBe(false);
  expect(due("scratchpad", true)).toBe(false);
  expect(due("store", false)).toBe(false);
  expect(due("sessions", true)).toBe(false);
  // A stream that dropped and came back may have missed a change.
  expect(due("server", false)).toBe(true);
  // Unsaved edits, or a save out: the human's text stays; the save merges.
  expect(due("scratchpad", false, true)).toBe(false);
  expect(due("ideas", true, false, true)).toBe(false);
  // And no sidebar list re-reads for it: the scratchpad is not in the ideas tree.
  for (const showing of ["ideas", "records", "notes", "awaiting"]) {
    expect(storeReloadDue({ showing, changed: "scratchpad", inFlight: false })).toBe(false);
  }
});

test("the app refuses to write anything but the scratchpad and ideas/", async () => {
  // KYO-114.c5 and the boundary of the whole record, checked at the mechanism rather
  // than at the buttons. Since KYO-459 the mechanism is kyo serve's (`serve_files.py`):
  // every write the app makes is a request to it, so these are the writes the app
  // cannot make no matter what asks it to -- whichever machine the project is on.
  const fixture = await makeRecordsFixture();
  try {
    const allowed = ["fine.md", "in a folder/also fine.md", "brand new folder/new.md"];
    for (const path of allowed) {
      const made = await createIdea(fixture.project, {
        folder: path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "",
        name: path.slice(path.lastIndexOf("/") + 1),
        text: "x",
      });
      expect(made.ok, `${path} should be writable: ${made.problem ?? ""}`).toBe(true);
      expect(existsSync(join(fixture.root, "ideas", path))).toBe(true);
    }
    const store = readFileSync(join(fixture.root, "kyosei", "config.yml"), "utf8");
    const refused = [
      "../kyosei/config.yml",
      "../kyosei/export/FIX-1.json",
      "../README.md",
      "in a folder/../../kyosei/bases.yml",
    ];
    for (const path of refused) {
      const saved = await saveIdea(fixture.project, {
        path,
        body: "overwritten",
        properties: [],
        base: "",
        version: null,
      });
      expect(saved.ok, `${path} was NOT refused`).toBe(false);
      // The refusal is kyo serve's, and it says what ideas/ is.
      expect(saved.problem ?? "").toContain("ideas/");
    }
    expect(readFileSync(join(fixture.root, "kyosei", "config.yml"), "utf8")).toBe(store);
    // And no write path of the app's own is left to bypass it: nothing under src/ calls a
    // file-writing function on a project's files (the app's own data directory aside).
    const ideas = readFileSync(join(SRC_ROOT, "main", "ideas.ts"), "utf8");
    expect(ideas).not.toMatch(/from "node:fs"/);
  } finally {
    removeFixture(fixture);
  }
});

test("a symlink out of ideas/ does not become a way into the store", async () => {
  // The reason kyo serve resolves real paths. A string-prefix check would wave this
  // through, and it is the one shape in which a write to `ideas/anything` really does
  // land under `kyosei/`.
  const fixture = await makeRecordsFixture();
  try {
    const link = join(fixture.root, "ideas", "sneaky");
    symlinkSync(join(fixture.root, "kyosei"), link);
    const before = readFileSync(join(fixture.root, "kyosei", "config.yml"), "utf8");
    const saved = await saveIdea(fixture.project, {
      path: "sneaky/config.yml",
      body: "overwritten",
      properties: [],
      base: "",
      version: null,
    });
    expect(saved.ok).toBe(false);
    expect(saved.problem ?? "").toMatch(/kyosei\//);
    expect(readFileSync(join(fixture.root, "kyosei", "config.yml"), "utf8")).toBe(before);
  } finally {
    removeFixture(fixture);
  }
});

test("ideas mode's own allowlist admits two reads and refuses everything else", () => {
  for (const command of IDEAS_READ_COMMANDS) {
    expect(() => assertIdeasRead([command])).not.toThrow();
  }
  // `promote` above all: it is the command this mode is nearest to and the one it
  // must not be able to run, because promotion is a store write (KYO-39).
  for (const command of ["promote", "new", "sweep", "brief", "rebuild", "export"]) {
    expect(() => assertIdeasRead([command]), `\`kyo ${command}\` was not refused`).toThrow(
      new RegExp(`may not run .kyo ${command}`),
    );
  }
});

test("the ideas tree lists what the vault holds, folders and non-markdown included", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const tree = await readIdeaTree(fixture.project);
    expect(tree.ok, tree.refusal ?? "").toBe(true);
    expect(tree.root).toBe(join(fixture.root, "ideas"));

    const paths = tree.files.map((file) => file.path).sort();
    expect(paths).toEqual(
      [...Object.keys(FIXTURE_IDEAS), FIXTURE_IDEAS_OTHER].sort((a, b) => a.localeCompare(b)),
    );

    // `ideas/scratchpad.md` is skipped, exactly as `ideas.idea_files` skips it: the
    // tree agrees with every `kyo` command about what this directory holds.
    expect(existsSync(join(fixture.root, "ideas", "scratchpad.md"))).toBe(true);
    expect(paths).not.toContain("scratchpad.md");

    // A folder holding nothing is still a folder -- it is where the new-idea modal
    // can put a file, and a tree that hid it would hide a destination.
    expect(tree.folders).toContain("an empty folder");
    expect(tree.folders).toContain("in a folder");
    expect(tree.folders).toContain("");

    // Non-markdown is listed and marked rather than hidden, because it IS in the
    // vault and the human sees it in Obsidian.
    const picture = tree.files.find((file) => file.path === FIXTURE_IDEAS_OTHER);
    expect(picture?.openable).toBe(false);
    expect(tree.files.find((file) => file.path === "bare.md")?.openable).toBe(true);

    // The stamp `kyo promote` wrote is carried through to the row's badge.
    const promoted = tree.files.find((file) => file.path === "in a folder/promoted already.md");
    expect(promoted?.promoted).toEqual(["FIX-1"]);
    expect(tree.files.find((file) => file.path === "bare.md")?.promoted).toEqual([]);

    // And the tree states where its rows came from, engine included.
    expect(tree.from).toContain("kyo base run unpromoted --json");
  } finally {
    removeFixture(fixture);
  }
});

test("the port of ideas.py agrees with the Base engine, file by file", async () => {
  // The check `shared/ideafile.ts` exists to earn, and the same shape as the grouping
  // check above: the CLI's own ideas source parses every file in the fixture vault,
  // and the port is compared against it for the three things the app reads --
  // properties, sections, and which sections are stubs. A port that drifted from
  // `ideas.py` fails here rather than in a pane, silently.
  const fixture = await makeRecordsFixture();
  try {
    const engine = await runBase(fixture.project, "unpromoted");
    expect(engine.ok, engine.refusal ?? "").toBe(true);
    expect(engine.rows.length).toBeGreaterThan(0);

    for (const row of engine.rows) {
      const id = String(row.id);
      const text = readFileSync(join(fixture.root, id), "utf8");

      // properties: the engine returns a mapping, the port an ordered array (the
      // file's key order has to survive a save). Same information either way.
      const enginePropsRaw = (row.properties ?? {}) as Record<string, unknown>;
      const ported = readProperties(text);
      const portedMap: Record<string, unknown> = {};
      for (const property of ported) {
        portedMap[property.key] = property.list ? property.values : (property.values[0] ?? null);
      }
      expect(portedMap, `${id}: properties differ from the engine's`).toEqual(enginePropsRaw);

      // sections: heading, level and 1-based whole-file line, all three.
      const engineSections = (row.sections ?? []) as Record<string, unknown>[];
      const portedSections = readSections(text);
      expect(
        portedSections.map((section) => [section.heading, section.level, section.line]),
        `${id}: sections differ from the engine's`,
      ).toEqual(engineSections.map((section) => [section.heading, section.level, section.line]));

      // and which of them are stubs.
      expect(
        portedSections.map((section) => (section.promotedTo === null ? [] : [section.promotedTo])),
        `${id}: promoted sections differ from the engine's`,
      ).toEqual(engineSections.map((section) => section.promoted_to));
    }
  } finally {
    removeFixture(fixture);
  }
});

/**
 * Run the CLI the way a controller session does -- directly, not through the app's
 * `runKyo`, which refuses `promote` (KYO-439). Checks that need a REAL promotion (the
 * stub's bytes, the remedy for the refusal) go through here.
 */
function runCliDirectly(
  resolution: { source: { program: string | null; baseArgs: string[] } },
  args: readonly string[],
  cwd: string,
): { code: number; stdout: string; stderr: string } {
  const { program, baseArgs } = resolution.source;
  if (program === null) throw new Error("no runnable kyo source for this check");
  const result = spawnSync(program, [...baseArgs, ...args], {
    cwd,
    encoding: "utf8",
    timeout: 60_000,
  });
  return { code: result.status ?? 1, stdout: result.stdout ?? "", stderr: result.stderr ?? "" };
}

test("the app never runs `kyo promote`; the controller's own run of the same argv works (KYO-439)", async () => {
  // The mechanism is `assertAppMayRun` inside `runKyo`, the one subprocess this app
  // starts for the CLI: remove it and the first expectation below fails, because the
  // promotion would go through. Electron-free on purpose -- this is the main process's
  // module, driven without a window.
  // The plain fixture, not the records one: this needs an `ideas/` file and a `bug`
  // kind, and nothing the records fixture seeds.
  const fixture = await makeFixture(1);
  try {
    const rel = "ideas/never from the app.md";
    const path = join(fixture.root, rel);
    writeFileSync(
      path,
      "# Never from the app\n\nA record's structure is the controller's.\n",
      "utf8",
    );
    const criteria = join(fixture.base, "kyo439-criteria.txt");
    writeFileSync(criteria, "the thing holds\n", "utf8");
    const touches = join(fixture.base, "kyo439-touches.txt");
    writeFileSync(touches, "kyosei/config.yml\n", "utf8");
    const argv = [
      "promote",
      rel,
      "--kind=bug",
      "--title=Never from the app",
      "--criteria-file",
      criteria,
      "--touches-file",
      touches,
    ];

    await expect(runKyo(fixture.resolution, argv, { cwd: fixture.root })).rejects.toThrow(
      /never runs `kyo promote` \(KYO-439\)/,
    );
    // A leading flag does not smuggle it past: the verb is the first non-flag word.
    expect(() => assertAppMayRun(["--json", "promote", rel])).toThrow(/never runs/);
    expect(() => assertAppMayRun(["base", "run", "unpromoted"])).not.toThrow();
    // Refused before anything ran: the idea file is where it was, unconsumed.
    expect(existsSync(path)).toBe(true);

    // The remedy the refusal names -- the controller runs it -- clears it: the same
    // argv, run as the CLI and not through the app, promotes the file.
    const direct = runCliDirectly(fixture.resolution, [...argv, "--json"], fixture.root);
    expect(direct.code, direct.stderr || direct.stdout).toBe(0);
    expect(JSON.parse(direct.stdout).record_id).toMatch(/^FIX-\d+$/);
  } finally {
    removeFixture(fixture);
  }
});

test("a stub written by `kyo promote` is the one this app recognises", async () => {
  // Not asserted against a copy of the template: the CLI is asked to promote a real
  // section, and the bytes it leaves behind are what `stubRecordIds` is run over. If
  // `ideas.promotion_stub` ever changes shape, this fails.
  const fixture = await makeRecordsFixture();
  try {
    const path = join(fixture.root, "ideas", "to promote.md");
    writeFileSync(path, "# A promotable idea\n\nSome prose worth a record.\n", "utf8");
    // Promotion-time structure, law 13's: a work kind wants criteria and touches, and
    // both arrive through `--*-file` rather than as prose arguments (law 5).
    const criteria = join(fixture.base, "promote-criteria.txt");
    writeFileSync(criteria, "the thing holds\n", "utf8");
    const touches = join(fixture.base, "promote-touches.txt");
    writeFileSync(touches, "kyosei/config.yml\n", "utf8");
    const promoted_ = runCliDirectly(
      fixture.resolution,
      [
        "promote",
        "ideas/to promote.md",
        "--kind",
        "bug",
        "--criteria-file",
        criteria,
        "--touches-file",
        touches,
        // KYO-228: promotion consumes the file by default; the stub this check reads
        // is now the `--keep` opt-in, and this is the one check that still wants it.
        "--keep",
      ],
      fixture.root,
    );
    // Asserted, not assumed: `kyo promote` refuses a kind whose policy requires
    // criteria, and a check that ignored the exit code would go on to conclude the
    // app cannot see a stub that was never written.
    expect(promoted_.code, promoted_.stderr || promoted_.stdout).toBe(0);
    const after = readFileSync(path, "utf8");
    const found = stubRecordIds(after);
    expect(found.length, `no stub found in:\n${after}`).toBeGreaterThan(0);
    expect(found[0]).toMatch(/^FIX-\d+$/);
    // And the file's frontmatter now names the same record, read by the port.
    const promoted = readProperties(after).find((property) => property.key === "promoted");
    expect(promoted?.values).toEqual(found);
    expect(promoted?.readOnly, "`promoted` must not be editable in the app").toBe(true);
  } finally {
    removeFixture(fixture);
  }
});

test("saving an idea file keeps every key the properties tool does not model", async () => {
  // The reason `composeIdeaFile` splices rather than re-renders. A file whose
  // frontmatter carries a comment and a nested mapping must come back with both,
  // byte for byte, after an edit to a different key.
  const fixture = await makeRecordsFixture();
  try {
    const relative = "awkward.md";
    const path = join(fixture.root, "ideas", relative);
    writeFileSync(
      path,
      "---\n# a comment nobody parses\nnested:\n  a: 1\n  b: 2\ntags:\n  - one\n---\n\n# Awkward\n\nbody\n",
      "utf8",
    );
    const opened = await openIdea(fixture.project, relative);
    expect(opened.ok, opened.problem ?? "").toBe(true);
    // The frontmatter is not in the body the editor shows -- that is the whole of
    // "frontmatter is hidden in the editor pane".
    expect(opened.body).not.toContain("nested:");
    expect(opened.body.trimStart().startsWith("# Awkward")).toBe(true);

    const properties = opened.properties.map((property) =>
      property.key === "tags" ? { ...property, values: ["one", "two"] } : property,
    );
    const saved = await saveIdea(fixture.project, {
      path: relative,
      body: "# Awkward\n\nedited body\n",
      properties,
      base: opened.base,
      version: opened.version,
    });
    expect(saved.ok, saved.problem ?? "").toBe(true);

    const after = readFileSync(path, "utf8");
    expect(after).toContain("# a comment nobody parses");
    expect(after).toContain("nested:\n  a: 1\n  b: 2");
    expect(after).toContain("  - two");
    expect(after).toContain("edited body");
    // And the CLI still reads the file the app wrote.
    const engine = await runBase(fixture.project, "unpromoted");
    const row = engine.rows.find((entry) => String(entry.id) === `ideas/${relative}`);
    expect((row?.properties as Record<string, unknown>)?.tags).toEqual(["one", "two"]);
  } finally {
    removeFixture(fixture);
  }
});

test("a new idea is a plain file: no record, no stamp, no ledger entry", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const events = () => sqliteScalar(dbPathFor(fixture.root), "SELECT count(*) AS n FROM events");
    const before = events();
    const result = await createIdea(fixture.project, {
      folder: "in a folder",
      name: "captured thought",
      text: "a thought worth keeping",
    });
    expect(result.ok, result.problem ?? "").toBe(true);
    expect(result.path).toBe(join(fixture.root, "ideas", "in a folder", "captured thought.md"));
    expect(readFileSync(result.path ?? "", "utf8")).toBe("a thought worth keeping\n");
    // Intake, and only intake: the store did not move.
    expect(events()).toBe(before);

    // An existing file is never overwritten -- capturing a thought must not destroy
    // writing to make room for writing.
    const again = await createIdea(fixture.project, {
      folder: "in a folder",
      name: "captured thought",
      text: "a different thought",
    });
    expect(again.ok).toBe(false);
    expect(again.problem).toContain("already exists");
    expect(readFileSync(result.path ?? "", "utf8")).toBe("a thought worth keeping\n");

    // A name is a file name, not a path: the folder field is the one control that
    // decides where this lands.
    const escaped = await createIdea(fixture.project, {
      folder: "",
      name: "../../kyosei/config.yml",
      text: "no",
    });
    expect(escaped.ok).toBe(false);
    expect(escaped.problem).toContain("file name, not a path");
  } finally {
    removeFixture(fixture);
  }
});

test("the completion sets are closed: only records that exist, only tags that do", async () => {
  // KYO-55, at the source of the two sets. A dangling reference is not typeable
  // because the app has nothing dangling to offer.
  const fixture = await makeRecordsFixture();
  try {
    const sets = await readCompletions(fixture.project);
    const ids = sets.records.map((record) => record.id).sort();
    const inStore = (await runBase(fixture.project, "everything")).rows.map((row) =>
      String(row.id),
    );
    expect(ids).toEqual([...inStore].sort());
    expect(sets.records.every((record) => record.title !== "")).toBe(true);

    // The tags are the vault's own -- frontmatter lists, a one-line scalar, and body
    // `#tags` -- and nothing else.
    expect(sets.tags).toContain("vault");
    expect(sets.tags).toContain("deep/nested");
    expect(sets.tags).toContain("loose");
    expect(sets.tags).toContain("single-scalar-tag");
    // The stub writes `#promoted` into the vault, so it really is a tag in it.
    expect(sets.tags).toContain("promoted");
    expect(sets.tags).not.toContain("");

    // Stated, not implied: there is no tag registry in v1.
    expect(sets.tagsFrom).toContain("no tag registry");

    // And every surface painting store facts carries the currency (KYO-74).
    expect(STORE_CURRENCY_STATES).toContain(sets.currency.state);
  } finally {
    removeFixture(fixture);
  }
});

// ------------------------------------------------ KYO-115: the tmux session
//
// The decisions behind the shared session, none of which need a window: what the
// session is called, how tmux is found, what the pane says, and the script the
// pty runs. They live here rather than in `smoke.spec.ts` for this file's own
// stated reason -- they launch nothing, so they fail with a line number instead
// of a screenshot, and they do not pay for an Electron launch apiece.
//
// What is NOT here: that the pane actually attaches and states its session, which
// needs a window, and that two clients see one live screen, which needs a real
// tmux. Both are in `smoke.spec.ts`.

test("the session is the controller's, named by prefix, joined by the CLI's own verb (KYO-474)", () => {
  // Until KYO-474 the pane made a `kyosei-<project home>` session of its own on the
  // default server. The session it shows now is the one `kyo controller start` made,
  // named after the project's prefix, and the line it hands a human is the CLI verb
  // that knows the socket and the exact target.
  expect(controllerJoinCommand("KYO")).toBe("kyo controller attach KYO");
  expect(controllerJoinCommand("a b")).toBe("kyo controller attach 'a b'");
  // From another machine, the same words through ssh -t: what the README tells a
  // person to type, and what the tab runs. Read back through a real shell, the words
  // after the host are exactly the one argument `zsh -lc` gets on the far end.
  const overSsh = controllerJoinOverSsh("devbox", "KYO");
  expect(overSsh.startsWith("ssh -t devbox ")).toBe(true);
  const typed = spawnSync(
    "/bin/sh",
    ["-c", `printf '%s\\n' ${overSsh.slice("ssh -t devbox ".length)}`],
    { encoding: "utf8" },
  );
  expect(typed.stdout).toBe("zsh -lc 'kyo controller attach KYO'\n");
  // The client's attach matches the name EXACTLY, as the CLI's `=PREFIX` does.
  expect(clientAttachArgs("/s", "KYO")).toEqual(["-S", "/s", "attach-session", "-t", "=KYO"]);
});

test("tmux detection is a PATH scan, overridable and refusable by name", () => {
  const dir = tempDirWithSpace("tmux scan");
  try {
    const stub = join(dir, "tmux");
    writeFileSync(stub, "#!/bin/sh\nexit 0\n", "utf8");
    chmodSync(stub, 0o755);
    expect(findTmux({ PATH: dir })).toBe(stub);
    expect(findTmux({ PATH: join(dir, "nothing here") })).toBeNull();

    // Turned off: the pane says it was turned off, not that tmux is missing.
    const off = resolveTmux({ PATH: dir, [TMUX_ENV]: "off" });
    expect(off.binary).toBeNull();
    expect(off.reason).toContain(TMUX_ENV);

    // A named binary that cannot run does NOT fall through to the PATH scan --
    // someone who named a path said which tmux they meant.
    const missing = resolveTmux({ PATH: dir, [TMUX_ENV]: join(dir, "not a tmux") });
    expect(missing.binary).toBeNull();
    expect(missing.reason).toContain("not a tmux");
    expect(resolveTmux({ PATH: dir, [TMUX_ENV]: stub }).binary).toBe(stub);

    // No tmux and no override: a reason a human can act on, naming both routes.
    // KYO-185: with the launch stated, because the sentence differs by launch and
    // this runner's own process has no terminal attached to it -- so leaving it to
    // the default would be asserting the Finder wording while reading as the shell's.
    // KYO-229: and with the capture stated too, for the same reason one step out --
    // this process HAS a `$SHELL`, so a defaulted third argument would quietly select
    // the other branch on a developer's desk and neither wording would be under test.
    const none = resolveTmux(
      { PATH: join(dir, "nothing here") },
      { tty: true, platform: "darwin" },
      NO_CAPTURE,
    );
    expect(none.reason).toContain("no tmux on PATH");
    // And no session without a prefix to name it by, even with tmux there.
    const unnamed = planTmux(
      null,
      "kyo serve did not answer",
      { PATH: dir },
      undefined,
      NO_CAPTURE,
    );
    expect(unnamed.session).toBeNull();
    expect(unnamed.reason).toContain("kyo serve did not answer");
    expect(planTmux("FIX", null, { PATH: dir }, undefined, NO_CAPTURE).session).toBe("FIX");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the pane's line states the session, and no longer a PATH it cannot fix", () => {
  const attached = noticeFor("tmux", "FIX", null);
  expect(attached).toContain("orchestrator session FIX");
  expect(attached).toContain("kyo controller attach FIX");

  // KYO-115.c3's stated limit -- a shell already running in an IDE's terminal keeps
  // the PATH it started with, so the app's shim directory could not reach it -- is
  // gone with the shims (KYO-450): `kyo` there is the login PATH's, the same one the
  // app runs, so there is nothing to tell the reader to export.
  expect(attached).not.toContain("export PATH");

  // And the fallback never borrows the shared session's voice.
  expect(noticeFor("bare", null, "no tmux on PATH, so this pane is alone")).toBe(
    "no tmux on PATH, so this pane is alone",
  );
});

// ------------------------------------------------ KYO-283: the session pills
//
// The seams the pill row is made of, every one of them Electron-free and none of them
// needing a tmux: what the socket is, what the row lists and in what order, what a
// click decides to do, and what the two commands say. That a session then really
// attaches and really survives the detach is `tmux-owner.spec.ts`'s, against a private
// server -- the same division KYO-115 drew between a stub's argv and tmux's behaviour.

test("the socket is the controller's, spelled as the CLI spells it, never the default (KYO-474.c2)", () => {
  // `kyo controller`'s rule, restated: `-S $KYOSEI_CONTROLLER_SOCKET` when set, else
  // `-L kyosei` with TMUX_TMPDIR dropped -- which tmux resolves under the real /tmp.
  expect(controllerSocketPath({ [CONTROLLER_SOCKET_ENV]: "/x/ctl" }, 502)).toBe("/x/ctl");
  expect(controllerSocketPath({ [CONTROLLER_SOCKET_ENV]: "  " }, 502)).toBe(
    join(tmuxDefaultTmpdir(), "tmux-502", CONTROLLER_SOCKET_NAME),
  );
  // TMUX_TMPDIR moves the DEFAULT server; the CLI drops it, and so this never reads it.
  expect(controllerSocketPath({ TMUX_TMPDIR: "/elsewhere" }, 502)).toBe(
    join(tmuxDefaultTmpdir(), "tmux-502", "kyosei"),
  );
  expect(controllerSocketPath({}, 7)).not.toContain("default");
  expect(controllerSocketSpelled({})).toBe("tmux -L kyosei");
  expect(controllerSocketSpelled({ [CONTROLLER_SOCKET_ENV]: "/x/ctl" })).toBe("tmux -S /x/ctl");
  // The environment the pane's tmux runs in drops exactly what the CLI drops.
  expect(
    controllerTmuxEnv({ TMUX: "/t,1,0", TMUX_PANE: "%1", TMUX_TMPDIR: "/e", PATH: "/b" }),
  ).toEqual({ PATH: "/b" });
  // And the funnel every argv goes through puts it in front, where tmux reads it.
  expect(tmuxArgs("/s/ck", ["list-sessions"])).toEqual(["-S", "/s/ck", "list-sessions"]);
});

test("every tmux argv this app builds names its socket (KYO-283.c5)", () => {
  // The KYO-254 shape as a scan rather than as a habit: a tmux subcommand written into
  // a source file without `-S` in front of it reaches whatever server the environment
  // it runs under names, which can be the user's own. `tmuxArgs` is
  // the one place `-S` is added, so what this checks is that nothing builds an argv
  // around it.
  //
  // The needles are assembled at runtime for the reason `tmux-owner.spec.ts` states
  // about its own scan: a literal `"list-sessions"` here would be a hit on this file
  // if it were ever scanned, and more to the point it keeps the spelling honest.
  const subcommands = [
    "list-sessions",
    "list-clients",
    "detach-client",
    "new-session",
    "set-environment",
    "show-environment",
    "attach-session",
  ];
  const offenders: string[] = [];
  for (const file of everySourceFile(SRC_ROOT)) {
    // Densified, so a call broken across lines by the formatter reads the same as one
    // written on a single line -- `sessionPrepareCalls` and `newSessionArgs` are both
    // wrapped, and a line-at-a-time scan would call every one of them an offender.
    const dense = readFileSync(file, "utf8").replaceAll(/\s+/g, "");
    for (const subcommand of subcommands) {
      const needle = `"${subcommand}"`;
      for (let at = dense.indexOf(needle); at !== -1; at = dense.indexOf(needle, at + 1)) {
        // The argv is either built by `tmuxArgs` -- which puts the socket in front --
        // or handed to `runTmux`, which builds it with `tmuxArgs` itself. Either way
        // the socket is an argument of the call this literal sits inside.
        const before = dense.slice(Math.max(0, at - 200), at);
        if (before.includes("tmuxArgs(") || before.includes("runTmux(")) continue;
        offenders.push(`${relative(SRC_ROOT, file)}: ${subcommand}`);
      }
    }
  }
  expect(
    offenders,
    "a tmux argv must be built by `tmuxArgs(socket, ...)`, which puts `-S <socket>` in " +
      "front of it. A bare server command reaches whichever server the environment " +
      "names, which is how a scratch command from inside an app pane once took the " +
      "user's live sessions down (KYO-254, KYO-283.c5).",
  ).toEqual([]);
  // And the funnel is really the funnel: `tmuxenv.ts` is where it lives (KYO-388; it
  // was `terminal.ts` until the headless server needed it without the pty), it is
  // defined there and nowhere else, and the module that runs the commands imports it
  // rather than writing its own.
  const main = (name: string) => readFileSync(join(SRC_ROOT, "main", name), "utf8");
  const homes = everySourceFile(SRC_ROOT).filter((file) =>
    /export function tmuxArgs\(/.test(readFileSync(file, "utf8")),
  );
  expect(homes.map((file) => relative(SRC_ROOT, file))).toEqual([join("main", "tmuxenv.ts")]);
  expect(main("tmuxctl.ts")).toMatch(/\btmuxArgs\(/);
  // `tmuxctl.ts` is reached by the headless server, so it takes the funnel -- and the
  // rest of the pane's machinery it needs -- from the pty-free module, never from
  // `terminal.ts`, which loads node-pty at its top.
  expect(main("tmuxctl.ts"), "tmuxctl.ts imports the pty-free module").toMatch(
    /from "\.\/tmuxenv"/,
  );
  // `controller.ts` is reached by it too, and since KYO-460 runs no tmux of its own --
  // `kyo controller` does -- so it needs neither; it must still not load the pty.
  for (const name of ["tmuxctl.ts", "controller.ts", "ssh.ts"]) {
    expect(main(name), `${name} imports terminal.ts, which loads the pty`).not.toMatch(
      /from "\.\/terminal"/,
    );
  }
  expect(main("controller.ts"), "controller.ts runs tmux itself").not.toMatch(/runTmux\(/);
  // And the pty-free module is pty-free at the source, as well as in the headless
  // bundle's graph (`server-main.spec.ts`, KYO-378.c5): no pty, and not by way of
  // `terminal.ts` either.
  const loadsPty = /(from|import\(|require\()\s*"[^"]*node-pty"/;
  expect(main("tmuxenv.ts")).not.toMatch(loadsPty);
  expect(main("tmuxenv.ts")).not.toMatch(/from "\.\/terminal"/);
  // The pattern really finds a pty import, so the line above cannot pass by missing it.
  expect(main("terminal.ts")).toMatch(loadsPty);
});

test("the pill row lists what is alive, project first, and marks the one we are in", () => {
  const alive = [
    { name: "another-project", clients: 1 },
    { name: "kyosei-kyosei", clients: 2 },
    { name: "zzz", clients: 0 },
  ];
  const pills = sessionPills(alive, "kyosei-kyosei", "another-project");
  // First and set apart, whichever order tmux answered in (KYO-283.c2).
  expect(pills.map((pill) => pill.name)).toEqual(["kyosei-kyosei", "another-project", "zzz"]);
  expect(pills[0]?.project).toBe(true);
  expect(pills.filter((pill) => pill.project)).toHaveLength(1);
  // Highlighted is "the one this pane is in", which is not the same fact as "the
  // project's" -- the two are independent, and a row that folded them together would
  // be unable to say the reader is in somebody else's session.
  expect(pills.find((pill) => pill.current)?.name).toBe("another-project");
  expect(pills.every((pill) => pill.live)).toBe(true);
  expect(pills.find((pill) => pill.name === "kyosei-kyosei")?.clients).toBe(2);

  // A session that ended drops off, because `live` is exactly what tmux answered.
  const after = sessionPills([{ name: "kyosei-kyosei", clients: 1 }], "kyosei-kyosei", null);
  expect(after.map((pill) => pill.name)).toEqual(["kyosei-kyosei"]);
  expect(after.every((pill) => pill.current)).toBe(false);

  // The project's session before anybody has started it: offered, and honestly
  // marked. This is KYO-115.c1's "the app starts it if absent", kept after the
  // bare-by-default open -- and it is the ONE pill that can be there without being
  // in the server's answer.
  const cold = sessionPills([{ name: "other", clients: 1 }], "kyosei-kyosei", null);
  expect(cold[0]).toEqual({
    name: "kyosei-kyosei",
    project: true,
    live: false,
    current: false,
    clients: 0,
  });
  expect(cold.filter((pill) => pill.live).map((pill) => pill.name)).toEqual(["other"]);

  // And a window whose project has no session name at all lists only what is alive.
  expect(sessionPills(alive, null, null).map((pill) => pill.name)).toEqual([
    "another-project",
    "kyosei-kyosei",
    "zzz",
  ]);
});

test("what the pane is attached to is read off the server, not remembered", () => {
  const clients = parseClients("/dev/ttys004\tkyosei-kyosei\n/dev/ttys009\tother\n");
  expect(clients).toEqual([
    { tty: "/dev/ttys004", session: "kyosei-kyosei" },
    { tty: "/dev/ttys009", session: "other" },
  ]);
  expect(attachedSession(clients, "/dev/ttys004")).toBe("kyosei-kyosei");
  // Matched by basename too: tmux and node-pty need not spell the same tty the same
  // way, and an attachment the app cannot see is one highlighted nowhere.
  expect(attachedSession(clients, "ttys009")).toBe("other");
  // A pane whose tty is in nobody's client list is bare, which is the answer after a
  // reader detached with their own prefix key -- something this app is never told.
  expect(attachedSession(clients, "/dev/ttys100")).toBeNull();
  expect(attachedSession(clients, null)).toBeNull();

  // And the session list parses the same way, tolerating tmux's trailing newline.
  expect(parseSessions("a\t0\nb\t3\n")).toEqual([
    { name: "a", clients: 0 },
    { name: "b", clients: 3 },
  ]);
  expect(parseSessions("")).toEqual([]);
});

test("the client is an argv, not a command line typed at a shell (KYO-293.c2)", () => {
  const socket = "/private/sockets/tmux-502/default";
  // The whole of a client pty's command: the socket in front, `attach-session`, the
  // target. An ARGV, handed to node-pty with the tmux binary as the file -- so there is
  // no shell in the pty to be busy, and nothing is quoted for one, because nothing
  // re-splits it. KYO-283's `attachCommandLine` produced a quoted shell line for the
  // pane's own shell to run, which is the defect this record is about: a shell already
  // in a foreground command never read it (KYO-293.c5, that function is gone).
  expect(clientAttachArgs(socket, "KYO")).toEqual(["-S", socket, "attach-session", "-t", "=KYO"]);
  // A socket path with a space in it is one argv element and needs no quoting at all
  // -- the case KYO-89 made this repository's own (`a project` in the checks).
  expect(clientAttachArgs("/s ck", "FIX")).toEqual(["-S", "/s ck", "attach-session", "-t", "=FIX"]);
  // And the funnel really is the funnel: `tmuxArgs` put the socket there.
  expect(clientAttachArgs(socket, "x").slice(0, 2)).toEqual(tmuxArgs(socket, []));
});

test("a click attaches, and there is no branch left that hands over a command", () => {
  const socket = "/private/sockets/tmux-502/default";
  const base: PaneAttachState = {
    binary: "/opt/homebrew/bin/tmux",
    socket,
    attachedTo: null,
    clientTty: null,
  };

  // A pane with no client: start one in that session.
  const fresh = planPaneAttach(base, "kyosei-kyosei");
  expect(fresh.action).toBe("attach");
  expect(fresh.surface).toBe("client");

  // A pane whose client is in another session: the same act, because the client is this
  // app's own pty -- it ends that one and starts another. The sentence has to say the
  // session it is leaving keeps running, because "detach" is the word a reader fears
  // means the other thing.
  const elsewhere = planPaneAttach(
    { ...base, attachedTo: "other", clientTty: "/dev/ttys004" },
    "kyosei-kyosei",
  );
  expect(elsewhere.action).toBe("attach");
  expect(elsewhere.reason).toContain("other keeps running");

  // KYO-293.c5: `prefill` is not reachable, because it is not in the vocabulary. The
  // case it existed for -- attached somewhere and unable to name our own client -- is
  // an ordinary attach now, since the client the app spawns is one it can always end.
  const unnamed = planPaneAttach(
    { ...base, attachedTo: "other", clientTty: null },
    "kyosei-kyosei",
  );
  expect(unnamed.action).toBe("attach");
  expect(ATTACH_ACTIONS).not.toContain("prefill");
  expect(ATTACH_ACTIONS).not.toContain("detach-then-attach");

  // The pill the pane is already in is not re-attached; a flash for nothing.
  expect(planPaneAttach({ ...base, attachedTo: "kyosei-kyosei" }, "kyosei-kyosei").action).toBe(
    "already",
  );
  // No tmux at all: refuse, and say why. There is no "no shell to type into" case any
  // more -- an attach does not need a shell.
  const noTmux = planPaneAttach({ ...base, binary: null, socket: null }, "kyosei-kyosei");
  expect(noTmux.action).toBe("refuse");
  expect(noTmux.reason).toContain("no tmux");
  expect(noTmux.surface).toBe("bare");
});

test("the leftmost control shows the bare shell and detaches a client, never a session", () => {
  const base: PaneAttachState = {
    binary: "/opt/homebrew/bin/tmux",
    socket: "/s/ck",
    attachedTo: "kyosei-kyosei",
    clientTty: "/dev/ttys004",
  };
  const bare = planPaneBare(base);
  expect(bare.action).toBe("bare");
  expect(bare.surface).toBe("bare");
  expect(bare.reason).toContain("keep running");
  // A pane already showing its own shell says what a bare pane is, rather than
  // reporting an error for a control the reader is allowed to press twice.
  expect(planPaneBare({ ...base, attachedTo: null }).reason).toBe(BARE_PANE_NOTICE);
  expect(planPaneBare({ ...base, attachedTo: null }).action).toBe("already");
  // KYO-293.c3, c5: with no tty to name it still shows the shell. KYO-283 refused here
  // -- it could only detach by `detach-client`, and would not guess a tty -- which left
  // the reader with no way back to a shell at all. The client is this app's own pty
  // now, so a signal to it detaches just as well, and this control can always act.
  const unnamed = planPaneBare({ ...base, clientTty: null });
  expect(unnamed.action).toBe("bare");
  expect(unnamed.surface).toBe("bare");
});

test("a client that goes away on its own says WHICH thing happened (KYO-293.c3)", () => {
  // The reader's own prefix key, then `d`. tmux tells the SERVER, never this app, so
  // the only signal here is the client pty exiting -- and the session being alive
  // afterwards is what says the reader did it rather than the session ending.
  const detached = clientGoneNotice("kyosei-kyosei", true);
  expect(detached).toContain("you detached");
  expect(detached).toContain("keep running");
  expect(detached).toContain("click kyosei-kyosei above");

  // The other case: the session is gone, so there is nothing to go back to, and the
  // sentence must not tell the reader to click a pill that is no longer in the row.
  const ended = clientGoneNotice("kyosei-kyosei", false);
  expect(ended).toContain("kyosei-kyosei ended");
  expect(ended).not.toContain("click kyosei-kyosei above");
  // Both end in what the pane IS now, because a reader looking at a shell has to know
  // whether a job started in it survives a quit (law 6).
  for (const said of [detached, ended]) expect(said).toContain(BARE_PANE_NOTICE);
});

test("nothing in this app makes a tmux session, or writes into one's environment (KYO-474.c2)", () => {
  // Until KYO-474 the pane created `kyosei-<project>` on the default server when it was
  // absent, and stated the app's `KYOSEI_*` into it (KYO-115.c1, KYO-265). The session
  // it shows now is the controller's, made by `kyo controller start`, and the app only
  // views it: a session made here would be one more on a server nobody asked it to
  // touch, and a `set-environment` would be writing into somebody else's session.
  // Assembled at runtime, for the reason the funnel scan above gives.
  // (`new-window` is not on the list: it is also this app's own menu command's name.)
  const creating = ["new-session", "set-environment", "show-environment"].map(
    (word) => `"${word}"`,
  );
  const offenders: string[] = [];
  for (const file of everySourceFile(SRC_ROOT)) {
    const text = readFileSync(file, "utf8");
    for (const word of creating) {
      if (text.includes(word)) offenders.push(`${relative(SRC_ROOT, file)}: ${word}`);
    }
  }
  expect(offenders).toEqual([]);
  // What a missing session is answered with instead: a sentence and Start controller.
  expect(ATTACH_ACTIONS).toContain("refuse");
});

// ------------------------------------- KYO-122.c1: the switch that keeps the suite quiet
//
// The parser, checked here rather than by launching apps, for the reason the rest of
// this file exists: an argv rule can be driven directly and fail with a line number.
// The behaviour it selects -- a window that is never shown and never focused -- is
// asserted against a real Electron in `smoke.spec.ts`, where it belongs.

test("the window-show switch is read from argv, last wins, and a typo stays loud", () => {
  // The default is the loud one. A human running `npm start` passes nothing, and a
  // window they cannot see is a worse failure than one that takes focus.
  expect(parseWindowShow([])).toBe("focus");
  expect(parseWindowShow(["/path/to/electron", "."])).toBe("focus");

  for (const show of WINDOW_SHOWS) {
    expect(parseWindowShow(["electron", ".", `${WINDOW_SHOW_SWITCH}=${show}`])).toBe(show);
  }

  // Last occurrence wins, which is what lets a caller append an override to a shared
  // argument list instead of rebuilding it.
  expect(parseWindowShow([`${WINDOW_SHOW_SWITCH}=hidden`, `${WINDOW_SHOW_SWITCH}=focus`])).toBe(
    "focus",
  );

  // A misspelled value falls back to the visible window rather than to no window at
  // all: the operator sees the app and knows their argument did nothing, instead of
  // being handed something indistinguishable from a broken install.
  expect(parseWindowShow([`${WINDOW_SHOW_SWITCH}=hiden`])).toBe("focus");
  expect(parseWindowShow([WINDOW_SHOW_SWITCH])).toBe("focus");
  expect(parseWindowShow([`${WINDOW_SHOW_SWITCH}=`])).toBe("focus");

  // A switch that merely starts the same way is not this one.
  expect(parseWindowShow([`${WINDOW_SHOW_SWITCH}-other=hidden`])).toBe("focus");
});

// ------------------------- KYO-121: the ribbon's status pill groups the ROLE word
//
// The defect showed as a records-mode left pane reading "10 open" beside a
// `Base > Open Work` holding one. Both numbers were right; the pane's word was wrong.
// A knowledge record -- a decision, a piece of evidence -- still stores the initial
// work status in the shared column and READS as `current` (KYO-44.c2, KYO-101.r1), so
// grouping the stored column files every decision in the project under `open`.
//
// That is a law and not a label. `kyo show`, `kyo list` and `kyo html` all print the
// role word precisely so that an `open` in any listing is a real, dispatchable open
// (KYO-46, KYO-101), and this app was the one rendering where that sentence was
// false -- on the surface a reader uses to decide what to work on.

test("a listing's status word is the role word, and the stored one only without it (KYO-365)", () => {
  // The Records list filtered to `open` showed twelve decisions and evidence records
  // beside the one open bug: its filter read the stored column the tree stopped reading
  // at KYO-121.
  expect(listingStatus({ status: "open", standing: "current" })).toBe("current");
  expect(listingStatus({ status: "open", standing: "void" })).toBe("void");
  expect(listingStatus({ status: "open", standing: "open" })).toBe("open");
  // The direct-read fallback has no registry to derive the word from; a row with no
  // filter value would vanish from the list, so it keeps the stored word.
  expect(listingStatus({ status: "done", standing: null })).toBe("done");
  expect(listingStatus({ status: "done", standing: "" })).toBe("done");
});

test("the status ribbon groups by standing, so an `open` bucket is dispatchable work", async () => {
  // The unit half, and it is here rather than inline in the assertions below so that
  // pointing the tree back at the raw column fails with the law rather than with a
  // diff of two strings (KYO-121.c4).
  expect(
    groupingField("status"),
    "The folder tree's status ribbon must group by `standing`, the role word, not by " +
      "the stored `status` column. KYO-46/KYO-101: kyo show, kyo list and kyo html all " +
      "print the role word so that an `open` in ANY listing is a real, dispatchable " +
      "open. Grouping the stored value files every decision and every piece of " +
      "evidence under a work status and makes that sentence false in this app.",
  ).toBe("standing");
  expect(TREE_GROUPING_FIELDS.kind).toBe("kind");
  expect(TREE_GROUPING_FIELDS.scope).toBe("scope");

  const fixture = await makeRecordsFixture();
  try {
    const tree = await readRecordTree(fixture.project, "status");
    expect(tree.ok, tree.refusal ?? "").toBe(true);
    // The receipt: the subprocess really asked for the role word.
    expect(tree.command).toContain("--group-by standing");
    expect(tree.command).not.toContain("--group-by status");

    // Every bucket the pane draws is the standing the row itself carries, so the
    // group heading and the record it holds cannot disagree.
    const standingOf = new Map(tree.records.map((row) => [row.id, row.standing]));
    for (const group of tree.groups) {
      for (const id of group.ids) {
        expect(standingOf.get(id), `${id} is filed under ${group.value}`).toBe(group.value);
      }
    }

    // The defect itself, over a store built to hold it: FIX-2 is an evidence record,
    // stored `open` and read `current`. Grouping the raw column puts it in the open
    // bucket; grouping the role word does not.
    const evidence = tree.records.find((row) => row.id === "FIX-2");
    expect(evidence?.kind).toBe("evidence");
    expect(evidence?.status, "the fixture no longer holds a knowledge record filed open").toBe(
      "open",
    );
    expect(evidence?.standing).toBe("current");
    const openBucket = tree.groups.find((group) => group.value === "open");
    expect(openBucket?.ids ?? [], "a knowledge record is in the tree's open bucket").not.toContain(
      "FIX-2",
    );

    // And the pane's open bucket is `kyo list --status open`'s answer over the same
    // store, id for id -- which is the sentence the two disagreeing numbers broke.
    const listed = await runKyoJson(fixture.resolution, ["list", "--status", "open"], {
      cwd: fixture.root,
    });
    expect(listed.code, listed.stderr).toBe(0);
    const listedRows = (listed.data?.records ?? []) as { id: string }[];
    expect(
      listedRows.length,
      "kyo list --status open answered nothing to compare against",
    ).toBeGreaterThan(0);
    const listedIds = listedRows.map((row) => row.id).sort();
    expect([...(openBucket?.ids ?? [])].sort(), "the tree's open bucket is not kyo list's").toEqual(
      listedIds,
    );

    // The other half of KYO-121.c3: nothing about what the engine ships changed. The
    // raw column is still projected on every row the tree carries, beside the role
    // word rather than instead of it.
    for (const row of tree.records) {
      expect(row.status, `${row.id} lost its stored status`).not.toBe("");
      expect(row.standing, `${row.id} lost its standing`).not.toBe("");
    }
    const everything = await runBase(fixture.project, "everything");
    expect(everything.ok, everything.refusal ?? "").toBe(true);
    for (const row of everything.rows) {
      expect(
        row.status,
        "the everything base stopped declaring the raw status column",
      ).toBeDefined();
    }
  } finally {
    removeFixture(fixture);
  }
});

// -------------------------- KYO-122.c11: the space goes to the picture, not a band

// ------------------------------------------------- one pane, one project (KYO-218)

test("an answer about a project that moved while it was being asked is asked again", async () => {
  // The rule `startSession` opens a pane under, driven directly, because `main.ts`
  // cannot be driven without an Electron and this can.
  //
  // A pane is opened from two facts about one project: the directory its pty starts
  // in, and the per-project home its tmux session is named after. Only the second
  // costs a subprocess, so only the second can be overtaken by a human switching
  // project -- and the pane that comes out then reports the new project's directory
  // under the previous project's session name. That is KYO-218 as it was reported,
  // and this is the shape of it with the timing made deliberate.
  let root = "/projects/kyosei";
  const asked: string[] = [];
  const moved = await askedAboutOneProject(
    () => root,
    async () => {
      const about = root;
      asked.push(about);
      // The switch, landing exactly where it has to land to do damage: after the
      // question was asked about one project and before its answer is used.
      if (asked.length === 1) root = "/projects/a project";
      return `home of ${about}`;
    },
  );
  // The answer is the one that belongs to the project the caller will read `root`
  // for. Nothing here waited longer or tried again after a failure: the first answer
  // was correct about a question nobody is asking any more.
  expect(asked).toEqual(["/projects/kyosei", "/projects/a project"]);
  expect(moved).toEqual({ root: "/projects/a project", answer: "home of /projects/a project" });

  // And a project that stays put is asked exactly once. The re-ask is a response to
  // the question changing, not a retry every open pays for.
  const still: string[] = [];
  const stayed = await askedAboutOneProject(
    () => "/projects/a project",
    async () => {
      still.push("asked");
      return "home of /projects/a project";
    },
  );
  expect(still).toEqual(["asked"]);
  expect(stayed).toEqual({ root: "/projects/a project", answer: "home of /projects/a project" });
});

// ================================================ KYO-231: the v3 read model
//
// Four queries, driven directly against a records fixture. No Electron: every one of
// them is an Electron-free module on purpose, so a failure here is a line number.
//
// What these do NOT check is whether a renderer draws any of it -- there is no v3
// renderer yet (stage 2 and 3 build it). What they check is that the main process
// produces the shapes stage 3 was promised, that the shapes carry what the CLI says
// rather than a second derivation of it, and that every absence is a named one.

/** The repository's own store, when this checkout has a built one. */
function thisStore(): string | null {
  const cliRoot = testResolution().cliRoot;
  if (cliRoot === null) return null;
  return existsSync(dbPathFor(cliRoot)) ? cliRoot : null;
}

const SKIP_NO_STORE =
  "this checkout has no built `kyosei/kyosei.db` -- the ordinary state of a dispatch " +
  "worktree, where it is gitignored. The comparison below is against THIS store's " +
  "KYO-215 by name (KYO-231.c1), so it runs in a clone that has been worked in and " +
  "says so where there is none. `kyo rebuild` in the store is what creates one.";

test("the record document carries every child object of a record, in id order", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const document = await readRecordDocument(fixture.project, "FIX-1");
    expect(document.ok, document.refusal ?? "").toBe(true);
    expect(document.id).toBe("FIX-1");
    expect(document.title).toBe("A defect with a gate");

    // Every child kind the record's description names, present as its own collection.
    expect(document.criteria.length).toBe(2);
    expect(document.criteria[0]?.id).toBe("FIX-1.c1");
    // The verification history hangs off the criterion, not off a second query.
    expect(Array.isArray(document.criteria[0]?.verifications)).toBe(true);
    expect(document.gates.length).toBe(1);
    expect(document.claims.length).toBe(1);
    expect(document.links.length).toBeGreaterThan(0);
    // Touches live on FIX-4, which is the fixture record `kyo spawn --touches-file`
    // made; FIX-1 declares none, and an empty collection is the honest answer for it.
    expect(document.touches).toEqual([]);

    // KYO-88: the claims stand BESIDE the gate. That join is done here, once, so no
    // tab that draws a gate has to do it.
    expect(document.gates[0]?.claims.map((claim) => claim.id)).toEqual(
      document.claims.map((claim) => claim.id),
    );
    expect(document.gates[0]?.open).toBe(true);

    // The description, which is a record's own prose and not a child object. FIX-2 is
    // the fixture's long one, written precisely so something in this store has one.
    const long = await readRecordDocument(fixture.project, "FIX-2");
    expect(long.description).toContain("A record longer than any pane");

    const spawned = await readRecordDocument(fixture.project, "FIX-4");
    expect(spawned.touches.length).toBeGreaterThan(0);
    expect(spawned.touches[0]?.id).toBe("FIX-4.t1");

    // Id order, which for a child object is its allocation counter (law 4).
    const dispatched = await readRecordDocument(fixture.project, "FIX-3");
    expect(dispatched.criteria.map((criterion) => criterion.id)).toEqual(["FIX-3.c1", "FIX-3.c2"]);
    // A dispatch carries the report its own `report_id` names (KYO-90), with verdicts.
    expect(dispatched.dispatches.length).toBe(1);
    expect(dispatched.dispatches[0]?.currentReport).not.toBeNull();

    // And this is a surface painting store facts, so it carries the verdict (KYO-74).
    expect(document.currency.state).toBe("current");
  } finally {
    removeFixture(fixture);
  }
});

test("a knowledge record renders `current`, not a work status", async () => {
  // KYO-121's difference, which is the whole of this half of c1: FIX-2 is filed
  // `open` in the `records` table and READS `current`, because its kind is a
  // knowledge archetype and `open` is not a word that archetype has. Both are
  // carried -- the stored column and the role word -- because a page showing only one
  // of them either hides what the store holds or prints a word the kind cannot have.
  const fixture = await makeRecordsFixture();
  try {
    const knowledge = await readRecordDocument(fixture.project, "FIX-2");
    expect(knowledge.ok).toBe(true);
    expect(knowledge.status).toBe("open");
    expect(knowledge.standing).toBe("current");

    const work = await readRecordDocument(fixture.project, "FIX-4");
    expect(work.status).toBe("working");
    expect(work.standing).toBe("working");
  } finally {
    removeFixture(fixture);
  }
});

test("the obligation collapse is the CLI's own, on this store's KYO-215", async () => {
  // KYO-231.c1's comparison, named by the criterion and run against the record it
  // names. REUSE rather than a port: `docs_check.collapse_obligations` computes the
  // grouping, `kyo show --json` carries it under `docs_obligation_groups`, and
  // `obligationsFrom` maps snake_case onto camelCase. So what this asserts is that
  // nothing between the two re-derived anything -- every id, every count, every
  // settled_why, in the same order.
  const root = thisStore();
  test.skip(root === null, SKIP_NO_STORE);
  const resolution = testResolution();
  const served = await serveCheckout(root as string);
  const document = await readRecordDocument(served.project, "KYO-215").finally(served.stop);
  expect(document.ok, document.refusal ?? "").toBe(true);
  const raw = await runKyo(resolution, ["show", "KYO-215", "--json"], { cwd: root as string });
  const envelope = JSON.parse(raw.stdout) as Record<string, unknown>;
  const theirs = envelope.docs_obligation_groups as Record<string, unknown>;
  expect(theirs, "`kyo show --json` no longer carries docs_obligation_groups").toBeTruthy();

  expect(document.obligations.total).toBe(theirs.total);
  expect(document.obligations.open).toBe(theirs.open);
  expect(document.obligations.countsByState).toEqual(
    (theirs.counts_by_state as { state: string; count: number }[]).map((entry) => ({
      state: entry.state,
      count: entry.count,
    })),
  );
  const theirGroups = theirs.groups as Record<string, unknown>[];
  expect(document.obligations.groups.length).toBe(theirGroups.length);
  document.obligations.groups.forEach((group, index) => {
    const other = theirGroups[index] as Record<string, unknown>;
    expect(group.state).toBe(other.state);
    expect(group.settledWhy).toBe(other.settled_why);
    expect(group.count).toBe(other.count);
    expect(group.obligationIds).toEqual(other.obligation_ids);
    expect(group.details.map((detail) => detail.detail)).toEqual(
      (other.details as { detail: string }[]).map((detail) => detail.detail),
    );
  });
  // Every obligation is a member of exactly one group -- the property the collapse
  // exists to keep, checked here rather than assumed from the totals.
  const listed = document.obligations.groups.flatMap((group) => group.obligationIds);
  expect(new Set(listed).size).toBe(document.obligations.total);
  // And there is no second implementation of the grouping in this app to drift from
  // it: `obligationsFrom` reads the envelope, and nothing else groups an obligation.
  for (const file of everySourceFile(SRC_ROOT)) {
    const text = readFileSync(file, "utf8");
    if (file === join(SRC_ROOT, "main", "records.ts")) continue;
    expect(text.includes("settled_why"), `${file} groups obligations of its own`).toBe(false);
  }
});

test("awaiting-human returns open gates of both types, whatever the record's status", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const run = (args: string[]) => kyo(fixture.resolution, args, fixture.root);
    // One on an `open` record and one on a `working` one, which is the point: a gate
    // on work in progress is the same claim on a person's attention as a gate on work
    // that has not started, and a view that only looked at open work would hide half.
    await run(["gate", "FIX-3", "awaiting:human-review"]);
    await run(["gate", "FIX-4", "awaiting:human-run", "--do", "run the migration by hand"]);

    const waiting = await readAwaitingHuman(fixture.project);
    expect(waiting.ok, waiting.problem ?? "").toBe(true);
    expect(waiting.count).toBe(2);
    expect(waiting.gates.map((entry) => entry.recordId).sort()).toEqual(["FIX-3", "FIX-4"]);
    // Across statuses, stated as the assertion rather than implied by the count.
    expect(waiting.gates.map((entry) => entry.recordStatus).sort()).toEqual(["open", "working"]);
    // The payload and the record's title, which is what a badge's list has to draw.
    const run4 = waiting.gates.find((entry) => entry.recordId === "FIX-4");
    expect(run4?.gate.detail).toBe("run the migration by hand");
    expect(run4?.recordTitle).toBe("A task spawned from the defect");
    expect(run4?.gate.open).toBe(true);
    // FIX-1's gate is `holding-for`, which waits on the world rather than on a person,
    // and is deliberately not here.
    expect(waiting.gates.map((entry) => entry.recordId)).not.toContain("FIX-1");
    expect(waiting.types).toEqual(["awaiting:human-run", "awaiting:human-review"]);

    // Clearing one takes it out, which is what makes the badge a live count.
    await run(["ungate", `${run4?.gate.id}`, "--why", "ran it"]);
    expect((await readAwaitingHuman(fixture.project)).count).toBe(1);
  } finally {
    removeFixture(fixture);
  }
});

test("the records list searches by id and title, and runs the store's own bases", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const all = await readRecordsList(fixture.project);
    expect(all.ok, all.problem ?? "").toBe(true);
    expect(all.rows.length).toBe(5);
    // The saved bases come with it, so a picker never needs a second call. As a SET:
    // the CLI lists them in the order `kyosei/bases.yml` declares them, and pinning
    // that order here would be this check asserting a fixture's file layout.
    expect([...all.bases.map((base) => base.name)].sort()).toEqual([...fixture.bases].sort());

    // By id, and by title, case-insensitively -- both of them, because a person
    // reaching for a record types whichever they remember.
    expect((await readRecordsList(fixture.project, { query: "fix-2" })).rows).toHaveLength(1);
    const byTitle = await readRecordsList(fixture.project, { query: "no scope" });
    expect(byTitle.rows.map((row) => row.id).sort()).toEqual(["FIX-2", "FIX-3"]);
    expect(byTitle.query).toBe("no scope");

    // A saved base answers instead, and its rows carry the engine's `standing`, which
    // a direct read of the `records` table does not hold and does not invent.
    const open = await readRecordsList(fixture.project, { base: "open-work" });
    expect(open.ok, open.problem ?? "").toBe(true);
    expect(open.base).toBe("open-work");
    expect(open.rows.every((row) => row.status === "open")).toBe(true);
    // The unfiltered list carries the role word too (KYO-365), asked of the CLI: FIX-2
    // is a knowledge record stored `open` that reads `current`, and a Status filter
    // built from these rows must not file it beside the open bugs.
    const decision = all.rows.find((row) => row.id === "FIX-2");
    expect(decision?.status).toBe("open");
    expect(decision?.standing).toBe("current");
    expect(listingStatus(decision ?? { status: "", standing: null })).toBe("current");
    expect(all.rows.every((row) => row.standing !== null)).toBe(true);

    // A base the CLI refuses surfaces the CLI's refusal, never an empty list.
    const refused = await readRecordsList(fixture.project, { base: "no-such" });
    expect(refused.ok).toBe(false);
    expect(refused.problem ?? "").not.toBe("");
  } finally {
    removeFixture(fixture);
  }
});

// ============================================== KYO-290: the readiness filter's value
//
// The filter itself is drawn by `shell/sidebar.ts` and needs a window; what it reads
// does not, and this is that half. The criterion (KYO-290.c1) turns on the class being
// the CLI's and not the app's, which is a claim about `readRecordsList` and about the
// group's vocabulary in `shared/protocol.ts` -- both Electron-free, both driven here
// against a real store rather than against a stub of one.

test("the readiness filter's vocabulary covers every board class exactly once", async () => {
  // The chips the box draws, and the label on each. A class added to `BOARD_CLASSES`
  // with no label fails the typecheck; one left out of the ORDER would simply never be
  // offered, which nothing but this would notice.
  expect([...BOARD_FILTER_ORDER].sort()).toEqual([...BOARD_CLASSES].sort());
  expect(Object.keys(BOARD_FILTER_LABELS).sort()).toEqual([...BOARD_CLASSES].sort());
  // The label the filter gives the class `kyo next` calls `ready`.
  expect(boardFilterLabel("ready")).toBe("dispatchable");
  // `settled-not-moved` is its own value and not a flavour of dispatchable: KYO-87 is
  // the record that exists because finished work counted as ready, and a group that
  // labelled the two the same would have reintroduced it in the filter box.
  expect(boardFilterLabel("settled-not-moved")).not.toBe(boardFilterLabel("ready"));
  // KYO-571: a record an unsettled blocker holds back is offered under its own label,
  // never as dispatchable -- `kyo next` stopped calling it ready in KYO-570.
  expect(BOARD_FILTER_ORDER).toContain("blocked");
  expect(boardFilterLabel("blocked")).toBe("blocked");
  expect(boardFilterLabel("blocked")).not.toBe(boardFilterLabel("ready"));
  // A value from a store this build has never heard of is drawn as itself, not hidden.
  expect(boardFilterLabel("some-future-class")).toBe("some-future-class");
  // The value function reads the stamped class and computes nothing.
  expect(boardFilterValue({ boardClass: "ready" })).toBe("ready");
});

test("every records-list row carries the board's own class, so the filter derives nothing", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const board = await readBoardClasses(fixture.project);
    expect(board.problem, board.problem ?? "").toBeNull();
    const list = await readRecordsList(fixture.project);
    expect(list.ok, list.problem ?? "").toBe(true);
    expect(list.boardProblem).toBeNull();

    // Row for row, the class is `classOf(board, id)` -- the same function the folder
    // tree and the record page read, over the same `kyo next --json`. This is what
    // KYO-290.c1 means by "not re-derived": there is no second rule here to disagree
    // with the CLI's the day either one changes.
    for (const row of list.rows) {
      expect(row.boardClass, row.id).toBe(classOf(board, row.id));
      expect(boardFilterValue(row), row.id).toBe(row.boardClass);
    }

    // And the set the filter would keep is `kyo next`'s ready list and nothing else.
    // The fixture is built so neither side is empty: FIX-5 is a task with touches and
    // nothing holding it, FIX-3 is dispatched, reported, every criterion met and never
    // moved -- KYO-87's class, which a filter on "dispatchable" must not return.
    const dispatchable = list.rows
      .filter((row) => boardFilterValue(row) === "ready")
      .map((row) => row.id)
      .sort();
    expect(dispatchable).toEqual([...board.ready].sort());
    expect(dispatchable.length, "the fixture offers no dispatchable record").toBeGreaterThan(0);
    expect(dispatchable).not.toContain("FIX-3");
    expect(list.rows.find((row) => row.id === "FIX-3")?.boardClass).toBe("settled-not-moved");

    // KYO-571: FIX-4 is held back by FIX-1's open `blocks` edge, so the Readiness
    // group's `blocked` value keeps it and `dispatchable` does not.
    const blocked = list.rows
      .filter((row) => boardFilterValue(row) === "blocked")
      .map((row) => row.id)
      .sort();
    expect(blocked).toEqual([...board.blocked].sort());
    expect(blocked).toContain("FIX-4");
    expect(dispatchable).not.toContain("FIX-4");

    // A saved base answers the same way: the class rides on the row whichever source
    // produced it, so the filter does not quietly stop working when a base is running.
    const open = await readRecordsList(fixture.project, { base: "open-work" });
    expect(open.ok, open.problem ?? "").toBe(true);
    for (const row of open.rows) expect(row.boardClass, row.id).toBe(classOf(board, row.id));
  } finally {
    removeFixture(fixture);
  }
});

test("a board that cannot be read leaves the list whole and says why, rather than reading as empty", async () => {
  // KYO-290.c2: every row still there, and no class for any of them -- the state in
  // which a filter box that still drew its chips would answer "dispatchable" with an
  // empty list, which reads as "nothing is dispatchable". Until KYO-459 a broken
  // `kinds.yml` produced it (the list was read by SQL, the board by `kyo next`); since
  // the list is `kyo list` too, a broken registry fails both, so the board's own read
  // is made to fail here -- `kyo next` refused by the served project, the list asked
  // of the real server.
  const fixture = await makeRecordsFixture();
  try {
    class BlindBoard extends ServedProject {
      override async json(args: readonly string[]) {
        if (args[0] === "next") {
          return { code: 1, data: null, stderr: "the board is unreadable in this check" };
        }
        return super.json(args);
      }
    }
    const project = new BlindBoard(fixture.project.client, fixture.root);
    const board = await readBoardClasses(project);
    expect(board.problem ?? "").not.toBe("");
    expect(board.ready.size).toBe(0);

    const list = await readRecordsList(project);
    // The list itself is FINE: this is a filter that went blind, not a failed read,
    // and failing the whole list over it would be the worse answer.
    expect(list.ok, list.problem ?? "").toBe(true);
    expect(list.rows.length).toBe(5);
    expect(list.problem).toBeNull();
    // Every row falls to `none` -- which is why the class alone cannot be trusted here,
    // and why the problem travels beside it.
    expect(list.rows.every((row) => row.boardClass === "none")).toBe(true);
    expect(list.boardProblem ?? "").not.toBe("");
    expect(list.boardProblem).toBe(board.problem);
    // What the group prints instead of its chips names the command and carries the
    // CLI's own words, so a reader is told which half of the box is blind and why.
    const says = boardFilterUnavailable(list.boardProblem ?? "");
    expect(says).toContain("kyo next");
    expect(says).toContain("dispatchable");
    expect(says).toContain(list.boardProblem ?? "");
  } finally {
    removeFixture(fixture);
  }
});

test("the activity rows join the store's half to git's, and a gone worktree is a fact", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const view = await readActivity(fixture.project);
    expect(view.ok, view.problem ?? "").toBe(true);
    // The fixture's one dispatch: FIX-3, dispatched, reported, every criterion met,
    // and its record still `open` -- which is KYO-87's class by construction.
    const row = view.rows.find((entry) => entry.dispatchId === "FIX-3.d1");
    expect(row, "the fixture's dispatch is not in the activity rows").toBeDefined();
    expect(row?.recordTitle).toBe("A defect with no scope");
    expect(row?.state).toBe("reported");
    expect(row?.reportId).not.toBeNull();
    expect(row?.verdictCounts).toEqual([{ verdict: "met", count: 2 }]);
    expect(row?.classes).toContain("finished-but-not-moved");
    expect(row?.classes).not.toContain("running");

    // The git half. Whatever `kyo dispatch` recorded for this fixture's worktree, the
    // row carries a NAMED outcome and a sentence -- never a throw and never a blank.
    expect([...GIT_FACTS_OUTCOMES]).toContain(row?.git.outcome);
    expect(row?.git.why).not.toBe("");

    // Since KYO-459 the git half is `kyo activity`'s (`git_facts`, read where the
    // project is, under the "a missing directory is a fact" rule its own tests hold);
    // the app renames it and derives nothing. Its named outcomes are the protocol's.
    expect(row?.git.present).toBe(
      row?.git.outcome === "read" || row?.git.outcome === "not-a-git-worktree",
    );

    // The window: running dispatches in full, then the newest finished ones.
    const narrowed = await readActivity(fixture.project, { finished: 0 });
    expect(narrowed.rows).toHaveLength(0);
    expect(narrowed.running).toBe(0);
    expect(view.readAt).not.toBe("");
  } finally {
    removeFixture(fixture);
  }
});

test("the latest brief comes back as sections with every id intact", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const brief = await readLatestBrief(fixture.project);
    expect(brief.ok, brief.problem ?? "").toBe(true);
    // The fixture runs `kyo brief --session-start` twice with a shift note between, so
    // the latest brief has a non-empty window to reconstruct.
    expect(brief.run?.id).toMatch(/^FIX-B\d+$/);
    expect(brief.previousBriefId).toMatch(/^FIX-B\d+$/);
    expect(brief.sections.map((section) => section.key)).toEqual(["shift-notes", "events"]);

    const notes = brief.sections.find((section) => section.key === "shift-notes");
    expect(notes?.items).toHaveLength(1);
    // Every id preserved, which is what lets a renderer link the row (KYO-230's
    // resolver takes `FIX-S1` and `FIX-E12` without being told what they are).
    expect(notes?.items[0]?.id).toMatch(/^FIX-S\d+$/);
    expect(notes?.items[0]?.text).toContain("A shift note the calendar tool should surface");

    const events = brief.sections.find((section) => section.key === "events");
    expect(events?.items.length ?? 0).toBeGreaterThan(0);
    // Every id preserved, on every row -- which is what c4 asks for and what lets a
    // renderer link a row without parsing anything. `recordId` is null here because
    // the one command in this brief's window is `kyo note --shift`, which is about the
    // session rather than about a record; the field carries the store's answer either
    // way rather than being omitted when it is null.
    expect(events?.items.every((item) => /^FIX-E\d+$/.test(item.id))).toBe(true);
    expect(events?.items.every((item) => item.at !== "" && item.actor !== "")).toBe(true);
    // Each event names the command that wrote it -- the store's own word, carried
    // rather than translated, so a renderer shows what the ledger says.
    expect(events?.items.every((item) => item.text !== "")).toBe(true);

    // And nothing here regenerates a brief: the store never kept the prose, and
    // rendering a freshly-computed board under an old date would be inventing one.
    expect(brief.run?.at).not.toBe("");
  } finally {
    removeFixture(fixture);
  }
});

test("every v3 read paints the currency, in all three states that are not `current`", async () => {
  const fixture = await makeRecordsFixture();
  try {
    // The three directions, produced the way `fixture.ts` produces them: the counter
    // in `kyosei/export/_allocations.json` moved one way or the other, and a record
    // document hidden so the two sides' RECORD SETS disagree while the counters do not.
    const counter = readAllocations(fixture).record as number;
    expect(counter).toBeGreaterThan(0);

    setAllocation(fixture, "record", counter + 50);
    const exportAhead = await readRecordDocument(fixture.project, "FIX-1");
    expect(exportAhead.currency.state).toBe("export-ahead");
    expect(exportAhead.currency.remedy).not.toBe("");

    setAllocation(fixture, "record", 1);
    const dbAhead = await readRecordsList(fixture.project);
    expect(dbAhead.currency.state).toBe("database-ahead");

    setAllocation(fixture, "record", counter);
    hideExportedRecord(fixture, "FIX-2");
    const cannotTell = await readLatestBrief(fixture.project);
    expect(cannotTell.currency.state).toBe("cannot-tell");
    // KYO-74's obligation: the verdict reaches the surface with the CLI's own sentence
    // and its own remedy, not a paraphrase composed here.
    expect(cannotTell.currency.says).not.toBe("");
    expect(cannotTell.currency.from).toBe("kyo list --json");
  } finally {
    removeFixture(fixture);
  }
});

test("all four v3 queries are on the bridge, with their types in the shared protocol", () => {
  // c4's other half, and it is a real check rather than a restatement: the bridge is
  // the whole of what a renderer can reach, so a query that exists in `records.ts` and
  // not here is a query stage 3 cannot call. Asserted against the three files that
  // have to agree -- the channel, the preload entry, and the typed method.
  const protocol = readFileSync(join(SRC_ROOT, "shared", "protocol.ts"), "utf8");
  const preload = readFileSync(join(SRC_ROOT, "main", "preload.ts"), "utf8");
  const main = readFileSync(join(SRC_ROOT, "main", "main.ts"), "utf8");
  const wired: [string, string][] = [
    ["recordDocument", "getRecordDocument"],
    ["awaitingHuman", "getAwaitingHuman"],
    ["recordsList", "getRecordsList"],
    ["activity", "getActivity"],
    ["latestBrief", "getLatestBrief"],
  ];
  for (const [channel, method] of wired) {
    expect(protocol, `${channel} is not a channel`).toContain(`${channel}: "kyosei:`);
    expect(protocol, `${method} is not on KyoseiBridge`).toContain(`${method}(`);
    expect(preload, `${method} is not exposed`).toContain(`${method}:`);
    expect(main, `${channel} has no handler`).toContain(`CHANNEL.${channel}`);
  }
  // The push, which is the one thing here that is not a request.
  expect(protocol).toContain('activityChanged: "kyosei:');
  expect(preload).toContain("onActivityChanged:");
  expect(main).toContain("CHANNEL.activityChanged");
  // And nothing in this block writes. The v3 tabs are read-only to a human by the
  // same mechanism records mode is: there is no method here that could change a store.
  for (const [, method] of wired) {
    expect(method.startsWith("get")).toBe(true);
  }
});

// ================================= KYO-310: a showing list follows the store
//
// Two seams, both Electron-free and both driven here rather than through a window.
//
//   THE DECISION. `storeReloadDue` is the whole of "is a reload due" -- which list is
//   showing, which world moved, and whether a read is already out. "A list that is
//   not showing costs nothing" is a claim about work NOT done, and this is where it
//   can be made as an assertion rather than as an absence of evidence.
//   THE SIGNAL. The project's kyo serve's events stream (KYO-459) against a REAL
//   store with the REAL CLI writing to it: `kyo new` run in the terminal pane is the
//   reported case, and a mock of it would be a mock of the one thing this record is
//   about. The fingerprint itself is `serve_events.py`'s, with its own Python checks.

test("KYO-310: a list that is not showing costs nothing, and each list follows its own world", () => {
  const due = (showing: string | null, changed: StoreChangeKind, inFlight = false) =>
    storeReloadDue({ showing, changed, inFlight });

  // The collapsed sidebar: no list is showing, so nothing is re-read whatever moved.
  expect(due(null, "store")).toBe(false);
  expect(due(null, "ideas")).toBe(false);

  // The three lists the record names, each following the world it actually reads.
  expect(due("records", "store")).toBe(true);
  expect(due("notes", "store")).toBe(true);
  expect(due("ideas", "ideas")).toBe(true);

  // ...and NOT the other one. A controller filing records must not re-read `ideas/`,
  // and somebody saving an idea must not re-run the records list's `kyo` subprocess.
  expect(due("records", "ideas")).toBe(false);
  expect(due("ideas", "store")).toBe(false);

  // Activity has a live push of its own; docs is a fact about the working tree.
  expect(due("activity", "store")).toBe(false);
  expect(due("docs", "store")).toBe(false);
  expect(LIST_FOLLOWS.activity).toBe(null);
  expect(LIST_FOLLOWS.docs).toBe(null);

  // A read already out answers the change that arrived after it started.
  expect(due("records", "store", true)).toBe(false);

  // A list nobody has heard of follows nothing, rather than following everything.
  expect(due("something-new", "store")).toBe(false);
});

test("KYO-310: the stream says the store's own change, and not a read, through kyo serve (KYO-459)", async () => {
  const fixture = await makeFixture(1);
  const seen: string[] = [];
  const stop = fixture.project.subscribe((kind) => seen.push(kind));
  /** Wait until `accept` holds, or give up -- the server stamps once a second. */
  const until = async (accept: () => boolean, ms = 20_000): Promise<void> => {
    const deadline = Date.now() + ms;
    while (!accept() && Date.now() < deadline) await new Promise((r) => setTimeout(r, 50));
  };
  try {
    // The stream's first fingerprint is taken when it opens; changes after it count.
    await new Promise((r) => setTimeout(r, 1_500));
    // A record filed from a terminal: the case KYO-310 was filed for.
    await kyo(fixture.resolution, ["new", "bug", "--title", "Filed from a terminal"], fixture.root);
    await until(() => seen.includes("store"));
    expect(seen).toContain("store");
    expect(seen).not.toContain("ideas");

    // A read-only command is not a change: the fingerprint is what keeps a controller
    // reading beside the app from re-running every list.
    await new Promise((r) => setTimeout(r, 1_500));
    const quiet = seen.length;
    await kyo(fixture.resolution, ["show", "FIX-1", "--json"], fixture.root);
    await kyo(fixture.resolution, ["list", "--json"], fixture.root);
    await new Promise((r) => setTimeout(r, 2_500));
    expect(seen.slice(quiet)).toEqual([]);

    // `ideas/` is its own world, with its own writer: an `ideas` change, not a `store`
    // one, so the records list does not re-read for it.
    writeFileSync(join(fixture.root, "ideas", "a thought.md"), "# a thought\n", "utf8");
    await until(() => seen.slice(quiet).includes("ideas"));
    expect(seen.slice(quiet)).toEqual(["ideas"]);

    // Stopped is stopped: nothing arrives after it, which is what a project no window
    // is open on gets.
    stop();
    const before = seen.length;
    await kyo(
      fixture.resolution,
      ["new", "bug", "--title", "After the stream stopped"],
      fixture.root,
    );
    await new Promise((r) => setTimeout(r, 2_000));
    expect(seen.length).toBe(before);
  } finally {
    stop();
    removeFixture(fixture);
  }
});

test("KYO-310: a stream whose server went away says so, and says so again when it is back", async () => {
  // The client's own `server` kind (KYO-378.c4, made by `kyoserve.ts`): the sidebar
  // shows its list again the way a first load does, naming the server when it fails.
  const fixture = await makeFixture(0);
  const seen: string[] = [];
  const project = clientFor(fixture.serve.address, true).project(fixture.root);
  const stop = project.subscribe((kind) => seen.push(kind));
  let again: Awaited<ReturnType<typeof startKyoServe>> | null = null;
  try {
    await new Promise((r) => setTimeout(r, 1_500));
    await stopKyoServe(fixture.serve);
    await expect
      .poll(() => seen.filter((kind) => kind === "server").length, { timeout: 10_000 })
      .toBe(1);
    again = await startKyoServe(fixture.home, {}, fixture.serve.address.port);
    await expect
      .poll(() => seen.filter((kind) => kind === "server").length, { timeout: 15_000 })
      .toBe(2);
  } finally {
    stop();
    if (again !== null) await stopKyoServe(again);
    removeFixture(fixture);
  }
});

test("KYO-310: the push is wired end to end -- channel, preload entry and typed method", () => {
  // The same three-file agreement the v3 read model is held to above, for the one
  // channel this record adds: a push that exists in `main.ts` and not in the preload
  // is a push no renderer can ever receive.
  const protocol = readFileSync(join(SRC_ROOT, "shared", "protocol.ts"), "utf8");
  const preload = readFileSync(join(SRC_ROOT, "main", "preload.ts"), "utf8");
  const main = readFileSync(join(SRC_ROOT, "main", "main.ts"), "utf8");
  const shell = readFileSync(join(SRC_ROOT, "renderer", "shell", "shell.ts"), "utf8");
  expect(protocol).toContain('storeChanged: "kyosei:');
  expect(protocol).toContain("onStoreChanged(");
  expect(preload).toContain("onStoreChanged:");
  expect(main).toContain("CHANNEL.storeChanged");
  expect(shell).toContain("onStoreChanged(");
  // And the stream is armed and swept rather than left running for a project nobody
  // has a window on -- and it is kyo serve's, not a watch of this app's own.
  expect(main).toContain("armStoreWatch(");
  expect(main).toContain("sweepStoreWatches()");
  expect(existsSync(join(SRC_ROOT, "main", "storewatch.ts"))).toBe(false);
});

// ------------------------------------------ KYO-465: a project is (server, root), remembered

test("a project last used through a kyo serve reopens through it, never as this machine's project at the same root (KYO-465.c1)", () => {
  const dir = mkdtempSync(join(tmpdir(), "kyosei kyo465 state "));
  try {
    const root = "/Users/me/coding-projects/kyosei";
    const remote = { host: "devbox.tailnet.ts.net", port: 443 };
    // This machine's copy was opened first; then the remote one's, at the same path.
    let state = withRecent(readProjectState(dir), { root, connection: null });
    state = withRecent(state, { root, connection: remote });
    state = withWindows(state, [
      { root, connection: remote },
      { root: "/elsewhere", connection: null },
    ]);
    expect(writeProjectState(dir, state)).toBeNull();

    // What the next launch reads back and opens: each window through its own server.
    const back = readProjectState(dir);
    expect(back.chosen).toEqual({ root, connection: remote });
    expect(windowsAtLaunch(back, undefined)).toEqual([
      { root, connection: remote },
      { root: "/elsewhere", connection: null },
    ]);
    // With no window remembered, the one chosen -- still through the remote machine's server.
    const chosenOnly = { ...back, windows: undefined };
    expect(windowsAtLaunch(chosenOnly, undefined)).toEqual([{ root, connection: remote }]);
    expect(
      resolveProjectRoot({ environment: undefined, chosen: back.chosen, checkout: "/checkout" }),
    ).toEqual({ root, source: "remote", connection: remote });
    // KYOSEI_PROJECT_ROOT still outranks every remembered window (KYO-175.c8).
    expect(windowsAtLaunch(back, "/pinned")).toEqual([{ root: "/pinned", connection: null }]);

    // A file from before this record held bare paths, which were only ever this
    // machine's; and a connection that cannot be read is dropped, never read as local.
    writeFileSync(
      join(dir, "project.json"),
      JSON.stringify({
        chosen: { root, connection: { host: 7 } },
        recents: [root, { root, connection: { port: 443 } }],
        windows: [{ root, connection: "devbox" }],
      }),
      "utf8",
    );
    const legacy = readProjectState(dir);
    expect(legacy.chosen).toBeNull();
    expect(legacy.recents).toEqual([{ root, connection: null }]);
    expect(windowsAtLaunch(legacy, undefined)).toEqual([]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("recents are keyed by (server, root): one root local and on two servers is three rows, each saying where it is served from (KYO-465.c2)", () => {
  const root = "/Users/me/coding-projects/kyosei";
  const remote = { host: "devbox.tailnet.ts.net", port: 443 };
  const other = { host: "box.tailnet.ts.net", port: 443 };
  let state = readProjectState(join(tmpdir(), "kyosei kyo465 nothing here"));
  state = withRecent(state, { root, connection: null });
  state = withRecent(state, { root, connection: remote });
  state = withRecent(state, { root, connection: other });
  // Opening the remote one again moves it to the head; it is not a fourth entry.
  state = withRecent(state, { root, connection: { host: "Devbox.tailnet.ts.net", port: 443 } });
  expect(state.recents.map((entry) => entry.connection?.host ?? null)).toEqual([
    "Devbox.tailnet.ts.net",
    "box.tailnet.ts.net",
    null,
  ]);
  const served = [
    { name: "kyosei", root, storeRoot: root, prefix: "KYO", canOpen: true, problem: null },
    {
      name: "extra",
      root: "/extra",
      storeRoot: "/extra",
      prefix: "EXT",
      canOpen: true,
      problem: null,
    },
  ];
  const remembered = [{ ...remote, root, project: "KYO" }];
  const offered = offeredEntries(state, served, remembered);
  expect(offered).toEqual([
    { root, project: "KYO", server: { host: "Devbox.tailnet.ts.net", port: 443 }, recent: true },
    { root, project: null, server: other, recent: true },
    { root, project: "KYO", server: null, recent: true },
    // The registry's other project: offered, and not a recent this app can forget.
    { root: "/extra", project: "EXT", server: null, recent: false },
  ]);
});

test("a recent can be forgotten one at a time or all at once, and the forgetting is what the next launch reads (KYO-465.c3)", () => {
  const dir = mkdtempSync(join(tmpdir(), "kyosei kyo465 forget "));
  try {
    const remote = { host: "devbox.tailnet.ts.net", port: 443 };
    let state = withRecent(readProjectState(dir), { root: "/a", connection: null });
    state = withRecent(state, { root: "/a", connection: remote });
    state = withRecent(state, { root: "/b", connection: null });
    // Forgetting the remote machine's /a leaves this machine's /a alone.
    state = withoutRecent(state, { root: "/a", connection: remote });
    expect(writeProjectState(dir, state)).toBeNull();
    expect(readProjectState(dir).recents).toEqual([
      { root: "/b", connection: null },
      { root: "/a", connection: null },
    ]);
    // Clearing keeps where the app reopens: forgetting a list is not closing a project.
    const cleared = withoutRecent(readProjectState(dir), null);
    expect(writeProjectState(dir, cleared)).toBeNull();
    const back = readProjectState(dir);
    expect(back.recents).toEqual([]);
    expect(back.chosen).toEqual({ root: "/b", connection: null });

    // A dev box's remembered rows go too, since the switcher offers them beside these.
    rememberRemote(dir, { ...remote, root: "/a", project: "KYO" });
    rememberRemote(dir, { ...remote, root: "/c", project: "CEE" });
    forgetRemote(dir, { ...remote, root: "/a" });
    expect(readRemotes(dir).map((entry) => entry.root)).toEqual(["/c"]);
    forgetRemote(dir, null);
    expect(readRemotes(dir)).toEqual([]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// --------------------------------------- is this a kyo the app reads (KYO-449, KYO-443)

const REPO_ROOT = join(APP_ROOT, "..", "..");

/** `pyproject.toml`'s own `version`, read the way a person reads it. */
function pyprojectVersion(): string {
  const text = readFileSync(join(REPO_ROOT, "pyproject.toml"), "utf8");
  const project = text.slice(text.indexOf("[project]"));
  const match = /^version\s*=\s*"([^"]+)"/m.exec(project);
  if (match === null) throw new Error("pyproject.toml has no [project] version");
  return match[1] ?? "";
}

test("KYO_ACCEPTS admits pyproject.toml's own version, so the app in this tree reads the CLI in this tree (KYO-449.c2)", () => {
  const version = pyprojectVersion();
  expect(judgeKyoVersion(version, KYO_ACCEPTS), `KYO_ACCEPTS ${KYO_ACCEPTS} vs ${version}`).toBe(
    "accepted",
  );
  // Declared once, beside the resolver: no other source file states a range of its own.
  const declaring = everySourceFile(SRC_ROOT).filter((file) =>
    /KYO_ACCEPTS\s*=/.test(readFileSync(file, "utf8")),
  );
  expect(declaring.map((file) => relative(SRC_ROOT, file))).toEqual([join("main", "kyo.ts")]);
  // The judge itself, both directions (under 0.x, a change in the middle number breaks).
  expect(judgeKyoVersion("0.1.0", ">=0.1.0,<0.2.0")).toBe("accepted");
  expect(judgeKyoVersion("0.1.9", ">=0.1.0,<0.2.0")).toBe("accepted");
  expect(judgeKyoVersion("0.0.9", ">=0.1.0,<0.2.0")).toBe("too-old");
  expect(judgeKyoVersion("0.1.0.dev3", ">=0.1.0,<0.2.0")).toBe("too-old");
  expect(judgeKyoVersion("0.2.0", ">=0.1.0,<0.2.0")).toBe("too-new");
  expect(judgeKyoVersion("0.2.0.dev1", ">=0.1.0,<0.2.0")).toBe("too-new");
  expect(judgeKyoVersion("1.0", ">=0.1.0,<0.2.0")).toBe("too-new");
  expect(judgeKyoVersion("not a version", ">=0.1.0,<0.2.0")).toBe("unparsable");
  // What the check exists to catch: a y bump in pyproject.toml without the range.
  expect(judgeKyoVersion("0.2.0", KYO_ACCEPTS)).not.toBe("accepted");
});

test("the install-kind labels and upgrade routes the too-old state shows are the CLI's own (KYO-449.c3, KYO-317)", () => {
  const python = join(REPO_ROOT, ".venv", "bin", "python");
  const script = [
    "import json",
    "from kyosei import buildinfo as b",
    "print(json.dumps({'labels': b.INSTALL_KIND_LABELS, 'routes': {k + ':' + str(r): " +
      "b.upgrade_route(k, r) for k in ('uv-tool', 'working-tree', 'cannot-tell', 'bundled') " +
      "for r in (True, False, None)}}))",
  ].join("\n");
  const said = JSON.parse(
    execFileSync(python, ["-c", script], { cwd: REPO_ROOT, encoding: "utf8" }),
  ) as { labels: Record<string, string>; routes: Record<string, string> };
  expect(INSTALL_KIND_LABELS).toEqual(said.labels);
  for (const [key, route] of Object.entries(said.routes)) {
    const [kind, release] = key.split(":");
    const flag = release === "True" ? true : release === "False" ? false : null;
    expect(upgradeRoute(kind ?? null, flag), key).toBe(route);
  }
});

/**
 * The four states of KYO-440 c2, word for word apart from their Markdown: the text each
 * state of the window must show, with its filled values written as placeholders. Held
 * here rather than read from the record, so that a change to the window's wording is a
 * change to this table as well.
 */
const KYO_440_STATES: Record<string, string> = {
  "No kyo":
    "Kyosei can't find the `kyo` command. The app reads your projects through the Kyosei " +
    "command line tool, and there is no `kyo` on the PATH your login shell (`<shell>`) sets " +
    "up. Install it: uv tool install kyosei (or `pipx install kyosei`), then click Check " +
    "again. This app reads kyo `<KYO_ACCEPTS>`.",
  "Too old":
    "This `kyo` is too old for this app. `<path>` is kyosei `<version>` (`<install-kind " +
    "label>`, build `<commit_short>`). This app reads kyo `<KYO_ACCEPTS>`. Upgrade it: uv " +
    "tool upgrade kyosei then click Check again. Upgrading `kyo` can move it to a newer " +
    "`schema_version` than your projects' stores. Each project's own Claude Code session " +
    "migrates its store (`kyo rebuild --force`, then `kyo export --refresh`). This app " +
    "never does.",
  "Too new":
    "This `kyo` is newer than this app understands. `<path>` is kyosei `<version>`. This " +
    "app reads kyo `<KYO_ACCEPTS>`. Update the Kyosei app to a release that reads it. " +
    "Until then the records views stay off. The terminal panes, and every Claude session " +
    "in them, keep working, because they run `kyo` directly and do not need this app to " +
    "read its output.",
  "Not Kyosei's kyo":
    "The `kyo` on your PATH is not Kyosei's. `<path>` did not answer `kyo version --json` " +
    "the way Kyosei does (`<exit code / first line of stderr>`). The PyPI package named " +
    "`kyo` is an unrelated project that installs a command with this name. Remove " +
    "whichever tool put it there, then `uv tool install kyosei`, and click Check again.",
};

/** One state of KYO-440 c2, by its heading. */
function kyo440State(heading: string): string {
  const text = KYO_440_STATES[heading];
  expect(text, heading).toBeDefined();
  return text ?? "";
}

/** A state's words as the window shows them, in order, with its filled values named back. */
function shownWords(
  facts: ReturnType<typeof kyoCompatFrom>,
  values: Record<string, string>,
): string {
  let words = [facts.title, facts.says, facts.command, facts.route, facts.after]
    .filter((part): part is string => part !== null && part !== "")
    .join(" ");
  for (const [value, placeholder] of Object.entries(values)) {
    words = words.replaceAll(value, placeholder);
  }
  return words.replace(/\s+/g, " ").trim();
}

test("the four states for the kyo on the login PATH show KYO-440 c2's exact text (KYO-449.c3)", () => {
  const path = "/Users/someone/.local/bin/kyo";
  const shell = "/bin/zsh";
  const named = {
    [path]: "<path>",
    [KYO_ACCEPTS]: "<KYO_ACCEPTS>",
    [shell]: "<shell>",
  };
  const subject = { kind: "path" as const, path, shell };

  const none = kyoCompatFrom(subject, null);
  expect(none.state).toBe("no-kyo");
  expect(shownWords(none, named)).toBe(kyo440State("No kyo"));

  const old = kyoCompatFrom(subject, {
    data: {
      command: "version",
      distribution: "kyosei",
      version: "0.0.7",
      kind: "uv-tool",
      release: true,
      commit_short: "abc1234",
    },
    detail: "",
  });
  expect(old.state).toBe("too-old");
  expect(
    shownWords(old, {
      ...named,
      "0.0.7": "<version>",
      [INSTALL_KIND_LABELS["uv-tool"] ?? "?"]: "<install-kind label>",
      abc1234: "<commit_short>",
    }),
  ).toBe(kyo440State("Too old"));
  // Any other copy: the CLI's own route sentence replaces the command line.
  const tree = kyoCompatFrom(subject, {
    data: { distribution: "kyosei", version: "0.0.7", kind: "working-tree", release: false },
    detail: "",
  });
  expect(tree.command).toBeNull();
  expect(tree.route).toBe(upgradeRoute("working-tree", false));

  const fresh = kyoCompatFrom(subject, {
    data: { distribution: "kyosei", version: "0.9.0", kind: "uv-tool", release: true },
    detail: "",
  });
  expect(fresh.state).toBe("too-new");
  expect(shownWords(fresh, { ...named, "0.9.0": "<version>" })).toBe(kyo440State("Too new"));

  const detail = "exit 2: usage: kyo [-h] {serve,run}";
  const other = kyoCompatFrom(subject, { data: null, detail });
  expect(other.state).toBe("not-kyosei");
  expect(shownWords(other, { ...named, [detail]: "<exit code / first line of stderr>" })).toBe(
    kyo440State("Not Kyosei's kyo"),
  );
  // A payload from another distribution is not Kyosei's, whatever its version says.
  expect(
    kyoCompatFrom(subject, { data: { distribution: "kyo", version: "0.1.0" }, detail: "x" }).state,
  ).toBe("not-kyosei");

  const fine = kyoCompatFrom(subject, {
    data: { distribution: "kyosei", version: pyprojectVersion() },
    detail: "",
  });
  expect(fine.state).toBe("accepted");
});

test("the PATH check runs `kyo version --json` of the resolved kyo and judges it (KYO-449.c1)", async () => {
  test.setTimeout(60_000);
  const shell = "/bin/zsh";
  // No kyo on the login PATH, outside any checkout: "no kyo", naming the shell.
  const outside = tempDirWithSpace("compat outside");
  const none = await checkKyo(resolveKyo({ startDir: outside, path: "" }), { shell, cwd: outside });
  expect(none.state).toBe("no-kyo");
  expect(none.says).toContain(`(\`${shell}\`)`);
  // A `kyo` that is not Kyosei's: it answers, but not as `version --json`.
  const stub = pathWithKyo("compat stub");
  const other = await checkKyo(resolveKyo({ startDir: outside, path: stub }), {
    shell,
    cwd: outside,
  });
  expect(other.state).toBe("not-kyosei");
  expect(other.where).toContain(join(stub, "kyo"));
  // An old Kyosei on the PATH, by what its own `version --json` says.
  const old = join(tempDirWithSpace("compat old"), "bin");
  mkdirSync(old, { recursive: true });
  writeFileSync(
    join(old, "kyo"),
    `#!/bin/sh\necho '{"command":"version","ok":true,"distribution":"kyosei","version":"0.0.3","kind":"cannot-tell","release":false,"commit_short":"0ld0ld0"}'\n`,
    "utf8",
  );
  chmodSync(join(old, "kyo"), 0o755);
  const aged = await checkKyo(resolveKyo({ startDir: outside, path: old }), {
    shell,
    cwd: outside,
  });
  expect(aged.state).toBe("too-old");
  expect(aged.says).toContain("is kyosei `0.0.3`");
  expect(aged.route).toBe(upgradeRoute("cannot-tell", false));
  // This checkout's CLI -- the one the app in this tree runs unpacked -- is accepted.
  const here = await checkKyo(testResolution(), { shell, cwd: outside });
  expect(here.state, here.says).toBe("accepted");
  expect(here.version).toBe(pyprojectVersion());
  for (const dir of [outside, dirname(stub), dirname(old)]) {
    rmSync(dir, { recursive: true, force: true });
  }
});

/** A loopback port nothing listens on, found by binding and letting it go. */
async function unusedPort(): Promise<number> {
  const { createServer } = await import("node:net");
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.on("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      probe.close(() =>
        resolve(typeof address === "object" && address !== null ? address.port : 0),
      );
    });
  });
}

test("a kyo serve that refuses the connection is not running; one that times out is slow, not down (KYO-449.c6)", async () => {
  test.setTimeout(60_000);
  const { createServer } = await import("node:net");
  // Refused: nothing listens there.
  const refusedClient = new ServeClient({ host: "127.0.0.1", port: await unusedPort() }, true);
  const refused = serveFactsFrom(refusedClient, await refusedClient.version(2_000));
  expect(refused.running).toBe(false);
  expect(refused.slow).toBe(false);
  expect(refused.compat).toBeNull();
  expect(refused.problem ?? "").toContain("kyo serve is not running");
  expect(refused.problem ?? "").toContain("connection refused");

  // Timed out: something took the connection and never answered.
  const held: import("node:net").Socket[] = [];
  const silent = createServer((socket) => {
    held.push(socket);
  });
  await new Promise<void>((resolve) => silent.listen(0, "127.0.0.1", () => resolve()));
  const port = (silent.address() as { port: number }).port;
  try {
    const slowClient = new ServeClient({ host: "127.0.0.1", port }, true);
    const probe = await slowClient.version(300);
    const slow = serveFactsFrom(slowClient, probe);
    expect(slow.running).toBe(true);
    expect(slow.slow).toBe(true);
    expect(slow.compat).toBeNull();
    expect(slow.problem ?? "").toContain("slow to answer");
    expect(slow.problem ?? "").not.toContain("not running");
  } finally {
    for (const socket of held) socket.destroy();
    await new Promise<void>((resolve) => silent.close(() => resolve()));
  }
});

test("the /v1/version probe waits longer than a loaded kyo serve's cold first answer (KYO-449.c7)", () => {
  // 3 s failed a loaded desktop run; `/v1/version` answers under the
  // same lock as every served command, so it waits behind whatever the server runs.
  expect(PROBE_TIMEOUT_MS).toBeGreaterThanOrEqual(15_000);
  // Every probe of a window's server goes through `version()`'s default: main.ts asks
  // for no shorter wait of its own.
  const main = readFileSync(join(SRC_ROOT, "main", "main.ts"), "utf8");
  expect(main).not.toMatch(/\.version\(\s*\d/);
});

/**
 * The lines of `text` that reach for Node's global `fetch` or anything else undici
 * carries (`WebSocket`, `EventSource`, the `undici` package), skipping comment lines --
 * the prose that explains why they are absent names them.
 */
function undiciUses(text: string): string[] {
  const hits: string[] = [];
  text.split("\n").forEach((line, index) => {
    const code = line.trim();
    if (code.startsWith("//") || code.startsWith("*") || code.startsWith("/*")) return;
    if (/\bfetch\b|\bundici\b|\bWebSocket\b|\bEventSource\b/.test(code.replace(/\/\/.*$/, ""))) {
      hits.push(`${index + 1}: ${code}`);
    }
  });
  return hits;
}

test("nothing in the main process speaks HTTP through global fetch/undici (KYO-468.c1)", () => {
  // undici's writeH1 calls socket.setTypeOfService on every HTTP/1 request, which throws
  // EINVAL on macOS for a socket in some closed/reset states -- an Uncaught Exception
  // dialog in an app with no handler. kyoserve.ts speaks node:http/node:https instead.
  // The scan itself catches what it is for, and not the comments that explain it.
  expect(undiciUses("  const r = await fetch(url);")).toHaveLength(1);
  expect(undiciUses("const f = globalThis.fetch;")).toHaveLength(1);
  expect(undiciUses('import { Agent } from "undici";')).toHaveLength(1);
  expect(undiciUses("  const socket = new WebSocket(url); // not fetch")).toHaveLength(1);
  expect(undiciUses("// never fetch here\n * not fetch( either\nconst fetched = 1;")).toEqual([]);

  const mainDir = join(SRC_ROOT, "main");
  const files = (readdirSync(mainDir, { recursive: true }) as string[]).filter((name) =>
    name.endsWith(".ts"),
  );
  expect(files).toContain("kyoserve.ts");
  const found: string[] = [];
  for (const name of files) {
    for (const hit of undiciUses(readFileSync(join(mainDir, name), "utf8"))) {
      found.push(`src/main/${name}:${hit}`);
    }
  }
  expect(found, "global fetch/undici in the main process").toEqual([]);
  // And the transport it uses instead.
  const transport = readFileSync(join(mainDir, "kyoserve.ts"), "utf8");
  expect(transport).toMatch(/from "node:http"/);
  expect(transport).toMatch(/from "node:https"/);
});

test("a kyo serve's /v1/version saying stale is a notice carrying stale_detail and the restart command, and saying not stale is none (KYO-550.c1)", async () => {
  const { createServer } = await import("node:http");
  let body = "{}";
  const fake = createServer((_request, response) => {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(body);
  });
  await new Promise<void>((resolve) => fake.listen(0, "127.0.0.1", () => resolve()));
  const port = (fake.address() as { port: number }).port;
  const client = new ServeClient({ host: "127.0.0.1", port }, true);
  const kyosei = {
    command: "version",
    distribution: "kyosei",
    version: pyprojectVersion(),
    kind: "uv-tool",
    release: true,
    commit_short: "abc1234",
  };
  const detail =
    "this server started as kyosei 0.1.0 (abc1234), and the build installed on disk is now " +
    "kyosei 0.1.0 (def5678); stop kyo serve and start it again to serve the installed build";
  try {
    // `src/kyosei/serve.py` `_version`, as a server whose build on disk moved on answers.
    body = JSON.stringify({ ok: true, kyosei, stale: true, stale_detail: detail });
    const stale = serveFactsFrom(client, await client.version());
    expect(stale.running).toBe(true);
    expect(stale.stale).toBe(true);
    expect(stale.staleDetail).toBe(detail);
    const notice = serveStaleNotice(stale);
    expect(notice).toContain(detail);
    expect(notice).toContain(`\`${SERVE_RESTART_COMMAND}\``);
    expect(SERVE_RESTART_COMMAND).toBe("kyo serve --restart");
    // A stale server is still the one the window reads: no state of its own.
    expect(stale.compat?.state, stale.compat?.says).toBe("accepted");

    // ...and as the same server answers once it is current again.
    body = JSON.stringify({ ok: true, kyosei, stale: false });
    const fresh = serveFactsFrom(client, await client.version());
    expect(fresh.stale).toBe(false);
    expect(fresh.staleDetail).toBeNull();
    expect(serveStaleNotice(fresh)).toBeNull();

    // An older server that sends no `stale` at all is not stale.
    body = JSON.stringify({ ok: true, kyosei });
    expect(serveStaleNotice(serveFactsFrom(client, await client.version()))).toBeNull();
    // A remote one names where the command is run.
    const remote = serveStaleNotice({ ...stale, local: false });
    expect(remote).toContain(`on the machine at http://127.0.0.1:${port}`);
  } finally {
    await new Promise<void>((resolve) => fake.close(() => resolve()));
  }
  // Nothing answering is never stale, whatever was said before.
  const gone = serveFactsFrom(client, await client.version(2_000));
  expect(gone.running).toBe(false);
  expect(gone.stale).toBe(false);
  expect(serveStaleNotice(gone)).toBeNull();
});

test("only the events stream closing or re-opening asks the window's kyo serve again at once, and never beside a re-probe loop already asking (KYO-471.c1)", () => {
  expect(serveReprobeDue({ kind: "server" }, false)).toBe(true);
  expect(serveReprobeDue({ kind: "server" }, true)).toBe(false);
  for (const kind of ["store", "ideas", "sessions"] as const) {
    expect(serveReprobeDue({ kind }, false)).toBe(false);
  }
});

test("a kyo serve's /v1/version is judged against KYO_ACCEPTS, and every state names its address (KYO-449.c5)", async () => {
  test.setTimeout(90_000);
  const { createServer } = await import("node:http");
  let answer: { status: number; type: string; body: string } = {
    status: 200,
    type: "application/json",
    body: "{}",
  };
  const fake = createServer((_request, response) => {
    response.writeHead(answer.status, { "Content-Type": answer.type });
    response.end(answer.body);
  });
  await new Promise<void>((resolve) => fake.listen(0, "127.0.0.1", () => resolve()));
  const port = (fake.address() as { port: number }).port;
  const client = new ServeClient({ host: "127.0.0.1", port }, true);
  const address = `http://127.0.0.1:${port}`;
  const build = (version: string) =>
    JSON.stringify({
      ok: true,
      kyosei: {
        command: "version",
        distribution: "kyosei",
        version,
        kind: "uv-tool",
        release: true,
        commit_short: "abc1234",
      },
      schema_version: 25,
    });
  try {
    answer = { status: 200, type: "application/json", body: build("0.0.2") };
    const old = serveFactsFrom(client, await client.version());
    expect(old.running).toBe(true);
    expect(old.compat?.state).toBe("too-old");
    expect(old.compat?.where).toBe(address);
    expect(old.compat?.says).toContain(`The kyo serve at \`${address}\` is kyosei \`0.0.2\``);
    expect(old.compat?.after).toContain("kyo serve --restart");

    answer = { status: 200, type: "application/json", body: build("0.7.0") };
    const fresh = serveFactsFrom(client, await client.version());
    expect(fresh.compat?.state).toBe("too-new");
    expect(fresh.compat?.says).toContain(`The kyo serve at \`${address}\` is kyosei \`0.7.0\``);
    expect(fresh.compat?.says).toContain("Until then the records views stay off.");

    // Something that is not kyo serve holds the port.
    answer = { status: 404, type: "text/html", body: "<h1>Not Found</h1>" };
    const other = serveFactsFrom(client, await client.version());
    expect(other.running).toBe(true);
    expect(other.compat?.state).toBe("not-kyosei");
    expect(other.compat?.title).toBe(`The kyo serve at ${address} is not Kyosei's.`);
    expect(other.compat?.says).toContain("404");

    // kyo serve's own refusal is kyo serve's, never "not Kyosei's".
    answer = {
      status: 403,
      type: "application/json",
      body: JSON.stringify({ ok: false, error: "bad Host", refused_by: "kyo serve" }),
    };
    expect(serveFactsFrom(client, await client.version()).compat).toBeNull();
  } finally {
    await new Promise<void>((resolve) => fake.close(() => resolve()));
  }

  // A real kyo serve from this checkout: accepted, by the release it runs.
  const home = tempDirWithSpace("compat serve home");
  const served = await startKyoServe(home);
  try {
    const real = new ServeClient(served.address, true);
    const facts = serveFactsFrom(real, await real.version());
    expect(facts.running).toBe(true);
    expect(facts.compat?.state, facts.compat?.says).toBe("accepted");
    expect(facts.compat?.version).toBe(pyprojectVersion());
    expect(facts.compat?.where).toBe(served.url);
  } finally {
    await stopKyoServe(served);
    rmSync(home, { recursive: true, force: true });
  }
});

test("while the server is down the window asks again every few seconds, one probe at a time, and stops once it answers (KYO-449.c8)", async () => {
  expect(SERVE_REPROBE_MS).toBeGreaterThanOrEqual(1_000);
  expect(SERVE_REPROBE_MS).toBeLessThanOrEqual(10_000);
  const timers: { run: () => void; ms: number }[] = [];
  const answers = ["down", "down", "up", "down"];
  const heard: string[] = [];
  let asked = 0;
  const loop = reprobeWhileDown<string>({
    probe: async () => answers[asked++] ?? "up",
    stillDown: (answer) => answer === "down",
    onAnswer: (answer) => heard.push(answer),
    everyMs: SERVE_REPROBE_MS,
    schedule: (run, ms) => {
      timers.push({ run, ms });
      return timers.length;
    },
    cancel: () => undefined,
  });
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  // Nothing asked until the first interval passes; then one probe per interval.
  expect(timers).toHaveLength(1);
  expect(timers[0]?.ms).toBe(SERVE_REPROBE_MS);
  expect(asked).toBe(0);
  timers[0]?.run();
  await settle();
  expect(heard).toEqual(["down"]);
  expect(timers).toHaveLength(2);
  timers[1]?.run();
  await settle();
  expect(heard).toEqual(["down", "down"]);
  timers[2]?.run();
  await settle();
  // It answered: the state is cleared by that answer, and nothing more is asked.
  expect(heard).toEqual(["down", "down", "up"]);
  expect(timers).toHaveLength(3);
  expect(loop.running).toBe(false);

  // A probe that has not answered yet holds the next one back (a slow server is never
  // asked twice at once), and stop() ends it.
  const pending: (() => void)[] = [];
  const slowTimers: (() => void)[] = [];
  const held = reprobeWhileDown<string>({
    probe: () =>
      new Promise((resolve) => {
        pending.push(() => resolve("down"));
      }),
    stillDown: () => true,
    onAnswer: () => undefined,
    schedule: (run) => {
      slowTimers.push(run);
      return slowTimers.length;
    },
    cancel: () => undefined,
  });
  slowTimers[0]?.();
  await settle();
  expect(pending).toHaveLength(1);
  expect(slowTimers).toHaveLength(1);
  pending[0]?.();
  await settle();
  expect(slowTimers).toHaveLength(2);
  held.stop();
  expect(held.running).toBe(false);
});

test("the window's serve states are wired to the facts and re-probe (KYO-449.c5, c6, c8)", () => {
  const shell = readFileSync(join(SRC_ROOT, "renderer", "shell", "shell.ts"), "utf8");
  expect(shell).toContain("reprobeWhileDown(");
  expect(shell).toContain('"serve-compat"');
  expect(shell).toContain('region: "serve-slow"');
  expect(shell).toContain("renderKyoCompat(facts.kyoCompat)");
  const main = readFileSync(join(SRC_ROOT, "main", "main.ts"), "utf8");
  expect(main).toContain("serveFactsFrom(project.client, version)");
  expect(main).toContain("checkKyo(resolved,");
});

// ============================================== KYO-467: what kyo serve now serves the app
//
// Three gaps KYO-459 found once the app read only through kyo serve: another machine's
// document text, `kyo list --json`'s timestamps and the idea tree's created time. Each
// is checked here against the fixture's own kyo serve, read by the same main-process
// function the window calls.

test("another machine's document is read through kyo serve's document route, and only a listed document (KYO-467.c1)", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const text = "# Remote\n\nRead over the document route.\n";
    mkdirSync(join(fixture.root, "docs"), { recursive: true });
    writeFileSync(join(fixture.root, "docs", "remote.md"), text, "utf8");
    // The fixture's own server, spoken to as ANOTHER machine's: `local` false is what
    // makes docs mode read through the route instead of this disk.
    const remote = new ServeClient(fixture.project.server, false).project(fixture.root);
    expect(remote.local).toBe(false);
    const tree = await readDocTree(remote);
    expect(tree.ok, tree.refusal ?? "").toBe(true);
    expect(tree.documents.map((entry) => entry.path)).toContain("docs/remote.md");

    const doc = await readDoc(remote, "docs/remote.md");
    expect(doc.ok, doc.problem ?? "").toBe(true);
    expect(doc.text).toBe(text);
    expect(doc.absolutePath, "a path on the other machine is no path on this one").toBeNull();

    // A real file that is not a document is refused by the server's own sentence; a
    // traversal never leaves this process.
    const config = await readDoc(remote, "kyosei/config.yml");
    expect(config.ok).toBe(false);
    expect(config.problem ?? "").toContain("is not one of this project's documents");
    const traversal = await readDoc(remote, "docs/../kyosei/config.yml");
    expect(traversal.ok).toBe(false);
    expect(traversal.problem ?? "").toContain('".." segment');
  } finally {
    removeFixture(fixture);
  }
});

test("the records list with no base carries each record's own created and updated stamps (KYO-467.c2)", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const list = await readRecordsList(fixture.project);
    expect(list.ok, list.problem ?? "").toBe(true);
    expect(list.base).toBeNull();
    expect(list.rows.length).toBeGreaterThan(1);
    for (const row of list.rows) {
      const shown = await runKyoJson(fixture.resolution, ["show", row.id, "--full"], {
        cwd: fixture.root,
      });
      expect(shown.code, shown.stderr).toBe(0);
      expect(row.createdAt, `${row.id} created`).toBe(shown.data?.created_at);
      expect(row.updatedAt, `${row.id} updated`).toBe(shown.data?.updated_at);
      expect(Number.isNaN(Date.parse(row.createdAt))).toBe(false);
    }
  } finally {
    removeFixture(fixture);
  }
});

test("the idea tree and an opened idea carry a created stamp again (KYO-467.c3)", async () => {
  const fixture = await makeRecordsFixture();
  try {
    const tree = await readIdeaTree(fixture.project);
    expect(tree.ok, tree.refusal ?? "").toBe(true);
    expect(tree.files.length).toBeGreaterThan(0);
    for (const file of tree.files) {
      expect(file.createdAt, `${file.path} has no created stamp`).not.toBeNull();
      expect(Number.isNaN(Date.parse(file.createdAt ?? ""))).toBe(false);
    }
    const first = tree.files.find((file) => file.openable);
    expect(first, "the fixture holds no openable idea").toBeDefined();
    const opened = await openIdea(fixture.project, first?.path ?? "");
    expect(opened.ok, opened.problem ?? "").toBe(true);
    expect(opened.createdAt).toBe(first?.createdAt);
  } finally {
    removeFixture(fixture);
  }
});

// ------------------------------- KYO-469: a torn-off tab keeps its window's server

test("a tear-off, a pop-out and a spare open on the source window's server, not this machine's (KYO-469.c1)", () => {
  // This machine's kyo serve and a remote machine's both serve `~/coding-projects/kyosei`.
  const root = "/Users/me/coding-projects/kyosei";
  const remote = { projectRoot: root, remote: { host: "devbox.tailnet.ts.net", port: 443 } };
  const local = { projectRoot: root, remote: null };

  // What a window opened out of the remote window opens on: the same root THROUGH the
  // remote server, never the bare path (which `createShellWindow` reads as this machine's).
  expect(openedFrom(remote)).toEqual(remote);
  expect(openedFrom(local)).toEqual(local);
  expect(projectOfWindow(remote)).toEqual({
    root,
    connection: { host: "devbox.tailnet.ts.net", port: 443 },
  });

  // A spare is used for its owner only on the owner's project -- the same root on the
  // same server. The one booted on this machine's copy of the path is not the remote one's.
  expect(spareFits(remote, remote)).toBe(true);
  expect(spareFits(local, local)).toBe(true);
  expect(spareFits(local, remote)).toBe(false);
  expect(spareFits(remote, local)).toBe(false);
  expect(
    spareFits({ ...remote, remote: { host: "DEVBOX.tailnet.ts.net", port: 443 } }, remote),
  ).toBe(true);
  expect(
    spareFits({ ...remote, remote: { host: "devbox.tailnet.ts.net", port: 8443 } }, remote),
  ).toBe(false);
  expect(spareFits({ ...remote, projectRoot: "/elsewhere" }, remote)).toBe(false);

  // And main.ts builds no shell window from a root without its server. Every
  // `createShellWindow(...)` call either names no project (the default, which carries its
  // own server), or spreads `openedFrom(...)`, or names `remote` beside `projectRoot`.
  const main = readFileSync(join(SRC_ROOT, "main", "main.ts"), "utf8");
  const calls: string[] = [];
  for (
    let at = main.indexOf("createShellWindow(");
    at !== -1;
    at = main.indexOf("createShellWindow(", at + 1)
  ) {
    if (main.slice(Math.max(0, at - 9), at) === "function ") continue;
    let depth = 0;
    let end = at + "createShellWindow".length;
    for (; end < main.length; end += 1) {
      if (main[end] === "(") depth += 1;
      if (main[end] === ")" && --depth === 0) break;
    }
    calls.push(main.slice(at, end + 1));
  }
  // The tear-off, the pop-out and the spare -- plus the windows that already carried it.
  expect(calls.length).toBeGreaterThanOrEqual(6);
  const rootAlone = calls.filter(
    (call) => /\bprojectRoot\b/.test(call) && !/\bremote\b/.test(call),
  );
  expect(rootAlone, "a shell window built from a root without its server").toEqual([]);
  const spread = calls.filter((call) => call.includes("...openedFrom("));
  expect(spread.length, "the tear-off, the pop-out and the spare spread openedFrom").toBe(3);
  // And a spare is matched to its owner by the identity, never by the path alone.
  expect(main).not.toMatch(/spare\??\.projectRoot\s*[!=]==/);
  expect(main.match(/spareFits\(spare, /g)?.length).toBe(2);
});

// ------------------------- KYO-470: a dropped tab lands only on its own project's strip

test("a tab dropped onto a window on another project stays in its own window (KYO-470.c1)", () => {
  const root = "/Users/me/coding-projects/kyosei";
  const remote = { projectRoot: root, remote: { host: "devbox.tailnet.ts.net", port: 443 } };
  const local = { projectRoot: root, remote: null };
  const free = (project: typeof remote | typeof local) => ({ ...project, sessionId: null });
  const busy = (project: typeof remote | typeof local) => ({ ...project, sessionId: "s1" });

  // The same project: the strip is a target, terminal or not, while it has no terminal.
  const remoteStrip = free(remote);
  expect(dropTarget(remote, "board", remoteStrip)).toBe(remoteStrip);
  expect(dropTarget(remote, "terminal", remoteStrip)).toBe(remoteStrip);
  expect(dropTarget(local, "board", free(local))).not.toBeNull();
  // The same server spelled in another case is the same project.
  expect(
    dropTarget(
      { ...remote, remote: { host: "DEVBOX.tailnet.ts.net", port: 443 } },
      "board",
      remoteStrip,
    ),
  ).toBe(remoteStrip);

  // The same root through another server, either way round, is another project: no
  // target, so the tab stays in the torn-off window (`attachedTo: null`).
  for (const kind of ["board", "doc", "terminal"]) {
    expect(dropTarget(remote, kind, free(local)), `${kind}: remote tab onto local`).toBeNull();
    expect(dropTarget(local, kind, free(remote)), `${kind}: local tab onto remote`).toBeNull();
    expect(
      dropTarget(
        remote,
        kind,
        free({ ...remote, remote: { host: "devbox.tailnet.ts.net", port: 8443 } }),
      ),
      `${kind}: another port`,
    ).toBeNull();
    // Another root on the same server is another project too.
    expect(dropTarget(remote, kind, free({ ...remote, projectRoot: "/elsewhere" }))).toBeNull();
    expect(dropTarget(local, kind, free({ ...local, projectRoot: "/elsewhere" }))).toBeNull();
  }
  // A terminal still never lands on a window that has one; anything else does.
  expect(dropTarget(remote, "terminal", busy(remote))).toBeNull();
  expect(dropTarget(remote, "board", busy(remote))).not.toBeNull();
  // Nothing under the pointer, or a carrier the main process does not know: no target.
  expect(dropTarget(remote, "board", null)).toBeNull();
  expect(dropTarget(remote, "board", undefined)).toBeNull();
  expect(dropTarget(undefined, "board", remoteStrip)).toBeNull();

  // And main.ts asks it before every handoff: both the hover a move shows and the drop a
  // release performs go through `dropTarget`, and no strip is taken from `stripUnder`
  // any other way in the drag handlers.
  const main = readFileSync(join(SRC_ROOT, "main", "main.ts"), "utf8");
  const handler = (channel: string) => {
    const at = main.indexOf(`(CHANNEL.${channel},`);
    expect(at, channel).toBeGreaterThan(-1);
    const end = main.indexOf("\n});\n", at);
    return main.slice(at, end);
  };
  for (const channel of ["tabDragMove", "tabDragEnd"]) {
    const body = handler(channel);
    expect(body, channel).toMatch(/const target = dropTarget\(/);
    expect(body.match(/stripUnder\(/g)?.length, channel).toBe(1);
    expect(body, channel).toMatch(/dropTarget\([^;]*stripUnder\(/s);
  }
  // The release hands the tab -- and a terminal's session -- only to that target.
  const end = handler("tabDragEnd");
  const decided = end.indexOf("const target = dropTarget(");
  expect(end.indexOf("target.sessionId = dragged.sessionId")).toBeGreaterThan(decided);
  expect(end.indexOf("CHANNEL.tabReceive")).toBeGreaterThan(decided);
  const refused = end.indexOf("if (target === null ");
  expect(refused).toBeGreaterThan(decided);
  expect(end.indexOf("return { attachedTo: null };", refused)).toBeLessThan(
    end.indexOf("CHANNEL.tabReceive"),
  );
});

test("the status bar and the recents name where a project is served from in one label, never blank (KYO-473.c5)", () => {
  // A local window, and a remote one: the words the status bar and the recents both show.
  expect(servedLabel("KYO", "/Users/me/kyosei", null)).toBe("KYO @ this machine");
  expect(servedLabel("KYO", "/Users/me/kyosei", { host: "devbox.tail.ts.net", port: 443 })).toBe(
    "KYO @ devbox.tail.ts.net:443",
  );
  expect(servedWhere(null)).toBe("this machine");
  expect(servedWhere({ host: "127.0.0.1", port: 47370 })).toBe("127.0.0.1:47370");
  // No readable prefix: the root's last segment, then the root, then "?" -- never blank.
  expect(servedLabel(null, "/Users/me/demo/", null)).toBe("demo @ this machine");
  expect(servedLabel("", "/Users/me/demo", null)).toBe("demo @ this machine");
  expect(servedLabel(null, "/", null)).toBe("/ @ this machine");
  expect(servedLabel(null, "", null)).toBe("? @ this machine");
  // One helper for both surfaces: the shell has no label of its own, and the status bar
  // and the recents rows both call it.
  const shell = readFileSync(join(SRC_ROOT, "renderer", "shell", "shell.ts"), "utf8");
  expect(shell).not.toMatch(/function servedLabel\(/);
  expect(shell).toMatch(/servedLine\.textContent = servedLabel\(/);
  expect(shell.match(/servedLabel\(entry\./g)?.length).toBe(2);
});
