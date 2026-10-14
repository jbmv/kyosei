// KYO-230: `kyosei://<project>/<id>` — the scheme, the resolver, the interception.
//
// Two kinds of check in one file, and they are separated by a heading rather than by
// two files because they are one subject and the second is meaningless without the
// first:
//
//   LAUNCHES NOTHING. The grammar. `links.ts` is Electron-free on purpose, so every
//   id shape, every refusal and the whole URL parse are driven directly and fail with
//   a line number. This is where the claim "one resolver, and it knows every shape the
//   CLI mints" is actually made -- including against `src/kyosei/ids.py` itself, so
//   the day the CLI adds a child kind this goes red instead of a link going quiet.
//
//   LAUNCHES THE APP. The wiring. That the scheme is claimed, that a link on the
//   launch's argv is followed, and that a `kyosei://` link inside the claude view is
//   taken by this app rather than handed to a browser.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { _electron, type ElectronApplication, expect, type Page, test } from "@playwright/test";
import {
  CHILD_KIND_BY_LETTER,
  decideClaudeClick,
  decideClaudeLink,
  followLink,
  ID_SHAPES,
  isKyoseiUrl,
  type KnownProject,
  MAX_CLICKED_TEXT,
  MAX_REFERENCES,
  MAX_SCANNED_TEXT,
  parseKyoseiUrl,
  referencesInText,
  resolveId,
  shapesItKnows,
  URL_SCHEME,
  urlFromArgv,
} from "../src/main/links";
import {
  closeReadyApp,
  type Fixture,
  MACOS_NO_RESTORE,
  makeFixture,
  markReadyToClose,
  openKind,
  removeFixture,
  suiteEnv,
  testResolution,
  windowShowArg,
} from "./fixture";

const APP_ROOT = join(__dirname, "..");

/** The projects a link may name in the checks below. */
const KNOWN: KnownProject[] = [
  { prefix: "FIX", root: "/tmp/somewhere/a project", connection: null, current: true },
  { prefix: "DM2", root: "/tmp/elsewhere/demo-app", connection: null, current: false },
];

// ============================================================= the grammar (no app)

test("the resolver maps every id shape the CLI mints, and says so about the rest", () => {
  // A record: the page itself, no anchor.
  const record = resolveId("KYO-12");
  expect(record.ok).toBe(true);
  if (record.ok) {
    expect(record.target).toEqual({
      kind: "record",
      id: "KYO-12",
      anchor: null,
      anchorKind: null,
      asWritten: "KYO-12",
    });
  }

  // Every child letter, each resolving to its OWNING record with itself as the
  // anchor and its kind spelled out -- which is what saves the renderer from parsing
  // an id to find out which section to scroll to.
  for (const [letter, kind] of Object.entries(CHILD_KIND_BY_LETTER)) {
    const id = `KYO-226.${letter}3`;
    const resolved = resolveId(id);
    expect(resolved.ok, `${id} did not resolve`).toBe(true);
    if (!resolved.ok) continue;
    expect(resolved.target.kind).toBe("record");
    expect(resolved.target.id).toBe("KYO-226");
    expect(resolved.target.anchor).toBe(id);
    expect(resolved.target.anchorKind).toBe(kind);
  }

  // The two run ids that have a page of their own.
  const note = resolveId("KYO-S12");
  expect(note.ok && note.target.kind).toBe("shift-note");
  const brief = resolveId("KYO-B9");
  expect(brief.ok && brief.target.kind).toBe("brief");
  // ...and they are not read as records, which is the one ordering this table needs.
  expect(note.ok && note.target.id).toBe("KYO-S12");

  // The shapes that are REAL and have no page. Refused as `not-openable`, never
  // folded into "I do not know what that is": a link id names an edge between two
  // records and an event id names a ledger row, so there is no single page either
  // could open, and a reader deserves to be told which of the two answers they got.
  for (const id of ["KYO-L7", "KYO-E41", "KYO-W3"]) {
    const resolved = resolveId(id);
    expect(resolved.ok, `${id} resolved to a page`).toBe(false);
    if (resolved.ok) continue;
    expect(resolved.outcome).toBe("not-openable");
    expect(resolved.refusal).toContain(id);
  }

  // And an unknown shape is refused NAMING the shapes it knows, generated from the
  // same table the matcher walks -- so the list cannot go stale against it.
  for (const nonsense of ["KYO", "kyo-12", "KYO-0", "KYO-12.z1", "KYO-12.c0", "12", ""]) {
    const resolved = resolveId(nonsense);
    expect(resolved.ok, `${JSON.stringify(nonsense)} resolved`).toBe(false);
    if (resolved.ok) continue;
    expect(resolved.outcome).toBe("unknown-shape");
    for (const shape of ID_SHAPES) expect(resolved.refusal).toContain(shape.example);
  }
  expect(shapesItKnows()).toContain("KYO-12.c3");
});

