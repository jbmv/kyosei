"""The configuration routes: the reads of KYO-952 and the writes of KYO-956 (KYO-950).

The documents :mod:`kyosei.config_view` builds for ``kyo config``, with three additions that
only a served document has: the presenting device's own name and permissions, the server's
build and whether it is stale, and the withholding of section 4.3 (the value of each
``NAME=value`` argument of ``orchestrator_command``). Both routes need a device token of any
permission, checked first by :func:`kyosei.serve_send.require_reader` (in the handler's
``_config``), so a request without one learns nothing from the refusal. Both read files and
write nothing; no route changes a setting in this build.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from . import config_splice, config_view, device_tokens
from .errors import KyoseiError
from .serve import Refusal

#: The ``Cache-Control`` of both responses: the documents carry the device's permissions.
NO_STORE = "private, no-store"


def _device(device) -> dict:
    """The presenting device, with whether it holds ``config`` (KYO-956): the view draws a
    control only for a device that can use it, and states why there is none for one that
    cannot."""
    return {
        "name": device.name,
        "permissions": list(device.permissions),
        "holds_config": device_tokens.PERMISSION_CONFIG in device.permissions,
    }


def _server(version: dict) -> dict:
    document = {"kyosei": version.get("kyosei"), "stale": bool(version.get("stale"))}
    if version.get("stale_detail"):
        document["stale_detail"] = version["stale_detail"]
    return document


def _answer(document: dict, etag: str | None) -> tuple[dict, dict]:
    headers = {"Cache-Control": NO_STORE}
    if etag:
        headers["ETag"] = etag
    return document, headers


def project_document(
    environ: dict[str, str], device, name: str, root: str, version: dict
) -> tuple[dict, dict]:
    """The project's document and the response headers, for a device that has presented a token."""
    from .config import resolve_layout

    layout = resolve_layout(explicit_root=root, required=False)
    try:
        document = config_view.build_project_document(layout, environ, withhold=True, project=name)
    except KyoseiError as exc:
        raise Refusal(409, str(exc), reason="not-a-project") from exc
    document["server"] = _server(version)
    document["device"] = _device(device)
    return _answer(document, document["files"]["config"]["etag"])


def settings_document(environ: dict[str, str], device, version: dict) -> tuple[dict, dict]:
    document = config_view.build_machine_document(environ, withhold=True)
    document["server"] = _server(version)
    document["device"] = _device(device)
    return _answer(document, document["files"]["settings"]["etag"])


def change_stamp(code_root: Path, kyosei_dir: Path, environ: dict[str, str]) -> str:
    """The stat stamp of every file whose change is a ``config`` event (section 4.5)."""
    from . import home_settings
    from .serve_events import file_stamp

    files = [kyosei_dir / "config.yml"]
    files += [kyosei_dir / name for name in config_view.BESIDE_FILES]
    files += [code_root / ".claude" / "agents" / f"{n}.md" for n in config_view.AGENT_TYPES]
    files.append(home_settings.settings_path(environ))
    return "|".join(file_stamp(path) for path in files)


# ------------------------------------------------------------------------ the write routes

#: The keys ``POST /v1/projects/{project}/config/{key}`` writes. A test fails if one of
#: ``serve_send``, ``orchestrator_command`` or ``controller_command`` is added (section 5.4),
#: and if a route here is not the one a document's ``write.route`` names.
PROJECT_KEY_ROUTES = (
    "worker_model",
    "worker_command_timeout_ceiling_seconds",
    "recx.prior_art.state",
    "docs_coverage.level",
    "read_transcripts",
)

#: The path segment of the agent type routes, and the keys of ``POST /v1/settings/{key}``.
AGENT_TYPES_ROUTE = "agent-types"
MACHINE_KEY_ROUTES = ("serve_idle_message",)

#: The machine keys no route writes, each with what is said of it.
NEVER_ROUTED = ("serve_send", "orchestrator_command", "controller_command")


def _refuse(
    environ: dict[str, str],
    device,
    *,
    project: str,
    file: Path,
    key: str,
    status: int,
    message: str,
    reason: str,
    **extra,
) -> Refusal:
    """A refusal that is also a row of the log naming ``device``."""
    config_splice.log_refusal(
        environ,
        project=project,
        file=file,
        key=key,
        reason=message,
        device=device.name,
        device_id=device.id,
    )
    return Refusal(status, message, reason=reason, **extra)


