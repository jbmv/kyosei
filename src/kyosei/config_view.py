"""The read of a project's and the machine's configuration (KYO-950 section 3 and 4, KYO-952).

One builder, :func:`build_project_document` and :func:`build_machine_document`, assembles the
documents that ``kyo config`` prints and that ``GET /v1/projects/{project}/config`` and
``GET /v1/settings`` serve (:mod:`kyosei.serve_config`). It reads files and writes nothing,
opens no database, and runs no command that does.

THE CLASS REGISTRY. :data:`SETTINGS` and :data:`DECISIONS` place every setting in one class:

- ``shown``: read, with no control;
- ``editable``: changed by the viewer through a writer, whose descriptor (``write``) the
  document carries as data; no writer exists in this build, so the descriptor names a route
  that is not yet served;
- ``request``: shown, with a request to the session as the way to change it;
- ``omitted``: a setup decision that stores nothing a view could show.

Every key of :data:`kyosei.schema.CONFIG_CONSUMERS`, every key of
:data:`kyosei.home_settings.KNOWN_KEYS` and every decision of the setup guide's section 3.4
is covered by exactly one row; ``tests/test_kyo950_config_view.py`` fails when one is not, so
a key registered later cannot reach a project without being placed. A client holds no list of
keys: it draws whatever the document lists.

WHAT IS READ. A project's ``kyosei/config.yml`` is read as text and as YAML here, apart from
:func:`kyosei.config.load_config`, so that a file this build refuses is still shown: the
document then carries ``readable: false``, the refusal verbatim, and the values of the raw
file. The comment above a key and the lines it occupies are found in the text.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .errors import KyoseiError

CLASS_SHOWN = "shown"
CLASS_EDITABLE = "editable"
CLASS_REQUEST = "request"
CLASS_OMITTED = "omitted"
CLASS_VALUES = (CLASS_SHOWN, CLASS_EDITABLE, CLASS_REQUEST, CLASS_OMITTED)

#: What replaces the value of a ``NAME=value`` argument of the orchestrator command in a
#: document served to a device (section 4.3).
WITHHELD = "(withheld)"

#: The first line of a request, in the form the skill teaches the orchestrator to read.
REQUEST_HEADER = "[kyo-config-request]"

#: The two agent types Kyosei packages, and the only ones the view edits.
AGENT_TYPES = ("kyosei-worker", "kyosei-designer")

#: Files beside ``config.yml`` whose names the document lists (section 4.4, ``beside``).
BESIDE_FILES = ("kinds.yml", "scopes.yml", "documents.yml", "gate-tags.yml", "procedures.md")


@dataclass(frozen=True)
class Setting:
    """One row of the registry: a setting, or the spellings of one, and its class."""

    id: str
    #: The spellings in the file, dotted for a nested key; the row's value is the one present
    #: spelling when ``alias`` is true and a mapping of the present ones otherwise.
    keys: tuple[str, ...]
    file: str
    decision: str | None
    section: str
    klass: str
    why: str
    absent_means: str
    alias: bool = False
    #: The label of the ``kyo onboard --check`` item that reads this setting back.
    item: str | None = None
    write: dict | None = None

    @property
    def top_keys(self) -> tuple[str, ...]:
        return tuple(sorted({key.split(".", 1)[0] for key in self.keys}))


@dataclass(frozen=True)
class Decision:
    """A decision of section 3.4 that holds no key of its own."""

    id: str
    title: str
    document: str  # "project" or "machine"
    klass: str
    why: str
    #: ``kyo onboard --check`` item labels that read the decision back.
    items: tuple[str, ...] = ()
    #: A request control is also offered although the class is ``shown``.
    request_also: bool = False
    #: The file a request about it names, when there is one.
    file: str | None = None


def _write(route: str, value: dict, *, clearable: bool = False, one_way: str | None = None) -> dict:
    return {
        "route": route,
        "value": value,
        "clearable": clearable,
        "permission": "config",
        "one_way": one_way,
    }


def _model_pattern() -> str:
    from .cmd_install_agents import _MODEL

    return _MODEL.pattern


#: The sentence a client shows before a change it cannot undo (amendment 2 of the proposal).
READ_TRANSCRIPTS_ONE_WAY = (
    "Turning this off cannot be undone from this view. Only a request to the session, or an "
    "edit of config.yml at the machine, turns it on again, and until then no device can read "
    "this project's session transcripts."
)


def _project_settings() -> tuple[Setting, ...]:
    c = "config"
    return (
        Setting(
            "schema_version", ("schema_version",), c, None, "identity", CLASS_SHOWN,
            "Stamped from the build by `write_config` and `kyo rebuild`; a hand change hides "
            "the database and config drift that KYO-5 reports.",
            "the file is refused: the key is required",
        ),
        Setting(
            "project_prefix", ("project_prefix",), c, "B3", "identity", CLASS_SHOWN,
            "Every record id carries it, and it cannot be changed once records cite it.",
            "the file is refused: the key is required", item="prefix",
        ),
        Setting(
            "export_dir", ("export_dir",), c, None, "identity", CLASS_SHOWN,
            "Required key; moving it moves the ledger export, which is a store operation.",
            "the file is refused: the key is required",
        ),
        Setting(
            "layout_at_init", ("layout_at_init",), c, "B1", "identity", CLASS_SHOWN,
            "A historical fact, never the live layout, which `kyo where` reports.",
            "the project predates the key; the push guard treats the layout as in-repo",
        ),
        Setting(
            "publication_acknowledged", ("publication_acknowledged",), c, None, "identity",
            CLASS_SHOWN,
            "A person's acceptance that pushing publishes an in-repo store, recorded at a "
            "push by `kyo check-push --acknowledge`; a device must not be able to give it.",
            "nobody has accepted publication, so the push guard warns",
        ),
        Setting(
            "code_repo_id", ("code_repo_id",), c, "A1", "identity", CLASS_SHOWN,
            "The code repository's identity, recorded automatically when the store was made.",
            "the store was not scaffolded for a recorded repository",
        ),
        Setting(
            "trunk", ("trunk",), c, "B4", "identity", CLASS_REQUEST,
            "`kyo check-branch` measures a worker's cut point from it, and whether the name "
            "is a real ref is checked only where it is used, so the change belongs with an "
            "agent that can check the ref.",
            "git's own answer is used (origin/HEAD, init.defaultBranch, then main or master)",
        ),
        Setting(
            "worker_model", ("worker_model",), c, "D1", "workers", CLASS_EDITABLE,
            "A single model id read at each dispatch; the writer applies the model pattern "
            "`install-agents` uses, which is stricter than the reader.",
            "a brief names no model and the harness picks one", item="worker_model",
            write=_write(
                "config/worker_model",
                {"type": "string", "pattern": _model_pattern()},
                clearable=True,
            ),
        ),
        Setting(
            "worktree_setup", ("worktree_setup",), c, "D4", "workers", CLASS_REQUEST,
            "A list of commands with notes, and the notes carry the project's reasoning.",
            "a brief states that nothing is declared for a fresh worktree",
            item="worktree_setup",
        ),
        Setting(
            "orchestrator_only_commands",
            ("orchestrator_only_commands", "controller_only_commands"), c, "D5", "workers",
            CLASS_REQUEST,
            "A list read by a blocking hook; the row names the spelling the file uses.",
            "no command is refused to a worker", alias=True, item="orchestrator_only_commands",
        ),
        Setting(
            "worker_command_timeout_ceiling_seconds",
            ("worker_command_timeout_ceiling_seconds",), c, "D3", "workers", CLASS_EDITABLE,
            "One whole number of seconds, or absent.",
            "no timeout a worker declares is refused",
            write=_write(
                "config/worker_command_timeout_ceiling_seconds",
                {"type": "integer", "minimum": 1},
                clearable=True,
            ),
        ),
        Setting(
            "close_wave", ("close_wave",), c, None, "workers", CLASS_REQUEST,
            "Three command lists, run by `kyo close-wave`.",
            "`kyo close-wave` has no project commands to run",
        ),
        Setting(
            "guard_tests", ("guard_tests",), c, None, "workers", CLASS_REQUEST,
            "Glob rules and test lists, with the project's reasons in comments.",
            "a brief states no guard tests",
        ),
        Setting(
            "docs_suggest_notice", ("docs_suggest_notice",), c, "D8", "notices", CLASS_REQUEST,
            "A boolean whose only job is to stop a `kyo doctor` notice once the project has "
            "read what it reported; a switch would let the notice be silenced unread.",
            "`kyo doctor` may print the notice",
        ),
        Setting(
            "kind_grants_notice", ("kind_grants_notice",), c, "D6", "notices", CLASS_REQUEST,
            "A boolean whose only job is to stop a `kyo doctor` notice once the project has "
            "read what it reported; a switch would let the notice be silenced unread.",
            "`kyo doctor` may print the notice",
        ),
        Setting(
            "kind_policy_notice", ("kind_policy_notice",), c, "D6", "notices", CLASS_REQUEST,
            "A boolean whose only job is to stop a `kyo doctor` notice once the project has "
            "read what it reported; a switch would let the notice be silenced unread.",
            "`kyo doctor` may print the notice",
        ),
        Setting(
            "onboarding", ("onboarding",), c, "F", "notices", CLASS_REQUEST,
            "The declined and deferred setup answers; an answer of declined is the person's, "
            "so a change is a request.",
            "no setup decision is recorded as declined or deferred", item="onboarding",
        ),
        Setting(
            "recx.prior_art.state", ("recx.prior_art.state",), c, "D12", "prior-art",
            CLASS_EDITABLE,
            "Written by `kyo prior-art-setting`; `on` is offered only while the extra is "
            "installed.",
            "prior-art search is not chosen",
            write=_write(
                "config/recx.prior_art.state", {"type": "string", "enum": ["on", "off"]}
            ),
        ),
        Setting(
            "recx.prior_art.on",
            ("recx.prior_art.on", "recx.prior_art.limit", "recx.prior_art.index_url"), c,
            "D12", "prior-art", CLASS_REQUEST,
            "The trigger, the result limit and the index address are edited by hand.",
            "the build's defaults apply",
        ),
        Setting(
            "people", ("people",), c, "D13", "people", CLASS_REQUEST,
            "A credit can name only a declared person, and adding one turns enforcement on "
            "for later rows.",
            "no person is declared, so a credit may name any",
        ),
        Setting(
            "read_transcripts", ("read_transcripts",), c, "D14", "people", CLASS_EDITABLE,
            "A device can turn it off, which narrows what every token reads; turning it on "
            "is a request, since it opens every session read route to the project's tokens.",
            "transcripts are not read: the session routes refuse", item="read_transcripts",
            write=_write(
                "config/read_transcripts",
                {"type": "boolean", "values": [False]},
                one_way=READ_TRANSCRIPTS_ONE_WAY,
            ),
        ),
        Setting(
            "docs_coverage.level", ("docs_coverage.level",), c, "D8", "docs", CLASS_EDITABLE,
            "Written by `kyo docs coverage --level`, which refuses `problem` without a "
            "`code:` list.",
            "coverage is not reported", item="docs_coverage",
            write=_write(
                "config/docs_coverage.level",
                {"type": "string", "enum": ["off", "notice", "problem"]},
            ),
        ),
        Setting(
            "docs_coverage.code",
            ("docs_coverage.code", "docs_coverage.unmapped"), c, "D8", "docs", CLASS_REQUEST,
            "Glob lists, with a reason for each path not expected to be governed.",
            "no code globs are declared",
        ),
    )


def _machine_settings() -> tuple[Setting, ...]:
    s = "settings"
    return (
        Setting(
            "orchestrator_command", ("orchestrator_command", "controller_command"), s, "E5",
            "machine", CLASS_SHOWN,
            "An argument list run on the server's machine by `kyo orchestrator start` and by "
            "the start route; no route writes it, and the row names the spelling the file "
            "uses, since a machine whose `kyo` predates KYO-519 must keep the old one.",
            "the default command, `claude`, runs", alias=True, item="kyo orchestrator",
        ),
        Setting(
            "serve_send", ("serve_send",), s, "E3", "machine", CLASS_SHOWN,
            "The gate in front of every send and terminal route; it is a machine setting so "
            "that nothing arriving from outside the machine can switch it on.",
            "`kyo serve` refuses every send and terminal route", item="kyo serve",
        ),
        Setting(
            "serve_idle_message", ("serve_idle_message",), s, "E3", "machine", CLASS_EDITABLE,
            "A boolean read at each look of the idle timer, so clearing it stops the messages "
            "without a restart; turning it on is refused while `serve_send` is off.",
            "`kyo serve` sends no idle message",
            write=_write("settings/serve_idle_message", {"type": "boolean"}),
        ),
    )


SETTINGS: tuple[Setting, ...] = _project_settings()
MACHINE_SETTINGS: tuple[Setting, ...] = _machine_settings()

_E = "project"
_M = "machine"
DECISIONS: tuple[Decision, ...] = (
    Decision("A1", "git repository", _E, CLASS_SHOWN,
             "`code_repo_id` and the onboard status of the store.", ("store",)),
    Decision("A2", "harness", _E, CLASS_SHOWN,
             "The harness the project is wired for; changing it is a reinstall, not a "
             "setting.", ("harness", "hooks")),
    Decision("A3", "existing tracking", _E, CLASS_OMITTED,
             "A one-time action at setup; nothing is stored."),
    Decision("B1", "store location", _E, CLASS_SHOWN,
             "Layout and store path from `kyo where`; moving a store is documented and "
             "costly.", ("store",)),
    Decision("B2", "store remote", _E, CLASS_REQUEST,
             "The store repository's remotes; adding one is a git operation on the machine.",
             ("store remote",)),
    Decision("C1", "plugin or copies", _E, CLASS_SHOWN,
             "The onboard `harness` item and its fix; changing route runs `kyo harness "
             "install` or `kyo install-hooks` on the machine.", ("harness", "hooks"),
             request_also=True),
    Decision("C2", "skill and procedures", _E, CLASS_SHOWN,
             "Not a decision; the status (current or behind) and its fix.",
             ("skill", "procedures"), request_also=True),
    Decision("C3", "CLAUDE.md section", _E, CLASS_SHOWN,
             "Not a decision; status only, on the copies route.", ("CLAUDE.md section",)),
    Decision("D2", "orchestrator model and effort", _E, CLASS_OMITTED,
             "Kyosei does not hold it; any flags in `orchestrator_command` are shown with "
             "the machine's settings."),
    Decision("D6", "kinds", _E, CLASS_REQUEST,
             "The kind names and each kind's `policy.worker_model`; `kinds.yml` is a "
             "structured file.", file="kyosei/kinds.yml"),
    Decision("D7", "scopes", _E, CLASS_REQUEST,
             "The names in `scopes.yml`.", ("scopes",), file="kyosei/scopes.yml"),
    Decision("D8", "documents", _E, CLASS_REQUEST,
             "The document sets of `documents.yml` and their styles.", ("documents",),
             file="kyosei/documents.yml"),
    Decision("D9", "statuses and gates", _E, CLASS_SHOWN,
             "The ladder is fixed by Kyosei; the gate tags of `gate-tags.yml` are a file.",
             request_also=True, file="kyosei/gate-tags.yml"),
    Decision("D10", "who deploys", _E, CLASS_OMITTED,
             "Stated in the project's CLAUDE.md and procedures, which are prose."),
    Decision("D11", "what must never enter the store", _E, CLASS_SHOWN,
             "The titles of `## Project rules` in `procedures.md`; a rule is added with "
             "`kyo rules add`.", request_also=True, file="kyosei/procedures.md"),
    Decision("E1", "layout", _M, CLASS_SHOWN, "The onboard `machine layout` item.",
             ("machine layout",)),
    Decision("E2", "one kyo per machine", _M, CLASS_SHOWN,
             "The onboard `build` item.", ("build",)),
    Decision("E3", "kyo serve", _M, CLASS_SHOWN,
             "The onboard `kyo serve` item, and the server's own `/v1/version`.",
             ("kyo serve",)),
    Decision("E4", "record links", _E, CLASS_REQUEST,
             "The `E4` entry under `onboarding:` in `config.yml`, when present.",
             file="kyosei/config.yml"),
    Decision("E5", "tmux sessions", _M, CLASS_SHOWN,
             "The onboard `kyo orchestrator` item.", ("kyo orchestrator",)),
)


def classified_config_keys() -> set[str]:
    """Every top-level ``config.yml`` key some row of :data:`SETTINGS` covers."""
    return {top for row in SETTINGS for top in row.top_keys}


def classified_machine_keys() -> set[str]:
    return {key for row in MACHINE_SETTINGS for key in row.keys}


def classified_decisions() -> list[str]:
    return [decision.id for decision in DECISIONS]


#: The setup decision the agent type files belong to (section 3.3).
AGENT_TYPE_DECISION = "C4"


def unclassified() -> dict[str, list[str]]:
    """What a registered key or decision lacks a row for; empty when every one is placed.

    ``tests/test_kyo950_config_view.py`` asserts this is empty, so a key added to
    :data:`kyosei.schema.CONFIG_CONSUMERS` or :data:`kyosei.home_settings.KNOWN_KEYS`
    without a row here fails a test.
    """
    from .home_settings import KNOWN_KEYS
    from .schema import CONFIG_CONSUMERS

    return {
        "config": sorted(set(CONFIG_CONSUMERS) - classified_config_keys()),
        "machine": sorted(set(KNOWN_KEYS) - classified_machine_keys()),
    }


# ----------------------------------------------------------------------------- reading text


def etag_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_value_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _key_line(lines: list[str], key: str, lo: int, hi: int, indent: int | None) -> int | None:
    pattern = re.compile(rf"^( *){re.escape(key)}\s*:(\s|$)")
    for index in range(lo, hi):
        match = pattern.match(lines[index])
        if match and (indent is None or len(match.group(1)) == indent):
            return index
    return None


def _block_end(lines: list[str], start: int, hi: int) -> int:
    """The last line of the value that begins on ``start``, trailing comments excluded."""
    base = _indent(lines[start])
    last = start
    for index in range(start + 1, hi):
        line = lines[index]
        if not line.strip():
            continue
        if _indent(line) > base or (_indent(line) == base and line.lstrip().startswith("- ")):
            if _is_value_line(line):
                last = index
            continue
        if line.lstrip().startswith("#") and _indent(line) > base:
            continue
        break
    return last


def locate(text: str, dotted: str) -> tuple[int, int] | None:
    """The 0-based first and last line of ``dotted`` and its value, or ``None``."""
    lines = text.splitlines()
    lo, hi, indent = 0, len(lines), 0
    found = None
    for depth, part in enumerate(dotted.split(".")):
        index = _key_line(lines, part, lo, hi, indent)
        if index is None:
            return None
        found = index
        end = _block_end(lines, index, hi)
        lo, hi = index + 1, end + 1
        child = next((_indent(lines[i]) for i in range(lo, hi) if _is_value_line(lines[i])), None)
        indent = child if child is not None and child > _indent(lines[index]) else None
        if depth < len(dotted.split(".")) - 1 and child is None:
            return None
    assert found is not None
    return found, _block_end(lines, found, len(lines))


def comment_above(text: str, first: int) -> list[str]:
    """The comment lines directly above line ``first`` (0-based), up to a blank line."""
    lines = text.splitlines()
    above: list[str] = []
    index = first - 1
    while index >= 0 and lines[index].strip().startswith("#"):
        above.append(lines[index].strip())
        index -= 1
    return list(reversed(above))


def trailing_comment(line: str) -> str | None:
    """The comment after a value on the key's own line, if the ``#`` is outside quotes."""
    if ":" not in line:
        return None
    value = line.split(":", 1)[1]
    single = double = 0
    for index, char in enumerate(value):
        if char == "'":
            single += 1
        elif char == '"':
            double += 1
        elif (
            char == "#"
            and single % 2 == 0
            and double % 2 == 0
            and index > 0
            and value[index - 1] == " "
        ):
            return value[index:].strip()
    return None


