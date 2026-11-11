"""Commitments for the turn index: salted per-line roots, chained per session (KYO-663).

WHAT IS COMMITTED. When a message is sent, each of its lines becomes a leaf, the leaves
are folded into a Merkle root, and the root and the line count go on the message's line
of the turn index (:mod:`kyosei.cmd_turns`). No text is stored. A person can later show
any subset of the lines with their salts and proofs, and a reader can check that those
lines belong to the root; the lines not shown stay unknown (decision KYO-654, DC6).

THE PRIMITIVES, all from the standard library.

- ``H`` is SHA-256 (:mod:`hashlib`). Its collision and second-preimage resistance are what
  the root and the chain rest on.
- A line's salt is ``HMAC-SHA256(K, context)`` (:mod:`hmac`). ``K`` is 32 bytes from
  :func:`secrets.token_bytes`, so it is uniform and needs no stretching: HMAC is used as a
  pseudorandom function to derive one salt per line, and a password-hashing function
  (PBKDF2, scrypt) would add cost and no strength, since nothing here is derived from a
  secret a person chose. Without ``K`` a salt cannot be computed, so a root over a
  one-word message cannot be confirmed by guessing the word.
- The salt's context is the JSON array ``[label, session, prompt_id, role, prev, i]``.
  ``prev`` (the hash of the session's preceding index line) is in it so that two messages
  under one prompt id and role (a reply committed at two Stops of one turn) never share
  a salt, which would show a reader holding both roots whether a line was the same in
  both. Every field is on the index line, so ``K`` and the line reproduce every salt.
- A leaf is ``H(0x00 ‖ salt ‖ line)`` and an inner node ``H(0x01 ‖ left ‖ right)``, with
  the tree split as RFC 6962 splits it (the left subtree takes the largest power of two
  below the count). The two prefixes keep a leaf from being read as a node, and that
  shape never duplicates a last leaf, so two different messages cannot share a root.

WHAT A LINE IS. A message is split on ``"\\n"`` only, and each piece is encoded as UTF-8.
``str.splitlines`` is not used: it also splits on form feeds and Unicode separators, and
a reader in another language would not reproduce it.

THE CHAIN. A sealed index line carries ``prev``, the hash of the sealed line before it in
its session (null on the session's first sealed line). A line's hash covers the fields
that never change after it is written: ``seal`` (the format version), ``session``,
``prompt_id``, ``at``, ``role``, ``root``, ``lines`` and ``prev``. It does not cover
``origin``, ``pasted``, ``turn`` or ``source``, which the Stop hook settles later from the
transcript. Editing a committed line, reordering two lines or inserting one changes a hash
that a later ``prev`` or a credit's ``span_seal`` already holds.

WHAT THIS DOES NOT PROVE. The index is a local file, and whoever can write it can rewrite
it from some line onward consistently. Evidence of when a commitment was made needs the
chain's head to be timestamped outside the machine, which is deferred (DC11).

THE KEY. ``K`` is kept in one of three places, and which one is the person's choice,
recorded in ``$KYOSEI_HOME/seal.json``:

- ``file``: ``$KYOSEI_HOME/seal.key``, mode 0600. What is used until another is chosen.
- ``keychain``: a generic password in the macOS login keychain, through ``security``.
- ``icloud-keychain``: a synchronizable generic password, which iCloud Keychain carries to
  the person's other Apple devices. The key then leaves this machine, end-to-end
  encrypted by iCloud Keychain. It is never chosen by default (:data:`ICLOUD_DISCLOSURE`).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from .config import store_home

#: The format version a sealed index line carries in ``seal``.
SEAL_VERSION = 1

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

_SALT_LABEL = "kyosei-seal-salt-v1"
_LINE_LABEL = b"kyosei-seal-line-v1\n"
_LEAF_PREFIX = b"\x00"
_NODE_PREFIX = b"\x01"

#: The fields of an index line that its hash covers, and no others.
HASHED_FIELDS = ("seal", "session", "prompt_id", "at", "role", "root", "lines", "prev")

KEY_BYTES = 32

STORE_FILE = "file"
STORE_KEYCHAIN = "keychain"
STORE_ICLOUD = "icloud-keychain"
KEY_STORES = (STORE_FILE, STORE_KEYCHAIN, STORE_ICLOUD)

KEY_FILE = "seal.key"
CHOICE_FILE = "seal.json"
#: Names a keychain file for the ``keychain`` store instead of the login keychain.
KEYCHAIN_ENV = "KYOSEI_SEAL_KEYCHAIN"
KEYCHAIN_SERVICE = "dev.kyosei.seal-key"
KEYCHAIN_ACCOUNT = "seal-key"

ICLOUD_DISCLOSURE = (
    "With icloud-keychain the seal key is stored as a synchronizable keychain item, and "
    "iCloud Keychain copies it to the other Apple devices signed in to the same account. "
    "The key then leaves this machine, end-to-end encrypted by iCloud Keychain."
)

_SECURITY_TIMEOUT = 10


class SealKeyError(Exception):
    """The seal key could not be read from, or written to, the chosen store."""


# ---------------------------------------------------------------------- commitments


def split_lines(text: str) -> list[str]:
    """The lines a message is committed by: split on ``"\\n"`` and on nothing else."""
    return text.split("\n")


def salt(
    key: bytes, session: str, prompt_id: str | None, role: str, prev: str | None, i: int
) -> bytes:
    """The salt of line ``i`` of the message on the index line these fields name."""
    context = json.dumps(
        [_SALT_LABEL, session, prompt_id, role, prev, i], separators=(",", ":")
    ).encode("utf-8")
    return hmac.new(key, context, hashlib.sha256).digest()


def leaf(line_salt: bytes, line: str) -> bytes:
    return hashlib.sha256(_LEAF_PREFIX + line_salt + line.encode("utf-8")).digest()


def _split(count: int) -> int:
    """The largest power of two strictly below ``count`` (RFC 6962)."""
    k = 1
    while k * 2 < count:
        k *= 2
    return k


def merkle_root(leaves: list[bytes]) -> bytes:
    if not leaves:
        raise ValueError("a message has at least one line")
    if len(leaves) == 1:
        return leaves[0]
    k = _split(len(leaves))
    return hashlib.sha256(
        _NODE_PREFIX + merkle_root(leaves[:k]) + merkle_root(leaves[k:])
    ).digest()


def merkle_proof(leaves: list[bytes], i: int) -> list[tuple[str, bytes]]:
    """The siblings from leaf ``i`` up to the root, each with the side it stands on."""
    if len(leaves) == 1:
        return []
    k = _split(len(leaves))
    if i < k:
        return [*merkle_proof(leaves[:k], i), ("right", merkle_root(leaves[k:]))]
    return [*merkle_proof(leaves[k:], i - k), ("left", merkle_root(leaves[:k]))]


def root_from_proof(a_leaf: bytes, proof: list[tuple[str, bytes]]) -> bytes:
    node = a_leaf
    for side, sibling in proof:
        pair = sibling + node if side == "left" else node + sibling
        node = hashlib.sha256(_NODE_PREFIX + pair).digest()
    return node


def leaves_of(
    key: bytes, session: str, prompt_id: str | None, role: str, prev: str | None, text: str
) -> list[bytes]:
    return [
        leaf(salt(key, session, prompt_id, role, prev, i), line)
        for i, line in enumerate(split_lines(text))
    ]


def commit(
    key: bytes, session: str, prompt_id: str | None, role: str, prev: str | None, text: str
) -> tuple[str, int]:
    """A message's root, as hex, and its line count."""
    leaves = leaves_of(key, session, prompt_id, role, prev, text)
    return merkle_root(leaves).hex(), len(leaves)


