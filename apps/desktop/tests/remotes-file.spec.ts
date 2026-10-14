// The remembered connections file, `remotes.json` (KYO-370, KYO-576). Launches no app.
//
// A file from before KYO-381 also carried KYO-369's `token` on each entry. Until KYO-576
// the first read rewrote the file without it; that one-time scrub is gone, so a file that
// still carries one is read as it is: the entry is its address, root and project, the
// token is ignored, and the file is not touched by a read. The next write (a connection
// remembered) stores those fields only, as it always did.

import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { readRemotes, rememberRemote, remotesPath } from "../src/main/remote";

let userData: string;

test.beforeEach(() => {
  userData = mkdtempSync(join(tmpdir(), "kyosei remotes "));
  mkdirSync(userData, { recursive: true });
});

test.afterEach(() => {
  rmSync(userData, { recursive: true, force: true });
});

test("a remotes file that still carries a token is read as it is, the token ignored and the file left alone (KYO-576.c2)", () => {
  const old = `${JSON.stringify(
    [
      { host: "devbox.example.ts.net", port: 443, root: "/srv/a", project: "FIX", token: "t1" },
      { host: "127.0.0.1", port: 47370, root: "/srv/b", project: null, token: "t2" },
    ],
    null,
    2,
  )}\n`;
  writeFileSync(remotesPath(userData), old, "utf8");

  const entries = readRemotes(userData);
  expect(entries).toEqual([
    { host: "devbox.example.ts.net", port: 443, root: "/srv/a", project: "FIX" },
    { host: "127.0.0.1", port: 47370, root: "/srv/b", project: null },
  ]);
  for (const entry of entries) expect(Object.keys(entry)).not.toContain("token");
  // No scrub: a read writes nothing, so the file is byte for byte what was there.
  expect(readFileSync(remotesPath(userData), "utf8")).toBe(old);
  expect(readRemotes(userData)).toEqual(entries);

  // A write stores the address, root and project, and nothing else.
  rememberRemote(userData, { host: "10.0.0.2", port: 8443, root: "/srv/c", project: "NEW" });
  const written = JSON.parse(readFileSync(remotesPath(userData), "utf8")) as Record<
    string,
    unknown
  >[];
  expect(written.map((item) => item.root)).toEqual(["/srv/c", "/srv/a", "/srv/b"]);
  for (const item of written)
    expect(Object.keys(item).sort()).toEqual(["host", "port", "project", "root"]);
});
