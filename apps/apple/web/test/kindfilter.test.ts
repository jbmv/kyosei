// Run with `npm test`. The Records list's Kind control (KYO-785), in a headless Chrome fed the
// frames the app would send; a machine with no Chrome skips these.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { openPage, type Page } from "./chrome.ts";

let page: Page | null = null;
before(async () => { page = await openPage(); });
after(async () => { await page?.close(); });

const RECORDS = [
  { id: "KYO-1", kind: "task", title: "The mac app list", status: "open" },
  { id: "KYO-2", kind: "plan", title: "Native mac app", status: "open" },
  { id: "KYO-3", kind: "plan", title: "Release notes", status: "open" },
  { id: "KYO-4", kind: "decision", title: "Mac app name", status: "open" },
];

async function open(): Promise<Page> {
  const opened = page!;
  await opened.reload();
  await opened.run(`(() => {
    window.kyo.project({ project: "p", scope: "server/p" });
    window.kyo.records({ project: "p" });
    window.kyo.recordLists({ list: { records: ${JSON.stringify(RECORDS)} } });
  })()`);
  return opened;
}

interface Seen {
  ids: string[]; label: string; color: string; accent: string; clearShown: boolean; field: string;
  boxes: string[]; checked: string[]; menuShut: boolean;
}

/** Reads the landing page's list, field and Kind control. */
const seen = (opened: Page) => opened.run<Seen>(`(() => {
  const root = document.getElementById("records-landing");
  const control = root.querySelector(".kind-control");
  const button = control.querySelector(".kind-button");
  const probe = document.createElement("span");
  probe.style.color = "var(--link)";
  document.body.append(probe);
  const accent = getComputedStyle(probe).color;
  probe.remove();
  return {
    ids: [...document.querySelectorAll("#records-groups [data-open]")].map((row) => row.dataset.open),
    label: button.textContent,
    color: getComputedStyle(button).color,
    accent,
    clearShown: getComputedStyle(control.querySelector(".kind-clear")).display !== "none",
    field: root.querySelector(".records-search").value,
    boxes: [...control.querySelectorAll(".kind-menu input")].map((box) => box.value),
    checked: [...control.querySelectorAll(".kind-menu input:checked")].map((box) => box.value),
    menuShut: control.querySelector(".kind-menu").hidden,
  };
})()`);

/** Shows the Records landing page. */
const landing = (opened: Page) => opened.run(`document.querySelector("[data-view-id=records], #views [data-view=records]")?.click()`);

const type = (opened: Page, text: string) => opened.run(`(() => {
  const field = document.querySelector("#records-landing .records-search");
  field.value = ${JSON.stringify(text)};
  field.dispatchEvent(new Event("input", { bubbles: true }));
})()`);

const check = (opened: Page, kind: string) => opened.run(`(() => {
  const box = [...document.querySelectorAll("#records-landing .kind-menu input")].find((b) => b.value === ${JSON.stringify(kind)});
  box.click();
})()`);

test("the Kind control lists the kinds the store holds, and the list shows only the kinds checked", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await landing(opened);
  let now = await seen(opened);
  assert.deepEqual(now.boxes, ["decision", "plan", "task"]);
  assert.equal(now.ids.length, 4, "with none checked every kind is shown");
  await opened.run(`document.querySelector("#records-landing .kind-button").click()`);
  assert.equal((await seen(opened)).menuShut, false);
  await check(opened, "plan");
  now = await seen(opened);
  assert.deepEqual(now.ids.sort(), ["KYO-2", "KYO-3"]);
  assert.deepEqual(now.checked, ["plan"]);
  await check(opened, "task");
  assert.deepEqual((await seen(opened)).ids.sort(), ["KYO-1", "KYO-2", "KYO-3"]);
  await check(opened, "plan");
  await check(opened, "task");
  now = await seen(opened);
  assert.equal(now.ids.length, 4);
  assert.equal(now.field.trim(), "", "unchecking the last kind removes the token");
});

test("the control reads Kind in the ordinary colour, and names the kinds in the accent colour with an x", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await landing(opened);
  const idle = await seen(opened);
  assert.equal(idle.label, "Kind");
  assert.equal(idle.clearShown, false);
  assert.notEqual(idle.color, idle.accent);
  await check(opened, "plan");
  await check(opened, "task");
  const active = await seen(opened);
  assert.equal(active.label, "Kind: plan, task");
  assert.equal(active.color, active.accent);
  assert.equal(active.clearShown, true);
  await opened.run(`document.querySelector("#records-landing .kind-clear").click()`);
  const cleared = await seen(opened);
  assert.equal(cleared.label, "Kind");
  assert.equal(cleared.clearShown, false);
  assert.equal(cleared.ids.length, 4);
  assert.equal(cleared.field, "");
});

test("kind:plan in the search field checks the kind and searches the rest as text", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await landing(opened);
  await type(opened, "kind:plan mac app");
  let now = await seen(opened);
  assert.deepEqual(now.ids, ["KYO-2"], "only plans matching \"mac app\"");
  assert.deepEqual(now.checked, ["plan"]);
  assert.equal(now.label, "Kind: plan");
  assert.equal(now.field, "kind:plan mac app");
  await type(opened, "kind:plan,task");
  now = await seen(opened);
  assert.deepEqual(now.checked.sort(), ["plan", "task"]);
  assert.deepEqual(now.ids.sort(), ["KYO-1", "KYO-2", "KYO-3"]);
  // Clearing from the control takes the token out of the field and leaves the words.
  await type(opened, "kind:plan mac app");
  await opened.run(`document.querySelector("#records-landing .kind-clear").click()`);
  now = await seen(opened);
  assert.equal(now.field, "mac app");
  assert.deepEqual(now.ids.sort(), ["KYO-1", "KYO-2", "KYO-4"]);
});

test("an unknown kind matches nothing and the control says so", async (t) => {
  if (!page) return t.skip("no Chrome on this machine");
  const opened = await open();
  await landing(opened);
  await type(opened, "kind:nothing");
  const now = await seen(opened);
  assert.deepEqual(now.ids, []);
  assert.equal(now.label, "Kind: nothing (no such kind)");
  assert.equal(now.color, now.accent);
});