def _dig(raw: dict, dotted: str):
    node = raw
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _parse(text: str) -> dict:
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _read_file(path: Path) -> tuple[bytes | None, str]:
    try:
        data = path.read_bytes()
    except OSError:
        return None, ""
    return data, data.decode("utf-8", errors="replace")


def _file_report(path: Path, data: bytes | None, problem: str | None) -> dict:
    return {
        "path": str(path),
        "etag": etag_of(data) if data is not None else None,
        "present": data is not None,
        "readable": data is not None and problem is None,
        "problem": problem,
    }


# ----------------------------------------------------------------------------- the rows


def _status_of(items: dict, label: str | None) -> dict | None:
    item = items.get(label) if label else None
    return None if item is None else {
        "status": item.status, "detail": item.detail, "fix": item.fix
    }


def _request_header(row_id: str, where: str, decision: str | None, lines: dict | None) -> str:
    place = where
    if lines is not None:
        place += f" lines {lines['first']}-{lines['last']}"
    suffix = f" (setup decision {decision})" if decision else ""
    return f"{REQUEST_HEADER} {row_id}, {place}{suffix}"


def withhold_command(value):
    """``(value, withheld)``: each ``NAME=value`` argument's value replaced (section 4.3)."""
    if not isinstance(value, list):
        return WITHHELD, [0]
    shown, withheld = [], []
    for index, arg in enumerate(value):
        if isinstance(arg, str) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", arg):
            shown.append(arg.split("=", 1)[0] + "=" + WITHHELD)
            withheld.append(index)
        else:
            shown.append(arg)
    return shown, withheld


