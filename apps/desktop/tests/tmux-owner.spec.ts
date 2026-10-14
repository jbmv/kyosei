// KYO-229.c4: the app owns the per-project tmux session, from any launch.
//
// The real Electron app, driven against a login shell this file owns. Every launch
// here differs from every OTHER launch in this suite in two deliberate ways (the file
// this comment used to name, `smoke.spec.ts`, was deleted by KYO-232; what it said
// about tmux moved to `seams.spec.ts`):
//
//   `KYOSEI_TMUX` IS NOT SET. `envFor` turns tmux off for every launch in that file,
//   so that the pane's world is stated rather than inherited. Here the pane's world is
//   the thing under test -- c1 asks that the pane attach to tmux "without any
//   `KYOSEI_TMUX` setting", which can only be shown by a tmux the app found on the
//   login shell's own PATH -- so the variable is absent and the PATH is the fixture's.
//
//   `KYOSEI_LOGIN_SHELL` names a shell this file wrote. The app's own seam for saying
//   which shell to ask (see `shellenv.ts`), and the only way an assertion about what
//   reaches a pane can mean the same thing on two machines: the developer's own
//   `.zshrc` exports whatever it exports.
//
// Everything tmux does for real -- a session surviving its client, scrollback
// surviving a relaunch -- needs tmux, and there is no simulating it: a stub can prove
// what the app ASKS for (`seams.spec.ts` does exactly that, against a tmux stub that
// logs its argv) and only tmux can prove that it happened. Those checks skip with
// their reason where there is none, on the pattern KYO-115 set.
//
// Every tmux this file starts is on a PRIVATE SERVER. Since KYO-474 the pane attaches
// to the CONTROLLER's server -- the one `kyo controller` resolves -- and every launch
// here names a socket of the run's own through `KYOSEI_CONTROLLER_SOCKET`, exactly as
// `tests/test_kyo_controller.py` and `controller.spec.ts` do; the login shell also
// exports a `TMUX_TMPDIR` of the run's own, which stands for the machine's DEFAULT
// server and must stay empty, because the app no longer touches that server at all.
// Nothing here can see, resize or kill a session belonging to the human at this
// machine -- not on `tmux -L kyosei`, and not on the default server -- and the teardown
// kills only servers this run started, by their socket.

import { execFileSync, spawnSync } from "node:child_process";
import {
  chmodSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron, type ElectronApplication, expect, type Page, test } from "@playwright/test";
import { LOGIN_SHELL_ENV } from "../src/main/shellenv";
import {
  CONTROLLER_SOCKET_ENV,
  CONTROLLER_SOCKET_NAME,
  controllerSocketPath,
  controllerSocketSpelled,
  findTmux,
  type PaneScreen,
  REPAINT_STEP_MS,
  RETIRED_SHIM_ID_ENV,
  repaintTmuxClient,
  TMUX_ENV,
} from "../src/main/terminal";
import {
  closeReadyApp,
  type Fixture,
  HOST_PANE_ENV,
  type LoginShellFixture,
  MACOS_NO_RESTORE,
  makeFixture,
  makeLoginShell,
  markReadyToClose,
  openKind,
  removeFixture,
  suiteEnv,
  testResolution,
  windowShowArg,
} from "./fixture";

const APP_ROOT = join(__dirname, "..");

/** The real tmux on this machine, if there is one. Read once, stated in the skips. */
const REAL_TMUX = findTmux(process.env);

const SKIP_WITHOUT_TMUX =
  "no tmux on this machine. What this app ASKS tmux for is checked against a stub in " +
  "seams.spec.ts; that the session then outlives the app is a fact only tmux can " +
  "establish, so this runs wherever a tmux binary is on PATH and says so where there " +
  "is none.";

let fixture: Fixture;
let app: ElectronApplication | null = null;

test.beforeAll(async () => {
  fixture = await makeFixture({ records: 1 });
});

test.afterAll(() => {
  removeFixture(fixture);
});

test.afterEach(async () => {
  if (app !== null) {
    await closeReadyApp(app);
    app = null;
  }
});

/**
 * The launch environment: the fixture project, the fixture login shell, and NO
 * `KYOSEI_TMUX` at all.
 *
 * The deletion is the assertion's other half. `suiteEnv()` here is the runner's
 * environment, and a developer who happens to have `KYOSEI_TMUX` set would otherwise
 * be running a different check from everybody else -- and the one criterion this file
 * is about says "without any `KYOSEI_TMUX` setting" in so many words.
 *
 * `suiteEnv` rather than `process.env` is the other half again, one level up
 * (KYO-254): the runner's `TMUX_TMPDIR` would otherwise beat the private one this
 * file's login shell exports -- `terminalEnvironment` layers the process environment
 * over the captured one -- and every check below would be looking at the wrong tmux
 * server. "the pane's server is the controller's, whatever TMUX_TMPDIR the app
 * inherited" is the check that fails if that ever comes back.
 */
