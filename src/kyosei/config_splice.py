"""The comment-keeping writers of a project's settings, and the log of what they wrote.

KYO-954, the third build of proposal KYO-950 (plan KYO-951), sections 5.1, 5.2 and 5.5.

Two writers of a ``config.yml`` key used to exist and kept the file's comments in different
ways; one of them re-rendered the whole file when its substitution did not apply, which
drops every comment a project wrote. This module holds the one :func:`splice` that every
writer calls, and :func:`apply_change`, which reads the file, splices, validates, writes it
atomically and logs the attempt.

The rules of :func:`splice`, tried in order:

1. The key is present on one line with a scalar value: the value is replaced on that line,
   and a trailing comment and its spacing stay.
2. The key is absent and the file holds its commented example (``# worker_model: x``): that
   one commented line becomes the live line.
3. The key is absent and no example is present: the line is appended after a blank line,
   under the generated comment ``# <key>: read by <consumers>``. A nested key inserts only
   the lines of the levels that are missing, at the end of the deepest block that exists.
4. Removal: the value line is deleted and the comments above it stay.

Every candidate is parsed as the reader parses it (the duplicate-refusing loader), compared
with the intended dictionary, and handed to the caller's ``validate``, which for
``config.yml`` is :func:`kyosei.config.validate_config_text`: the writer and the reader
apply one parser. There is no fallback to a full re-render. A file the splice cannot change
exactly is refused, naming the line to edit by hand.

THE LOG. ``$KYOSEI_HOME/config-log.jsonl`` takes one JSON object per attempt, written as
``serve_send``'s log is: created private to the account, refused inside a git repository,
and opened before the write, so a write that cannot be logged does not happen. Its readers
are the ``last_change`` of ``kyo config`` (:mod:`kyosei.config_view`), ``kyo config --log``,
and the session-start brief, which states each change since the previous brief.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from .clock import now
from .errors import KyoseiError

LOG_FILE = "config-log.jsonl"

#: The outcomes a log row carries.
OUTCOME_WRITTEN = "written"
OUTCOME_REFUSED = "refused"
OUTCOME_FAILED = "failed"

#: The device a CLI writer's row names.
DEVICE_CLI = "cli"

_ABSENT = object()


@dataclass(frozen=True)
class Change:
    """A dotted key and its new scalar value, or its removal when ``remove`` is true."""

    key: str
    value: object = None
    remove: bool = False
    #: The value's text as it is to be written, where the default rendering is not the one
    #: the key has always carried (``state: on`` is written bare). It must read back as
    #: ``value`` or as the boolean YAML 1.1 reads it as; the candidate is checked.
    literal: str | None = None

    def text(self) -> str:
        return self.literal if self.literal is not None else scalar_text(self.value)

    def parsed(self) -> object:
        return yaml.safe_load(self.literal) if self.literal is not None else self.value


@dataclass(frozen=True)
class Applied:
    """What :func:`apply_change` did."""

    path: Path
    key: str
    previous: object
    value: object
    written: bool
    etag: str


# ---------------------------------------------------------------------------- reading


def etag_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _load(text: str, path: Path) -> dict:
    from .registry import load_yaml_refusing_duplicates

    try:
        raw = load_yaml_refusing_duplicates(text, path) or {}
    except yaml.YAMLError as exc:
        raise KyoseiError(
            f"{path} is not valid YAML: {exc} -- hand edit {path} until it parses"
        ) from exc
    if not isinstance(raw, dict):
        raise KyoseiError(
            f"{path} must hold a YAML mapping, found {type(raw).__name__} -- hand edit {path}"
        )
    return raw


def _dig(raw: dict, parts: list[str]) -> object:
    node: object = raw
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return _ABSENT
        node = node[part]
    return node


def read_value(text: str, key: str, path: Path) -> tuple[bool, object]:
    """Whether the dotted ``key`` is present in ``text``, and its parsed value."""
    found = _dig(_load(text, path), key.split("."))
    return (False, None) if found is _ABSENT else (True, found)


def _intended(raw: dict, change: Change, path: Path) -> dict:
    parts = change.key.split(".")
    result = copy.deepcopy(raw)
    node = result
    holder: dict | None = None
    for part in parts[:-1]:
        holder = node
        child = node.get(part)
        if part not in node or child is None:
            if change.remove:
                return result
            child = node[part] = {}
        if not isinstance(child, dict):
            raise KyoseiError(
                f"{path} key {change.key!r}: {part!r} holds a {type(child).__name__}, not a "
                f"mapping -- hand edit {path}"
            )
        node = child
    if change.remove:
        node.pop(parts[-1], None)
        if not node and holder is not None:
            holder[parts[-2]] = None  # the parent's line stays, with nothing under it
    else:
        node[parts[-1]] = change.parsed()
    return result


# ---------------------------------------------------------------------------- locating


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_value_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _block_end(lines: list[str], start: int, limit: int) -> int:
    """One past the last value line of the key that begins on ``start`` (a comment or a
    blank line after the last value line is not part of the block)."""
    base = _indent(lines[start])
    last = start
    for index in range(start + 1, limit):
        line = lines[index]
        if not _is_value_line(line):
            continue
        if _indent(line) > base or (_indent(line) == base and line.lstrip().startswith("- ")):
            last = index
            continue
        break
    return last + 1


def _find_key(
    lines: list[str], part: str, lo: int, hi: int, indent: int, key: str, path: Path
) -> int | None:
    pattern = re.compile(rf"^( *){re.escape(part)}[ \t]*:(?:[ \t]|$)")
    hits = [
        i for i in range(lo, hi) if (m := pattern.match(lines[i])) and len(m.group(1)) == indent
    ]
    if len(hits) > 1:
        raise KyoseiError(
            f"{path} writes {part!r} (of {key!r}) on more than one line, lines "
            f"{', '.join(str(h + 1) for h in hits)} -- hand edit {path}: keep one of them"
        )
    return hits[0] if hits else None


@dataclass
class _Found:
    index: int
    indent: int
    end: int


def _locate(lines: list[str], key: str, path: Path) -> tuple[list[_Found], int]:
    """The lines of each level of ``key`` that exist, and the index of the first missing
    level (``len(parts)`` when every level exists)."""
    parts = key.split(".")
    found: list[_Found] = []
    lo, hi, indent = 0, len(lines), 0
    for depth, part in enumerate(parts):
        index = _find_key(lines, part, lo, hi, indent, key, path)
        if index is None:
            return found, depth
        end = _block_end(lines, index, hi)
        found.append(_Found(index, indent, end))
        lo, hi = index + 1, end
        child = next((_indent(lines[i]) for i in range(lo, hi) if _is_value_line(lines[i])), None)
        if depth < len(parts) - 1:
            if child is None or child <= indent:
                return found, depth + 1
            indent = child
    return found, len(parts)


_SCALAR = (str, int, float, bool, type(None))
_NOT_BARE = ("[", "{", "|", ">", "&", "*", "!")


def _hand_edit(path: Path, found: _Found, lines: list[str], why: str) -> KyoseiError:
    shown = lines[found.index].strip()
    return KyoseiError(
        f"{path} line {found.index + 1} ({shown!r}) {why} -- hand edit {path} at line "
        f"{found.index + 1}"
    )


def scalar_text(value: object) -> str:
    """``value`` as one line of YAML, bare where YAML allows it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if not isinstance(value, _SCALAR):
        raise KyoseiError(f"only a scalar can be written to a single line; got {value!r}")
    dumped = yaml.safe_dump(value, default_flow_style=True, width=10_000)
    first = dumped.splitlines()[0]
    return first[:-4] if first.endswith(" ...") else first