def _row_document(
    row: Setting,
    text: str,
    raw: dict,
    where: str,
    items: dict,
    *,
    withhold: bool,
) -> dict:
    present_keys = [key for key in row.keys if _dig(raw, key)[0]]
    if row.alias:
        value = _dig(raw, present_keys[0])[1] if present_keys else None
    elif len(row.keys) == 1:
        value = _dig(raw, row.keys[0])[1] if present_keys else None
    else:
        value = {key: _dig(raw, key)[1] for key in present_keys} if present_keys else None
    shown_key = present_keys[0] if row.alias and present_keys else row.keys[0]
    lines = None
    comment: list[str] = []
    trailing = None
    spans = [locate(text, key) for key in present_keys]
    spans = [span for span in spans if span is not None]
    if spans:
        first = min(span[0] for span in spans)
        last = max(span[1] for span in spans)
        lines = {"first": first + 1, "last": last + 1}
        comment = comment_above(text, first)
        trailing = trailing_comment(text.splitlines()[spans[0][0]])
    document = {
        "key": row.id,
        "file": row.file,
        "decision": row.decision,
        "section": row.section,
        "class": row.klass,
        "why": row.why,
        "present": bool(present_keys),
        "keys_in_file": present_keys,
        "value": value,
        "absent_means": row.absent_means,
        "consumers": _consumers(row),
        "comment": comment,
        "trailing": trailing,
        "lines": lines,
        "status": _status_of(items, row.item),
        "last_change": None,
    }
    if row.alias:
        document["spelling"] = shown_key
    if row.id == "orchestrator_command" and withhold and present_keys:
        document["value"], document["withheld"] = withhold_command(value)
    if row.klass == CLASS_EDITABLE:
        document["write"] = row.write
    if row.klass == CLASS_REQUEST:
        document["request"] = {
            "header": _request_header(row.id, where, row.decision, lines)
        }
    return document


