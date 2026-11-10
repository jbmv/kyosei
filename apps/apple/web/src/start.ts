// The Start orchestrator panel (KYO-719): when it is offered, and what it says. It holds no
// state and touches no element, so that it can be tested without a page.

/** What the app says of one press, as the page receives it. */
export interface StartFrame {
  /** `starting` while the request is out, `answered` with the server's reply, or `failed`
   *  when no request could be made or answered. */
  phase: "starting" | "answered" | "failed";
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
}

const word = (value: unknown): string | null =>
  typeof value === "string" && value ? value : null;

/**
 * The panel for a project whose session list is `live` (null before any list has arrived)
 * and whose last press came to `frame`.
 */
export function startView(live: boolean | null, frame: StartFrame | null): StartView {
  if (live !== false) return { shown: false, enabled: false, text: "" };
  if (!frame) return { shown: true, enabled: true, text: "This project has no live session." };
  if (frame.phase === "starting") {
    return { shown: true, enabled: false, text: "Starting the orchestrator session." };
  }
  if (frame.phase === "failed") {
    return { shown: true, enabled: true, text: frame.text ?? "The session was not started." };
  }
  const reply = frame.reply ?? {};
  if (frame.status !== 200) {
    // The server's sentence names the remedy, so it is shown as it was sent.
    const refusal = word(reply.error)
      ?? `The server did not start a session (HTTP ${frame.status ?? "status unknown"}).`;
    return { shown: true, enabled: true, text: refusal };
  }
  const session = word(reply.session) ?? "the session";
  if (reply.waiting_for_trust === true || reply.state === "waiting-for-trust") {
    const folder = word(reply.trust_folder) ?? "the project's folder";
    const attach = word(reply.attach);
    return {
      shown: true,
      enabled: false,
      text: `Started ${session}, and Claude Code is asking whether to trust ${folder}. `
        + "The session waits until a person answers in its terminal"
        + (attach ? `, which \`${attach}\` opens on the server's machine` : "")
        + ". It is shown here after that.",
    };
  }
  return {
    shown: true,
    enabled: false,
    text: `Started ${session}. It is shown here once Claude Code has begun.`,
  };
}
