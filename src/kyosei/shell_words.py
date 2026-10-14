"""What a Bash command line runs, and where — the one reading both blocking guards share.

KYO-409. ``kyo check-push --pre-tool-use`` (:mod:`kyosei.cmd_publish`) and ``kyo
check-command --pre-tool-use`` (:mod:`kyosei.cmd_check_command`) each used to carry a private
copy of a shell tokeniser, with identical separator tuples, and both copies missed the
same shapes: a single ``&`` was not a separator, so ``sleep 1 & git push`` read as one
command whose word was ``sleep``; a wrapper in front of the command (``env``,
``command``, ``nohup``, ``time``, ``timeout 900``) hid the command word; and the push
guard never asked *where* a push would run at all. Worse, the suite guard's own sibling
refusal (KYO-312) tells a worker to detach a long run with ``nohup <command> >
/tmp/run.log 2>&1 &`` — which walked straight past the suite guard. One reading, here,
means a shape fixed for one guard is fixed for both.

**What it reads.** Segments IN ORDER, split on ``&&``, ``||``, ``;``, ``|``, ``|&``,
a single ``&`` (backgrounding) and newlines — outside quotes, outside ``$( … )`` and
backticks, and never on the ``&`` of a redirection (``2>&1``, ``&>``). Comments and
heredoc bodies are skipped, so a ``git push`` inside a commit message's heredoc is text,
not a command. ``( … )`` subshells are *unwrapped*: the command inside is a command,
and a ``cd`` inside one is undone at its ``)``. Each segment's words come from
:func:`shlex.split`, falling back to a whitespace split — never an exception, because a
``PreToolUse`` hook that died on an unbalanced quote would block the tool call with a
traceback. Then, repeatedly: leading ``NAME=value`` assignments are collected as the
segment's environment, redirections are dropped, and a transparent wrapper
(:data:`WRAPPERS`) is stepped over, options and all.

**What it follows.** A running directory that starts at the caller's (the payload's
``cwd``), moves on ``cd``/``pushd``/``popd`` and ``env -C``, is restored at the end of a
subshell, and is *not* moved by a ``cd`` that runs in a pipeline or in the background
(each of those is its own subshell in every POSIX shell).

**What it does not, stated rather than discovered.** A variable or substitution
anywhere in a directory (``cd "$WT"``), ``cd -``, ``pushd +N``, ``$( … )`` and
backtick bodies (not searched for commands), shell keywords (``for … do git push``
reads ``do`` as the command word), ``{ … }`` groups, ``sudo``, ``xargs``, ``eval``,
``bash -c '…'``, and a command hidden inside a script. An unknown directory is
reported as ``None`` and never borrowed from the previous segment; both guards treat it
as *never refuse*, because a blocking hook that guesses is the one that gets disabled
(KYO-33).
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

#: The control operators that end a segment, longest first where one is a prefix of
#: another. Named for the docs and the tests; :func:`_scan` is what applies them.
SEPARATORS = ("&&", "||", ";;", "|&", ";", "|", "&", "\n")

#: The segment terminators after which the segment ran in a subshell of its own — a
#: pipeline element or a background job — so a ``cd`` there moves nobody else.
_ISOLATING = frozenset({"|", "|&", "&"})

#: Commands that run the command after them, in the same directory, having consumed
#: their own options. Each is stepped over by :func:`invocation`; see
#: :func:`_skip_wrapper` for the options each one takes.
WRAPPERS = frozenset(
    {"env", "command", "nohup", "time", "timeout", "gtimeout", "nice", "exec"}
)

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
_REDIRECTION = re.compile(r"^(?:\d+|&)?(?:>>|>\||>&|<&|<<<|<<-|<<|<>|>|<)(.*)$", re.S)
_WORD_END = frozenset(" \t\n;&|()<>")


# ------------------------------------------------------------------ segmenting


@dataclass(frozen=True)
class _Piece:
    #: ``"cmd"`` for a command segment, ``"("``/``")"`` for a subshell boundary.
    kind: str
    text: str = ""
    #: The operator that ended this segment (``""`` at end of line or before ``)``).
    terminator: str = ""


def _heredoc_word(command: str, index: int) -> tuple[str, bool, int]:
    """The delimiter of a ``<<WORD`` starting at ``index``, whether it is ``<<-``, and
    where the operator and its word end."""
    j = index + 2
    strip_tabs = command.startswith("-", j)
    if strip_tabs:
        j += 1
    while j < len(command) and command[j] in " \t":
        j += 1
    start = j
    quote: str | None = None
    while j < len(command):
        char = command[j]
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in _WORD_END:
            break
        j += 1
    word = command[start:j].replace("'", "").replace('"', "").replace("\\", "")
    return word, strip_tabs, j


def _scan(command: str) -> list[_Piece]:
    """The command line as ordered pieces. Never raises; an unclosed quote simply runs
    to the end of the line, which is what the shell would complain about anyway."""
    pieces: list[_Piece] = []
    buffer: list[str] = []
    heredocs: list[tuple[str, bool]] = []
    quote: str | None = None
    backtick = False
    substitution = 0
    index = 0
    length = len(command)

    def flush(terminator: str) -> None:
        text = "".join(buffer).strip()
        buffer.clear()
        if text:
            pieces.append(_Piece("cmd", text, terminator))
        elif pieces and pieces[-1].kind == ")" and terminator:
            # `( cd x ) & …` — the subshell as a whole was backgrounded or piped.
            pieces[-1] = _Piece(")", "", terminator)

    while index < length:
        char = command[index]
        if quote == "'":
            buffer.append(char)
            if char == "'":
                quote = None
            index += 1
            continue
        if char == "\\" and index + 1 < length:
            if command[index + 1] != "\n":  # a line continuation joins the lines
                buffer.append(command[index : index + 2])
            index += 2
            continue
        if quote == '"':
            buffer.append(char)
            if char == '"':
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            buffer.append(char)
            index += 1
            continue
        if char == "`":
            backtick = not backtick
            buffer.append(char)
            index += 1
            continue
        if backtick:
            buffer.append(char)
            index += 1
            continue
        if char in "$<>" and command.startswith("(", index + 1):
            substitution += 1
            buffer.append(command[index : index + 2])
            index += 2
            continue
        if substitution:
            if char == "(":
                substitution += 1
            elif char == ")":
                substitution -= 1
            buffer.append(char)
            index += 1
            continue
        # ---- top level
        last = buffer[-1][-1:] if buffer else ""
        if char == "#" and last in ("", " ", "\t"):
            newline = command.find("\n", index)
            index = length if newline < 0 else newline
            continue
        if command.startswith("<<", index) and not command.startswith("<<<", index):
            word, strip_tabs, end = _heredoc_word(command, index)
            if word:
                heredocs.append((word, strip_tabs))
            buffer.append(command[index:end])
            index = end
            continue
        if char == "\n":
            flush("\n")
            index += 1
            for word, strip_tabs in heredocs:
                while index < length:
                    newline = command.find("\n", index)
                    end = length if newline < 0 else newline
                    line = command[index:end]
                    index = end + 1
                    if (line.lstrip("\t") if strip_tabs else line) == word:
                        break
            heredocs.clear()
            continue
        pair = command[index : index + 2]
        if pair in ("&&", "||", ";;", "|&"):
            flush(pair)
            index += 2
            continue
        if char in ";|":
            flush(char)
            index += 1
            continue
        if char == "&":
            if last in (">", "<") or command.startswith(">", index + 1):
                buffer.append(char)  # 2>&1, >&2, &>file — a redirection, not a job
            else:
                flush("&")
            index += 1
            continue
        if char == "(":
            if "".join(buffer).strip():
                buffer.append(char)  # `f()`, `a=(1 2)` — not a subshell
            else:
                pieces.append(_Piece("("))
            index += 1
            continue
        if char == ")":
            flush("")
            pieces.append(_Piece(")"))
            index += 1
            continue
        buffer.append(char)
        index += 1
    flush("")
    return pieces


def segments(command: str) -> list[str]:
    """Every command segment of ``command``, in order, as written."""
    return [piece.text for piece in _scan(command) if piece.kind == "cmd"]


def tokens(segment: str) -> list[str]:
    """``shlex`` when it can, a whitespace split when it cannot. Never raises."""
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


# ------------------------------------------------------------------ invocations


@dataclass(frozen=True)
class Invocation:
    """What one segment runs, once the shell's own furniture is set aside."""

    #: The command and its arguments, wrappers, assignments and redirections removed.
    argv: tuple[str, ...]
    #: Every ``NAME=value`` in front of the command, including ``env NAME=value``.
    env: dict[str, str] = field(default_factory=dict)
    #: Directories a wrapper moves into before running it (``env -C DIR``), in order.
    chdir: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        """The command word's basename — ``/usr/bin/git`` is ``git``."""
        return Path(self.argv[0]).name if self.argv else ""


