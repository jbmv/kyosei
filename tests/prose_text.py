"""Shared prose reader for the two "prose fell behind the build" checks.

KYO-35 and KYO-36 are the same family: a statement *about* the build, written in
prose, that no mechanism re-derives. Both checks have to read prose the way a
human does rather than the way `grep` does, and the reason is concrete — the
citation that produced KYO-35 was missed because the controller grepped for the
fixed string ``founding charter`` and this repo's prose is hard-wrapped, so the
phrase spanned a line break and the grep reported absence. Any check built on
fixed-string line matching would repeat exactly that mistake.

So both checks read a *flattened* form: comment leaders and markup stripped,
every line joined into one whitespace-normalised string, so a citation or a hook
recipe is matched whether it sits on one line or four. This module is the single
copy of that reader (law 3) — the two checks cannot disagree about what a
sentence in this repo says.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Line-leading noise that a wrapped sentence carries in this repo's sources:
#: Sphinx-style ``#:`` field comments, plain ``#`` comments and markdown headings,
#: SQL ``--`` comments inside :data:`kyosei.schema.DDL`, and blockquote markers.
_LEADERS = ("#:", "#", "--", "//", ">")

#: Characters that are markup rather than words here: reST/markdown code spans
#: (``x`` and `x`) and emphasis. Dropping them lets a citation written
#: ```docs/design/charter.md`, "Sweep"`` compare equal to one written plain.
_MARKUP_RE = re.compile(r"[`*_]")

_WS_RE = re.compile(r"\s+")


def flatten_prose(text: str) -> str:
    """One whitespace-normalised line, with comment leaders and markup removed.

    Hard line wrapping disappears here, which is the entire point: a phrase split
    across a line break reads as one phrase.
    """
    parts = []
    for raw in text.splitlines():
        line = raw.strip()
        for leader in _LEADERS:
            if line.startswith(leader):
                line = line[len(leader) :].lstrip()
                break
        parts.append(line)
    flat = " ".join(parts)
    flat = _MARKUP_RE.sub("", flat)
    return _WS_RE.sub(" ", flat).strip()


def iter_source_files(globs: tuple[str, ...], excludes: tuple[str, ...] = ()) -> list[Path]:
    """Every file under ``globs`` relative to the repo root, excluding ``excludes``.

    ``excludes`` are matched with :meth:`Path.match` against the repo-relative path.
    """
    seen: dict[Path, None] = {}
    for pattern in globs:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if not path.is_file():
                continue
            rel = path.relative_to(REPO_ROOT)
            if any(rel.match(ex) for ex in excludes):
                continue
            seen[path] = None
    return list(seen)


def read_flat(path: Path) -> str:
    return flatten_prose(path.read_text(encoding="utf-8"))
