// Run with `npm test`. Short names for a subagent (KYO-726) and the send gate (KYO-724).
import assert from "node:assert/strict";
import { test } from "node:test";
import { agentKind, shortModel } from "../src/agents.ts";
import { SendGate } from "../src/sending.ts";

test("a model is named by its family word", () => {
  assert.equal(shortModel("claude-fable-5-2"), "fable");
  assert.equal(shortModel("claude-opus-5"), "opus");
  assert.equal(shortModel("claude-3-5-sonnet-20241022"), "sonnet");
  assert.equal(shortModel(null), "");
});

test("a subagent is named by model and effort, and by its type before it has replied", () => {
  assert.equal(agentKind({ model: "claude-fable-5-2", effort: "medium", agent_type: "kyosei-designer" }),
    "fable medium");
  assert.equal(agentKind({ model: "claude-haiku-5-5", effort: null, agent_type: "x" }), "haiku");
  assert.equal(agentKind({ model: null, effort: null, agent_type: "kyosei-designer" }), "kyosei-designer");
});

test("a second press while a send is in flight is refused, and the gate reopens when it answers", () => {
  const gate = new SendGate<string>();
  assert.equal(gate.begin("a"), true);
  assert.equal(gate.begin("a"), false);
  assert.equal(gate.begin("b"), false);
  assert.equal(gate.end(), "a");
  assert.equal(gate.begin("b"), true);
});
