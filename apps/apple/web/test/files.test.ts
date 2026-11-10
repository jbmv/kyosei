// Run with `npm test`. The Files view's roots, tree, file kinds, path finding, answers,
// mentions and path mapping (KYO-736).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  HEAD_CHARS, NONE_STANDS_MS, answerOf, entriesOf, entryOf, fileKind, folderOf, foldersAbove, isPerSession,
  headOf, hrefPath, inOrder, languageOf, lineCount, linkPlace, localPath, looksOutside, mentioned,
  pathSpans, rootLabel, rootShown, servedRoots, serverPath, sizeWords, stands, treeRows, wholePath,
  withoutLine,
} from "../src/files.ts";

const found = (text: string) => pathSpans(text).map((span) => span.text);

// The document `GET .../files` returned from a scratch server in the split layout.
const rootsDocument = {
  ok: true, max_file_bytes: 33554432,
  roots: [
    { name: "code", path: "/work/demo", served: true },
    { name: "store", path: "/work/store", served: true },
    { name: "scratch", reason: "scratch-not-recorded", served: false },
  ],
};

test("a root has a chip only when the server says it serves it", () => {
  assert.deepEqual(servedRoots(rootsDocument).map((root) => root.name), ["code", "store"]);
  const later = { roots: [...rootsDocument.roots.slice(0, 2), { name: "scratch", path: "/tmp/s", served: true }] };
  assert.deepEqual(servedRoots(later).map((root) => root.name), ["code", "store", "scratch"]);
  const inRepo = { roots: [rootsDocument.roots[0], { name: "store", path: "/work/demo", served: false, same_as: "code" }] };
  assert.deepEqual(servedRoots(inRepo).map((root) => root.name), ["code"]);
  assert.deepEqual(servedRoots(null), []);
  assert.deepEqual(servedRoots({ ok: false, error: "refused" }), []);
});

// The scratch root as the server after KYO-735 states it: one directory for each session.
const perSession = (sessions: string[]) => ({
  ok: true,
  roots: [
    ...rootsDocument.roots.slice(0, 2),
    {
      name: "scratch", served: sessions.length > 0, per_session: true, sessions,
      ...(sessions.length ? {} : { reason: "scratch-not-recorded" }),
    },
  ],
});

test("a session's scratchpad has a chip while a session is followed", () => {
  const names = (document: unknown, session: string | null) =>
    servedRoots(document, session).map((root) => root.name);
  assert.deepEqual(names(perSession(["s1"]), "s1"), ["code", "store", "scratch"]);
  assert.deepEqual(names(perSession(["s1"]), null), ["code", "store"]);
  // A session that has reported no directory is refused by the tree route, in the server's
  // words, so its chip is still shown.
  assert.deepEqual(names(perSession([]), "s2"), ["code", "store", "scratch"]);
  assert.equal(isPerSession(servedRoots(perSession([]), "s2"), "scratch"), true);
  assert.equal(isPerSession(servedRoots(perSession([]), "s2"), "code"), false);
});

test("the chips are named Code, Kyosei and Scratch", () => {
  assert.deepEqual(["code", "store", "scratch", "other"].map(rootLabel), ["Code", "Kyosei", "Scratch", "Other"]);
});

test("the chosen root is shown while it is served, and the first otherwise", () => {
  const roots = servedRoots(rootsDocument);
  assert.equal(rootShown("store", roots), "store");
  assert.equal(rootShown("scratch", roots), "code");
  assert.equal(rootShown("code", []), null);
});

const top = [
  { name: "README.md", path: "README.md", type: "file", text: true, media_type: "text/markdown" },
  { name: ".github", path: ".github", type: "dir" },
  { name: "src", path: "src", type: "dir" },
  { name: ".editorconfig", path: ".editorconfig", type: "file", text: true },
  { name: "Makefile", path: "Makefile", type: "file", text: true },
  { name: "docs", path: "docs", type: "dir" },
];
const listings = new Map([
  ["code:", top],
  ["code:docs", [
    { name: "plan.md", path: "docs/plan.md", type: "file" },
    { name: "img", path: "docs/img", type: "dir" },
  ]],
  ["code:.github", [{ name: "ci.yml", path: ".github/ci.yml", type: "file" }]],
  ["store:", [{ name: "kyosei", path: "kyosei", type: "dir" }]],
]);
const shown = (rows: { entry: { path: string }; depth: number }[]) =>
  rows.map((row) => `${"  ".repeat(row.depth)}${row.entry.path}`);

