# Kyosei reference

The full reference for operating Kyosei, grouped by subject. The repository's README is
a one-screen introduction that links here. For a walkthrough rather than a reference,
start with the guide in [`docs/guide/`](../../guide/).

| Page | What it covers | Sections |
| --- | --- | --- |
| [install.md](install.md) | Installing `kyo`, telling which copy is running, upgrading a project to a newer build | Install; Upgrading |
| [operating.md](operating.md) | The day-to-day workflow of a consumer project and the literal commands for it | Operating a Kyosei project; Quick start |
| [store.md](store.md) | Where the store lives, what `kyo init` creates, the database and its git-canonical export, reading the store back | Where the store lives; What `kyo init` creates; The store; Reading the store |
| [registries.md](registries.md) | The kind, scope, status and gate-tag registries, and saved views | The kind registry; The scope registry; The status registry, and why contracts key off roles; The gate-tag registry; Saved views |
| [dispatch.md](dispatch.md) | Dispatching work to workers, ingesting reports, and sweep, next and the session briefing | The dispatch loop; Sweep, next, and the session briefing |
| [docs-governance.md](docs-governance.md) | Layer 3: `governs:` sections, `kyo docs check`, documentation obligations | Layer 3: the docs-SOT skeleton validator |
| [serve-and-desktop.md](serve-and-desktop.md) | `kyo serve`, `kyo orchestrator`, reaching a machine from another one, the desktop app | Starting an orchestrator from another machine; Serving a store; Reaching it from another machine; The desktop app |
| [rules-and-exit-codes.md](rules-and-exit-codes.md) | The rules with machinery behind them, and what each exit code means | Rules with machinery behind them; Exit codes |

Elsewhere in the repository:

- [`docs/reference/cli.md`](../cli.md) — every command and flag, generated from the parser.
- `kyo changelog` — what each build changed, as packaged with the build you run.
- [`docs/design/charter.md`](../../design/charter.md) — the design record: the laws, the
  architecture, and the reasoning behind both.
