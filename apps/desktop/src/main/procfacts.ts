// Two facts about this machine that only the system can answer (KYO-390): what a
// process is called, and what the machine is called. A seam of its own, like
// `gitfacts.ts`: `ps` and `scutil`, read-only, and never a route to `kyo`.

import { execFileSync } from "node:child_process";
import { hostname } from "node:os";
import { basename } from "node:path";

/**
 * The name of the process with this pid, as `ps` reports it -- its basename, with a
 * login shell's leading "-" dropped -- or null when there is no such process or `ps`
 * cannot answer. The same name `kyo sessions --hook` records, read the same way.
 */
export function processNameOf(pid: number): string | null {
  try {
    const name = execFileSync("ps", ["-o", "comm=", "-p", String(pid)], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: 2_000,
    }).trim();
    return name === "" ? null : basename(name).replace(/^-/, "");
  } catch {
    return null;
  }
}

/**
 * This machine's name as its owner knows it: on macOS the computer name ("Alex's
 * MacBook Pro"), which `hostname()` is not -- on one Mac that answered a DNS name from
 * its network, `Mac.example.net`, which its owner did not recognise. Elsewhere, and
 * whenever `scutil` cannot answer, the host name.
 */
export function machineName(): string {
  if (process.platform === "darwin") {
    try {
      const name = execFileSync("/usr/sbin/scutil", ["--get", "ComputerName"], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
        timeout: 2_000,
      }).trim();
      if (name !== "") return name;
    } catch {
      // the host name, below
    }
  }
  return hostname();
}
