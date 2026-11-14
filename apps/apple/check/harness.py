"""The Mac app's check harness: a test server, a check build, and the channel between them.

A run starts the real ``kyo serve`` over a temporary store, starts the check build of the Mac
app (``package.sh check``) against it, and drives and reads the app through the check channel
the build listens on (``Sources/KyoseiMac/CheckChannel.swift``). It needs no permission from
the system: the app acts on itself and takes its own screenshots.

WHAT A RUN IS KEPT FROM. A run happens on a machine a person is using, which also runs their
own ``kyo serve``, their sessions in tmux and the installed app.

- The server's ``KYOSEI_HOME``, ``CLAUDE_CONFIG_DIR`` and ``HOME`` are directories of the run,
  so the registry of projects, the device tokens, drafts and transcripts are the run's own.
- The first ``tmux`` on the server's ``PATH`` is a wrapper (:func:`write_tmux_wrapper`). The
  server asks two fixed tmux servers for panes, ``-L default`` and ``-L kyosei``, which are
  the person's own; the wrapper maps both to a private socket in the run's directory, refuses
  every other call, and logs each call with what it did.
- The app is the check build, which has its own bundle identifier and so its own settings,
  reads its server and its token from the environment alone, and refuses to start without a
  server. ``CFFIXED_USER_HOME`` moves the page's storage into the run's directory.
- The run stops only the processes it started, by the process objects it holds.

The harness is outside ``tests/``, the only path ``pyproject.toml`` collects, so ``make test``
and CI do not run it. ``make app-check`` does.
"""

from __future__ import annotations

import contextlib
import hashlib
import html
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
APPLE = HERE.parent
REPO = APPLE.parent.parent
CHECK_APP = APPLE / ".build" / "package-check" / "KyoseiCheck.app"
CHECK_EXECUTABLE = CHECK_APP / "Contents" / "MacOS" / "KyoseiCheck"
CHECK_IDENTIFIER = "dev.kyosei.apple.check"
SHIPPED_IDENTIFIER = "dev.kyosei.apple"
MARKER = b"KYOSEI_CHECK_SOCKET"

#: The tmux servers ``kyo serve`` asks for panes (``claude_code_pane.PANE_SOCKETS``), which the
#: wrapper maps to the run's private socket.
MAPPED_SOCKETS = ("default", "kyosei")

#: Where the check build's window is put, as ``x,y,width,height`` in screen points, unless the
#: environment of the run says otherwise.
DEFAULT_FRAME = "60,60,1180,860"

#: Every permission a device token can carry, so that the app under test is refused nothing.
PERMISSIONS = ("send", "notes", "start", "terminal", "files")

LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
    "/Support/lsregister"
)


class CheckError(AssertionError):
    """The harness could not do what a scenario asked of it."""


# ------------------------------------------------------------------------- the tmux wrapper


