// Where a project is served from, in words (KYO-465.c2, KYO-473.c5).
//
// One project is (server, root): the same root on this machine and on a dev box is two
// projects, and everything that names one names where it is served from -- the recents
// rows in the switcher and New Window's chooser, and the status bar of the window on
// it. They share this one helper, so they cannot disagree about what a label says.
//
// Electron-free and DOM-free: a check imports it directly (tests/seams.spec.ts).

/** A kyo serve a project is read through, other than this machine's own. */
export interface ServedFrom {
  host: string;
  port: number;
}

/**
 * A root's last path segment, for a project whose store has no readable `project`
 * prefix to label it with instead. The renderer never runs on a platform whose
 * separator is `\`, so a plain split is exact rather than a stand-in for `basename`.
 */
export function baseName(root: string): string {
  const segments = root.split("/").filter((segment) => segment !== "");
  return segments.at(-1) ?? root;
}

/** Where, alone: "this machine", or the server's `host:port`. */
export function servedWhere(server: ServedFrom | null): string {
  return server === null ? "this machine" : `${server.host}:${server.port}`;
}

/**
 * `<prefix> @ <where>`: "KYO @ this machine", "KYO @ <machine>.<tailnet>.ts.net:443". Never
 * blank -- a project with no readable prefix is named by its root's last segment, and a
 * root with none by the root itself.
 */
export function servedLabel(
  project: string | null,
  root: string,
  server: ServedFrom | null,
): string {
  const name = project !== null && project !== "" ? project : baseName(root) || root || "?";
  return `${name} @ ${servedWhere(server)}`;
}
