"""``kyo onboard [--check [--all]]``: the build's own agent setup prompt, and the audit that
reads an existing project back against the same checklist (KYO-514).

The prompt is ``defaults/onboard.md``, packaged with the wheel so the setup instructions
always match the installed build (a prompt kept only on a website drifts from the CLI it
describes). It is built from the one list of setup decisions,
``docs/guide/setup-decisions.md``: every decision id there (A1, B1, ...) appears in the
prompt, asked or skipped with its reason, and ``tests/test_kyo_onboard.py`` parses both and
fails on one the prompt omits.

``--check`` is that list's readback (section F) run against a project that already exists
(section G). It reads files and asks git, and asks `claude plugin list --json` only for a
project whose settings enable the Claude Code plugin (KYO-596). It never opens
``kyosei.db`` (opening a WAL database writes its index even under ``query_only``), never
runs `kyo doctor` (which runs an export round-trip and keeps a cache file), and writes
nothing anywhere. Its `kyo doctor`
item is the result the last doctor run recorded beside the database (KYO-564). Every item it
prints is one of :data:`READBACK_LABELS`, which is also the label set of the fixed block
the prompt ends with, so the two cannot describe different checklists.
"""

from __future__ import annotations

import shlex
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path

import yaml

from .commands import Context, Output
from .errors import KyoseiError
from .git_queries import run_git

#: The packaged prompt, beside the other packaged assets.
PROMPT_ASSET = "onboard.md"

#: Replaced by the printing build's identity line, so a pasted prompt says which `kyo`
#: wrote it.
BUILD_PLACEHOLDER = "{{kyo-build}}"

#: The readback items: the labels of the fixed block the prompt ends with, and the only
#: labels `--check` prints (the machine's items once, then each project's).
MACHINE_LABELS: tuple[str, ...] = (
    "build",
    "machine layout",
    "kyo serve",
    "kyo orchestrator",
)
PROJECT_LABELS: tuple[str, ...] = (
    "store",
    "store remote",
    "prefix",
    # KYO-596: a project on the Claude Code plugin reads `harness` in place of `hooks`,
    # `skill` and `CLAUDE.md section`; one on the copies reads those three instead.
    "harness",
    "hooks",
    "skill",
    "procedures",
    "CLAUDE.md section",
    "worker_model",
    "agent types",
    "worktree_setup",
    # KYO-563.c5: the orchestrator spelling; the item's detail names the alias it reads.
    "orchestrator_only_commands",
    "scopes",
    "documents",
    # KYO-560: every declined or deferred setup decision, read back.
    "onboarding",
    "kyo doctor",
)
READBACK_LABELS: tuple[str, ...] = MACHINE_LABELS + PROJECT_LABELS

#: KYO-596: the items only a project on the copies reads, and the one only a project on
#: the plugin reads. Every other project label is read in both.
COPIES_LABELS: tuple[str, ...] = ("hooks", "skill", "CLAUDE.md section")
PLUGIN_LABELS: tuple[str, ...] = ("harness",)

#: What an item's status says. ``behind`` is always "older than the build running this";
#: ``ahead`` is the reverse, and its remedy is a newer `kyo`, never a project change.
STATUSES: dict[str, str] = {
    "present": "set up, and current with this build where that can be compared",
    "missing": "absent, or a decision never made",
    "declined": "decided against, as recorded under onboarding: in kyosei/config.yml",
    "deferred": "put off until later, as recorded under onboarding: in kyosei/config.yml",
    "behind": "installed, but older than this build's copy",
    # KYO-596: the harness item's two states of its own.
    "copied": "the plugin loads from a cached copy, not from this build's stable path",
    "doubled": "the plugin is enabled and Kyosei's copies are still in the repository",
    "ahead": "written by a newer build than this one",
    "disagrees": "set, but contradicting another setting",
    "unreadable": "this build cannot read it",
    "not-needed": "nothing to set up here, and the detail says why",
    "not-run": "--check does not run this, and the fix names what does",
    "failing": "its last recorded run found problems, named in the detail",
}

#: Directories whose presence at the code root says the repository has several
#: components, which is when scopes earn their keep (setup decision D7).
COMPONENT_DIRS = ("apps", "packages", "services")

#: Model aliases a Claude Code agent type may name instead of a full model id.
MODEL_ALIASES = ("opus", "sonnet", "haiku")

