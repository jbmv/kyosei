# Machine layouts

After this page you will be able to choose where Kyosei runs: on one machine with the CLI
alone, on one machine with the Mac app, or on a dev box you reach from a laptop.

## Which layout?

([E1](setup-decisions.md#e1-which-layout-ask-this-before-e2-to-e5)) Only the CLI is
required. Everything else on this page is optional, and macOS is the platform that has been
measured.

| Layout | What runs where | You need |
|---|---|---|
| **CLI only** | One machine holds the repositories, the stores, `kyo`, and Claude Code in your own terminal. | Nothing more. |
| **One machine with the app** | The same, plus `kyo serve` started at login and the Mac app reading through it. | `kyo serve`, the app. |
| **Dev box and light client** | The dev box holds every repository and store and runs `kyo`, `kyo serve` and the orchestrator sessions under tmux. The laptop reaches them through the app, a browser or `ssh`, and talks to a session through the app or claude.ai's Remote Control. | Everything below, and a private network between the two machines, such as Tailscale. |

Start with CLI only. Add the app when you want to see the board rather than ask for it, and
the dev box when you want sessions to keep running while your laptop is closed.

In every layout the store stays on the machine whose agents write to it. In the dev-box
layout the laptop holds no store and runs no orchestrator; it only views and talks.

## One `kyo` per machine

([E2](setup-decisions.md#e2-one-kyo-per-machine-and-path)) `uv tool install kyosei`
puts `kyo` in `~/.local/bin`. Every project's hooks, every terminal and `kyo serve` run that one
copy, so upgrading it upgrades every project on the machine at once. Never install it in
editable mode: every project would then run whatever is in a checkout, unfinished edits
included.

`~/kyosei/` is the machine's Kyosei home: the list of projects (`projects.json`), machine
settings (`settings.yml`), the split stores, and each store's worker worktrees.

The most common problem is PATH. `~/.local/bin` is on PATH in a login shell, but not in a plain
`ssh host kyo ...`, nor in anything started at login without help. Run remote commands
through a login shell: `ssh host "zsh -lc 'kyo ...'"`.

## `kyo serve`

([E3](setup-decisions.md#e3-kyo-serve)) A small HTTP server on loopback (port 47370) that
serves every project in `projects.json` to a viewer.

- **Without a device token**, a viewer reads what the CLI's `--json` output shows and the
  text of governed documents, and writes two things: files under a project's `ideas/`, and
  its session scratchpad.
- **With a device token**, issued on the machine by `kyo devices issue <name>
  --permission <p>`, a device can also read a project's Claude Code sessions (any token),
  send input to a session and keep drafts and a task list for it (`send`), file your notes
  in the store (`notes`), start an orchestrator session (`start`), type into a session's
  terminal (`terminal`), read a project's files (`files`) and change the editable settings
  (`config`). Sending and the terminal also
  need `serve_send: true` in `~/kyosei/settings.yml`, and are off until you add it.
- No viewer changes a record. For a device with a token the server files your
  notes in the store and, where the token holds `config`, changes an editable setting in the
  store's `kyosei/config.yml`.

The reference lists
[which routes need a device token](../reference/topics/serve-and-desktop.md#which-routes-need-a-device-token).

- Try it with `kyo serve`, or start it at every login with `kyo serve --install --apply`.
- Restart it after every upgrade (`kyo serve --restart`); a running server keeps serving the
  build it started as, and `kyo serve --status` says when it is stale.
- The routes that need no token have no password. Every account on the machine can reach
  them, and so can read your records and write your `ideas/`. `kyo sessions --json`, which
  lists a project's sessions and their transcript paths, is one of those routes.
- To reach it from another machine, put it behind Tailscale Serve
  (`kyo serve --install --allow-host <machine>.<tailnet>.ts.net --apply`, then
  `tailscale serve --bg 47370`) and restrict access with a tailnet rule. Never use
  `tailscale funnel` on this port: that publishes every project's records to the internet.

## The Mac app

([E4](setup-decisions.md#e4-the-mac-app-and-record-links)) The repository holds a native Mac
app under `apps/apple/`, a client of `kyo serve` built from source for the maintainer's own
devices and the current macOS; [its README](../../apps/apple/README.md) describes it and how
it is built. It shows a project's Claude Code sessions as rendered conversation and sends
prompts to them, and beside the conversation it shows the project's records, files and
configuration and a terminal on the session's pane. It ships no `kyo` and no server of its
own. To use it as a light client, name the dev box's server in its Settings, with a device
token issued there.

A `kyosei://` link in a conversation the app shows opens the record in the app, so an
orchestrator can write each record id as such a link. That helps only if you read the
orchestrator in the app; elsewhere the link is noise around an id, so the setup agent asks.

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

`orchestrator_command` in `~/kyosei/settings.yml` sets what a session runs; the default is
`claude`. (`controller_command` is the same key under its earlier name, and is still
read.) Turn on Remote Login (System Settings, General, Sharing) for any of the SSH paths.

Back to [the guide's contents](README.md).
