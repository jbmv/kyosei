"""``kyo config [--machine] [--json]``: a project's or the machine's configuration, read.

KYO-952, the read side of proposal KYO-950. The document is built by
:mod:`kyosei.config_view`, which also serves ``GET /v1/projects/{project}/config`` and
``GET /v1/settings`` (:mod:`kyosei.serve_config`); this command is the terminal's copy of
the same read. It writes nothing and opens no database. Unlike the served documents it
prints ``orchestrator_command`` in full, since the person at the terminal is on the machine.
"""

from __future__ import annotations

from argparse import Namespace

from . import config_view
from .commands import Context, Output


def cmd_config(ctx: Context, args: Namespace) -> Output:
    if args.machine:
        document = config_view.build_machine_document(ctx.environ)
    else:
        assert ctx.layout is not None
        document = config_view.build_project_document(ctx.layout, ctx.environ)
    return Output(config_view.render_text(document), document)
