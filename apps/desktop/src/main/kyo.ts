// One resolver, two sources -- the only place in this app that decides how `kyo`
// is invoked (KYO-79 decision 3, KYO-111.c1).
//
// The two sources are named, not inferred:
//
//   working-tree  the CLI in *this* repository's checkout, run through the venv
//                 `make test` provisions. A change under `src/kyosei/` is live in
//                 the app the next time it invokes anything -- no rebuild, no
//                 reinstall, because there is nothing built in between. Only an
//                 UNPACKED app run from this checkout has one (`npm start`, the
//                 desktop suite); a packed app has no `src/kyosei/__main__.py`
//                 above it, so this source never answers there.
//   login-path    the `kyo` the user installed, found on their LOGIN shell's PATH
//                 -- the same PATH Terminal.app and every hook see, captured by
//                 `shellenv.ts` (KYO-229). No directory is guessed or probed beyond
//                 it: a `kyo` the hooks do not run is not one this app should run
//                 either (KYO-443).
//
// KYO-450 (under KYO-443) removed the third arrangement this file used to describe: a
// CPython runtime shipped inside a packed build, and the PATH shims that exposed it.
// The app runs no Python of its own now. One `kyo` per machine -- the uv tool -- is
// what the app, its terminal panes, Terminal.app and every hook run (KYO-443).
//
// Call sites never construct an interpreter path. They ask `resolveKyo()` for a
// resolution and hand it to `runKyo`. That is the seam; there is no second one.
//
// This module imports nothing from Electron on purpose: everything it needs about
// the world arrives as an argument, so the resolver, the argv guard and the shell
// quoting are all exercisable from a plain Node test without launching an app.

import { execFile } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { promisify } from "node:util";
import {
  KYO_SOURCES,
  type KyoCompatFacts,
  type KyoCompatState,
  type KyoSourceFacts,
  type KyoSourceName,
} from "../shared/protocol";
import { findOnPath } from "./shellenv";

const execFileAsync = promisify(execFile);

const IS_WINDOWS = process.platform === "win32";

function venvPython(root: string): string {
  return IS_WINDOWS
    ? join(root, ".venv", "Scripts", "python.exe")
    : join(root, ".venv", "bin", "python");
}

// -------------------------------------------------------------- shell quoting

const SHELL_SAFE = /^[A-Za-z0-9_@%+=:,./-]+$/;

/**
 * POSIX shell quoting for one word (KYO-89 / KYO-111.c5).
 *
 * This app's *own* repository path contains spaces on purpose, so a command line
 * assembled by concatenation is wrong here on the first try rather than on some
 * other machine later. Nothing in this file passes a string to a shell to be
 * re-split -- `runKyo` spawns an argv array with no shell at all, which is the
 * stronger guarantee -- so this exists for the places a real shell reads what we
 * wrote (a tmux session's first shell, `terminal.ts`) and any command line shown to
 * a human.
 */
export function shellQuote(value: string): string {
  if (value.length > 0 && SHELL_SAFE.test(value)) return value;
  return `'${value.replaceAll("'", "'\\''")}'`;
}

/** A whole argv rendered as a shell command line, every word quoted. */
export function shellCommandLine(parts: readonly string[]): string {
  return parts.map(shellQuote).join(" ");
}

// ------------------------------------------------------------- the argv guard

/**
 * Prose reaches the store through a `--*-file` flag or stdin, never as an
 * argument value (`kyosei/procedures.md`; KYO-91). The CLI enforces its half; this
 * is the app's half, at the one place arguments are assembled, so a future call
 * site cannot route around it by building its own subprocess.
 *
 * A newline in an argument is the mechanical signal that body text is being
 * passed where a flag value belongs -- there is no multi-line flag-value form in
 * the tool at all -- so it is refused by name, with the route that does work.
 */
