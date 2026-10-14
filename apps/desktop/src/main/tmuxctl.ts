// The one place this app runs a tmux command and reads its answer (KYO-283).
//
// KYO-293 QUALIFIES THAT FIRST SENTENCE, so it cannot go quietly false. There is now a
// second way a tmux process starts here, and it is deliberately not one of these: the
// pane's CLIENT is a pty whose process is the tmux binary, spawned by `terminal.ts`
// through node-pty with `clientAttachArgs`. It is a terminal for a human to look at
// rather than a command whose output this file parses -- which is why it belongs to the
// pty machinery and not to `runTmux`. The rule both obey is the same one: `tmuxArgs`
// builds the argv and `seams.spec.ts` fails on any tmux subcommand written outside it.
//
// WHY A SECOND SPAWNING SEAM EXISTS AT ALL. Until this record the pane's own shell did
// everything tmux-shaped: `sessionScript` created the session, set its environment and
// exec'd a client, all typed into a pty, because `kyo.ts` was the only file allowed to
// start a process and widening that guard for the pane's setup would have bought
// nothing the script could not do. What the script could not do is the thing KYO-283
// asks for: ANSWER A QUESTION. "Which sessions are alive on this server", "which
// session is this pane's client in", "detach this client and leave the session
// running" are all reads and acts the app has to perform and read the result of, and
// a script typed into a pane produces output on a screen, not a value.
//
// So `seams.spec.ts`'s allowlist gains one entry, with its reason, on that file's own
// terms: an entry added without a reason is an entry nobody argued for. It is not a
// second route to the CLI -- nothing here names `kyo`, an interpreter, or the
// resolver, and `seams.spec.ts` checks that too.
//
// THE SOCKET IS NAMED ON EVERY COMMAND. Not for tidiness: which server a bare `tmux`
// reaches is decided by `TMUX_TMPDIR` in whatever environment the command inherits,
// and this app runs inside one environment while its panes run in another. A scratch
// server command from inside an app pane once took a user's real tmux sessions down
// for exactly that reason. `tmuxArgs` puts `-S <socket>` in front of every
// argv this app builds and `seams.spec.ts` fails on a tmux argv that skips it, so the
// property is held by a check rather than by everyone remembering.
//
// NOTHING HERE ENDS A SESSION, AND SINCE KYO-474 NOTHING HERE MAKES ONE. The subcommands
// this app may never issue are listed in `terminal.ts` (`TMUX_SUBCOMMANDS_REFUSED`) and
// scanned for in every source file, this one included. `detach-client` is the only
// thing below that removes anything, and what it removes is a client -- which is the
// whole of KYO-283.c4. The server these commands reach is the controller's (`tmux -L
// kyosei`, spelled as a socket path by `controllerSocketPath`), whose sessions `kyo
// controller start` makes; the app used to create and prepare a session of its own on
// the default server, and no longer touches that server at all.

import { execFile } from "node:child_process";
import {
  CLIENT_LIST_FORMAT,
  parseClients,
  parseSessions,
  SESSION_LIST_FORMAT,
  type TmuxClient,
  type TmuxSession,
  tmuxArgs,
} from "./tmuxenv";

/**
 * How long one tmux command is given before it is abandoned.
 *
 * Bounded because this runs on the main process's event loop and the pill row is
 * polled: a tmux whose server is wedged would otherwise pile up one unfinished child
 * per tick, which is the shape KYO-264 measured as the spinning cursor. Generous
 * enough that a cold server start is not mistaken for a wedge.
 */
export const TMUX_TIMEOUT_MS = 5_000;

export interface TmuxRun {
  ok: boolean;
  stdout: string;
  /** tmux's own message when it refused, or the spawn's. Empty when it worked. */
  problem: string;
}

