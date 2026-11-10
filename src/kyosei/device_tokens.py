"""Per-device tokens, and ``kyo devices`` that manages them (KYO-699).

A route that can change something on this machine, the session send routes first, the note
routes of chunk E5 next and the orchestrator start route after them (KYO-719), is reached
with a token issued to one device. Nothing here
knows what any permission allows: a token carries a list of permission names, and a route
asks for the one it needs. Adding a permission means adding its name to :data:`PERMISSIONS`.

WHERE. ``$KYOSEI_HOME/device-tokens.json``, a file of this machine beside ``settings.yml``
and ``projects.json``. A token belongs to the machine that honours it, so it is not in any
project's store, and a file rather than a directory so that it is not taken for a project
under the home (:func:`kyosei.cmd_check_clean._home_dirs`).

WHAT IS KEPT. The SHA-256 digest of each token, its device name, its permissions and its
dates. The token itself is printed once, by ``kyo devices issue``, and cannot be recovered.
A token is 256 random bits, so a plain digest is enough: a slow password hash protects
guessable secrets, and this secret is not guessable. A revoked token's entry stays in the
file, so a device named in the send log can still be looked up, and so the refusal can say
it was revoked.

HOW IT IS PRESENTED. ``Authorization: Bearer <token>``, a header and never the query string,
because a request line is written to the server's log. The file is read afresh on every
request, so a revocation takes effect at once and without restarting the server. A file that
cannot be parsed refuses every token, by name, rather than being read as empty.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path

from .commands import Context, Output
from .config import refuse_real_home_from_a_dispatch_worktree, store_home
from .errors import KyoseiError

#: The file's name under ``$KYOSEI_HOME``.
TOKENS_FILE = "device-tokens.json"

#: The permission that lets a token drive a session.
PERMISSION_SEND = "send"

#: The permission that lets a token file, read and tag notes (KYO-706). A name of its own,
#: so that a device that only writes notes cannot type into a session, and a token issued
#: before the note routes existed does not gain a store write it was never issued for.
PERMISSION_NOTES = "notes"

#: The permission that lets a token start a project's orchestrator session (KYO-719, decision
#: KYO-718). A name of its own, because it starts a process on this machine, which a token
#: issued to type into a session, or to file notes, was not issued for.
PERMISSION_START = "start"

#: Every permission this build can grant.
PERMISSIONS: tuple[str, ...] = (PERMISSION_SEND, PERMISSION_NOTES, PERMISSION_START)

#: Marks a string as a Kyosei token, for a person reading it and for secret scanners.
TOKEN_PREFIX = "kyo_"

_TOKEN_BYTES = 32

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_SHAPE = '{"devices": [{"id": ..., "name": ..., "digest": ..., "permissions": [...], ...}]}'


@dataclass(frozen=True)
class Device:
    """One issued token, as the file holds it. The token itself is held nowhere."""

    id: str
    name: str
    digest: str
    permissions: tuple[str, ...]
    created_at: str
    revoked_at: str | None = None

    def stored(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "digest": self.digest,
            "permissions": list(self.permissions),
            "created_at": self.created_at,
            "revoked_at": self.revoked_at,
        }

    def shown(self) -> dict:
        """What ``kyo devices`` prints: everything but the digest."""
        return {
            "id": self.id,
            "name": self.name,
            "permissions": list(self.permissions),
            "created_at": self.created_at,
            "revoked_at": self.revoked_at,
        }


def tokens_path(environ: dict[str, str] | None = None) -> Path:
    return store_home(environ) / TOKENS_FILE


def token_digest(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _unreadable(path: Path, why: str) -> KyoseiError:
    return KyoseiError(
        f"this machine's device tokens {path} cannot be read: {why}. Every token is refused "
        f"until the file is fixed, rather than reading it as empty; correct it by hand (JSON, "
        f"{_SHAPE}), or move it aside and issue the tokens again with `kyo devices issue`"
    )


def _device(index: int, entry: object, path: Path) -> Device:
    if not isinstance(entry, dict):
        raise _unreadable(path, f"devices[{index}] is not an object")
    texts = {key: entry.get(key) for key in ("id", "name", "digest", "created_at")}
    missing = [key for key, value in texts.items() if not isinstance(value, str) or not value]
    permissions = entry.get("permissions")
    revoked = entry.get("revoked_at")
    if (
        missing
        or not isinstance(permissions, list)
        or not all(isinstance(name, str) for name in permissions)
        or not (revoked is None or isinstance(revoked, str))
    ):
        raise _unreadable(path, f"devices[{index}] is not a complete entry")
    return Device(
        texts["id"],
        texts["name"],
        texts["digest"],
        tuple(permissions),
        texts["created_at"],
        revoked,
    )


def read_devices(path: Path) -> list[Device]:
    """Every device the file records, read now. A missing file is the ordinary empty list."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        raise _unreadable(path, str(exc)) from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _unreadable(path, f"it is not JSON ({exc})") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("devices"), list):
        raise _unreadable(path, 'it is not an object with a "devices" list')
    return [_device(index, entry, path) for index, entry in enumerate(raw["devices"])]