def _without_redirections(words: list[str]) -> list[str]:
    kept: list[str] = []
    skip_next = False
    for word in words:
        if skip_next:
            skip_next = False
            continue
        found = _REDIRECTION.match(word)
        if found:
            skip_next = found.group(1) == ""
            continue
        kept.append(word)
    return kept


def _skip_options(
    words: list[str], *, flags: Iterable[str] = (), valued: Iterable[str] = ()
) -> list[str]:
    """Drop leading options: ``flags`` alone, ``valued`` with the word after them, any
    other ``-x`` alone, ``--`` ending the options."""
    flags, valued = set(flags), set(valued)
    index = 0
    while index < len(words):
        word = words[index]
        if word == "--":
            return words[index + 1 :]
        if word in valued:
            index += 2
            continue
        if word.startswith("-") and word != "-":
            index += 1
            continue
        break
    return words[index:]


def _skip_wrapper(words: list[str], env: dict[str, str], chdir: list[str]) -> list[str] | None:
    """``words`` with its leading wrapper stepped over; ``None`` when what follows is
    not a command at all (``command -v git`` looks ``git`` up and runs nothing)."""
    name, rest = Path(words[0]).name, words[1:]
    if name == "env":
        index = 0
        while index < len(rest):
            word = rest[index]
            if word == "--":
                index += 1
                break
            if word in ("-u", "--unset"):
                index += 2
            elif word in ("-C", "--chdir"):
                if index + 1 < len(rest):
                    chdir.append(rest[index + 1])
                index += 2
            elif word.startswith("--chdir="):
                chdir.append(word.partition("=")[2])
                index += 1
            elif word in ("-S", "--split-string") or word.startswith("--split-string="):
                value = (
                    word.partition("=")[2]
                    if "=" in word
                    else (rest[index + 1] if index + 1 < len(rest) else "")
                )
                after = index + (1 if "=" in word else 2)
                return tokens(value) + rest[after:]
            elif word.startswith("-") and word != "-":
                index += 1
            elif word == "-" or _ASSIGNMENT.match(word):
                if word != "-":
                    key, _, value = word.partition("=")
                    env[key.rstrip("+")] = value
                index += 1
            else:
                break
        return rest[index:]
    if name == "command":
        if any(word in ("-v", "-V") for word in rest[:2]):
            return None
        return _skip_options(rest, flags=("-p",))
    if name == "nohup":
        return _skip_options(rest)
    if name == "time":
        return _skip_options(rest, flags=("-p", "-l", "-h"))
    if name in ("timeout", "gtimeout"):
        after = _skip_options(rest, valued=("-s", "-k", "--signal", "--kill-after"))
        return after[1:]  # the duration
    if name == "nice":
        return _skip_options(rest, valued=("-n", "--adjustment"))
    if name == "exec":
        return _skip_options(rest, valued=("-a",))
    return words  # pragma: no cover - only reached for a name not in WRAPPERS