function envFor(shell: LoginShellFixture, extra: Record<string, string> = {}) {
  const env = suiteEnv();
  env.KYOSEI_PROJECT_ROOT = fixture.root;
  env.KYOSEI_HOME = fixture.home;
  env[LOGIN_SHELL_ENV] = shell.shell;
  delete env[TMUX_ENV];
  // The pane's own `$SHELL`. Set to `/bin/sh` rather than left as the developer's,
  // so the shell a check types into is the same one on every desk -- the LOGIN shell
  // being asked is still the fixture's, which is what c1 is about.
  env.SHELL = "/bin/sh";
  // A runner reached over SSH would make `kyo controller start` (Start controller)
  // refuse as if the app had arrived over SSH.
  for (const name of ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"]) delete env[name];
  return { ...env, ...extra };
}

async function launch(shell: LoginShellFixture, extra: Record<string, string> = {}): Promise<Page> {
  app = await _electron.launch({
    args: [APP_ROOT, ...MACOS_NO_RESTORE, windowShowArg()],
    env: envFor(shell, extra),
  });
  const page = await app.firstWindow();
  await page.waitForSelector('body[data-ready="true"]');
  markReadyToClose(app);
  // KYO-232: the terminal is a TAB now, opened from the rail like anything else, and
  // a window that has not been asked for one has no pty. Opened here rather than in
  // each check, because every check in this file is about what is behind it.
  await openKind(page, "terminal");
  return page;
}

/** The tab's screens, the strip above them, and the line that says what this pane is. */
function paneOf(page: Page) {
  const body = page.locator('[data-region="tab-body"][data-tab-kind="terminal"]');
  return {
    body,
    status: page.locator('[data-region="terminal-status"]'),
    pills: page.locator('[data-region="terminal-pills"]'),
    bare: page.locator('[data-region="terminal-bare"]'),
    pill: (name: string) => page.locator(`[data-terminal-pill="${name}"]`),
    // The pane's two screens (KYO-293): the tmux client's and the bare login shell's,
    // each with an xterm of its own. Read separately, because the whole question c2
    // asks is whether what is on one of them survives the other being shown.
    surface: (surface: "client" | "bare") =>
      page.locator(`[data-region="terminal-surface"][data-terminal-surface="${surface}"]`),
  };
}

/** What is on one of the pane's two screens right now. */
async function screenText(page: Page, surface: "client" | "bare"): Promise<string> {
  return await paneOf(page).surface(surface).innerText();
}

/** The pill row as the reader sees it: the names, in the order they are drawn. */
async function pillNames(page: Page): Promise<string[]> {
  return await page
    .locator('[data-region="terminal-pill"]')
    .evaluateAll((nodes) => nodes.map((node) => (node as HTMLElement).dataset.terminalPill ?? ""));
}

/**
 * Type a line into whichever of the pane's two screens is showing, once it has printed
 * anything at all.
 *
 * The click is on the SHOWN surface and not on the tab body (KYO-293): with two xterms
 * stacked in the pane, a click on the body would be a click on whichever one the
 * browser decided was under the pointer, and the keystrokes would go to that one's pty.
 */
async function typeInPane(page: Page, line: string): Promise<void> {
  const { body } = paneOf(page);
  const surface = ((await body.getAttribute("data-terminal-surface")) ?? "bare") as
    | "client"
    | "bare";
  const screen = paneOf(page).surface(surface);
  await expect.poll(async () => (await screen.innerText()).trim().length).toBeGreaterThan(0);
  await screen.click();
  await page.keyboard.type(`${line}\n`);
}

// ---------------------------------------------------------------------------- c1

test("the login shell's environment reaches the pty, PATH and all", async () => {
  const shell = makeLoginShell({ tmux: null });
  try {
    const page = await launch(shell);
    const { body } = paneOf(page);

    // The marker: a variable that exists ONLY in the environment the fixture login
    // shell exports. This process never had it, so its presence in the pane is the
    // capture reaching the pty and nothing else.
    await typeInPane(page, 'echo "MARKER:$KYOSEI_FIXTURE_MARKER"');
    await expect
      .poll(async () => await body.innerText(), { timeout: 20_000 })
      .toContain(`MARKER:${shell.marker}`);

    // And the symptom the record was filed about, from the other end: `npm` resolves
    // in the pane, because the PATH is the login shell's and not `launchd`'s four
    // directories. When the record was filed, this was the line that failed.
    await typeInPane(page, "command -v npm");
    await expect
      .poll(async () => await body.innerText(), { timeout: 20_000 })
      .toContain(join(shell.binDir, "npm"));
  } finally {
    shell.remove();
  }
});

// ------------------------------------------------ KYO-474.c2: the controller's server

test("the pane opens in the project's controller session, on the controller's server (KYO-474.c2)", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  const shell = makeLoginShell({ tmux: REAL_TMUX });
  const ctl = controllerSocket();
  try {
    // The controller's session, as `kyo controller start` leaves it: detached, named
    // after the prefix, on the controller's server -- made here from OUTSIDE the app,
    // with a line on its screen the app could not have written.
    startControllerSession(ctl, "FIX");
    ctlTmux(ctl, ["send-keys", "-t", "=FIX:", "echo FROM-THE-CONTROLLER", "Enter"]);

    const page = await launch(shell, { [CONTROLLER_SOCKET_ENV]: ctl });
    const { body, status, pills } = paneOf(page);
    await expect(body).toHaveAttribute("data-terminal", "live", { timeout: 20_000 });
    await expect(body).toHaveAttribute("data-terminal-mode", "tmux", { timeout: 20_000 });
    await expect(body).toHaveAttribute("data-terminal-session", "FIX");
    await expect(body).toHaveAttribute("data-terminal-surface", "client");
    // The same socket `kyo controller` resolves under this environment.
    await expect(body).toHaveAttribute("data-terminal-socket", ctl);
    await expect(status).toContainText("orchestrator session FIX");
    await expect(status).toContainText("kyo controller attach FIX");

    // The pane's VISIBLE screen shows the session's own output: attached, not created.
    await expect
      .poll(async () => await screenText(page, "client"), { timeout: 20_000 })
      .toContain("FROM-THE-CONTROLLER");
    expect(sessionsOnSocket(ctl).filter((name) => name === "FIX")).toHaveLength(1);

    // Its pills are that server's sessions: the project's, set apart, live, current.
    await expect(pills).toBeVisible();
    await expect.poll(() => pillNames(page), { timeout: 20_000 }).toEqual(["FIX"]);
    const pill = paneOf(page).pill("FIX");
    await expect(pill).toHaveAttribute("data-terminal-pill-project", "true");
    await expect(pill).toHaveAttribute("data-terminal-pill-live", "true", { timeout: 20_000 });
    await expect(pill).toHaveAttribute("data-terminal-pill-current", "true", { timeout: 20_000 });

    // What is typed reaches the session, read from the server rather than the xterm.
    await typeInPane(page, "echo IN-THE-CONTROLLER-$((6*7))");
    await expect
      .poll(() => capturedOn(ctl).join("\n"), { timeout: 20_000 })
      .toContain("IN-THE-CONTROLLER-42");

    // And nothing at all on the default server (the login shell's TMUX_TMPDIR): the app
    // no longer creates a session of its own there.
    expect(listSessions(shell)).toEqual([]);
  } finally {
    killServerAt(ctl);
    killServer(shell);
    shell.remove();
  }
});