# ---------------------------------------------------------------------------- chain


def is_sealed(line: Mapping) -> bool:
    return line.get("seal") is not None


def line_hash(line: Mapping) -> str:
    """The hash of a sealed index line, over :data:`HASHED_FIELDS` only."""
    body = json.dumps(
        {name: line.get(name) for name in HASHED_FIELDS}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(_LINE_LABEL + body).hexdigest()


def chain_tail(lines: list[dict], session: str) -> str | None:
    """The hash of the session's last sealed line, which the next line's ``prev`` holds."""
    for line in reversed(lines):
        if line.get("session") == session and is_sealed(line):
            return line_hash(line)
    return None


def seal_fields(
    lines: list[dict],
    session: str,
    prompt_id: str | None,
    role: str,
    text: str | None,
    key: bytes | None,
) -> dict:
    """The fields that chain a new line after ``lines`` and, given the text and the key,
    commit its message. With either missing the line is chained and commits nothing."""
    prev = chain_tail(lines, session)
    root: str | None = None
    count: int | None = None
    if text is not None and key is not None:
        root, count = commit(key, session, prompt_id, role, prev, text)
    return {"seal": SEAL_VERSION, "role": role, "root": root, "lines": count, "prev": prev}


def verify_chain(lines: list[dict]) -> list[str]:
    """What is wrong with one session's lines, in file order; ``[]`` when the chain holds.

    Lines written before sealing began come first and are not chained. From the first
    sealed line on, every line must be sealed and must name its predecessor's hash.
    """
    problems: list[str] = []
    tail: str | None = None
    began = False
    for position, line in enumerate(lines, 1):
        if not is_sealed(line):
            if began:
                problems.append(
                    f"line {position} is not chained, and the chain begins before it"
                )
            continue
        if line.get("prev") != tail:
            problems.append(
                f"line {position} does not follow the line before it: its prev is "
                f"{_short(line.get('prev'))}, and that line's hash is {_short(tail)}"
            )
        tail = line_hash(line)
        began = True
    return problems


def recomputed_hashes(lines: list[dict]) -> set[str]:
    """One session's line hashes, recomputed from its first sealed line forward: each
    line's ``prev`` is taken from the hash just computed for the line before it, not from
    what the line stores. A change to any line therefore changes every hash after it,
    which is what a credit's ``span_seal`` is compared against."""
    hashes: set[str] = set()
    tail: str | None = None
    for line in lines:
        if is_sealed(line):
            tail = line_hash({**line, "prev": tail})
            hashes.add(tail)
    return hashes


def _short(value: object) -> str:
    return value[:12] if isinstance(value, str) else "empty"


def verify_message(key: bytes, line: Mapping, text: str) -> bool:
    """Whether ``text`` is the message this index line committed."""
    session = line.get("session")
    role = line.get("role")
    if not isinstance(session, str) or not isinstance(role, str) or not line.get("root"):
        return False
    root, count = commit(key, session, line.get("prompt_id"), role, line.get("prev"), text)
    return hmac.compare_digest(root, str(line["root"])) and count == line.get("lines")


def span_seal(lines: list[dict], last_turn: str) -> tuple[str | None, str]:
    """A span's seal from its session's lines: the hash of the sealed line whose ``turn``
    is the span's last record, or None with why."""
    problems = verify_chain(lines)
    if problems:
        return None, f"the session's chain does not verify: {problems[0]}"
    for line in lines:
        if line.get("turn") == last_turn:
            if not is_sealed(line):
                return None, "the span's last turn was indexed before sealing began"
            return line_hash(line), ""
    return None, "the span's last turn has no line in the turn index"


# -------------------------------------------------------------------------- the key


def key_path(environ: Mapping[str, str] | None = None) -> Path:
    return store_home(dict(environ) if environ is not None else None) / KEY_FILE


def choice_path(environ: Mapping[str, str] | None = None) -> Path:
    return store_home(dict(environ) if environ is not None else None) / CHOICE_FILE


def recorded_choice(environ: Mapping[str, str] | None = None) -> str | None:
    """The key store the person chose on this machine, or None when none is recorded."""
    try:
        data = json.loads(choice_path(environ).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    choice = data.get("key_store") if isinstance(data, dict) else None
    return choice if choice in KEY_STORES else None


def chosen_store(environ: Mapping[str, str] | None = None) -> str:
    """Where the key is kept: the recorded choice, or the local file when none is."""
    return recorded_choice(environ) or STORE_FILE


def record_choice(store: str, environ: Mapping[str, str] | None = None) -> None:
    if store not in KEY_STORES:
        raise ValueError(f"unknown key store {store!r}")
    path = choice_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"key_store": store}) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _decode(text: str, where: str) -> bytes:
    try:
        key = bytes.fromhex(text.strip())
    except ValueError as exc:
        raise SealKeyError(f"{where} does not hold a hex key") from exc
    if len(key) != KEY_BYTES:
        raise SealKeyError(f"{where} holds {len(key)} bytes, and a seal key is {KEY_BYTES}")
    return key


