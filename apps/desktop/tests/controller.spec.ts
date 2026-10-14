// KYO-460: the app changes a machine only by running ordinary CLI commands there -- its
// own `kyo` here, or `ssh <host> zsh -lc '<command>'` to a dev box (KYO-452). The claude
// tab's controller row runs `kyo controller list|start`; New project runs a listing for
// its directory picker, `mkdir`, `kyo init` and `kyo controller start`.
//
// NEVER A REAL HOST. Every SSH check hands `ssh.ts` a stand-in ssh (`makeFakeSsh`): it
// writes down the argv it was given, then does what ssh does at the far end -- joins the
// words after the host with spaces and hands them to a shell -- with a far end of this
// run's own: its own HOME, a ZDOTDIR whose login profile prints noise, and a `kyo` on
// PATH that is this checkout's. A launched app gets it through `KYOSEI_SSH`.
//
// NEVER THE REAL `claude`, AND NEVER THE REAL `tmux -L kyosei`. `kyo controller start`
// runs `controller_command` from `$KYOSEI_HOME/settings.yml`, which here is a stand-in
// that writes down its argv and waits for a file before it exits (its `/exit`); and its
// tmux server is on a socket this run names (`KYOSEI_CONTROLLER_SOCKET`), killed by that
// socket afterwards -- never by name, which would reach the user's own server.

import { type ChildProcess, execFileSync, spawn, spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron, type ElectronApplication, expect, type Page, test } from "@playwright/test";
import {
  CONTROLLER_COMMAND_HOME,
  controllerStatusOn,
  parseControllerList,
  remoteStatusCache,
  startControllerOn,
  statusFromListing,
} from "../src/main/controller";
import { commandLine, shellQuote } from "../src/main/kyo";
import { machineName } from "../src/main/procfacts";
import { REMOTES_FILE } from "../src/main/remote";
import { storeService } from "../src/main/service";
import { sessionsPathFor } from "../src/main/sessions";
import {
  breadcrumbs,
  type CommandHost,
  commandArgv,
  createProject,
  folderNameProblem,
  LISTING_MARK,
  listDirectory,
  listingWords,
  makeFolder,
  parseListing,
  type Runner,
  type RunResult,
  runProcess,
  SSH_OPTIONS,
  SSH_PROGRAM_ENV,
  serverEntries,
  sshCommandHost,
  sshHostFor,
  sshHostProblem,
  writeSshHost,
} from "../src/main/ssh";
import { findTmux, TMUX_ENV } from "../src/main/terminal";
import {
  CONTROLLER_POLL_MS,
  type ControllerStatus,
  DEFAULT_STORE_LOCATION,
  STORE_LOCATION_LABELS,
  type StoreLocation,
} from "../src/shared/protocol";
import {
  closeReadyApp,
  type Fixture,
  HOST_PANE_ENV,
  MACOS_NO_RESTORE,
  makeFixture,
  markReadyToClose,
  openKind,
  removeFixture,
  removeTree,
  suiteEnv,
  testResolution,
  windowShowArg,
} from "./fixture";

const APP_ROOT = join(__dirname, "..");

/** The real tmux on this machine, if there is one. */
const REAL_TMUX = findTmux(process.env);
const SKIP_WITHOUT_TMUX =
  "no tmux on this machine: kyo controller start runs a controller in a tmux session, and " +
  "only tmux can show one starting and ending";

/** The fixture's prefix (`makeFixture` runs `kyo init --prefix FIX`). */
const PREFIX = "FIX";

/** Where the dialog and the tab are photographed. Never committed. */
const SCREENS = join(tmpdir(), "kyosei-kyo-460-screens");

/** The variables that would make `kyo controller` think it arrived over SSH. */
const SSH_SESSION_ENV = ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"];

// The host terminal's variables, out of THIS process before anything reads them: the
// checks below run `kyo` in-process, and an inherited `TMUX` would name the runner's
// own server (KYO-254), an inherited `SSH_CONNECTION` would make `kyo controller start`
// refuse as if it had arrived over SSH.
for (const name of [...HOST_PANE_ENV, TMUX_ENV, "TMUX", "TMUX_PANE", ...SSH_SESSION_ENV]) {
  delete process.env[name];
}

// ------------------------------------------------------------------- helpers

interface StandIn {
  path: string;
  dir: string;
  /** Written by the stand-in: its arguments, one per line. */
  argvFile: string;
  /** Written by the stand-in: the directory it was started in. */
  cwdFile: string;
  /** Create this and the stand-in exits -- its `/exit`. */
  exitFile: string;
}

/** A program that stands in for `claude`: records how it was run, waits, exits. */
function makeStandIn(base: string): StandIn {
  const dir = join(base, "stand in");
  mkdirSync(dir, { recursive: true });
  const path = join(dir, "stand-in controller");
  const argvFile = join(dir, "argv");
  const cwdFile = join(dir, "cwd");
  const exitFile = join(dir, "exit");
  writeFileSync(
    path,
    [
      "#!/bin/sh",
      "# KYO-460's stand-in for claude: see tests/controller.spec.ts.",
      `printf '%s\\n' "$@" > ${shellQuote(argvFile)}`,
      `pwd -P > ${shellQuote(cwdFile)}`,
      `while [ ! -e ${shellQuote(exitFile)} ]; do sleep 0.1; done`,
      "",
    ].join("\n"),
    { encoding: "utf8", mode: 0o755 },
  );
  return { path, dir, argvFile, cwdFile, exitFile };
}

/** A `KYOSEI_HOME` whose settings.yml runs the stand-in, as `kyo controller` reads it. */
function homeRunning(home: string, standIn: StandIn): void {
  mkdirSync(home, { recursive: true });
  writeFileSync(
    join(home, "settings.yml"),
    `controller_command: ${JSON.stringify([standIn.path, "--remote-control", "{prefix}"])}\n`,
    "utf8",
  );
}

interface FakeSsh {
  /** The ssh program to hand `ssh.ts`. */
  program: string;
  /** The far end's HOME. */
  home: string;
  /** Every argv it was given, in order (without the program). */
  calls(): string[][];
}

/**
 * A stand-in for ssh. With `fail`, it prints that on stderr and exits 255, as ssh does
 * when it cannot connect; otherwise it runs the far end's command as ssh's far end does.
 */