export function assertNoProseArguments(args: readonly string[]): void {
  for (const arg of args) {
    if (!/[\n\r]/.test(arg)) continue;
    const head = JSON.stringify(arg.slice(0, 40));
    throw new Error(
      `kyo: refusing to pass multi-line prose as an argument value (${head}...). ` +
        "Body text enters the store only through a `--<flag>-file` flag or stdin " +
        "(kyosei/procedures.md, KYO-91). Write it with `proseFileArgs()` and pass the " +
        "resulting `--<flag>-file <path>` pair instead.",
    );
  }
}

/**
 * The `kyo` subcommands this app never runs, by any route (KYO-439).
 *
 * `promote` turns an idea into a record, and a record's structure -- its kind, title,
 * criteria and touches -- is the controller's to author. The desktop app has no basis
 * on which to write a record, so it never promotes an idea. KYO-234 once gave the idea
 * tab a dialog that ran it; KYO-439 removed that dialog end to end, and this refusal is
 * what keeps it removed: it sits in `runKyo`, the one subprocess this app starts for the
 * CLI, so a future call site cannot reach the command by building its own argv. The
 * scratchpad's "Promote to idea" (KYO-252) writes an idea FILE and never reaches here.
 */
export const APP_NEVER_RUNS: readonly string[] = ["promote"];

/** Refuse, by name, a subcommand this app never runs (`APP_NEVER_RUNS`). */
export function assertAppMayRun(args: readonly string[]): void {
  const verb = args.find((word) => !word.startsWith("-")) ?? "";
  if (!APP_NEVER_RUNS.includes(verb)) return;
  throw new Error(
    `kyo: this app never runs \`kyo ${verb}\` (KYO-439). Promoting an idea into a record ` +
      "is the orchestrator's act: hand the idea file's path to the orchestrator session, which " +
      "runs `kyo promote` with the kind, criteria and touches it decides.",
  );
}

/**
 * Write prose to a file and return the `["--<flag>-file", path]` pair for it.
 *
 * The one supported way for this app to give `kyo` a body. Returned as an argv
 * pair rather than a string so it can only ever be spliced into an argv array --
 * there is no form of this that produces a shell fragment.
 */
export function proseFileArgs(flag: string, text: string, dir: string): [string, string] {
  if (!/^[a-z][a-z-]*$/.test(flag)) {
    throw new Error(`kyo: ${JSON.stringify(flag)} is not a flag name (lower-case and dashes)`);
  }
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${flag}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.txt`);
  writeFileSync(path, text.endsWith("\n") ? text : `${text}\n`, "utf8");
  return [`--${flag}-file`, path];
}

// ---------------------------------------------------------------- the sources

export interface KyoSource {
  name: KyoSourceName;
  label: string;
  /** The interpreter, or null when this source cannot run here. */
  program: string | null;
  /** What goes before the caller's own arguments. */
  baseArgs: string[];
  /** The refusal, naming what is missing, or null when the source is available. */
  unavailable: string | null;
}

export interface ResolveInput {
  /**
   * Where to start looking for this repository's checkout: the directory the app
   * was loaded from. The walk upwards ends at the first directory holding
   * `src/kyosei/__main__.py`.
   */
  startDir: string;
  /**
   * The login shell's PATH, as `shellenv.ts` captured it -- never `process.env.PATH`,
   * which from a Finder launch is launchd's four-directory minimum no terminal on the
   * machine has (KYO-229). Null when the capture failed, with `pathProblem` saying why.
   */
  path: string | null;
  /** Why there is no login PATH to search, when `path` is null. */
  pathProblem?: string | null;
}

export interface KyoResolution {
  source: KyoSource;
  candidates: KyoSource[];
  /** The repository checkout the working-tree source was found in, if any. */
  cliRoot: string | null;
}

/** Walk up from `startDir` to the checkout that holds the CLI's source. */
export function findCliRoot(startDir: string): string | null {
  let dir = startDir;
  for (;;) {
    if (existsSync(join(dir, "src", "kyosei", "__main__.py"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

function workingTreeSource(startDir: string, cliRoot: string | null): KyoSource {
  const base: Omit<KyoSource, "program" | "baseArgs" | "unavailable"> = {
    name: "working-tree",
    label: "working tree",
  };
  if (cliRoot === null) {
    return {
      ...base,
      program: null,
      baseArgs: [],
      unavailable:
        `no src/kyosei/__main__.py at or above ${startDir}, so there is no working-tree ` +
        "CLI here to run",
    };
  }
  const python = venvPython(cliRoot);
  if (!existsSync(python)) {
    return {
      ...base,
      program: null,
      baseArgs: [],
      unavailable:
        `${cliRoot} holds the CLI's source but has no interpreter at ${python}; run ` +
        "`make test` there to provision it (uv, Python 3.12)",
    };
  }
  return { ...base, program: python, baseArgs: ["-m", "kyosei"], unavailable: null };
}