def write_tmux_wrapper(
    directory: Path, *, real_tmux: str | None, socket_path: Path, log: Path
) -> Path:
    """Write the ``tmux`` the test server finds first on its ``PATH``, and return its path.

    A call that begins ``-L default`` or ``-L kyosei`` is run by the real tmux against the
    private socket, with those two arguments replaced by ``-S <socket>`` and no configuration
    file read. Any other call is refused with status 1: one that names another socket, and
    one that names none, which tmux would send to the server ``$TMUX`` or its default names.
    Each call is one line of the log: ``mapped`` or ``refused``, a tab, the socket it reached
    or ``-``, a tab, and the arguments it was given.

    With no tmux installed the wrapper refuses everything, which a server that finds no pane
    reads as no session.
    """
    directory.mkdir(parents=True, exist_ok=True)
    wrapper = directory / "tmux"
    names = "|".join(f'"-L {name}"' for name in MAPPED_SOCKETS)
    mapped = (
        f'    printf \'mapped\\t%s\\t%s\\n\' "{socket_path}" "$*" >> "{log}"\n'
        "    shift 2\n"
        "    unset TMUX TMUX_PANE TMUX_TMPDIR\n"
        f'    exec "{real_tmux}" -S "{socket_path}" -f /dev/null "$@"\n'
        if real_tmux
        else f'    printf \'refused\\t-\\t%s\\n\' "$*" >> "{log}"\n    exit 1\n'
    )
    wrapper.write_text(
        "#!/bin/sh\n"
        "# Written by the Mac app's check harness (apps/apple/check/harness.py).\n"
        'case "${1:-} ${2:-}" in\n'
        f"  {names})\n"
        f"{mapped}"
        "    ;;\n"
        "esac\n"
        f'printf \'refused\\t-\\t%s\\n\' "$*" >> "{log}"\n'
        'echo "tmux (check harness wrapper): refused; only -L default and -L kyosei are '
        "mapped, to the run's private socket\" >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


@dataclass(frozen=True)
class TmuxCall:
    verdict: str
    socket: str
    arguments: str


def read_tmux_log(log: Path) -> list[TmuxCall]:
    """Every call the wrapper took, in order."""
    if not log.exists():
        return []
    calls = []
    for line in log.read_text(encoding="utf-8").splitlines():
        verdict, _, rest = line.partition("\t")
        reached, _, arguments = rest.partition("\t")
        calls.append(TmuxCall(verdict, reached, arguments))
    return calls


def calls_reaching_another_socket(log: Path, socket_path: Path) -> list[TmuxCall]:
    """The logged calls that were neither refused nor sent to the private socket."""
    return [
        call
        for call in read_tmux_log(log)
        if not (
            (call.verdict == "refused" and call.socket == "-")
            or (call.verdict == "mapped" and call.socket == str(socket_path))
        )
    ]


# ------------------------------------------------------------------------------ the run


def _short_directory() -> Path:
    """A directory for the run whose path leaves room for a Unix socket's name in it."""
    made = Path(tempfile.mkdtemp(prefix="kyo-check-"))
    if len(str(made)) > 60:
        shutil.rmtree(made)
        made = Path(tempfile.mkdtemp(prefix="kyo-check-", dir="/tmp"))  # noqa: S108
    return made


def _scrubbed(environ: dict[str, str]) -> dict[str, str]:
    """The environment without what would point a process at the person's own state."""
    dropped = ("TMUX", "KYOSEI_", "CLAUDE", "VIRTUAL_ENV", "__CFBundleIdentifier", "XPC_")
    return {name: value for name, value in environ.items() if not name.startswith(dropped)}


@dataclass
class Run:
    """One run's directory and the processes it started."""

    directory: Path = field(default_factory=_short_directory)
    started: list[subprocess.Popen] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.directory.chmod(0o700)
        self.home = self.directory / "home"
        self.kyosei_home = self.directory / "kyosei-home"
        self.claude = self.directory / "claude"
        self.bin = self.directory / "bin"
        self.tmux_socket = self.directory / "tmux.sock"
        self.tmux_log = self.directory / "tmux.log"
        for made in (
            self.home,
            self.kyosei_home,
            self.claude / "sessions",
            self.claude / "projects",
        ):
            made.mkdir(parents=True, exist_ok=True)
        self.real_tmux = shutil.which("tmux")
        self.wrapper = write_tmux_wrapper(
            self.bin, real_tmux=self.real_tmux, socket_path=self.tmux_socket, log=self.tmux_log
        )

    def server_environment(self) -> dict[str, str]:
        """The environment of the test server and of every ``kyo`` command of the run."""
        return {
            **_scrubbed(dict(os.environ)),
            "HOME": str(self.home),
            "KYOSEI_HOME": str(self.kyosei_home),
            "CLAUDE_CONFIG_DIR": str(self.claude),
            # The wrapper first, then the system's own directories and nothing else.
            "PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Check Harness",
            "GIT_AUTHOR_EMAIL": "check@example.invalid",
            "GIT_COMMITTER_NAME": "Check Harness",
            "GIT_COMMITTER_EMAIL": "check@example.invalid",
        }

    def kyo(self, *arguments: str, cwd: Path, stdin: str | None = None) -> str:
        """Run one ``kyo`` command of this checkout in the run's environment."""
        done = subprocess.run(  # noqa: S603 - this interpreter and this checkout's kyosei
            [sys.executable, "-m", "kyosei", *arguments],
            cwd=cwd,
            env=self.server_environment(),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if done.returncode != 0:
            raise CheckError(f"kyo {' '.join(arguments)} exited {done.returncode}: {done.stderr}")
        return done.stdout

    def git(self, *arguments: str, cwd: Path) -> str:
        done = subprocess.run(  # noqa: S603
            ["/usr/bin/git", *arguments],
            cwd=cwd,
            env=self.server_environment(),
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return done.stdout

    def stop(self) -> None:
        """Stop what the run started and nothing else, then remove the run's directory."""
        for process in reversed(self.started):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        # A tmux server exists on the private socket only if a mapped call started one. It
        # is the run's own, and it is addressed by its socket and by nothing else.
        if self.real_tmux and self.tmux_socket.exists():
            subprocess.run(  # noqa: S603
                [self.real_tmux, "-S", str(self.tmux_socket), "kill-server"],
                env=_scrubbed(dict(os.environ)),
                capture_output=True,
                timeout=10,
                check=False,
            )
        shutil.rmtree(self.directory, ignore_errors=True)


# ------------------------------------------------------------------------------ the server


@dataclass
class Server:
    run: Run
    project: Path
    name: str
    process: subprocess.Popen
    url: str
    log: Path
    version: str
    token: str

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)


def make_project(run: Run, name: str = "checked") -> Path:
    """A git repository with a Kyosei store in it, registered under the run's own home."""
    project = run.directory / name
    project.mkdir()
    run.git("init", "-q", "-b", "main", ".", cwd=project)
    run.kyo("init", ".", "--in-repo", "--prefix", "CHK", "--read-transcripts", "yes", cwd=project)
    return project


def commit_all(run: Run, project: Path, message: str) -> None:
    run.git("add", "-A", cwd=project)
    run.git("commit", "-q", "-m", message, cwd=project)


def start_server(
    run: Run, project: Path, *, permissions: tuple[str, ...] = PERMISSIONS, deadline: float = 30.0
) -> Server:
    """Start ``kyo serve`` on a free port over ``project`` and issue a device token for it."""
    log = run.directory / "serve.log"
    token = run.kyo(
        "devices",
        "issue",
        "check-harness",
        "--token-only",
        *[part for permission in permissions for part in ("--permission", permission)],
        cwd=project,
    ).strip()
    with log.open("wb") as stream:
        process = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "kyosei", "serve", "--port", "0"],
            cwd=project,
            env=run.server_environment(),
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    run.started.append(process)
    end = time.monotonic() + deadline
    marker = "kyo serve: serving "
    while time.monotonic() < end:
        text = log.read_text(encoding="utf-8", errors="replace")
        if marker in text:
            line = next(row for row in text.splitlines() if marker in row)
            address = line.split(marker, 1)[1].split()[0]
            url = address.rsplit("/v1", 1)[0]
            version = line.split("(", 1)[1].rsplit(")", 1)[0] if "(" in line else ""
            return Server(run, project, project.name, process, url, log, version, token)
        if process.poll() is not None:
            raise CheckError(f"kyo serve exited {process.returncode} before serving:\n{text}")
        time.sleep(0.05)
    process.terminate()
    raise CheckError(
        f"kyo serve did not say it was serving within {deadline} s:\n{log.read_text()}"
    )


# ------------------------------------------------------------------------------ the channel


class ChannelError(CheckError):
    """A request the app answered with ``ok: false``."""


class Channel:
    """The runner's end of the check channel: one request and one answer at a time."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.connect(str(path))
        self.buffer = b""
        self.count = 0

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.socket.close()

    def request(self, op: str, *, timeout: float = 30.0, **fields: Any) -> Any:
        self.count += 1
        body = {"op": op, "id": self.count, **fields}
        self.socket.settimeout(timeout)
        self.socket.sendall(json.dumps(body).encode("utf-8") + b"\n")
        while b"\n" not in self.buffer:
            chunk = self.socket.recv(65536)
            if not chunk:
                raise CheckError(f"the app closed the channel before answering {op}")
            self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\n")
        answer = json.loads(line)
        if answer.get("id") != self.count:
            raise CheckError(
                f"the answer to request {self.count} carried the id {answer.get('id')}"
            )
        if not answer.get("ok"):
            raise ChannelError(f"{op}: {answer.get('error')}")
        return answer.get("value")

    def eval(self, script: str, **arguments: Any) -> Any:
        """Run ``script``, the body of an async function, in the page, and return its value."""
        return self.request("eval", script=script, args=arguments)

    def wait(self, what: str, script: str, *, deadline: float = 15.0, **arguments: Any) -> Any:
        """Ask ``script`` until its value is truthy; every wait of a scenario is a condition
        asked of the page with a limit, and none is a sleep."""
        end = time.monotonic() + deadline
        value = None
        while time.monotonic() < end:
            value = self.eval(script, **arguments)
            if value:
                return value
            time.sleep(0.1)
        raise CheckError(f"waited {deadline} s for {what}; the last answer was {value!r}")


# ------------------------------------------------------------------------------ the app


def executable_digest(executable: Path = CHECK_EXECUTABLE) -> str:
    return hashlib.sha256(executable.read_bytes()).hexdigest()


def require_check_build(app: Path = CHECK_APP) -> Path:
    """The check build's executable, after checking that it is one.

    The bundle's identifier is read from its ``Info.plist`` and the marker from its
    executable, so that a run can never start the shipped app, whose settings are the
    person's own, by being pointed at the wrong bundle.
    """
    executable = app / "Contents" / "MacOS" / "KyoseiCheck"
    if not executable.exists():
        raise CheckError(f"no check build at {app}; build it with `sh apps/apple/package.sh check`")
    identifier = subprocess.run(  # noqa: S603
        [
            "/usr/libexec/PlistBuddy",
            "-c",
            "Print :CFBundleIdentifier",
            str(app / "Contents" / "Info.plist"),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if identifier != CHECK_IDENTIFIER:
        raise CheckError(f"{app} has the identifier {identifier}, not {CHECK_IDENTIFIER}")
    if MARKER not in executable.read_bytes():
        raise CheckError(f"{executable} holds no check channel; it is not a check build")
    return executable


def forget_settings() -> None:
    """Delete the check build's own defaults, so that a scenario starts from none.

    The domain is the check build's and never the shipped app's: the name is a constant of
    this module, and the shipped app's differs from it.
    """
    assert CHECK_IDENTIFIER != SHIPPED_IDENTIFIER and CHECK_IDENTIFIER.endswith(".check")
    subprocess.run(  # noqa: S603
        ["/usr/bin/defaults", "delete", CHECK_IDENTIFIER], capture_output=True, check=False
    )


@dataclass
class App:
    process: subprocess.Popen
    channel: Channel
    log: Path
    #: Seconds from starting the executable to the channel answering with a window.
    launch_seconds: float

    def quit(self, *, deadline: float = 15.0) -> int:
        """End the app as its Quit item does, and return its exit status."""
        with contextlib.suppress(CheckError, OSError):
            self.channel.request("quit")
        self.channel.close()
        try:
            return self.process.wait(timeout=deadline)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                return self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                return self.process.wait(timeout=5)


def start_app(
    run: Run,
    server: Server,
    *,
    project: str | None = None,
    frame: str | None = None,
    activate: bool = False,
    deadline: float = 30.0,
) -> App:
    """Start the check build against ``server`` and wait for its channel and its window."""
    executable = require_check_build()
    channel_path = run.directory / "channel.sock"
    user_home = run.directory / "app-home"
    user_home.mkdir(exist_ok=True)
    log = run.directory / "app.log"
    environment = {
        **_scrubbed(dict(os.environ)),
        "KYOSEI_SERVE_URL": server.url,
        "KYOSEI_CHECK_SOCKET": str(channel_path),
        "KYOSEI_CHECK_TOKEN": server.token,
        "KYOSEI_CHECK_FRAME": frame or os.environ.get("KYOSEI_CHECK_FRAME") or DEFAULT_FRAME,
        # Measured in the proposal (KYO-942): this moves WebKit's storage, and so the
        # page's saved state, under the directory named.
        "CFFIXED_USER_HOME": str(user_home),
    }
    if project is not None:
        environment["KYOSEI_PROJECT"] = project
    if activate:
        environment["KYOSEI_CHECK_ACTIVATE"] = "1"
    began = time.monotonic()
    with log.open("ab") as stream:
        process = subprocess.Popen(  # noqa: S603
            [str(executable)],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    run.started.append(process)
    end = began + deadline
    channel: Channel | None = None
    while time.monotonic() < end:
        if process.poll() is not None:
            raise CheckError(f"the check build exited {process.returncode}:\n{log.read_text()}")
        try:
            channel = channel or Channel(channel_path)
            state = channel.request("state")
        except (OSError, ChannelError):
            time.sleep(0.05)
            continue
        if state.get("bundle") != CHECK_IDENTIFIER:
            process.terminate()
            raise CheckError(f"the app started is {state.get('bundle')}, not {CHECK_IDENTIFIER}")
        return App(process, channel, log, time.monotonic() - began)
    process.terminate()
    raise CheckError(f"the check build gave no window within {deadline} s:\n{log.read_text()}")


def unregister(app: Path = CHECK_APP) -> None:
    """Remove the check build from LaunchServices, so that nothing opens it by mistake."""
    if Path(LSREGISTER).exists():
        subprocess.run([LSREGISTER, "-u", str(app)], capture_output=True, check=False)  # noqa: S603


def leftover_check_builds() -> list[str]:
    """The running processes whose executable is a check build, as ``ps`` lists them."""
    listed = subprocess.run(  # noqa: S603
        ["/bin/ps", "-axo", "pid=,comm="], capture_output=True, text=True, check=True
    ).stdout
    return [row.strip() for row in listed.splitlines() if row.strip().endswith("/KyoseiCheck")]


# ------------------------------------------------------------------------------ reading a PNG


@dataclass
class Picture:
    width: int
    height: int
    channels: int
    rows: list[bytes]

    def pixel(self, x: int, y: int) -> tuple[int, ...]:
        at = x * self.channels
        return tuple(self.rows[y][at : at + self.channels])

    def colours(self, left: int, top: int, right: int, bottom: int) -> set[tuple[int, ...]]:
        """The distinct colours in a rectangle of the picture, alpha left out."""
        found: set[tuple[int, ...]] = set()
        for y in range(max(top, 0), min(bottom, self.height)):
            row = self.rows[y]
            for x in range(max(left, 0), min(right, self.width)):
                at = x * self.channels
                found.add(tuple(row[at : at + 3]))
        return found


def read_png(path: Path) -> Picture:
    """Decode an 8-bit RGB or RGBA PNG that is not interlaced, which is what the app writes."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise CheckError(f"{path} is not a PNG")
    at = 8
    packed = b""
    width = height = depth = kind = interlace = 0
    while at < len(data):
        (length,) = struct.unpack(">I", data[at : at + 4])
        name = data[at + 4 : at + 8]
        body = data[at + 8 : at + 8 + length]
        at += 12 + length
        if name == b"IHDR":
            width, height, depth, kind, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif name == b"IDAT":
            packed += body
    if depth != 8 or kind not in (2, 6) or interlace:
        raise CheckError(f"{path}: depth {depth}, colour type {kind}, interlace {interlace}")
    channels = 4 if kind == 6 else 3
    stride = width * channels
    raw = zlib.decompress(packed)
    rows: list[bytes] = []
    before = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        method = raw[start]
        line = bytearray(raw[start + 1 : start + 1 + stride])
        if method == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 255
        elif method == 2:
            for i in range(stride):
                line[i] = (line[i] + before[i]) & 255
        elif method == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + before[i]) >> 1)) & 255
        elif method == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                above = before[i]
                corner = before[i - channels] if i >= channels else 0
                guess = left + above - corner
                nearest = min(
                    (abs(guess - left), 0, left),
                    (abs(guess - above), 1, above),
                    (abs(guess - corner), 2, corner),
                )[2]
                line[i] = (line[i] + nearest) & 255
        rows.append(bytes(line))
        before = line
    return Picture(width, height, channels, rows)