test("with no controller session the pane says so, offers Start controller, and creates nothing; Start starts it and the pane attaches (KYO-474.c2, c4)", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  test.setTimeout(120_000);
  const shell = makeLoginShell({ tmux: REAL_TMUX });
  const ctl = controllerSocket();
  const settings = join(fixture.home, "settings.yml");
  // What `kyo controller start` runs (KYO-462's `controller_command`): a stand-in for
  // claude that says who it is and waits, never the real one.
  const standIn = join(shell.dir, "stand-in controller");
  writeFileSync(standIn, '#!/bin/sh\necho "STAND-IN CONTROLLER $1"\nexec sleep 600\n', "utf8");
  chmodSync(standIn, 0o755);
  writeFileSync(settings, `controller_command: ${JSON.stringify([standIn, "{prefix}"])}\n`, "utf8");
  try {
    const page = await launch(shell, { [CONTROLLER_SOCKET_ENV]: ctl });
    const { body, status } = paneOf(page);
    const offer = page.locator('[data-region="terminal-offer"]');
    await expect(offer).toBeVisible({ timeout: 20_000 });
    await expect(offer).toHaveAttribute("data-terminal-offer", "start");
    await expect(offer).toContainText("no orchestrator session FIX is running");
    await expect(offer).toContainText("Start orchestrator");
    await expect(status).toContainText("no orchestrator session FIX");
    // The pane is its bare shell meanwhile, and no session was made anywhere: not on the
    // controller's server, and not on the default one.
    await expect(body).toHaveAttribute("data-terminal-surface", "bare");
    expect(sessionsOnSocket(ctl)).toEqual([]);
    expect(listSessions(shell)).toEqual([]);

    // The offer is the KYO-460 flow: `kyo controller start`, then the pane attaches.
    await page.locator('[data-region="terminal-offer-action"]').click();
    await expect(body).toHaveAttribute("data-terminal-mode", "tmux", { timeout: 60_000 });
    await expect(body).toHaveAttribute("data-terminal-session", "FIX");
    await expect(offer).toBeHidden();
    await expect
      .poll(async () => await screenText(page, "client"), { timeout: 20_000 })
      .toContain("STAND-IN CONTROLLER FIX");
    expect(sessionsOnSocket(ctl)).toEqual(["FIX"]);
    expect(listSessions(shell)).toEqual([]);
  } finally {
    rmSync(settings, { force: true });
    killServerAt(ctl);
    killServer(shell);
    shell.remove();
  }
});

test("a session on the default server is not listed and is left alone (KYO-474.c2)", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  const shell = makeLoginShell({ tmux: REAL_TMUX });
  const ctl = controllerSocket();
  try {
    // One the person made on the default server before the app started, and the
    // project's controller session on the controller's server.
    tmux(shell, ["new-session", "-d", "-s", "somebody-elses", "/bin/sh"]);
    startControllerSession(ctl, "FIX");
    ctlTmux(ctl, ["new-session", "-d", "-s", "aaa-other", "/bin/sh"]);
    ctlTmux(ctl, ["new-session", "-d", "-s", "zzz-other", "/bin/sh"]);

    const page = await launch(shell, { [CONTROLLER_SOCKET_ENV]: ctl });
    await expect(paneOf(page).body).toHaveAttribute("data-terminal-mode", "tmux", {
      timeout: 20_000,
    });
    // Every session on the CONTROLLER's server, the project's first -- and none of the
    // default server's.
    await expect
      .poll(() => pillNames(page), { timeout: 20_000 })
      .toEqual(["FIX", "aaa-other", "zzz-other"]);
    await expect(paneOf(page).pill("aaa-other")).toHaveAttribute(
      "data-terminal-pill-project",
      "false",
    );

    // A session that ends drops off, on the poll and without anybody telling the app.
    // `kill-session` is a thing this SUITE may do to a server it owns and the app may
    // never do at all -- `seams.spec.ts` scans the sources for the word.
    ctlTmux(ctl, ["kill-session", "-t", "=zzz-other"]);
    await expect.poll(() => pillNames(page), { timeout: 20_000 }).toEqual(["FIX", "aaa-other"]);

    // The default server's session is exactly as it was: there, with no client.
    await closeReadyApp(app as ElectronApplication);
    app = null;
    expect(listSessions(shell)).toEqual(["somebody-elses"]);
    expect(tmux(shell, ["list-clients", "-F", "#{client_tty}"]).trim()).toBe("");
  } finally {
    killServerAt(ctl);
    killServer(shell);
    shell.remove();
  }
});