def _trailing_comment(body: str, indent_text: str, key_part: str, old: object) -> str:
    """The comment after a value, with the spacing the project chose, or ``""``."""
    for offset in (i for i, char in enumerate(body) if char == "#"):
        if offset == 0 or body[offset - 1] not in " \t":
            continue
        head = body[:offset].rstrip()
        try:
            parsed = yaml.safe_load(head.strip())
        except yaml.YAMLError:
            continue
        if parsed == {key_part: old}:
            return body[len(head) :]
    return ""


def _replace_value(
    lines: list[str], found: _Found, part: str, old: object, new: str, path: Path
) -> list[str]:
    if found.end != found.index + 1:
        raise _hand_edit(path, found, lines, "carries a value over more than one line")
    if not isinstance(old, _SCALAR):
        raise _hand_edit(
            path,
            found,
            lines,
            f"holds a {type(old).__name__}, and a value of another shape is not replaced "
            "by a single scalar",
        )
    line = lines[found.index]
    body = line.rstrip("\r\n")
    ending = line[len(body) :]
    match = re.match(rf"^( *){re.escape(part)}([ \t]*:[ \t]*)(.*)$", body)
    if match is None:  # pragma: no cover - _find_key matched this line
        raise _hand_edit(path, found, lines, "was not read as a key and value")
    rest = match.group(3)
    if rest.startswith(_NOT_BARE):
        raise _hand_edit(path, found, lines, "writes its value in a form the splice does not edit")
    sep = match.group(2)
    if not sep.endswith((" ", "\t")):
        sep += " "
    if not rest:
        trailing = ""
    elif rest.startswith("#"):  # no value, only a comment: the comment moves after the value
        trailing = "  " + rest
    else:
        trailing = _trailing_comment(body, match.group(1), part, old)
    out = list(lines)
    out[found.index] = f"{match.group(1)}{part}{sep}{new}{trailing}{ending}"
    return out