GIT_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class Item:
    """One readback line: its label, status, what was found, and what fixes it."""

    label: str
    status: str
    detail: str
    fix: str | None = None
    #: KYO-560: whether a recorded ``onboarding:`` answer may stand in for this item.
    #: False where the item is missing for a reason the decision does not cover -- no
    #: git repository at all is not "the operator declined a remote".
    answerable: bool = True

    def to_json(self) -> dict:
        return {"label": self.label, "status": self.status, "detail": self.detail, "fix": self.fix}


# ----------------------------------------------------------------------------- prompt


def packaged_prompt_text() -> str:
    """The packaged prompt as shipped, placeholder and all."""
    from .scaffold import defaults_dir

    source = defaults_dir()
    if source is None:
        raise KyoseiError("this build ships no defaults/ directory; reinstall kyosei")
    path = source / PROMPT_ASSET
    if not path.is_file():
        raise KyoseiError(f"the packaged setup prompt is missing from {path}; reinstall kyosei")
    return path.read_text(encoding="utf-8")


def build_stamp() -> str:
    """The identity line `kyo version` prints, plus the one schema this build speaks."""
    from .buildinfo import version_line
    from .schema import SCHEMA_VERSION

    return f"{version_line()} (speaks schema_version {SCHEMA_VERSION})"


#: KYO-586.c2: replaced by one sentence saying whether this build can import recx_cli, which
#: decides whether the prompt's [D12] is asked.
RECX_PLACEHOLDER = "{{recx-extra}}"


def recx_extra_sentence() -> str:
    """Whether the `kyo` printing the prompt can import recx_cli, as [D12] reads it."""
    from .prior_art import extra_installed

    if extra_installed():
        return "This build can import recx_cli: the `kyosei[recx]` extra is installed."
    return (
        "This build cannot import recx_cli: the `kyosei[recx]` extra is not installed."
    )


def render_prompt() -> str:
    return (
        packaged_prompt_text()
        .replace(BUILD_PLACEHOLDER, build_stamp())
        .replace(RECX_PLACEHOLDER, recx_extra_sentence())
    )


# ------------------------------------------------------------------------------ check


def _git(cwd: Path, *args: str) -> tuple[int, str]:
    try:
        done = run_git(cwd, *args, timeout=GIT_TIMEOUT_SECONDS)
    except OSError as exc:
        return 1, str(exc)
    return done.returncode, done.stdout.strip()


def _yaml_mapping(path: Path, key: str) -> dict | None:
    """``path``'s top-level ``key`` mapping; ``{}`` when the file is absent or empty,
    ``None`` when it cannot be read as one."""
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    value = raw.get(key) or {}
    return value if isinstance(value, dict) else None


def machine_items(environ: dict[str, str]) -> list[Item]:
    """The machine half of the readback: this build, and which login services exist."""
    from . import cmd_controller, serve_install

    items = [Item("build", "present", build_stamp())]
    manager = serve_install.platform_manager()
    serve_agent = serve_install.agent_path(manager, environ) if manager else None
    if serve_agent is None:
        serve = Item(
            "kyo serve",
            "not-needed",
            "this platform has no login service kyo serve can install (launchd and systemd "
            "only); run `kyo serve` in a terminal when a viewer needs it",
        )
    elif serve_agent.is_file():
        serve = Item("kyo serve", "present", f"login agent installed at {serve_agent}")
    else:
        serve = Item(
            "kyo serve",
            "missing",
            "no login agent; needed only for the desktop app or a dev box (setup decision E3)",
            "`kyo serve --install --apply`",
        )
    controller_agent = cmd_controller.launch_agent_path(environ) if manager == "launchd" else None
    if controller_agent is None:
        controller = Item(
            "kyo orchestrator",
            "not-needed",
            "orchestrator sessions under tmux start at login on macOS only",
        )
    elif controller_agent.is_file():
        controller = Item(
            "kyo orchestrator", "present", f"login agent installed at {controller_agent}"
        )
    else:
        controller = Item(
            "kyo orchestrator",
            "missing",
            "no login agent; needed only to run orchestrator sessions on a dev box "
            "(setup decision E5)",
            "`kyo orchestrator --install --apply`",
        )
    if serve.status == "present" and controller.status == "present":
        layout = "dev box: kyo serve and orchestrator sessions both start at login"
    elif serve.status == "present":
        layout = "kyo serve starts at login: one machine with the app, or a dev box"
    else:
        layout = "CLI only: no login service is installed"
    items.append(Item("machine layout", "present", f"{layout} (inferred, setup decision E1)"))
    items.extend([serve, controller])
    return items