test("the app and the CLI name the same controller socket (KYO-474.c2)", () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  // One: the CLI's own constants are the app's. Read from the Python source, because
  // the two sides are two languages and nothing else ties them together.
  const python = readFileSync(
    join(APP_ROOT, "..", "..", "src", "kyosei", "cmd_controller.py"),
    "utf8",
  );
  expect(python).toContain(`SOCKET_NAME = "${CONTROLLER_SOCKET_NAME}"`);
  expect(python).toContain(`SOCKET_ENV = "${CONTROLLER_SOCKET_ENV}"`);
  expect(python).toMatch(/DROPPED_ENV = \("TMUX", "TMUX_PANE", "TMUX_TMPDIR"\)/);

  // Two: under KYOSEI_CONTROLLER_SOCKET, `kyo controller list --json` names the same
  // server the app does, spelled the same way.
  const ctl = controllerSocket();
  try {
    const env = suiteEnv({ KYOSEI_HOME: fixture.home, [CONTROLLER_SOCKET_ENV]: ctl });
    const { program, baseArgs } = testResolution().source;
    if (program === null) throw new Error("no runnable kyo for this check");
    const listed = JSON.parse(
      execFileSync(program, [...baseArgs, "controller", "list", "--json"], {
        cwd: fixture.root,
        env,
        encoding: "utf8",
      }),
    ) as { socket: string };
    expect(listed.socket).toBe(`tmux -S ${ctl}`);
    expect(controllerSocketSpelled(env)).toBe(listed.socket);
    expect(controllerSocketPath(env)).toBe(ctl);
  } finally {
    rmSync(join(ctl, ".."), { recursive: true, force: true });
  }

  // Three: without it, the CLI's `-L kyosei` is tmux's own rule under a scrubbed
  // TMUX_TMPDIR -- measured on a PRIVATE `-L` name, so the real `tmux -L kyosei` is
  // never reached, and compared with what the app computes for that name.
  const name = `kyo474-${process.pid}-${Math.random().toString(36).slice(2, 8)}`;
  const scrubbed = suiteEnv();
  delete scrubbed[CONTROLLER_SOCKET_ENV];
  const uid = typeof process.getuid === "function" ? process.getuid() : 0;
  try {
    const made = spawnSync(
      REAL_TMUX as string,
      ["-L", name, "-f", "/dev/null", "new-session", "-d", "-s", "probe", "sleep 30"],
      { env: scrubbed, encoding: "utf8" },
    );
    expect(made.status, made.stderr).toBe(0);
    const said = execFileSync(
      REAL_TMUX as string,
      ["-L", name, "display-message", "-p", "-t", "=probe", "#{socket_path}"],
      { env: scrubbed, encoding: "utf8" },
    ).trim();
    expect(controllerSocketPath(scrubbed, uid, name)).toBe(said);
    // TMUX_TMPDIR, which the CLI drops, would have moved it: the app does not read it.
    expect(controllerSocketPath({ ...scrubbed, TMUX_TMPDIR: shellDir() }, uid, name)).toBe(said);
  } finally {
    spawnSync(REAL_TMUX as string, ["-L", name, "kill-server"], { env: scrubbed });
  }
});

// ------------------------------------------------ KYO-293: two surfaces, one pane

