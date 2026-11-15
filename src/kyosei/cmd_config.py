"""``kyo config``: a project's or the machine's configuration, read; and the writers of the
project's editable settings.

KYO-952, the read side of proposal KYO-950. The document is built by
:mod:`kyosei.config_view`, which also serves ``GET /v1/projects/{project}/config`` and
``GET /v1/settings`` (:mod:`kyosei.serve_config`); ``kyo config [--machine] [--json]`` is the
terminal's copy of the same read. It writes nothing and opens no database. Unlike the served
documents it prints ``orchestrator_command`` in full, since the person at the terminal is on
the machine.

KYO-954 adds ``kyo config set KEY VALUE``, ``kyo config unset KEY`` and ``kyo config --log``
(sections 5.1, 5.2, 5.3 and 5.5 of the proposal). ``set`` and ``unset`` write
``worker_model``, ``worker_command_timeout_ceiling_seconds`` and ``read_transcripts``
through :func:`kyosei.config_splice.apply_change`, which keeps the file's comments, writes
atomically and logs the attempt; every other key is refused, naming its class. The two other
editable keys have their own commands, ``kyo prior-art-setting`` and ``kyo docs coverage
--level``, which write through the same splice.

KYO-955 adds ``kyo config --machine set serve_idle_message true|false``, the one editable key of
``$KYOSEI_HOME/settings.yml``. It splices that line, validates the candidate with
:func:`kyosei.home_settings.parse` (the parser the readers use) and logs the attempt; the
machine file is never created, and ``serve_send`` and ``orchestrator_command`` are set at the
machine by hand. Whether ``serve_send`` is on is not checked at the terminal: that refusal
belongs to the served route.
"""

from __future__ import annotations

import re
from argparse import Namespace

from . import config_splice, config_view
from .commands import Context, Output
from .errors import KyoseiError, UsageError

#: The keys ``set`` and ``unset`` write directly.
WRITABLE = ("worker_model", "worker_command_timeout_ceiling_seconds", "read_transcripts")

#: The editable keys that are written by a command of their own.
OTHER_WRITERS = {
    "recx.prior_art.state": "kyo prior-art-setting on|off",
    "docs_coverage.level": "kyo docs coverage --level off|notice|problem",
}


def cmd_config(ctx: Context, args: Namespace) -> Output:
    action = getattr(args, "action", None)
    if getattr(args, "log", False):
        return _cmd_log(ctx)
    if action is not None:
        return _cmd_write(ctx, args, action)
    if args.machine:
        document = config_view.build_machine_document(ctx.environ)
    else:
        assert ctx.layout is not None
        document = config_view.build_project_document(ctx.layout, ctx.environ)
    return Output(config_view.render_text(document), document)


def _cmd_log(ctx: Context) -> Output:
    rows = config_splice.read_log(ctx.environ)
    lines = [f"{config_splice.log_path(ctx.environ)}: {len(rows)} attempt(s)"]
    for row in rows:
        reason = f" -- {row['reason']}" if row.get("reason") else ""
        lines.append(
            f"  {row.get('at')}  {row.get('outcome')}  {row.get('device')}  "
            f"{row.get('project')}  {row.get('key')}: {config_splice.shown(row.get('previous'))}"
            f" -> {config_splice.shown(row.get('new'))}{reason}"
        )
    return Output(lines, {"ok": True, "command": "config", "log": rows})


def _setting(key: str) -> config_view.Setting | None:
    for row in config_view.SETTINGS:
        if key == row.id or key in row.keys:
            return row
    return None


def _class_refusal(key: str, row: config_view.Setting | None) -> KyoseiError:
    if row is None:
        return KyoseiError(
            f"{key!r} is not a setting of kyosei/config.yml that this build knows; `kyo config` "
            "lists them with the class of each"
        )
    if row.klass == config_view.CLASS_REQUEST:
        return KyoseiError(
            f"{row.id} is a request setting (class: {row.klass}): {row.why} It is changed by "
            "asking the session, which edits the file and keeps its comments"
        )
    if row.klass == config_view.CLASS_SHOWN:
        return KyoseiError(
            f"{row.id} is shown only (class: {row.klass}): {row.why} Nothing changes it from "
            "`kyo config`"
        )
    command = OTHER_WRITERS.get(row.keys[0])
    if command is not None:
        return KyoseiError(
            f"{row.id} is editable (class: {row.klass}) and is written by `{command}`, not by "
            "`kyo config set`"
        )
    return KyoseiError(f"{row.id} is not written by `kyo config set` in this build")


def check_model(value: str) -> str:
    """``value`` when it is a model id as ``kyo install-agents`` applies the pattern."""
    from .cmd_install_agents import _MODEL

    if not _MODEL.fullmatch(value):
        raise KyoseiError(
            f"{value!r} is not a model id or alias (letters, digits and . _ - : / [ ] only, "
            "and it starts with a letter or digit, for example `claude-sonnet-5-5` or "
            "`opus`)"
        )
    return value


def _parse_value(key: str, text: str) -> object:
    if key == "worker_model":
        return check_model(text.strip())
    if key == "read_transcripts":
        lowered = text.strip().lower()
        if lowered not in ("true", "false"):
            raise KyoseiError(f"read_transcripts takes `true` or `false`; got {text!r}")
        return lowered == "true"
    if not re.fullmatch(r"[0-9]+", text.strip()):
        raise KyoseiError(f"{key} takes a whole number of seconds, for example `240`; got {text!r}")
    return int(text.strip())


