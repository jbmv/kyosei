// KYO-474: a dev box window's terminal tab is a client of the controller session THERE,
// through the person's own SSH -- `ssh -t <host> zsh -lc 'kyo controller attach
// <prefix>'`, built by `ssh.ts`'s one argv builder -- and its pills are that dev box's
// `kyo controller list --json`, over the same SSH.
//
//   KYO-474.c3  the exact argv; typed keys reach the far end; its output is on the
//               pane's visible screen; the pills are the far end's listing
//   KYO-474.c4  no session, an old `kyo`, ssh itself failing, a dropped connection:
//               each said in prose with the far end's own words, with Start controller
//               or Reconnect; closing the app only hangs up, and nothing is ever asked
//               to end a session
//
// NEVER A REAL HOST. The dev box is a second `kyo serve` on loopback (as in
// `remote.spec.ts`), and ssh is a stand-in (`KYOSEI_SSH`) that writes down every argv it
// is given and plays the far end: it answers `kyo controller list --json` with a listing
// whose state a file of this run's own decides, answers `kyo controller start`, and for
// `kyo controller attach` prints a banner, echoes every line typed at it, and writes the
// raw keystrokes down. No tmux runs at all, so nothing here can reach a real server.

import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _electron, type ElectronApplication, expect, type Page, test } from "@playwright/test";
import { type ControllerListing, pillsFromListing } from "../src/main/controller";
import { shellQuote } from "../src/main/kyo";
import {
  commandArgv,
  controllerAttachArgv,
  SSH_OPTIONS,
  SSH_PROGRAM_ENV,
  SSH_TTY_OPTION,
  sshCommandHost,
} from "../src/main/ssh";
import { saidByClient, TMUX_ENV } from "../src/main/terminal";
import {
  closeReadyApp,
  type Fixture,
  MACOS_NO_RESTORE,
  makeFixture,
  markReadyToClose,
  openKind,
  removeFixture,
  suiteEnv,
  useServe,
  windowShowArg,
} from "./fixture";

const APP_ROOT = join(__dirname, "..");
const LOCAL_HOST = "127.0.0.1";
/** The fixture's prefix (`makeFixture` runs `kyo init --prefix FIX`). */
const PREFIX = "FIX";

test.describe.configure({ mode: "serial" });

let client: ElectronApplication | null = null;
let page: Page;
let devBoxFixture: Fixture;
let laptopFixture: Fixture;
let profile: string;
let standIn: SshStandIn;

// ------------------------------------------------------------------- the stand-in

/** What the far end does, decided by a file the checks write. */
type FarEnd = "live" | "no-session" | "old-kyo" | "unreachable";

interface SshStandIn {
  dir: string;
  /** The ssh program the app is handed through `KYOSEI_SSH`. */
  program: string;
  /** Every argv it was given, in order (without the program). */
  calls(): string[][];
  /** The raw keystrokes an attached client was sent. */
  keys(): string;
  /** Signals an attached client received. */
  signals(): string;
  /** Set what the far end does from now on. */
  set(state: FarEnd): void;
}

