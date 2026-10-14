// KYO-238: a fixture is removed while something is still writing into it.
//
// The record's first intermittent is `ENOTEMPTY, Directory not empty` out of
// `removeFixture`, on a memory-starved full run, in a check that passed 3 of 3 alone
// immediately afterwards. The check it happened in no longer exists -- it was in
// `tests/smoke.spec.ts`, which KYO-232 deleted with the v2 renderer 27 minutes after
// the record was filed -- but `removeFixture` is called by every spec file in this
// directory, so the defect outlived the check that found it.
//
// What is under test here is therefore the SUITE'S OWN teardown rather than the app:
// that `removeTree` wins a race a bare `rmSync` loses, and that it is `TEARDOWN_RETRIES`
// doing the winning. Both arms run against a real writing process rather than a
// simulated one, because the thing being measured is a kernel-level interleaving that
// nothing in-process reproduces.

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { removeTree, suiteEnv } from "./fixture";

/** How long a writer keeps writing, and how long the teardown lets it get going. */
const WRITER_MS = 400;
const HEADSTART_MS = 50;

/**
 * A directory shaped like a fixture, with a process writing inside it.
 *
 * The writer stands for what really writes there at teardown time: a `kyo` subprocess
 * the app spawned and nobody awaits, SQLite's `-wal` and `-shm` beside a database
 * being closed, the git poll, the pane's own shell. It creates files rather than
 * holding one open, because that is the shape that beats a recursive remove: the walk
 * reads the directory, unlinks what it saw, and finds a new name there when it comes
 * to `rmdir`.
 */
function busyTree(): { base: string; stop(): void } {
  const base = mkdtempSync(join(tmpdir(), "kyosei teardown "));
  const inner = join(base, "a project", "kyosei");
  mkdirSync(inner, { recursive: true });
  for (let i = 0; i < 200; i += 1) writeFileSync(join(inner, `seed${i}`), "z".repeat(200));
  const writer = spawn(
    process.execPath,
    [
      "-e",
      `const {mkdirSync,writeFileSync}=require("node:fs");const {join}=require("node:path");` +
        `const d=join(process.argv[1],"a project","kyosei");const until=Date.now()+${WRITER_MS};` +
        `let n=0;while(Date.now()<until){n+=1;try{mkdirSync(d,{recursive:true});` +
        `writeFileSync(join(d,"kyosei.db-wal"),"x".repeat(64));writeFileSync(join(d,"f"+(n%40)),"y");}catch{}}`,
      base,
    ],
    { stdio: "ignore", env: suiteEnv() },
  );
  return {
    base,
    stop: () => {
      writer.kill("SIGKILL");
      rmSync(base, { recursive: true, force: true, maxRetries: 20, retryDelay: 50 });
    },
  };
}

/** Remove one busy tree with `remove`, and say whether it threw ENOTEMPTY. */
async function attempt(remove: (path: string) => void): Promise<string | null> {
  const tree = busyTree();
  await new Promise((resolve) => setTimeout(resolve, HEADSTART_MS));
  try {
    remove(tree.base);
    return null;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code ?? "unknown";
  } finally {
    tree.stop();
  }
}

test("a fixture directory being written into is still removed", async () => {
  test.setTimeout(120_000);
  // Ten consecutive wins. A single win would prove nothing -- the bare remove wins
  // roughly a third of the time -- and ten is what makes this fail when
  // `TEARDOWN_RETRIES` is taken out of `removeTree`: at the measured bare rate the
  // chance of ten in a row is about three in a hundred thousand.
  const codes: (string | null)[] = [];
  for (let i = 0; i < 10; i += 1) codes.push(await attempt(removeTree));
  expect(codes.filter((code) => code !== null)).toEqual([]);
});

test("a single-attempt remove really does lose that race", async () => {
  test.setTimeout(120_000);
  // The other half of the assertion, and the reason the retries are not decoration:
  // the same conditions, with `rmSync` as every teardown in this suite used to call
  // it. At least one of twenty must fail, or the arm above is passing vacuously --
  // the writer stopped writing, the machine got slower, the harness stopped racing
  // anything -- and the guard above would then hold nothing in place.
  const codes: (string | null)[] = [];
  for (let i = 0; i < 20; i += 1) {
    codes.push(await attempt((path) => rmSync(path, { recursive: true, force: true })));
  }
  const failures = codes.filter((code) => code !== null);
  expect(
    failures.length,
    `no bare remove lost the race in 20 attempts: ${codes.join(",")}`,
  ).toBeGreaterThan(0);
  expect(new Set(failures)).toEqual(new Set(["ENOTEMPTY"]));
});

test("the tree really is gone when the retrying remove returns", async () => {
  const tree = busyTree();
  await new Promise((resolve) => setTimeout(resolve, HEADSTART_MS));
  try {
    removeTree(tree.base);
    // Returning without throwing is not the claim; the directory being gone is.
    expect(existsSync(tree.base)).toBe(false);
  } finally {
    tree.stop();
  }
});
