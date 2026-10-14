"""What `kyo init` discloses about committed prose and dispatch worktrees (KYO-54, KYO-70)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import (
    KYOSEI_DIR,
    LAYOUT_IN_REPO,
    STORE_LINK_NAME,
    Layout,
    worktree_home,
)
from .events_ledger import EVENTS_DIRNAME
from .ledgers import BRIEFS_FILENAME, SHIFT_NOTES_FILENAME


#: Exactly what becomes git-committed prose when a store is committed to the code
#: repository — named surface by surface, because "the store" is precisely the
#: abstraction that keeps this cost invisible: it does not tell a maintainer whether
#: the tool puts private thoughts on GitHub by default. `kyo init` reads this at the
#: moment it scaffolds (KYO-54.c3): it is the only warning a consumer gets at the
#: moment the decision is actually made, so it names the things at stake rather
#: than gesturing at "the store".
@dataclass(frozen=True)
class ProseSurface:
    """One kind of committed prose, named in the consumer's terms.

    ``key`` is what a *count* of this surface is called. KYO-60 needs the surfaces
    not merely listed but measured — "41 idea files", not "your intake layer" —
    and a count belongs to the same declaration as the description of what is
    being counted, or the two drift. ``kyosei.cmd_publish.SURFACE_COUNTERS`` holds
    one counter per key and a test fails if a surface is added without one, so a
    surface can never be described here and left uncounted there (law 3).
    """

    key: str
    path: str
    unit: str
    what: str


COMMITTED_PROSE_SURFACES: tuple[ProseSurface, ...] = (
    ProseSurface(
        "ideas",
        "ideas/",
        "idea file",
        "the zero-ceremony intake layer, verbatim — unfiltered by construction, "
        "because that is what it is for",
    ),
    ProseSurface(
        "records",
        f"{KYOSEI_DIR}/export/*.json",
        "record",
        "every record's description, including promoted idea text word for word, "
        "and every note appended to a record",
    ),
    ProseSurface(
        "evidence_artifacts",
        f"{KYOSEI_DIR}/export/*.json (evidence artifacts)",
        "evidence artifact",
        "evidence stored verbatim (law 5): whatever was in the terminal — logs, "
        "stack traces, query output, customer identifiers",
    ),
    ProseSurface(
        "shift_notes",
        f"{KYOSEI_DIR}/export/{SHIFT_NOTES_FILENAME}",
        "shift note",
        "shift notes: what an orchestrator suspects, and what it thinks is going wrong",
    ),
    ProseSurface(
        "briefs",
        f"{KYOSEI_DIR}/export/{BRIEFS_FILENAME}",
        "frozen dispatch brief",
        "frozen dispatch briefs, which quote all of the above and are immutable",
    ),
    ProseSurface(
        "events",
        # KYO-268.c2: the directory, not `_events.jsonl` — the ledger is written as
        # bounded shards, and a disclosure naming a file a new store never creates would
        # send a maintainer looking for something that is not there.
        f"{KYOSEI_DIR}/export/{EVENTS_DIRNAME}/",
        "event",
        "the append-only events ledger, which has already copied every version of "
        "every one of them",
    ),
)


def worktree_disclosure(
    layout: str, *, code_root: Path, store_root: Path, environ: dict[str, str] | None = None
) -> list[str]:
    """The lines `kyo init` prints about where dispatch worktrees will be created
    (KYO-70.c4), in the same breath as where the store goes.

    Said at setup because dispatching materialises directories outside the repository,
    and setup is the moment that sentence costs nothing (docs/design/scaffold.md#kyo-70).
    """
    # Derived through the same function every command resolves a worktree with
    # (law 3), so `kyo init`'s promise and `kyo dispatch`'s behaviour cannot name
    # two different directories.
    from .config import project_home_name

    name = project_home_name(Layout(layout, store_root=store_root, code_root=code_root))
    home = worktree_home(name, environ)
    lines = [
        f"Dispatch worktrees will be created under {home}/<branch>/ — one directory per "
        "dispatch branch, outside this repository and outside whatever directory you keep "
        "your projects in.",
        "`kyo dispatch --branch NAME` derives that path for you; `--worktree DIR` overrides "
        "it. `kyo worktrees` lists them and `kyo worktrees --prune` removes the ones whose "
        "dispatch has finished, refusing any that still holds uncommitted work.",
    ]
    if layout == LAYOUT_IN_REPO:
        lines.append(
            f"Note that this is true even though your store is in-repo: {home.parent} "
            "will be created by the first `kyo dispatch --branch`, solely to hold worktrees "
            "(KYO-70); `kyo init` creates nothing there. A worktree cannot go inside this "
            "repository — that is the one arrangement git itself warns about — and beside "
            "it is where they used to go and why this was decided."
        )
    return lines


def prose_disclosure(layout: str, *, code_root: Path, store_root: Path) -> list[str]:
    """The lines `kyo init` prints about what does and does not become committed.

    Both layouts get the same list of surfaces, generated from
    :data:`COMMITTED_PROSE_SURFACES` (law 3), because the consumer choosing the
    split layout deserves to know what they just declined as much as the one
    choosing in-repo deserves to know what they just accepted.
    """
    if layout == LAYOUT_IN_REPO:
        head = [
            f"This project's store is INSIDE the code repository at {code_root}.",
            "If you commit the store — and `kyo` is built on the assumption that you "
            "do; the export is the git-canonical serialization and what `kyo rebuild` "
            "reconstructs the database from — then the following becomes committed "
            "prose in the same repository as your code, visible to everyone that "
            "repository is visible to:",
        ]
    else:
        head = [
            f"This project's store is OUTSIDE the code repository, at {store_root}, "
            "in its own git repository. The code repository carries only "
            f"{STORE_LINK_NAME}, a pointer naming that path.",
            "What that keeps out of the code repository, none of which is committed "
            "beside your code under this layout:",
        ]
    body = [f"  - {s.path} — {s.what}" for s in COMMITTED_PROSE_SURFACES]
    tail = [
        "`kyo redact` removes any of that prose from the store and from the "
        "git-canonical ledgers going forward; it cannot reach a commit that has "
        "already been pushed.",
    ]
    if layout == LAYOUT_IN_REPO:
        tail.append(
            "To keep the store out of this repository instead, initialise elsewhere "
            "without --in-repo (the default), which puts it under ~/kyosei/<project>/."
        )
    else:
        tail.append(
            "To keep the store in the code repository instead — the right answer when "
            "records are not sensitive and you want them reviewed in the same pull "
            "request as the code — initialise with `kyo init --in-repo`."
        )
    return [*head, *body, *tail]
