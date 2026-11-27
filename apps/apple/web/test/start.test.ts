// Run with `npm test`. The Start orchestrator panel is offered only without a live session,
// and says in words what the server answered (KYO-719).
import assert from "node:assert/strict";
import { test } from "node:test";
import { hookless, startView } from "../src/start.ts";

test("nothing is offered before a session list arrives, or while a session is live", () => {
  assert.equal(startView(null, null).shown, false);
  assert.equal(startView(true, null).shown, false);
  assert.equal(startView(true, { phase: "starting" }).shown, false);
});

test("a project with no live session is offered the button", () => {
  assert.deepEqual(startView(false, null), {
    shown: true, enabled: true, text: "This project has no live session.", terminal: false,
  });
});

test("the button cannot be pressed twice while the request is out", () => {
  const view = startView(false, { phase: "starting" });
  assert.equal(view.enabled, false);
  assert.match(view.text, /Starting/);
});

test("a started session is named, and the button stays off until it appears", () => {
  const view = startView(false, {
    phase: "answered", status: 200, reply: { state: "started", session: "KYO" },
  });
  assert.equal(view.enabled, false);
  assert.equal(view.text, "Started KYO. It is shown here once Claude Code has begun.");
  assert.equal(view.terminal, false);
});

test("waiting-for-trust is said in words, with the folder and where to answer", () => {
  const view = startView(false, {
    phase: "answered",
    status: 200,
    reply: {
      state: "waiting-for-trust", waiting_for_trust: true, trust_folder: "/work/proj",
      session: "KYO", attach: "tmux -L kyosei attach -t '=KYO'",
    },
  });
  assert.equal(view.enabled, false);
  assert.match(view.text, /asking whether to trust \/work\/proj/);
  assert.match(view.text, /`tmux -L kyosei attach -t '=KYO'`/);
  assert.doesNotMatch(view.text, /waiting-for-trust/);
  // KYO-964: the prompt is answered in the app's own terminal, which the panel offers.
  assert.equal(view.terminal, true);
  assert.match(view.text, /Open the terminal shows here/);
});

test("the terminal is offered only while the trust prompt waits", () => {
  for (const frame of [
    null,
    { phase: "starting" as const },
    { phase: "failed" as const, text: "No." },
    { phase: "answered" as const, status: 409, reply: { error: "already running" } },
  ]) {
    assert.equal(startView(false, frame).terminal, false);
  }
  const waiting = startView(false, {
    phase: "answered", status: 200, reply: { state: "waiting-for-trust", session: "KYO" },
  });
  assert.equal(waiting.terminal, true);
  assert.doesNotMatch(waiting.text, /server's machine/);
});

test("a refusal is the server's own sentence, and the button can be pressed again", () => {
  const error = "the token of device ipad does not carry the start permission; revoke it "
    + "(`kyo devices revoke ipad`) and issue another";
  const view = startView(false, {
    phase: "answered", status: 403, reply: { ok: false, error, reason: "no-start-permission" },
  });
  assert.deepEqual(view, { shown: true, enabled: true, text: error, terminal: false });
});

test("a reply that is not the server's still says something", () => {
  const view = startView(false, { phase: "answered", status: 502, reply: null });
  assert.equal(view.enabled, true);
  assert.match(view.text, /HTTP 502/);
});

test("a press that reached no server says why", () => {
  const view = startView(false, { phase: "failed", text: "The server could not be reached." });
  assert.deepEqual(view, {
    shown: true, enabled: true, text: "The server could not be reached.", terminal: false,
  });
});

// KYO-988: a start in a project whose session hooks are not installed.
const HOOKLESS = { installed: false, route: null, harness: "missing", detail: "no settings file" };

test("a session the project's hooks cannot list is said so, with the remedy and the terminal", () => {
  const view = startView(false, {
    phase: "answered", status: 200,
    reply: { state: "started", session: "CHS", session_hooks: HOOKLESS },
  });
  assert.equal(view.text,
    "Started CHS. It will not be shown here, because this project's session hooks are not "
    + "installed, and a session is shown only once one of its hooks has run. Run `kyo onboard` "
    + "in the session's terminal, which Open the terminal shows here, then start a new "
    + "session, or type /reload-plugins in that one.");
  assert.equal(view.terminal, true);
  assert.equal(view.enabled, false);
  assert.doesNotMatch(view.text, /once Claude Code has begun/);
});

test("the trust prompt and the missing hooks are said together", () => {
  const view = startView(false, {
    phase: "answered", status: 200,
    reply: {
      state: "waiting-for-trust", waiting_for_trust: true, trust_folder: "/work/chs",
      session: "CHS", session_hooks: HOOKLESS,
    },
  });
  assert.match(view.text, /asking whether to trust \/work\/chs/);
  assert.match(view.text, /session hooks are not installed/);
  assert.doesNotMatch(view.text, /It is shown here after that/);
  assert.equal(view.terminal, true);
});

test("hooks that are installed, or a reply that does not say, change nothing", () => {
  for (const hooks of [{ installed: true }, { installed: null }, undefined]) {
    const view = startView(false, {
      phase: "answered", status: 200,
      reply: { state: "started", session: "KYO", session_hooks: hooks },
    });
    assert.equal(view.text, "Started KYO. It is shown here once Claude Code has begun.");
    assert.equal(hookless({ session_hooks: hooks }), false);
  }
  assert.equal(hookless({ session_hooks: HOOKLESS }), true);
});

test("once that session has ended, the button is given back", () => {
  const view = startView(false, { phase: "ended" });
  assert.deepEqual(view, {
    shown: true, enabled: true,
    text: "The session that was started has ended. This project has no live session.",
    terminal: false,
  });
});
