// The Start orchestrator panel (KYO-719): when it is offered, and what it says. It holds no
// state and touches no element, so that it can be tested without a page.

/** What the app says of one press, as the page receives it. */
export interface StartFrame {
  /** `starting` while the request is out, `answered` with the server's reply, or `failed`
   *  when no request could be made or answered. `ended` follows an answer whose session the
   *  project's hooks could not list, once that session has ended (KYO-988). */
  phase: "starting" | "answered" | "failed" | "ended";
  status?: number;
  /** The server's JSON document, as it was sent. */
  reply?: Record<string, unknown> | null;
  /** Why no reply came, in words. */
  text?: string;
}

export interface StartView {
  /** Whether the panel is shown at all. */
  shown: boolean;
  /** Whether the button can be pressed. */
  enabled: boolean;
  text: string;
  /** Whether the panel offers the terminal, where the trust prompt is answered (KYO-964). */
  terminal: boolean;
}

const word = (value: unknown): string | null =>
  typeof value === "string" && value ? value : null;

/**
 * Whether a start's reply says that the project's session hooks are not installed, so the
 * session it started is never listed (KYO-988). Only an explicit `installed: false` counts.
 */
export function hookless(reply: Record<string, unknown> | null | undefined): boolean {
  const hooks = reply?.session_hooks;
  return typeof hooks === "object" && hooks !== null
    && (hooks as Record<string, unknown>).installed === false;
}

/** The panel's words for a session the project's hooks cannot list (KYO-988). */
export const HOOKLESS_TEXT = "It will not be shown here, because this project's session hooks "
  + "are not installed, and a session is shown only once one of its hooks has run. Run "
  + "`kyo onboard` in the session's terminal, which Open the terminal shows here, then start a "
  + "new session, or type /reload-plugins in that one.";

/**
 * The panel for a project whose session list is `live` (null before any list has arrived)
 * and whose last press came to `frame`.
 */
export function startView(live: boolean | null, frame: StartFrame | null): StartView {
  const off = { terminal: false };
  if (live !== false) return { shown: false, enabled: false, text: "", ...off };
  if (!frame) {
    return { shown: true, enabled: true, text: "This project has no live session.", ...off };
  }
  if (frame.phase === "starting") {
    return { shown: true, enabled: false, text: "Starting the orchestrator session.", ...off };
  }
  if (frame.phase === "ended") {
    return {
      shown: true, enabled: true,
      text: "The session that was started has ended. This project has no live session.", ...off,
    };
  }
  if (frame.phase === "failed") {
    return {
      shown: true, enabled: true, text: frame.text ?? "The session was not started.", ...off,
    };
  }
  const reply = frame.reply ?? {};
  if (frame.status !== 200) {
    // The server's sentence names the remedy, so it is shown as it was sent.
    const refusal = word(reply.error)
      ?? `The server did not start a session (HTTP ${frame.status ?? "status unknown"}).`;
    return { shown: true, enabled: true, text: refusal, ...off };
  }
  const session = word(reply.session) ?? "the session";
  const unlisted = hookless(reply);
  if (reply.waiting_for_trust === true || reply.state === "waiting-for-trust") {
    const folder = word(reply.trust_folder) ?? "the project's folder";
    const attach = word(reply.attach);
    return {
      shown: true,
      enabled: false,
      text: `Started ${session}, and Claude Code is asking whether to trust ${folder}. `
        + "The session waits until a person answers in its terminal, which Open the terminal "
        + "shows here"
        + (attach ? ` and \`${attach}\` opens on the server's machine` : "")
        + (unlisted ? `. ${HOOKLESS_TEXT}` : ". It is shown here after that."),
      terminal: true,
    };
  }
  if (unlisted) {
    return {
      shown: true, enabled: false, text: `Started ${session}. ${HOOKLESS_TEXT}`, terminal: true,
    };
  }
  return {
    shown: true,
    enabled: false,
    text: `Started ${session}. It is shown here once Claude Code has begun.`,
    ...off,
  };
}
