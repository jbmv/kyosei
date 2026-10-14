# Working on Kyosei

Kyosei (共生) is a provenance-first idea tracker, task board and documentation-governance
CLI for human–agent development. This file is for an agent or a person building, testing or
contributing to Kyosei itself. To *use* Kyosei in another project, install it
(`uv tool install kyosei`) and run `kyo onboard` there instead; the operator's guide is
[`docs/guide/`](docs/guide/README.md).

## Commands

```
make venv      # provision .venv and run nothing -- what a fresh dispatch worktree needs (KYO-297)
make test      # pytest via a uv-provisioned 3.12 venv (host python may be older)
make lint      # ruff on src and tests
make test LOG=/tmp/t.log   # same run, whole output to the file, last lines and the
                           # suite's OWN exit status on the terminal -- `| tail` reports
                           # tail's status and drops the log on interruption (KYO-193)
.venv/bin/python -m kyosei --help   # the CLI (`kyosei` / `kyo` when installed via pipx/uv)
```

**Provision with `make venv` before you run anything.** `.venv/` is gitignored, so a fresh
clone or worktree (`git worktree add` checks out tracked files and nothing else) has none.
Every `make` target depends on a stamp target that provisions it with the
`VIRTUAL_ENV=$(VENV)` prefix, which takes under a second cold. Do not install by hand with
`uv pip install`: without that prefix it follows any inherited `VIRTUAL_ENV`, and if you
were started from another checkout's environment it installs your source over that
environment while appearing to succeed. The same inheritance means bare `python`,
`pytest` and `kyo` resolve to the other checkout's tooling until you provision, so tests
run before that step test a different tree. `kyo dispatch` states a project's
`worktree_setup` commands in every brief (KYO-143).

If you work in a Kyosei dispatch worktree, run the tests that cover your change with
`.venv/bin/python -m pytest <files>` and leave `make test`, which runs the whole suite
(about four minutes across all cores), to whoever merges the work (KYO-297).

## This repository operates itself with Kyosei

Kyosei tracks its own work in its own store, in the split layout: the store is a separate,
private repository on the maintainer's machine, and this repository carries no part of it
(its `.kyosei` link file is gitignored). A contributor needs none of it; the tests build
their own temporary projects.

- **Dispatched workers never write to the store** — `kyo check-branch` enforces it and runs
  when a worker stops.
- **Proposals and other planning documents live in the store repository, never in this
  one**; a test fails if anything is tracked under `docs/proposals/`.
- Claude Code sessions here take the `kyosei` skill (`kyosei:kyosei`) and the hooks from the
  Kyosei plugin of the installed build, as consumer projects do (KYO-610); this repository
  carries no copy of either. The plugin's source is `src/kyosei/harness/claude-code/plugin/`
  and reaches sessions after `make install-tool`. If an installed build breaks a hook, run
  `claude plugin disable kyosei@kyosei` in this repository, then install a fixed build with
  `make install-tool`.
- Any schema change bumps `schema_version` through the compat machinery
  (`tests/test_schema_compat.py` fails otherwise) and adds rebuild defaults for the prior
  export format.
- Every stored field ships with a consumer in the same commit; every mechanism ships with
  a test that fails if the mechanism is removed; every error message naming a remedy has a
  test proving the remedy clears the error.

## Where things live

- The operator's guide, for someone running a project with Kyosei: `docs/guide/`, starting
  at `docs/guide/README.md`. Its decisions list, `docs/guide/setup-decisions.md`, is what
  `kyo onboard`'s prompt (`src/kyosei/defaults/onboard.md`) is checked against.
- Operating reference for consumers: `README.md` and `docs/reference/topics/`; the command
  reference `docs/reference/cli.md` is generated from the CLI.
- Design record (laws, architecture, reasoning): `docs/design/charter.md` — read it before
  changing the schema, the kind registry, or any law-adjacent behavior. Its laws were drawn
  from an operational log of earlier agent-run projects, which is not published.
- The desktop app: `apps/desktop/`, a separate Electron project with its own README and
  checks.
- `llms.txt` at the repository root routes a visiting agent to the right one of these.

## Record citations

Kyosei's own development is tracked with Kyosei, and source comments, tests and documents
cite its records as `KYO-n`: the id of a record in that tracker, with a suffix such as
`.c2` or `.d1` naming a criterion or a dispatch within the record. `kyo changelog --all`
lists the completed records with their titles. A citation marks where a behaviour came
from; the surrounding text should still state the reason on its own.