test("the child letters this app knows are the CLI's own, letter for letter", () => {
  // The one oracle available for a vocabulary that lives in another language. There
  // is no importing a Python dict, so the copy in `links.ts` is checked against the
  // source it was copied from -- `kyosei.ids.CHILD_KIND_BY_LETTER` -- read out of the
  // checkout the app's own resolver found. KYO-137 is the precedent that makes this
  // worth having: it added two child kinds and had to remember to widen a regex
  // somewhere else, which is exactly the drift a second copy invites.
  const cliRoot = testResolution().cliRoot;
  expect(cliRoot, "no checkout to compare against").not.toBeNull();
  const source = readFileSync(join(cliRoot as string, "src", "kyosei", "ids.py"), "utf8");
  const block = /CHILD_KIND_BY_LETTER = \{([^}]*)\}/.exec(source)?.[1] ?? "";
  expect(block, "kyosei/ids.py no longer declares CHILD_KIND_BY_LETTER as a literal").not.toBe("");
  const theirs: Record<string, string> = {};
  for (const match of block.matchAll(/"([a-z])":\s*"([a-z-]+)"/g)) {
    theirs[match[1] as string] = match[2] as string;
  }
  expect(Object.keys(theirs).length).toBeGreaterThan(0);
  expect(CHILD_KIND_BY_LETTER).toEqual(theirs);
});

test("a link is a project and one id, and anything else is refused rather than guessed", () => {
  const good = parseKyoseiUrl(`${URL_SCHEME}://FIX/KYO-12.c3`);
  expect(good.ok).toBe(true);
  if (good.ok) {
    expect(good.url).toEqual({ project: "FIX", id: "KYO-12.c3", column: "active" });
  }

  // A column the link asked for, and an unrecognised one falling back to `active`
  // rather than refusing: which column a tab opens in is a preference, and a link
  // that would not open at all because of one is worse than one that opens here.
  expect(parseKyoseiUrl(`${URL_SCHEME}://FIX/KYO-12?column=other`)).toMatchObject({
    url: { column: "other" },
  });
  expect(parseKyoseiUrl(`${URL_SCHEME}://FIX/KYO-12?column=sideways`)).toMatchObject({
    url: { column: "active" },
  });

  // Percent-encoding, because a URL that has been through the operating system or
  // through Chromium has been encoded by one of them.
  expect(parseKyoseiUrl(`${URL_SCHEME}://a%20project/KYO-12`)).toMatchObject({
    url: { project: "a project" },
  });

  // Not ours: left alone, and named as such rather than reported as broken.
  const foreign = parseKyoseiUrl("https://claude.ai/code");
  expect(foreign.ok).toBe(false);
  if (!foreign.ok) expect(foreign.outcome).toBe("not-our-scheme");
  expect(isKyoseiUrl("https://claude.ai/code")).toBe(false);
  expect(isKyoseiUrl(` KYOSEI://FIX/KYO-1`)).toBe(true);

  // Ours and malformed: a project with no id, and -- the one worth stating -- a link
  // with MORE path than this app understands. Opening its first segment would be the
  // app acting on a link it does not understand, which is how a reader ends up on a
  // page that is nearly what they clicked.
  for (const raw of [
    `${URL_SCHEME}://FIX`,
    `${URL_SCHEME}:///KYO-1`,
    `${URL_SCHEME}://FIX/KYO-1/c3`,
  ]) {
    const parsed = parseKyoseiUrl(raw);
    expect(parsed.ok, `${raw} parsed`).toBe(false);
    if (!parsed.ok) expect(parsed.outcome).toBe("malformed");
  }
});

test("a link for a project this app does not know is refused, naming the ones it does", () => {
  // Matched on the store's own prefix OR the project directory's name, either case:
  // both are what a person writing a link by hand reaches for.
  expect(followLink(`${URL_SCHEME}://FIX/FIX-1`, KNOWN, "open-url")).toMatchObject({
    ok: true,
    request: { projectRoot: "/tmp/somewhere/a project" },
  });
  expect(followLink(`${URL_SCHEME}://demo-app/DM2-25`, KNOWN, "open-url")).toMatchObject({
    ok: true,
    request: { projectRoot: "/tmp/elsewhere/demo-app" },
  });
  expect(followLink(`${URL_SCHEME}://dm2/DM2-25`, KNOWN, "open-url")).toMatchObject({ ok: true });

  // And one it does not know is refused -- never opened against whatever happens to
  // be open, which would put a reader in front of the right id in the wrong project
  // with nothing on screen saying so.
  const refused = followLink(`${URL_SCHEME}://someone-elses/ABC-1`, KNOWN, "open-url");
  expect(refused.ok).toBe(false);
  if (!refused.ok) {
    expect(refused.outcome).toBe("unknown-project");
    expect(refused.refusal).toContain("someone-elses");
    expect(refused.refusal).toContain("FIX");
    expect(refused.refusal).toContain("/tmp/elsewhere/demo-app");
  }

  // With no project open at all, the refusal says that rather than listing nothing.
  const nothing = followLink(`${URL_SCHEME}://FIX/FIX-1`, [], "open-url");
  expect(nothing.ok).toBe(false);
  if (!nothing.ok) expect(nothing.refusal).toContain("no project open");

  // The whole request, so the fields the renderer reads are asserted once somewhere.
  const followed = followLink(`${URL_SCHEME}://FIX/FIX-1.g2?column=new`, KNOWN, "claude-view");
  expect(followed).toEqual({
    ok: true,
    refusal: null,
    request: {
      target: {
        kind: "record",
        id: "FIX-1",
        anchor: "FIX-1.g2",
        anchorKind: "gate",
        asWritten: "FIX-1.g2",
      },
      column: "new",
      projectRoot: "/tmp/somewhere/a project",
      connection: null,
      choice: null,
      url: `${URL_SCHEME}://FIX/FIX-1.g2?column=new`,
      source: "claude-view",
    },
  });
});