def _in_source_repo(code_root: Path) -> bool:
    from .install_skill import SKILL_ASSET
    from .scaffold import repo_asset_source

    return repo_asset_source(code_root, SKILL_ASSET) is not None


#: What a source-repository reader is told instead of an install command, which would
#: write the running build's older copy over the checkout's own source.
SOURCE_REPO_FIX = (
    "`kyo doctor` -- this is Kyosei's own source repository, where the install commands "
    "refuse to write"
)


def _store_items(layout, raw: dict) -> tuple[list[Item], bool]:
    """The store and its schema, and whether its config could be read at all."""
    from .config import load_config
    from .currency import missing_database_remedy
    from .schema import SCHEMA_VERSION

    kind = "split" if layout.split else "in-repo"
    where = f"{kind} store at {layout.store_root}"
    try:
        config = load_config(layout.store_root, code_root=layout.code_root)
    except KyoseiError as exc:
        return [
            Item("store", "unreadable", f"{where}; this build refuses its config: {exc}")
        ], False
    stored = config.schema_version
    if stored > SCHEMA_VERSION:
        from .buildinfo import upgrade_route

        item = Item(
            "store",
            "ahead",
            f"{where}, schema_version {stored}; this build speaks {SCHEMA_VERSION}, so it "
            "cannot open this store",
            upgrade_route(),
        )
    elif stored < SCHEMA_VERSION:
        item = Item(
            "store",
            "behind",
            f"{where}, schema_version {stored}; this build speaks {SCHEMA_VERSION}",
            "`kyo rebuild --force`, then `kyo export --refresh`, then `kyo doctor`",
        )
    elif not config.db_path.is_file():
        item = Item(
            "store",
            "missing",
            f"{where}, but {config.db_path} is absent",
            missing_database_remedy(config),
        )
    else:
        item = Item("store", "present", f"{where}, schema_version {stored}")
    return [item], True


def _remote_item(layout) -> Item:
    repo = layout.store_root if layout.split else layout.code_root
    code, out = _git(repo, "remote", "-v")
    if code != 0:
        return Item(
            "store remote",
            "missing",
            f"{repo} is not a git repository, so the store has no history and no remote",
            f"`git -C {shlex.quote(str(repo))} init`",
            answerable=False,
        )
    urls = sorted({line.split()[1] for line in out.splitlines() if len(line.split()) > 1})
    if urls:
        travels = "" if layout.split else " (in-repo: pushing the code repository publishes it)"
        return Item("store remote", "present", ", ".join(urls) + travels)
    return Item(
        "store remote",
        "missing",
        "none: the store exists on this disk only. That is the default (setup decision B2); "
        "a private remote is a backup and any remote is a publication",
        f"`git -C {shlex.quote(str(repo))} remote add origin <private-url> && "
        f"git -C {shlex.quote(str(repo))} push -u origin HEAD`",
    )


def _hooks_item(code_root: Path, source_repo: bool) -> Item:
    from .install_hooks import (
        CLAUDE_SETTINGS_REL,
        HOOK_INSTALL_TARGETS,
        hook_drift,
        installed_elements,
        packaged_elements,
    )
    from .scaffold import repo_hooks_source

    fix = (
        SOURCE_REPO_FIX
        if source_repo
        else ("`kyo install-hooks` (it writes .claude/settings.json: ask the operator first)")
    )
    path = code_root / CLAUDE_SETTINGS_REL
    if not path.is_file():
        return Item("hooks", "missing", f"no {CLAUDE_SETTINGS_REL}", fix)
    drift = hook_drift(code_root)
    try:
        import json

        settings = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return Item("hooks", "unreadable", drift[0] if drift else f"{path} is not JSON", fix)
    hooks_dir = repo_hooks_source(code_root)
    events = sorted({event for event, _ in HOOK_INSTALL_TARGETS})
    ours = set()
    if isinstance(settings, dict):
        for event in events:
            ours |= installed_elements(settings, event) & packaged_elements(event, hooks_dir)
    if not ours:
        return Item("hooks", "missing", f"{CLAUDE_SETTINGS_REL} runs no Kyosei hook", fix)
    if drift:
        more = f" (and {len(drift) - 1} more)" if len(drift) > 1 else ""
        return Item("hooks", "behind", drift[0] + more, fix)
    return Item("hooks", "present", f"{CLAUDE_SETTINGS_REL} is current with this build")


