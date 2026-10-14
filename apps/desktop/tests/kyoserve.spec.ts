// KYO-459: the app's one transport, a client of `kyo serve`.
//
// Launches no app. `kyoserve.ts` is Electron-free, so everything it promises is driven
// here directly -- the address rules as pure functions, and the client against a REAL
// `kyo serve` this check starts on an ephemeral loopback port, over a fixture store
// built by the real CLI. Never the server on 47370.

import { createServer } from "node:net";
import { expect, test } from "@playwright/test";
import {
  addressLabel,
  baseUrl,
  DEFAULT_SERVE_PORT,
  localServer,
  parseServerAddress,
  routeOf,
  SERVE_URL_ENV,
  ServeClient,
  ServeUnreachable,
  START_SERVE_COMMANDS,
} from "../src/main/kyoserve";
import {
  type Fixture,
  type KyoServeFixture,
  kyo,
  makeFixture,
  removeFixture,
  startKyoServe,
  stopKyoServe,
} from "./fixture";

// ------------------------------------------------------------- the address (no server)

test("a server is an address: loopback is plain HTTP on kyo serve's port, anything else HTTPS", () => {
  expect(parseServerAddress("127.0.0.1")).toEqual({
    address: { host: "127.0.0.1", port: DEFAULT_SERVE_PORT },
    problem: null,
  });
  expect(parseServerAddress("http://127.0.0.1:47371/")).toEqual({
    address: { host: "127.0.0.1", port: 47371 },
    problem: null,
  });
  expect(parseServerAddress("https://devbox.tailnet.ts.net")).toEqual({
    address: { host: "devbox.tailnet.ts.net", port: 443 },
    problem: null,
  });
  expect(parseServerAddress("devbox.tailnet.ts.net")).toEqual({
    address: { host: "devbox.tailnet.ts.net", port: 443 },
    problem: null,
  });
  expect(baseUrl({ host: "127.0.0.1", port: 47370 })).toBe("http://127.0.0.1:47370");
  expect(baseUrl({ host: "devbox.tailnet.ts.net", port: 443 })).toBe(
    "https://devbox.tailnet.ts.net",
  );
  expect(addressLabel({ host: "devbox", port: 8443 })).toBe("https://devbox:8443");

  // Refused before anything is sent: a plain-HTTP LAN listener is what kyo serve refuses
  // to be (it has no password or token; the tailnet's access rule is the boundary), and
  // loopback does not speak TLS.
  const lan = parseServerAddress("http://192.168.1.20:47370");
  expect(lan.address).toBeNull();
  expect(lan.problem).toContain("Tailscale Serve");
  expect(parseServerAddress("https://127.0.0.1").problem).toContain("plain HTTP");
  expect(parseServerAddress("ftp://x").problem).toContain("not an http or https address");
  expect(parseServerAddress("devbox:99999").problem).toContain("port");
  expect(parseServerAddress("").problem).toContain("needs an address");

  // This machine's server: the default, or the address the environment names.
  expect(localServer({})).toEqual({ host: "127.0.0.1", port: DEFAULT_SERVE_PORT });
  expect(localServer({ [SERVE_URL_ENV]: "http://127.0.0.1:51000" })).toEqual({
    host: "127.0.0.1",
    port: 51000,
  });
});

test("a command's argv becomes its route: positionals in the path, flags in the query", () => {
  expect(routeOf(["show", "KYO-12"])).toEqual({ command: "show", path: "show/KYO-12", query: "" });
  expect(routeOf(["base", "run", "my base", "--group-by", "kind"])).toEqual({
    command: "base",
    path: "base/run/my%20base",
    query: "group-by=kind",
  });
  expect(routeOf(["activity", "--finished=3", "--full"]).query).toBe("finished=3&full");
  expect(() => routeOf(["list", "--full", "--status", "open", "stray"])).toThrow(
    /positionals first/,
  );
});

// --------------------------------------------------------- against a real kyo serve

let fixture: Fixture;
let serve: KyoServeFixture;

test.beforeAll(async () => {
  fixture = await makeFixture(2);
  serve = await startKyoServe(fixture.home);
});

test.afterAll(async () => {
  if (serve !== undefined) await stopKyoServe(serve);
  if (fixture !== undefined) removeFixture(fixture);
});

test("the client reads the server's version, its projects, and runs a served command", async () => {
  const client = new ServeClient(serve.address, true);
  const version = await client.version();
  expect(version.ok).toBe(true);
  const listed = await client.projects();
  expect(listed.ok).toBe(true);
  if (!listed.ok) return;
  const entry = listed.projects.find((project) => project.root === fixture.root);
  expect(entry?.prefix).toBe("FIX");

  const project = client.project(fixture.root);
  const where = await project.json(["where"]);
  expect(where.code).toBe(0);
  expect(where.data?.project_prefix).toBe("FIX");
  const shown = await project.json(["show", "FIX-1"]);
  expect(shown.code).toBe(0);
  // The CLI's own failure envelope comes back verbatim, with exit 1.
  const missing = await project.json(["show", "FIX-999"]);
  expect(missing.code).toBe(1);
  expect(missing.data?.ok).toBe(false);
  // And a refusal kyo serve makes itself carries its sentence where stderr was.
  const brief = await project.json(["brief"]);
  expect(brief.code).toBe(1);
  expect(brief.stderr).toContain("does not run `kyo brief`");
});

