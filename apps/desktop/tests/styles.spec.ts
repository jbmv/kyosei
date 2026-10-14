// Every class name the renderer writes has a rule (KYO-242.c1, KYO-253.c1).
//
// KYO-240.d1 measured 56 of 171 class names with no rule anywhere, and the count grew
// to 68 of 228 over the two slices that followed before anyone looked again: a class
// with no rule costs nothing to write and is invisible until a reader meets the
// element it is on, drawn as the browser's default. This check is the thing that
// keeps the count at zero -- it reads the same sources a bundler would and fails on
// the first name it cannot find a selector for, naming the file that writes it.
//
// What counts as "written": a `class:` literal handed to `el(...)`, either branch of
// a `cond ? "a" : "b"` class expression, a `classList.add(...)` literal, and the
// CodeMirror class hooks compose passes by name (`tooltipClass`, `optionClass`). A
// name built from a template (`md-li-${depth}`) is checked by its literal prefix
// only, which is the part a rule can name. What counts as "a rule": the name appears
// as `.name` in `shell.css` or in any `styles.ts` under the renderer, all of which
// install through `installStyles` (`tabs/kind.ts`).
//
// This spec needs no window: it is a read of the tree, and runs in the same Playwright
// process as the rest so `npm run check` carries it.

import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { expect, test } from "@playwright/test";

const RENDERER = join(__dirname, "..", "src", "renderer");

/** Every `.ts` and `.css` file under the renderer, as `[relative path, text]`. */
function rendererSources(): [string, string][] {
  const out: [string, string][] = [];
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (/\.(ts|css)$/.test(entry.name) && !entry.name.endsWith(".d.ts")) {
        out.push([relative(RENDERER, full), readFileSync(full, "utf8")]);
      }
    }
  };
  walk(RENDERER);
  return out;
}

/**
 * The names a class string carries. A token with a template hole in it (`md-li-${n}`,
 * `link${suffix}`) is dropped whole: the literal beside it (`md-li`, `link`) is the
 * part a rule can name, and it is on the same element.
 */
function namesIn(raw: string): string[] {
  return raw
    .replace(/[\w-]*\$\{.*?\}[\w-]*/g, " ")
    .split(/\s+/)
    .map((name) => name.trim())
    .filter((name) => /^[a-zA-Z][\w-]*$/.test(name));
}

/** Every class name the renderer writes, with the files that write it. */
export function emittedClasses(sources: [string, string][]): Map<string, Set<string>> {
  const emitted = new Map<string, Set<string>>();
  const note = (name: string, file: string): void => {
    let files = emitted.get(name);
    if (files === undefined) {
      files = new Set();
      emitted.set(name, files);
    }
    files.add(file);
  };
  for (const [file, text] of sources) {
    if (file.endsWith(".css") || /styles\.ts$/.test(file)) continue;
    // `class: "a b"`, `class: 'a'`, `class: \`a-${x}\`` -- a template's hole may
    // hold a nested template, so it runs to the last backtick on the line.
    for (const match of text.matchAll(/\bclass:\s*(?:"([^"]*)"|'([^']*)'|`(.*)`\s*[,}])/g)) {
      for (const name of namesIn(match[1] ?? match[2] ?? match[3] ?? "")) note(name, file);
    }
    // `class: cond ? "a" : "b"`.
    for (const match of text.matchAll(/\bclass:\s*[^,\n]*?\?\s*"([^"]*)"\s*:\s*"([^"]*)"/g)) {
      for (const raw of [match[1] ?? "", match[2] ?? ""]) {
        for (const name of namesIn(raw)) note(name, file);
      }
    }
    for (const match of text.matchAll(/classList\.(?:add|toggle)\(\s*"([^"]*)"/g)) {
      for (const name of namesIn(match[1] ?? "")) note(name, file);
    }
    // CodeMirror's class hooks: `tooltipClass: () => "x"`, `optionClass: () => "x"`.
    for (const match of text.matchAll(/(?:tooltipClass|optionClass):\s*\(\)\s*=>\s*"([^"]*)"/g)) {
      for (const name of namesIn(match[1] ?? "")) note(name, file);
    }
  }
  return emitted;
}

/** Every class name a selector in any sheet names. */
export function styledClasses(sources: [string, string][]): Set<string> {
  const styled = new Set<string>();
  for (const [file, text] of sources) {
    if (!(file.endsWith(".css") || /styles\.ts$/.test(file))) continue;
    for (const match of text.matchAll(/\.([a-zA-Z][\w-]*)/g)) styled.add(match[1] ?? "");
  }
  return styled;
}

test("every class name the renderer writes has a rule in a stylesheet", () => {
  const sources = rendererSources();
  const emitted = emittedClasses(sources);
  const styled = styledClasses(sources);

  // The reader of a failure needs the name and where it is written, not a count.
  const unstyled = [...emitted.entries()]
    .filter(([name]) => !styled.has(name))
    .map(([name, files]) => `${name}  (written by ${[...files].join(", ")})`)
    .sort();
  expect(unstyled, "class names with no rule in shell.css or any styles.ts").toEqual([]);

  // And the check itself is looking at something: the renderer writes many classes,
  // and a regex that matched none would pass vacuously.
  expect(emitted.size).toBeGreaterThan(150);
  expect(styled.size).toBeGreaterThan(150);
});

test("the sheets draw from one token set: no literal colour or pixel font size", () => {
  // A literal colour or font size in a rule is a second palette (KYO-253.c2). The
  // tokens themselves are declared on `:root` in shell.css, which is the one place
  // a literal belongs.
  const offenders: string[] = [];
  for (const [file, text] of rendererSources()) {
    if (!(file.endsWith(".css") || /styles\.ts$/.test(file))) continue;
    const body = file.endsWith(".css") ? text.replace(/:root\s*\{[^}]*\}/, "") : text;
    for (const [index, line] of body.split("\n").entries()) {
      if (/font-size:\s*\d+(\.\d+)?px/.test(line))
        offenders.push(`${file}:${index + 1} ${line.trim()}`);
      if (/(?:color|background|border-color|fill|stroke):\s*#[0-9a-fA-F]{3,8}\b/.test(line)) {
        offenders.push(`${file}:${index + 1} ${line.trim()}`);
      }
    }
  }
  expect(offenders, "rules using a literal instead of a token").toEqual([]);
});