function makeFakeSsh(base: string, fail: string | null = null): FakeSsh {
  const dir = join(base, fail === null ? "fake ssh" : "failing ssh");
  const home = join(dir, "far home");
  const zdot = join(dir, "zdotdir");
  const bin = join(dir, "bin");
  mkdirSync(home, { recursive: true });
  mkdirSync(zdot, { recursive: true });
  mkdirSync(bin, { recursive: true });
  // The far end's login profile says something on stdout, as real ones do: nothing the
  // app reads may mistake it for a folder or a JSON document.
  writeFileSync(join(zdot, ".zprofile"), "echo 'noise from a login profile/'\n", "utf8");
  // `kyo` on the far end's PATH is this checkout's.
  if (testResolution().source.program === null) throw new Error("no kyo for the far end");
  writeFileSync(join(bin, "kyo"), `#!/bin/sh\nexec ${commandLine(testResolution(), [])} "$@"\n`, {
    encoding: "utf8",
    mode: 0o755,
  });
  const log = join(dir, "calls.jsonl");
  const script = join(dir, "fake-ssh.cjs");
  writeFileSync(
    script,
    [
      "// KYO-460's stand-in for ssh: see tests/controller.spec.ts.",
      'const fs = require("node:fs");',
      'const { spawnSync } = require("node:child_process");',
      "const argv = process.argv.slice(2);",
      `fs.appendFileSync(${JSON.stringify(log)}, JSON.stringify(argv) + "\\n");`,
      fail === null
        ? ""
        : `process.stderr.write(${JSON.stringify(`${fail}\n`)}); process.exit(255);`,
      'const at = argv.indexOf("--");',
      "const words = argv.slice(at + 2);",
      // The fake REMOTE side: it inherits the environment ssh was run with, which is
      // the app's own (already scrubbed by suiteEnv at launch), as a real sshd would.
      "const env = Object.assign({}, process.env, {",
      `  HOME: ${JSON.stringify(home)}, ZDOTDIR: ${JSON.stringify(zdot)},`,
      `  PATH: ${JSON.stringify(bin)} + ":" + process.env.PATH,`,
      '  GIT_AUTHOR_NAME: "kyosei checks", GIT_AUTHOR_EMAIL: "checks@example.invalid",',
      '  GIT_COMMITTER_NAME: "kyosei checks", GIT_COMMITTER_EMAIL: "checks@example.invalid" });',
      'const done = spawnSync("/bin/sh", ["-c", words.join(" ")], { stdio: "inherit", env });',
      "process.exit(done.status === null ? 1 : done.status);",
      "",
    ].join("\n"),
    "utf8",
  );
  const wrapper = join(dir, "ssh");
  writeFileSync(
    wrapper,
    ["#!/bin/sh", `exec ${shellQuote(process.execPath)} ${shellQuote(script)} "$@"`, ""].join("\n"),
    { encoding: "utf8", mode: 0o755 },
  );
  return {
    program: wrapper,
    home: realpathSync(home),
    calls: () =>
      existsSync(log)
        ? readFileSync(log, "utf8")
            .split("\n")
            .filter(Boolean)
            .map((line) => JSON.parse(line) as string[])
        : [],
  };
}

/** A runner that answers from a script and writes down every argv. Never runs anything. */
function recordingRunner(answers: RunResult[]): { run: Runner; argvs: string[][] } {
  const argvs: string[][] = [];
  const run: Runner = async (argv) => {
    argvs.push([...argv]);
    return answers.shift() ?? { code: 0, stdout: "", stderr: "" };
  };
  return { run, argvs };
}

/** A socket path for `kyo controller`'s tmux server, short enough for a Unix socket. */
function controllerSocket(): string {
  return join(realpathSync(mkdtempSync(join(tmpdir(), "k460-"))), "ctl");
}

function tmuxEnv(): Record<string, string | undefined> {
  // suiteEnv drops TMUX, TMUX_PANE, TMUX_TMPDIR and the app's own variables (KYO-254):
  // dropping TMUX alone left TMUX_TMPDIR able to point this tmux at another server.
  return suiteEnv();
}

/** End the tmux server on that socket -- by its socket, which is what makes it this run's. */
function killControllerServer(socket: string): void {
  if (REAL_TMUX === null) return;
  try {
    execFileSync(REAL_TMUX, ["-S", socket, "kill-server"], { env: tmuxEnv(), stdio: "ignore" });
  } catch {
    // No server left; nothing this run started is still up.
  }
}

function sessionsOnSocket(socket: string): string[] {
  if (REAL_TMUX === null) return [];
  try {
    return execFileSync(REAL_TMUX, ["-S", socket, "list-sessions", "-F", "#{session_name}"], {
      env: tmuxEnv(),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    })
      .split("\n")
      .filter(Boolean);
  } catch {
    return [];
  }
}