test("a root the server does not serve is refused with `kyo projects add`, and adding it clears it", async () => {
  const other = await makeFixture({ records: 0, git: false, dir: "another project" });
  // `makeFixture` points the process's KYOSEI_HOME at its own home; the remedy is run
  // against the SERVER's.
  process.env.KYOSEI_HOME = fixture.home;
  try {
    const client = new ServeClient(serve.address, true);
    const refused = await client
      .nameFor(other.root)
      .then(() => null)
      .catch((error: Error) => error.message);
    expect(refused).toContain(`kyo projects add ${other.root}`);
    // The remedy the sentence names.
    await kyo(fixture.resolution, ["projects", "add", other.root], fixture.base);
    expect(await client.nameFor(other.root)).toBe("another project");
  } finally {
    await kyo(fixture.resolution, ["projects", "remove", other.root], fixture.base).catch(
      () => undefined,
    );
    removeFixture(other);
  }
});

test("nothing answering is one sentence naming both ways to start kyo serve, and starting it clears it", async () => {
  // A port nothing listens on, found by binding and releasing it.
  const port = await new Promise<number>((resolve) => {
    const probe = createServer();
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      probe.close(() =>
        resolve(typeof address === "object" && address !== null ? address.port : 0),
      );
    });
  });
  const client = new ServeClient({ host: "127.0.0.1", port }, true);
  const down = await client.version();
  expect(down.ok).toBe(false);
  if (!down.ok) {
    expect(down.unreachable).toBe(true);
    expect(down.problem).toContain("kyo serve is not running");
    for (const command of START_SERVE_COMMANDS) expect(down.problem).toContain(`\`${command}\``);
  }
  await expect(client.project(fixture.root).json(["where"])).rejects.toBeInstanceOf(
    ServeUnreachable,
  );
  // The remedy: `kyo serve` on that port.
  const started = await startKyoServe(fixture.home, {}, port);
  try {
    expect((await client.version()).ok).toBe(true);
  } finally {
    await stopKyoServe(started);
  }
});

test("ideas and the scratchpad are written through the file routes, never over a newer copy", async () => {
  const project = new ServeClient(serve.address, true).project(fixture.root);
  const made = await project.writeFile("in a folder/new idea.md", "# one\n", "create");
  expect(made.ok).toBe(true);
  const again = await project.writeFile("in a folder/new idea.md", "# two\n", "create");
  expect(again.ok).toBe(false);
  if (!again.ok) expect(again.status).toBe(412);
  const read = await project.readFile("in a folder/new idea.md");
  expect(read.ok && read.text).toBe("# one\n");
  if (!read.ok || !made.ok) return;
  const replaced = await project.writeFile("in a folder/new idea.md", "# two\n", read.etag ?? "");
  expect(replaced.ok).toBe(true);
  // The etag read before the replace no longer matches: refused, never overwritten.
  const stale = await project.writeFile("in a folder/new idea.md", "# three\n", read.etag ?? "");
  expect(stale.ok).toBe(false);

  const listed = await project.ideaEntries();
  expect(listed.ok).toBe(true);
  if (listed.ok) {
    expect(listed.entries.map((entry) => entry.path)).toContain("in a folder/new idea.md");
  }
  expect((await project.move("in a folder/new idea.md", "moved.md")).ok).toBe(true);
  expect((await project.mkdir("a new folder")).ok).toBe(true);
  const current = await project.readFile("moved.md");
  if (!current.ok) throw new Error(current.problem);
  expect((await project.deleteFile("moved.md", current.etag)).ok).toBe(true);

  // The scratchpad: none yet reads as missing, then a create, then a replace.
  const pad = await project.readFile(null);
  expect(pad.ok).toBe(false);
  if (!pad.ok) expect(pad.missing).toBe(true);
  const created = await project.writeFile(null, "a thought\n", "create");
  expect(created.ok).toBe(true);
  expect((await project.readFile(null)).ok).toBe(true);

  // Nothing outside ideas/ is reachable by a relative path.
  const escaped = await project.writeFile("../kyosei/config.yml", "x", "create");
  expect(escaped.ok).toBe(false);
});

test("a change under ideas/ arrives on the event stream", async () => {
  const project = new ServeClient(serve.address, true).project(fixture.root);
  const heard: string[] = [];
  const stop = project.subscribe((kind) => heard.push(kind));
  try {
    // The stream opens with the server's first fingerprint; a change after that is
    // an event. Written through the file route, as the app writes.
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    const written = await project.writeFile(`event ${Date.now()}.md`, "x\n", "create");
    expect(written.ok).toBe(true);
    await expect.poll(() => heard.includes("ideas"), { timeout: 10_000 }).toBe(true);
  } finally {
    stop();
  }
});