# file


def _file_read(environ: Mapping[str, str] | None) -> bytes | None:
    path = key_path(environ)
    try:
        text = path.read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise SealKeyError(f"{path} cannot be read: {exc}") from exc
    return _decode(text, str(path))


def _file_write(key: bytes, environ: Mapping[str, str] | None) -> None:
    """Create the key file, mode 0600. An existing file is never overwritten."""
    path = key_path(environ)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    except OSError as exc:
        raise SealKeyError(f"{path} cannot be written: {exc}") from exc
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(key.hex() + "\n")


def _file_delete(environ: Mapping[str, str] | None) -> None:
    key_path(environ).unlink(missing_ok=True)


# the login keychain, through `security`


def _keychain_args(environ: Mapping[str, str] | None) -> list[str]:
    named = (environ if environ is not None else os.environ).get(KEYCHAIN_ENV)
    return [named] if named else []


def _security(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    if sys.platform != "darwin":
        raise SealKeyError("the keychain stores exist on macOS only")
    try:
        return subprocess.run(  # noqa: S603 - a fixed system tool, no shell
            ["/usr/bin/security", *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=_SECURITY_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SealKeyError(f"`security` did not answer: {exc}") from exc


_SECURITY_NOT_FOUND = 44


def _keychain_read(environ: Mapping[str, str] | None) -> bytes | None:
    result = _security(
        ["find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"]
        + _keychain_args(environ)
    )
    if result.returncode == _SECURITY_NOT_FOUND:
        return None
    if result.returncode != 0:
        raise SealKeyError(f"the keychain did not give the seal key: {result.stderr.strip()}")
    return _decode(result.stdout, "the keychain item")


def _keychain_write(key: bytes, environ: Mapping[str, str] | None) -> None:
    """Add the item. The command goes to ``security -i`` on stdin, so that the key is
    never an argument another process could read from the process table."""
    target = "".join(f' "{name}"' for name in _keychain_args(environ))
    command = (
        f"add-generic-password -s {KEYCHAIN_SERVICE} -a {KEYCHAIN_ACCOUNT} "
        f"-w {key.hex()}{target}\n"
    )
    result = _security(["-i"], stdin=command)
    if result.returncode != 0:
        raise SealKeyError(f"the keychain did not take the seal key: {result.stderr.strip()}")


def _keychain_delete(environ: Mapping[str, str] | None) -> None:
    _security(
        ["delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT]
        + _keychain_args(environ)
    )


# iCloud Keychain, through the Security framework

_ERR_ITEM_NOT_FOUND = -25300
_UTF8 = 0x08000100


def _sec_item(
    operation: str,
    key: bytes | None = None,
    *,
    synchronizable: bool = True,
    service: str = KEYCHAIN_SERVICE,
):
    """One Security framework call on the synchronizable seal-key item.

    ``operation`` is ``read``, ``add`` or ``delete``; returns ``(status, data)``. This is
    the seam the tests replace: ``security`` cannot address synchronizable items, so the
    calls go through :mod:`ctypes`, and a real ``add`` would write to the account's
    iCloud Keychain.
    """
    if sys.platform != "darwin":
        raise SealKeyError("the keychain stores exist on macOS only")
    void = ctypes.c_void_p
    cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    sec = ctypes.CDLL(ctypes.util.find_library("Security"))
    cf.CFStringCreateWithCString.restype = void
    cf.CFStringCreateWithCString.argtypes = [void, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFDataCreate.restype = void
    cf.CFDataCreate.argtypes = [void, ctypes.c_char_p, ctypes.c_long]
    cf.CFDataGetLength.restype = ctypes.c_long
    cf.CFDataGetLength.argtypes = [void]
    cf.CFDataGetBytePtr.restype = ctypes.POINTER(ctypes.c_ubyte)
    cf.CFDataGetBytePtr.argtypes = [void]
    cf.CFDictionaryCreateMutable.restype = void
    cf.CFDictionaryCreateMutable.argtypes = [void, ctypes.c_long, void, void]
    cf.CFDictionarySetValue.argtypes = [void, void, void]
    cf.CFRelease.argtypes = [void]
    for name in ("SecItemCopyMatching", "SecItemAdd"):
        getattr(sec, name).restype = ctypes.c_int32
        getattr(sec, name).argtypes = [void, ctypes.POINTER(void)]
    sec.SecItemDelete.restype = ctypes.c_int32
    sec.SecItemDelete.argtypes = [void]

    def constant(library, name: str):
        return void.in_dll(library, name)

    def string(text: str):
        return cf.CFStringCreateWithCString(None, text.encode("utf-8"), _UTF8)

    owned = [string(service), string(KEYCHAIN_ACCOUNT)]
    query = cf.CFDictionaryCreateMutable(
        None,
        0,
        ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")),
        ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryValueCallBacks")),
    )
    true = constant(cf, "kCFBooleanTrue")
    cf.CFDictionarySetValue(
        query, constant(sec, "kSecClass"), constant(sec, "kSecClassGenericPassword")
    )
    cf.CFDictionarySetValue(query, constant(sec, "kSecAttrService"), owned[0])
    cf.CFDictionarySetValue(query, constant(sec, "kSecAttrAccount"), owned[1])
    if synchronizable:
        cf.CFDictionarySetValue(query, constant(sec, "kSecAttrSynchronizable"), true)
    try:
        if operation == "read":
            cf.CFDictionarySetValue(query, constant(sec, "kSecReturnData"), true)
            found = void()
            status = sec.SecItemCopyMatching(query, ctypes.byref(found))
            if status != 0 or not found.value:
                return status, None
            length = cf.CFDataGetLength(found)
            data = bytes(cf.CFDataGetBytePtr(found)[:length])
            cf.CFRelease(found)
            return status, data
        if operation == "add":
            if key is None:
                raise ValueError("add needs the key")
            payload = key.hex().encode("ascii")
            data_ref = cf.CFDataCreate(None, payload, len(payload))
            owned.append(data_ref)
            cf.CFDictionarySetValue(query, constant(sec, "kSecValueData"), data_ref)
            # A synchronizable item cannot be bound to this device, so the accessibility
            # class is the one without "ThisDeviceOnly".
            cf.CFDictionarySetValue(
                query,
                constant(sec, "kSecAttrAccessible"),
                constant(sec, "kSecAttrAccessibleAfterFirstUnlock"),
            )
            return sec.SecItemAdd(query, None), None
        if operation == "delete":
            return sec.SecItemDelete(query), None
        raise ValueError(f"unknown operation {operation!r}")
    finally:
        for ref in owned:
            cf.CFRelease(ref)
        cf.CFRelease(query)


def _icloud_read(environ: Mapping[str, str] | None) -> bytes | None:
    status, data = _sec_item("read")
    if status == _ERR_ITEM_NOT_FOUND:
        return None
    if status != 0 or data is None:
        raise SealKeyError(
            f"the synchronizable keychain did not give the seal key (OSStatus {status})"
        )
    return _decode(data.decode("ascii", errors="replace"), "the synchronizable keychain item")


def _icloud_write(key: bytes, environ: Mapping[str, str] | None) -> None:
    status, _ = _sec_item("add", key)
    if status != 0:
        raise SealKeyError(
            f"the synchronizable keychain did not take the seal key (OSStatus {status})"
        )


def _icloud_delete(environ: Mapping[str, str] | None) -> None:
    _sec_item("delete")


_READ = {STORE_FILE: _file_read, STORE_KEYCHAIN: _keychain_read, STORE_ICLOUD: _icloud_read}
_WRITE = {STORE_FILE: _file_write, STORE_KEYCHAIN: _keychain_write, STORE_ICLOUD: _icloud_write}
_DELETE = {
    STORE_FILE: _file_delete,
    STORE_KEYCHAIN: _keychain_delete,
    STORE_ICLOUD: _icloud_delete,
}


def read_key(store: str, environ: Mapping[str, str] | None = None) -> bytes | None:
    """The key ``store`` holds, or None when it holds none. Raises :class:`SealKeyError`."""
    return _READ[store](environ)


def load_key(environ: Mapping[str, str] | None = None) -> bytes:
    """The seal key from the chosen store, made there on first use.

    Raises :class:`SealKeyError` when the store cannot be read or written; the caller
    then writes its line chained and uncommitted, since a salt cannot be made.
    """
    store = chosen_store(environ)
    key = read_key(store, environ)
    if key is not None:
        return key
    _WRITE[store](secrets.token_bytes(KEY_BYTES), environ)
    # Read back rather than trust what was generated: when two hooks run at once, the
    # one that lost the race to create the key must use the winner's.
    key = read_key(store, environ)
    if key is None:
        raise SealKeyError(f"the {store} key store did not keep the new seal key")
    return key


def move_key(target: str, environ: Mapping[str, str] | None = None) -> str:
    """Make ``target`` the key store, taking the existing key along; says what was done.

    The key is copied, read back from ``target`` and only then removed from where it
    was, so that roots already committed stay reproducible and a failed move loses
    nothing. With no key anywhere, one is made in ``target``.
    """
    if target not in KEY_STORES:
        raise ValueError(f"unknown key store {target!r}")
    current = chosen_store(environ)
    held = read_key(target, environ)
    if current == target:
        if held is None:
            _WRITE[target](secrets.token_bytes(KEY_BYTES), environ)
            done = "a new key was made there"
        else:
            done = "the key was already there"
    else:
        existing = read_key(current, environ)
        if existing is None:
            if held is None:
                _WRITE[target](secrets.token_bytes(KEY_BYTES), environ)
                done = "a new key was made there"
            else:
                done = "the key already there is kept"
        elif held is not None and held != existing:
            raise SealKeyError(
                f"the {target} key store already holds a different seal key from the one "
                f"in the {current} store, and neither was changed"
            )
        else:
            if held is None:
                _WRITE[target](existing, environ)
            if read_key(target, environ) != existing:
                raise SealKeyError(
                    f"the {target} key store did not return the key it was given; the key "
                    f"stays in the {current} store"
                )
            _DELETE[current](environ)
            done = f"the key was moved from the {current} store"
    if read_key(target, environ) is None:
        raise SealKeyError(f"the {target} key store did not keep the seal key")
    record_choice(target, environ)
    return done