def _consumers(row: Setting) -> list[str]:
    from .schema import CONFIG_CONSUMERS

    names: list[str] = []
    for top in row.top_keys:
        names.extend(CONFIG_CONSUMERS.get(top, ()))
    return names


def _decision_document(decision: Decision, items: dict, where: str) -> dict:
    document = {
        "id": decision.id,
        "title": decision.title,
        "class": decision.klass,
        "why": decision.why,
        "request_also": decision.request_also,
        "status": [
            {"label": label, **status}
            for label in decision.items
            if (status := _status_of(items, label)) is not None
        ],
    }
    if decision.klass == CLASS_REQUEST or decision.request_also:
        place = decision.file or where
        document["request"] = {
            "header": _request_header(f"decision {decision.id}", place, decision.id, None)
        }
    return document


# ----------------------------------------------------------------------------- beside


def _yaml_file(path: Path) -> dict:
    data, text = _read_file(path)
    return _parse(text) if data is not None else {}


def project_rule_titles(text: str) -> list[str]:
    """The ``###`` titles under ``## Project rules`` in ``procedures.md``."""
    titles: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == "Project rules"
        elif inside and line.startswith("### "):
            titles.append(line[4:].strip())
    return titles


def _beside(kyosei_dir: Path) -> dict:
    kinds = _yaml_file(kyosei_dir / "kinds.yml")
    kinds = kinds.get("kinds", kinds) if isinstance(kinds.get("kinds"), dict) else kinds
    listed = []
    for name, body in kinds.items():
        policy = body.get("policy") if isinstance(body, dict) else None
        model = policy.get("worker_model") if isinstance(policy, dict) else None
        shown = model if isinstance(model, str) else None
        listed.append({"kind": str(name), "worker_model": shown})

    def names(file: str, key: str) -> list[str]:
        body = _yaml_file(kyosei_dir / file).get(key)
        return sorted(str(name) for name in body) if isinstance(body, dict) else []

    gate = _yaml_file(kyosei_dir / "gate-tags.yml")
    tags = gate.get("tags", gate)
    _, procedures = _read_file(kyosei_dir / "procedures.md")
    return {
        "kinds": listed,
        "scopes": names("scopes.yml", "scopes"),
        "documents": names("documents.yml", "documents"),
        "gate_tags": sorted(str(tag) for tag in tags) if isinstance(tags, (dict, list)) else [],
        "project_rules": project_rule_titles(procedures),
    }


