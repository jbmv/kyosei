"""``$KYOSEI_HOME/settings.yml`` — settings that belong to the MACHINE, not to a project
(KYO-462).

The first and only key is ``orchestrator_command``: the argument list ``kyo orchestrator
start`` runs in an orchestrator session (KYO-458), where the literal ``{prefix}`` is
replaced by the project's prefix wherever it appears in an argument. A missing file, or
a file without the key, means ``["claude"]`` — claude with no arguments, what a new
install runs. An example value is::

    orchestrator_command: ["claude", "--chrome", "--remote-control", "{prefix}"]

KYO-519 (from KYO-513): before the controller -> orchestrator rename the key was spelled
``controller_command``, and that spelling is read permanently. Both present with different
values is refused by name. Nothing writes the file: the operator renames the key by hand,
and only once this machine's ``kyo`` is at least KYO-519, because an older one refuses the
new spelling and ``kyo orchestrator start`` fails on this machine.

WHY NOT A PROJECT'S ``kyosei/config.yml``. Two reasons, both in the record. Which
flags claude takes on this machine (``--chrome`` needs this machine's browser; a
Remote Control name is this machine's account) is a property of the machine, not of a
project. And an unknown key in a project's config is refused by an older installed
build, which then blinds every hook in that project (KYO-335) — a machine setting
placed there would be a project-wide outage waiting for the next version skew. So
nothing here opens a project's store: :func:`load` reads one file under
``$KYOSEI_HOME`` (or ``~/kyosei``) and nothing else.

STRICT, AND BY NAME. A malformed file is refused rather than half-read: a typo'd key
silently falling back to the default would start the wrong command in a session nobody
is watching. Every refusal names the file, what is wrong, and the value that works.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import store_home
from .errors import KyoseiError

#: The file's name under ``$KYOSEI_HOME``.
SETTINGS_FILE = "settings.yml"

#: The key naming the orchestrator session's argument list.
ORCHESTRATOR_COMMAND_KEY = "orchestrator_command"

#: KYO-519 (from KYO-513): the same key before the rename, read permanently.
CONTROLLER_COMMAND_KEY = "controller_command"

#: What a missing file or key means: claude, with no arguments.
DEFAULT_CONTROLLER_COMMAND: tuple[str, ...] = ("claude",)

#: The placeholder replaced by the project's prefix, wherever it appears in an argument.
PREFIX_PLACEHOLDER = "{prefix}"

#: Every key this build knows. Anything else is refused by name.
KNOWN_KEYS: tuple[str, ...] = (ORCHESTRATOR_COMMAND_KEY, CONTROLLER_COMMAND_KEY)

#: The value a refusal offers as working: the default, spelled as the file would hold it.
_EXAMPLE = f'{ORCHESTRATOR_COMMAND_KEY}: ["claude", "--remote-control", "{PREFIX_PLACEHOLDER}"]'


@dataclass(frozen=True)
class HomeSettings:
    """The machine's settings, as read (or defaulted) from :func:`settings_path`."""

    path: Path
    controller_command: tuple[str, ...]
    #: ``"file"`` when the command came from the file, ``"default"`` otherwise.
    source: str

    def controller_argv(self, prefix: str) -> list[str]:
        """The command for one project: ``{prefix}`` replaced in every argument."""
        return [arg.replace(PREFIX_PLACEHOLDER, prefix) for arg in self.controller_command]


def settings_path(environ: Mapping[str, str] | None = None) -> Path:
    return store_home(dict(environ) if environ is not None else None) / SETTINGS_FILE


def _refuse(path: Path, problem: str) -> KyoseiError:
    return KyoseiError(
        f"{path} {problem}; fix it so it reads, for example, `{_EXAMPLE}` (a YAML list "
        f"of strings, where {PREFIX_PLACEHOLDER} is replaced by the project's prefix), "
        f"or remove the file to run the default, `{DEFAULT_CONTROLLER_COMMAND[0]}` with "
        "no arguments"
    )


def load(environ: Mapping[str, str] | None = None) -> HomeSettings:
    """Read ``$KYOSEI_HOME/settings.yml``; refuse a malformed one by name."""
    path = settings_path(environ)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return HomeSettings(path, DEFAULT_CONTROLLER_COMMAND, "default")
    except OSError as exc:
        raise _refuse(path, f"could not be read ({exc.strerror or exc})") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        raise _refuse(path, f"is not YAML{where}") from exc
    if raw is None:
        return HomeSettings(path, DEFAULT_CONTROLLER_COMMAND, "default")
    if not isinstance(raw, dict):
        raise _refuse(path, f"is a YAML {type(raw).__name__}, not a mapping of settings")
    unknown = sorted(str(key) for key in raw if key not in KNOWN_KEYS)
    if unknown:
        raise _refuse(
            path,
            f"has a key this build does not know ({', '.join(unknown)}; it knows "
            f"{', '.join(KNOWN_KEYS)})",
        )
    present = [key for key in (ORCHESTRATOR_COMMAND_KEY, CONTROLLER_COMMAND_KEY) if key in raw]
    if not present:
        return HomeSettings(path, DEFAULT_CONTROLLER_COMMAND, "default")
    if len(present) == 2 and raw[ORCHESTRATOR_COMMAND_KEY] != raw[CONTROLLER_COMMAND_KEY]:
        raise KyoseiError(
            f"{path} gives both {ORCHESTRATOR_COMMAND_KEY} and {CONTROLLER_COMMAND_KEY}, "
            "with different values, and they are two spellings of one key (KYO-519); "
            f"keep one of them -- {CONTROLLER_COMMAND_KEY} while this machine's kyo is "
            f"older than KYO-519, which refuses {ORCHESTRATOR_COMMAND_KEY}"
        )
    key = present[0]
    value = raw[key]
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(arg, str) and arg for arg in value)
    ):
        raise _refuse(
            path,
            f"gives {key} as {value!r}, which is not a non-empty list "
            "of non-empty strings",
        )
    return HomeSettings(path, tuple(value), "file")
