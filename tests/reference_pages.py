"""Where the README's reference sections live since the README became a front page.

Originally the repository's ``README.md`` was the whole operating reference, and
the tests that hold a passage of that reference to the code read it there. The README
is now a one-screen introduction, and every section it carried moved, text intact, to a
page under ``docs/reference/topics/``. A test that reads a moved passage by path names
the page that holds it through the constants here, so a later regrouping of the pages
is one edit in this file rather than a search through the suite; a test that names a
document the way the docs parser reports it (``"docs/reference/topics/operating.md"``)
spells the path, as it spelled ``"README.md"`` before.
"""

from __future__ import annotations

from pathlib import Path

from tests.prose_text import REPO_ROOT

TOPICS_DIR = REPO_ROOT / "docs" / "reference" / "topics"

#: The topic index the front page and the guide link to.
INDEX = TOPICS_DIR / "README.md"
INSTALL = TOPICS_DIR / "install.md"
OPERATING = TOPICS_DIR / "operating.md"
STORE = TOPICS_DIR / "store.md"
REGISTRIES = TOPICS_DIR / "registries.md"
DISPATCH = TOPICS_DIR / "dispatch.md"
DOCS_GOVERNANCE = TOPICS_DIR / "docs-governance.md"
SERVE_AND_DESKTOP = TOPICS_DIR / "serve-and-desktop.md"
RULES_AND_EXIT_CODES = TOPICS_DIR / "rules-and-exit-codes.md"

#: Every topic page, the index included, as repository-relative posix paths.
TOPIC_GLOB = "docs/reference/topics/*.md"


def rel(path: Path) -> str:
    """``path`` relative to the repository root, posix-style, as tests name documents."""
    return path.relative_to(REPO_ROOT).as_posix()


def topic_pages() -> tuple[Path, ...]:
    """Every page under ``docs/reference/topics/``, sorted."""
    return tuple(sorted(TOPICS_DIR.glob("*.md")))
