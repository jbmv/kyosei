// This machine's project registry, as the app sees it (KYO-379, KYO-459).
//
// `$KYOSEI_HOME/projects.json` lists the projects this machine holds, and `kyo serve`
// serves exactly those. Since KYO-455 the CLI is the registry's ONE writer: `kyo
// init` adds the project it creates and `kyo projects add|remove` edits the list. So
// the app writes no registry file of its own any more -- the atomic writer, the fold of
// the recents into it and the location lookup that lived here are gone -- and reads it
// the way every viewer does: `/v1/projects` of this machine's kyo serve, which also
// labels each entry with its prefix.
//
// What the app still does to the registry is ask the CLI, in the one way the frame
// allows (KYO-452): opening a directory on this machine that the registry does not list
// runs `kyo projects add <dir>` here, through the app's one invocation of its own `kyo`
// (`kyo.ts`), and says the CLI's refusal verbatim when it refuses. Another machine's
// registry changes over SSH, never through the app.
//
// Electron-free, like every other seam.

import type { AddProjectResult, OfferedProject } from "../shared/protocol";
import type { ProjectState } from "./facts";
import { type KyoResolution, runKyoJson } from "./kyo";
import { type ServedEntry, sameServer } from "./kyoserve";
import type { RemoteEntry } from "./remote";

/** The command the app runs to add a directory on this machine to its registry. */
export const ADD_PROJECT_COMMAND = "kyo projects add";

/**
 * What the switcher offers: this installation's recents in their own order, then every
 * other project this machine's kyo serve lists (KYO-379.c1), each labelled with the
 * prefix the server reads. A list that could not be read costs the extra rows and the
 * labels, never the recents.
 *
 * Keyed by (server, root) since KYO-465.c2: the same root served by this machine and by
 * a dev box -- or by two dev boxes -- is two rows, each carrying the server it is read
 * through, and only this machine's list labels this machine's rows. `remembered` is
 * what labels a dev box's: the prefix its server read when the project was opened.
 */
export function offeredEntries(
  state: ProjectState,
  served: readonly ServedEntry[] | null,
  remembered: readonly RemoteEntry[] = [],
): OfferedProject[] {
  const listed = served ?? [];
  const offered: OfferedProject[] = state.recents.map(({ root, connection }) => ({
    root,
    project:
      connection === null
        ? (listed.find((entry) => entry.root === root)?.prefix ?? null)
        : (remembered.find((entry) => entry.root === root && sameServer(entry, connection))
            ?.project ?? null),
    server: connection === null ? null : { host: connection.host, port: connection.port },
    recent: true,
  }));
  for (const entry of listed) {
    if (offered.some((held) => held.server === null && held.root === entry.root)) continue;
    offered.push({ root: entry.root, project: entry.prefix, server: null, recent: false });
  }
  return offered;
}

/**
 * Add a directory on this machine to its registry: `kyo projects add <dir> --json`,
 * run here. The CLI validates it -- a directory holding no initialised project is
 * refused with its own sentence, which comes back verbatim -- and one already listed is
 * answered as listed (`added: false`), not refused.
 */
export async function addLocalProject(
  resolution: KyoResolution,
  root: string,
): Promise<AddProjectResult> {
  const typed = root.trim();
  const refused = (problem: string, path: string | null = null): AddProjectResult => ({
    ok: false,
    root: typed,
    project: null,
    path,
    problem,
  });
  if (typed === "") return refused("a project is added by its directory on this machine");
  if (resolution.source.program === null) {
    return refused(
      `no runnable kyo to run \`${ADD_PROJECT_COMMAND}\` with: ${resolution.source.unavailable ?? "no source"}`,
    );
  }
  let result: Awaited<ReturnType<typeof runKyoJson>>;
  try {
    result = await runKyoJson(resolution, ["projects", "add", typed], { cwd: typed });
  } catch (error) {
    return refused(`could not run \`${ADD_PROJECT_COMMAND}\`: ${(error as Error).message}`);
  }
  const data = result.data;
  const registry = typeof data?.registry === "string" ? data.registry : null;
  if (data === null || data.ok !== true) {
    const said =
      typeof data?.error === "string" && data.error !== ""
        ? data.error
        : result.stderr.trim() || `\`${ADD_PROJECT_COMMAND} ${typed}\` exited ${result.code}`;
    return refused(said, registry);
  }
  const project =
    data.project !== null && typeof data.project === "object"
      ? (data.project as Record<string, unknown>)
      : {};
  return {
    ok: true,
    root: typeof project.root === "string" ? project.root : typed,
    project: typeof project.prefix === "string" ? project.prefix : null,
    path: registry,
    problem: null,
  };
}