function makeSshStandIn(base: string, root: string): SshStandIn {
  const dir = mkdtempSync(join(base, "ssh stand in "));
  const log = join(dir, "calls.jsonl");
  const keys = join(dir, "keys");
  const signals = join(dir, "signals");
  const state = join(dir, "far end");
  const script = join(dir, "fake-ssh.cjs");
  const listing = (session: string | null) =>
    JSON.stringify({
      ok: true,
      command: "controller",
      action: "list",
      socket: "tmux -L kyosei",
      tmux: "/opt/homebrew/bin/tmux",
      server_running: true,
      projects: [
        {
          project: "a project",
          prefix: PREFIX,
          root,
          registered: true,
          session,
          claude_alive: session !== null,
          pid: session === null ? null : 4242,
          command: session === null ? null : "claude",
          started_with: session === null ? null : "claude --remote-control FIX",
        },
      ],
      other_sessions: [],
    });
  writeFileSync(
    script,
    [
      "// KYO-474's stand-in for ssh: see tests/remote-terminal.spec.ts.",
      'const fs = require("node:fs");',
      "const argv = process.argv.slice(2);",
      `fs.appendFileSync(${JSON.stringify(log)}, JSON.stringify(argv) + "\\n");`,
      `const state = fs.existsSync(${JSON.stringify(state)}) ? fs.readFileSync(${JSON.stringify(state)}, "utf8").trim() : "live";`,
      "const command = argv[argv.length - 1] || '';",
      'if (state === "unreachable") {',
      '  process.stderr.write("ssh: connect to host 127.0.0.1 port 22: Connection refused\\n");',
      "  process.exit(255);",
      "}",
      'if (command.includes("controller list --json")) {',
      '  process.stdout.write("noise from a login profile\\n");',
      `  process.stdout.write((state === "no-session" ? ${JSON.stringify(listing(null))} : ${JSON.stringify(listing(PREFIX))}) + "\\n");`,
      "  process.exit(0);",
      '} else if (command.includes("controller start")) {',
      `  fs.writeFileSync(${JSON.stringify(state)}, "live");`,
      `  process.stdout.write(${JSON.stringify(
        JSON.stringify({
          ok: true,
          session: PREFIX,
          pid: 4242,
          cwd: root,
          started_with: "claude --remote-control FIX",
          attach: "tmux -L kyosei attach -t =FIX",
          remote_control_name: PREFIX,
        }),
      )} + "\\n");`,
      "  process.exit(0);",
      '} else if (command.includes("controller attach")) {',
      '  if (state === "old-kyo") {',
      '    process.stderr.write("usage: kyo controller [-h] [{start,stop,list}] [PROJECT]\\n");',
      "    process.stderr.write(\"kyo controller: error: argument action: invalid choice: 'attach' (choose from 'start', 'stop', 'list')\\n\");",
      "    process.exit(2);",
      "  }",
      '  process.stdout.write("STAND-IN ATTACHED TO THE FAR END\\r\\n");',
      '  for (const signal of ["SIGHUP", "SIGTERM"]) {',
      `    process.on(signal, () => { fs.appendFileSync(${JSON.stringify(signals)}, signal + "\\n"); process.exit(129); });`,
      "  }",
      '  let buffer = "";',
      '  process.stdin.setEncoding("utf8");',
      '  process.stdin.on("data", (chunk) => {',
      `    fs.appendFileSync(${JSON.stringify(keys)}, chunk);`,
      "    buffer += chunk;",
      "    for (let at = buffer.search(/[\\r\\n]/); at !== -1; at = buffer.search(/[\\r\\n]/)) {",
      "      const line = buffer.slice(0, at);",
      "      buffer = buffer.slice(at + 1);",
      '      if (line === "") continue;',
      '      if (line === "drop") {',
      '        process.stderr.write("Connection to 127.0.0.1 closed by remote host.\\r\\n");',
      "        process.exit(255);",
      "      }",
      '      process.stdout.write("ECHO:" + line + "\\r\\n");',
      "    }",
      "  });",
      '  process.stdin.on("end", () => process.exit(0));',
      "} else {",
      '  process.stderr.write("the stand-in does not know " + command + "\\n");',
      "  process.exit(127);",
      "}",
      "",
    ].join("\n"),
    "utf8",
  );
  const program = join(dir, "ssh");
  writeFileSync(
    program,
    ["#!/bin/sh", `exec ${shellQuote(process.execPath)} ${shellQuote(script)} "$@"`, ""].join("\n"),
    { encoding: "utf8", mode: 0o755 },
  );
  const read = (path: string) => (existsSync(path) ? readFileSync(path, "utf8") : "");
  return {
    dir,
    program,
    calls: () =>
      read(log)
        .split("\n")
        .filter(Boolean)
        .map((line) => JSON.parse(line) as string[]),
    keys: () => read(keys),
    signals: () => read(signals),
    set: (value) => writeFileSync(state, value, "utf8"),
  };
}

/** The calls whose far-end command is `kyo controller attach`. */
function attachCalls(): string[][] {
  return standIn.calls().filter((argv) => (argv.at(-1) ?? "").includes("controller attach"));
}

// ------------------------------------------------------------------- the pane

function pane() {
  const body = page.locator('[data-region="tab-body"][data-tab-kind="terminal"]:not([hidden])');
  return {
    body,
    status: page.locator('[data-region="terminal-status"]'),
    offer: page.locator('[data-region="terminal-offer"]'),
    offerAction: page.locator('[data-region="terminal-offer-action"]'),
    said: page.locator('[data-region="terminal-said"]'),
    detach: page.locator('[data-region="terminal-bare"]'),
    client: page.locator('[data-region="terminal-surface"][data-terminal-surface="client"]'),
    pill: (name: string) => page.locator(`[data-terminal-pill="${name}"]`),
  };
}