// ------------------------------- a project is (server, name), links included (KYO-463)
//
// A laptop can know two copies of one project: its own older copy, read through this
// machine's kyo serve, and a remote machine's, read through that machine's kyo serve.
// Same prefix, same root path. A link followed from a window on the remote copy must
// open the remote copy -- before KYO-463 every link path matched on the prefix alone,
// found the local copy, and opened a local window on it.

const REMOTE = { host: "devbox.tailnet.ts.net", port: 443 };
const SAME_ROOT = "/Users/me/coding-projects/kyosei";

/** The laptop's view: its own copy and the remote one, `current` on the asker's. */
function laptop(asker: "local" | "remote" | "none"): KnownProject[] {
  return [
    { prefix: "KYO", root: SAME_ROOT, connection: null, current: asker === "local" },
    { prefix: "KYO", root: SAME_ROOT, connection: REMOTE, current: asker === "remote" },
  ];
}

test("a link followed from a window on a server opens that server's project, never a local one", () => {
  // KYO-463.c2: the Electron-free check that fails if a remote window's link resolves
  // to a local project. Every link path goes through `followLink` (Activity, docs,
  // records, the terminal) or `decideClaudeLink`/`decideClaudeClick` (the claude view).
  const url = `${URL_SCHEME}://KYO/KYO-399.d1`;
  const followed = followLink(url, laptop("remote"), "in-app");
  expect(followed.ok).toBe(true);
  if (followed.ok) {
    expect(followed.request.connection).toEqual(REMOTE);
    expect(followed.request.projectRoot).toBe(SAME_ROOT);
  }
  const clicked = decideClaudeLink(url, laptop("remote"));
  expect(clicked.action).toBe("open-in-app");
  if (clicked.action === "open-in-app" && clicked.delivery.ok) {
    expect(clicked.delivery.request.connection).toEqual(REMOTE);
  } else {
    throw new Error("the claude view's link was not followed");
  }
  const text = decideClaudeClick(
    { senderId: 7, claudeViewIds: [7], text: "KYO-399.d1" },
    laptop("remote"),
  );
  expect(text.action).toBe("open");
  if (text.action === "open") expect(text.reference.project.connection).toEqual(REMOTE);

  // And from a window on this machine's copy, the local copy -- the preference is the
  // asking window's server, not "remote wins".
  const local = followLink(url, laptop("local"), "in-app");
  expect(local.ok && local.request.connection).toBeNull();
});

test("a window on a server prefers that server for a project it is not on, and the choice is said", () => {
  // KYO-463.c3. The asking window is on the remote machine's OTHER project; the link names KYO,
  // served both here and there. It opens through the asking window's connection, and
  // the request says which one it chose and which it passed over.
  const known: KnownProject[] = [
    { prefix: "DEMO", root: "/Users/me/demo", connection: REMOTE, current: true },
    { prefix: "KYO", root: SAME_ROOT, connection: null, current: false },
    { prefix: "KYO", root: SAME_ROOT, connection: REMOTE, current: false },
  ];
  const followed = followLink(`${URL_SCHEME}://KYO/KYO-12`, known, "in-app");
  expect(followed.ok).toBe(true);
  if (!followed.ok) return;
  expect(followed.request.connection).toEqual(REMOTE);
  expect(followed.request.choice).toContain("https://devbox.tailnet.ts.net");
  expect(followed.request.choice).toContain("this machine's kyo serve");

  // A link from outside the app, with no window asking: it still resolves (the first
  // known project in the order given), and it SAYS which connection it chose.
  const outside = followLink(`${URL_SCHEME}://KYO/KYO-12`, laptop("none"), "open-url");
  expect(outside.ok).toBe(true);
  if (outside.ok) {
    expect(outside.request.choice).toMatch(/^opened through /);
    expect(outside.request.choice).toContain("also served by");
  }

  // One connection serving the project is no choice at all, and says nothing.
  const only = followLink(`${URL_SCHEME}://FIX/FIX-1`, KNOWN, "open-url");
  expect(only.ok && only.request.choice).toBeNull();
});