def _cmd_write(ctx: Context, args: Namespace, action: str) -> Output:
    from .config import config_path_for, refuse_store_write, validate_config_text

    if args.machine:
        return _cmd_write_machine(ctx, args, action)
    key = args.key
    if not key:
        raise UsageError(
            f"`kyo config {action}` needs a key: `kyo config {action} KEY"
            f"{' VALUE' if action == 'set' else ''}`"
        )
    if action == "set" and args.value is None:
        raise UsageError(f"`kyo config set {key}` needs a value")
    if action == "unset" and args.value is not None:
        raise UsageError("`kyo config unset` takes the key only")
    layout = ctx.layout
    if layout is None or not layout.configured:
        raise KyoseiError(
            "kyo config set and unset change a project's kyosei/config.yml, and no Kyosei "
            "project is configured here; run it inside one (or with --root)"
        )
    path = config_path_for(layout.store_root)
    project = layout.code_root.name
    try:
        change = _change_for(action, key, args.value)
    except KyoseiError as exc:
        config_splice.log_refusal(
            ctx.environ, project=project, file=path, key=key, reason=str(exc), actor=ctx.actor
        )
        raise

    def precheck(present: bool, previous: object) -> None:
        refuse_store_write(
            layout.store_via_main_checkout,
            ctx.code_root,
            f"{'change' if action == 'set' else 'clear'} {key}",
        )

    def validate(candidate: str) -> None:
        validate_config_text(candidate, path)

    applied = config_splice.apply_change(
        path=path,
        change=change,
        validate=validate,
        environ=ctx.environ,
        project=project,
        actor=ctx.actor,
        precheck=precheck,
        probe_root=layout.store_root,
        expected_etag=getattr(args, "if_match", None),
    )
    shown = config_splice.shown(applied.value)
    if applied.written:
        line = f"{key}: {shown} (was {config_splice.shown(applied.previous)}) in {path}"
    else:
        line = f"{key}: already {shown} in {path}; nothing written"
    return Output(
        [line, f"  logged in {config_splice.log_path(ctx.environ)}"],
        {
            "ok": True,
            "command": "config",
            "action": action,
            "key": key,
            "previous": applied.previous,
            "value": applied.value,
            "written": applied.written,
            "etag": applied.etag,
            "config_path": str(path),
            "wrote_to_store": True,
        },
    )


#: The machine keys no writer changes, and what is said of each.
MACHINE_BY_HAND = ("serve_send", "orchestrator_command", "controller_command")


def _cmd_write_machine(ctx: Context, args: Namespace, action: str) -> Output:
    """``kyo config --machine set serve_idle_message true|false`` (KYO-955)."""
    from . import home_settings

    key = args.key
    if action != "set":
        raise UsageError(
            "`kyo config --machine` writes with `set` only: "
            "`kyo config --machine set serve_idle_message true|false`"
        )
    if not key or args.value is None:
        raise UsageError(
            "`kyo config --machine set` needs a key and a value: "
            "`kyo config --machine set serve_idle_message true|false`"
        )
    path = home_settings.settings_path(ctx.environ)
    project = "machine"

    def refuse(reason: str) -> KyoseiError:
        config_splice.log_refusal(
            ctx.environ, project=project, file=path, key=key, reason=reason, actor=ctx.actor
        )
        return KyoseiError(reason)

    if key in MACHINE_BY_HAND:
        raise refuse(
            f"{key} is set at the machine by hand: edit {path} yourself. `kyo config --machine "
            f"set` writes {home_settings.SERVE_IDLE_MESSAGE_KEY} only"
        )
    if key != home_settings.SERVE_IDLE_MESSAGE_KEY:
        raise refuse(
            f"{key!r} is not a setting of {path} that `kyo config --machine set` writes; it "
            f"writes {home_settings.SERVE_IDLE_MESSAGE_KEY} only"
        )
    lowered = args.value.strip().lower()
    if lowered not in ("true", "false"):
        raise refuse(f"{key} takes `true` or `false`; got {args.value!r}")
    if not path.is_file():
        raise refuse(
            f"{path} does not exist, and `kyo config --machine set` does not create it; create "
            f"the file by hand (an empty file is enough) and set {key} again"
        )
    change = config_splice.Change(key, lowered == "true")

    def validate(candidate: str) -> None:
        home_settings.parse(candidate, path)

    applied = config_splice.apply_change(
        path=path,
        change=change,
        validate=validate,
        environ=ctx.environ,
        project=project,
        actor=ctx.actor,
        expected_etag=getattr(args, "if_match", None),
    )
    shown = config_splice.shown(applied.value)
    if applied.written:
        line = f"{key}: {shown} (was {config_splice.shown(applied.previous)}) in {path}"
    else:
        line = f"{key}: already {shown} in {path}; nothing written"
    return Output(
        [line, f"  logged in {config_splice.log_path(ctx.environ)}"],
        {
            "ok": True,
            "command": "config",
            "machine": True,
            "action": action,
            "key": key,
            "previous": applied.previous,
            "value": applied.value,
            "written": applied.written,
            "etag": applied.etag,
            "settings_path": str(path),
            "wrote_to_store": False,
        },
    )


def _change_for(action: str, key: str, text: str | None) -> config_splice.Change:
    """The change a request names, or the refusal that says why it cannot be made."""
    row = _setting(key)
    if row is None or row.klass != config_view.CLASS_EDITABLE or key not in WRITABLE:
        raise _class_refusal(key, row)
    if action == "unset":
        return config_splice.Change(key, remove=True)
    assert text is not None
    return config_splice.Change(key, _parse_value(key, text))