_GENERATED = re.compile(r"^#[ \t]*[A-Za-z_]\w*[ \t]*: read by ")


def _example_index(lines: list[str], key: str) -> int | None:
    """The one commented line ``# key: token`` that is the key's example, or ``None``."""
    pattern = re.compile(rf"^#[ \t]?{re.escape(key)}[ \t]*:[ \t]*\S+[ \t]*$")
    hits = [
        i
        for i, line in enumerate(lines)
        if pattern.match(line.rstrip("\r\n")) and not _GENERATED.match(line)
    ]
    return hits[0] if len(hits) == 1 else None


def _lines_for(parts: list[str], value: str, indent: int, step: int = 2) -> list[str]:
    out = []
    for depth, part in enumerate(parts[:-1]):
        out.append(" " * (indent + depth * step) + f"{part}:")
    out.append(" " * (indent + (len(parts) - 1) * step) + f"{parts[-1]}: {value}")
    return out


def _generated_comment(top: str) -> str | None:
    from .schema import CONFIG_CONSUMERS

    consumers = CONFIG_CONSUMERS.get(top)
    return f"# {top}: read by {', '.join(consumers)}" if consumers else None


def _candidate(text: str, change: Change, path: Path) -> str:
    lines = text.splitlines(keepends=True)
    parts = change.key.split(".")
    found, missing = _locate(lines, change.key, path)
    if missing == len(parts):  # every level exists
        last = found[-1]
        if change.remove:
            if last.end != last.index + 1:
                raise _hand_edit(path, last, lines, "carries a value over more than one line")
            out = list(lines)
            del out[last.index]
            return "".join(out)
        _, old = read_value(text, change.key, path)
        return "".join(_replace_value(lines, last, parts[-1], old, change.text(), path))
    if change.remove:
        return text  # nothing to remove
    # The key is absent.
    if missing == 0 and len(parts) == 1:
        example = _example_index(lines, parts[0])
        if example is not None:  # rule 2
            ending = lines[example][len(lines[example].rstrip("\r\n")) :] or "\n"
            out = list(lines)
            out[example] = f"{parts[0]}: {change.text()}{ending}"
            return "".join(out)
    if missing == 0:  # rule 3, the whole key is new
        lead = ""
        if text and not text.endswith("\n"):
            lead = "\n"
        if text.strip() and not text.endswith("\n\n"):
            lead += "\n"
        comment = _generated_comment(parts[0])
        block = ([comment] if comment else []) + _lines_for(parts, change.text(), 0)
        return text + lead + "\n".join(block) + "\n"
    # An ancestor exists: insert the missing levels at the end of its block.
    ancestor = found[missing - 1]
    ancestor_line = lines[ancestor.index].rstrip("\r\n")
    _, current = read_value(text, ".".join(parts[:missing]), path)
    after_colon = ancestor_line.split(":", 1)[1].strip()
    if (current is not None and not isinstance(current, dict)) or (
        after_colon and not after_colon.startswith("#")
    ):
        raise _hand_edit(path, ancestor, lines, "is not a block mapping the splice can extend")
    if ancestor.end != ancestor.index + 1 and not isinstance(current, dict):
        raise _hand_edit(path, ancestor, lines, "is not a block mapping the splice can extend")
    child_indent = next(
        (
            _indent(lines[i])
            for i in range(ancestor.index + 1, ancestor.end)
            if _is_value_line(lines[i])
        ),
        ancestor.indent + 2,
    )
    step = max(child_indent - ancestor.indent, 2)
    new_lines = _lines_for(parts[missing:], change.text(), child_indent, step)
    out = list(lines)
    if out and not out[ancestor.end - 1].endswith("\n"):
        out[ancestor.end - 1] += "\n"
    out[ancestor.end : ancestor.end] = [line + "\n" for line in new_lines]
    return "".join(out)


