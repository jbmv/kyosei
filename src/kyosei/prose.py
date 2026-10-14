"""Law 5 — prose enters via file or stdin, never as a shell argument.

Shell interpolation has been seen silently deleting words mid-sentence three times in
one session while the command reported success. So:

* multi-line prose (descriptions, notes) has **no** flag-value form at all;
* the single-line values that *do* travel as flags — the set is not listed here,
  because a hand-kept list is what KYO-86 was filed about; it is read off the
  shipped parser by :func:`kyosei.cli.prose_argument_flags` — are refused if they
  contain a newline, which is the shape a mangled heredoc arrives in;
* those same values are checked for the residue a shell substitution leaves behind
  (:func:`shell_residue`), which is as far as this layer can see: KYO-91;
* every stored value is echoed back with its length and digest (see
  :mod:`kyosei.render`), so corruption is visible at the moment it happens.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

from .errors import KyoseiError, UsageError

#: ``--description-file -`` reads stdin.
STDIN = "-"

# ------------------------------------------------------- KYO-52: paths in prose
#
# Prose is not path-typed and never will be. A column-level fix (`contracts.store_path`)
# reaches `dispatches.worktree` and `touches.path`; it cannot reach a path quoted
# inside an idea, an evidence blob, or a `kyo doctor` error pasted into a note —
# and those are where most of this repo's own stored usernames came from. So the
# write path warns, in the same non-refusing style as `kyo dispatch`'s bare-number
# warning: the prose is stored exactly as given, and the one moment anyone can
# still cheaply rewrite it is named.
#
# Deliberately narrow, because a warning that fires on ordinary input is one people
# learn to route around (the same reasoning that kept the apostrophe out of
# `_PAIRED` above). It matches only the *home-directory* shape — `/Users/<name>/…`,
# `/home/<name>/…`, `C:\Users\<name>\…` — which is precisely the shape that carries
# a username and a directory layout. `/usr/lib/python3.12`, `/etc/hosts` and
# `/tmp/x` are machine-generic, disclose nothing about who ran the command, and are
# left alone.

_HOME_PATH_RE = re.compile(
    r"""(?:/(?:Users|home)/[^/\s"'`,;)\]]+(?:/[^\s"'`,;)\]]*)*)"""
    r"""|(?:[A-Za-z]:\\Users\\[^\\\s"'`,;)\]]+(?:\\[^\s"'`,;)\]]*)*)"""
)

#: Warnings accumulated by this process's prose reads, drained once by
#: :func:`kyosei.cli.main` after the command succeeds. A module-level collector
#: rather than a return value because every write command in the tool funnels its
#: prose through :func:`read_prose`, and threading a second return value through
#: all of them would put the check in thirty places instead of one — where a later
#: prose surface (there have been five in a day) would be the one that forgets it.
_PENDING_HOME_PATHS: list[str] = []


def home_paths(text: str) -> list[str]:
    """Home-directory-shaped absolute paths in ``text``, in order, deduplicated."""
    found: list[str] = []
    for match in _HOME_PATH_RE.finditer(text):
        value = match.group(0).rstrip(".,;:)")
        if value not in found:
            found.append(value)
    return found


def reset_path_warnings() -> None:
    """Start one CLI invocation with an empty collector."""
    _PENDING_HOME_PATHS.clear()


def pending_path_warnings() -> list[str]:
    """What the prose read during this invocation disclosed."""
    return list(_PENDING_HOME_PATHS)


def path_warning_line(paths: list[str]) -> str:
    """The non-refusing warning text, given what :func:`home_paths` found."""
    return (
        f"  warning: this prose names {len(paths)} absolute path(s) — "
        f"{', '.join(repr(p) for p in paths)}. kyosei/export/ is git-canonical, so this "
        "text is about to become permanent and publishable-by-accident, and an absolute "
        "path carries a username and a directory layout that mean nothing on any other "
        "checkout. `kyo redact` cannot reach it once it is committed to git history. "
        "Stored as given — rewrite it relative to the project root if you can."
    )


def read_prose(source: str, *, what: str) -> str:
    """Read prose from a file path, or from stdin when ``source`` is ``-``.

    Line endings are normalised to ``\\n`` and trailing blank lines are dropped, so
    that storing the same text twice stores the same bytes. Everything else is kept
    verbatim, and the caller is shown exactly what landed.

    KYO-52: any home-directory-shaped absolute path in the text is collected for the
    warning ``kyo`` prints after the command succeeds. Collected, never refused —
    the prose is returned unchanged.
    """
    if source == STDIN:
        raw = sys.stdin.read()
    else:
        path = Path(source)
        if not path.is_file():
            raise KyoseiError(f"cannot read {what}: {source} is not a file")
        raw = path.read_text(encoding="utf-8")
    text = normalise(raw)
    for found in home_paths(text):
        if found not in _PENDING_HOME_PATHS:
            _PENDING_HOME_PATHS.append(found)
    return text


def normalise(raw: str) -> str:
    return raw.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


# ------------------------------------ KYO-66: matching a phrase in hard-wrapped prose
#
# This repo's prose — `procedures.md`, the charter, every stored description — is
# hard-wrapped, deliberately: a paragraph kept on one long line makes a one-word edit
# rewrite the whole line, and `kyosei/export/` is git-canonical precisely so a change
# reads as a small diff. The wrapping stays.
#
# What it costs is that a *fixed-string* match over that prose answers a narrower
# question than the one being asked. `"some phrase" in text` returning False means the
# phrase does not appear **without an intervening line break**; it is not evidence that
# the phrase is absent, and absence is almost always the claim. That mistake has been
# made four times in one day, and it is not only a human failure mode: the
# tool itself matched stored phrases literally in four places (the `kyo ideas review`
# mention scan, touch symbol validation, `governs:` target validation, and `kyo redact`'s
# embedded-copy scrub), and every one of them would under-match in silence — a scan that
# reports nothing, a validator that says "not found" about text that is right there, and
# a redaction that leaves a copy behind.
#
# So there is exactly one answer to "does this phrase appear in that prose", here (law 3),
# and it is the followable form the record prescribes: split the phrase on whitespace and
# join the escaped words with a whitespace gap, which matches the phrase however it wraps.
# The equivalent for a *reader* — who has no regex — is to search a distinctive single
# word and read the lines around it, since a word cannot span a line break. That half is
# a `procedures.md` rule; it can never be a mechanism.
#
# `tests/prose_text.py` reached the same conclusion for the test-side prose checks
# (KYO-35/36) before the product code did. Nothing links the two: that module flattens a
# whole *file* to compare sentences, this one builds a pattern for one *needle*, and the
# test helper must keep reading the repo's sources rather than the store.

#: What may stand between two words of a stored phrase. Any run of real whitespace —
#: that is the hard wrapping — plus the escape sequences JSON writes a newline or a tab
#: as, because some of this store's prose is matched while it is still JSON-encoded
#: (``reports.body_json``, and the ``kyosei/export/*.jsonl`` ledgers).
_PHRASE_GAP = r"(?:\s|\\[nrt])+"

_WHITESPACE_RE = re.compile(r"\s+")


def flatten(text: str) -> str:
    """``text`` with every whitespace run collapsed to one space and the ends trimmed.

    The form to compare or display a phrase in when where it wrapped is not information:
    ``kyo ideas review`` flags the mention it found, and a flag carrying a raw line break
    would be reporting the wrapping rather than the token.
    """
    return _WHITESPACE_RE.sub(" ", text).strip()


def phrase_words(phrase: str) -> list[str]:
    """``phrase`` split on whitespace — empty for a phrase with no words in it."""
    return phrase.split()


def phrase_pattern(phrase: str) -> str:
    """Regex source matching ``phrase`` however the prose holding it is wrapped.

    Every word is escaped, so a phrase containing regex metacharacters is matched as
    the literal text it is. Raises :class:`ValueError` on a phrase with no words: an
    empty pattern matches at every position, and silently "finding" a blank needle
    everywhere is a worse failure than the one this function exists to fix.
    """
    words = phrase_words(phrase)
    if not words:
        raise ValueError("cannot build a phrase pattern from text with no words in it")
    return _PHRASE_GAP.join(re.escape(word) for word in words)


def phrase_regex(phrase: str, *, whole_word: bool = False) -> re.Pattern[str]:
    """:func:`phrase_pattern`, compiled. ``whole_word`` refuses a match that is part of
    a longer identifier — what symbol resolution (``path#symbol`` in a record's touches
    and in a ``governs:`` target) means by "contains that symbol"."""
    pattern = phrase_pattern(phrase)
    if whole_word:
        pattern = rf"(?<!\w){pattern}(?!\w)"
    return re.compile(pattern)


def contains_phrase(haystack: str, phrase: str, *, whole_word: bool = False) -> bool:
    """Is ``phrase`` in ``haystack``, ignoring how either one is wrapped?

    A phrase with no words in it is in nothing — the caller asked an empty question and
    gets a definite answer rather than an exception, because every caller here reaches
    this from user-supplied text.
    """
    if not phrase_words(phrase):
        return False
    return phrase_regex(phrase, whole_word=whole_word).search(haystack) is not None


def replace_phrase(haystack: str, phrase: str, replacement: str) -> tuple[str, int]:
    """``(text with every wrapping of ``phrase`` replaced, how many were replaced)``.

    The replacement is inserted literally — no backreference expansion — so a marker
    containing ``\\1`` cannot rewrite the text it is redacting.
    """
    if not phrase_words(phrase):
        return haystack, 0
    return phrase_regex(phrase).subn(lambda _match: replacement, haystack)


def read_lines(source: str, *, what: str) -> list[str]:
    """Read a one-per-line list.

    Law 6: an empty file means an empty list — zero items added, reported as zero.
    It is never silently treated as "no argument given".
    """
    text = read_prose(source, what=what)
    return [line.strip() for line in text.split("\n") if line.strip()]


def single_line(value: str, *, what: str) -> str:
    """Accept a one-line flag value, or refuse it loudly.

    KYO-52: the other half of the prose write path — ``--title`` and ``--why`` are
    stored prose too, and "a `kyo doctor` error pasted into a reason" is exactly the
    ``--why`` case. Same collector, same non-refusing warning.
    """
    cleaned = normalise(value)
    if "\n" in cleaned:
        raise UsageError(
            f"{what} must be a single line; multi-line prose enters only via a "
            f"--*-file flag or stdin (law 5)"
        )
    if not cleaned.strip():
        raise UsageError(f"{what} must not be empty")
    for found in home_paths(cleaned):
        if found not in _PENDING_HOME_PATHS:
            _PENDING_HOME_PATHS.append(found)
    return cleaned


# --------------------------------------- KYO-26 / KYO-91: what the shell leaves behind
#
# KYO-26 shipped a check that refused a flag value containing an *odd* number of
# backticks or double quotes, and said in its own docstring that an odd count "is the
# signature of a substitution that swallowed the text between them."
#
# It is not, and KYO-91 is that sentence turned into a defect. Command substitution is
# not a partial event: the shell removes the opening delimiter, the closing delimiter
# and everything between them, and puts the command's stdout there instead. What
# reaches this process contains ZERO backticks. Zero is even. The check passed it. The
# reproduction on KYO-91 stored `dead because  already handles it` — two spaces where
# `kyo rebuild` had been — with no refusal and no warning. Meanwhile the odd count it
# *does* fire on is the rarer shape, and an interactive shell will usually not even
# submit one, because it waits for the closing delimiter.
#
# So there are two things to be honest about at once.
#
# What is IMPOSSIBLE here: recovering, or even detecting, a substitution whose command
# printed something. Its output is now ordinary text in an ordinary argument, and no
# amount of inspection inside this process can distinguish it from prose the caller
# typed. That case is out of reach and nothing below pretends otherwise. Narrowing what
# may be an argument at all (`--why` for a phrase, a file past some length) is the only
# real answer to it, and that is a trade this record did not make.
#
# What is REACHABLE: the residue left when the command printed nothing — a command that
# failed, an unset `$VAR`, anything writing to stderr. The text vanishes and its
# surrounding spaces close up into a gap. That gap is a real signal, and it is also a
# gap a person occasionally types on purpose, so this has a false-positive rate that is
# not zero and is not pretended away — see `shell_residue`.

#: Characters that travel in pairs, so an *odd* count means one of the pair is gone.
#: Weak evidence of a mangling, not the signature of one (see above), and kept because
#: it does catch a value assembled by a script rather than typed at a prompt.
#:
#: The apostrophe is deliberately NOT here. It was, for about a minute, and this
#: repo's own suite refused it: "the premise died and this wasn't worth pursuing"
#: has an odd number of ``'`` and is perfectly ordinary English. A check that
#: fires on ordinary input is one people learn to route around, which would cost
#: more than the mangling it catches. Backticks are the real signal (a pair runs a
#: command); an unbalanced double quote is a weaker but still uncommon one.
_PAIRED = ("`", '"')

#: Any run of whitespace, so a finding can say how long it is and where it starts.
_GAP_RE = re.compile(r"\s+")

#: Stood in for the whitespace a finding names, because one space and two spaces are
#: the same picture in a terminal and the entire finding is about which one it is.
_GAP_MARK = "·"


def shell_residue(value: str) -> list[str]:
    """Signs that a shell removed text from ``value`` before ``kyo`` was a process.

    Returns one phrase per finding, in reading order; an empty list means nothing
    was found — which is emphatically **not** the same as "nothing was eaten". A
    substitution whose command printed output is invisible from in here, forever.

    The findings:

    * an **odd** number of backticks or double quotes — one of a pair is missing;
    * a **gap**: two or more whitespace characters in a row, or whitespace at
      either end of the value. This is what a substitution leaves when its command
      printed nothing, and it is the shape KYO-91 was reproduced with.

    The gap finding has a false-positive rate and it is a real one: a doubled space
    typed after a full stop, or a stray space inside the quotes, is this exact
    shape. Callers differ in what they do about that — :func:`check_not_shell_mangled`
    refuses, :func:`shell_residue_warning` says so and stores anyway — but the
    detection is the same, because the tool genuinely cannot tell the two apart.
    """
    findings: list[str] = []
    for character in _PAIRED:
        if value.count(character) % 2 == 1:
            findings.append(f"an unbalanced {character!r}")
    for match in _GAP_RE.finditer(value):
        run = match.group(0)
        if match.start() == 0:
            findings.append("whitespace before the first word")
        elif match.end() == len(value):
            findings.append("whitespace after the last word")
        elif len(run) > 1:
            findings.append(
                f"a {len(run)}-character gap at offset {match.start()}, "
                f"wide enough to have held a word"
            )
    return findings


def marked_residue(value: str) -> str:
    """``value`` with exactly the whitespace :func:`shell_residue` named made visible."""

    def mark(match: re.Match[str]) -> str:
        run = match.group(0)
        if match.start() == 0 or match.end() == len(value) or len(run) > 1:
            return _GAP_MARK * len(run)
        return run

    return _GAP_RE.sub(mark, value)


def check_not_shell_mangled(value: str, *, what: str) -> None:
    """Refuse a one-line flag value that a shell appears to have eaten part of.

    Law 5 says prose never arrives as an argument, and ``--why-file`` exists for
    that reason — but ``--why`` also exists, is the obvious path for a short
    reason, and is not safe. A consumer resolving a sweep flag with a reason
    containing backticks lost a word to command substitution before ``kyo`` ever
    saw the string, and the stored reason is wrong and (for a resolved flag) not
    correctable.

    The tool cannot stop a shell expanding a substitution before it is invoked,
    and — KYO-91 — it cannot *see* one either, because a substitution takes its
    own delimiters with it and leaves an argument that looks like any other. What
    it can see is :func:`shell_residue`: the gap left when the substituted command
    printed nothing, and the weaker odd-delimiter signal KYO-86 shipped.

    Call this only for a flag that has a ``--<name>-file`` twin, because the
    message names one: a refusal is only fair where there is a way to succeed.
    :func:`shell_residue_warning` is the form for the flags that have no such twin.
    """
    findings = shell_residue(value)
    if not findings:
        return
    raise UsageError(
        f"{what} looks like the shell removed part of it before kyo saw it: "
        f"{'; '.join(findings)}. What arrived was: {value!r}. A substitution takes its "
        f"own delimiters with it, so a gap where a word was is the only trace it leaves "
        f"— and kyo cannot tell that gap from one you typed. If this is what you meant, "
        f"pass it with {what}-file, which the shell does not touch."
    )


def shell_residue_warning(value: str, *, what: str) -> str | None:
    """The same findings said out loud, for a flag with no file-based twin.

    ``--title`` is required on ``kyo new`` and there is no ``--title-file``. A false
    positive there would not annoy someone; it would make filing a record impossible
    with no route around it. ``--do`` and ``--method`` are the same shape. So for a
    flag the parser offers no file form of, the residue is *stated* at the moment of
    the write and the value is stored exactly as given — loud, not silent, which is
    the whole of what KYO-91 asks for.

    Returns the warning line, or ``None`` when there is nothing to say.
    """
    findings = shell_residue(value)
    if not findings:
        return None
    return (
        f"  warning: {what} looks like the shell removed part of it before kyo saw it: "
        f"{'; '.join(findings)}. Stored as given, with the whitespace made visible: "
        f"{marked_residue(value)!r}. A substitution takes its own delimiters with it, so "
        f"a gap where a word was is the only trace it leaves — and kyo cannot tell that "
        f"gap from one you typed. There is no {what}-file to hand it over out of the "
        f"shell's reach; if the gap is not yours, restate the value with the text in "
        f"single quotes, which stop the shell expanding anything inside them."
    )


def fingerprint(text: str) -> str:
    """Short digest of stored text, printed on every write so corruption is visible."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