test("folders come before files, each by name without regard to case", () => {
  assert.deepEqual(
    inOrder(top).map((entry) => entry.name),
    [".github", "docs", "src", ".editorconfig", "Makefile", "README.md"]);
});

test("a tree shows a folder's entries only while the folder is open", () => {
  assert.deepEqual(shown(treeRows("code", listings, new Set(), true)), [
    ".github", "docs", "src", ".editorconfig", "Makefile", "README.md",
  ]);
  const rows = treeRows("code", listings, new Set(["code:docs"]), true);
  assert.deepEqual(shown(rows), [
    ".github", "docs", "  docs/img", "  docs/plan.md", "src", ".editorconfig", "Makefile", "README.md",
  ]);
  assert.equal(rows.find((row) => row.entry.path === "docs")?.open, true);
  assert.equal(rows.find((row) => row.entry.path === "src")?.open, false);
});

test("dotfiles are hidden until asked for, and a dotfolder's contents with it", () => {
  const open = new Set(["code:.github"]);
  assert.deepEqual(shown(treeRows("code", listings, open, false)), ["docs", "src", "Makefile", "README.md"]);
  assert.deepEqual(shown(treeRows("code", listings, open, true)).slice(0, 2), [".github", "  .github/ci.yml"]);
});

test("an open folder whose listing has not arrived is marked as waiting", () => {
  const rows = treeRows("code", listings, new Set(["code:src", "code:docs"]), false);
  assert.equal(rows.find((row) => row.entry.path === "src")?.waiting, true);
  assert.equal(rows.find((row) => row.entry.path === "docs")?.waiting, false);
});

test("each root has its own tree", () => {
  assert.deepEqual(shown(treeRows("store", listings, new Set(["code:docs"]), true)), ["kyosei"]);
  assert.deepEqual(treeRows("scratch", listings, new Set(), true), []);
});

test("a tree document's entries are read, and anything else is not a listing", () => {
  assert.equal(entriesOf({ ok: true, entries: top })?.length, 6);
  assert.equal(entriesOf({ ok: false, error: "no" }), null);
  assert.deepEqual(entriesOf({ entries: [null, { name: 3 }, top[0]] }), [top[0]]);
});

test("the folders above a path are opened outermost first", () => {
  assert.deepEqual(foldersAbove("docs/img/dot.png"), ["docs", "docs/img"]);
  assert.deepEqual(foldersAbove("README.md"), []);
  assert.equal(folderOf("docs/img/dot.png"), "docs/img");
  assert.equal(folderOf("README.md"), "");
});

test("a file is drawn by its type: markdown, text, image, or by its name alone", () => {
  assert.equal(fileKind({ name: "plan.md", media_type: "text/markdown", text: true }), "markdown");
  assert.equal(fileKind({ name: "NOTES.MD" }), "markdown");
  assert.equal(fileKind({ name: "serve.py", media_type: "text/x-python", text: true }), "text");
  assert.equal(fileKind({ name: "config.yml", media_type: "application/yaml", text: true }), "text");
  assert.equal(fileKind({ name: "dot.png", media_type: "image/png", text: false }), "image");
  assert.equal(fileKind({ name: "blob.bin", media_type: "application/octet-stream", text: false }), "other");
  assert.equal(fileKind({ name: "a.txt", media_type: "text/plain; charset=utf-8" }), "text");
});

test("a source file's language is read from its extension, and an unknown one is plain", () => {
  assert.equal(languageOf("src/kyosei/serve.py"), "python");
  assert.equal(languageOf("chat.ts"), "typescript");
  assert.equal(languageOf("SettingsView.swift"), "swift");
  assert.equal(languageOf("pyproject.toml"), "ini");
  assert.equal(languageOf("ci.YML"), "yaml");
  assert.equal(languageOf("Makefile"), null);
  assert.equal(languageOf(".gitignore"), null);
  assert.equal(languageOf("a.b/README"), null);
  assert.equal(languageOf("main.rs"), null);
});

test("sizes are said in the reader's words", () => {
  assert.equal(sizeWords(1), "1 byte");
  assert.equal(sizeWords(269), "269 bytes");
  assert.equal(sizeWords(7737), "7.6 KB");
  assert.equal(sizeWords(61234), "60 KB");
  assert.equal(sizeWords(3 * 1024 * 1024), "3.0 MB");
  assert.equal(sizeWords(undefined), "");
});