async function typeInClient(line: string): Promise<void> {
  const screen = pane().client;
  await expect.poll(async () => (await screen.innerText()).trim().length).toBeGreaterThan(0);
  await screen.click();
  await page.keyboard.type(`${line}\n`);
}

test.beforeAll(async () => {
  test.setTimeout(180_000);
  devBoxFixture = await makeFixture({ records: 0 });
  laptopFixture = await makeFixture({ records: 0, dir: "b project" });
  useServe(laptopFixture);
  profile = mkdtempSync(join(tmpdir(), "kyosei remote terminal profile "));
  standIn = makeSshStandIn(profile, devBoxFixture.root);
  writeFileSync(
    join(profile, "project.json"),
    JSON.stringify({ chosen: laptopFixture.root, recents: [laptopFixture.root] }),
    "utf8",
  );
  const env = suiteEnv({ [SSH_PROGRAM_ENV]: standIn.program });
  delete env.KYOSEI_PROJECT_ROOT;
  env.KYOSEI_HOME = laptopFixture.home;
  env[TMUX_ENV] = "off";
  env.KYOSEI_LOGIN_SHELL = "off";
  client = await _electron.launch({
    args: [APP_ROOT, `--user-data-dir=${profile}`, ...MACOS_NO_RESTORE, windowShowArg()],
    env,
  });
  page = await client.firstWindow();
  await page.waitForSelector('body[data-ready="true"]');
  await page.waitForSelector('body[data-facts="ready"]');
  markReadyToClose(client);
  const onDevBox = await page.evaluate((input) => window.kyosei.connectRemote(input), {
    address: devBoxFixture.serve.url,
    root: devBoxFixture.root,
  });
  expect(onDevBox.refused, JSON.stringify(onDevBox.refused)).toBeNull();
  expect(onDevBox.project.remote).toEqual({
    host: LOCAL_HOST,
    port: devBoxFixture.serve.address.port,
  });
});

test.afterAll(async () => {
  if (client !== null) await closeReadyApp(client);
  client = null;
  removeFixture(devBoxFixture);
  removeFixture(laptopFixture);
  rmSync(profile, { recursive: true, force: true });
});

// ------------------------------------------------------------------- c3: the argv

test("the dev box client is ssh -t to the SSH host with zsh -lc 'kyo controller attach <prefix>', from the one builder (KYO-474.c3)", () => {
  const host = sshCommandHost("devbox", "/usr/bin/ssh");
  const argv = controllerAttachArgv(host, PREFIX);
  expect(argv).toEqual([
    "/usr/bin/ssh",
    SSH_TTY_OPTION,
    ...SSH_OPTIONS,
    "--",
    "devbox",
    "zsh",
    "-lc",
    "'kyo controller attach FIX'",
  ]);
  expect(SSH_TTY_OPTION).toBe("-t");
  // BatchMode stays: a password prompt in the pane would be one nothing answers for.
  expect(argv).toContain("BatchMode=yes");
  // It IS the one builder, with a tty; everything else the app runs over SSH has none.
  expect(argv).toEqual(commandArgv(host, ["kyo", "controller", "attach", PREFIX], { tty: true }));
  expect(commandArgv(host, ["kyo", "controller", "list", "--json"])).not.toContain("-t");
  // What the far end's shells make of the last word: `zsh -lc` gets exactly the verb.
  const unquoted = spawnSync("/bin/sh", ["-c", `printf '%s' ${argv.at(-1)}`], {
    encoding: "utf8",
  });
  expect(unquoted.stdout).toBe("kyo controller attach FIX");
});

test("a dev box's pills are its listing's sessions, this project's first (KYO-474.c3)", () => {
  const row = (root: string, prefix: string, session: string | null) => ({
    project: prefix.toLowerCase(),
    prefix,
    root,
    session,
    claudeAlive: session !== null,
    pid: null,
    startedWith: null,
    trustFolder: null,
  });
  const listing: ControllerListing = {
    socket: "tmux -L kyosei",
    serverRunning: true,
    projects: [row("/a", "AAA", "AAA"), row("/me", PREFIX, null), row("/z", "ZZZ", "ZZZ")],
  };
  expect(pillsFromListing(listing, "/me/", PREFIX)).toEqual([
    { name: PREFIX, project: true, live: false },
    { name: "AAA", project: false, live: true },
    { name: "ZZZ", project: false, live: true },
  ]);
  const running = { ...listing, projects: [row("/me", PREFIX, PREFIX), row("/a", "AAA", "AAA")] };
  expect(pillsFromListing(running, "/me", PREFIX).map((pill) => [pill.name, pill.live])).toEqual([
    [PREFIX, true],
    ["AAA", true],
  ]);
});