# ----------------------------------------------------------------------------- agent types


def _agent_type_documents(code_root: Path, items: dict) -> list[dict]:
    from .cmd_install_agents import _MODEL  # noqa: F401 - the pattern the writer applies

    found = []
    status = _status_of(items, "agent types")
    for name in AGENT_TYPES:
        path = code_root / ".claude" / "agents" / f"{name}.md"
        data, text = _read_file(path)
        if data is None:
            continue
        meta: dict = {}
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) == 3:
                meta = _parse(parts[1])
        model, effort = meta.get("model"), meta.get("effort")
        found.append(
            {
                "name": name,
                "decision": AGENT_TYPE_DECISION,
                "file": f".claude/agents/{name}.md",
                "model": str(model) if model is not None else None,
                "effort": str(effort) if effort is not None else None,
                "etag": etag_of(data),
                "class": CLASS_EDITABLE,
                "why": "Two front-matter lines, validated by the model pattern and the "
                "effort levels `kyo install-agents` uses; creating or removing a type is "
                "a request.",
                "status": status,
                "write": _write(
                    f"agent-types/{name}",
                    {
                        "type": "object",
                        "model": {"type": "string", "pattern": _model_pattern()},
                        "effort": {"type": "string", "enum": _effort_levels()},
                    },
                ),
            }
        )
    return found


