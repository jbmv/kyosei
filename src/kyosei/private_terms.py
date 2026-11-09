"""What a project keeps out of everything it publishes (KYO-628, KYO-669).

One matcher, two consumers: the guard test over the public tree
(``tests/test_defaults_name_no_consumer.py``) and ``kyo check-push``, which applies it to
the messages and author names of the commits a push sends.

WHERE THE TERMS LIVE. The names this protects cannot be written in a tracked file, and
neither can digests of them: a SHA-256 of a short name is reversed by hashing a list of
common names, so a published digest list publishes what it guards. The terms are read at
run time from two places outside the tracked tree:

- **the machine's project registry** (``$KYOSEI_HOME/projects.json``, what
  ``kyo projects list`` prints), through :func:`kyosei.cmd_projects.registered`. Each
  entry gives a project name and a record prefix;
- **the word file** ``private-words.txt`` beside ``kinds.yml`` in the project's store.
  One word per line; ``#`` starts a comment.

THE MATCHING RULES. Several registry entries are ordinary words, so:

1. A **project name** matches whole, case-insensitively, between non-alphanumeric
   characters; ``-`` and ``_`` in a name match each other.
2. A **component** of a hyphenated name (the parts between ``-``, ``_`` or ``.``)
   matches on its own only when it is at least :data:`MIN_COMPONENT` characters long and
   is not a common English word. A word is common when it is in the system word list
   (:data:`DICTIONARY`, lowercase entries only, so proper names are not common) or in
   :data:`COMMON_WORDS`, the software vocabulary that list lacks.
3. A **record prefix** matches only as a record id, ``PREFIX-<digits>``, in its own
   case. A prefix that is also an English word is never matched as a bare word.
4. A **listed word** matches whole, case-insensitively.
5. The project's own name and prefix, and the public names in :data:`NEVER_PRIVATE`,
   are never private.

A **home path** is ``/Users/<account>`` or ``/home/<account>`` (:data:`HOME_PATH`).

Nothing here prints a term: a match reports the kind of term it found.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .config import Layout, config_path_for

#: The word file's name, beside ``kinds.yml`` in the store.
WORD_FILE = "private-words.txt"

#: Never private, whatever the registry or the word file says (rule 5). Lowercase.
NEVER_PRIVATE = frozenset({"kyosei", "kyo", "recx"})

#: A component shorter than this never matches alone (rule 2): two- and three-letter
#: parts are abbreviations that occur everywhere.
MIN_COMPONENT = 4

#: The system word list (rule 2). Absent on some machines; then only COMMON_WORDS counts,
#: which makes the component rule stricter, never looser.
DICTIONARY = Path("/usr/share/dict/words")

#: Software vocabulary the system word list does not carry (rule 2).
_SOFTWARE_WORDS = """
    api apis app apps async auth backend bot cert certs cli config configs cron css
    dashboard demo dev devops docs frontend github gui html http https infra ios json
    kotlin linux macos mobile monorepo multi npm oauth pdf plugin plugins prod repo
    repos sdk sql ssh svg toml tui url urls webapp website yaml