# ------------------------------------------------------------------------------ the result


@dataclass
class Assertion:
    id: str
    text: str
    passed: bool
    detail: str = ""
    screenshot: str | None = None
    look: bool = False


@dataclass
class Scenario:
    """One scenario's assertions and screenshots, written into the run's result."""

    name: str
    title: str
    out: Path
    app: App | None = None
    assertions: list[Assertion] = field(default_factory=list)
    began: float = field(default_factory=time.monotonic)
    seconds: float | None = None
    error: str | None = None

    def snapshot(self, name: str) -> str:
        """A screenshot of the app's window, taken by the app, and its file name."""
        assert self.app is not None
        file = f"{self.name}-{name}.png"
        self.app.channel.request("snapshot", path=str(self.out / file))
        return file

    def check(
        self, id: str, text: str, passed: Any, detail: Any = "", *, look: str | None = None
    ) -> bool:
        """Record one assertion. ``look`` names a screenshot taken now and put beside the
        assertion's text on the review sheet, for what a person judges by eye."""
        shot = self.snapshot(look) if look is not None and self.app is not None else None
        said = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
        self.assertions.append(Assertion(id, text, bool(passed), said, shot, look is not None))
        return bool(passed)

    def failed(self) -> list[Assertion]:
        return [assertion for assertion in self.assertions if not assertion.passed]

    def finish(self) -> None:
        self.seconds = round(time.monotonic() - self.began, 2)

    def document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "passed": self.error is None and not self.failed() and bool(self.assertions),
            "seconds": self.seconds,
            "error": self.error,
            "assertions": [vars(assertion) for assertion in self.assertions],
        }