def splice(
    text: str,
    change: Change,
    *,
    validate: Callable[[str], None],
    path: Path | None = None,
) -> str:
    """``text`` with ``change`` applied by the four rules of the module docstring.

    Every line other than the changed, added or removed one is returned byte for byte as it
    was. Raises :class:`KyoseiError` naming the hand edit when the file cannot be changed
    exactly, or when the candidate does not read back as the intended change or fails
    ``validate``.
    """
    where = path if path is not None else Path("config.yml")
    raw = _load(text, where)
    intended = _intended(raw, change, where)
    candidate = _candidate(text, change, where)
    if candidate == text:
        return text
    try:
        parsed = _load(candidate, where)
    except KyoseiError as exc:
        raise KyoseiError(
            f"{where} could not be changed to {change.key}: "
            f"{'removed' if change.remove else change.text()} without breaking "
            f"it ({exc}) -- hand edit {where}"
        ) from exc
    if parsed != intended:
        raise KyoseiError(
            f"{where} could not be changed at {change.key} without disturbing another line "
            f"-- hand edit {where}: set `{change.key.split('.')[-1]}` by hand"
        )
    validate(candidate)
    return candidate


# ---------------------------------------------------------------------------- the log


def log_path(environ: dict[str, str]) -> Path:
    from .config import store_home

    return store_home(environ) / LOG_FILE


def _log_refusal(path: Path, why: str) -> KyoseiError:
    return KyoseiError(
        f"nothing was written, because the configuration log {path} cannot be used: {why}. "
        "A setting is only written when the change can be recorded"
    )


def open_log(environ: dict[str, str]) -> int:
    """The log, open to append, private to the account. Raises when it cannot be used."""
    from .transcript_archive import enclosing_git_repository

    path = log_path(environ)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        repository = enclosing_git_repository(path.parent)
        if repository is not None:
            raise _log_refusal(
                path,
                f"it would lie inside the git repository at {repository}, where it could be "
                "committed; point $KYOSEI_HOME at a directory outside every git repository",
            )
        return os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    except OSError as exc:
        raise _log_refusal(path, str(exc)) from exc


def _logged(exc: KyoseiError) -> KyoseiError:
    """``exc``, marked as already a row of the log, so a caller that logs refusals of its
    own (the served routes, KYO-956) does not write a second row for the same attempt."""
    exc.logged = True  # type: ignore[attr-defined]
    return exc


def was_logged(exc: BaseException) -> bool:
    return bool(getattr(exc, "logged", False))


def _append(descriptor: int, row: dict) -> None:
    os.write(descriptor, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))