test("a short text is shown whole, and a long one as its head, cut at a line", () => {
  assert.deepEqual(headOf("one\ntwo\n"), { text: "one\ntwo\n", whole: true });
  const long = "0123456789\n".repeat(HEAD_CHARS / 10);
  const head = headOf(long);
  assert.equal(head.whole, false);
  assert.ok(head.text.length <= HEAD_CHARS + 1);
  assert.ok(head.text.endsWith("\n"));
  assert.equal(head.text.length % 11, 0);
  assert.deepEqual(headOf("abcdef", 3), { text: "abc", whole: false });
});

test("lines are counted with or without a last newline", () => {
  assert.equal(lineCount(""), 0);
  assert.equal(lineCount("a"), 1);
  assert.equal(lineCount("a\n"), 1);
  assert.equal(lineCount("a\nb"), 2);
  assert.equal(lineCount("a\n\nb\n"), 3);
});

// ------------------------------------------------------------------- paths in what is said

test("an absolute path, a path under the home folder and a relative path are found", () => {
  assert.deepEqual(found("open /Users/x/plans/native-apps.md now"), ["/Users/x/plans/native-apps.md"]);
  assert.deepEqual(found("see ~/notes/today.md"), ["~/notes/today.md"]);
  assert.deepEqual(found("in src/kyosei/serve.py and docs/guide/README.md"), [
    "src/kyosei/serve.py", "docs/guide/README.md",
  ]);
  assert.deepEqual(found("./docs/plan.md"), ["./docs/plan.md"]);
  assert.deepEqual(found("the folder apps/apple/web/ holds it"), ["apps/apple/web/"]);
});

test("a line after a colon belongs to the path, as do a column and a range", () => {
  assert.deepEqual(found("src/kyosei/serve.py:120"), ["src/kyosei/serve.py:120"]);
  assert.deepEqual(found("at src/a.py:120:7, and src/b.py:12-40."), ["src/a.py:120:7", "src/b.py:12-40"]);
  assert.equal(withoutLine("src/a.py:120:7"), "src/a.py");
  assert.equal(withoutLine("src/b.py:12-40"), "src/b.py");
  assert.equal(withoutLine("src/c.py"), "src/c.py");
});

test("the place of each path in the text is given", () => {
  const text = "see docs/plan.md:3, then";
  const [span] = pathSpans(text);
  assert.equal(text.slice(span.from, span.to), "docs/plan.md:3");
});

test("prose with a slash is not asked about", () => {
  for (const text of [
    "and/or", "this and/or that.", "w/o it", "n/a", "24/7", "10/11/2026", "a 1/2 share",
    "input/output", "he/she/they", "/clear", "run /help now", "km/h", "../up/one.md",
  ]) {
    assert.deepEqual(found(text), [], text);
  }
});

test("the punctuation around a path is not part of it", () => {
  assert.deepEqual(found("It is in docs/y.md."), ["docs/y.md"]);
  assert.deepEqual(found("(docs/y.md)"), ["docs/y.md"]);
  assert.deepEqual(found("“docs/x.md”, then"), ["docs/x.md"]);
  assert.deepEqual(found("docs/y.md, docs/z.md; and"), ["docs/y.md", "docs/z.md"]);
});

test("a touch's path is found without the plus sign before it or the symbol after it", () => {
  const text = "+apps/apple/web/src/files.ts";
  const [span] = pathSpans(text);
  assert.equal(span.text, "apps/apple/web/src/files.ts");
  assert.equal(text.slice(span.from, span.to), "apps/apple/web/src/files.ts");
  assert.deepEqual(found("apps/apple/web/src/chat.ts#drawReply"), ["apps/apple/web/src/chat.ts"]);
});

test("a web address is not a file path", () => {
  assert.deepEqual(found("http://127.0.0.1:47370/v1/projects/kyosei/files"), []);
  assert.deepEqual(found("https://example.com/docs/guide.md"), []);
  assert.deepEqual(found("kyosei://kyosei/KYO-736"), []);
});

test("a code span that is one absolute path may hold spaces", () => {
  assert.equal(
    wholePath("/Users/x/Library/Application Support/kyosei/plan.md"),
    "/Users/x/Library/Application Support/kyosei/plan.md");
  assert.equal(wholePath("~/dispatch worktrees/kyo-736/README.md:12"), "~/dispatch worktrees/kyo-736/README.md:12");
  assert.equal(wholePath("/Users/x/plan.md"), null, "one without a space is found the usual way");
  assert.equal(wholePath("cat /etc/hosts"), null);
  assert.equal(wholePath("/usr/bin/git log --oneline"), null);
  assert.equal(wholePath("/bin/cp a.txt /tmp/b.txt"), null);
});

