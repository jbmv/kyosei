// Run with `npm test`. KYO-810: a prompt was typed into the terminal's input box, left there
// unsubmitted, and shown in the composer as "Sending…" with nothing else happening. The page
// is handed the frames the Mac app passes on from the server's `held` event and its `ready`
// event. The server's half, that a message typed and not submitted is kept with the reason
// `not-submitted`, is tested in tests/test_kyo_serve_held.py. A machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

interface Box { hidden: boolean; text: string; sendNow: boolean; edit: boolean }

const box = `({
  hidden: document.getElementById("held").hidden,
  text: document.getElementById("held-text").textContent,
  sendNow: !document.getElementById("held-send-now").hidden,
  edit: !document.getElementById("held-edit").hidden,
})`;

const detail = "the message was typed into the input box of pane %1 and Enter did not submit it";

test("a prompt typed and not submitted is said to be, with what to do", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  const shown = await page.run<Box>(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.sent({ id: "m1" });
    window.kyo.held(${JSON.stringify({
      outcome: "held", id: "m1",
      held: { id: "m1", state: "held", retrying: false, reason: "not-submitted", detail },
    })});
    return ${box};
  })()`);
  assert.equal(shown.hidden, false);
  assert.match(shown.text, /^Typed into the session's terminal, but not submitted\./);
  assert.match(shown.text, /press Enter there, or choose Edit/);
  assert.deepEqual([shown.sendNow, shown.edit], [false, true]);
});

test("a page opened later is told the same from the stream's first event", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  const shown = await page.run<Box>(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.ready(${JSON.stringify({
      screen: { state: "idle" },
      held: { id: "m1", state: "held", retrying: false, reason: "not-submitted", detail },
    })});
    return ${box};
  })()`);
  assert.match(shown.text, /not submitted/);
});

test("a prompt that waits on the terminal says what it waits on", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  const shown = await page.run<Box>(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.held(${JSON.stringify({
      outcome: "held", id: "m2",
      held: { id: "m2", state: "held", retrying: true, reason: "input-box-in-use",
        detail: "text is in the terminal's input box" },
    })});
    return ${box};
  })()`);
  assert.equal(shown.text, "Waiting: text is in the terminal's input box");
});

test("a prompt on its way still reads as sending, and a delivered one leaves", async (t) => {
  if (!page) return t.skip("no Chrome here");
  await page.reload();
  const shown = await page.run<Box[]>(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.held({ outcome: "held", id: "m3", held: { id: "m3", retrying: true, reason: null, detail: null } });
    const first = ${box};
    window.kyo.held({ outcome: "delivered", id: "m3", held: null });
    return [first, ${box}];
  })()`);
  assert.equal(shown[0].text, "Sending…");
  assert.equal(shown[1].hidden, true);
});
