// Run with `npm test`. KYO-883: Chat's side bar is gone. Its subagents are listed in the
// popover under the composer's Send, running ones and then, under a rule, finished ones; a
// project with more than one live session lists them in the project dropdown, under the
// project, and one with a single session lists none. A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const AT = "2026-11-12T10:00:00.000Z";
const session = (id: string, title: string, live: boolean) =>
  ({ session_id: id, title, live, first_seen: AT, remote_control_url: null });

test("the subagent popover lists the running ones, a rule, then the finished ones", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  const listed = await page.run<{ rows: string[]; rule: number; hidden: boolean }>(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [${JSON.stringify(session("S1", "t", true))}] } });
    window.kyo.apply({
      tool_calls: [
        { call_id: "c1", uuid: "u1", at: "${AT}", name: "Agent", input: {}, result: null },
        { call_id: "c2", uuid: "u2", at: "${AT}", name: "Agent", input: {}, result: null },
      ],
      subagents: [
        { agent_id: "a1", description: "Still working", agent_type: "kyosei-worker", tool_call_uuid: "u1" },
        { agent_id: "a2", description: "Already done", agent_type: "kyosei-worker", tool_call_uuid: "u2" },
      ],
      deliveries: [{ uuid: "d1", at: "${AT}", kind: "task", text: "done", sender: null, sender_id: null,
        handback: false, task_id: "a2", call_id: "c2", status: "completed", summary: null }],
    });
    document.getElementById("running-agents-count").click();
    const list = document.getElementById("running-agents-list");
    return {
      rows: [...list.querySelectorAll(".running-agent-name")].map((row) => row.textContent),
      rule: list.querySelectorAll("hr").length,
      hidden: list.hidden,
    };
  })()`);
  assert.deepEqual(listed, { rows: ["Still working", "Already done"], rule: 1, hidden: false });
  assert.equal(await page.run<string>(`document.getElementById("running-agents-count").textContent`), "Subagents: 1");
});

test("the project dropdown lists the project's live sessions only when it has more than one", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  await page.reload();
  const projects = { project: "p", projects: [{ name: "p", can_open: true, problem: null }], problem: null, live: {} };
  const rows = () => page!.run<string[]>(`(() => {
    window.kyo.projects(${JSON.stringify(projects)});
    return [...document.querySelectorAll("#project-popup .project-session")].map((row) => row.textContent);
  })()`);
  await page.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      ${JSON.stringify(session("S1", "The orchestrator", true))}, ${JSON.stringify(session("S0", "An old one", false))} ] } });
  })()`);
  assert.deepEqual(await rows(), []);
  await page.run(`window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
    ${JSON.stringify(session("S1", "The orchestrator", true))}, ${JSON.stringify(session("S2", "A second", true))} ] } })`);
  assert.deepEqual(await rows(), ["The orchestrator", "A second"]);
});