test("a markdown link's address is asked as a path when it has no scheme", () => {
  assert.equal(hrefPath("docs/plan.md"), "docs/plan.md");
  assert.equal(hrefPath("has%20space/a%20file.txt"), "has space/a file.txt");
  assert.equal(hrefPath("src/a.py#L12"), "src/a.py:12");
  assert.equal(hrefPath("src/a.py#L12-L20"), "src/a.py:12");
  assert.equal(hrefPath("README.md#install"), "README.md");
  assert.equal(hrefPath("/Users/x/y.md"), "/Users/x/y.md");
  assert.equal(hrefPath("#install"), null);
  assert.equal(hrefPath("https://example.com/a.md"), null);
  assert.equal(hrefPath("mailto:a@example.com"), null);
  assert.equal(hrefPath("//example.com/a.md"), null);
});

test("a relative link in a file is read against the file's own folder", () => {
  assert.deepEqual(linkPlace("docs", "plan.md"), { kind: "inside", path: "docs/plan.md", line: null });
  assert.deepEqual(linkPlace("docs", "./img/dot.png"), { kind: "inside", path: "docs/img/dot.png", line: null });
  assert.deepEqual(linkPlace("docs", "../README.md#top"), { kind: "inside", path: "README.md", line: null });
  assert.deepEqual(linkPlace("docs/guide", "../../src/a.py#L7"), { kind: "inside", path: "src/a.py", line: 7 });
  assert.deepEqual(linkPlace("", "docs/plan.md"), { kind: "inside", path: "docs/plan.md", line: null });
  assert.deepEqual(linkPlace("docs", "has%20space/a.txt"), { kind: "inside", path: "docs/has space/a.txt", line: null });
});

test("a link that climbs above the root, or is only a fragment, leads nowhere", () => {
  assert.equal(linkPlace("docs", "../../outside.md"), null);
  assert.equal(linkPlace("", "../x.md"), null);
  assert.equal(linkPlace("docs", "#section"), null);
  assert.equal(linkPlace("docs", "https://example.com"), null);
});

test("an absolute link in a file is the server's to place", () => {
  assert.deepEqual(linkPlace("docs", "/Users/x/y.md"), { kind: "ask", given: "/Users/x/y.md" });
  assert.deepEqual(linkPlace("docs", "~/notes/z.md#L3"), { kind: "ask", given: "~/notes/z.md:3" });
});

// ---------------------------------------------------------------------- the server's answers

// The three documents the resolve route returned from a scratch server.
const resolvedFile = {
  found: true, given: "docs/guide.md:3", line: 3, media_type: "text/markdown",
  mtime_ns: 1794337075722407531, name: "guide.md", ok: true, path: "docs/guide.md", root: "code",
  size: 269, text: true, type: "file",
};
const resolvedOutside = {
  detail: "the path lies outside every root this project serves", found: false,
  given: "/Users/x/y.md", line: null, ok: true, path: null, reason: "outside-every-root",
  root: null, roots: ["code", "store"],
};
const resolvedNowhere = {
  detail: "no root holds a file or directory git shows at that path", found: false,
  given: "and/or.x", line: null, ok: true, path: null, reason: "not-found", root: null,
  roots: ["code", "store"],
};

test("a path the server resolves is a file, with its root, path and line", () => {
  const answer = answerOf("docs/guide.md:3", resolvedFile);
  assert.deepEqual(answer, {
    kind: "file", root: "code", path: "docs/guide.md", line: 3, name: "guide.md", type: "file",
    media_type: "text/markdown", text: true, size: 269,
  });
  assert.equal(answer?.kind === "file" && entryOf(answer).path, "docs/guide.md");
});

test("a path outside every root is an outside path, without its line", () => {
  assert.deepEqual(answerOf("/Users/x/y.md", resolvedOutside), { kind: "outside", path: "/Users/x/y.md", line: null });
  assert.deepEqual(
    answerOf("/Users/x/y.md:12", { ...resolvedOutside, given: "/Users/x/y.md:12", line: 12 }),
    { kind: "outside", path: "/Users/x/y.md", line: 12 });
});

test("a path no root holds is not a link, and a refusal is no answer", () => {
  assert.deepEqual(answerOf("and/or.x", resolvedNowhere), { kind: "none" });
  assert.equal(answerOf("docs/guide.md", { ok: false, error: "no", reason: "no-files-permission" }), null);
  assert.equal(answerOf("docs/guide.md", null), null);
});