test("only kyosei links are taken from the claude view; everything else is the browser's", () => {
  // The decision both Chromium hooks share. `claude.ai` -- and every page the reader
  // walks to from it -- is left alone: this view renders a first-party site and this
  // app has no business deciding where else it may go.
  for (const url of ["https://claude.ai/code", "https://example.invalid", "mailto:a@b.c"]) {
    expect(decideClaudeLink(url, KNOWN)).toEqual({ action: "let-the-browser-have-it" });
  }
  const ours = decideClaudeLink(`${URL_SCHEME}://FIX/FIX-1.c1`, KNOWN);
  expect(ours.action).toBe("open-in-app");

  // A `kyosei://` link that cannot be followed is STILL taken, carrying its refusal.
  // The click was unambiguously meant for this app, so telling the reader why nothing
  // opened is the answer -- and handing a malformed link to a browser is not.
  const broken = decideClaudeLink(`${URL_SCHEME}://nobody/QQ-1`, KNOWN);
  expect(broken.action).toBe("open-in-app");
  if (broken.action === "open-in-app") expect(broken.delivery.ok).toBe(false);
});

// ------------------------------------------ what a message's TEXT names (KYO-263.c5)
//
// claude.ai keeps a `kyosei:` markdown link's words and drops its href, so what the
// reader clicks is an anchor with text and no target and the two hooks above never
// fire. The preload sends that text here. These drive the pure function that reads it:
// text in, targets out, no Chromium.

test("a bare id in a message names the record its own prefix belongs to", () => {
  const found = referencesInText("done in FIX-12, see FIX-12.c3 for why", KNOWN);
  expect(found.map((one) => one.asWritten)).toEqual(["FIX-12", "FIX-12.c3"]);
  expect(found[0]?.form).toBe("bare-id");
  expect(found[0]?.project.root).toBe("/tmp/somewhere/a project");
  // The child id keeps its anchor, exactly as a clicked link would.
  expect(found[1]?.target).toEqual({
    kind: "record",
    id: "FIX-12",
    anchor: "FIX-12.c3",
    anchorKind: "criterion",
    asWritten: "FIX-12.c3",
  });

  // ...and the URL it hands on is one `followLink` takes, unchanged. This is the whole
  // claim that the menu opens records by the same path every other link does: if these
  // two ever disagree, the entry would open something else or nothing.
  for (const reference of found) {
    const delivery = followLink(reference.url, KNOWN, "claude-view");
    expect(delivery.ok, `${reference.url} is not a link this app follows`).toBe(true);
    if (delivery.ok) expect(delivery.request.target).toEqual(reference.target);
  }
});

test("a bare id for a project this app does not know resolves to nothing", () => {
  // The one guess this must never make. `ZZZ-1` is a well-formed id, and opening it
  // against whichever project happens to be in front of the reader is the failure
  // `matchProject` exists to prevent -- so it resolves to nothing at all.
  expect(referencesInText("ZZZ-1 and QQ-4.c2 are elsewhere", KNOWN)).toEqual([]);
  // A project this app knows but is not looking at IS resolved, against ITS root.
  const other = referencesInText("DM2-9 is the other one", KNOWN);
  expect(other.map((one) => one.project.root)).toEqual(["/tmp/elsewhere/demo-app"]);
});

test("a kyosei:// URL that survived as text is read as the URL it is", () => {
  const found = referencesInText(
    `I filed it: ${URL_SCHEME}://FIX/FIX-3.g1?column=new. Also ${URL_SCHEME}://FIX/FIX-4`,
    KNOWN,
  );
  expect(found.map((one) => one.form)).toEqual(["url", "url"]);
  expect(found[0]?.url).toBe(`${URL_SCHEME}://FIX/FIX-3.g1?column=new`);
  // The sentence's full stop is not part of the URL; the dot in `FIX-3.g1` is.
  expect(found[0]?.asWritten.endsWith(".")).toBe(false);
  expect(found[0]?.target.anchor).toBe("FIX-3.g1");
  expect(followLink(found[0]?.url ?? "", KNOWN, "claude-view").request?.column).toBe("new");
  // The id inside a URL is not ALSO offered as a bare id: one click, one entry.
  expect(found).toHaveLength(2);
});

test("text that names nothing this app can open produces no entries", () => {
  for (const text of [
    "",
    "no ids here at all",
    // Recognised shapes with no page in this app: a typed link, a ledger event, a
    // sweep run. `resolveId` says so and the menu stays empty rather than offering an
    // entry that would only explain itself.
    "FIX-L7 FIX-E41 FIX-W3",
    // A well-formed id inside a longer token is not a reference to anything.
    "XFIX-12 FIX-12a FIX-012",
  ]) {
    expect(referencesInText(text, KNOWN), `"${text}" named something`).toEqual([]);
  }
});

