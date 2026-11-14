// Run with `npm test`. A message `kyo serve` sent itself, the idle timer's (KYO-929), is drawn as the
// server's and not as the person's prompt (KYO-930): labelled, on the other side and without the
// person's fill. The page is the built renderer in a headless Chrome, fed the frames the app would
// send; a machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const AUTOMATED = "Automated message from kyo serve, not typed by the person: this session has had no activity for 40 minutes.";

/** One automated turn, one turn of the person's and one turn from a server that sends no field. */
async function open(): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.menu({ project: "p", current: "S1", changes: null, sessions: { sessions: [
      { session_id: "S1", title: "t", live: true, first_seen: "2026-01-01T00:00:00Z", remote_control_url: null },
    ] } });
    window.kyo.apply({ reset: true, turns: [
      { id: "u1", at: "2026-01-01T00:00:00Z", text: "A prompt the person typed.", automatic: false },
      { id: "u2", at: "2026-01-01T00:40:00Z", text: ${JSON.stringify(AUTOMATED)}, automatic: true },
      { id: "u3", at: "2026-01-01T00:50:00Z", text: "A prompt from a server that predates the field." },
    ], replies: [] });
  })()`);
  return opened;
}

interface Drawn { text: string; automatic: boolean; label: string; align: string; fill: string; title: string }

const DRAWN = `(() => [...document.querySelectorAll("#chat .turn")].map((turn) => {
  const style = getComputedStyle(turn);
  return {
    text: turn.textContent, automatic: turn.classList.contains("turn-automatic"),
    label: getComputedStyle(turn, "::before").content, align: style.marginLeft,
    fill: style.backgroundColor, title: turn.title,
  };
}))()`;

test("an automated turn is labelled and styled as the server's, and the person's prompts are as before (KYO-930)", async () => {
  const opened = await open();
  const [person, automated, older] = await opened.run<Drawn[]>(DRAWN);
  assert.equal(automated.automatic, true);
  assert.equal(automated.label, '"Automated message from kyo serve"');
  assert.equal(automated.text, AUTOMATED, "the label is not text of the turn, which an anchor measures");
  assert.match(automated.title, /not typed by the person/);
  assert.equal(automated.align, "0px", "on the opposite side from the person's prompts");
  assert.equal(automated.fill, "rgba(0, 0, 0, 0)", "without the person's fill");
  for (const own of [person, older]) {
    assert.equal(own.automatic, false);
    assert.equal(own.label, "none");
    assert.notEqual(own.align, "0px");
    assert.notEqual(own.fill, "rgba(0, 0, 0, 0)");
    assert.equal(own.title, "");
  }
});