test("a busy bare shell does not block an attach, and keeps its own screen (KYO-293.c2)", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  const shell = makeLoginShell({ tmux: REAL_TMUX });
  const ctl = controllerSocket();
  try {
    startControllerSession(ctl, "FIX");
    const page = await launch(shell, { [CONTROLLER_SOCKET_ENV]: ctl });
    const { body, bare } = paneOf(page);
    await expect(body).toHaveAttribute("data-terminal-mode", "tmux", { timeout: 20_000 });

    // Down to the bare shell, and leave it busy in a foreground command -- the exact
    // situation KYO-293 was filed about: with the bare shell running a long command,
    // the tmux session could not be opened, because opening it meant typing the attach
    // command into that busy shell.
    await bare.click();
    await expect(body).toHaveAttribute("data-terminal-surface", "bare", { timeout: 20_000 });
    await typeInPane(page, "echo MY-OWN-SHELL; sleep 120");
    await expect
      .poll(async () => await screenText(page, "bare"), { timeout: 20_000 })
      .toContain("MY-OWN-SHELL");

    // With that shell holding its own pty, the pill attaches AT ONCE.
    await paneOf(page).pill("FIX").click();
    await expect(body).toHaveAttribute("data-terminal-mode", "tmux", { timeout: 20_000 });
    await expect(body).toHaveAttribute("data-terminal-surface", "client");
    await typeInPane(page, "echo IN-THE-SESSION-ANYWAY");
    await expect
      .poll(async () => await screenText(page, "client"), { timeout: 20_000 })
      .toContain("IN-THE-SESSION-ANYWAY");

    // The command the reader left running never saw a keystroke.
    const barely = await screenText(page, "bare");
    expect(barely).toContain("MY-OWN-SHELL");
    expect(barely).not.toContain("attach-session");
    expect(barely).not.toContain("IN-THE-SESSION-ANYWAY");

    await bare.click();
    await expect(body).toHaveAttribute("data-terminal-surface", "bare", { timeout: 20_000 });
    await expect
      .poll(async () => await screenText(page, "bare"), { timeout: 20_000 })
      .toContain("MY-OWN-SHELL");
  } finally {
    killServerAt(ctl);
    killServer(shell);
    shell.remove();
  }
});

test("the leftmost control shows the bare shell and detaches the client, never the session (KYO-293.c3)", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  const shell = makeLoginShell({ tmux: REAL_TMUX });
  const ctl = controllerSocket();
  try {
    // A session made from outside, with a job in it, so what survives the detach is a
    // process the app never started.
    startControllerSession(ctl, "FIX");
    ctlTmux(ctl, ["send-keys", "-t", "=FIX:", "sleep 600 & echo SURVIVOR:$!", "Enter"]);

    const page = await launch(shell, { [CONTROLLER_SOCKET_ENV]: ctl });
    const { body, bare } = paneOf(page);
    await expect(body).toHaveAttribute("data-terminal-mode", "tmux", { timeout: 20_000 });
    await expect(bare).toBeEnabled({ timeout: 20_000 });

    await bare.click();
    await expect(body).toHaveAttribute("data-terminal-mode", "bare", { timeout: 20_000 });
    await expect(body).toHaveAttribute("data-terminal-surface", "bare");
    await expect(paneOf(page).status).toContainText("keep running");
    await typeInPane(page, "echo BACK-IN-MY-SHELL");
    await expect
      .poll(async () => await screenText(page, "bare"), { timeout: 20_000 })
      .toContain("BACK-IN-MY-SHELL");
    // The detach was a detach: the session is still there, and the job in it.
    expect(sessionsOnSocket(ctl)).toContain("FIX");
    const pid = /SURVIVOR:(\d+)/.exec(capturedOn(ctl).join("\n"))?.[1];
    expect(pid).toBeDefined();

    // Back again, and it is the SAME shell (c3).
    await paneOf(page).pill("FIX").click();
    await expect(body).toHaveAttribute("data-terminal-mode", "tmux", { timeout: 20_000 });
    await expect
      .poll(async () => await screenText(page, "client"), { timeout: 20_000 })
      .toContain(`SURVIVOR:${pid}`);
    await bare.click();
    await expect(body).toHaveAttribute("data-terminal-surface", "bare", { timeout: 20_000 });
    expect(await screenText(page, "bare")).toContain("BACK-IN-MY-SHELL");
  } finally {
    killServerAt(ctl);
    killServer(shell);
    shell.remove();
  }
});

test("a reader's own detach falls the pane back to bare and says which happened", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  const shell = makeLoginShell({ tmux: REAL_TMUX });
  const ctl = controllerSocket();
  try {
    startControllerSession(ctl, "FIX");
    const page = await launch(shell, { [CONTROLLER_SOCKET_ENV]: ctl });
    const { body, status } = paneOf(page);
    await expect(body).toHaveAttribute("data-terminal-mode", "tmux", { timeout: 20_000 });

    // The reader's own prefix key, which this app never sees: `detach-client` aimed at
    // the session from OUTSIDE stands in for it exactly.
    ctlTmux(ctl, ["detach-client", "-s", "=FIX"]);

    await expect(body).toHaveAttribute("data-terminal-surface", "bare", { timeout: 20_000 });
    await expect(status).toContainText("you detached", { timeout: 20_000 });
    expect(sessionsOnSocket(ctl)).toContain("FIX");
    await typeInPane(page, "echo FELL-BACK-TO-MY-SHELL");
    await expect
      .poll(async () => await screenText(page, "bare"), { timeout: 20_000 })
      .toContain("FELL-BACK-TO-MY-SHELL");
  } finally {
    killServerAt(ctl);
    killServer(shell);
    shell.remove();
  }
});