def read_log(environ: dict[str, str]) -> list[dict]:
    """Every row of the log, oldest first; a line that is not a row is skipped."""
    path = log_path(environ)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def latest_changes(environ: dict[str, str], file: Path | str) -> dict[str, dict]:
    """The latest written row of the log for each key of ``file``."""
    latest: dict[str, dict] = {}
    for row in read_log(environ):
        if row.get("outcome") == OUTCOME_WRITTEN and row.get("file") == str(file):
            latest[str(row.get("key"))] = row
    return latest


def changes_since(environ: dict[str, str], file: Path | str, since: str | None) -> list[dict]:
    """The written rows of ``file`` after the instant ``since``; none without one."""
    if since is None:
        return []
    return [
        row
        for row in read_log(environ)
        if row.get("outcome") == OUTCOME_WRITTEN
        and row.get("file") == str(file)
        and str(row.get("at", "")) > since
        and row.get("previous") != row.get("new")
    ]


def shown(value: object) -> str:
    return "(absent)" if value is None else json.dumps(value, ensure_ascii=False)


def change_line(row: dict) -> str:
    """One plain line stating a logged change, asking nothing."""
    who = str(row.get("device") or DEVICE_CLI)
    if row.get("actor") and who == DEVICE_CLI:
        who = f"{who} ({row['actor']})"
    return (
        f"config: {row.get('key')} changed from {shown(row.get('previous'))} to "
        f"{shown(row.get('new'))} by {who} at {row.get('at')}"
    )


# ---------------------------------------------------------------------------- writing


def log_refusal(
    environ: dict[str, str],
    *,
    project: str,
    file: Path,
    key: str,
    reason: str,
    actor: str | None = None,
    device: str = DEVICE_CLI,
    device_id: str | None = None,
) -> None:
    """Append a refused attempt that was refused before the file was read. The refusal itself
    is what the caller reports, so a log that cannot be opened is not reported over it."""
    try:
        descriptor = open_log(environ)
    except KyoseiError:
        return
    try:
        _append(
            descriptor,
            {
                "at": now(),
                "device": device,
                "device_id": device_id or device,
                "actor": actor,
                "project": project,
                "file": str(file),
                "key": key,
                "previous": None,
                "new": None,
                "outcome": OUTCOME_REFUSED,
                "reason": reason,
            },
        )
    finally:
        os.close(descriptor)


def _probe_refusal(root: Path, environ: dict[str, str]) -> str | None:
    """Why the ``kyo`` on PATH cannot open the store at ``root``, or ``None``."""
    from .config import probe_path_build

    build = probe_path_build(root, environ=environ)
    if build is None or build.can_open is not False:
        return None
    return build.problem


def apply_change(
    *,
    path: Path,
    change: Change,
    validate: Callable[[str], None],
    environ: dict[str, str],
    project: str,
    actor: str | None = None,
    device: str = DEVICE_CLI,
    device_id: str | None = None,
    precheck: Callable[[bool, object], None] | None = None,
    expected_etag: str | None = None,
    probe_root: Path | None = None,
) -> Applied:
    """Read ``path``, splice ``change`` into it, validate, write it atomically, and log.

    The log is opened first, so a write that cannot be logged does not happen. ``precheck``
    receives whether the key is present and its current value and raises
    :class:`KyoseiError` to refuse. ``expected_etag`` is compared with the file's digest
    when it is read and again immediately before the rename. A write that adds a key is
    followed by a question to the ``kyo`` on PATH, and the previous bytes are restored when
    it can no longer open the store (``probe_root`` is that store's root).
    """
    from .export import atomic_write_text

    descriptor = open_log(environ)
    row: dict = {
        "at": now(),
        "device": device,
        "device_id": device_id or device,
        "actor": actor,
        "project": project,
        "file": str(path),
        "key": change.key,
        "previous": None,
        "new": None if change.remove else change.value,
    }
    try:
        try:
            if not path.is_file():
                raise KyoseiError(f"{path} does not exist, so there is nothing to change in it")
            data = path.read_bytes()
            if expected_etag is not None and etag_of(data) != expected_etag:
                raise KyoseiError(
                    f"{path} changed since it was read (its digest is {etag_of(data)}, not "
                    f"{expected_etag}); read it again and repeat the change"
                )
            text = data.decode("utf-8")
            present, previous = read_value(text, change.key, path)
            row["previous"] = previous if present else None
            if precheck is not None:
                precheck(present, previous)
            candidate = splice(text, change, validate=validate, path=path)
        except KyoseiError as exc:
            _append(descriptor, {**row, "outcome": OUTCOME_REFUSED, "reason": str(exc)})
            _logged(exc)
            raise
        if candidate == text:
            return Applied(path, change.key, row["previous"], row["new"], False, etag_of(data))
        if path.read_bytes() != data:
            reason = (
                f"{path} changed while the change was being prepared; read it again and "
                "repeat the change"
            )
            _append(descriptor, {**row, "outcome": OUTCOME_REFUSED, "reason": reason})
            raise _logged(KyoseiError(reason))
        atomic_write_text(path, candidate)
        if not present and not change.remove:
            problem = _probe_refusal(probe_root, environ) if probe_root is not None else None
            if problem is not None:
                atomic_write_text(path, text)
                reason = (
                    f"the `kyo` on PATH cannot open the store with {change.key} added "
                    f"({problem}); {path} was restored to its previous bytes. Install this "
                    "build as the one on PATH, then repeat the change"
                )
                _append(descriptor, {**row, "outcome": OUTCOME_FAILED, "reason": reason})
                raise _logged(KyoseiError(reason))
        _append(descriptor, {**row, "outcome": OUTCOME_WRITTEN, "reason": None})
        return Applied(
            path, change.key, row["previous"], row["new"], True, etag_of(candidate.encode())
        )
    finally:
        os.close(descriptor)