test("text is scanned to a bound, and names a bounded number of records", () => {
  // The text is whatever a remote page put inside an element, and this runs on the
  // main process's thread between the click and the record opening.
  const far = `${"x".repeat(MAX_SCANNED_TEXT)} FIX-99`;
  expect(referencesInText(far, KNOWN)).toEqual([]);
  expect(referencesInText(`FIX-99 ${far}`, KNOWN).map((one) => one.asWritten)).toEqual(["FIX-99"]);

  const many = Array.from({ length: MAX_REFERENCES + 4 }, (_, index) => `FIX-${index + 1}`);
  const capped = referencesInText(many.join(" "), KNOWN);
  expect(capped).toHaveLength(MAX_REFERENCES);
  // The first ones in the text, not an arbitrary subset.
  expect(capped.map((one) => one.asWritten)).toEqual(many.slice(0, MAX_REFERENCES));
  // The same id twice is one entry.
  expect(referencesInText("FIX-1 and FIX-1 again", KNOWN)).toHaveLength(1);
});

// --------------------------- what the preload's click is worth, on the main side (c5)
//
// The message arrives from a page this app did not write, so every arm of the decision
// is driven here: the sender, the payload's type, the payload's length, and what it
// names. No Chromium, so each one fails with a line number.

/** The claude view of this app, as the handler sees it: one contents id. */
const CLAUDE_VIEW = 42;

test("a click reported by the claude view's preload opens the record its text names", () => {
  // The element measured on the live page (KYO-263) carries exactly this text.
  const decision = decideClaudeClick(
    { senderId: CLAUDE_VIEW, claudeViewIds: [CLAUDE_VIEW], text: "FIX-1.c1" },
    KNOWN,
  );
  expect(decision.action).toBe("open");
  if (decision.action !== "open") return;
  expect(decision.reference.form).toBe("bare-id");
  expect(decision.reference.target.anchor).toBe("FIX-1.c1");
  // ...and the URL it hands on is one `followLink` takes, unchanged, which is the whole
  // claim that a click in claude.ai opens a record by the same path every link does.
  const delivery = followLink(decision.reference.url, KNOWN, "claude-view");
  expect(delivery.ok).toBe(true);
  expect(delivery.request?.target).toEqual(decision.reference.target);

  // A `kyosei://` URL as the anchor's own text is read as the URL it is, `?column=`
  // and all -- the markdown link's text is whatever the writer put in the brackets.
  const asUrl = decideClaudeClick(
    {
      senderId: CLAUDE_VIEW,
      claudeViewIds: [CLAUDE_VIEW],
      text: `${URL_SCHEME}://FIX/FIX-1?column=new`,
    },
    KNOWN,
  );
  expect(asUrl.action === "open" && asUrl.reference.form).toBe("url");
});

test("a click from anywhere but a claude view of this app is dropped, by name", () => {
  // The sender check is the boundary KYO-321 states: `ipcMain` is per process, so this
  // channel is reachable from every renderer in the app, and exactly one contents may
  // be believed on it. A shell window's renderer is not it, and neither is a claude
  // view that has gone away.
  for (const [senderId, viewIds] of [
    [7, [CLAUDE_VIEW]],
    [CLAUDE_VIEW, []],
  ] as const) {
    const decision = decideClaudeClick({ senderId, claudeViewIds: viewIds, text: "FIX-1" }, KNOWN);
    expect(decision.action).toBe("drop");
    if (decision.action === "drop") expect(decision.why).toContain(String(senderId));
  }
});

test("text that names no record, and text that is not text, are dropped in silence", () => {
  const click = (text: unknown) =>
    decideClaudeClick({ senderId: CLAUDE_VIEW, claudeViewIds: [CLAUDE_VIEW], text }, KNOWN);
  for (const text of [
    // Every element the preload reports is an anchor with no href, and in claude.ai's
    // own chrome that is a fair number of things that name no record at all.
    "Copy",
    "",
    "   ",
    // A shape with no page here, and an id belonging to no project this app knows.
    "FIX-L7",
    "ZZZ-1",
  ]) {
    expect(click(text).action, JSON.stringify(text)).toBe("drop");
  }
  // Not a string: the payload is whatever reached `ipcMain`, and a renderer sends what
  // it likes. Named, rather than thrown or coerced.
  for (const wrong of [undefined, null, 12, { text: "FIX-1" }, ["FIX-1"]]) {
    const decision = click(wrong);
    expect(decision.action).toBe("drop");
    if (decision.action === "drop") expect(decision.why).toContain("not a string");
  }
  // And the cap is applied HERE, not only in the preload: a cap inside a renderer is
  // one a renderer could be made to skip.
  const far = `${"x".repeat(MAX_CLICKED_TEXT)} FIX-1`;
  expect(click(far).action).toBe("drop");
  expect(click(`FIX-1 ${"x".repeat(MAX_CLICKED_TEXT)}`).action).toBe("open");
});

test("a link on the launch's own argv is found, and nothing else is", () => {
  expect(urlFromArgv(["electron", ".", `${URL_SCHEME}://FIX/FIX-1`])).toBe(
    `${URL_SCHEME}://FIX/FIX-1`,
  );
  expect(urlFromArgv(["electron", ".", "--kyosei-window-show=hidden"])).toBeNull();
});