def _write_devices(path: Path, devices: list[Device]) -> None:
    """Replace the file whole. ``mkstemp`` makes the temporary file private to this account."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"devices": [d.stored() for d in devices]}, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def holder_of(token: str, devices: list[Device]) -> Device | None:
    """The device issued ``token``, revoked or not, or None.

    Every entry is compared, in constant time, so the answer does not depend on where in
    the file a digest stands.
    """
    digest = token_digest(token)
    found = None
    for device in devices:
        if hmac.compare_digest(device.digest, digest):
            found = device
    return found


# ------------------------------------------------------------------------------ acts


def issue(
    name: str, permissions: list[str], environ: dict[str, str] | None = None
) -> tuple[Device, str]:
    """A new device and its token, which exists only in this return value afterwards."""
    from . import clock

    if not _NAME.match(name):
        raise KyoseiError(
            f"{name!r} is not a device name: use up to 64 letters, digits, dots, dashes or "
            "underscores, beginning with a letter or digit (`kyo devices issue my-ipad`)"
        )
    path = tokens_path(environ)
    devices = read_devices(path)
    if any(device.name == name and device.revoked_at is None for device in devices):
        raise KyoseiError(
            f"a device named {name!r} already holds a live token in {path}; revoke it first "
            f"(`kyo devices revoke {name}`) or choose another name"
        )
    token = TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    device = Device(
        secrets.token_hex(4),
        name,
        token_digest(token),
        tuple(sorted(set(permissions))),
        clock.now(),
    )
    _write_devices(path, [*devices, device])
    return device, token


def revoke(target: str, environ: dict[str, str] | None = None) -> tuple[Device, bool]:
    """Revoke the device named or numbered ``target``; True when it was live until now."""
    from . import clock

    path = tokens_path(environ)
    devices = read_devices(path)
    matches = [d for d in devices if d.name == target and d.revoked_at is None] or [
        d for d in devices if d.id == target
    ]
    if not matches:
        raise KyoseiError(
            f"no live device is named {target!r}, and no device has that id, in {path}; "
            "`kyo devices list` shows each device's name and id"
        )
    (chosen,) = matches
    if chosen.revoked_at is not None:
        return chosen, False
    done = Device(
        chosen.id,
        chosen.name,
        chosen.digest,
        chosen.permissions,
        chosen.created_at,
        clock.now(),
    )
    _write_devices(path, [done if d.id == chosen.id else d for d in devices])
    return done, True


# ---------------------------------------------------------------------------- command


def _permissions_word(device: Device) -> str:
    return ", ".join(device.permissions) if device.permissions else "none"


def _refuse_from_worktree(environ: dict[str, str], action: str) -> None:
    refuse_real_home_from_a_dispatch_worktree(
        environ,
        command=f"kyo devices {action}",
        what=f"{action} a device token in {TOKENS_FILE}",
    )


def cmd_devices(ctx: Context, args: Namespace) -> Output:
    """``kyo devices list``, ``issue NAME [--permission P]…``, ``revoke NAME|ID``."""
    environ = ctx.environ
    action = args.action
    if action == "list":
        if args.target or args.permission:
            raise KyoseiError("kyo devices list takes no argument; run `kyo devices list`")
        path = tokens_path(environ)
        devices = read_devices(path)
        lines = [f"{path}: {len(devices)} device(s)"]
        for device in devices:
            state = f"revoked {device.revoked_at}" if device.revoked_at else "live"
            lines.append(
                f"  {device.name:<24} {device.id}  permissions: {_permissions_word(device)}  "
                f"issued {device.created_at}  {state}"
            )
        return Output(
            lines,
            {
                "ok": True,
                "command": "devices",
                "action": "list",
                "file": str(path),
                "devices": [device.shown() for device in devices],
            },
        )
    if not args.target:
        example = "my-ipad --permission send" if action == "issue" else "<name>"
        raise KyoseiError(
            f"kyo devices {action} needs a device; name one (`kyo devices {action} {example}`)"
        )
    if action == "issue":
        _refuse_from_worktree(environ, action)
        device, token = issue(args.target, args.permission or [], environ)
        return Output(
            [
                f"issued a token to {device.name} (id {device.id}), permissions: "
                f"{_permissions_word(device)}",
                f"  {token}",
                "This is the only time the token is shown; only its digest is kept. The device "
                "sends it as `Authorization: Bearer <token>`.",
            ],
            {
                "ok": True,
                "command": "devices",
                "action": "issue",
                "device": device.shown(),
                "token": token,
            },
        )
    if args.permission:
        raise KyoseiError(
            "kyo devices revoke takes no --permission; run `kyo devices revoke <name>`"
        )
    _refuse_from_worktree(environ, action)
    device, changed = revoke(args.target, environ)
    verb = "revoked" if changed else "already revoked"
    return Output(
        [f"{verb}: {device.name} (id {device.id}) at {device.revoked_at}"],
        {
            "ok": True,
            "command": "devices",
            "action": "revoke",
            "revoked": changed,
            "device": device.shown(),
        },
    )
