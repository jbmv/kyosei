// Builds the renderer into the app's resources. The output is not committed.
import { build } from "esbuild";
import { cpSync, mkdirSync, rmSync } from "node:fs";

const out = "../Sources/KyoseiMac/Resources/renderer";
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

await build({
  entryPoints: ["src/chat.ts"],
  bundle: true,
  minify: true,
  format: "iife",
  target: "safari18",
  outfile: `${out}/chat.js`,
});

cpSync("static", out, { recursive: true });
cpSync("node_modules/katex/dist/katex.min.css", `${out}/katex.min.css`);
cpSync("node_modules/katex/dist/fonts", `${out}/fonts`, { recursive: true });
cpSync("node_modules/highlight.js/styles/github.min.css", `${out}/hljs-light.css`);
cpSync("node_modules/highlight.js/styles/github-dark.min.css", `${out}/hljs-dark.css`);
// The terminal emulator's own stylesheet, and its notice, which goes wherever its code does.
cpSync("node_modules/@xterm/xterm/css/xterm.css", `${out}/xterm.css`);
cpSync("LICENSE-xterm", `${out}/LICENSE-xterm`);
// CodeMirror, which the composer's editor is built on (KYO-909), and its notice likewise.
cpSync("LICENSE-codemirror", `${out}/LICENSE-codemirror`);
