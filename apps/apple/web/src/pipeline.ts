// Adapted from MDHero's src/lib/renderer/pipeline.ts (MIT, see ../LICENSE-mdhero): the same
// markdown-it, DOMPurify, highlight.js and KaTeX configuration. Removed: frontmatter,
// Marp, image-path resolution through Tauri and the direction plugin. Changed: every block
// is stamped with its source line, not only the top-level ones, and kyosei:// links survive.
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
import taskLists from "markdown-it-task-lists";
import texmath from "markdown-it-texmath";
import katex from "katex";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import ini from "highlight.js/lib/languages/ini";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import plaintext from "highlight.js/lib/languages/plaintext";
import python from "highlight.js/lib/languages/python";
import shell from "highlight.js/lib/languages/shell";
import sql from "highlight.js/lib/languages/sql";
import swift from "highlight.js/lib/languages/swift";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

const languages = {
  bash, css, diff, ini, javascript, json, markdown, plaintext, python, shell, sql, swift,
  typescript, xml, yaml, toml: ini, html: xml, js: javascript, ts: typescript, tsx: typescript,
  jsx: javascript, py: python, sh: bash, zsh: bash, text: plaintext, txt: plaintext,
};
for (const [name, language] of Object.entries(languages)) hljs.registerLanguage(name, language);

const md: MarkdownIt = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  highlight: (source, language) => {
    // A fence with no known language stays plain: guessing colours prose.
    if (language && hljs.getLanguage(language)) {
      try {
        return hljs.highlight(source, { language }).value;
      } catch {}
    }
    return "";
  },
});

md.use(texmath, { engine: katex, delimiters: "dollars" });
md.use(taskLists, { enabled: false, label: true });

// Each block carries the line it came from, which gives a paragraph, list item or table
// row an address within its message that tagging can anchor to.
md.core.ruler.push("source-line", (state) => {
  for (const token of state.tokens) {
    if (token.map && token.type.endsWith("_open")) {
      token.attrSet("data-source-line", String(token.map[0]));
      token.attrSet("data-source-end", String(token.map[1]));
    }
  }
});

const ADD_TAGS = [
  "math", "mrow", "mi", "mo", "mn", "msup", "msub", "mfrac", "mover", "munder", "msqrt",
  "mtable", "mtr", "mtd", "annotation", "semantics", "mspace", "mtext", "mpadded",
];
const ADD_ATTR = ["class", "style", "aria-hidden", "mathvariant", "encoding", "data-source-line", "data-source-end"];
const ALLOWED_URI_REGEXP = /^(?:https?|mailto|kyosei):/i;

export function render(source: string): string {
  return DOMPurify.sanitize(md.render(source), { ADD_TAGS, ADD_ATTR, ALLOWED_URI_REGEXP });
}