test("a pane in one session moves to another by detaching its own client first", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  const shell = makeLoginShell({ tmux: REAL_TMUX });
  const ctl = controllerSocket();
  try {
    startControllerSession(ctl, "FIX");
    ctlTmux(ctl, ["send-keys", "-t", "=FIX:", 'echo "THE-PROJECT"', "Enter"]);
    ctlTmux(ctl, ["new-session", "-d", "-s", "other-work", "/bin/sh"]);
    ctlTmux(ctl, ["send-keys", "-t", "=other-work:", 'echo "THE-OTHER"', "Enter"]);

    const page = await launch(shell, { [CONTROLLER_SOCKET_ENV]: ctl });
    const { body } = paneOf(page);
    await expect(body).toHaveAttribute("data-terminal-session", "FIX", { timeout: 20_000 });
    await expect
      .poll(async () => await screenText(page, "client"), { timeout: 20_000 })
      .toContain("THE-PROJECT");

    await paneOf(page).pill("other-work").click();
    await expect(body).toHaveAttribute("data-terminal-session", "other-work", { timeout: 20_000 });
    await expect
      .poll(async () => await screenText(page, "client"), { timeout: 20_000 })
      .toContain("THE-OTHER");
    await expect(paneOf(page).pill("other-work")).toHaveAttribute(
      "data-terminal-pill-current",
      "true",
    );
    await expect(paneOf(page).pill("FIX")).toHaveAttribute("data-terminal-pill-current", "false");
    // The one it left is still running: a detach, never anything else.
    expect(sessionsOnSocket(ctl)).toContain("FIX");
  } finally {
    killServerAt(ctl);
    killServer(shell);
    shell.remove();
  }
});

test("quitting leaves the session and everything in it running; relaunching comes back to it", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  const shell = makeLoginShell({ tmux: REAL_TMUX });
  const ctl = controllerSocket();
  try {
    startControllerSession(ctl, "FIX");
    const first = await launch(shell, { [CONTROLLER_SOCKET_ENV]: ctl });
    await expect(paneOf(first).body).toHaveAttribute("data-terminal-mode", "tmux", {
      timeout: 20_000,
    });
    // Unquoted, and `!` is why: an INTERACTIVE shell has history expansion on.
    await typeInPane(first, "sleep 600 & echo SURVIVOR:$!");
    await expect
      .poll(async () => await screenText(first, "client"), { timeout: 20_000 })
      .toMatch(/SURVIVOR:\d+/);
    const pid = /SURVIVOR:(\d+)/.exec(await screenText(first, "client"))?.[1];
    expect(pid).toBeDefined();

    await closeReadyApp(app as ElectronApplication);
    app = null;

    // The session is a server-side object: the app's pty was a CLIENT, and quitting
    // detached it. Nothing in this app ends a session.
    expect(sessionsOnSocket(ctl)).toContain("FIX");
    const alive = ctlTmux(ctl, ["list-panes", "-t", "=FIX:", "-F", "#{pane_pid}"]).trim();
    expect(alive).not.toBe("");

    const second = await launch(shell, { [CONTROLLER_SOCKET_ENV]: ctl });
    const body = paneOf(second).body;
    await expect(body).toHaveAttribute("data-terminal-mode", "tmux", { timeout: 20_000 });
    await expect(body).toHaveAttribute("data-terminal-session", "FIX");
    await expect
      .poll(async () => await screenText(second, "client"), { timeout: 20_000 })
      .toContain(`SURVIVOR:${pid}`);
    await typeInPane(second, `kill -0 ${pid} && echo "STILL-RUNNING"`);
    await expect
      .poll(async () => await screenText(second, "client"), { timeout: 20_000 })
      .toContain("STILL-RUNNING");
  } finally {
    killServerAt(ctl);
    killServer(shell);
    shell.remove();
  }
});

// -------------------------------------------------------------------- KYO-254.c2
//
// The two checks below are the ones that fail if `suiteEnv`'s scrub is ever removed.
// They do not describe the app: they describe THIS SUITE, and what it must not hand
// the app. That is why they live here rather than in `seams.spec.ts` -- the thing
// under test is the launch environment `envFor` builds, and this is the file whose
// checks the inheritance broke.

test("a repaint steps back to the size the screen holds by then, not the one it began with (KYO-364)", () => {
  // The pane's opening, without the pane: `show` asks for a repaint at the size measured
  // before the session strip is laid out, and the ResizeObserver reports the screen two
  // rows shorter inside the step's delay. The step back must land on the second size --
  // landing on the first is a pty two rows taller than its xterm, and a cursor drawn two
  // rows below the prompt.
  const sizes: Array<[number, number]> = [];
  const client = {
    pty: { resize: (cols: number, rows: number) => sizes.push([cols, rows]) },
    size: { cols: 128, rows: 47 },
    exited: false,
  } as unknown as PaneScreen;
  let pending: (() => void) | null = null;
  let delay = -1;
  repaintTmuxClient(client, { cols: 128, rows: 47 }, (run, ms) => {
    pending = run;
    delay = ms;
  });
  expect(sizes).toEqual([[128, 46]]);
  expect(delay).toBe(REPAINT_STEP_MS);
  // What `resizeSurface` does when the renderer's smaller measurement arrives.
  client.size = { cols: 128, rows: 45 };
  client.pty.resize(128, 45);
  (pending as unknown as () => void)();
  expect(sizes.at(-1)).toEqual([128, 45]);
});