def _harness_item(code_root: Path) -> Item | None:
    """KYO-596: the harness item for a project on the Claude Code plugin, or ``None`` for
    one on the copies, which reads the hooks, skill and CLAUDE.md-section items instead.

    :func:`kyosei.doctor_install.harness_state` decides both, so this item and `kyo
    doctor`'s ``harness`` check cannot disagree. It may run `claude plugin list --json`,
    which reads Claude Code's state; only when a settings file enables the plugin.
    """
    from .doctor_install import harness_state

    state = harness_state(code_root)
    if not state.on_plugin:
        return None
    return Item("harness", state.status or "missing", state.detail, state.fix)


def _skill_items(code_root: Path, source_repo: bool) -> list[Item]:
    from .install_skill import (
        CLAUDE_MD,
        CLAUDE_MD_BEGIN,
        SKILL_ASSETS,
        claude_md_carries_init_section,
        claude_md_drift,
        skill_drift,
    )

    fix = SOURCE_REPO_FIX if source_repo else "`kyo install-skill`"
    installed = any((code_root / asset.target).is_file() for asset in SKILL_ASSETS)
    if not installed:
        skill = Item("skill", "missing", "no .claude/skills/kyosei/ in the code repository", fix)
    else:
        drift = skill_drift(code_root)
        skill = (
            Item("skill", "behind", "; ".join(drift), fix)
            if drift
            else Item("skill", "present", "current with this build")
        )
    md_path = code_root / CLAUDE_MD
    text = md_path.read_text(encoding="utf-8") if md_path.is_file() else None
    marked = text is not None and CLAUDE_MD_BEGIN in text
    if source_repo:
        section = Item(
            "CLAUDE.md section",
            "not-needed",
            "this is Kyosei's own source repository; its CLAUDE.md is written by hand and "
            "`kyo install-skill` refuses here",
        )
    elif installed:
        # KYO-508's own check, reused rather than re-derived.
        drift = claude_md_drift(code_root)
        section = (
            Item("CLAUDE.md section", "behind" if marked else "missing", drift[0], fix)
            if drift
            else Item("CLAUDE.md section", "present", f"{CLAUDE_MD} carries the Kyosei section")
        )
    elif claude_md_carries_init_section(text):
        # KYO-558: the section `kyo init` writes, which points a session at this check
        # rather than at a skill that is not there yet.
        section = Item(
            "CLAUDE.md section",
            "present",
            f"{CLAUDE_MD} carries the setup-incomplete section `kyo init` writes, which "
            "points a session here; `kyo install-skill` replaces it with the full one",
            fix,
        )
    elif marked:
        section = Item(
            "CLAUDE.md section",
            "present",
            f"{CLAUDE_MD} carries a Kyosei section, but the skill it points to is missing",
            fix,
        )
    else:
        section = Item(
            "CLAUDE.md section",
            "missing",
            f"{CLAUDE_MD} {'is absent' if text is None else 'has no Kyosei section'}, so a "
            "session is never told this project uses Kyosei; `kyo install-skill` writes it "
            "with the skill",
            fix,
        )
    return [skill, section]