/** The command a reader with no `kyo` runs, named once for every refusal that needs it. */
export const INSTALL_KYO_COMMAND = "uv tool install kyosei";

/**
 * The `kyo` the user installed, on their login shell's PATH (KYO-443, KYO-450).
 *
 * Found the way a shell's `command -v` finds it -- the first directory holding an
 * executable file of that name -- by the one helper the headless server already uses
 * for `tmux` and `claude` (`findOnPath`). Nothing else is searched: a `kyo` in some
 * directory the login shell does not put on PATH is one the hooks and Terminal.app do
 * not run, and this app answering with it would be the "two builds on one machine"
 * arrangement KYO-443 set out to end.
 *
 * Whether the `kyo` found is one this app reads -- its version against the range the
 * app accepts -- is KYO-449's check, not this function's; this only says where it is.
 */
function loginPathSource(path: string | null, pathProblem: string | null): KyoSource {
  const base: Omit<KyoSource, "program" | "baseArgs" | "unavailable"> = {
    name: "login-path",
    label: "kyo on your login PATH",
  };
  const install =
    `Install it with \`${INSTALL_KYO_COMMAND}\` (or \`pipx install kyosei\`) and relaunch ` +
    "the app.";
  if (path === null) {
    return {
      ...base,
      program: null,
      baseArgs: [],
      unavailable:
        "this app could not read your login shell's PATH" +
        (pathProblem === null || pathProblem === "" ? "" : ` (${pathProblem})`) +
        ", so it cannot find the `kyo` your terminals run. " +
        install,
    };
  }
  const found = findOnPath(IS_WINDOWS ? "kyo.exe" : "kyo", path);
  if (found === null) {
    return {
      ...base,
      program: null,
      baseArgs: [],
      unavailable: `there is no \`kyo\` on the PATH your login shell sets up. ${install}`,
    };
  }
  return { ...base, program: found, baseArgs: [], unavailable: null };
}

// ------------------------------------------ the kyo releases this app reads (KYO-449)

/**
 * The kyo releases this app reads, as a PEP 440 specifier (KYO-443). Declared here,
 * once, beside the resolver, because it is a fact about the `--json` shapes this app's
 * code consumes -- which change only with the code in this tree.
 *
 * Releases follow SemVer read the 0.x way, so in `0.y.z` the `y` is breaking. A
 * commit that changes a `--json` shape (or kyo serve's `/v1` envelope, KYO-452) this
 * app reads, incompatibly, bumps `y` in `pyproject.toml` AND this range, in the same
 * commit. `tests/seams.spec.ts` fails when this range does not admit `pyproject.toml`'s
 * own version, so the app in this tree always reads the CLI in this tree.
 */
export const KYO_ACCEPTS = ">=0.1.0,<0.2.0";

/** A version's comparable parts: its release numbers, and where it sits beside them. */
interface ParsedVersion {
  release: number[];
  /** -1 a pre-release (a/b/rc/dev) of `release`, 0 the release, 1 a post-release. */
  phase: -1 | 0 | 1;
}

/**
 * The part of PEP 440 a kyo release uses: `N(.N)*`, optionally a pre-, dev- or
 * post-release suffix, optionally a `+local` label (ignored). Null when it is not one.
 */