def _effort_levels() -> list[str]:
    from .cmd_install_agents import EFFORT_LEVELS

    return list(EFFORT_LEVELS)


# ----------------------------------------------------------------------------- documents


def _items_by_label(items) -> dict:
    return {item.label: item for item in items}


def build_project_document(
    layout, environ: dict[str, str], *, withhold: bool = False, project: str | None = None
) -> dict:
    """The project document of section 4.4 for one resolved ``layout``. Reads only."""
    from .cmd_onboard import project_items
    from .config import config_path_for, load_config

    if not layout.configured:
        raise KyoseiError(
            f"no Kyosei project is configured at {layout.code_root}, so there is no "
            "configuration to read"
        )
    path = config_path_for(layout.store_root)
    data, text = _read_file(path)
    problem = None
    try:
        load_config(layout.store_root, code_root=layout.code_root)
    except KyoseiError as exc:
        problem = str(exc)
    raw = _parse(text)
    items = _items_by_label(project_items(layout))
    where = _display_path(layout, path)
    agents_dir = layout.code_root / ".claude" / "agents"
    return {
        "ok": True,
        "command": "config",
        "project": project or layout.code_root.name,
        "layout": layout.kind,
        "files": {
            "config": _file_report(path, data, problem),
            "agent_types": {"path": ".claude/agents", "present": agents_dir.is_dir()},
        },
        "settings": [
            _row_document(row, text, raw, where, items, withhold=withhold) for row in SETTINGS
        ],
        "agent_types": _agent_type_documents(layout.code_root, items),
        "beside": _beside(layout.store_root / "kyosei"),
        "decisions": [
            _decision_document(decision, items, where)
            for decision in DECISIONS
            if decision.document == _E
        ],
    }