test("a launch environment carries none of the host terminal's own variables", () => {
  // Planted in the RUNNER's environment, which is exactly where they were when
  // KYO-254 was found: a Claude Code session inside the packaged app's own terminal pane.
  const restore = plant({
    TMUX: "/nowhere/tmux-0/default,1,0",
    TMUX_PANE: "%99",
    TMUX_TMPDIR: "/nowhere",
    KYOSEI_DESKTOP: "1",
    [RETIRED_SHIM_ID_ENV]: "the-host-apps-shim",
  });
  try {
    const shell = makeLoginShell({ tmux: null });
    try {
      const env = envFor(shell);
      // Every name the scrub owns is absent -- not empty, absent: an empty `TMUX` is
      // still a `TMUX` to some readers, and tmux itself treats an empty TMUX_TMPDIR
      // as "use the default", which is a third server again.
      for (const name of HOST_PANE_ENV) {
        expect(Object.hasOwn(env, name), `${name} reached the launch environment`).toBe(false);
      }
      // And the scrub is a scrub, not a clean room: what the suite legitimately says
      // about this launch is still there, and so is the machine's own PATH.
      expect(env.KYOSEI_PROJECT_ROOT).toBe(fixture.root);
      expect(env[LOGIN_SHELL_ENV]).toBe(shell.shell);
      expect(env.PATH).toBe(process.env.PATH);
    } finally {
      shell.remove();
    }
  } finally {
    restore();
  }
});

test("the pane's server is the controller's, whatever TMUX_TMPDIR the app inherited", async () => {
  test.skip(REAL_TMUX === null, SKIP_WITHOUT_TMUX);
  // A second private server that NOTHING in this run should ever reach. It stands
  // where the host pane's `kyosei switch sockets` directory stood in KYO-254: a real,
  // reachable, writable tmux server directory that is not the controller's.
  const decoy = realpathSync(mkdtempSync(join(tmpdir(), "kyo")));
  const shell = makeLoginShell({ tmux: REAL_TMUX });
  const ctl = controllerSocket();
  const restore = plant({
    TMUX_TMPDIR: decoy,
    TMUX: `${join(decoy, "tmux-0", "default")},1,0`,
    TMUX_PANE: "%99",
    KYOSEI_DESKTOP: "1",
    [RETIRED_SHIM_ID_ENV]: "the-host-apps-shim",
  });
  try {
    startControllerSession(ctl, "FIX");
    const page = await launch(shell, { [CONTROLLER_SOCKET_ENV]: ctl });
    const { body } = paneOf(page);
    await expect(body).toHaveAttribute("data-terminal-mode", "tmux", { timeout: 20_000 });
    await expect(body).toHaveAttribute("data-terminal-session", "FIX");
    await expect(body).toHaveAttribute("data-terminal-socket", ctl);
    // Neither the planted server nor the login shell's has anything on it.
    expect(sessionsOn(decoy)).toEqual([]);
    expect(listSessions(shell)).toEqual([]);
  } finally {
    restore();
    killServerAt(ctl);
    killServer(shell);
    // Deliberately unconditional: if the socket rule ever broke, something has to end
    // what landed on the decoy. Never the DEFAULT server -- `killServerIn` names its
    // directory.
    killServerIn(decoy);
    shell.remove();
  }
});

test("no spec that launches the app copies the whole environment", () => {
  // The two checks above hold the scrub in place where it IS. This one holds it where
  // it is not yet: a spec added tomorrow that writes its own environment loop is
  // exactly how five deletions per file come to be forgotten, and nothing else in this
  // suite would notice.
  //
  // The needles are assembled at runtime rather than written out, and that is not
  // decoration. This file calls the launch function and so scans itself; a literal
  // needle would be satisfied by the copy of itself inside the assertion that names
  // it, and the check could then never fail -- the same trap README.md records against
  // the KYO-218 scans.
  const spellings = [
    ["Object", ".entries(", "process", ".env)"].join(""),
    ["...", "process", ".env"].join(""),
  ];
  const launches = ["_electron", ".launch("].join("");
  const offenders: string[] = [];
  for (const name of readdirSync(__dirname).filter((file) => file.endsWith(".spec.ts"))) {
    const source = readFileSync(join(__dirname, name), "utf8");
    // Only the files that start an app: `seams.spec.ts` builds environments for `kyo`
    // shims and tmux stubs and is entitled to say what goes in them.
    if (!source.includes(launches)) continue;
    const dense = source.replaceAll(/\s+/g, "");
    for (const spelling of spellings) {
      if (dense.includes(spelling)) offenders.push(`${name}: ${spelling}`);
    }
  }
  expect(
    offenders,
    "a spec that launches the app must build its environment with `suiteEnv()` from " +
      "./fixture, which drops the host terminal's TMUX, TMUX_PANE, TMUX_TMPDIR, " +
      "KYOSEI_DESKTOP and KYOSEI_SHIM_ID (KYO-254). Copying the whole environment is " +
      "what hands the app under test the terminal the suite was started in.",
  ).toEqual([]);
});

/**
 * Put these names in `process.env` for the length of one check, and give back the
 * function that restores it exactly -- including deleting the ones that were absent.
 */
function plant(values: Record<string, string>): () => void {
  const before = new Map<string, string | undefined>();
  for (const [key, value] of Object.entries(values)) {
    before.set(key, process.env[key]);
    process.env[key] = value;
  }
  return () => {
    for (const [key, value] of before) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  };
}

// ---------------------------------------------------------------------------- c3

