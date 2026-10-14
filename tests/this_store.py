"""This repository's own Kyosei store, wherever this checkout reaches it (KYO-630).

Kyosei tracks its own development in a store that is not part of this repository: in the
maintainer's checkout it is a separate repository named by the gitignored `.kyosei` link
(the split layout), and a public checkout has none. The tests that compare a committed
file with that store find it the way every `kyo` command does, through
:func:`kyosei.config.resolve_layout`, so they run wherever the store is reachable,
including a dispatch worktree reading through the main checkout, and skip elsewhere.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def registry_dir() -> Path | None:
    """The store's registry directory (``kinds.yml``, ``procedures.md``, ``export/``), or
    ``None`` when this checkout reaches no store."""
    from kyosei.config import config_path_for, resolve_layout

    layout = resolve_layout(REPO_ROOT, required=False)
    if not layout.configured:
        return None
    directory = config_path_for(layout.store_root).parent
    return directory if directory.is_dir() else None


#: Resolved once per test session; ``None`` in a checkout without the store.
REGISTRY_DIR = registry_dir()
#: A path that never exists, for the skip conditions when there is no store.
_ABSENT = REPO_ROOT / ".no-store-reachable"


def store_path(*parts: str) -> Path:
    """A path inside the store's registry directory, or one that does not exist."""
    return REGISTRY_DIR.joinpath(*parts) if REGISTRY_DIR is not None else _ABSENT
