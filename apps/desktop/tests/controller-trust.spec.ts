// KYO-567: the claude tab's word for a session waiting on Claude Code's workspace-trust
// prompt, decided without a window. `kyo controller start|list --json` say
// `waiting_for_trust` and `trust_folder`; `ssh.ts` and `controller.ts` carry that onto
// `ControllerStatus.trustFolder`; `controller-trust.ts` decides what the row says and
// offers. Every check here is on those pure functions, with the CLI's own JSON shapes.
//
// What a person sees in the window -- the sentence, and the Open terminal button raising
// the terminal tab on the session -- is a human-observed check after a re-pack: the
// suite launches the app hidden.

import { expect, test } from "@playwright/test";
import { parseControllerList, startControllerOn, statusFromListing } from "../src/main/controller";
import { parseStarted, type Runner, sshCommandHost, waitingForTrust } from "../src/main/ssh";
import { TRUST_TAB, trustOffer, trustWhy, WAITING_FOR_TRUST } from "../src/shared/controller-trust";

/** One `kyo controller list --json` project row, as the CLI writes it. */
function row(over: Record<string, unknown>): Record<string, unknown> {
  return {
    project: "demo",
    prefix: "DEMO",
    root: "/Users/me/demo",
    registered: true,
    session: "DEMO",
    claude_alive: true,
    pid: 42,
    command: "claude",
    started_with: "claude --remote-control DEMO",
    waiting_for_trust: false,
    trust_folder: null,
    ...over,
  };
}

function listing(over: Record<string, unknown>) {
  const parsed = parseControllerList({
    ok: true,
    socket: "tmux -L kyosei",
    server_running: true,
    projects: [row(over)],
  });
  if (parsed === null) throw new Error("the listing did not parse");
  return parsed;
}

test("a listed session showing the trust prompt is waiting for trust, names the folder, and offers the terminal tab (KYO-567.c2, c3)", () => {
  const status = statusFromListing(
    listing({ waiting_for_trust: true, trust_folder: "/Users/me/demo" }),
    "/Users/me/demo",
    "devbox",
  );
  expect(status).toMatchObject({ running: true, canStart: false, trustFolder: "/Users/me/demo" });
  expect(status.why).toBe(trustWhy("DEMO", "devbox", "/Users/me/demo"));
  expect(status.why).toContain(WAITING_FOR_TRUST);
  const offer = trustOffer(status);
  if (offer === null) throw new Error("no offer for a session waiting for trust");
  expect(offer.text).toContain("Claude Code is asking whether to trust /Users/me/demo");
  expect(offer.text).toContain("terminal tab");
  expect(offer.action).toBe("Open terminal");
  expect(offer.tab).toEqual({ kind: "terminal", arg: "" });
  expect(offer.tab).toBe(TRUST_TAB);
  // The offer opens a tab and answers nothing: no label promises to trust anything.
  expect(`${offer.action} ${offer.tip}`.toLowerCase()).not.toMatch(/\btrust and\b|\byes\b/);
});

test("a running session not showing the prompt, a session that exited, and an older kyo that never says, offer nothing (KYO-567.c3)", () => {
  const plain = statusFromListing(listing({}), "/Users/me/demo", "devbox");
  expect(plain).toMatchObject({ running: true, trustFolder: null });
  expect(trustOffer(plain)).toBeNull();
  // A kyo older than KYO-567 has no such keys at all.
  const older = statusFromListing(
    listing({ waiting_for_trust: undefined, trust_folder: undefined }),
    "/Users/me/demo",
    "devbox",
  );
  expect(older.trustFolder).toBeNull();
  expect(trustOffer(older)).toBeNull();
  // An exited session is not waiting, whatever its last screen said.
  const exited = statusFromListing(
    listing({ claude_alive: false, pid: null, waiting_for_trust: true, trust_folder: "/x" }),
    "/Users/me/demo",
    "devbox",
  );
  expect(exited.running).toBe(false);
  expect(trustOffer(exited)).toBeNull();
});

test("Start's answer of waiting-for-trust is carried to the status the row draws (KYO-567.c3)", () => {
  const started = parseStarted({
    ok: true,
    state: "waiting-for-trust",
    waiting_for_trust: true,
    trust_folder: "/Users/me/demo",
    session: "DEMO",
    pid: 42,
    cwd: "/Users/me/demo",
    started_with: "claude --remote-control DEMO",
    attach: "tmux -L kyosei attach -t =DEMO",
    remote_control_name: "DEMO",
  });
  expect(started).toMatchObject({ session: "DEMO", trustFolder: "/Users/me/demo" });
  const plain = parseStarted({
    ok: true,
    state: "started",
    waiting_for_trust: false,
    trust_folder: null,
    session: "DEMO",
    pid: 42,
    cwd: "/Users/me/demo",
  });
  expect(plain?.trustFolder).toBeNull();
  // Waiting with no folder named: the session's directory is the folder asked about.
  expect(waitingForTrust({ waiting_for_trust: true, cwd: "/w" })).toBe("/w");
  expect(waitingForTrust({ waiting_for_trust: "yes", trust_folder: "/w" })).toBeNull();
});

test("Start through a dev box: the row says waiting for trust as soon as start returns, before any poll (KYO-567.c3)", async () => {
  const said: string[] = [];
  // A stand-in for ssh: `list` has no session yet, `start` answers waiting-for-trust.
  const run: Runner = async (argv) => {
    const words = argv.join(" ");
    said.push(words);
    const answer = words.includes("controller list")
      ? {
          ok: true,
          socket: "tmux -L kyosei",
          server_running: true,
          projects: [row({ session: null, claude_alive: false, pid: null })],
        }
      : {
          ok: true,
          state: "waiting-for-trust",
          waiting_for_trust: true,
          trust_folder: "/Users/me/demo",
          session: "DEMO",
          pid: 42,
          cwd: "/Users/me/demo",
          started_with: "claude --remote-control DEMO",
          attach: "tmux -L kyosei attach -t =DEMO",
          remote_control_name: "DEMO",
        };
    return { code: 0, stdout: `${JSON.stringify(answer)}\n`, stderr: "" };
  };
  const status = await startControllerOn(sshCommandHost("devbox"), "/Users/me/demo", run);
  expect(said.some((words) => words.includes("controller start"))).toBe(true);
  expect(status).toMatchObject({ running: true, trustFolder: "/Users/me/demo", problem: null });
  expect(status.why).toBe(trustWhy("DEMO", "devbox", "/Users/me/demo"));
  expect(trustOffer(status)?.text).toContain(
    "Claude Code is asking whether to trust /Users/me/demo on devbox",
  );
});
