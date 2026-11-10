// Run with `npm test`. The Start orchestrator panel is offered only without a live session,
// and says in words what the server answered (KYO-719).
import assert from "node:assert/strict";
import { test } from "node:test";
import { startView } from "../src/start.ts";

test("nothing is offered before a session list arrives, or while a session is live", () => {
  assert.equal(startView(null, null).shown, false);
  assert.equal(startView(true, null).shown, false);
  assert.equal(startView(true, { phase: "starting" }).shown, false);
});

test("a project with no live session is offered the button", () => {
  assert.deepEqual(startView(false, null), {
    shown: true, enabled: true, text: "This project has no live session.",
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
});

test("waiting-for-trust is said in words, with the folder and where to answer", () => {
  const view = startView(false, {
    phase: "answered",
    status: 200,
    reply: {
      state: "waiting-for-trust", waiting_for_trust: true, trust_folder: "/work/proj",
      session: "KYO", attach: "tmux -L kyosei attach -t =KYO",
    },
  });
  assert.equal(view.enabled, false);
  assert.match(view.text, /asking whether to trust \/work\/proj/);
  assert.match(view.text, /`tmux -L kyosei attach -t =KYO`/);
  assert.doesNotMatch(view.text, /waiting-for-trust/);
});

test("a refusal is the server's own sentence, and the button can be pressed again", () => {
  const error = "the token of device ipad does not carry the start permission; revoke it "
    + "(`kyo devices revoke ipad`) and issue another";
  const view = startView(false, {
    phase: "answered", status: 403, reply: { ok: false, error, reason: "no-start-permission" },
  });
  assert.deepEqual(view, { shown: true, enabled: true, text: error });
});

test("a reply that is not the server's still says something", () => {
  const view = startView(false, { phase: "answered", status: 502, reply: null });
  assert.equal(view.enabled, true);
  assert.match(view.text, /HTTP 502/);
});

test("a press that reached no server says why", () => {
  const view = startView(false, { phase: "failed", text: "The server could not be reached." });
  assert.deepEqual(view, { shown: true, enabled: true, text: "The server could not be reached." });
});