/** Set these on `process.env` for one check, and put back what was there. */
async function withEnv<T>(vars: Record<string, string>, body: () => Promise<T>): Promise<T> {
  const before: Record<string, string | undefined> = {};
  for (const [key, value] of Object.entries(vars)) {
    before[key] = process.env[key];
    process.env[key] = value;
  }
  try {
    return await body();
  } finally {
    for (const [key, value] of Object.entries(before)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

/**
 * What the far end's two shells make of the last argument: its login shell unquotes
 * it into a script, and `zsh -lc` splits the script into words.
 */
function farEndWords(lastArg: string): string[] {
  const script = spawnSync("/bin/sh", ["-c", `printf '%s' ${lastArg}`], { encoding: "utf8" });
  const words = spawnSync(
    "/bin/sh",
    ["-c", `f() { for a in "$@"; do printf '%s\\n' "$a"; done; }; f ${script.stdout}`],
    { encoding: "utf8" },
  );
  return words.stdout.split("\n").slice(0, -1);
}

// ------------------------------------------------ the argv, and the far end's shell

test("every remote call is `ssh <host> zsh -lc '<command>'`, built by one function, and a host that could be an option is refused (KYO-460.c1)", () => {
  const host = sshCommandHost("me@devbox", "/usr/bin/ssh");
  expect(commandArgv(host, ["kyo", "init", "--json", "/a b"])).toEqual([
    "/usr/bin/ssh",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "--",
    "me@devbox",
    "zsh",
    "-lc",
    "'kyo init --json '\\''/a b'\\'''",
  ]);
  expect(SSH_OPTIONS).toEqual(["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]);
  // Quoted twice, and both shells at the far end give back exactly the words.
  const nasty = ["kyo", "controller", "start", "--json", "/Users/me/my project's $HOME `dir`"];
  expect(farEndWords(commandArgv(host, nasty).at(-1) ?? "")).toEqual(nasty);
  // Here, the words are the argv: spawned with no shell at all.
  const local: CommandHost = { kind: "local", machine: "here", resolution: testResolution() };
  expect(commandArgv(local, ["mkdir", "-p", "--", "/a b"])).toEqual(["mkdir", "-p", "--", "/a b"]);
  // The listing's path is `$1` inside its script, never spliced into it.
  expect(listingWords("/x; rm -rf ~")).toEqual([
    "/bin/sh",
    "-c",
    `cd -- "\${1:-$HOME}" && printf '%s\\n' ${LISTING_MARK} && pwd -P && command ls -1p`,
    "sh",
    "/x; rm -rf ~",
  ]);
  // A host is data, never an option: `--` is in front of it, and these never get there.
  expect(sshHostProblem("-oProxyCommand=/tmp/owned")).toContain("cannot begin with '-'");
  expect(sshHostProblem("devbox extra")).toContain("no spaces");
  expect(sshHostProblem("")).toContain("empty");
  expect(sshHostProblem("me@devbox.tail1234.ts.net")).toBeNull();
  // The seam a launched app reads.
  expect(SSH_PROGRAM_ENV).toBe("KYOSEI_SSH");
});

test("a folder listed through ssh arrives as folder names only: spaces and quotes survive both shells, and files and a login profile's noise never reach the picker (KYO-460.c1, c2)", async () => {
  const base = realpathSync(mkdtempSync(join(tmpdir(), "kyosei ssh listing ")));
  try {
    const ssh = makeFakeSsh(base);
    const tree = join(ssh.home, "Projects");
    for (const name of ["a b'c", "plain", ".hidden", "Zeta", "alpha"]) {
      mkdirSync(join(tree, name), { recursive: true });
    }
    writeFileSync(join(tree, "notes.txt"), "a file, not a folder\n", "utf8");
    const host = sshCommandHost("devbox", ssh.program);

    const listed = await listDirectory(host, tree, runProcess);
    expect(listed.problem).toBeNull();
    expect(listed.listing).toEqual({ path: tree, folders: ["a b'c", "alpha", "plain", "Zeta"] });
    // What ssh was handed is exactly what the one builder built.
    expect(ssh.calls()).toEqual([commandArgv(host, listingWords(tree)).slice(1)]);

    // No path: the far end's home, as its own shell says it.
    const home = await listDirectory(host, null, runProcess);
    expect(home.listing?.path).toBe(ssh.home);
    expect(home.listing?.folders).toEqual(["Projects"]);

    // New Folder: `mkdir`, and a name that is already there is the far end's refusal.
    expect((await makeFolder(host, join(tree, "made here"), runProcess)).ok).toBe(true);
    expect(existsSync(join(tree, "made here"))).toBe(true);
    const again = await makeFolder(host, join(tree, "made here"), runProcess);
    expect(again.ok).toBe(false);
    expect(again.problem).toContain("File exists");
    expect(folderNameProblem("a/b")).toContain("'/'");
    expect(folderNameProblem("..")).not.toBeNull();

    // The parser on its own: the profile's line before the mark is not a folder.
    expect(
      parseListing(`noise from a login profile/\n${LISTING_MARK}\n/far\nsub/\nfile\nlink@\n`),
    ).toEqual({ path: "/far", folders: ["sub"] });
    expect(parseListing("no mark at all\n")).toBeNull();
    expect(breadcrumbs("/Users/me/x").map((crumb) => crumb.path)).toEqual([
      "/",
      "/Users",
      "/Users/me",
      "/Users/me/x",
    ]);
  } finally {
    removeTree(base);
  }
});

test("what ssh prints on stderr is the problem, verbatim, at every step that runs over it (KYO-460.c1)", async () => {
  const base = realpathSync(mkdtempSync(join(tmpdir(), "kyosei ssh failing ")));
  const said =
    "ssh: Could not resolve hostname nowhere.invalid: nodename nor servname provided, or not known";
  try {
    const ssh = makeFakeSsh(base, said);
    const host = sshCommandHost("nowhere.invalid", ssh.program);
    expect((await listDirectory(host, "/x", runProcess)).problem).toBe(said);
    expect((await makeFolder(host, "/x/y", runProcess)).problem).toBe(said);
    const made = await createProject(
      host,
      "/x/y",
      { startController: true, storeLocation: "split" },
      runProcess,
    );
    expect(made).toMatchObject({ ok: false, failedStep: "mkdir", problem: said });
    const status = await controllerStatusOn(host, "/x/y", runProcess);
    expect(status.problem).toBe(said);
    expect(status.machine).toBe("nowhere.invalid");
    const started = await startControllerOn(host, "/x/y", runProcess);
    expect(started.running).toBe(false);
    expect(started.problem).toBe(said);
    expect(started.why).toBe("kyo controller start did not start a session on nowhere.invalid");
    // One ssh call each (Start asks first, then starts), and New project stopped at mkdir.
    expect(ssh.calls().length).toBe(6);
  } finally {
    removeTree(base);
  }
});

test("New project runs mkdir -p, kyo init and kyo controller start there, in that order, and stops at the step that fails (KYO-460.c2)", async () => {
  const host = sshCommandHost("devbox", "/usr/bin/ssh");
  const root = "/Users/me/new thing";
  const init = {
    ok: true,
    code_root: root,
    project_prefix: "NEW",
    registry: { name: "new thing", registered: true, problem: null },
  };
  const start = {
    ok: true,
    session: "NEW",
    pid: 4242,
    cwd: root,
    started_with: "claude --remote-control NEW",
    attach: "tmux -L kyosei attach -t =NEW",
    remote_control_name: "NEW",
  };
  const { run, argvs } = recordingRunner([
    { code: 0, stdout: "", stderr: "" },
    // A login profile's line before the document is not the document.
    { code: 0, stdout: `noise from a login profile\n${JSON.stringify(init)}\n`, stderr: "" },
    { code: 0, stdout: `${JSON.stringify(start)}\n`, stderr: "" },
  ]);
  const made = await createProject(
    host,
    root,
    { startController: true, storeLocation: "split" },
    run,
  );
  expect(argvs).toEqual([
    commandArgv(host, ["mkdir", "-p", "--", root]),
    commandArgv(host, ["kyo", "init", "--defaults", "--json", root]),
    commandArgv(host, ["kyo", "controller", "start", "--json", root]),
  ]);
  expect(argvs[1]?.at(-1)).toBe("'kyo init --defaults --json '\\''/Users/me/new thing'\\'''");
  expect(made).toEqual({
    ok: true,
    root,
    machine: "devbox",
    name: "new thing",
    prefix: "NEW",
    registered: true,
    controller: {
      session: "NEW",
      pid: 4242,
      cwd: root,
      startedWith: "claude --remote-control NEW",
      attach: "tmux -L kyosei attach -t =NEW",
      remoteControlName: "NEW",
      trustFolder: null,
    },
    failedStep: null,
    problem: null,
  });

  // Without a controller: two commands, and nothing started.
  const quiet = recordingRunner([
    { code: 0, stdout: "", stderr: "" },
    { code: 0, stdout: JSON.stringify(init), stderr: "" },
  ]);
  const plain = await createProject(
    host,
    root,
    { startController: false, storeLocation: "split" },
    quiet.run,
  );
  expect(quiet.argvs).toHaveLength(2);
  expect(plain.controller).toBeNull();
  expect(plain.ok).toBe(true);

  // `kyo init` refuses: its stderr, verbatim, and no controller is asked for.
  const refused = recordingRunner([
    { code: 0, stdout: "", stderr: "" },
    { code: 1, stdout: "", stderr: "zsh:1: command not found: kyo\n" },
  ]);
  const noKyo = await createProject(
    host,
    root,
    { startController: true, storeLocation: "split" },
    refused.run,
  );
  expect(refused.argvs).toHaveLength(2);
  expect(noKyo).toMatchObject({
    ok: false,
    failedStep: "init",
    problem: "zsh:1: command not found: kyo",
  });

  // The controller refuses after the project is made: made, and the refusal said.
  const late = recordingRunner([
    { code: 0, stdout: "", stderr: "" },
    { code: 0, stdout: JSON.stringify(init), stderr: "" },
    { code: 1, stdout: "", stderr: "kyo: no tmux -L kyosei server is running, and ...\n" },
  ]);
  const noServer = await createProject(
    host,
    root,
    { startController: true, storeLocation: "split" },
    late.run,
  );
  expect(noServer).toMatchObject({
    ok: false,
    prefix: "NEW",
    registered: true,
    failedStep: "controller",
    problem: "kyo: no tmux -L kyosei server is running, and ...",
  });

  // A relative path is refused before anything runs.
  const none = recordingRunner([]);
  expect(
    (
      await createProject(
        host,
        "relative",
        { startController: false, storeLocation: "split" },
        none.run,
      )
    ).ok,
  ).toBe(false);
  expect(none.argvs).toEqual([]);
});

test("New project's store-location choice is the kyo init layout flag -- --defaults for the split store, --in-repo for the store in the code repo -- locally and over ssh, every other word kept (KYO-573.c2, c3)", async () => {
  const root = "/Users/me/new thing";
  const init = JSON.stringify({ ok: true, code_root: root, project_prefix: "NEW", registry: null });
  const expected: Record<StoreLocation, string[]> = {
    split: ["init", "--defaults", "--json", root],
    "in-repo": ["init", "--in-repo", "--json", root],
  };
  // The labels exactly as KYO-573.c1 fixes them: the split one's closing parenthesis
  // has no opening one, and stays that way.
  expect(DEFAULT_STORE_LOCATION).toBe("split");
  expect(STORE_LOCATION_LABELS).toEqual({
    split:
      "this keeps the kyosei store in your home folder, a separate git repository is initialized there for the store only, and nothing from kyosei is pushed to your remote code repository)",
    "in-repo":
      "store the kyosei store inside the code repo (this may expose your ideas, records, your quoted reasoning, and anything else you do in kyosei to the public)",
  });

  // Over ssh: every command goes through the stand-in runner.
  const ssh = sshCommandHost("devbox", "/usr/bin/ssh");
  for (const location of ["split", "in-repo"] as const) {
    const { run, argvs } = recordingRunner([
      { code: 0, stdout: "", stderr: "" },
      { code: 0, stdout: init, stderr: "" },
    ]);
    const made = await createProject(
      ssh,
      root,
      { startController: false, storeLocation: location },
      run,
    );
    expect(made.ok).toBe(true);
    expect(argvs).toEqual([
      commandArgv(ssh, ["mkdir", "-p", "--", root]),
      commandArgv(ssh, ["kyo", ...expected[location]]),
    ]);
  }

  // Here: mkdir through the stand-in runner, and kyo is the app's own resolution -- a
  // stand-in program that writes down its words and answers as kyo init does.
  const base = realpathSync(mkdtempSync(join(tmpdir(), "kyosei new project store ")));
  try {
    const said = join(base, "argv");
    const program = join(base, "kyo");
    writeFileSync(
      program,
      `#!/bin/sh\nprintf '%s\\n' "$@" > '${said}'\nprintf '%s\\n' '${init}'\n`,
      { mode: 0o755 },
    );
    const real = testResolution();
    const local: CommandHost = {
      kind: "local",
      machine: "here",
      resolution: { ...real, source: { ...real.source, program, baseArgs: [] } },
    };
    for (const location of ["split", "in-repo"] as const) {
      const { run, argvs } = recordingRunner([{ code: 0, stdout: "", stderr: "" }]);
      const made = await createProject(
        local,
        root,
        { startController: false, storeLocation: location },
        run,
      );
      expect(made).toMatchObject({ ok: true, prefix: "NEW", failedStep: null });
      expect(argvs).toEqual([["mkdir", "-p", "--", root]]);
      expect(readFileSync(said, "utf8").split("\n").slice(0, -1)).toEqual(expected[location]);
    }
  } finally {
    removeTree(base);
  }
});

test("the controller row reads kyo controller list -- running, exited, not running, not registered -- and a dev box is asked at most once per interval (KYO-460.c3)", async () => {
  const row = (prefix: string, session: string | null, alive: boolean, pid: number | null) => ({
    project: prefix.toLowerCase(),
    prefix,
    root: `/p/${prefix.toLowerCase()}`,
    session,
    claude_alive: alive,
    pid,
    started_with: alive ? "claude" : null,
  });
  const listing = parseControllerList({
    ok: true,
    socket: "tmux -L kyosei",
    server_running: true,
    projects: [
      row("AAA", "AAA", true, 7),
      row("BBB", "BBB", false, null),
      row("CCC", null, false, null),
    ],
  });
  if (listing === null) throw new Error("the listing did not parse");
  const running = statusFromListing(listing, "/p/aaa/", "devbox");
  expect(running).toMatchObject({
    running: true,
    canStart: false,
    pid: 7,
    session: "AAA",
    machine: "devbox",
  });
  expect(running.why).toBe("orchestrator session AAA is running on devbox (tmux -L kyosei, pid 7)");
  const exited = statusFromListing(listing, "/p/bbb", "devbox");
  expect(exited).toMatchObject({ running: false, canStart: true });
  expect(exited.why).toContain("its program has exited");
  expect(statusFromListing(listing, "/p/ccc", "devbox").why).toBe(
    "no orchestrator session for CCC on devbox",
  );
  const stranger = statusFromListing(listing, "/elsewhere", "devbox");
  expect(stranger).toMatchObject({ running: false, canStart: true, session: null });
  expect(stranger.why).toContain("not in that machine's project registry");
  // No command of the app's own: the row names where the CLI keeps it.
  expect(running.command).toBe(CONTROLLER_COMMAND_HOME);
  expect(running.program).toBeNull();
  expect(parseControllerList({ ok: false, error: "x" })).toBeNull();

  // The cache: asked once within the interval, again after it, and a failure is not kept.
  let clock = 0;
  let asked = 0;
  const answer = (problem: string | null): ControllerStatus => ({ ...running, problem });
  const cache = remoteStatusCache(15_000, () => clock);
  const ask = async () => {
    asked += 1;
    return answer(null);
  };
  await cache.get("k", ask);
  clock = 14_000;
  await cache.get("k", ask);
  expect(asked).toBe(1);
  clock = 15_001;
  await cache.get("k", ask);
  expect(asked).toBe(2);
  const failing = async () => {
    asked += 1;
    return answer("ssh: connect to host devbox port 22: Operation timed out");
  };
  await cache.get("f", failing);
  await new Promise((resolve) => setTimeout(resolve, 0));
  await cache.get("f", failing);
  expect(asked).toBe(4);
});

test("server entries: this machine, then each dev box once, each carrying its SSH host -- its host name until one is set (KYO-460.c1)", () => {
  const data = realpathSync(mkdtempSync(join(tmpdir(), "kyosei ssh hosts ")));
  try {
    expect(sshHostFor(data, "devbox", 47369)).toBe("devbox");
    expect(writeSshHost(data, "devbox", 47369, "me@devbox.ts.net")).toEqual({
      ok: true,
      problem: null,
      sshHost: "me@devbox.ts.net",
    });
    expect(sshHostFor(data, "devbox", 47369)).toBe("me@devbox.ts.net");
    expect(writeSshHost(data, "devbox", 47369, "-oProxyCommand=x").ok).toBe(false);
    expect(sshHostFor(data, "devbox", 47369)).toBe("me@devbox.ts.net");
    const entries = serverEntries(
      [
        { host: "devbox", port: 47369 },
        { host: "devbox", port: 47369 },
        { host: "laptop", port: 47369 },
      ],
      "here",
      { "devbox:47369": "me@devbox.ts.net" },
    );
    expect(entries).toEqual([
      { server: null, label: "This machine (here)", sshHost: null },
      { server: { host: "devbox", port: 47369 }, label: "devbox", sshHost: "me@devbox.ts.net" },
      { server: { host: "laptop", port: 47369 }, label: "laptop", sshHost: "laptop" },
    ]);
    // Empty goes back to the host name.
    expect(writeSshHost(data, "devbox", 47369, "").sshHost).toBe("devbox");
    expect(sshHostFor(data, "devbox", 47369)).toBe("devbox");
  } finally {
    removeTree(data);
  }
});

// ------------------------------------------------ the real CLI, through the stand-in ssh

test("through ssh, kyo init makes and registers the project, kyo controller start starts its session, and the row reads it running, then gone (KYO-460.c2, c3)", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  test.setTimeout(120_000);
  const base = realpathSync(mkdtempSync(join(tmpdir(), "kyosei ssh project ")));
  const socket = controllerSocket();
  const home = join(base, "far kyosei home");
  const standIn = makeStandIn(base);
  homeRunning(home, standIn);
  try {
    await withEnv({ KYOSEI_HOME: home, KYOSEI_CONTROLLER_SOCKET: socket }, async () => {
      const ssh = makeFakeSsh(base);
      const host = sshCommandHost("devbox", ssh.program);
      const root = join(ssh.home, "Projects", "Brand New");
      const made = await createProject(
        host,
        root,
        { startController: true, storeLocation: "split" },
        runProcess,
      );
      expect(made.problem, made.problem ?? "").toBeNull();
      expect(made).toMatchObject({ ok: true, root, machine: "devbox", registered: true });
      expect(made.prefix).toMatch(/^[A-Z][A-Z0-9]{1,15}$/);
      const prefix = made.prefix as string;
      expect(made.controller?.session).toBe(prefix);
      expect(made.controller?.pid).toBeGreaterThan(1);
      expect(made.controller?.attach).toContain(`attach -t =${prefix}`);
      // The registry on that machine names it (KYO-455): `kyo init` registered it.
      const registry = JSON.parse(readFileSync(join(home, "projects.json"), "utf8")) as {
        projects: { root: string }[];
      };
      expect(registry.projects.map((entry) => entry.root)).toContain(root);
      // The session runs the machine's controller_command, in the project, named for it.
      expect(sessionsOnSocket(socket)).toContain(prefix);
      await expect
        .poll(() => (existsSync(standIn.argvFile) ? readFileSync(standIn.argvFile, "utf8") : ""))
        .toBe(`--remote-control\n${prefix}\n`);
      expect(readFileSync(standIn.cwdFile, "utf8").trim()).toBe(root);

      // The row, asked over the same ssh: running, and Start is not offered.
      const status = await controllerStatusOn(host, root, runProcess);
      expect(status).toMatchObject({ running: true, canStart: false, session: prefix });
      expect(status.why).toContain(`orchestrator session ${prefix} is running on devbox`);
      // Start beside it starts nothing: it reports the one that runs.
      const again = await startControllerOn(host, root, runProcess);
      expect(again.running).toBe(true);
      expect(sessionsOnSocket(socket).filter((name) => name === prefix)).toHaveLength(1);

      // Its `/exit`: the session goes, and the row says so.
      writeFileSync(standIn.exitFile, "", "utf8");
      await expect.poll(() => sessionsOnSocket(socket), { timeout: 10_000 }).not.toContain(prefix);
      const after = await controllerStatusOn(host, root, runProcess);
      expect(after).toMatchObject({ running: false, canStart: true });

      // Start controller, over ssh: `kyo controller start`, and the session it reports.
      rmSync(standIn.exitFile, { force: true });
      const started = await startControllerOn(host, root, runProcess);
      expect(started.problem, started.problem ?? "").toBeNull();
      expect(started).toMatchObject({ running: true, canStart: false, session: prefix });
      expect(started.why).toContain(`started orchestrator session ${prefix} on devbox (pid `);
      expect(started.why).toContain("attach there with");
      expect(ssh.calls().at(-1)?.at(-1)).toBe(
        shellQuote(`kyo controller start --json ${shellQuote(root)}`),
      );
    });
  } finally {
    writeFileSync(standIn.exitFile, "", "utf8");
    killControllerServer(socket);
    removeTree(base);
  }
});

test("on this machine, Start runs kyo controller start for the project and reports the session it started, through the served member (KYO-460.c3)", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  test.setTimeout(120_000);
  const project = await makeFixture(1);
  const socket = controllerSocket();
  const standIn = makeStandIn(project.base);
  homeRunning(project.home, standIn);
  const data = realpathSync(mkdtempSync(join(tmpdir(), "kyosei controller userData ")));
  const service = storeService;
  try {
    await withEnv({ KYOSEI_HOME: project.home, KYOSEI_CONTROLLER_SOCKET: socket }, async () => {
      const before = await service.controllerStatus(data, project.root, project.resolution);
      expect(before).toMatchObject({ running: false, canStart: true, session: PREFIX });
      expect(before.machine).toBe(machineName());
      expect(before.why).toContain(`no orchestrator session for ${PREFIX} on ${machineName()}`);

      const started = await service.startController(data, project.root, project.resolution);
      expect(started.problem, started.problem ?? "").toBeNull();
      expect(started).toMatchObject({ running: true, canStart: false, session: PREFIX });
      expect(started.why).toContain(`started orchestrator session ${PREFIX} on ${machineName()}`);
      expect(sessionsOnSocket(socket)).toContain(PREFIX);
      await expect
        .poll(() => (existsSync(standIn.argvFile) ? readFileSync(standIn.argvFile, "utf8") : ""))
        .toBe(`--remote-control\n${PREFIX}\n`);
      expect(readFileSync(standIn.cwdFile, "utf8").trim()).toBe(project.root);

      // A second Start starts nothing new: the one that runs is the answer.
      const second = await service.startController(data, project.root, project.resolution);
      expect(second.running).toBe(true);
      expect(sessionsOnSocket(socket).filter((name) => name === PREFIX)).toHaveLength(1);
    });
  } finally {
    writeFileSync(standIn.exitFile, "", "utf8");
    killControllerServer(socket);
    removeFixture(project);
    removeTree(data);
  }
});

// ------------------------------------------------ a session started by hand (KYO-390)

/**
 * A stand-in for a whole Claude Code session (KYO-390): like `makeStandIn`, but it also
 * fires the project's real session hook the way `claude` does -- as its own child,
 * through a shell -- so the hook's walk up its ancestry lands on it.
 */
function makeSessionStandIn(base: string, project: Fixture, sessionId: string): StandIn {
  const plain = makeStandIn(join(base, sessionId));
  const script = join(plain.dir, "session.cjs");
  const hook = commandLine(project.resolution, ["sessions", "--hook"]);
  writeFileSync(
    script,
    [
      "// KYO-390's stand-in for a Claude Code session: see tests/controller.spec.ts.",
      'const { spawnSync } = require("node:child_process");',
      'const fs = require("node:fs");',
      `const exitFile = ${JSON.stringify(plain.exitFile)};`,
      "const cwd = process.cwd();",
      `const payload = { session_id: ${JSON.stringify(sessionId)}, hook_event_name: "SessionStart", cwd };`,
      `spawnSync("/bin/sh", ["-c", ${JSON.stringify(hook)}], { cwd, input: JSON.stringify(payload) });`,
      "setInterval(() => { if (fs.existsSync(exitFile)) process.exit(0); }, 100);",
      "",
    ].join("\n"),
    "utf8",
  );
  writeFileSync(
    plain.path,
    ["#!/bin/sh", `exec ${shellQuote(process.execPath)} ${shellQuote(script)} "$@"`, ""].join("\n"),
    { encoding: "utf8", mode: 0o755 },
  );
  return plain;
}

async function recordedPid(project: Fixture, sessionId: string): Promise<number> {
  let pid = 0;
  await expect
    .poll(
      () => {
        const path = sessionsPathFor(project.root);
        if (!existsSync(path)) return 0;
        const doc = JSON.parse(readFileSync(path, "utf8")) as {
          sessions: { session_id: string; pid?: number | null }[];
        };
        pid = doc.sessions.find((entry) => entry.session_id === sessionId)?.pid ?? 0;
        return pid;
      },
      { timeout: 15_000 },
    )
    .toBeGreaterThan(1);
  return pid;
}

test("a live session started by hand is the controller: kyo controller list has none, the row reads it running, and Start starts nothing beside it (KYO-390.c1, c2; KYO-460.c3)", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  test.setTimeout(120_000);
  const project = await makeFixture(1);
  const socket = controllerSocket();
  const launcherWouldRun = makeStandIn(join(project.base, "launcher"));
  homeRunning(project.home, launcherWouldRun);
  const data = realpathSync(mkdtempSync(join(tmpdir(), "kyosei controller outside ")));
  let child: ChildProcess | null = null;
  try {
    await withEnv({ KYOSEI_HOME: project.home, KYOSEI_CONTROLLER_SOCKET: socket }, async () => {
      const outside = makeSessionStandIn(project.base, project, "outside-390");
      child = spawn(outside.path, [], { cwd: project.root, stdio: "ignore", detached: true });
      child.unref();
      const pid = await recordedPid(project, "outside-390");
      const service = storeService;
      // The sessions the project's hook recorded, as the app reads them: through the
      // project's kyo serve (KYO-459).
      const sessions = async () => (await service.readSessionsOf(project.project)).entries;
      const status = await service.controllerStatus(
        data,
        project.root,
        project.resolution,
        sessions,
      );
      expect(status.running, status.why).toBe(true);
      expect(status.canStart).toBe(false);
      expect(status.pid).toBe(pid);
      expect(status.recorded).toEqual({ sessionId: "outside-390", state: "running" });
      expect(status.why).toContain(
        `orchestrator session outside-390 is running on ${machineName()}`,
      );
      expect(status.why).toContain("started outside kyo controller");
      const refused = await service.startController(
        data,
        project.root,
        project.resolution,
        sessions,
      );
      expect(refused.running).toBe(true);
      expect(sessionsOnSocket(socket)).not.toContain(PREFIX);
      expect(existsSync(launcherWouldRun.argvFile)).toBe(false);
    });
  } finally {
    const started = child as ChildProcess | null;
    if (started?.pid !== undefined) {
      try {
        process.kill(started.pid, "SIGKILL");
      } catch {
        // already gone
      }
    }
    killControllerServer(socket);
    removeFixture(project);
    removeTree(data);
  }
});

// ----------------------------------------------------------- the app, launched

interface Launched {
  app: ElectronApplication;
  page: Page;
}

async function launchOn(
  project: Fixture,
  profile: string,
  extra: Record<string, string>,
): Promise<Launched> {
  const env = suiteEnv(extra);
  env.KYOSEI_PROJECT_ROOT = project.root;
  env.KYOSEI_HOME = extra.KYOSEI_HOME ?? project.home;
  for (const name of SSH_SESSION_ENV) delete env[name];
  delete env[TMUX_ENV];
  delete env.TMUX;
  env.SHELL = "/bin/sh";
  const app = await _electron.launch({
    args: [APP_ROOT, `--user-data-dir=${profile}`, ...MACOS_NO_RESTORE, windowShowArg()],
    env,
  });
  const page = await app.firstWindow();
  await page.waitForSelector('body[data-ready="true"]');
  markReadyToClose(app);
  return { app, page };
}

async function openNewProject(app: ElectronApplication): Promise<void> {
  await app.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()?.getMenuItemById("new-project")?.click();
  });
}

