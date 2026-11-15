"""``GET /v1/projects/{project}/config`` and ``GET /v1/settings`` (KYO-952, KYO-950 section 4).

The documents :mod:`kyosei.config_view` builds for ``kyo config``, with three additions that
only a served document has: the presenting device's own name and permissions, the server's
build and whether it is stale, and the withholding of section 4.3 (the value of each
``NAME=value`` argument of ``orchestrator_command``). Both routes need a device token of any
permission, checked first by :func:`kyosei.serve_send.require_reader` (in the handler's
``_config``), so a request without one learns nothing from the refusal. Both read files and
write nothing; no route changes a setting in this build.
"""

from __future__ import annotations

from pathlib import Path

from . import config_view
from .errors import KyoseiError
from .serve import Refusal

#: The ``Cache-Control`` of both responses: the documents carry the device's permissions.
NO_STORE = "private, no-store"


def _device(device) -> dict:
    return {"name": device.name, "permissions": list(device.permissions)}


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
        document = config_view.build_project_document(
            layout, environ, withhold=True, project=name
        )
    except KyoseiError as exc:
        raise Refusal(409, str(exc), reason="not-a-project") from exc
    document["server"] = _server(version)
    document["device"] = _device(device)
    return _answer(document, document["files"]["config"]["etag"])


def settings_document(
    environ: dict[str, str], device, version: dict
) -> tuple[dict, dict]:
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