def identity(server_version: str) -> dict[str, Any]:
    """What a run tested: the commit, the check build's executable and the server's kyo."""

    def git(*arguments: str) -> str:
        return subprocess.run(  # noqa: S603
            ["/usr/bin/git", *arguments], cwd=REPO, capture_output=True, text=True, check=False
        ).stdout.strip()

    return {
        "commit": git("rev-parse", "HEAD"),
        "tree_is_clean": git("status", "--porcelain") == "",
        "check_build": str(CHECK_APP),
        "check_build_identifier": CHECK_IDENTIFIER,
        "executable_sha256": executable_digest() if CHECK_EXECUTABLE.exists() else None,
        "kyo_serve": server_version,
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def write_result(
    out: Path, scenarios: list[Scenario], *, server_version: str, measurements: dict[str, Any]
) -> Path:
    """Write ``result.json`` and the review sheet ``review.html`` into ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    document = {
        "identity": identity(server_version),
        "passed": bool(scenarios) and all(scenario.document()["passed"] for scenario in scenarios),
        "measurements": measurements,
        "scenarios": [scenario.document() for scenario in scenarios],
    }
    result = out / "result.json"
    result.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "review.html").write_text(review_sheet(document), encoding="utf-8")
    return result


def review_sheet(document: dict[str, Any]) -> str:
    """The review sheet: each screenshot beside the text of what it is a look at."""
    made = document["identity"]
    parts = [
        "<!doctype html><meta charset='utf-8'><title>Mac app check: review sheet</title>",
        "<style>body{font:14px/1.45 -apple-system,sans-serif;margin:24px;max-width:1500px;"
        "color:#1d1d1f;background:#fff}h1{font-size:20px}h2{font-size:16px;margin-top:32px}"
        ".look{display:grid;grid-template-columns:320px 1fr;gap:20px;border-top:1px solid #d2d2d7;"
        "padding:16px 0}.look img{max-width:100%;border:1px solid #d2d2d7}"
        ".pass{color:#1a7f37}.fail{color:#c00;font-weight:600}code{font-size:12px}"
        ".detail{color:#6e6e73;font-size:12px;word-break:break-word}</style>",
        "<h1>Mac app check: review sheet</h1>",
        "<p>Screenshots of the check build, taken by the app of its own window. They show the "
        f"check build <code>{html.escape(str(made['check_build_identifier']))}</code> and not "
        "the installed app.</p>",
        f"<p>Commit <code>{html.escape(str(made['commit']))}</code>"
        f"{'' if made['tree_is_clean'] else ' with uncommitted changes'}; executable sha256 "
        f"<code>{html.escape(str(made['executable_sha256']))}</code>; server "
        f"<code>{html.escape(str(made['kyo_serve']))}</code>; written "
        f"{html.escape(str(made['written_at']))}.</p>",
    ]
    for scenario in document["scenarios"]:
        verdict = "pass" if scenario["passed"] else "fail"
        parts.append(
            f"<h2>Scenario {html.escape(scenario['name'])}: {html.escape(scenario['title'])} "
            f"<span class='{verdict}'>({verdict}, {scenario['seconds']} s)</span></h2>"
        )
        if scenario["error"]:
            parts.append(f"<p class='fail'>{html.escape(scenario['error'])}</p>")
        for assertion in scenario["assertions"]:
            verdict = "pass" if assertion["passed"] else "fail"
            image = (
                f"<a href='{html.escape(assertion['screenshot'])}'>"
                f"<img src='{html.escape(assertion['screenshot'])}' alt=''></a>"
                if assertion["screenshot"]
                else "<span class='detail'>no screenshot: read from the page, not looked at</span>"
            )
            parts.append(
                "<div class='look'><div>"
                f"<strong>{html.escape(assertion['id'])}</strong> "
                f"<span class='{verdict}'>{verdict}</span>"
                f"<p>{html.escape(assertion['text'])}</p>"
                f"<p class='detail'>{html.escape(assertion['detail'])}</p></div>"
                f"<div>{image}</div></div>"
            )
    return "\n".join(parts) + "\n"


def poll(what: str, done: Callable[[], Any], *, deadline: float = 10.0) -> Any:
    """Ask ``done`` until it answers something truthy, within ``deadline`` seconds."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        value = done()
        if value:
            return value
        time.sleep(0.05)
    raise CheckError(f"waited {deadline} s for {what}")


def each_line(path: Path) -> Iterator[str]:
    if path.exists():
        yield from path.read_text(encoding="utf-8", errors="replace").splitlines()