/**
 * Run one tmux command, asynchronously, and never throw.
 *
 * Asynchronous because the synchronous form held the main process -- every window's
 * menus, clicks and IPC -- for the length of the call, which is what KYO-264 found
 * behind a reported freeze of the whole app. Never throwing because every caller
 * below has a sentence to say about a failure and none of them has anything to do
 * with an exception: a tmux that is not there and a session that has just ended are
 * ordinary answers, not faults.
 */
export function runTmux(
  binary: string,
  socket: string,
  args: readonly string[],
  env: Record<string, string | undefined>,
  timeoutMs: number = TMUX_TIMEOUT_MS,
): Promise<TmuxRun> {
  return runTmuxArgv(binary, tmuxArgs(socket, args), env, timeoutMs);
}

/**
 * The same, for an argv that already carries its socket.
 *
 * The one caller is `applyPrepare`, whose calls come out of `sessionPrepareCalls` as
 * whole argvs -- built by `tmuxArgs` there, so the socket is in them, and adding it
 * again here would name it twice. Two entry points, one funnel for the `-S`.
 */
export function runTmuxArgv(
  binary: string,
  argv: readonly string[],
  env: Record<string, string | undefined>,
  timeoutMs: number = TMUX_TIMEOUT_MS,
): Promise<TmuxRun> {
  return new Promise((resolve) => {
    execFile(
      binary,
      [...argv],
      { env, timeout: timeoutMs, encoding: "utf8" },
      (error, stdout, stderr) => {
        if (error) {
          const said = `${stderr}`.trim();
          resolve({ ok: false, stdout: `${stdout}`, problem: said === "" ? error.message : said });
          return;
        }
        resolve({ ok: true, stdout: `${stdout}`, problem: "" });
      },
    );
  });
}

/** tmux's own words for "there is no server on this socket": an answer, not a fault. */
export const NO_SERVER =
  /no server running|error connecting to .*\((no such file or directory|connection refused)\)/i;

/**
 * The sessions alive on this socket right now, in tmux's own order.
 *
 * An empty list for a server that is not running, which is not an error: a machine
 * where nobody has started tmux yet has no sessions, and that is the honest answer to
 * "what is alive". The caller distinguishes the two by `problem`.
 */
export async function aliveSessions(
  binary: string,
  socket: string,
  env: Record<string, string | undefined>,
): Promise<{ sessions: TmuxSession[]; problem: string | null }> {
  const run = await runTmux(binary, socket, ["list-sessions", "-F", SESSION_LIST_FORMAT], env);
  if (!run.ok) {
    // "no server running on ..." is tmux saying there are no sessions, in its own
    // words. Reported as an empty list rather than as a problem, because a pill row
    // that said "error" on a machine with no tmux session open would be wrong every
    // morning.
    // A `-S` socket that does not exist yet says it differently -- "error connecting to
    // <path> (No such file or directory)" -- and means the same: the CLI reads both as
    // "no server" (`_NO_SERVER` in `cmd_controller.py`), and so does this.
    if (NO_SERVER.test(run.problem)) return { sessions: [], problem: null };
    return { sessions: [], problem: run.problem };
  }
  return { sessions: parseSessions(run.stdout), problem: null };
}

/** Which tty is in which session, so the pane can find its own client. */
export async function attachedClients(
  binary: string,
  socket: string,
  env: Record<string, string | undefined>,
): Promise<TmuxClient[]> {
  const run = await runTmux(binary, socket, ["list-clients", "-F", CLIENT_LIST_FORMAT], env);
  return run.ok ? parseClients(run.stdout) : [];
}

/**
 * Detach ONE client, named by its tty. Never a session, and never by session.
 *
 * `detach-client -t <tty>` and not `-s <session>`: the second detaches every client
 * of that session, which on the shared server this whole design rests on is the
 * user's own terminal and whatever else has joined. The tty is this pane's pty,
 * which the app knows and nobody else is.
 */
export async function detachClient(
  binary: string,
  socket: string,
  env: Record<string, string | undefined>,
  tty: string,
): Promise<TmuxRun> {
  return await runTmux(binary, socket, ["detach-client", "-t", tty], env);
}