test("an outside path is drawn only when it reads as a file", () => {
  assert.equal(looksOutside("/Users/x/plans/native-apps.md"), true);
  assert.equal(looksOutside("~/notes/today"), true);
  assert.equal(looksOutside("/Volumes/name/plans"), true);
  assert.equal(looksOutside("/srv/data/report.csv"), true);
  assert.equal(looksOutside("/v1/projects/kyosei/files"), false);
  assert.equal(looksOutside("/cmd/show"), false);
  assert.equal(looksOutside("docs/plan.md"), false);
});

test("a found file stands for the session, and a path nothing held is asked again later", () => {
  const file = answerOf("docs/guide.md:3", resolvedFile)!;
  const outside = answerOf("/Users/x/y.md", resolvedOutside)!;
  assert.equal(stands(file, 0, 10 * NONE_STANDS_MS), true);
  assert.equal(stands(outside, 0, 10 * NONE_STANDS_MS), true);
  assert.equal(stands({ kind: "none" }, 1000, 1000 + NONE_STANDS_MS - 1), true);
  assert.equal(stands({ kind: "none" }, 1000, 1000 + NONE_STANDS_MS), false);
});

// -------------------------------------------------------------- mentioned in this session

const mention = (root: string, path: string, at: string, line: number | null = null) => ({
  root, path, line, at, entry: { name: path.split("/").pop()!, path, type: "file" },
});

test("the files mentioned are listed newest first, each once", () => {
  let list = mentioned([], mention("code", "a.md", "2026-11-10T10:00:00Z"));
  list = mentioned(list, mention("code", "b.md", "2026-11-10T12:00:00Z"));
  list = mentioned(list, mention("store", "a.md", "2026-11-10T11:00:00Z"));
  assert.deepEqual(list.map((item) => `${item.root}:${item.path}`), ["code:b.md", "store:a.md", "code:a.md"]);
  list = mentioned(list, mention("code", "a.md", "2026-11-10T13:00:00Z", 7));
  assert.deepEqual(list.map((item) => `${item.root}:${item.path}`), ["code:a.md", "code:b.md", "store:a.md"]);
  assert.equal(list[0].line, 7);
});

test("an older mention of a file does not displace a newer one", () => {
  let list = mentioned([], mention("code", "a.md", "2026-11-10T13:00:00Z", 7));
  list = mentioned(list, mention("code", "a.md", "2026-11-10T09:00:00Z", 1));
  assert.equal(list.length, 1);
  assert.equal(list[0].line, 7);
});

// ------------------------------------------------------------- a path on this machine

test("on the server's own machine a path is its own", () => {
  assert.equal(localPath("/Users/x/plans/a.md", true, []), "/Users/x/plans/a.md");
  assert.equal(localPath("~/notes/a.md", true, []), "~/notes/a.md");
});

test("on another machine a path has a place only through a mapping", () => {
  assert.equal(localPath("/Users/x/plans/a.md", false, []), null);
  const mapping = [{ server: "/Users/name", here: "/Volumes/name" }];
  assert.equal(localPath("/Users/name/plans/a.md", false, mapping), "/Volumes/name/plans/a.md");
  assert.equal(localPath("/Users/name", false, mapping), "/Volumes/name");
  assert.equal(localPath("/Users/nameon/a.md", false, mapping), null, "a prefix ends at a folder");
  assert.equal(localPath("/etc/hosts", false, mapping), null);
});

test("a mapping is applied before the path is taken as its own, the longest prefix first", () => {
  const mappings = [
    { server: "/Users/name/", here: "/Volumes/name/" },
    { server: "/Users/name/plans", here: "/Volumes/plans" },
    { server: "~", here: "/Volumes/name" },
    { server: "", here: "/nowhere" },
  ];
  assert.equal(localPath("/Users/name/plans/a.md", true, mappings), "/Volumes/plans/a.md");
  assert.equal(localPath("/Users/name/b.md", true, mappings), "/Volumes/name/b.md");
  assert.equal(localPath("~/notes/a.md", false, mappings), "/Volumes/name/notes/a.md");
  assert.equal(localPath("/tmp/c.md", true, mappings), "/tmp/c.md");
});

test("a served file's path on the server's machine is its root's path and its own", () => {
  const roots = servedRoots(rootsDocument);
  assert.equal(serverPath(roots, "store", "docs/proposals/a.md"), "/work/store/docs/proposals/a.md");
  assert.equal(serverPath(roots, "code", ""), "/work/demo");
  assert.equal(serverPath(roots, "scratch", "a.md"), null);
});
