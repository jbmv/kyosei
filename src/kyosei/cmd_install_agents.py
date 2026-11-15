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

``--in-place`` (KYO-955, proposal KYO-950 section 5.3) is the one edit short of ``--force``: it
changes the ``model:`` and ``effort:`` lines of an existing file's front matter, through
:func:`edit_front_matter`, and nothing else, so that a file the project has since edited keeps
its description and its body. It creates no file, and refuses one whose front matter is not
the template's. Each write and each refusal is a row of the configuration log
(:mod:`kyosei.config_splice`), and an expected etag may be given.
"""

from __future__ import annotations

import re
from argparse import Namespace
from pathlib import Path

import yaml

from . import config_splice
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


_FRONT_MATTER_FENCE = "---"


def _lines_of(text: str) -> list[str]:
    """``text`` split after each ``\\n`` only, so a line keeps its own ending (``\\r\\n``
    stays ``\\r\\n``) and no other character splits it."""
    return re.findall(r"[^\n]*\n|[^\n]+", text)


def _front_matter_span(lines: list[str], path: Path) -> tuple[int, int]:
    """The indexes of the two fence lines around the front matter of ``lines``."""
    if not lines or lines[0].rstrip("\r\n") != _FRONT_MATTER_FENCE:
        raise KyoseiError(
            f"{path} has no front matter (its first line is not `---`), so there is no "
            f"model or effort line to change; restore it with `kyo install-agents --type ... "
            "--force`"
        )
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == _FRONT_MATTER_FENCE:
            return 0, index
    raise KyoseiError(
        f"{path} opens its front matter with `---` and never closes it; hand edit {path}, or "
        "restore it with `kyo install-agents --type ... --force`"
    )


def _front_line(lines: list[str], key: str, end: int, path: Path) -> int:
    pattern = re.compile(rf"^{key}[ \t]*:(?:[ \t]|\r?\n?$)")
    hits = [i for i in range(1, end) if pattern.match(lines[i])]
    if len(hits) != 1:
        raise KyoseiError(
            f"{path} has {'no' if not hits else 'more than one'} `{key}:` line in its front "
            f"matter, so it cannot be changed in place; hand edit {path}, or restore it with "
            "`kyo install-agents --type ... --force`"
        )
    return hits[0]


def _front_value(line: str, key: str) -> str:
    body = line.rstrip("\r\n")
    value = body.split(":", 1)[1]
    value = re.sub(r"(^|[ \t])#.*$", "", value).strip()
    return value.strip("\"'")


def edit_front_matter(
    text: str, template: str, model: str, effort: str, path: Path
) -> tuple[str, dict[str, str | None], dict[str, str]]:
    """``text`` with the ``model:`` and ``effort:`` lines of its front matter set, the
    values before them, and the values after.

    Only those two lines change, and only inside the block between the first two ``---``
    lines; a trailing comment on either line and every line ending stay. Refuses a file with
    no front matter, one whose ``name:`` is not the template's, a model outside the model
    pattern and an effort outside :data:`EFFORT_LEVELS`.
    """
    if template not in TEMPLATES:
        raise KyoseiError(f"{template!r} is not one of the templates {', '.join(TEMPLATES)}")
    if not _MODEL.fullmatch(model):
        raise KyoseiError(
            f"{model!r} is not a model id or alias (letters, digits and . _ - : / [ ] only, "
            "for example `opus` or `claude-sonnet-4-5`)"
        )
    if effort not in EFFORT_LEVELS:
        raise KyoseiError(
            f"{effort!r} is not an effort level; use one of {', '.join(EFFORT_LEVELS)}"
        )
    lines = _lines_of(text)
    _, end = _front_matter_span(lines, path)
    name_at = _front_line(lines, "name", end, path)
    wanted = agent_name(template)
    if _front_value(lines[name_at], "name") != wanted:
        raise KyoseiError(
            f"{path} names itself {_front_value(lines[name_at], 'name')!r}, not {wanted!r}, "
            f"so it is not the {template} type; nothing was changed"
        )
    previous: dict[str, str | None] = {}
    for key, value in (("model", model), ("effort", effort)):
        at = _front_line(lines, key, end, path)
        line = lines[at]
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        previous[key] = _front_value(line, key)
        head, _, rest = body.partition(":")
        gap = rest[: len(rest) - len(rest.lstrip(" \t"))] or " "
        comment = re.search(r"[ \t]+#.*$", rest)
        lines[at] = f"{head}:{gap}{value}{comment.group(0) if comment else ''}{ending}"
    candidate = "".join(lines)
    meta = yaml.safe_load("".join(_lines_of(candidate)[1:end]))
    if (
        not isinstance(meta, dict)
        or str(meta.get("name")) != wanted
        or str(meta.get("model")) != model
        or str(meta.get("effort")) != effort
    ):
        raise KyoseiError(
            f"{path} could not be changed to model {model}, effort {effort} without disturbing "
            f"another line; hand edit the two lines in {path}"
        )
    return candidate, previous, {"model": model, "effort": effort}


def _cmd_in_place(ctx: Context, args: Namespace, chosen: dict[str, tuple[str, str]]) -> Output:
    """``--in-place``: edit the two lines of each existing file; nothing is written when any
    file is refused, and each attempt is a row of the configuration log."""
    if args.force:
        raise KyoseiError(
            "--in-place changes two lines of an existing file and --force replaces the whole "
            "file; give one of them"
        )
    etag = getattr(args, "if_match", None)
    if etag is not None and len(chosen) != 1:
        raise KyoseiError("--if-match names one file's digest; give it with one --type")
    directory = ctx.code_root / AGENTS_DIR
    project = ctx.code_root.name
    plan: list[tuple[str, Path]] = []
    for template in chosen:
        plan.append((template, directory / f"{agent_name(template)}.md"))

    def make_edit(template: str, path: Path):
        model, effort = chosen[template]
        return lambda text: edit_front_matter(text, template, model, effort, path)

    # Every file is checked before any is written, so that a refusal leaves none changed.
    checked: dict[str, tuple[str, dict, dict]] = {}
    for template, path in plan:
        if not path.is_file():
            reason = (
                f"{path} does not exist, and --in-place changes an existing type; creating "
                "one is `kyo install-agents --type ...` without it"
            )
            config_splice.log_refusal(
                ctx.environ, project=project, file=path, key=agent_name(template),
                reason=reason, actor=ctx.actor,
            )
            raise KyoseiError(reason)
        try:
            data = path.read_bytes()
            if etag is not None and config_splice.etag_of(data) != etag:
                raise KyoseiError(
                    f"{path} changed since it was read (its digest is "
                    f"{config_splice.etag_of(data)}, not {etag}); read it again and repeat "
                    "the change"
                )
            checked[template] = make_edit(template, path)(data.decode("utf-8"))
        except (KyoseiError, UnicodeDecodeError) as exc:
            reason = str(exc) if isinstance(exc, KyoseiError) else f"{path} is not UTF-8 text"
            config_splice.log_refusal(
                ctx.environ, project=project, file=path, key=agent_name(template),
                reason=reason, actor=ctx.actor,
            )
            raise KyoseiError(reason) from exc

    lines: list[str] = []
    files = []
    for template, path in plan:
        rel = path.relative_to(ctx.code_root)
        _, previous, new = checked[template]
        shown_now = f"model {new['model']}, effort {new['effort']}"
        if args.print_only:
            changed = previous != new
            lines.append(
                f"would change {rel}: {shown_now} (was model {previous['model']}, effort "
                f"{previous['effort']})" if changed else f"{rel} is already {shown_now}"
            )
            files.append({"path": str(path), "state": "replace" if changed else "current"})
            continue
        applied = config_splice.apply_edit(
            path=path,
            key=agent_name(template),
            edit=make_edit(template, path),
            environ=ctx.environ,
            project=project,
            actor=ctx.actor,
            expected_etag=etag,
        )
        lines.append(
            f"changed {rel}: {shown_now} (was model {previous['model']}, effort "
            f"{previous['effort']})" if applied.written else f"{rel} is already {shown_now}"
        )
        files.append(
            {"path": str(path), "state": "replace" if applied.written else "current",
             "etag": applied.etag}
        )
    return Output(
        lines,
        {
            "ok": True,
            "command": "install-agents",
            "in_place": True,
            "files": files,
            "changed": not args.print_only and any(f["state"] != "current" for f in files),
            "printed_only": bool(args.print_only),
        },
    )


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

    if getattr(args, "in_place", False):
        return _cmd_in_place(ctx, args, chosen)
    if getattr(args, "if_match", None) is not None:
        raise KyoseiError("--if-match is an option of --in-place")

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