def apply_edit(
    *,
    path: Path,
    key: str,
    edit: Callable[[str], tuple[str, object, object]],
    environ: dict[str, str],
    project: str,
    actor: str | None = None,
    device: str = DEVICE_CLI,
    device_id: str | None = None,
    expected_etag: str | None = None,
) -> Applied:
    """:func:`apply_change` for a file whose edit is not a key's value: read ``path``, let
    ``edit`` return ``(candidate, previous, new)``, write it atomically and log it under
    ``key``.

    KYO-955. An agent type's ``model:`` and ``effort:`` lines are two lines of a front matter,
    which :func:`splice` does not address. ``edit`` raises :class:`KyoseiError` to refuse and
    does its own validation; the log, the etag compared at the read and again before the
    rename, and the atomic write are the ones of :func:`apply_change`.
    """
    from .export import atomic_write_text

    descriptor = open_log(environ)
    row: dict = {
        "at": now(),
        "device": device,
        "device_id": device_id or device,
        "actor": actor,
        "project": project,
        "file": str(path),
        "key": key,
        "previous": None,
        "new": None,
    }
    try:
        try:
            if not path.is_file():
                raise KyoseiError(f"{path} does not exist, so there is nothing to change in it")
            data = path.read_bytes()
            if expected_etag is not None and etag_of(data) != expected_etag:
                raise KyoseiError(
                    f"{path} changed since it was read (its digest is {etag_of(data)}, not "
                    f"{expected_etag}); read it again and repeat the change"
                )
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise KyoseiError(f"{path} is not UTF-8 text ({exc.reason})") from exc
            candidate, previous, new = edit(text)
            row["previous"], row["new"] = previous, new
        except KyoseiError as exc:
            _append(descriptor, {**row, "outcome": OUTCOME_REFUSED, "reason": str(exc)})
            _logged(exc)
            raise
        if candidate == text:
            return Applied(path, key, previous, new, False, etag_of(data))
        if path.read_bytes() != data:
            reason = (
                f"{path} changed while the change was being prepared; read it again and "
                "repeat the change"
            )
            _append(descriptor, {**row, "outcome": OUTCOME_REFUSED, "reason": reason})
            raise _logged(KyoseiError(reason))
        atomic_write_text(path, candidate)
        _append(descriptor, {**row, "outcome": OUTCOME_WRITTEN, "reason": None})
        return Applied(path, key, previous, new, True, etag_of(candidate.encode()))
    finally:
        os.close(descriptor)