def _procedures_item(layout, source_repo: bool) -> Item:
    from . import procedures
    from .install_procedures import procedures_ledger_drift, procedures_path

    fix = SOURCE_REPO_FIX if source_repo else "`kyo install-procedures`"
    path = procedures_path(layout.store_root)
    if not path.is_file():
        return Item("procedures", "missing", f"no {path}", fix)
    differences, errors = procedures_ledger_drift(layout.store_root, layout.code_root)
    if errors:
        return Item("procedures", "unreadable", "; ".join(errors), fix)
    missing = [d.title for d in differences if d.kind == procedures.LEDGER_MISSING]
    changed = [d.title for d in differences if d.kind == procedures.LEDGER_CHANGED]
    if missing:
        return Item(
            "procedures",
            "behind",
            f"{len(missing)} rule(s) this build ships are not in {path}: "
            + "; ".join(repr(t) for t in missing),
            fix,
        )
    # KYO-515: a rule still on a wording an earlier build shipped is one nobody in the
    # project wrote, and a plain rerun moves it, so it is behind rather than an edit.
    bodies = procedures.rule_bodies(path.read_text(encoding="utf-8")) if changed else {}
    inherited = [
        title
        for title in changed
        if not source_repo
        and procedures.is_superseded_wording(title, bodies.get(title, ""))
    ]
    if inherited:
        return Item(
            "procedures",
            "behind",
            f"{len(inherited)} rule(s) in {path} are still on an earlier packaged wording, "
            "which nobody in the project wrote: "
            + "; ".join(repr(t) for t in inherited),
            fix,
        )
    if changed:
        return Item(
            "procedures",
            "present",
            f"every packaged rule is there; {len(changed)} read differently from this "
            "build's wording, as the project's own edits. "
            "`kyo install-procedures --print` shows them and `--adopt RULE_TITLE` takes "
            "this build's text for one",
        )
    return Item("procedures", "present", "current with this build")


def _same_model(agent_model: str, declared: str) -> bool:
    a, d = agent_model.strip().lower(), declared.strip().lower()
    if a == d:
        return True
    if a in MODEL_ALIASES and a in d:
        return True
    return d in MODEL_ALIASES and d in a


def _agent_types(code_root: Path) -> list[tuple[str, str | None]]:
    """``(file name, model or None)`` for every agent type that is a Kyosei worker:
    one whose definition tells it to read its brief with `kyo show`."""
    directory = code_root / ".claude" / "agents"
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "kyo show" not in text:
            continue
        model = None
        if text.startswith("---"):
            front = text.split("---", 2)
            if len(front) == 3:
                try:
                    meta = yaml.safe_load(front[1]) or {}
                except yaml.YAMLError:
                    meta = {}
                value = meta.get("model") if isinstance(meta, dict) else None
                model = str(value).strip() if value else None
        found.append((path.name, model))
    return found


def _model_items(layout, raw: dict) -> list[Item]:
    config_path = layout.store_root / "kyosei" / "config.yml"
    kinds = _yaml_mapping(layout.store_root / "kyosei" / "kinds.yml", "kinds") or {}
    overrides = {
        name: body["policy"]["worker_model"]
        for name, body in kinds.items()
        if isinstance(body, dict)
        and isinstance(body.get("policy"), dict)
        and isinstance(body["policy"].get("worker_model"), str)
    }
    over = (
        "; kind overrides: " + ", ".join(f"{k}={v}" for k, v in sorted(overrides.items()))
        if overrides
        else ""
    )
    model = raw.get("worker_model")
    model = model.strip() if isinstance(model, str) and model.strip() else None
    if model is None:
        worker = Item(
            "worker_model",
            "missing",
            "unset, so a brief names no model and the harness picks one" + over,
            f"add `worker_model: <model id>` to {config_path} (setup decision D1: "
            "well-specified records to a faster model, reasoning to a stronger one; "
            "`kyo onboard` asks it)",
        )
    else:
        worker = Item("worker_model", "present", model + over)

    types = _agent_types(layout.code_root)
    if not types:
        agents = Item(
            "agent types",
            "not-needed",
            "none in .claude/agents/; workers run as the harness's general-purpose subagent "
            "(setup decision C4)",
        )
    else:
        listing = ", ".join(f"{name}: {m or 'no model:'}" for name, m in types)
        declared = ([model] if model else []) + list(overrides.values())
        wrong = [
            (name, m)
            for name, m in types
            if m
            and m.lower() != "inherit"
            and declared
            and not any(_same_model(m, d) for d in declared)
        ]
        if wrong:
            agents = Item(
                "agent types",
                "disagrees",
                f"{listing}; "
                + ", ".join(f"{name} runs {m}" for name, m in wrong)
                + f", which is neither worker_model ({model or 'unset'}) nor any kind's "
                "policy.worker_model, so a brief and the worker that reads it name "
                "different models",
                "make `model:` in .claude/agents/"
                + ", .claude/agents/".join(name for name, _ in wrong)
                + f" agree with worker_model, or change worker_model in {config_path}",
            )
        else:
            agents = Item("agent types", "present", listing)
    return [worker, agents]


