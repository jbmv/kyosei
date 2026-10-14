"""``kyo install-agents --type TEMPLATE=MODEL[:EFFORT] ... [--force] [--print]`` (KYO-607):
write the worker agent types the operator chose in setup decision C4 into the project's
``.claude/agents/``.

An agent type fixes a worker's model and effort, which are the operator's choice, so
Kyosei ships no type of its own (the packaged types were retired; the plugin carries none). What it
ships is the text around those two values: one template per kind of worker, each carrying
the standing instruction that makes it a Kyosei worker (read the brief with
``kyo show <dispatch-id>``, never write under the store, leave the whole suite to the
orchestrator). The templates are this repository's hand-written ``kyosei-worker.md`` and
``kyosei-designer.md`` with the model and effort left open.

A written file is the project's from then on: an existing one is replaced only with
``--force``, and the whole call writes nothing when any one file would be refused, so a
partial set is never left behind. ``kyo onboard --check``'s ``agent types`` item reads
the files back and reports a type whose model disagrees with ``worker_model``.
"""

from __future__ import annotations

import re
from argparse import Namespace
from pathlib import Path

from .commands import Context, Output
from .errors import KyoseiError
from .export import atomic_write_text
from .scaffold import defaults_dir

#: The packaged templates, under ``defaults/agents/``, by the name ``--type`` uses.
TEMPLATES = ("worker", "designer")

#: Effort each template gets when ``--type`` names none: the split this repository's
#: own hand-written types use.
DEFAULT_EFFORT = {"worker": "medium", "designer": "high"}

#: Claude Code's subagent ``effort:`` values.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

#: A model id or alias as it may stand unquoted in YAML front matter: ``opus``,
#: ``claude-sonnet-4-5``, ``sonnet[1m]``, a provider id with ``.``, ``:`` or ``/``.
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/\[\]-]*")

AGENTS_DIR = Path(".claude") / "agents"


def agent_name(template: str) -> str:
    """The file stem and ``name:`` a template is written under."""
    return f"kyosei-{template}"


def template_text(template: str) -> str:
    base = defaults_dir()
    if base is None:
        raise KyoseiError("this kyo build ships no defaults/ directory; reinstall kyo")
    return (base / "agents" / f"{template}.md").read_text(encoding="utf-8")


def render(template: str, model: str, effort: str) -> str:
    text = template_text(template)
    for key, value in (("name", agent_name(template)), ("model", model), ("effort", effort)):
        text = text.replace("{{" + key + "}}", value)
    return text


def parse_type(spec: str) -> tuple[str, str, str]:
    """``TEMPLATE=MODEL[:EFFORT]`` as ``(template, model, effort)``.

    The effort is split off the right-hand end only when it is one of
    :data:`EFFORT_LEVELS`, so a model id that itself contains ``:`` (a provider id such
    as ``...-v1:0``) is read whole.
    """
    template, sep, rest = spec.partition("=")
    template = template.strip()
    if not sep or template not in TEMPLATES or not rest.strip():
        raise KyoseiError(
            f"--type {spec!r}: expected TEMPLATE=MODEL[:EFFORT], where TEMPLATE is one "
            f"of {', '.join(TEMPLATES)} (for example `--type designer=opus:high`)"
        )
    model, effort = rest.strip(), DEFAULT_EFFORT[template]
    head, colon, tail = model.rpartition(":")
    if colon and tail in EFFORT_LEVELS:
        model, effort = head, tail
    if not _MODEL.fullmatch(model):
        raise KyoseiError(
            f"--type {spec!r}: {model!r} is not a model id or alias "
            "(letters, digits and . _ - : / [ ] only, for example `opus` or "
            "`claude-sonnet-4-5`)"
        )
    return template, model, effort


def cmd_install_agents(ctx: Context, args: Namespace) -> Output:
    specs = list(args.types or [])
    if not specs:
        raise KyoseiError(
            "name at least one type with --type TEMPLATE=MODEL[:EFFORT], for example "
            "`kyo install-agents --type worker=sonnet --type designer=opus:high` "
            "(setup decision C4; the default is none, and workers then run as the "
            "harness's general-purpose subagent)"
        )
    chosen: dict[str, tuple[str, str]] = {}
    for spec in specs:
        template, model, effort = parse_type(spec)
        if template in chosen:
            raise KyoseiError(f"--type names {template} twice; name each template once")
        chosen[template] = (model, effort)

    directory = ctx.code_root / AGENTS_DIR
    plan: list[tuple[Path, str, str]] = []  # (path, text, state)
    refused: list[Path] = []
    for template, (model, effort) in chosen.items():
        path = directory / f"{agent_name(template)}.md"
        text = render(template, model, effort)
        if not path.exists():
            state = "new"
        else:
            try:
                current = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                current = None
            if current == text:
                state = "current"
            else:
                state = "replace"
                if not args.force:
                    refused.append(path)
        plan.append((path, text, state))

    if refused and not args.print_only:
        names = ", ".join(str(p.relative_to(ctx.code_root)) for p in refused)
        raise KyoseiError(
            f"refusing to overwrite {names}, which already exists and differs from what "
            "this would write; nothing was written. Rerun with `--force` to replace it, "
            "or with `--print` to see the text first"
        )

    lines: list[str] = []
    files = []
    for path, text, state in plan:
        rel = path.relative_to(ctx.code_root)
        if args.print_only:
            verb = {"new": "would write", "replace": "would replace", "current": "current:"}
            lines.append(
                f"{verb[state]} {rel}"
                + (" (needs --force)" if state == "replace" and not args.force else "")
            )
            lines.extend("    " + line for line in text.splitlines())
        elif state == "current":
            lines.append(f"{rel} is already current (no changes)")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, text)
            lines.append(f"{'replaced' if state == 'replace' else 'wrote'} {rel}")
        files.append({"path": str(path), "state": state})
    if not args.print_only:
        lines.append(
            "`kyo onboard --check` reads these back, and reports a type whose model "
            "agrees with neither worker_model nor any kind's policy.worker_model"
        )
    return Output(
        lines,
        {
            "ok": True,
            "command": "install-agents",
            "files": files,
            "changed": not args.print_only and any(f["state"] != "current" for f in files),
            "printed_only": bool(args.print_only),
        },
    )