test("New Project… is a directory picker over ssh -- breadcrumbs, folders, double-click, New Folder, Choose -- that never shows the listing's text, then makes the project and starts its controller there (KYO-460.c1, c2)", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  test.setTimeout(180_000);
  const project = await makeFixture(1);
  const base = project.base;
  const socket = controllerSocket();
  const farHome = join(base, "far kyosei home");
  const standIn = makeStandIn(base);
  homeRunning(farHome, standIn);
  const ssh = makeFakeSsh(base);
  mkdirSync(join(ssh.home, "Projects", "older one"), { recursive: true });
  mkdirSync(join(ssh.home, "Documents"), { recursive: true });
  const profile = realpathSync(mkdtempSync(join(tmpdir(), "kyosei new project profile ")));
  // A dev box this app has connected to before: the only kind New project offers.
  writeFileSync(
    join(profile, REMOTES_FILE),
    JSON.stringify([{ host: "devbox", port: 47369, root: "/somewhere/else", project: "DEV" }]),
    "utf8",
  );
  let app: ElectronApplication | null = null;
  try {
    const launched = await launchOn(project, profile, {
      [SSH_PROGRAM_ENV]: ssh.program,
      KYOSEI_HOME: farHome,
      KYOSEI_CONTROLLER_SOCKET: socket,
    });
    app = launched.app;
    const { page } = launched;
    await openNewProject(app);
    const dialog = page.locator('[data-region="new-project-dialog"]');
    await expect(dialog).toBeVisible();
    const select = page.locator('[data-region="new-project-server"]');
    await expect(select.locator("option")).toHaveText([
      `This machine (${machineName()})`,
      "devbox (over SSH)",
    ]);
    // The dev box: its SSH host is its host name until someone sets another.
    await select.selectOption({ label: "devbox (over SSH)" });
    const sshHost = page.locator('[data-region="new-project-ssh-host"]');
    await expect(sshHost).toBeVisible();
    await expect(sshHost).toHaveValue("devbox");
    const folders = page.locator('[data-region="new-project-folders"] [data-folder]');
    await expect(folders).toHaveText(["Documents", "Projects"]);
    const crumbs = page.locator('[data-region="new-project-crumb"]');
    await expect(crumbs.last()).toHaveText(ssh.home.split("/").at(-1) ?? "");

    // Double-click to descend; the breadcrumbs follow.
    await folders.filter({ hasText: "Projects" }).dblclick();
    await expect(folders).toHaveText(["older one"]);
    await expect(crumbs.last()).toHaveText("Projects");
    // New Folder, named, made there, and selected.
    await page.locator('[data-region="new-project-new-folder"]').click();
    await page.locator('[data-region="new-project-folder-name"]').fill("demo app");
    await page.locator('[data-region="new-project-folder-name"]').press("Enter");
    await expect(folders).toHaveText(["demo app", "older one"]);
    await expect(folders.filter({ hasText: "demo app" })).toHaveAttribute("data-selected", "true");
    const root = join(ssh.home, "Projects", "demo app");
    await expect(page.locator('[data-region="new-project-chosen"]')).toHaveText(
      `Project folder: ${root}`,
    );
    mkdirSync(SCREENS, { recursive: true });
    await page.screenshot({ path: join(SCREENS, "new-project-picker.png") });
    // Nothing of the command's own text reaches the dialog.
    expect(await dialog.innerText()).not.toContain(LISTING_MARK);
    expect(await dialog.innerText()).not.toContain("noise from a login profile");

    // Where the store lives (KYO-573.c1): both choices, with the labels it fixes, and
    // the split store selected unless someone picks the other.
    const store = page.locator('[data-region="new-project-store"]');
    await expect(store.locator("label")).toHaveText([
      STORE_LOCATION_LABELS.split,
      STORE_LOCATION_LABELS["in-repo"],
    ]);
    const storeChoice = (location: StoreLocation) =>
      store.locator(`[data-region="new-project-store-choice"][data-location="${location}"]`);
    await expect(storeChoice("split")).toBeChecked();
    await expect(storeChoice("in-repo")).not.toBeChecked();

    // Choose: mkdir -p, kyo init and kyo controller start, over ssh.
    await expect(page.locator('[data-region="new-project-start"]')).toBeChecked();
    await page.locator('[data-region="new-project-choose"]').click();
    const said = page.locator('[data-region="new-project-said"]');
    await expect(said.first()).toContainText(`in ${root} on devbox.`, { timeout: 60_000 });
    await expect(said.nth(1)).toHaveText("It is in devbox's project registry as demo app.");
    await expect(said.nth(2)).toContainText("is running there (pid ");
    await expect(said.nth(3)).toContainText("Attach to it there with:");
    await page.screenshot({ path: join(SCREENS, "new-project-made.png") });
    const registry = JSON.parse(readFileSync(join(farHome, "projects.json"), "utf8")) as {
      projects: { root: string }[];
    };
    expect(registry.projects.map((entry) => entry.root)).toContain(root);
    await expect
      .poll(() => (existsSync(standIn.cwdFile) ? readFileSync(standIn.cwdFile, "utf8").trim() : ""))
      .toBe(root);
    // Every call went to the dev box's SSH host, through a login shell.
    const calls = ssh.calls();
    expect(calls.length).toBeGreaterThanOrEqual(6);
    for (const call of calls) {
      expect(call.slice(0, 8)).toEqual([...SSH_OPTIONS, "--", "devbox", "zsh", "-lc"]);
    }
    expect(calls.map((call) => call.at(-1) ?? "").filter((arg) => arg.startsWith("'kyo "))).toEqual(
      [
        shellQuote(`kyo init --defaults --json ${shellQuote(root)}`),
        shellQuote(`kyo controller start --json ${shellQuote(root)}`),
      ],
    );
    await page.locator('[data-region="new-project-done"]').click();
    await expect(dialog).toHaveCount(0);
  } finally {
    if (app !== null) await closeReadyApp(app);
    writeFileSync(standIn.exitFile, "", "utf8");
    killControllerServer(socket);
    removeFixture(project);
    removeTree(profile);
  }
});