def _setup_items(layout, raw: dict) -> list[Item]:
    config_path = layout.store_root / "kyosei" / "config.yml"
    code = layout.code_root
    items = []
    setup = raw.get("worktree_setup") or []
    if isinstance(setup, list) and setup:
        runs = [e.get("run") if isinstance(e, dict) else e for e in setup]
        items.append(Item("worktree_setup", "present", "; ".join(str(r) for r in runs)))
    else:
        hints = [
            name
            for name in ("Makefile", "pyproject.toml", "uv.lock", "package.json")
            if (code / name).is_file()
        ]
        seen = f" (this repository has {', '.join(hints)})" if hints else ""
        items.append(
            Item(
                "worktree_setup",
                "missing",
                "none declared, so a fresh dispatch worktree has tracked files only and a "
                "worker inherits the orchestrator's environment" + seen,
                f"add a `worktree_setup:` list to {config_path} (setup decision D4)",
            )
        )
    # KYO-519: either spelling of the key (from KYO-513). KYO-563.c5: the item is named
    # by the orchestrator spelling, as every line of onboarding's prose is, and says
    # which spelling it found and that the other is read too. The fix still names the
    # old spelling beside the new, because a kyo older than KYO-519 refuses the new one.
    new, old = "orchestrator_only_commands", "controller_only_commands"
    declared = new if raw.get(new) else old if raw.get(old) else None
    only = raw.get(declared) if declared else []
    if isinstance(only, list) and only:
        items.append(
            Item(
                new,
                "present",
                "; ".join(map(str, only))
                + f" (declared as `{declared}`; this build reads `{new}` and its alias "
                f"`{old}`)",
            )
        )
    else:
        items.append(
            Item(
                new,
                "missing",
                f"none declared under `{new}` or its alias `{old}`, so nothing stops a "
                "worker running the whole suite",
                f"add an `{new}:` list to {config_path} (setup decision D5), or "
                f"`{old}:` where a kyo older than KYO-519 also reads this store, because "
                "that build refuses the new spelling",
            )
        )
    scopes = _yaml_mapping(layout.store_root / "kyosei" / "scopes.yml", "scopes")
    components = [name for name in COMPONENT_DIRS if (code / name).is_dir()]
    if scopes is None:
        items.append(Item("scopes", "unreadable", "kyosei/scopes.yml is not a mapping"))
    elif scopes:
        items.append(Item("scopes", "present", ", ".join(sorted(scopes))))
    elif components:
        items.append(
            Item(
                "scopes",
                "missing",
                f"none declared, in a repository with {', '.join(c + '/' for c in components)}",
                f"declare them in {layout.store_root / 'kyosei' / 'scopes.yml'} "
                "(setup decision D7)",
            )
        )
    else:
        items.append(Item("scopes", "not-needed", "none declared; one component needs none"))
    documents = _yaml_mapping(layout.store_root / "kyosei" / "documents.yml", "documents")
    if documents is None:
        items.append(Item("documents", "unreadable", "kyosei/documents.yml is not a mapping"))
    elif documents:
        items.append(Item("documents", "present", ", ".join(sorted(map(str, documents)))))
    else:
        items.append(
            Item(
                "documents",
                "missing",
                "none declared, so no document section is tied to the code it describes",
                "`kyo docs suggest` (drafts one and writes nothing; setup decision D8)",
            )
        )
    return items


#: KYO-560: which setup decision each project item is the trace of, so an answer
#: recorded under ``onboarding:`` can stand in for the setting it explains the absence
#: of. Only decisions whose answer is otherwise invisible are here; C2 and C3 are never
#: asked, and the machine items (E3, E5) are not a project's to record.
DECISION_OF_LABEL: dict[str, str] = {
    "store remote": "B2",
    "harness": "C1",
    "hooks": "C1",
    "agent types": "C4",
    "worker_model": "D1",
    "worktree_setup": "D4",
    "orchestrator_only_commands": "D5",
    "scopes": "D7",
    "documents": "D8",
}