export function parseKyoVersion(text: string): ParsedVersion | null {
  const match =
    /^v?(\d+(?:\.\d+)*)((?:[-_.]?(?:a|b|c|rc|alpha|beta|pre|preview|dev|post)\d*)*)(?:\+[a-z0-9.]+)?$/i.exec(
      text.trim(),
    );
  if (match === null) return null;
  const release = (match[1] ?? "").split(".").map((part) => Number(part));
  const suffix = (match[2] ?? "").toLowerCase();
  const phase: -1 | 0 | 1 =
    suffix === "" ? 0 : /^[-_.]?post/.test(suffix) && !/dev/.test(suffix) ? 1 : -1;
  return { release, phase };
}

/** Negative, zero or positive, as `a` is below, equal to or above `b`. */
export function compareKyoVersions(a: ParsedVersion, b: ParsedVersion): number {
  const length = Math.max(a.release.length, b.release.length);
  for (let index = 0; index < length; index += 1) {
    const left = a.release[index] ?? 0;
    const right = b.release[index] ?? 0;
    if (left !== right) return left - right;
  }
  return a.phase - b.phase;
}

type Clause = { op: ">=" | ">" | "<=" | "<" | "==" | "!="; version: ParsedVersion };

/** A specifier's clauses; throws on one this function cannot read, naming it. */
export function parseKyoSpecifier(spec: string): Clause[] {
  return spec.split(",").map((raw) => {
    const match = /^\s*(>=|<=|==|!=|>|<)\s*([^\s]+)\s*$/.exec(raw);
    const version = match === null ? null : parseKyoVersion(match[2] ?? "");
    if (match === null || version === null) {
      throw new Error(`kyo: ${JSON.stringify(raw.trim())} is not a clause this app reads`);
    }
    return { op: match[1] as Clause["op"], version };
  });
}

/**
 * Where `version` stands against `accepts`: inside it, below it (a lower-bound clause
 * fails), at or above its upper bound (an upper-bound clause fails), or not a version.
 */
export function judgeKyoVersion(
  version: string,
  accepts: string = KYO_ACCEPTS,
): "accepted" | "too-old" | "too-new" | "unparsable" {
  const parsed = parseKyoVersion(version);
  if (parsed === null) return "unparsable";
  let old = false;
  let fresh = false;
  for (const clause of parseKyoSpecifier(accepts)) {
    const order = compareKyoVersions(parsed, clause.version);
    // PEP 440: `<V` admits no pre-release of V, and `>V` no post-release of V, unless V
    // is one itself -- so `0.2.0.dev1` is too new for `<0.2.0`.
    const sameRelease =
      compareKyoVersions({ ...parsed, phase: 0 }, { ...clause.version, phase: 0 }) === 0;
    const holds =
      clause.op === ">="
        ? order >= 0
        : clause.op === ">"
          ? order > 0 && !(sameRelease && clause.version.phase === 0)
          : clause.op === "<="
            ? order <= 0
            : clause.op === "<"
              ? order < 0 && !(sameRelease && clause.version.phase === 0)
              : clause.op === "=="
                ? order === 0
                : order !== 0;
    if (holds) continue;
    if (clause.op === ">=" || clause.op === ">" || (clause.op === "==" && order < 0)) old = true;
    else fresh = true;
  }
  if (old) return "too-old";
  if (fresh) return "too-new";
  return "accepted";
}

/**
 * What `kyo version`'s "installed as" line says for each kind -- the CLI's own
 * `buildinfo.INSTALL_KIND_LABELS`, mirrored because `version --json` carries the kind
 * and not its words. `tests/seams.spec.ts` compares the two.
 */
export const INSTALL_KIND_LABELS: Readonly<Record<string, string>> = {
  "uv-tool": "a uv tool install",
  "working-tree": "a working tree",
  "cannot-tell": "an install this copy cannot place from its own path",
};

/** What moves a uv-tool copy that is a published release (KYO-446.c3). */
export const UV_TOOL_UPGRADE_COMMAND = "uv tool upgrade kyosei";

