// What the claude tab says, and offers, while an orchestrator session is waiting on
// Claude Code's workspace-trust prompt (KYO-567).
//
// THE MECHANISM. In a folder Claude Code has never trusted, the interactive claude a
// Start runs stops on its "trust this folder?" dialog before it registers with Remote
// Control, so claude.ai never lists the session and says nothing about why. `kyo
// controller start` and `kyo controller list` read the session's pane and report
// `waiting-for-trust` with the folder; `controller.ts` carries that onto
// `ControllerStatus.trustFolder`, and this file decides what the row shows from it.
//
// THE ANSWER IS THE PERSON'S (KYO-568). The app never sends a keystroke to the pane and
// never writes Claude Code's own settings to pre-trust a folder, so the one thing the
// row offers is the place the prompt is: the terminal tab, which opens on the project's
// orchestrator session. There is no "trust and start" here, by decision.
//
// Pure and Electron-free: the renderer imports it to draw, `controller.ts` imports it
// for the status sentence, and `tests/controller-trust.spec.ts` checks it on its own --
// the window's visible surface is a human-observed check, since the suite launches the
// app hidden.

import type { ControllerStatus } from "./protocol";

/** The state `kyo controller` reports while the prompt is on screen. */
export const WAITING_FOR_TRUST = "waiting-for-trust";

/** The tab the offer opens: the terminal, on the project's orchestrator session. */
export const TRUST_TAB = { kind: "terminal", arg: "" } as const;

/** What the row shows, and the one act it offers, while the session waits for trust. */
export interface TrustOffer {
  /** The row's sentence. */
  text: string;
  /** The button's label. */
  action: string;
  /** The button's hover text. */
  tip: string;
  /** Where the button goes. */
  tab: typeof TRUST_TAB;
}

function folderOf(folder: string): string {
  return folder === "" ? "the project's folder" : folder;
}

/** The status sentence for a session waiting on the prompt: what, where, and why it matters. */
export function trustWhy(session: string, machine: string, folder: string): string {
  return (
    `orchestrator session ${session} on ${machine} is ${WAITING_FOR_TRUST}: Claude Code is ` +
    `asking whether to trust ${folderOf(folder)}, and it will not register with Remote ` +
    "Control until someone answers"
  );
}

/**
 * The offer for this status, or null when there is nothing waiting: a session that is
 * running and whose pane shows Claude Code's trust prompt.
 */
export function trustOffer(status: ControllerStatus): TrustOffer | null {
  if (!status.running || status.trustFolder === null) return null;
  const where = status.machine === "" ? "" : ` on ${status.machine}`;
  return {
    text:
      `Claude Code is asking whether to trust ${folderOf(status.trustFolder)}${where}. ` +
      "claude.ai lists this session only after you answer — in the terminal tab.",
    action: "Open terminal",
    tip:
      `Open the terminal tab on orchestrator session ${status.session ?? "for this project"}, ` +
      "where Claude Code's prompt is waiting. Kyosei does not answer it for you.",
    tab: TRUST_TAB,
  };
}