def answered_items(items: list[Item], raw: dict, config_path: Path) -> list[Item]:
    """KYO-560: ``items`` with each recorded ``onboarding:`` answer applied, and the
    ``onboarding`` item that reads the whole map back appended.

    An item the check found ``missing`` whose decision is recorded becomes ``declined``
    or ``deferred``, with the reason and no fix: the decision was made, and the absence
    is its answer. One found ``present`` contradicts a recorded "no" or "later", so it
    ``disagrees`` and the fix names both ways out. Any other status is left alone. A
    map this build cannot read changes no item, and the ``onboarding`` item says why,
    naming the key (KYO-560.c4).
    """
    from .config import ONBOARDING_KEY, parse_onboarding

    try:
        answers = parse_onboarding(raw.get(ONBOARDING_KEY), config_path)
    except KyoseiError as exc:
        return [*items, Item("onboarding", "unreadable", str(exc))]
    result = []
    for item in items:
        answer = answers.get(DECISION_OF_LABEL.get(item.label, ""))
        if answer is None or not item.answerable or item.status not in ("missing", "present"):
            result.append(item)
            continue
        recorded = f"setup decision {answer.decision} is recorded as {answer.status}"
        if item.status == "present":
            result.append(
                Item(
                    item.label,
                    "disagrees",
                    f"{item.detail}; but {recorded} under {ONBOARDING_KEY}: in {config_path}",
                    f"delete {answer.decision} from {ONBOARDING_KEY}: in {config_path} if the "
                    "decision changed, or undo the setting if it did not",
                )
            )
            continue
        why = answer.reason or "no reason recorded"
        result.append(Item(item.label, answer.status, f"{recorded}: {why}. Found: {item.detail}"))
    if answers:
        listing = "; ".join(
            f"{a.decision} {a.status}" + (f" ({a.reason})" if a.reason else "")
            for a in answers.values()
        )
        result.append(Item("onboarding", "present", listing))
    else:
        result.append(
            Item(
                "onboarding",
                "not-needed",
                f"no declined or deferred decision is recorded under {ONBOARDING_KEY}: in "
                f"{config_path}; one answered no or later is written there",
            )
        )
    return result


def project_items(layout) -> list[Item]:
    """The project half of the readback for one resolved layout. Reads only."""
    from .config import read_raw_config

    if not layout.configured:
        return [
            Item(
                "store",
                "missing",
                f"no Kyosei project at {layout.code_root}",
                "`kyo init` (or `kyo onboard`, which walks through the choices first)",
            )
        ]
    raw = read_raw_config(layout.store_root)
    items, readable = _store_items(layout, raw)
    source_repo = _in_source_repo(layout.code_root)
    items.append(_remote_item(layout))
    prefix = raw.get("project_prefix")
    items.append(
        Item("prefix", "present", str(prefix))
        if isinstance(prefix, str) and prefix.strip()
        else Item("prefix", "unreadable", "kyosei/config.yml names no project_prefix")
    )
    harness = _harness_item(layout.code_root)
    if harness is not None:
        items.extend([harness, _procedures_item(layout, source_repo)])
    else:
        items.append(_hooks_item(layout.code_root, source_repo))
        skill, section = _skill_items(layout.code_root, source_repo)
        items.extend([skill, _procedures_item(layout, source_repo), section])
    items.extend(_model_items(layout, raw))
    items.extend(_setup_items(layout, raw))
    items = answered_items(items, raw, layout.store_root / "kyosei" / "config.yml")
    items.append(_doctor_item(layout, readable))
    return items


def _age(since: str, until: str) -> str:
    """``since`` to ``until`` (two :func:`kyosei.clock.now` stamps) in the largest whole
    unit, for a readback line. A clock that moved backwards reads as just now."""
    from datetime import datetime

    start = datetime.fromisoformat(since.replace("Z", "+00:00"))
    end = datetime.fromisoformat(until.replace("Z", "+00:00"))
    seconds = max(int((end - start).total_seconds()), 0)
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return "just now"