/**
 * The CLI's own `buildinfo.upgrade_route(kind, release)` sentence (KYO-317), mirrored for
 * the same reason and compared by the same check. A kind this build does not know reads
 * as `cannot-tell`, as the CLI's does.
 */
export function upgradeRoute(kind: string | null, release: boolean | null): string {
  if (kind === "working-tree") {
    return (
      "pull or check out a newer commit in this checkout -- it runs the " +
      "checkout live, so there is nothing to reinstall"
    );
  }
  if (kind === "uv-tool") {
    if (release === true) {
      return `\`${UV_TOOL_UPGRADE_COMMAND}\` -- this copy is a published release`;
    }
    return (
      "re-snapshot it from a Kyosei checkout: `make install-tool` there (it runs " +
      "`uv tool install --force .` with VIRTUAL_ENV unset) -- this copy is an " +
      `unreleased snapshot, so \`${UV_TOOL_UPGRADE_COMMAND}\` would move it to the ` +
      "latest release rather than newer source"
    );
  }
  return (
    "install it as the one supported kind, since this copy's install cannot be told " +
    "apart from its own path: `uv tool install kyosei` for the published release, or " +
    "`make install-tool` (`uv tool install --force .` with VIRTUAL_ENV unset) from " +
    "a Kyosei checkout for an unreleased snapshot, and remove whatever put this copy " +
    "on PATH so the one you install is the one that answers"
  );
}

/** A kyo, as the words of its state name it. */
export type KyoSubject =
  /** The kyo this app runs on this machine: `path` is its command line. */
  | { kind: "path"; path: string; shell: string }
  /** A kyo serve: `address` as a person types it; `local` is this machine's. */
  | { kind: "serve"; address: string; local: boolean };

