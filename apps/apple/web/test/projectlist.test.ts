// Run with `npm test`. File > Projects… shows the list in place of the chat and returns without clearing it (KYO-766).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const chat = readFileSync(new URL("../src/chat.ts", import.meta.url), "utf8");
const css = readFileSync(new URL("../static/chat.css", import.meta.url), "utf8");
const app = readFileSync(new URL("../../Sources/KyoseiMac/App.swift", import.meta.url), "utf8");

test("the menu item, the page's frame and the list's styling are joined", () => {
  assert.match(app, /Button\("Projects…"\)[\s\S]*keyboardShortcut/);
  assert.match(chat, /projectList\(\): void \{\s*toggleProjectList\(\)/);
  assert.match(css, /body\.pick-project #projects-pane/);
});

test("the list is left by Escape, by the same item, or by choosing the shown project, and clears nothing", () => {
  assert.match(chat, /event\.key !== "Escape" \|\| !projectListShown\(\)/);
  assert.match(chat, /if \(name === windowProject && !session\) closeProjectList\(\)/);
  const toggle = chat.match(/function toggleProjectList\(\)[\s\S]*?\n}/)![0];
  assert.doesNotMatch(toggle, /kyo\.clear|showTexts|windowProject =/);
});