test("what a client printed is repeated in prose with the escapes removed and the words kept (KYO-474.c4)", () => {
  const raw =
    "\x1b[?1049h\x1b[H\x1b[2Jnoise\r\n\r\n\r\nusage: kyo controller\r\n" +
    "kyo controller: error: argument action: invalid choice: 'attach'\r\n\x1b[0m";
  expect(saidByClient(raw)).toBe(
    "noise\n\nusage: kyo controller\nkyo controller: error: argument action: invalid choice: 'attach'",
  );
  expect(saidByClient("a\nb\nc\nd", 2)).toBe("c\nd");
  expect(saidByClient("\x1b[31m\x1b[0m")).toBe("");
});

// ------------------------------------------------------------------- c3: the pane

test("the pane attaches over ssh: exact argv, typed keys reach the far end, its output is on the visible screen, the pills are its listing (KYO-474.c3)", async () => {
  test.setTimeout(120_000);
  standIn.set("live");
  await openKind(page, "terminal");
  const { body, client: screen, status } = pane();
  await expect(body).toHaveAttribute("data-terminal", "live", { timeout: 30_000 });
  await expect(body).toHaveAttribute("data-terminal-where", "ssh");
  await expect(body).toHaveAttribute("data-terminal-ssh-host", LOCAL_HOST);
  await expect(body).toHaveAttribute("data-terminal-surface", "client");
  await expect(body).toHaveAttribute("data-terminal-mode", "tmux", { timeout: 30_000 });
  await expect(body).toHaveAttribute("data-terminal-session", PREFIX);
  await expect(status).toContainText(`orchestrator session ${PREFIX}`);

  // The exact argv, as the stand-in received it: `-t`, the options, the SSH host the
  // window's server entry carries (its host name, since none was set), and the verb.
  await expect.poll(() => attachCalls().length, { timeout: 30_000 }).toBe(1);
  expect(attachCalls()[0]).toEqual([
    "-t",
    ...SSH_OPTIONS,
    "--",
    LOCAL_HOST,
    "zsh",
    "-lc",
    "'kyo controller attach FIX'",
  ]);
  // The far end's own output, on the pane's visible screen.
  await expect(screen).toBeVisible();
  await expect
    .poll(async () => await screen.innerText(), { timeout: 30_000 })
    .toContain("STAND-IN ATTACHED TO THE FAR END");

  // Typed keys reach the stand-in, and what it answers comes back to the screen.
  await typeInClient("hello-from-the-pane");
  await expect.poll(() => standIn.keys(), { timeout: 30_000 }).toContain("hello-from-the-pane");
  await expect
    .poll(async () => await screen.innerText(), { timeout: 30_000 })
    .toContain("ECHO:hello-from-the-pane");

  // The pills are the dev box's `kyo controller list --json`, over the same ssh.
  const pill = pane().pill(PREFIX);
  await expect(pill).toBeVisible({ timeout: 30_000 });
  await expect(pill).toHaveAttribute("data-terminal-pill-project", "true");
  await expect(pill).toHaveAttribute("data-terminal-pill-current", "true");
  const listed = standIn.calls().filter((argv) => (argv.at(-1) ?? "").includes("controller list"));
  expect(listed.length).toBeGreaterThan(0);
  expect(listed[0]).toEqual([
    ...SSH_OPTIONS,
    "--",
    LOCAL_HOST,
    "zsh",
    "-lc",
    "'kyo controller list --json'",
  ]);
});

// ------------------------------------------------------------------- c4

test("a dropped connection is said with ssh's own words and Reconnect, which attaches again (KYO-474.c4)", async () => {
  test.setTimeout(120_000);
  const { body, offer, offerAction, said, client: screen } = pane();
  await typeInClient("drop");
  await expect(offer).toBeVisible({ timeout: 30_000 });
  await expect(offer).toHaveAttribute("data-terminal-offer", "reconnect");
  await expect(offer).toContainText(`the connection to ${PREFIX} on ${LOCAL_HOST} ended`);
  await expect(offer).toContainText("exited with status 255");
  await expect(said).toContainText("Connection to 127.0.0.1 closed by remote host.");
  await expect(body).toHaveAttribute("data-terminal-action", "ended");
  await expect(offerAction).toHaveText("Reconnect");

  await offerAction.click();
  await expect.poll(() => attachCalls().length, { timeout: 30_000 }).toBe(2);
  await expect(offer).toBeHidden({ timeout: 30_000 });
  await expect(body).toHaveAttribute("data-terminal-mode", "tmux", { timeout: 30_000 });
  await typeInClient("back-again");
  await expect
    .poll(async () => await screen.innerText(), { timeout: 30_000 })
    .toContain("ECHO:back-again");
});