/** What a kyo answered to `version --json` (or kyo serve's `/v1/version`, its `kyosei`). */
export interface KyoVersionAnswer {
  /** The payload, when one parsed. */
  data: Record<string, unknown> | null;
  /** Why it is not Kyosei's answer, when it did not parse: exit code and stderr's first line. */
  detail: string;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/**
 * Judge one kyo and say so in KYO-440 c2's words (KYO-449.c3), naming what was judged
 * (KYO-449.c5: a server by its address). `answer` null is "no kyo" -- for a path only;
 * a kyo serve that did not answer is `ServeFacts.running`'s state, not this one.
 *
 * The PATH wording is KYO-440's, apart from its Markdown. A kyo serve's
 * follows KYO-452: the same states, with "the kyo on your PATH" read as "the kyo
 * serve at <address>", and a remedy that also restarts the server -- an upgrade on disk
 * does not change what a running kyo serve answers.
 */
export function kyoCompatFrom(
  subject: KyoSubject,
  answer: KyoVersionAnswer | null,
  accepts: string = KYO_ACCEPTS,
): KyoCompatFacts {
  const where = subject.kind === "path" ? subject.path : subject.address;
  const words = (
    state: KyoCompatState,
    version: string | null,
    parts: {
      title: string;
      says?: string;
      command?: string | null;
      route?: string | null;
      after?: string;
    },
  ): KyoCompatFacts => ({
    state,
    where,
    version,
    accepts,
    title: parts.title,
    says: parts.says ?? "",
    command: parts.command ?? null,
    route: parts.route ?? null,
    after: parts.after ?? "",
  });
  const again = "click Check again";
  if (answer === null) {
    const shell = subject.kind === "path" ? subject.shell : "";
    return words("no-kyo", null, {
      title: "Kyosei can't find the `kyo` command.",
      says:
        "The app reads your projects through the Kyosei command line tool, and there is no " +
        `\`kyo\` on the PATH your login shell (\`${shell}\`) sets up. Install it:`,
      command: "uv tool install kyosei",
      after: `(or \`pipx install kyosei\`), then ${again}. This app reads kyo \`${accepts}\`.`,
    });
  }
  const data = answer.data;
  const distribution = text(data?.distribution);
  const version = text(data?.version);
  // Kyosei's answer: a version payload, from the `kyosei` distribution. A build older
  // than the `distribution` field that still answers as `kyo version` is judged by its
  // version -- it is Kyosei's, and "too old" is the true thing to tell its reader.
  const kyosei =
    data !== null &&
    version !== null &&
    (distribution === "kyosei" || (distribution === null && data.command === "version"));
  const standing = kyosei && version !== null ? judgeKyoVersion(version, accepts) : "unparsable";
  const named =
    subject.kind === "path" ? `\`${subject.path}\`` : `The kyo serve at \`${subject.address}\``;
  if (standing === "unparsable") {
    if (subject.kind === "path") {
      return words("not-kyosei", version, {
        title: "The `kyo` on your PATH is not Kyosei's.",
        says:
          `\`${subject.path}\` did not answer \`kyo version --json\` the way Kyosei does ` +
          `(\`${answer.detail}\`). The PyPI package named \`kyo\` is an unrelated project ` +
          "that installs a command with this name. Remove whichever tool put it there, then " +
          `\`uv tool install kyosei\`, and ${again}.`,
      });
    }
    return words("not-kyosei", version, {
      title: `The kyo serve at ${subject.address} is not Kyosei's.`,
      says:
        `\`${subject.address}\` did not answer \`/v1/version\` the way Kyosei's kyo serve does ` +
        `(\`${answer.detail}\`). Something else is answering at that address. Stop whatever ` +
        "holds it, or point this app at the address kyo serve listens on, then start kyo " +
        `serve there and ${again}.`,
    });
  }
  const shown = version ?? "?";
  if (standing === "too-old") {
    const kind = text(data?.kind);
    const release = typeof data?.release === "boolean" ? data.release : null;
    const label = INSTALL_KIND_LABELS[kind ?? ""] ?? INSTALL_KIND_LABELS["cannot-tell"] ?? "";
    const build = text(data?.commit_short) ?? "unknown";
    // The one upgrade command, where it is the route: a published uv tool. Every other
    // copy gets the CLI's own sentence for its kind (KYO-317), since `uv tool upgrade`
    // would not move it -- or, for an unreleased snapshot, would move it backwards
    // (KYO-446.c3).
    const command = kind === "uv-tool" && release === true ? UV_TOOL_UPGRADE_COMMAND : null;
    const then =
      subject.kind === "serve"
        ? "then restart it (`kyo serve --restart`, or stop the `kyo serve` in its terminal " +
          `and start it again), and ${again}`
        : `then ${again}`;
    return words("too-old", version, {
      title: "This `kyo` is too old for this app.",
      says:
        `${named} is kyosei \`${shown}\` (\`${label}\`, build \`${build}\`). This app reads kyo ` +
        `\`${accepts}\`. Upgrade it${subject.kind === "serve" && !subject.local ? " on that machine" : ""}:`,
      command,
      route: command === null ? upgradeRoute(kind, release) : null,
      after:
        `${then}. Upgrading \`kyo\` can move it to ` +
        "a newer `schema_version` than your projects' stores. Each project's own Claude Code " +
        "session migrates its store (`kyo rebuild --force`, then `kyo export --refresh`). This " +
        "app never does.",
    });
  }
  if (standing === "too-new") {
    return words("too-new", version, {
      title: "This `kyo` is newer than this app understands.",
      says:
        `${named} is kyosei \`${shown}\`. This app reads kyo \`${accepts}\`. Update the ` +
        "Kyosei app to a release that reads it. Until then the records views stay off. The terminal panes, and every " +
        "Claude session in them, keep working, because they run `kyo` directly and do not " +
        "need this app to read its output.",
    });
  }
  return words("accepted", version, {
    title: `${subject.kind === "path" ? `\`${subject.path}\`` : `The kyo serve at ${subject.address}`} is kyosei ${shown}, which this app reads (kyo ${accepts}).`,
  });
}

/** The first line of a failed run, as the "not Kyosei's" state quotes it. */
export function failureDetail(code: number, stderr: string): string {
  const first =
    stderr
      .split("\n")
      .find((line) => line.trim() !== "")
      ?.trim() ?? "";
  return `exit ${code}${first === "" ? "" : `: ${first.slice(0, 200)}`}`;
}

/**
 * The kyo this app runs, judged (KYO-449.c1): `kyo version --json` of the resolution --
 * the login PATH's kyo in a packed app, the checkout's in an unpacked one (KYO-79
 * decision 3, stated in `where`). Never throws: a run that fails is "not Kyosei's".
 */
export async function checkKyo(
  resolution: KyoResolution,
  options: { shell: string; cwd: string; timeoutMs?: number },
): Promise<KyoCompatFacts> {
  if (resolution.source.program === null) {
    return kyoCompatFrom({ kind: "path", path: "kyo", shell: options.shell }, null);
  }
  const subject: KyoSubject = { kind: "path", path: commandLine(resolution), shell: options.shell };
  try {
    const result = await runKyoJson(resolution, ["version"], {
      cwd: options.cwd,
      timeoutMs: options.timeoutMs ?? 20_000,
    });
    const ok = result.code === 0 && result.data !== null;
    return kyoCompatFrom(subject, {
      data: ok ? result.data : null,
      detail: failureDetail(result.code, result.stderr),
    });
  } catch (error) {
    return kyoCompatFrom(subject, {
      data: null,
      detail: (error as Error).message.split("\n")[0] ?? "",
    });
  }
}

/**
 * Decide, once, how this app invokes `kyo`. Preference order is fixed and stated:
 * a checkout's working tree wins, because the whole point of decision 3 is that a
 * developer's edit under `src/kyosei/` is live without a rebuild; a packed build has
 * no working tree and falls through to the `kyo` on the login PATH.
 */
export function resolveKyo(input: ResolveInput): KyoResolution {
  const cliRoot = findCliRoot(input.startDir);
  const candidates = [
    workingTreeSource(input.startDir, cliRoot),
    loginPathSource(input.path, input.pathProblem ?? null),
  ];
  const chosen = candidates.find((source) => source.unavailable === null) ?? candidates[0];
  // `candidates` is a two-element literal, so the fallback is total; the assertion
  // is for `noUncheckedIndexedAccess`, not for a case that can occur.
  if (chosen === undefined) throw new Error("kyo: the resolver has no candidate sources");
  return { source: chosen, candidates, cliRoot };
}

/**
 * Why no source could be used, naming *every* one that was tried.
 *
 * Reporting only the first candidate's refusal would tell a packed build's user
 * about a missing working tree they were never going to have, and say nothing
 * about the `kyo` that is actually missing from their PATH. Both refusals, always.
 */
export function refusalFor(resolution: KyoResolution): string {
  return resolution.candidates
    .filter((source) => source.unavailable !== null)
    .map((source) => `${source.label}: ${source.unavailable}`)
    .join("\n");
}

/** The resolution as the UI states it -- which source is in use, and why. */
export function describeResolution(resolution: KyoResolution): KyoSourceFacts {
  const available = resolution.source.unavailable === null;
  return {
    chosen: available ? resolution.source.name : null,
    label: available ? resolution.source.label : "no runtime",
    detail: available ? commandLine(resolution) : refusalFor(resolution),
    candidates: resolution.candidates.map((source) => ({
      name: source.name,
      label: source.label,
      available: source.unavailable === null,
      detail:
        source.unavailable ??
        shellCommandLine([source.program ?? "", ...source.baseArgs, "<command>"]),
    })),
  };
}

/** The chosen source's invocation as a shell command line, every word quoted. */
export function commandLine(resolution: KyoResolution, args: readonly string[] = []): string {
  const { program, baseArgs } = resolution.source;
  if (program === null) return "";
  return shellCommandLine([program, ...baseArgs, ...args]);
}

/** Sanity: the resolver's source names are exactly the protocol's. */
export const RESOLVER_SOURCE_NAMES: readonly KyoSourceName[] = KYO_SOURCES;

// ------------------------------------------------------------- the invocation

export interface KyoResult {
  code: number;
  stdout: string;
  stderr: string;
}

export interface RunOptions {
  /** The directory `kyo` runs in; the CLI resolves the project by walking up. */
  cwd: string;
  timeoutMs?: number;
}

/**
 * Run `kyo` through the resolved source. The *only* subprocess this app starts
 * for the CLI.
 *
 * `execFile` with an argv array and no shell: nothing is re-split, so a project
 * path with spaces (this repository's own, KYO-89) needs no quoting and cannot be
 * corrupted by any. `shell` is never enabled here, and there is no overload that
 * takes a command string.
 *
 * A non-zero exit is *returned*, not thrown. Several `kyo` commands exit 1 or 2
 * with a fully-formed `--json` payload on stdout -- a contract violation carries
 * its data -- and a caller that treated the exit code as "no answer" would throw
 * away the answer.
 */
export async function runKyo(
  resolution: KyoResolution,
  args: readonly string[],
  options: RunOptions,
): Promise<KyoResult> {
  // KYO-203.c3: the one place a check can count real invocations of this, the one
  // subprocess this app starts for the CLI, without guessing at a call site above
  // it (the gate filter, a client-side refinement over rows the engine already
  // returned, must never reach here). Read through `globalThis` -- shared with an
  // Electron test's own `app.evaluate()`, running in this same process -- rather
  // than exposed over IPC: there is no consumer of an invocation count and this
  // app grows no surface for one.
  //
  // KYO-184 adds the per-verb tally beside the total, and the reason is that the
  // total alone is not measurable in a running app: a renderer polls `kyo list` on a
  // timer, so a check that asserted "this read cost exactly N invocations" would be
  // asserting the absence of a tick it cannot control -- an intermittent check, which
  // is worse than none. The tally lets a check name the command it is about ("this
  // handler runs `where` once and `check-branch` never"), which is both the sharper
  // claim and the one nothing else in the app can perturb.
  const globals = globalThis as {
    __kyoInvocations?: number;
    __kyoCommands?: Record<string, number>;
  };
  globals.__kyoInvocations = (globals.__kyoInvocations ?? 0) + 1;
  // The subcommand: the first word that is not a flag. `["docs", "check"]` tallies as
  // `docs`, which is the granularity every consumer of this wants.
  const verb = args.find((word) => !word.startsWith("-")) ?? "";
  const tally = globals.__kyoCommands ?? {};
  tally[verb] = (tally[verb] ?? 0) + 1;
  globals.__kyoCommands = tally;
  const { program, baseArgs, unavailable } = resolution.source;
  if (program === null) throw new Error(unavailable ?? "kyo: no runnable source");
  assertAppMayRun(args);
  assertNoProseArguments(args);
  try {
    const { stdout, stderr } = await execFileAsync(program, [...baseArgs, ...args], {
      cwd: options.cwd,
      timeout: options.timeoutMs ?? 20_000,
      maxBuffer: 32 * 1024 * 1024,
      windowsHide: true,
    });
    return { code: 0, stdout, stderr };
  } catch (error) {
    const failure = error as { code?: unknown; stdout?: string; stderr?: string; message?: string };
    if (typeof failure.stdout === "string" || typeof failure.stderr === "string") {
      return {
        code: typeof failure.code === "number" ? failure.code : 1,
        stdout: failure.stdout ?? "",
        stderr: failure.stderr ?? "",
      };
    }
    throw error;
  }
}

/** Run a `--json` command and parse its payload, whatever the exit code was. */
export async function runKyoJson(
  resolution: KyoResolution,
  args: readonly string[],
  options: RunOptions,
): Promise<{ code: number; data: Record<string, unknown> | null; stderr: string }> {
  const result = await runKyo(resolution, [...args, "--json"], options);
  const trimmed = result.stdout.trim();
  if (trimmed === "") return { code: result.code, data: null, stderr: result.stderr };
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
      return { code: result.code, data: parsed as Record<string, unknown>, stderr: result.stderr };
    }
    return { code: result.code, data: null, stderr: result.stderr };
  } catch {
    return { code: result.code, data: null, stderr: result.stderr };
  }
}