test("New Project… with the in-repo store chosen runs kyo init --in-repo there and the store is made inside the new folder (KYO-573.c1, c2)", async () => {
  test.setTimeout(120_000);
  const project = await makeFixture(1);
  const base = project.base;
  const farHome = join(base, "far kyosei home");
  mkdirSync(farHome, { recursive: true });
  const ssh = makeFakeSsh(base);
  mkdirSync(join(ssh.home, "Projects", "in repo"), { recursive: true });
  const profile = realpathSync(mkdtempSync(join(tmpdir(), "kyosei new project profile ")));
  writeFileSync(
    join(profile, REMOTES_FILE),
    JSON.stringify([{ host: "devbox", port: 47369, root: "/somewhere/else", project: "DEV" }]),
    "utf8",
  );
  let app: ElectronApplication | null = null;
  try {
    const launched = await launchOn(project, profile, {
      [SSH_PROGRAM_ENV]: ssh.program,
      KYOSEI_HOME: farHome,
    });
    app = launched.app;
    const { page } = launched;
    await openNewProject(app);
    await page
      .locator('[data-region="new-project-server"]')
      .selectOption({ label: "devbox (over SSH)" });
    const folders = page.locator('[data-region="new-project-folders"] [data-folder]');
    await folders.filter({ hasText: "Projects" }).dblclick();
    await folders.filter({ hasText: "in repo" }).click();
    const root = join(ssh.home, "Projects", "in repo");
    await expect(page.locator('[data-region="new-project-chosen"]')).toHaveText(
      `Project folder: ${root}`,
    );
    // The other store location, by its label; no orchestrator this time.
    await page.getByText(STORE_LOCATION_LABELS["in-repo"], { exact: true }).click();
    await expect(
      page.locator('[data-region="new-project-store-choice"][data-location="in-repo"]'),
    ).toBeChecked();
    await expect(
      page.locator('[data-region="new-project-store-choice"][data-location="split"]'),
    ).not.toBeChecked();
    await page.locator('[data-region="new-project-start"]').uncheck();
    mkdirSync(SCREENS, { recursive: true });
    await page.screenshot({ path: join(SCREENS, "new-project-in-repo.png") });
    await page.locator('[data-region="new-project-choose"]').click();
    const said = page.locator('[data-region="new-project-said"]');
    await expect(said.first()).toContainText(`in ${root} on devbox.`, { timeout: 60_000 });
    // The store is inside the new folder, and no split store was made for it.
    expect(existsSync(join(root, "kyosei", "config.yml"))).toBe(true);
    expect(existsSync(join(root, "ideas"))).toBe(true);
    expect(existsSync(join(farHome, "in repo", "kyosei"))).toBe(false);
    expect(
      ssh
        .calls()
        .map((call) => call.at(-1) ?? "")
        .filter((arg) => arg.startsWith("'kyo ")),
    ).toEqual([shellQuote(`kyo init --in-repo --json ${shellQuote(root)}`)]);
  } finally {
    if (app !== null) await closeReadyApp(app);
    removeFixture(project);
    removeTree(profile);
  }
});