def _display_path(layout, path: Path) -> str:
    try:
        return str(path.relative_to(layout.store_root))
    except ValueError:
        return str(path)


def build_machine_document(environ: dict[str, str], *, withhold: bool = False) -> dict:
    """The machine document: the three settings of ``settings.yml`` and the machine's items."""
    from . import home_settings
    from .cmd_onboard import machine_items

    path = home_settings.settings_path(environ)
    data, text = _read_file(path)
    problem = None
    effective = False
    try:
        loaded = home_settings.load(environ)
        effective = bool(loaded.serve_send and loaded.serve_idle_message)
    except KyoseiError as exc:
        problem = str(exc)
    raw = _parse(text)
    items = _items_by_label(machine_items(environ))
    rows = [
        _row_document(row, text, raw, "settings.yml", items, withhold=withhold)
        for row in MACHINE_SETTINGS
    ]
    for row in rows:
        if row["key"] == "serve_idle_message":
            row["effective"] = effective
    return {
        "ok": True,
        "command": "config",
        "machine": True,
        "files": {"settings": _file_report(path, data, problem)},
        "settings": rows,
        "items": [item.to_json() for item in items.values()],
        "decisions": [
            _decision_document(decision, items, "settings.yml")
            for decision in DECISIONS
            if decision.document == _M
        ],
    }


