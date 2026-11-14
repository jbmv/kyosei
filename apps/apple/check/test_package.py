"""KYO-944.c1: ``package.sh check`` writes the check build and nothing of the app's package.

The build is run here, which takes a few seconds once ``make app-check`` has built it, so
that the app's own package directory can be read before and after it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import harness
import pytest

PACKAGE = harness.APPLE / ".build" / "package"
SCRIPT = harness.APPLE / "package.sh"
ASKS_FOR_MORE = (
    "KYOSEI_NOTARY_KEY",
    "KYOSEI_NOTARY_KEY_ID",
    "KYOSEI_NOTARY_ISSUER",
    "KYOSEI_PROVISIONING_PROFILE",
)


def _environment(**added: str) -> dict[str, str]:
    kept = {name: value for name, value in os.environ.items() if name not in ASKS_FOR_MORE}
    kept.pop("KYOSEI_SIGN_IDENTITY", None)
    return {**kept, **added}


def _listing(directory: Path) -> list[tuple[str, int, int]] | None:
    """Every file under ``directory`` with its size and modification time, or None."""
    if not directory.exists():
        return None
    return sorted(
        (str(path.relative_to(directory)), path.lstat().st_size, path.lstat().st_mtime_ns)
        for path in directory.rglob("*")
    )


@pytest.mark.parametrize("name", ASKS_FOR_MORE)
def test_check_mode_refuses_to_notarize(name: str) -> None:
    before = _listing(harness.CHECK_APP)
    done = subprocess.run(
        ["/bin/sh", str(SCRIPT), "check"],
        env=_environment(**{name: "anything"}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 2, done
    assert name in done.stderr and "never notarized" in done.stderr
    # It refused before building: the check build is as it was.
    assert _listing(harness.CHECK_APP) == before


def test_check_mode_writes_the_check_build_and_leaves_the_apps_package() -> None:
    before = _listing(PACKAGE)
    done = subprocess.run(
        ["/bin/sh", str(SCRIPT), "check"],
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=230,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert _listing(PACKAGE) == before

    app = harness.APPLE / ".build" / "package-check" / "KyoseiCheck.app"
    assert done.stdout.strip().splitlines()[-1] == ".build/package-check/KyoseiCheck.app"
    plist = app / "Contents" / "Info.plist"

    def read(key: str) -> str:
        return subprocess.run(
            ["/usr/libexec/PlistBuddy", "-c", f"Print :{key}", str(plist)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    assert read("CFBundleIdentifier") == "dev.kyosei.apple.check"
    # KYO-945: an accessory app from its first moment, so that starting it brings nothing
    # in front of the app a person is using.
    assert read("LSUIElement") == "true"
    assert read("CFBundleExecutable") == "KyoseiCheck"
    executable = app / "Contents" / "MacOS" / "KyoseiCheck"
    assert harness.MARKER in executable.read_bytes()
    assert (app / "Contents" / "Resources" / "KyoseiApple_KyoseiMac.bundle").is_dir()
    assert not (app / "Contents" / "embedded.provisionprofile").exists()
    # Signed, ad hoc when no identity is given, and with no notarization ticket stapled.
    assert subprocess.run(["/usr/bin/codesign", "--verify", str(app)], check=False).returncode == 0
    described = subprocess.run(
        ["/usr/bin/codesign", "-dv", str(app)], capture_output=True, text=True, check=False
    ).stderr
    assert "Signature=adhoc" in described
    assert not (app / "Contents" / "CodeResources").exists()


def test_the_script_takes_no_other_argument() -> None:
    done = subprocess.run(
        ["/bin/sh", str(SCRIPT), "notarize"], env=_environment(), capture_output=True, check=False
    )
    assert done.returncode == 2


def test_the_apps_packaging_refuses_an_executable_that_holds_the_channel() -> None:
    """The app's own packaging reads the executable it built for the channel's marker and
    stops before packaging one that holds it. Read from the script, since running the app's
    packaging is not the harness's to do: the check is there, and it comes before the
    package directory is written."""
    script = SCRIPT.read_text(encoding="utf-8")
    app_part = script.split("OUT=.build/package\n", 1)[1]
    guard = 'if grep -q "$MARKER" "$PRODUCTS/KyoseiMac"; then'
    assert "MARKER=KYOSEI_CHECK_SOCKET" in script
    assert guard in app_part
    assert app_part.index(guard) < app_part.index('rm -rf "$OUT"')
    assert "-DKYOSEI_CHECK" not in app_part