// ========================================================== the wiring (the real app)

let fixture: Fixture;
let app: ElectronApplication | null = null;
/** Everything the app printed this launch: `deliverLink` narrates on stdout. */
let said: string[] = [];

test.beforeAll(async () => {
  fixture = await makeFixture({ records: 1 });
});

test.afterAll(() => {
  removeFixture(fixture);
});

test.afterEach(async () => {
  if (app !== null) {
    await closeReadyApp(app);
    app = null;
  }
});

function envFor(): Record<string, string> {
  const env = suiteEnv();
  env.KYOSEI_PROJECT_ROOT = fixture.root;
  env.KYOSEI_HOME = fixture.home;
  // This file is about links, not about panes: a pane that attached to somebody's
  // real tmux would be a live session in a check that never looks at one.
  env.KYOSEI_TMUX = "off";
  // ...and no login-shell capture either, for the same reason: it costs a shell
  // start-up per launch and nothing here reads the environment it produces.
  env.KYOSEI_LOGIN_SHELL = "off";
  return env;
}

async function launch(extraArgs: string[] = []): Promise<Page> {
  said = [];
  app = await _electron.launch({
    args: [APP_ROOT, ...MACOS_NO_RESTORE, windowShowArg(), ...extraArgs],
    env: envFor(),
  });
  // The app's own stdout, which is where `deliverLink` says what it opened. Read here
  // rather than through a test-only hook on `globalThis`: the line is a real thing the
  // app prints for a human debugging a link that went nowhere, so a check reading it
  // is checking the product rather than an instrument built for the check.
  app.process().stdout?.on("data", (chunk: Buffer) => said.push(chunk.toString()));
  const page = await app.firstWindow();
  await page.waitForSelector('body[data-ready="true"]');
  markReadyToClose(app);
  return page;
}

function saidSoFar(): string {
  return said.join("");
}

/**
 * The page the claude view is given, and it is not a look-alike.
 *
 * The third element is copied from a controller message as claude.ai/code rendered it
 * (KYO-263), with the ids changed to the fixture project's and nothing else: a markdown
 * link that the site left as an anchor with NO href, a `kyosei://` URL that survived as
 * plain text, and a bare id.
 * That is what the gesture has to work on, so it is what the check clicks.
 *
 * Served by this check rather than by the app: `data:` is loaded over CDP, so no
 * production seam takes a URL and nothing points a logged-in view anywhere. The view
 * itself is the real one -- `claudeViewFor`'s `webPreferences`, partition, hooks and
 * preload -- because it is the view the app built when the tab was opened.
 */
const CLAUDE_PAGE =
  'data:text/html,<a id="deep" href="kyosei://FIX/FIX-1.g1">go</a>' +
  '<button id="pop" onclick="window.open(\'kyosei://FIX/FIX-1.d1\')">pop</button>' +
  '<ol dir="ltr" start="1"><li>A markdown link: ' +
  '<a target="_blank" rel="noreferrer">FIX-1.c1</a></li>' +
  "<li>A bare URL: kyosei://FIX/FIX-4</li><li>A bare id: FIX-5</li></ol>";

test("the app claims the scheme, and follows a link handed to it on launch", async () => {
  // The fixture store's prefix is `FIX` and `kyo new bug` made `FIX-1`; the link
  // names a child of it, so what is asserted is the anchor as well as the page.
  await launch([`${URL_SCHEME}://FIX/FIX-1.c1`]);

  // Claimed at run time, which is the half a development `electron .` needs: it has
  // no `Info.plist` of its own. The packed half is `build.mjs`'s `protocols` option,
  // read back out of the built bundle by `verifyBundle` and pinned in `seams.spec.ts`.
  const claimed = await (app as ElectronApplication).evaluate(
    ({ app: electronApp }, scheme: string) => electronApp.isDefaultProtocolClient(scheme),
    URL_SCHEME,
  );
  expect(claimed).toBe(true);

  await expect.poll(saidSoFar, { timeout: 20_000 }).toContain("open record FIX-1");
  expect(saidSoFar()).toContain("criterion FIX-1.c1");
  expect(saidSoFar()).toContain("from launch-argument");
});

