// The other machines' kyo serves this app has connected to, remembered (KYO-370, KYO-459).
//
// Until KYO-459 this file was the other end of the app's own server: a `Proxy` over
// `StoreService` that POSTed each member over HTTPS to a dev box's app, trusted its
// self-minted certificate on first use (`known-hosts.json`), and kept the token that
// dev box issued for its password (`remote-tokens.json`). All of that is gone with the
// server it spoke to. A dev box now runs `kyo serve` on loopback with Tailscale Serve in
// front of it (docs/reference/topics/serve-and-desktop.md "Reaching it from another
// machine"), so a connection is an
// ADDRESS and nothing else: HTTPS with a certificate the system already trusts, no pin,
// no password and no token -- the tailnet's access rule is the boundary. The
// transport is `kyoserve.ts`, the same one this machine's own projects are read through.
//
// What is left here is the memory: `remotes.json` beside `project.json` in the app's
// own data directory, never in any project -- the address and the project opened there,
// newest first, so the switcher offers each one as a row that opens without a dialog.
//
// Electron-free, like every other seam.

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { ProjectEntry, RemoteEntryFacts, RemoteFacts } from "../shared/protocol";
import {
  addressLabel,
  clientFor,
  parseServerAddress,
  type ServedEntry,
  type ServerAddress,
} from "./kyoserve";

/** A connection to another machine's kyo serve: its address, and nothing else. */
export type RemoteConnection = ServerAddress;

/** A remembered connection: the address and the project it opened there. */
export interface RemoteEntry {
  host: string;
  port: number;
  /** The project root ON THE DEV BOX. */
  root: string;
  /** That store's record-id prefix, when the dev box could read it. */
  project: string | null;
}

/** How a connection is named in every sentence about it: its address, as typed. */
export function remoteLabel(connection: ServerAddress): string {
  return addressLabel(connection);
}

/** What a renderer may know about a connection: the address. */
export function remoteFacts(connection: RemoteConnection | null): RemoteFacts | null {
  return connection === null ? null : { host: connection.host, port: connection.port };
}

/** What asking a dev box's kyo serve for its projects answered. */
export type RemoteProbe =
  | { ok: true; projects: ProjectEntry[]; served: ServedEntry[] }
  | { ok: false; problem: string };

/**
 * Ask a dev box's kyo serve which projects it serves (`/v1/projects`). A refusal names
 * the address and says what was refused, in kyo serve's own words where it gave some.
 */
export async function probeRemote(connection: RemoteConnection): Promise<RemoteProbe> {
  const listed = await clientFor(connection).projects(15_000);
  if (!listed.ok) return { ok: false, problem: listed.problem };
  return {
    ok: true,
    projects: listed.projects.map((entry) => ({ root: entry.root, project: entry.prefix })),
    served: listed.projects,
  };
}

// ------------------------------------------------------- the remembered ones

/** Beside `project.json`: the connections this app has opened, newest first. */
export const REMOTES_FILE = "remotes.json";

/** How many remembered connections the switcher offers. */
export const MAX_REMOTES = 32;

export function remotesPath(userDataDir: string): string {
  return join(userDataDir, REMOTES_FILE);
}

export function readRemotes(userDataDir: string): RemoteEntry[] {
  try {
    const raw: unknown = JSON.parse(readFileSync(remotesPath(userDataDir), "utf8"));
    if (!Array.isArray(raw)) return [];
    const entries: RemoteEntry[] = [];
    for (const item of raw) {
      if (item === null || typeof item !== "object") continue;
      const data = item as Record<string, unknown>;
      if (typeof data.host !== "string" || typeof data.port !== "number") continue;
      const parsed = parseServerAddress(
        `${data.host.includes(":") ? `[${data.host}]` : data.host}:${data.port}`,
      );
      if (parsed.address === null) continue;
      if (typeof data.root !== "string" || data.root === "") continue;
      entries.push({
        ...parsed.address,
        root: data.root,
        project: typeof data.project === "string" ? data.project : null,
      });
    }
    // A file from before KYO-381 also held KYO-369's token. Nothing reads it: an entry is
    // its address, root and project, and the file is left as it is until the next write
    // (KYO-576), which stores those fields only.
    return entries.slice(0, MAX_REMOTES);
  } catch {
    // Not written yet (the ordinary state before a first connection), or unreadable.
    return [];
  }
}

function writeRemotes(userDataDir: string, entries: RemoteEntry[]): void {
  const stored = entries.map(({ host, port, root, project }) => ({ host, port, root, project }));
  try {
    mkdirSync(userDataDir, { recursive: true });
    writeFileSync(remotesPath(userDataDir), `${JSON.stringify(stored, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
  } catch {
    // A failure to remember costs the list, never the connection.
  }
}

/** The same connection to the same project, however the label changed. */
function sameRemote(a: RemoteEntry, b: RemoteEntry): boolean {
  return a.host === b.host && a.port === b.port && a.root === b.root;
}

/** Put `entry` first, dropping an older copy of it; write the file; answer the list. */
export function rememberRemote(userDataDir: string, entry: RemoteEntry): RemoteEntry[] {
  const entries = [
    { host: entry.host, port: entry.port, root: entry.root, project: entry.project },
    ...readRemotes(userDataDir).filter((held) => !sameRemote(held, entry)),
  ].slice(0, MAX_REMOTES);
  writeRemotes(userDataDir, entries);
  return entries;
}

/**
 * Forget one remembered project on one server, or every one with `null` (KYO-465.c3):
 * a dev box's projects are offered beside the recents, so removing a recent that lives
 * on a server has to remove it here too, or it is back in the menu on the next read.
 */
export function forgetRemote(
  userDataDir: string,
  which: { host: string; port: number; root: string } | null,
): RemoteEntry[] {
  const kept =
    which === null
      ? []
      : readRemotes(userDataDir).filter((held) => !sameRemote(held, { ...which, project: null }));
  writeRemotes(userDataDir, kept);
  return kept;
}

/** The remembered connections as the switcher sees them. */
export function remoteEntryFacts(entries: RemoteEntry[]): RemoteEntryFacts[] {
  return entries.map(({ host, port, root, project }) => ({ host, port, root, project }));
}

export function rememberedRemote(
  userDataDir: string,
  which: { host: string; port: number; root: string },
): RemoteEntry | null {
  return (
    readRemotes(userDataDir).find(
      (entry) =>
        entry.host === which.host && entry.port === which.port && entry.root === which.root,
    ) ?? null
  );
}