def _writer_refusal(exc: KyoseiError, environ, device, *, project, file, key) -> Refusal:
    """The refusal a writer raised, with its own sentence, and a row unless it left one."""
    if not config_splice.was_logged(exc):
        config_splice.log_refusal(
            environ,
            project=project,
            file=file,
            key=key,
            reason=str(exc),
            device=device.name,
            device_id=device.id,
        )
    return Refusal(422, str(exc), reason="refused")


def _device_for_config(environ: dict[str, str], authorization: str | None):
    """The presenting device when it holds ``config``. A device without it is refused with
    the row of the attempt; a request with no valid token learns what a token needs."""
    from . import serve_send

    try:
        return serve_send.require_device(
            environ, authorization, device_tokens.PERMISSION_CONFIG
        ), None
    except Refusal as refusal:
        if refusal.extra.get("reason") != f"no-{device_tokens.PERMISSION_CONFIG}-permission":
            raise
        return serve_send.require_reader(environ, authorization), refusal


def _stale_refusal(version: dict) -> str:
    return (
        "this server is stale: "
        + str(version.get("stale_detail") or "a newer build is installed than the one it runs")
        + ". A stale server validates a change with an older build's parser than the hooks "
        "run, so no setting is written until it is restarted with `kyo serve --restart`; "
        "nothing was changed"
    )


def _unresolved_file(environ: dict[str, str], route: str, key: str) -> Path:
    """The file a route would write, named without resolving the project."""
    if route == "settings":
        from . import home_settings

        return home_settings.settings_path(environ)
    if route == "config":
        return Path("config.yml")
    return Path(".claude") / "agents" / f"{key}.md"


def write(
    server,
    authorization: str | None,
    if_match: str | None,
    read_body: Callable[[], bytes],
    version_of: Callable[[], dict],
    *,
    project: str | None,
    route: str,
    key: str,
    root_of: Callable[[], str] | None = None,
) -> dict:
    """One write route: ``route`` is ``config``, ``agent-types`` or ``settings``."""
    from . import serve_send

    environ = server.environ
    device, no_permission = _device_for_config(environ, authorization)
    name = project or "machine"
    if no_permission is not None:
        # KYO-958 F5: refused before the log is opened or the project is looked up, so that
        # neither can answer a token that may not write at all. The row is still written
        # where the log can be; the file is named as the route would, not looked up.
        config_splice.log_refusal(
            environ,
            project=name,
            file=_unresolved_file(environ, route, key),
            key=key,
            reason=str(no_permission),
            device=device.name,
            device_id=device.id,
        )
        raise Refusal(403, str(no_permission), reason=no_permission.extra["reason"])
    try:
        os.close(config_splice.open_log(environ))
    except KyoseiError as exc:
        raise Refusal(500, str(exc), reason="log-unwritable") from exc

    layout = None
    if route == "settings":
        from . import home_settings

        file = home_settings.settings_path(environ)
    else:
        from .config import config_path_for, resolve_layout

        assert root_of is not None
        layout = resolve_layout(explicit_root=root_of(), required=False)
        name = layout.code_root.name
        if route == "config":
            file = config_path_for(layout.store_root) if layout.configured else Path("config.yml")
        else:
            file = layout.code_root / ".claude" / "agents" / f"{key}.md"

    def refuse(status: int, message: str, reason: str, **extra) -> Refusal:
        return _refuse(
            environ,
            device,
            project=name,
            file=file,
            key=key,
            status=status,
            message=message,
            reason=reason,
            **extra,
        )

    version = version_of()
    if version.get("stale"):
        raise refuse(409, _stale_refusal(version), "server-stale")
    if layout is not None and not layout.configured:
        raise refuse(
            409,
            f"no Kyosei project is configured at {layout.code_root}, so there is no "
            "configuration to change",
            "not-a-project",
        )

    try:
        document = serve_send.json_object(read_body())
    except Refusal as refusal:
        reason = refusal.extra.get("reason", "bad-request")
        raise refuse(refusal.status, str(refusal), reason) from refusal
    try:
        if route == "config":
            return _write_project_key(
                environ, device, layout, file, key, document, if_match, refuse, name
            )
        if route == AGENT_TYPES_ROUTE:
            return _write_agent_type(
                environ, device, layout, file, key, document, if_match, refuse, name
            )
        return _write_machine_key(environ, device, file, key, document, if_match, refuse)
    except KyoseiError as exc:
        raise _writer_refusal(exc, environ, device, project=name, file=file, key=key) from exc
    except Refusal:
        raise
    except Exception as exc:
        # KYO-957 F1, the backstop: an error no writer converted would otherwise close the
        # connection with no answer and leave no row of the attempt.
        raise refuse(
            500,
            f"the change to {key} failed inside the server ({type(exc).__name__}: {exc}); "
            f"read {file} at the machine before repeating it",
            "failed",
        ) from exc