// ==================================================================== KYO-240.c1
//
// The other half of the check above, and the half that was missing: the link does not
// only RESOLVE, it opens a tab.
//
// The two are genuinely different facts and the gap between them is where the bug
// lived. `deliverLaunchLink` sends the delivery on the window's `ready-to-show`, and
// `ipcRenderer` drops an event that reaches a channel nobody is listening on, so a
// shell that subscribed after `await bridge.getLayout()` printed the line above and
// opened nothing (KYO-233.d1, whose own version of this check passed alone four times
// and failed inside a full run). `shell.ts` now subscribes before its first await and
// holds the delivery until the workspace is on screen.
//
// NOT LOAD-SENSITIVE, which is the other half of the criterion: every wait below is a
// wait for the delivery to have happened -- `launch` waits for `body[data-ready]`, and
// `toHaveCount` polls for the tab -- and there is no sleep, no fixed delay, and no
// assertion that something has NOT happened yet. On a machine that takes ten seconds
// to get the window up, this check takes ten seconds and passes.
test("a link on the launch's own argv opens its tab, not only its log line", async () => {
  const page = await launch([`${URL_SCHEME}://FIX/FIX-1.c1`]);

  // The tab the link asked for: a record tab on FIX-1, opened by a delivery that
  // arrived before `start()` had finished building the workspace.
  const tab = page.locator(
    '[data-region="tab-body"][data-tab-kind="record"][data-tab-arg="FIX-1"]',
  );
  await expect(tab, "the launch link resolved and opened no tab").toHaveCount(1, {
    timeout: 60_000,
  });

  // ...and the "you were sent here" strip, which is how the reader knows a link put
  // them where they are rather than their own click (KYO-227).
  const strip = page.locator('[data-region="link-strip"]');
  await expect(strip).toBeVisible();
  await expect(strip).toHaveAttribute("data-tone", "sent-here");
  await expect(strip).toContainText("FIX-1");

  // The main process's own line, so a failure here says which side it came from: a
  // missing line is a resolver problem, a line with no tab is a renderer problem.
  expect(saidSoFar()).toContain("from launch-argument");
});

test("a link that arrives while the app is running is followed, on the same path", async () => {
  await launch();
  // `open-url` is how macOS hands a URL to an app that is ALREADY running -- a link
  // clicked in Chrome, in a terminal, in another app -- and it is the surface a check
  // cannot get at from outside without asking the operating system to open a URL, which
  // would reach whichever copy of this app `launchd` decided to use.
  //
  // So the event is emitted on Electron's own `app` object, from inside the app, over
  // CDP. What that exercises is this app's registered listener and everything past it:
  // the `preventDefault`, the project match, the resolver and the delivery. What it
  // does NOT exercise is macOS's delivery of the event, which belongs to macOS.
  await (app as ElectronApplication).evaluate(({ app: electronApp }, url: string) => {
    electronApp.emit("open-url", { preventDefault: () => {} }, url);
  }, `${URL_SCHEME}://FIX/FIX-1.n2?column=other`);

  await expect.poll(saidSoFar, { timeout: 20_000 }).toContain("open record FIX-1");
  expect(saidSoFar()).toContain("note FIX-1.n2");
  // The column the link asked for, carried through to the request rather than dropped.
  expect(saidSoFar()).toContain("in the other column");
  expect(saidSoFar()).toContain("from open-url");
});

test("a link for a project the app does not know is refused, in the app, by name", async () => {
  await launch([`${URL_SCHEME}://not-this-one/ZZZ-9`]);
  await expect
    .poll(saidSoFar, { timeout: 20_000 })
    .toContain("link not followed (unknown-project)");
  // The refusal names what the app DOES know, which is the remedy: open that project.
  expect(saidSoFar()).toContain("FIX");
});