test("a failing ssh is shown in the picker in ssh's own words, and a changed SSH host is kept on the entry and used (KYO-460.c1)", async () => {
  test.setTimeout(120_000);
  const project = await makeFixture(1);
  const said = "me@nowhere.invalid: Permission denied (publickey,password,keyboard-interactive).";
  const failing = makeFakeSsh(project.base, said);
  const profile = realpathSync(mkdtempSync(join(tmpdir(), "kyosei new project profile ")));
  writeFileSync(
    join(profile, REMOTES_FILE),
    JSON.stringify([{ host: "devbox", port: 47369, root: "/x", project: null }]),
    "utf8",
  );
  let app: ElectronApplication | null = null;
  try {
    const launched = await launchOn(project, profile, { [SSH_PROGRAM_ENV]: failing.program });
    app = launched.app;
    const { page } = launched;
    await openNewProject(app);
    await page
      .locator('[data-region="new-project-server"]')
      .selectOption({ label: "devbox (over SSH)" });
    const problem = page.locator('[data-region="new-project-problem"]');
    await expect(problem).toHaveText(said);
    await expect(problem).toHaveAttribute("data-state", "refused");
    await expect.poll(() => failing.calls().at(-1)?.[5]).toBe("devbox");
    // The SSH host, changed here, is kept on the entry and is where the next call goes.
    const sshHost = page.locator('[data-region="new-project-ssh-host"]');
    await sshHost.fill("me@nowhere.invalid");
    await sshHost.press("Enter");
    await expect.poll(() => failing.calls().at(-1)?.[5]).toBe("me@nowhere.invalid");
    expect(sshHostFor(profile, "devbox", 47369)).toBe("me@nowhere.invalid");
    mkdirSync(SCREENS, { recursive: true });
    await page.screenshot({ path: join(SCREENS, "new-project-ssh-refused.png") });
  } finally {
    if (app !== null) await closeReadyApp(app);
    removeFixture(project);
    removeTree(profile);
  }
});