# ----------------------------------------------------------------------------- the terminal


def _brief(value, width: int = 100) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= width else text[: width - 3] + "..."


def render_text(document: dict) -> list[str]:
    """The lines ``kyo config`` prints without ``--json``."""
    machine = bool(document.get("machine"))
    lines = [
        "kyo config" + (" --machine" if machine else "") + ": read only, nothing was written.",
    ]
    for name, report in document["files"].items():
        if "readable" not in report:
            continue
        state = "readable" if report["readable"] else "NOT READABLE"
        lines.append(f"{name}: {report['path']} ({state})")
        if report.get("problem"):
            lines.append(f"  refused: {report['problem']}")
    section = None
    for row in document["settings"]:
        if row["section"] != section:
            section = row["section"]
            lines += ["", section]
        value = _brief(row["value"]) if row["present"] else f"(absent: {row['absent_means']})"
        lines.append(f"  {row['key']} [{row['class']}]: {value}")
        lines.append(f"      {row['why']}")
        if row.get("status"):
            lines.append(f"      status: {row['status']['status']} -- {row['status']['detail']}")
    if document.get("agent_types"):
        lines += ["", "agent types"]
        for agent in document["agent_types"]:
            lines.append(
                f"  {agent['name']} [{agent['class']}]: model {agent['model']}, "
                f"effort {agent['effort']}"
            )
    if document.get("beside"):
        beside = document["beside"]
        lines += ["", "beside config.yml"]
        lines.append("  kinds: " + ", ".join(k["kind"] for k in beside["kinds"]))
        for key in ("scopes", "documents", "gate_tags", "project_rules"):
            lines.append(f"  {key}: " + ", ".join(beside[key]))
    lines += ["", "setup decisions that hold no key of their own"]
    for decision in document["decisions"]:
        lines.append(f"  {decision['id']} {decision['title']} [{decision['class']}]: "
                     f"{decision['why']}")
    return lines