def _value_of(document: dict, refuse) -> object:
    if set(document) != {"value"}:
        raise refuse(
            400,
            'the body of this route is {"value": ...}, and nothing else',
            "bad-request",
        )
    return document["value"]


def _answer_write(key: str, previous: object, value: object, written: bool, etag: str) -> dict:
    return {
        "ok": True,
        "key": key,
        "previous": previous,
        "value": value,
        "written": written,
        "etag": etag,
    }


def _current_of_key(path: Path, key: str) -> Callable[[bytes], object]:
    def read(data: bytes) -> object:
        return config_splice.read_value(data.decode("utf-8"), key, path)[1]

    return read


def _write_project_key(environ, device, layout, file, key, document, if_match, refuse, project):
    from . import cmd_config, serve_files

    if key not in PROJECT_KEY_ROUTES:
        raise refuse(
            404,
            f"{key!r} has no route that writes it: the settings a device may change are "
            f"{', '.join(PROJECT_KEY_ROUTES)}, and `GET .../config` names the class of every "
            "other",
            "no-route",
        )
    value = _value_of(document, refuse)
    if key == "read_transcripts":
        if value is True:
            raise refuse(
                403,
                "read_transcripts is turned on by a request to the session, not by a device: "
                "turning it on opens every session read route to the project's tokens, and a "
                "token that could do that could open the routes to itself. Use the request "
                "control of the Config view, or edit config.yml at the machine; turning it "
                "off is the one change a device makes",
                "by-request",
            )
        if value is not False:
            raise refuse(400, 'read_transcripts takes {"value": false}', "bad-request")

    def run(etag: str):
        if key == "recx.prior_art.state":
            return _write_prior_art(environ, device, layout, file, value, etag, refuse)
        if key == "docs_coverage.level":
            return _write_level(environ, device, layout, file, value, etag, refuse)
        action = "unset" if value is None else "set"
        applied = cmd_config.write_project_setting(
            layout,
            environ,
            action,
            key,
            value=value,
            device=device.name,
            device_id=device.id,
            expected_etag=etag,
        )
        return applied.previous, applied.value, applied.written, applied.etag

    previous, new, written, etag = serve_files.replace_config_text(
        file,
        f"{file.name}",
        run,
        if_match=if_match,
        current_value=_current_of_key(file, key),
        refused=lambda why: config_splice.log_refusal(
            environ,
            project=project,
            file=file,
            key=key,
            reason=why,
            device=device.name,
            device_id=device.id,
        ),
    )
    return _answer_write(key, previous, new, written, etag)


def _write_prior_art(environ, device, layout, file, value, etag, refuse):
    from . import prior_art

    if value not in (prior_art.STATE_ON, prior_art.STATE_OFF):
        raise refuse(
            400, 'recx.prior_art.state takes {"value": "on"} or {"value": "off"}', "bad-request"
        )
    before, after, written = prior_art.set_prior_art_state(
        layout.store_root,
        value,
        environ=environ,
        project=layout.code_root.name,
        main_checkout=layout.store_via_main_checkout,
        code_root=layout.code_root,
        device=device.name,
        device_id=device.id,
        expected_etag=etag,
    )
    previous = None if before.state == prior_art.STATE_ABSENT else before.state
    return previous, after.state, written, config_splice.etag_of(file.read_bytes())


