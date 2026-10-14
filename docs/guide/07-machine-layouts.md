# Machine layouts

After this page you will be able to choose where Kyosei runs: on one machine with the CLI
alone, on one machine with the desktop app, or on a dev box you reach from a laptop.

## Which layout?

([E1](setup-decisions.md#e1-which-layout-ask-this-before-e2-to-e5)) Only the CLI is
required. Everything else on this page is optional, and macOS is the platform that has been
measured.

| Layout | What runs where | You need |
|---|---|---|
| **CLI only** | One machine holds the repositories, the stores, `kyo`, and Claude Code in your own terminal. | Nothing more. |
| **One machine with the app** | The same, plus `kyo serve` started at login and the desktop app reading through it. | `kyo serve`, the app. |
| **Dev box and light client** | The dev box holds every repository and store and runs `kyo`, `kyo serve` and the orchestrator sessions under tmux. The laptop runs the app (or a browser, or `ssh`) and talks to a session through claude.ai's Remote Control. | Everything below, and a private network between the two machines, such as Tailscale. |

Start with CLI only. Add the app when you want to see the board rather than ask for it, and
the dev box when you want sessions to keep running while your laptop is closed.

In every layout the store stays on the machine whose agents write to it. In the dev-box
layout the laptop holds no store and runs no orchestrator; it only views and talks.

## One `kyo` per machine

([E2](setup-decisions.md#e2-one-kyo-per-machine-and-path)) `uv tool install kyosei`
puts `kyo` in `~/.local/bin`. Every project's hooks, every terminal and the app run that one
copy, so upgrading it upgrades every project on the machine at once. Never install it in
editable mode: every project would then run whatever is in a checkout, unfinished edits
included.

`~/kyosei/` is the machine's Kyosei home: the list of projects (`projects.json`), machine
settings (`settings.yml`), the split stores, and each store's worker worktrees.

The usual trap is PATH. `~/.local/bin` is on PATH in a login shell, but not in a plain
`ssh host kyo ...`, nor in anything started at login without help. Run remote commands
through a login shell: `ssh host "zsh -lc 'kyo ...'"`.

## `kyo serve`

([E3](setup-decisions.md#e3-kyo-serve)) A small HTTP server on loopback (port 47370) that
serves every project in `projects.json` to a viewer. It serves what the CLI's `--json`
output shows, and accepts only two kinds of write: files under a project's `ideas/`, and its
session scratchpad. It starts no process and writes no record.

- Try it with `kyo serve`, or start it at every login with `kyo serve --install --apply`.
- Restart it after every upgrade (`kyo serve --restart`); a running server keeps serving the
  build it started as, and `kyo serve --status` says when it is stale.
- It has no password. Every account on the machine can reach it.
- To reach it from another machine, put it behind Tailscale Serve
  (`kyo serve --install --allow-host <machine>.<tailnet>.ts.net --apply`, then
  `tailscale serve --bg 47370`) and restrict access with a tailnet rule. Never use
  `tailscale funnel` on this port: that publishes every project to the internet.

## The desktop app

([E4](setup-decisions.md#e4-the-desktop-app-macos)) A macOS viewer of `kyo serve`: a board,
record pages, a terminal pane per project and a list of each project's Claude Code
sessions. It ships no `kyo` and no server of its own, and says so when `kyo serve` is not
answering. To use it as a light client, choose **Connect to a dev box…** in its project
switcher and give it the dev box's tailnet address and an SSH host.

The app registers `kyosei://` links, so an orchestrator can write each record id as a link
that opens the record. That helps only if you read the orchestrator where the app is
installed; elsewhere the link is noise around an id, so the setup agent asks.

## Orchestrator sessions under tmux

([E5](setup-decisions.md#e5-orchestrator-sessions-under-tmux-kyo-orchestrator-macos)) On a dev
box, `kyo orchestrator start <project>` starts an orchestrator session in a detached tmux
session named after the project's prefix, which keeps running when you disconnect.
`kyo orchestrator list`, `attach` and `stop` do the rest. (`kyo controller` is the same
command under its name before KYO-519, and still works.)

These sessions run on a dedicated tmux server, `tmux -L kyosei`, which
`kyo orchestrator --install --apply` starts at login. The reason is macOS: Claude Code's
credentials are in the login keychain, which a process started straight from an SSH login
cannot read. Over SSH, `kyo orchestrator start` refuses rather than start a session that
cannot authenticate. Stopping that tmux server ends every orchestrator session on the
machine.

`controller_command` in `~/kyosei/settings.yml` sets what a session runs; the default is
`claude`. Turn on Remote Login (System Settings, General, Sharing) for any of the SSH paths.

Back to [the guide's contents](README.md).