def _doctor_item(layout, readable: bool) -> Item:
    """KYO-564: the last `kyo doctor` result, read from the file every doctor run leaves
    beside the database (:func:`kyosei.doctor.record_last_result`), never by opening the
    database or running the checks. ``not-run`` only when no result is recorded."""
    from . import clock
    from .buildinfo import build_identity
    from .doctor import last_result_path, read_last_result

    unreadable_config = "" if readable else "; this build cannot read this store's config"
    path = last_result_path(layout.store_root)
    result = read_last_result(layout.store_root)
    if result is None:
        if path.exists():
            return Item(
                "kyo doctor",
                "unreadable",
                f"the recorded result at {path} is not one this build can read"
                + unreadable_config,
                "`kyo doctor` (records a fresh result)",
            )
        return Item(
            "kyo doctor",
            "not-run",
            "no result recorded: --check never opens the database, and reads the result "
            "`kyo doctor` leaves beside it" + unreadable_config,
            "`kyo doctor`",
        )
    commit = result.get("commit")
    ours = build_identity(with_dirty=False).short_commit
    if not commit:
        build = "an unknown build"
    elif commit == ours:
        build = f"this build ({commit})"
    else:
        build = f"build {commit}, not this one ({ours or 'unknown'})"
    form = "kyo doctor --hook" if result.get("hook") else "kyo doctor"
    when = f"`{form}` {_age(result['at'], clock.now())} ({result['at']}), by {build}"
    failing = [str(name) for name in result["failing"]]
    if not failing:
        return Item("kyo doctor", "present", f"clean -- {when}" + unreadable_config)
    return Item(
        "kyo doctor",
        "failing",
        f"{len(failing)} failing check(s): {', '.join(failing)} -- {when}" + unreadable_config,
        "`kyo doctor` (prints each finding; the result here updates when it next runs)",
    )


#: KYO-558.c3: the statuses `kyo init` lists as setup still to do. ``not-run`` (doctor)
#: and ``not-needed`` are not work, and ``present`` is done.
INCOMPLETE_STATUSES = ("missing", "behind", "copied", "doubled")


def setup_incomplete(layout) -> tuple[list[str], list[Item]]:
    """KYO-558.c3/c10: the block `kyo init` ends with -- every project item `kyo onboard
    --check` reads as missing or behind, each with its fix, then `kyo onboard` -- and
    those items. Both empty when nothing is missing or behind. Reads only."""
    items = [item for item in project_items(layout) if item.status in INCOMPLETE_STATUSES]
    if not items:
        return [], []
    lines = [
        f"Setup incomplete: `kyo onboard --check` reads {len(items)} project item(s) as "
        "missing or behind:",
        *_render(items),
        "Next: `kyo onboard`, the guided setup: it prints the steps for your agent to "
        "follow, and the choices that are yours are put to you.",
    ]
    return lines, items


def check_targets(ctx: Context, args: Namespace) -> list[tuple[str, object]]:
    """``(heading, layout)`` for each project to check: the one here, or every registered one."""
    from .cmd_projects import read_entries, registry_path
    from .config import LAYOUT_UNCONFIGURED, Layout, resolve_layout

    if not args.all:
        layout = resolve_layout(explicit_root=args.root, required=False)
        return [(str(layout.code_root), layout)]
    targets = []
    for entry in read_entries(registry_path(ctx.environ)):
        root = Path(entry["root"]).expanduser()
        if not root.is_dir():
            missing = Layout(LAYOUT_UNCONFIGURED, store_root=root, code_root=root)
            targets.append((str(root), missing))
            continue
        targets.append((str(root), resolve_layout(explicit_root=root, required=False)))
    return targets


def _render(items: list[Item]) -> list[str]:
    lines = []
    for item in items:
        lines.append(f"  {item.label}: {item.status} -- {item.detail}")
        if item.fix:
            lines.append(f"      fix: {item.fix}")
    return lines


def cmd_onboard(ctx: Context, args: Namespace) -> Output:
    """``kyo onboard``: print the setup prompt. ``--check``: read projects back, write nothing."""
    if args.all and not args.check:
        args.check = True
    if not args.check:
        text = render_prompt()
        return Output([text.rstrip("\n")], {"prompt": text, "build": build_stamp()})

    from .cmd_projects import registry_path

    machine = machine_items(ctx.environ)
    lines = [
        "kyo onboard --check: read only, nothing was written. Statuses: "
        + ", ".join(STATUSES)
        + ".",
        "machine",
        *_render(machine),
    ]
    projects = []
    targets = check_targets(ctx, args)
    if args.all and not targets:
        lines.append(f"no projects are registered in {registry_path(ctx.environ)}")
    for heading, layout in targets:
        items = project_items(layout)
        projects.append({"root": heading, "items": [item.to_json() for item in items]})
        lines.append("")
        lines.append(f"project {heading}")
        lines.extend(_render(items))
    data = {
        "statuses": STATUSES,
        "machine": [item.to_json() for item in machine],
        "projects": projects,
    }
    return Output(lines, data)
