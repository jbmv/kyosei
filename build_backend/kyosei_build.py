"""KYO-226: the build hook. setuptools, with two files written first.

An installed copy of Kyosei has no ``.git`` and no store, so the two things a consumer
needs to know about it — *which build is this* and *what changed since the one I had* —
can only be captured at the moment the wheel is built. Consumers install with
``uv tool install .`` or ``pipx install .`` from a checkout, which builds a wheel from
the working tree at that instant, and this module is what runs then.

It is an **in-tree PEP 517 backend** (``backend-path`` in ``pyproject.toml``) rather than
a ``setup.py`` ``cmdclass``: the hook has to fire for every frontend and every build kind
(wheel, editable -- and sdist, which is refused, KYO-447), and a backend that delegates
each of those explicitly is the form where that is readable rather than inferred from
which distutils command setuptools happened to route through. Everything except those
three build entry points is re-exported from ``setuptools.build_meta`` unchanged.

The stamp also records whether this build is a release (KYO-445): true only when the
commit being built carries the tag ``v<version>`` and the tree is clean at this moment,
which is why the release sequence tags *before* it builds.

Both products are written by one call to :func:`kyosei.buildinfo.regenerate`, so the
packaged changelog can never be older than the stamp beside it — see that function for
why that is the property rather than an implementation detail.

This module imports Kyosei's own code (adding ``src/`` to ``sys.path``) instead of
re-reading the store with a parser of its own. That is the same rule the rest of the
project keeps: ``kyosei.rebuild.read_record_documents`` is the one door a record document
enters through, and a build script with its own idea of what a record is would be a
second opinion nothing compares. It is also why ``pyyaml`` is in ``build-system.requires``
— the build reads the store's status registry, and the registry is YAML.
"""

from __future__ import annotations

import sys
from pathlib import Path

from setuptools import build_meta as _setuptools

#: The checkout being built. A PEP 517 backend runs with the project root as the working
#: directory, but this is derived from the module's own location instead, which is true
#: whatever the frontend does about cwd.
ROOT = Path(__file__).resolve().parent.parent

# Hooks with nothing to add, re-exported so this module is a complete backend.
get_requires_for_build_wheel = _setuptools.get_requires_for_build_wheel
get_requires_for_build_sdist = _setuptools.get_requires_for_build_sdist
get_requires_for_build_editable = _setuptools.get_requires_for_build_editable
prepare_metadata_for_build_wheel = _setuptools.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = _setuptools.prepare_metadata_for_build_editable


def _generate() -> None:
    """Write the build stamp and the packaged changelog into ``src/kyosei/``."""
    source = ROOT / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from kyosei import buildinfo

    buildinfo.regenerate(ROOT, source / "kyosei")


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    _generate()
    return _setuptools.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    _generate()
    return _setuptools.build_editable(wheel_directory, config_settings, metadata_directory)


#: KYO-447.c3: the refusal an sdist build ends in, and the remedy it names.
SDIST_REFUSAL = (
    "Kyosei does not build a source distribution. Its wheel carries two files only a "
    "checkout can write -- the build stamp (`kyo version`) and the packaged changelog "
    "(`kyo changelog`) -- and a wheel built from an sdist has neither the `.git` nor "
    "the store they are generated from; the sdist would not even contain this build "
    "backend. Build the wheel from the checkout instead: `uv build --wheel`, or "
    "`make release` to tag, build, verify and publish one "
    "(docs/reference/topics/install.md, \"Upgrading\")."
)


class SdistRefused(RuntimeError):
    """Raised by :func:`build_sdist`; its message is :data:`SDIST_REFUSAL`."""


def build_sdist(sdist_directory, config_settings=None):
    """KYO-447.c3: refuse, loudly.

    ``uv build`` with no flags builds an sdist and then a wheel *from* it. Before this
    refusal that path failed with ``backend-path entry `build_backend` does not exist``
    -- true, and silent about why. Shipping a working sdist was the alternative, and it
    was rejected: an sdist-built wheel's stamp would name no commit and its changelog
    would be empty, a release that cannot say what it is. Every route that installs
    Kyosei (``uv tool install .``, ``uv pip install <checkout>``, ``pip install .``)
    builds the wheel straight from the tree and never reaches this hook.
    """
    raise SdistRefused(SDIST_REFUSAL)