def invocation(words: list[str]) -> Invocation:
    """Parse one segment's words into what it runs. Never raises."""
    env: dict[str, str] = {}
    chdir: list[str] = []
    rest = _without_redirections(list(words))
    while rest:
        if _ASSIGNMENT.match(rest[0]):
            key, _, value = rest[0].partition("=")
            env[key.rstrip("+")] = value
            rest = rest[1:]
            continue
        if Path(rest[0]).name in WRAPPERS:
            stepped = _skip_wrapper(rest, env, chdir)
            if stepped is None:
                return Invocation((), env, tuple(chdir))
            rest = stepped
            continue
        break
    return Invocation(tuple(rest), env, tuple(chdir))


# ------------------------------------------------------------------ directories


def moved(here: Path | None, target: str) -> Path | None:
    """Where moving from ``here`` to ``target`` lands, or ``None`` when that is not
    knowable — a variable, a substitution, an option, or a relative move from an
    unknown place. Law 6: unknown is reported as unknown, never as the last value."""
    if not target or target.startswith("-") or "$" in target or "`" in target:
        return None
    path = Path(target).expanduser()
    if not path.is_absolute():
        if here is None:
            return None
        path = here / path
    try:
        return path.resolve()
    except (OSError, RuntimeError):  # pragma: no cover - a path the OS refuses
        return None


def _cd_target(here: Path | None, args: list[str]) -> Path | None:
    args = [arg for arg in args if arg not in ("-P", "-L", "-e", "-@")]
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        return Path.home().resolve()
    if len(args) != 1:
        return None
    return moved(here, args[0])


@dataclass(frozen=True)
class Step:
    """One command a line runs, and the directory it runs in (``None``: unknown)."""

    segment: str
    invocation: Invocation
    cwd: Path | None


def walk(command: str, cwd: Path | None) -> list[Step]:
    """Every command ``command`` runs, in order, each with its directory.

    ``cd``/``pushd``/``popd`` segments are followed and not returned; a segment that
    runs nothing (a bare assignment, ``command -v x``) is returned with an empty argv,
    so a caller reading the environment it sets still sees it.
    """
    here = cwd
    subshells: list[Path | None] = []
    stack: list[Path | None] = []
    previous = ""
    steps: list[Step] = []
    for piece in _scan(command):
        if piece.kind == "(":
            subshells.append(here)
            continue
        if piece.kind == ")":
            if subshells:
                here = subshells.pop()
            previous = piece.terminator
            continue
        call = invocation(tokens(piece.text))
        isolated = piece.terminator in _ISOLATING or previous in ("|", "|&")
        previous = piece.terminator
        start = here
        for target in call.chdir:
            start = moved(start, target)
        if call.name in ("cd", "pushd", "popd"):
            args = list(call.argv[1:])
            if call.name == "popd":
                destination = stack.pop() if stack and not args else None
            elif call.name == "pushd":
                destination = (
                    moved(start, args[0])
                    if len(args) == 1 and not args[0].startswith("+")
                    else None
                )
                if not isolated:
                    stack.append(here)
            else:
                destination = _cd_target(start, args)
            if not isolated:
                here = destination
            continue
        steps.append(Step(piece.text, call, start))
    return steps