test("with no tmux the pane is an app-owned shell and says what that costs", async () => {
  const shell = makeLoginShell({ tmux: null });
  try {
    const page = await launch(shell);
    const { body, status } = paneOf(page);

    // tmux is optional, never integral: the pane still works.
    await expect(body).toHaveAttribute("data-terminal", "live");
    await expect(body).toHaveAttribute("data-terminal-mode", "bare");
    await typeInPane(page, 'echo "STILL-A-SHELL"');
    await expect
      .poll(async () => await body.innerText(), { timeout: 20_000 })
      .toContain("STILL-A-SHELL");

    // And the header says plainly what this shell is and what it costs -- which is
    // the difference between the two panes a reader cannot otherwise see.
    await expect(status).toBeVisible();
    await expect(status).toHaveAttribute("data-terminal-mode", "bare");
    await expect(status).toContainText("ends when this app quits");
    // The one change that fixes it, named, on a platform where that command exists.
    if (process.platform === "darwin") await expect(status).toContainText("brew install tmux");
    // The PATH searched was the login shell's, so the app may say so rather than
    // hedging about a tmux that might be installed somewhere it cannot see (KYO-185's
    // hedge, superseded exactly here and nowhere else).
    await expect(status).toContainText(shell.shell);
  } finally {
    shell.remove();
  }
});

// --------------------------------------------------------------- the private servers

function tmux(shell: LoginShellFixture, args: string[]): string {
  return execFileSync(REAL_TMUX as string, args, {
    encoding: "utf8",
    env: { ...tmuxEnv(shell) },
  });
}

/**
 * The environment every tmux command in this file runs with: the fixture's own
 * server, and no trace of the terminal the suite was started in.
 *
 * `suiteEnv` is what drops `TMUX` and `TMUX_PANE` (KYO-254): with either of them set
 * tmux targets the server the RUNNER is sitting in whatever `TMUX_TMPDIR` says, which
 * is how a scratch `kill-server` from inside an app pane took a user's real tmux
 * server down. `killServer` below is that same command.
 */
function tmuxEnv(shell: LoginShellFixture): Record<string, string> {
  return suiteEnv({ TMUX_TMPDIR: shell.sockets });
}

/**
 * A socket for this check's own controller server -- the one `KYOSEI_CONTROLLER_SOCKET`
 * names for the app -- short enough for a unix socket path.
 */
function controllerSocket(): string {
  return join(realpathSync(mkdtempSync(join(tmpdir(), "k474-"))), "ctl");
}

/** A tmux command on this check's controller server, named by its socket and nothing else. */
function ctlTmux(socket: string, args: string[]): string {
  return execFileSync(REAL_TMUX as string, ["-S", socket, ...args], {
    encoding: "utf8",
    env: suiteEnv(),
  });
}

/** A detached session on the controller's server, as `kyo controller start` leaves one. */
function startControllerSession(socket: string, name: string): void {
  ctlTmux(socket, ["-f", "/dev/null", "new-session", "-d", "-s", name, "/bin/sh"]);
}

function sessionsOnSocket(socket: string): string[] {
  try {
    return ctlTmux(socket, ["list-sessions", "-F", "#{session_name}"])
      .split("\n")
      .filter((name) => name !== "");
  } catch {
    return [];
  }
}

/** What is on every pane of the controller's server, read from the server. */
function capturedOn(socket: string): string[] {
  try {
    return ctlTmux(socket, ["list-panes", "-a", "-F", "#{pane_id}"])
      .split("\n")
      .filter((id) => id !== "")
      .map((id) => ctlTmux(socket, ["capture-pane", "-p", "-t", id]));
  } catch {
    return [];
  }
}

/** End this check's controller server, by its socket, and remove its directory. */
function killServerAt(socket: string): void {
  if (REAL_TMUX !== null) {
    try {
      ctlTmux(socket, ["kill-server"]);
    } catch {
      // No server left; nothing this run started is still up.
    }
  }
  rmSync(join(socket, ".."), { recursive: true, force: true });
}

/** A directory nothing listens in, for a TMUX_TMPDIR the app must not read. */
function shellDir(): string {
  return join(tmpdir(), "kyo474-not-read");
}

/**
 * The sessions on the login shell's own server -- the stand-in for the machine's
 * DEFAULT server, which the app must leave alone -- or none.
 */
function listSessions(shell: LoginShellFixture): string[] {
  return sessionsOn(shell.sockets);
}

/** The same question, of any server directory -- including one nothing should be on. */
function sessionsOn(sockets: string): string[] {
  if (REAL_TMUX === null) return [];
  try {
    return execFileSync(REAL_TMUX, ["list-sessions", "-F", "#{session_name}"], {
      encoding: "utf8",
      env: suiteEnv({ TMUX_TMPDIR: sockets }),
    })
      .split("\n")
      .filter((name) => name !== "");
  } catch {
    return [];
  }
}

function killServer(shell: LoginShellFixture): void {
  killServerIn(shell.sockets);
}

/**
 * Kill the server in ONE directory and remove it.
 *
 * `TMUX_TMPDIR` is the only thing that says which server this is, and `suiteEnv` is
 * what guarantees no `TMUX` in the environment overrides it (KYO-254). Every
 * `kill-server` on a TMUX_TMPDIR server in this file goes through here for that reason.
 */
function killServerIn(sockets: string): void {
  if (REAL_TMUX === null) return;
  try {
    execFileSync(REAL_TMUX, ["kill-server"], {
      encoding: "utf8",
      env: suiteEnv({ TMUX_TMPDIR: sockets }),
    });
  } catch {
    // No server left to kill; the sessions above were this run's only ones.
  }
  rmSync(sockets, { recursive: true, force: true });
}
