// KYO-275: the app under test never touches the packaged app's userData directory.
//
// KYO-189 decided that every build of this app shares one `userData` directory --
// `~/Library/Application Support/@kyosei/desktop` on macOS -- and with it the `kyo`
// shim every pane runs. This suite is a launcher that decision never counted. Each app
// it started regenerated the user's LIVE shim on launch and pointed it at this
// checkout's CLI, so a test run switched two consumer projects' open panes to a
// schema-20 CLI against schema-18 stores, with KYO-189's own warning correctly firing
// and its remedy ("quit the other build") naming a test that had already exited.
//
// The check below is the one that fails if the seam is ever removed: it snapshots the
// real directory's state files and shell-env log, launches an app the way every other
// spec does, and asserts those files are byte-identical afterwards. Run on a machine
// without the real directory (a fresh checkout, CI), the snapshot is empty and the
// assertion is that it stays that way.
//
// KYO-450: the shims are gone. A launch writes no `kyo` anywhere -- not at `ready`
// (KYO-315's per-build `cli/<id>/`) and not when a pane opens (the shared `bin/`) --
// and the first launch of this build REMOVES the ones an earlier build left in its own
// userData. So this check now plants both directories in the suite's directory, opens
// a pane, and asserts the launch took them away and wrote none back, while the real
// directory's `bin/` and `cli/` -- which belong to whatever build the user runs --
// are left exactly as they were.
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { _electron, type ElectronApplication, expect, test } from "@playwright/test";
import { CLI_NAMES, RETIRED_SHIM_DIRS, TMUX_ENV, USER_DATA_ENV } from "../src/main/terminal";
import {
  closeReadyApp,
  type Fixture,
  MACOS_NO_RESTORE,
  makeFixture,
  markReadyToClose,
  openKind,
  removeFixture,
  suiteEnv,
  suiteUserData,
  windowShowArg,
} from "./fixture";

const APP_ROOT = join(__dirname, "..");

/** Where the packaged build and a dev build keep their state on this machine (KYO-189). */
function realUserData(): string {
  if (process.platform === "darwin") {
    return join(homedir(), "Library", "Application Support", "@kyosei", "desktop");
  }
  if (process.platform === "win32") {
    return join(process.env.APPDATA ?? join(homedir(), "AppData", "Roaming"), "@kyosei", "desktop");
  }
  return join(process.env.XDG_CONFIG_HOME ?? join(homedir(), ".config"), "@kyosei", "desktop");
}

/** The files a launch rewrites or appends to: the shell-env log (KYO-266), the
 * remembered project and the layouts -- and an earlier build's shims (KYO-111, KYO-189),
 * which a suite launch must never remove from the REAL directory. */
const WATCHED = [
  ...CLI_NAMES.map((name) => join("bin", name)),
  join("bin", ".kyo-shim.json"),
  "shell-env.log",
  "project.json",
  "layouts.json",
];

interface FileSnapshot {
  exists: boolean;
  body: string | null;
  mtimeMs: number | null;
}

function snapshot(dir: string): Record<string, FileSnapshot> {
  const out: Record<string, FileSnapshot> = {};
  // An earlier build's per-build shim directories (KYO-315) are keyed by an owner id
  // nobody here can name in advance, so they are watched as a listing: a suite launch
  // leaking into the real directory would remove them (KYO-450's cleanup).
  const cli = join(dir, "cli");
  out.cli = existsSync(cli)
    ? { exists: true, body: readdirSync(cli).sort().join("\n"), mtimeMs: null }
    : { exists: false, body: null, mtimeMs: null };
  for (const relative of WATCHED) {
    const path = join(dir, relative);
    if (!existsSync(path)) {
      out[relative] = { exists: false, body: null, mtimeMs: null };
      continue;
    }
    // The retired server's socket (KYO-459.c3) is watched by its stamp alone: a socket
    // is not a file that can be read, and opening it is not a thing this check may do.
    const stat = statSync(path);
    out[relative] = {
      exists: true,
      body: stat.isFile() ? readFileSync(path, "utf8") : `(not a file: ${stat.mode})`,
      mtimeMs: stat.mtimeMs,
    };
  }
  return out;
}

let fixture: Fixture;
let app: ElectronApplication | null = null;

test.beforeEach(async () => {
  fixture = await makeFixture(1);
});

test.afterEach(async () => {
  if (app !== null) {
    await closeReadyApp(app);
    app = null;
  }
  removeFixture(fixture);
});

test("a suite launch writes no kyo anywhere, clears only its own userData's old shims, and leaves the real directory untouched", async () => {
  const real = realUserData();
  const before = snapshot(real);
  const env = suiteEnv({
    KYOSEI_PROJECT_ROOT: fixture.root,
    KYOSEI_HOME: fixture.home,
    [TMUX_ENV]: "off",
  });
  // The seam is on every environment `suiteEnv` builds, not something this spec adds.
  const declared = env[USER_DATA_ENV] ?? "";
  expect(declared).toBe(suiteUserData());
  expect(declared.startsWith(real)).toBe(false);

  // What an earlier build left in ITS userData -- here, the suite's: the shared pane
  // shims and a per-build set. The launch below is the "first launch of this build".
  for (const name of RETIRED_SHIM_DIRS) {
    const dir =
      name === "cli" ? join(suiteUserData(), name, "0123456789ab") : join(suiteUserData(), name);
    mkdirSync(dir, { recursive: true });
    for (const cli of CLI_NAMES) writeFileSync(join(dir, cli), "#!/bin/sh\n", "utf8");
  }

  app = await _electron.launch({
    args: [APP_ROOT, ...MACOS_NO_RESTORE, windowShowArg()],
    env,
  });
  const page = await app.firstWindow();
  await page.waitForSelector('body[data-ready="true"]');
  markReadyToClose(app);
  // The app answers with the directory it actually used, which is the seam's value.
  const used = await app.evaluate(({ app: electronApp }) => electronApp.getPath("userData"));
  expect(used).toBe(suiteUserData());

  // A pane opens -- which is when the shared `bin/` shims used to be written -- and
  // nothing is written back: the pane's `kyo` is the login PATH's (KYO-450.c2).
  await openKind(page, "terminal");
  const terminal = page.locator('[data-region="tab-body"][data-tab-kind="terminal"]');
  await expect(terminal).toHaveAttribute("data-terminal", "live", { timeout: 30_000 });
  for (const name of RETIRED_SHIM_DIRS) {
    expect(existsSync(join(suiteUserData(), name)), `${name}/ is still in userData`).toBe(false);
  }

  // -- and nothing of the real directory moved: not an earlier build's shims, not the
  // log a launch appends to on its first capture, not the remembered project.
  await closeReadyApp(app);
  app = null;
  expect(snapshot(real)).toEqual(before);
});