test("the claude tab's Start runs kyo controller start here, and the row reads the session running, then gone within one poll of its /exit (KYO-460.c3)", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  test.setTimeout(150_000);
  const project = await makeFixture(1);
  const socket = controllerSocket();
  const standIn = makeStandIn(project.base);
  homeRunning(project.home, standIn);
  const profile = realpathSync(mkdtempSync(join(tmpdir(), "kyosei controller profile ")));
  let app: ElectronApplication | null = null;
  try {
    const launched = await launchOn(project, profile, { KYOSEI_CONTROLLER_SOCKET: socket });
    app = launched.app;
    const { page } = launched;
    await openKind(page, "claude");
    const row = page.locator('[data-region="claude-controller"]');
    const text = page.locator('[data-region="claude-controller-status"]');
    const start = page.locator('[data-region="claude-controller-start"]');
    await expect(row).toHaveAttribute("data-controller-running", "false", { timeout: 30_000 });
    await expect(text).toContainText(`no orchestrator session for ${PREFIX} on ${machineName()}`);
    await expect(start).toBeVisible();
    // The app keeps no command of its own: nothing to change here.
    await expect(page.locator('[data-region="claude-controller-change"]')).toBeHidden();

    await start.click();
    await expect(row).toHaveAttribute("data-controller-running", "true", { timeout: 20_000 });
    await expect(start).toBeHidden();
    expect(sessionsOnSocket(socket)).toContain(PREFIX);
    mkdirSync(SCREENS, { recursive: true });
    await page.screenshot({ path: join(SCREENS, "claude-tab-started.png") });

    writeFileSync(standIn.exitFile, "", "utf8");
    await expect.poll(() => sessionsOnSocket(socket), { timeout: 10_000 }).not.toContain(PREFIX);
    await expect(row).toHaveAttribute("data-controller-running", "false", {
      timeout: CONTROLLER_POLL_MS + 5_000,
    });
    await expect(start).toBeVisible();
  } finally {
    if (app !== null) await closeReadyApp(app);
    writeFileSync(standIn.exitFile, "", "utf8");
    killControllerServer(socket);
    removeFixture(project);
    removeTree(profile);
  }
});