test("a kyosei link clicked inside the claude view is opened in-app, never in a browser", async () => {
  const page = await launch();
  // The view is created lazily, when a renderer first asks to show one.
  // KYO-232: the claude session is a TAB now, opened from the rail.
  await openKind(page, "claude");
  await expect(page.locator('[data-region="claude-frame"]')).toBeVisible();

  // KYO-263.c6, where it is used: the gesture is a click, and the tab says so. Nothing
  // else in the app can -- the element the reader clicks is inside claude.ai's page, it
  // looks like a link that does nothing, and a reader who is not told has no way to
  // discover that it now opens the record.
  const hint = page.locator('[data-region="claude-open-hint"]');
  await expect(hint).toHaveText("click a linked record id to open it");
  await expect(hint).toHaveAttribute("data-tip", /markdown link/);

  // The view's own contents, found by ORIGIN: this app's renderers are `file:` and
  // the view's page is not. The same rule `poppedPaneWindow` uses in `smoke.spec.ts`,
  // and for the same reason -- the two are indistinguishable by arrival order.
  const findView = (): Promise<number | null> =>
    (app as ElectronApplication).evaluate(({ webContents }) => {
      const view = webContents
        .getAllWebContents()
        .find((contents) => !contents.getURL().startsWith("file://") && !contents.isDestroyed());
      return view === undefined ? null : view.id;
    });
  await expect.poll(findView, { timeout: 20_000 }).not.toBeNull();
  const viewId = (await findView()) as number;

  // What is under test is what happens when a page INSIDE that view tries to reach a
  // `kyosei://` link, and what happens when the reader clicks what claude.ai actually
  // renders. `CLAUDE_PAGE` above says where its markup came from.
  //
  // The view is still fetching claude.ai when this arrives on a loaded machine, and a
  // second load racing the first ends with whichever lands last on screen -- so the
  // first is waited out, the data page is loaded, and its arrival is what is asserted.
  await expect
    .poll(
      () =>
        (app as ElectronApplication).evaluate(
          ({ webContents }, id: number) => webContents.fromId(id)?.isLoading() ?? true,
          viewId,
        ),
      { timeout: 30_000 },
    )
    .toBe(false);
  await (app as ElectronApplication).evaluate(
    async ({ webContents }, [id, page]: [number, string]) => {
      const view = webContents.fromId(id);
      await view?.loadURL(page).catch(() => undefined);
    },
    [viewId, CLAUDE_PAGE] as [number, string],
  );
  // Not "is the URL the data one" -- `getURL` can say so before the page's document
  // is the one that URL names -- but "is the page's own element there".
  // ...and asked for again if it has not arrived: on a loaded machine a load can be
  // superseded by the site's own late navigation, and one more ask lands.
  await expect
    .poll(
      () =>
        (app as ElectronApplication).evaluate(
          async ({ webContents }, [id, page]: [number, string]) => {
            const view = webContents.fromId(id);
            if (view === undefined) return false;
            const there = await view
              .executeJavaScript("!!document.getElementById('pop')")
              .catch(() => false);
            if (there === true) return true;
            if (!view.isLoading()) void view.loadURL(page).catch(() => undefined);
            return false;
          },
          [viewId, CLAUDE_PAGE] as [number, string],
        ),
      { timeout: 30_000, intervals: [500, 1000, 2000] },
    )
    .toBe(true);

  // Both Chromium paths a link can take, driven in the page itself. `window.open`
  // reaches `setWindowOpenHandler`; a plain anchor click reaches `will-navigate`.
  // Both are registered on this view, and what this asserts is that the app took the
  // link rather than handing it to `shell.openExternal`.
  const listeners = await (app as ElectronApplication).evaluate(
    ({ webContents }, id: number) => webContents.fromId(id)?.listenerCount("will-navigate") ?? 0,
    viewId,
  );
  expect(listeners, "the claude view has no will-navigate listener").toBeGreaterThan(0);

  await (app as ElectronApplication).evaluate(
    ({ webContents }, id: number) =>
      webContents.fromId(id)?.executeJavaScript("document.getElementById('pop').click()"),
    viewId,
  );
  await expect.poll(saidSoFar, { timeout: 20_000 }).toContain("open record FIX-1");
  expect(saidSoFar()).toContain("from claude-view");

  await (app as ElectronApplication).evaluate(
    ({ webContents }, id: number) =>
      webContents.fromId(id)?.executeJavaScript("document.getElementById('deep').click()"),
    viewId,
  );
  await expect.poll(saidSoFar, { timeout: 20_000 }).toContain("gate FIX-1.g1");

  // The view never left the page it was on: the link was taken here, not navigated to
  // and not handed out. A `shell.openExternal` would have round-tripped through the
  // operating system and back into this same app, which "works" and is not this.
  const stillHere = await (app as ElectronApplication).evaluate(
    ({ webContents }, id: number) => webContents.fromId(id)?.getURL() ?? "",
    viewId,
  );
  expect(stillHere.startsWith("data:")).toBe(true);

  // ------------------------------------------------- the third path (KYO-263.c5)
  //
  // Two real anchors have been clicked by now and BOTH have an href, so the preload
  // has had two chances to report a click it must leave alone. It reported neither:
  // an anchor with an href is a working link, handled by the page or by
  // `will-navigate`, and taking it would take a link away.
  expect(saidSoFar(), "the preload reported a click on an anchor that HAS an href").not.toContain(
    "claude click",
  );

  // The gesture itself, on the element claude.ai actually renders (KYO-263): an
  // anchor with the words, no href, and a record id inside it. A left click on it did
  // nothing in the page before this record and does nothing in the page now -- what
  // changed is that the preload reads its text and the record opens here.
  await (app as ElectronApplication).evaluate(
    ({ webContents }, id: number) =>
      webContents.fromId(id)?.executeJavaScript("document.querySelector('a:not([href])').click()"),
    viewId,
  );
  await expect.poll(saidSoFar, { timeout: 20_000 }).toContain("criterion FIX-1.c1");
  expect(saidSoFar()).toContain("claude click");
  expect(saidSoFar()).toContain("from claude-view");

  // NOTHING OF OURS IS REACHABLE FROM THE PAGE, which is the boundary KYO-321 states
  // and the one property a check has to hold from the page's OWN world.
  // `executeJavaScript` runs in the main world -- the page's -- so this is the same
  // view of `window` claude.ai's own scripts have.
  const exposed = await (app as ElectronApplication).evaluate(
    ({ webContents }, id: number) =>
      webContents.fromId(id)?.executeJavaScript(
        `JSON.stringify({
           kyosei: typeof window.kyosei,
           ipc: typeof window.ipcRenderer,
           require: typeof window.require,
           named: Object.getOwnPropertyNames(window).filter((key) => /kyo/i.test(key)),
         })`,
      ) as Promise<string>,
    viewId,
  );
  expect(JSON.parse(exposed)).toEqual({
    kyosei: "undefined",
    ipc: "undefined",
    require: "undefined",
    named: [],
  });
});
