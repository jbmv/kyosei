"""The receipt of a write: which of a command's lines a reader off a terminal is sent.

KYO-874. An orchestrator reads every byte a command prints, and re-reads it on every
later turn. A write's full output echoes what was stored (law 5), which is what a
person at a terminal wants and is most of what an agent filtered away with `grep`. A
filter that keeps the lines it expects drops the warning it did not expect. The receipt
is the command's own shorter form, so that nothing needs filtering: what was done and
the ids, the length and digest of each stored text, and every warning, refusal and
remedy the full output carries.

It imports nothing from this package, so any module that renders lines can mark them.
"""

from __future__ import annotations

from collections.abc import Iterable


class Line(str):
    """A printed line whose form in a receipt differs from its form in the full output.

    KYO-874. A command that writes returns every line it has always returned, and marks
    the ones a receipt prints differently: :func:`detail` for a line a receipt leaves
    out, :func:`receipt_form` for a line a receipt replaces by a shorter one. A plain
    ``str`` is in neither group and is printed in both forms. That default is the
    point: a warning added to a command later is in its receipt unless someone marks it
    as detail, where a receipt assembled from a list of known warnings would drop it.

    A ``str`` subclass, so every existing reader of ``Output.lines`` (the text printer,
    ``--json``'s ``steps[].lines``, a composing command) reads the full form unchanged.
    """

    receipt: str | None

    def __new__(cls, full: str, receipt: str | None) -> Line:
        line = super().__new__(cls, full)
        line.receipt = receipt
        return line


def detail(line: str) -> Line:
    """``line``, left out of a receipt: it restates what the command was given or what
    `kyo show` prints, and carries no warning, refusal or remedy."""
    return Line(line, None)


def details(lines: Iterable[str]) -> list[str]:
    """:func:`detail` over ``lines``."""
    return [detail(line) for line in lines]


def receipt_form(full: str, receipt: str) -> Line:
    """``full`` in the full output and ``receipt`` in a receipt."""
    return Line(full, receipt)


def indented(prefix: str, lines: Iterable[str]) -> list[str]:
    """``lines`` with ``prefix`` before each, keeping each line's receipt form: what a
    composing command (`kyo accept`, `kyo confirm`) does to a step's lines."""
    out: list[str] = []
    for line in lines:
        if isinstance(line, Line):
            short = None if line.receipt is None else prefix + line.receipt
            out.append(Line(prefix + line, short))
        else:
            out.append(prefix + line)
    return out


def receipt_lines(lines: Iterable[str]) -> list[str]:
    """The receipt of ``lines``: each line's receipt form, and the lines that have none
    left out. Nothing else is removed and no line is truncated."""
    out: list[str] = []
    for line in lines:
        if isinstance(line, Line):
            if line.receipt is not None:
                out.append(line.receipt)
        else:
            out.append(line)
    return out