test("no session there is said, with Start controller, which starts it over ssh and attaches (KYO-474.c4)", async () => {
  test.setTimeout(120_000);
  const { body, offer, offerAction, detach } = pane();
  // The session goes away while the pane is detached.
  await detach.click();
  await expect(offer).toHaveAttribute("data-terminal-offer", "reconnect", { timeout: 30_000 });
  await expect(offer).toContainText(`detached from ${PREFIX} on ${LOCAL_HOST}`);
  standIn.set("no-session");
  const before = attachCalls().length;

  await offerAction.click();
  await expect(offer).toHaveAttribute("data-terminal-offer", "start", { timeout: 30_000 });
  await expect(offer).toContainText(
    `no orchestrator session ${PREFIX} is running on ${LOCAL_HOST}`,
  );
  await expect(offerAction).toHaveText("Start orchestrator");
  // Nothing attached: the listing said there was nothing to attach to.
  expect(attachCalls().length).toBe(before);

  await offerAction.click();
  await expect.poll(() => attachCalls().length, { timeout: 60_000 }).toBe(before + 1);
  await expect(body).toHaveAttribute("data-terminal-mode", "tmux", { timeout: 30_000 });
  await expect(offer).toBeHidden();
  // The KYO-460 flow: `kyo controller start` with the project's root, over ssh.
  const started = standIn
    .calls()
    .filter((argv) => (argv.at(-1) ?? "").includes("controller start"));
  expect(started).toHaveLength(1);
  expect(started[0]?.at(-1)).toBe(
    shellQuote(`kyo controller start --json ${shellQuote(devBoxFixture.root)}`),
  );
});

test("a kyo there that predates attach is shown in its own words (KYO-474.c4)", async () => {
  test.setTimeout(120_000);
  const { offer, offerAction, said, detach } = pane();
  await detach.click();
  await expect(offer).toHaveAttribute("data-terminal-offer", "reconnect", { timeout: 30_000 });
  standIn.set("old-kyo");
  await offerAction.click();
  await expect(said).toContainText("invalid choice: 'attach'", { timeout: 30_000 });
  await expect(offer).toContainText("exited with status 2");
  await expect(offer).toHaveAttribute("data-terminal-offer", "reconnect");
});

test("ssh itself failing is said in ssh's own words, with Reconnect (KYO-474.c4)", async () => {
  test.setTimeout(120_000);
  const { offer, offerAction, said } = pane();
  standIn.set("unreachable");
  await offerAction.click();
  await expect(said).toContainText("ssh: connect to host 127.0.0.1 port 22: Connection refused", {
    timeout: 30_000,
  });
  await expect(offer).toContainText(`could not reach ${LOCAL_HOST}`);
  await expect(offer).toHaveAttribute("data-terminal-offer", "reconnect");

  // And Reconnect is the remedy it names: once ssh works again, it attaches.
  standIn.set("live");
  const before = attachCalls().length;
  await offerAction.click();
  await expect.poll(() => attachCalls().length, { timeout: 30_000 }).toBe(before + 1);
  await expect(pane().body).toHaveAttribute("data-terminal-mode", "tmux", { timeout: 30_000 });
});

test("quitting the app only hangs up: nothing is ever asked to end a session (KYO-474.c4)", async () => {
  test.setTimeout(120_000);
  await expect
    .poll(async () => await pane().client.innerText(), { timeout: 30_000 })
    .toContain("STAND-IN ATTACHED TO THE FAR END");
  await closeReadyApp(client as ElectronApplication);
  client = null;
  // The attached client was hung up -- which is what a far end's tmux reads as a
  // client going away, and nothing more.
  await expect.poll(() => standIn.signals(), { timeout: 15_000 }).toContain("SIGHUP");
  // And across everything this file did, no call asked the far end to end anything.
  const everything = standIn.calls().map((argv) => argv.join(" "));
  expect(everything.length).toBeGreaterThan(0);
  for (const words of everything) {
    for (const ending of ["kill-session", "kill-server", "controller stop"]) {
      expect(words, `a call asked the far end to ${ending}`).not.toContain(ending);
    }
  }
});