def _write_level(environ, device, layout, file, value, etag, refuse):
    from . import docs_coverage
    from .config import read_raw_config

    if value not in docs_coverage.LEVELS:
        raise refuse(
            400,
            f"docs_coverage.level takes one of {', '.join(docs_coverage.LEVELS)}",
            "bad-request",
        )
    block = read_raw_config(layout.store_root).get(docs_coverage.DOCS_COVERAGE_KEY)
    previous = block.get("level") if isinstance(block, dict) else None
    docs_coverage.write_level(
        layout.store_root,
        value,
        environ=environ,
        project=layout.code_root.name,
        main_checkout=layout.store_via_main_checkout,
        code_root=layout.code_root,
        device=device.name,
        device_id=device.id,
        expected_etag=etag,
    )
    new_etag = config_splice.etag_of(file.read_bytes())
    return previous, value, new_etag != etag, new_etag


def _write_agent_type(environ, device, layout, file, key, document, if_match, refuse, project):
    from . import cmd_install_agents, serve_files

    if key not in config_view.AGENT_TYPES:
        raise refuse(
            404,
            f"{key!r} is not an agent type this route changes: the types are "
            f"{', '.join(config_view.AGENT_TYPES)}, and creating or removing one is a request "
            "to the session",
            "no-route",
        )
    unknown = sorted(set(document) - {"model", "effort"})
    if unknown or not all(isinstance(document.get(k), str) for k in ("model", "effort")):
        raise refuse(
            400,
            'the body of this route is {"model": "<id>", "effort": "<level>"}, both strings',
            "bad-request",
        )
    template = key.removeprefix("kyosei-")

    def run(etag: str):
        applied = cmd_install_agents.apply_in_place(
            layout.code_root,
            environ,
            template,
            document["model"],
            document["effort"],
            device=device.name,
            device_id=device.id,
            expected_etag=etag,
        )
        return applied.previous, applied.value, applied.written, applied.etag

    def current(data: bytes) -> object:
        import yaml

        parts = data.decode("utf-8").split("---", 2)
        meta = yaml.safe_load(parts[1]) if len(parts) == 3 else {}
        meta = meta if isinstance(meta, dict) else {}
        return {"model": meta.get("model"), "effort": meta.get("effort")}

    previous, new, written, etag = serve_files.replace_config_text(
        file,
        f".claude/agents/{key}.md",
        run,
        if_match=if_match,
        current_value=current,
        refused=lambda why: config_splice.log_refusal(
            environ,
            project=project,
            file=file,
            key=key,
            reason=why,
            device=device.name,
            device_id=device.id,
        ),
    )
    return _answer_write(key, previous, new, written, etag)


def _write_machine_key(environ, device, file, key, document, if_match, refuse):
    from . import cmd_config, home_settings, serve_files

    if key not in MACHINE_KEY_ROUTES:
        raise refuse(
            404,
            f"{key!r} has no route that writes it: only {', '.join(MACHINE_KEY_ROUTES)} is "
            "changed from a device. serve_send and orchestrator_command are set at the "
            "machine, by its owner, because a token that could set either would open the "
            "gate the other permissions stand behind",
            "no-route",
        )
    value = _value_of(document, refuse)
    if not isinstance(value, bool):
        raise refuse(400, f'{key} takes {{"value": true}} or {{"value": false}}', "bad-request")
    if value:
        try:
            settings = home_settings.load(environ)
        except KyoseiError as exc:
            raise refuse(500, str(exc), "settings-unreadable") from exc
        if not settings.serve_send:
            raise refuse(
                403,
                f"{key} is not turned on while serve_send is not true: the messages could "
                "not be sent, and the setting would begin to act the moment the machine's "
                f"owner turns sending on for another reason. Set serve_send: true in {file} "
                f"at the machine first; turning {key} off is always accepted",
                "send-disabled",
            )

    def run(etag: str):
        applied = cmd_config.write_machine_setting(
            environ,
            key,
            "true" if value else "false",
            device=device.name,
            device_id=device.id,
            expected_etag=etag,
        )
        return applied.previous, applied.value, applied.written, applied.etag

    previous, new, written, etag = serve_files.replace_config_text(
        file,
        file.name,
        run,
        if_match=if_match,
        current_value=_current_of_key(file, key),
        refused=lambda why: config_splice.log_refusal(
            environ,
            project="machine",
            file=file,
            key=key,
            reason=why,
            device=device.name,
            device_id=device.id,
        ),
    )
    return _answer_write(key, previous, new, written, etag)
