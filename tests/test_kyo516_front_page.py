"""KYO-516: the README is a one-screen front page, and its reference lives in topic pages.

The README was once the whole operating reference, about four thousand lines
organised by milestone and record id. It is now the page a stranger meets at a public
repository: what Kyosei is and why, how to install it, the loop in a picture, and links.
Every section it carried moved, text intact, to a page under ``docs/reference/topics/``.

Each test here fails if one half of that is undone: the front page growing back into a
manual, a moved section going missing, a topic page nobody can reach from the index, or
a home path reaching a page meant to be public. Private names on these pages are
``tests/test_defaults_name_no_consumer.py``'s to catch (KYO-628), across the whole
public tree.
"""

from __future__ import annotations

import re

import pytest

from tests.prose_text import REPO_ROOT
from tests.reference_pages import INDEX, TOPICS_DIR, rel, topic_pages

README = REPO_ROOT / "README.md"

#: The criterion's own number: one screen, generously read.
FRONT_PAGE_MAX_LINES = 150

#: Every heading the README carried before the move, spelled exactly, as later renamed.
#: The README's title and its milestone narrative were not kept: the narrative was a
#: dated account, and every mechanism it described is documented in its current form on
#: the topic pages (KYO-621).
MOVED_HEADINGS = (
    "## Install",
    # KYO-519 renamed these two after they moved (controller -> orchestrator).
    "### Starting an orchestrator from another machine: `kyo orchestrator` (KYO-458)",
    "### Serving a store: `kyo serve` (KYO-448)",
    "### Reaching it from another machine: Tailscale Serve and SSH (KYO-441)",
    "## Upgrading",
    # KYO-621 retitled this one without its build date.
    "### Upgrading from schema 25 to 26",
    "## Operating a Kyosei project",
    "### The whole suite is the orchestrator's: `controller_only_commands` (KYO-298)",
    "### Doc fragments: a document that grows by numbered section (KYO-299)",
    "### What an in-repo store costs the repository (KYO-268)",
    "### The desktop app reads every project through kyo serve (KYO-459)",
    "## Quick start",
    "### One record, one file: `--from` (KYO-330)",
    "## Where the store lives",
    "### Moving an existing store out of the code repository",
    "### Reclaiming the disk an old events ledger cost (KYO-268)",
    "### Before your first push: `kyo check-push`",
    "## What `kyo init` creates",
    "## The kind registry",
    "## The scope registry",
    "## The status registry, and why contracts key off roles",
    "## The gate-tag registry",
    "## Saved views: `kyosei/bases.yml` and `kyo base`",
    "### The three sources",
    "## The store",
    "## The dispatch loop",
    "## Sweep, next, and the session briefing",
    '### Staleness coverage — "no flags" is not the same as "nothing is stale"',
    "## Reading the store",
    "## The desktop app (`apps/desktop/`)",
    "## Layer 3: the docs-SOT skeleton validator",
    "## Rules with machinery behind them",
    "## Exit codes",
)

#: An absolute path into somebody's home directory. ``~/...`` is not one: it is the
#: reader's own home, which is what a generic instruction should say.
HOME_PATH_RE = re.compile(r"(?:/Users|/home)/[A-Za-z0-9._-]+")

#: A record id in prose, e.g. ``KYO-516`` or ``KYO-516.c1``.
RECORD_ID_RE = re.compile(r"\b[A-Z]{2,}-\d+\b")


def _public_pages():
    return (README, *topic_pages())


def _headings(text: str) -> list[str]:
    return [line.rstrip() for line in text.splitlines() if re.match(r"^#{1,6} ", line)]


def test_the_front_page_fits_on_one_screen() -> None:
    lines = README.read_text(encoding="utf-8").splitlines()
    assert len(lines) < FRONT_PAGE_MAX_LINES, (
        f"README.md is {len(lines)} lines; the front page stays under "
        f"{FRONT_PAGE_MAX_LINES} and reference material goes in {rel(TOPICS_DIR)}/"
    )


def test_the_front_page_says_what_why_how_to_install_and_the_loop() -> None:
    headings = _headings(README.read_text(encoding="utf-8"))
    for wanted in ("## Why", "## Install", "## Operating a Kyosei project"):
        assert wanted in headings, f"README.md has no {wanted!r} section"


def test_the_front_page_links_the_guide_and_the_reference() -> None:
    text = README.read_text(encoding="utf-8")
    assert "](docs/guide/)" in text, "README.md does not link the guide"
    assert "](docs/reference/topics/README.md)" in text, "README.md does not link the topics"


def test_the_front_page_prose_carries_no_record_ids() -> None:
    text = README.read_text(encoding="utf-8")
    assert not RECORD_ID_RE.findall(text), RECORD_ID_RE.findall(text)


def test_every_moved_heading_is_a_heading_of_some_topic_page() -> None:
    owners: dict[str, list[str]] = {}
    for page in topic_pages():
        for heading in _headings(page.read_text(encoding="utf-8")):
            owners.setdefault(heading, []).append(page.name)
    missing = [h for h in MOVED_HEADINGS if h not in owners]
    assert not missing, f"README headings no topic page carries: {missing}"
    doubled = {h: owners[h] for h in MOVED_HEADINGS if len(owners[h]) > 1}
    assert not doubled, f"moved headings carried twice: {doubled}"


def test_the_topic_index_links_every_page_and_every_link_resolves() -> None:
    index = INDEX.read_text(encoding="utf-8")
    linked = set(re.findall(r"\]\(([\w.-]+\.md)\)", index))
    pages = {page.name for page in topic_pages() if page != INDEX}
    assert pages <= linked, f"topic pages the index does not link: {sorted(pages - linked)}"
    for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", index):
        if "://" in target:
            continue
        assert (INDEX.parent / target).exists(), f"{rel(INDEX)} links {target}, which is absent"


@pytest.mark.parametrize("page", _public_pages(), ids=lambda page: page.name)
def test_relative_links_on_the_public_pages_resolve(page) -> None:
    """The move rewrote every relative link for its new depth; a page that still links
    as though it sat at the repository root fails here."""
    text = page.read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
        if ":" in target:
            continue
        assert (page.parent / target).exists(), f"{rel(page)} links {target}, which is absent"


@pytest.mark.parametrize("page", _public_pages(), ids=lambda page: page.name)
def test_no_home_path_on_a_public_page(page) -> None:
    text = page.read_text(encoding="utf-8")
    homes = HOME_PATH_RE.findall(text)
    assert not homes, f"{rel(page)} carries absolute home paths: {homes}"