"""
COMMON_WORDS = frozenset(_SOFTWARE_WORDS.split())

HOME_PATH = re.compile(r"(?<![\w~.])/(?:Users|home)/([A-Za-z0-9._-]+)")


@dataclass(frozen=True)
class Terms:
    """What is private, as compiled matchers. Never printed."""

    names: tuple[re.Pattern[str], ...]
    components: tuple[re.Pattern[str], ...]
    prefixes: tuple[re.Pattern[str], ...]
    words: tuple[re.Pattern[str], ...]

    @property
    def empty(self) -> bool:
        return not (self.names or self.components or self.prefixes or self.words)

    def kinds_on(self, line: str) -> list[str]:
        found = []
        for kind, patterns in (
            ("project name", self.names),
            ("name component", self.components),
            ("record prefix", self.prefixes),
            ("listed word", self.words),
        ):
            if any(p.search(line) for p in patterns):
                found.append(kind)
        return found

    def kinds_on_published_line(self, line: str) -> list[str]:
        """:meth:`kinds_on`, plus any home path, for text with no fixtures in it: a
        commit message is prose for a stranger, so no account name is excused."""
        return [*self.kinds_on(line), *(["home path"] if HOME_PATH.search(line) else [])]


def common_words(dictionary: Path = DICTIONARY) -> frozenset[str]:
    try:
        text = dictionary.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return COMMON_WORDS
    return COMMON_WORDS | {w for w in (raw.strip() for raw in text.splitlines()) if w.islower()}


def _whole(term: str) -> re.Pattern[str]:
    body = "".join("[-_]" if ch in "-_" else re.escape(ch) for ch in term)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)


def compile_terms(
    entries: Iterable[tuple[str, str | None]],
    words: Iterable[str],
    *,
    common: frozenset[str],
    own: Iterable[str] = (),
) -> Terms:
    """``entries`` are ``(project name, record prefix or None)``; ``own`` are this
    project's name and prefix, never private (rule 5)."""
    never = NEVER_PRIVATE | {o.lower() for o in own if o}
    names: set[str] = set()
    components: set[str] = set()
    prefixes: set[str] = set()
    for name, prefix in entries:
        lowered = name.strip().lower()
        if lowered and lowered not in never:
            names.add(lowered)
            parts = [p for p in re.split(r"[-_.]+", lowered) if p]
            if len(parts) > 1:
                components.update(
                    p
                    for p in parts
                    if len(p) >= MIN_COMPONENT and p not in common and p not in never
                )
        if prefix and prefix.strip() and prefix.strip().lower() not in never:
            prefixes.add(prefix.strip())
    listed = {w.strip().lower() for w in words if w.strip() and w.strip().lower() not in never}
    return Terms(
        names=tuple(_whole(n) for n in sorted(names)),
        components=tuple(_whole(c) for c in sorted(components - names)),
        prefixes=tuple(
            re.compile(rf"(?<![A-Za-z0-9]){re.escape(p)}-\d+") for p in sorted(prefixes)
        ),
        words=tuple(_whole(w) for w in sorted(listed)),
    )


def read_word_file(path: Path) -> list[str]:
    words = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            words.append(line)
    return words


def word_file_path(layout: Layout) -> Path | None:
    """``private-words.txt`` beside ``kinds.yml`` in this project's store, if configured."""
    if not layout.configured:
        return None
    return config_path_for(layout.store_root).parent / WORD_FILE


def registry_entries(
    layout: Layout, environ: dict[str, str] | None = None
) -> tuple[list[tuple[str, str | None]], list[str], Path]:
    """``(other projects' (name, prefix), this project's name and prefix, registry path)``."""
    from .cmd_projects import registered, registry_path

    path = registry_path(environ)
    mine = {layout.code_root.resolve(), layout.store_root.resolve()}
    if layout.main_checkout is not None:
        mine.add(layout.main_checkout.resolve())
    others: list[tuple[str, str | None]] = []
    own: list[str] = []
    for row in registered(environ):
        root = Path(row.root).expanduser()
        basename = root.name
        if layout.configured and root.resolve() in mine:
            own.extend(t for t in (row.name, basename, row.prefix) if t)
            continue
        others.append((row.name, row.prefix))
        if basename.lower() != row.name.lower():
            others.append((basename, None))
    return others, own, path


def project_terms(layout: Layout, environ: dict[str, str] | None = None) -> Terms:
    """Everything private to a configured project: the registry's other projects and
    its word file. Empty for a directory that is no project, and for a project with
    neither source."""
    if not layout.configured:
        return compile_terms((), (), common=COMMON_WORDS)
    entries, own, _ = registry_entries(layout, environ)
    path = word_file_path(layout)
    words = read_word_file(path) if path is not None and path.is_file() else []
    if not entries and not words:
        return compile_terms((), (), common=COMMON_WORDS)
    return compile_terms(entries, words, common=common_words(), own=own)
