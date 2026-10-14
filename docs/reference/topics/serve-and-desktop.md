# Serving, remote orchestrators and the desktop app

`kyo serve`, `kyo orchestrator`, reaching a machine from another one, and the desktop app that reads every project through `kyo serve`.

### Starting an orchestrator from another machine: `kyo orchestrator` (KYO-458)

`kyo orchestrator start <project>` starts a detached tmux session, named after the
project's prefix, that runs the machine's orchestrator command in the project's
directory, and returns at once. `kyo orchestrator list` shows each registered
project's session, whether claude is alive in it, and the command it was started
with, and `kyo orchestrator stop <project>` ends it. A project is named as it
appears in `$KYOSEI_HOME/projects.json` (its name or its prefix), or by its
directory.

`kyo orchestrator attach <project>` puts your terminal in that session (KYO-474). It
replaces itself with `tmux -L kyosei attach -t =<prefix>` — the dedicated server, and
the exact name, so you never have to remember either — and with no server or no session
it says which, naming `kyo orchestrator start` (or, over SSH with no server,
`kyo orchestrator --install --apply`). From another machine:

```sh
ssh -t devbox "zsh -lc 'kyo orchestrator attach KYO'"
```

`-t` gives tmux a terminal to draw on; `zsh -lc` is for the PATH (below). Attaching
over SSH does not change claude's credentials — the session was spawned by the
server the desktop login started, and your SSH client only views it. Closing the
terminal or losing the connection detaches; the session keeps running. Plain `tmux
ls` over SSH finds nothing, because it reads the default server, not `-L kyosei`.

**Claude Code's "trust this folder?" prompt** (KYO-567). In a folder Claude Code has
never trusted — a project made with `git init` and `kyo init` in a terminal, or by New
Project… — the claude a start runs stops on that prompt before it registers with Remote
Control, so claude.ai never lists the session. `kyo orchestrator start` reads the new
session's screen for a few seconds and, when the prompt is there, reports
`waiting-for-trust` (`"state"` and `"trust_folder"` in `--json`) naming the folder;
`kyo orchestrator list` reads it the same way for every running session. Answer it
yourself, in `kyo orchestrator attach <project>` or the desktop app's terminal tab:
Kyosei never answers it and never pre-trusts a folder by writing `~/.claude.json`. When Kyosei does not recognise the prompt (its wording is Claude Code's),
start still says the session has not registered with Remote Control yet and where to
look.

**The command is a machine setting: `controller_command` in
`$KYOSEI_HOME/settings.yml`** (`~/kyosei/settings.yml` by default; KYO-462). It is
a list of arguments, and the literal `{prefix}` in any of them is replaced by the
project's prefix. With no file, or no key, the command is `claude` with no
arguments. To have claude.ai list each session under its project's prefix, and
give it Chrome, write:

```yaml
# ~/kyosei/settings.yml
controller_command: ["claude", "--chrome", "--remote-control", "{prefix}"]
```

Since KYO-519 the key is also spelled `orchestrator_command`, and this build reads
either; both with different values is refused by name. Rename it by hand on a machine
only once that machine's `kyo` is at least KYO-519, because an older one refuses the new
spelling and `kyo orchestrator start` fails there. Nothing writes this file.

The setting belongs to the machine, so it is never read from or written to a
project's `kyosei/config.yml`. An unknown key there is refused by an older
installed build, which blinds every hook in that project (KYO-335). A malformed
`settings.yml` is refused by name, with the value that works. That covers a file
that is not YAML, a `controller_command` that is not a non-empty list of strings,
and a key this build does not know. Fix the file, or remove it to run `claude`
alone. A session already running keeps the command it was started with. The new
value applies from the next `start`.

Every session lives on one dedicated tmux server, `tmux -L kyosei`, and the
program is `zsh -lc 'exec <controller_command>'`. Both choices come from measurement on
macOS (KYO-458.m1). A process started directly in an SSH login cannot read the
login keychain, where claude keeps its credentials. A session created over SSH
inside a server that was already running from the desktop login can read it,
because the server spawns the session. Such a session still has a bare PATH, so
the login shell builds your PATH before claude starts.

On macOS, run this once in a terminal on the machine itself:

```sh
kyo orchestrator --install            # prints the LaunchAgent and what --apply runs
kyo orchestrator --install --apply    # writes ~/Library/LaunchAgents/dev.kyosei.controller-tmux.plist
                                    # and loads it in gui/<uid>: tmux -L kyosei runs from login
```

Over SSH, `kyo orchestrator start` refuses when that server is not running rather
than starting one in the SSH context. The refusal names `--install`. `kyo
orchestrator --uninstall` is a dry run; `--uninstall --apply` boots the agent out,
which ends its server and every orchestrator session in it, and removes the file.

**The SSH path needs two things from you.**

- **macOS needs Remote Login turned on** (System Settings → General → Sharing →
  Remote Login) on the machine that holds the project, or `ssh` has nothing to
  connect to.
- **A non-interactive `ssh host kyo …` does not read your login profile**, so
  `~/.local/bin` (where `kyo` and `claude` usually live) is not on its PATH and the
  command is "not found". Use a login shell, `ssh host "zsh -lc 'kyo orchestrator
  start KYO'"`, or the absolute path, `ssh host ~/.local/bin/kyo orchestrator start
  KYO`. Errors come back as the SSH command's stderr.

### Serving a store: `kyo serve` (KYO-448)

`kyo serve` serves every project this machine knows to any viewer, over plain HTTP
on loopback. The projects are the ones `kyo projects list` shows
(`$KYOSEI_HOME/projects.json`), read afresh on every request. `kyo init` adds the
project it creates, and `kyo projects add|remove` edits the list. A viewer can read
everything the CLI's `--json` reads. It can write exactly two things in each project:
files under its `ideas/`, and its session scratchpad. Nothing else is writable: no
record, no promotion, no export, and the server starts no process. One
module holds that line, `src/kyosei/serve_files.py`. Beside the command reads, it serves
one more thing read-only: a governed document's text, at
`/v1/projects/<name>/docs/<path>`, for a file `kyo docs check` lists and no other
(KYO-467) -- a source file, `kyosei/` or a `..` is refused.

**In the foreground**, for a session or a trial:

```sh
kyo serve                 # http://127.0.0.1:47370; one line per request on stderr
kyo serve --port 47400    # another port (0 lets the system pick one)
```

Ctrl-C (SIGINT) or SIGTERM stops it. Before it binds, it asks its port who is there.
If another `kyo serve` answers, it refuses and names that server's pid and build. If
something else holds the port, it says so and names `--port`.

**At login, as a per-user agent (KYO-457).** `--install` prints the agent and changes
nothing; `--apply` writes it and starts it. Run it in a terminal on the machine itself,
in the desktop login: the launchd agent goes into `gui/<uid>`, and a refusal from
`launchctl` says to run it again from there.

```sh
kyo serve --install                  # dry run: the plist (or unit) and the commands
kyo serve --install --apply          # macOS: ~/Library/LaunchAgents/dev.kyosei.serve.plist
                                     # Linux: ~/.config/systemd/user/kyosei-serve.service
kyo serve --status                   # the agent, what answers on the port, and whether it is stale
kyo serve --restart                  # launchctl kickstart -k / systemctl --user restart
kyo serve --uninstall --apply        # stop it and remove the file (without --apply: a dry run)
```

The agent runs the absolute path of the `kyo` that ran `--install`, with the same
`--port`, `--bind`, `--allow-host` and `--allow-origin`. It carries `KYOSEI_HOME` (when
set) and the installing shell's `PATH`. On macOS its stderr goes to
`~/Library/Logs/kyosei-serve.log`. To change a flag, run `--install --apply` again with
the new flags. Windows is not supported, and `--install` there says so.

**Restart it after every upgrade.** A running server keeps serving the build it started
as. An upgrade replaces the `kyo` on disk under it: `make install-tool`, `uv tool
install --force .` or `uv tool upgrade kyosei` (the desktop app ships no `kyo`
(KYO-450), so swapping the app moves nothing here). Nothing restarts the server for you. `GET /v1/version`
reports `stale: true`, with both builds, from then on, and `kyo serve --status` prints
the same line and the remedy:

```sh
kyo serve --restart       # the installed agent
                          # (a foreground kyo serve: Ctrl-C and run it again)
```

**Loopback only, with no override.** `kyo serve` binds `127.0.0.1` (or `::1` with
`--bind ::1`) and refuses any other address. It has no TLS, no password and no token,
so a LAN bind would make every device on the LAN a reader of every project and a writer
of every `ideas/`. Loopback does not stop a web page in your own browser, so three
checks stand in front of every route:

- **Host.** A request whose `Host` is not `127.0.0.1`, `localhost`, `[::1]` or a name
  given with `--allow-host` is refused (403), because it may be a page reaching loopback
  through DNS rebinding. The refusal names the `--allow-host` that would admit it.
- **Origin.** A request that carries an `Origin` is refused (403) unless that origin
  was given with `--allow-origin`, so a site you visit cannot read your projects. A
  non-browser client sends no `Origin`. A browser viewer you trust is admitted by
  naming its origin, for example `--allow-origin http://localhost:5173`.
- **JSON posts.** A `POST` must carry `Content-Type: application/json`, which a
  cross-site form cannot send. `PUT` and `DELETE` cannot come from a form at all.

**Other accounts on this machine can reach it.** A loopback port is open to every
user logged in to the machine, not only to you. Any process under another account can
read every project you serve and write every `ideas/` and scratchpad, and those writes
run as you. On a single-user Mac that is nobody else. On a machine other people log in
to, it is them too. Kyosei states this rather than engineering around it.

### Reaching it from another machine: Tailscale Serve and SSH (KYO-441)

The network is Tailscale, and it replaces a VPN. `kyo serve` stays on loopback.
Tailscale Serve proxies the tailnet to it, with a certificate Tailscale provisions,
so the other machine trusts it through its own trust store. Anything that changes the
machine goes over SSH on the same tailnet: `kyo init`, `kyo projects add|remove`,
`kyo orchestrator start|stop`. The placeholders below are `<devbox>` for the machine
that holds the projects and `<tailnet>` for your tailnet's name.

**Once, in Tailscale's admin console:** under **DNS**, turn on **MagicDNS** and
**HTTPS Certificates**. `tailscale serve` does not work until HTTPS Certificates is
enabled. Before that, the machine has no certificate domain.

**On every machine:** install Tailscale from tailscale.com and sign in with the same
account. On macOS the CLI is inside the app, not on PATH:

```sh
alias tailscale=/Applications/Tailscale.app/Contents/MacOS/Tailscale   # 1.102.4 measured
```

On `<devbox>` itself, set Tailscale to launch at login, disable key expiry for it in
the admin console (**Machines**), and turn off automatic sleep, so it stays reachable.

**On `<devbox>`, from a terminal in the desktop login:**

```sh
kyo serve --install --allow-host <devbox>.<tailnet>.ts.net --apply
tailscale serve --bg 47370
tailscale serve status    # https://<devbox>.<tailnet>.ts.net (tailnet only) -> http://127.0.0.1:47370
```

`--allow-host` admits the name the requests arrive under, and the agent carries it from
then on. From any machine on the tailnet:

```sh
curl https://<devbox>.<tailnet>.ts.net/v1/version
curl https://<devbox>.<tailnet>.ts.net/v1/projects
```

**Never run `tailscale funnel` on this port. Funnel publishes it to the whole internet,
and `kyo serve` has no password.** Serve reaches your tailnet only. Funnel is the one
command that turns this recipe into a public, unauthenticated reader of every project.

**Restrict it to yourself with an access rule.** With Serve on, anyone the tailnet's
policy lets reach `<devbox>`'s port 443 can read every served project and write every
`ideas/` and scratchpad. Nothing more: no store write, and no process started. On a
tailnet only you are in, that is your own devices. On a tailnet shared with anyone
else, give only yourself the ports. In the admin console's **Access controls**, name
the machine by its tailnet address (`tailscale ip -4` on `<devbox>`) and write a grant:

```jsonc
{
  "hosts": { "devbox": "<devbox's tailnet address>" },
  "grants": [
    // you, from any of your devices, to kyo serve (443) and SSH (22) on <devbox>
    { "src": ["you@example.com"], "dst": ["devbox"], "ip": ["tcp:443", "tcp:22"] }
  ]
}
```

A grant only restricts when nothing broader admits the same traffic. A new tailnet's
policy starts with an allow-everything rule, and while that rule stays, this grant
changes nothing. Remove it, or narrow it so it does not cover `<devbox>`. `you@example.com` stands
for the login you sign in to Tailscale with.

**SSH, for everything that changes the machine.** SSH over the tailnet is macOS's own
**Remote Login** (System Settings → General → Sharing → Remote Login) on `<devbox>`,
with your existing keys. It is not Tailscale SSH. MagicDNS's short name works:
`ssh <devbox>`. A non-interactive command does not read your login profile, so wrap it
in a login shell ("Starting an orchestrator from another machine", above). A new project,
without the app:

```sh
ssh <devbox> "zsh -lc 'mkdir -p ~/code/<name> && cd ~/code/<name> && kyo init --defaults'"
ssh <devbox> "zsh -lc 'kyo orchestrator start <name>'"
ssh <devbox> "zsh -lc 'kyo orchestrator list'"
```

`kyo init --defaults` makes a split store (`--in-repo` puts it inside the code repository
instead; KYO-573) and registers the project, so the next `/v1/projects` lists it. A bare
`kyo init` in a folder with no store writes nothing and names these routes (KYO-558).
`kyo orchestrator start` needs `kyo orchestrator --install --apply` to have been run once
on `<devbox>` itself (above).

**What was measured, and what was not** (KYO-441, Tailscale 1.102.4 on macOS).
*Measured* from a client off `<devbox>`'s LAN: `GET
/v1/version` and `GET /v1/projects` at `https://<devbox>.<tailnet>.ts.net` answered.
`ssh <devbox> "zsh -lc '… kyo orchestrator list'"` listed the running orchestrator
sessions. *Measured* on `<devbox>` through the tailnet name: a command route answered
200. A request with a foreign `Origin` was refused 403. `http://<devbox's tailnet
address>:47370` was refused, because the server binds loopback only. So the `Host`
that Tailscale Serve forwards passes `--allow-host <devbox>.<tailnet>.ts.net`.

*Not measured:*

- Whether the recipe also works without `--allow-host`.
- The access-rule grant above. The measured tailnet had one user and no rule, so the
  grant is written from Tailscale's policy format, not tested.
- Whether Tailscale Serve adds identity headers such as `Tailscale-User-Login`.
  `kyo serve` reads none.
- Whether `tailscale serve --bg` survives a reboot of `<devbox>`. Check with
  `tailscale serve status` after one.
- `kyo init` over SSH. The SSH step measured was `kyo orchestrator list`.

## The desktop app (`apps/desktop/`)

governs: apps/desktop/src/main/main.ts, apps/desktop/src/main/kyo.ts, apps/desktop/src/shared/protocol.ts

The Kyosei desktop app lives in [`apps/desktop/`](../../../apps/desktop/README.md) — its own
Electron project, with its own dependency manifest and its own checks, under the
`app-desktop` scope in Kyosei's own store. Its checks run from this
directory: `npm --prefix apps/desktop run check` (lint, typecheck, and two suites —
one that launches the app and asserts its window, one that drives the app's seams
with no app at all). `make test` is the Python suite and knows nothing about that
directory, deliberately. That directory's README carries the shell's design
authority and the reasons behind its tooling.

The renderer is v3 (KYO-227, KYO-232): one window shell with a left nav rail, a
collapsible sidebar whose content the rail selects, and one or two columns of
tabs. There are no modes. Everything openable is a tab — the scratchpad, an idea, a
record, a brief, a doc, the terminal, the claude session, the activity view — and
pop-out is "move this tab to its own window". The layout (columns, widths, tabs,
sidebar) is remembered per display configuration, so each set of screens restores
its own. It is worth knowing how the app reaches your project, because it is the
same store this CLI writes to:

- **You choose which project the app is looking at, from inside the app** (KYO-175).
  The project switcher at the foot of the nav rail opens a picker and the projects
  you have opened before; choosing one moves every tab, without an environment
  variable and without a restart. A directory with no project at or above it is refused where you
  choose it, naming what was looked for, rather than being adopted and then shown as
  a broken store. `KYOSEI_PROJECT_ROOT` still wins if it is set — and while it is,
  the switcher says so and stays disabled rather than recording a choice it would
  then ignore. The project you picked is remembered between launches, in the app's
  own data directory and never in any project — together with the kyo serve it was
  read through (KYO-465): every window reopens on the project it last showed, through
  the server it came from, so a project last used on another machine never comes back
  as this machine's project at the same path. Recents are kept per (server, project),
  each row saying where it is served from; the × at a row's end removes it and "Clear
  recent projects" forgets them all. A project only this machine's registry lists has
  no ×: `kyo projects remove` takes it off that list.
- **This machine's projects are listed in one file, `$KYOSEI_HOME/projects.json`**
  (`~/kyosei/projects.json` by default, beside the per-project homes `kyo init` keeps
  there; KYO-379). It is the machine's project **registry**: `{"projects": [{"root":
  "/path/to/a/project"}]}`, one entry per project this machine holds and serves. The app
  and this machine's server share it — the app adds a project to it whenever you open
  one, the switcher offers every project it lists after your recents, and the server
  answers another machine for exactly the projects it lists, re-reading the file when it
  changes, so an edit by hand takes effect on the next request with no restart. Every
  write is a temporary file and a rename, and a file that cannot be parsed is refused by
  name rather than read as empty — fix it or move it aside. The app finds it by asking
  `kyo where --json`, whose `store_home` is that directory. **The project you have open
  and the order of your recents stay per installation**, in the app's own
  `project.json`: a laptop's last-opened project is not the dev box's. The first launch
  that finds recents there folds them into the registry, once.
- **The claude session is a tab** (KYO-186, KYO-232), a `WebContentsView` on a
  persistent partition, opened from the rail and moved to its own window like any
  other tab. It opens on this project's own Remote Control session when the store's
  `kyosei/sessions.json` names one, and on `claude.ai/code` when it does not — and it
  follows a project switch, because that file lives inside the store the switch points at.
  It also follows an orchestrator you start *after* the tab is open (KYO-382): the app
  watches that file, and the tab moves to the new session when the session hook
  writes it — only when the answer changed, so a turn ending never reloads the page
  under you.
  Remote Control itself has no third-party API; nothing here integrates with it,
  the pane renders the first-party web UI.
- **The claude tab starts the orchestrator for you** (KYO-383, KYO-460). A second line
  under the tab's caption says whether this project's orchestrator session is running,
  naming the machine that holds the project, and when it is not it offers **Start**.
  Both are `kyo controller` on that machine (KYO-458), the alias the app still sends so
  that a `kyo` older than KYO-519 answers it: the line is `kyo controller list`,
  and Start is `kyo controller start <root>` — this machine's `kyo` for a local project,
  and `ssh <host> zsh -lc 'kyo controller start …'` for a project opened from a dev box,
  with the dev box's SSH host (its host name unless one is set in the connect dialog).
  Whatever ssh or `kyo` prints on stderr is shown verbatim. What runs in the session is
  that machine's `controller_command` (`$KYOSEI_HOME/settings.yml`, KYO-462); the app
  keeps no command of its own. A session started any other way on this machine — by
  hand, in a terminal or the app's own pane — counts too (KYO-390): the session hook
  records its process, and while it is alive the line says so and Start is not offered.
  While the session is waiting on Claude Code's trust prompt (KYO-567), the line says
  Claude Code is asking whether to trust the folder and offers **Open terminal**, which
  opens the terminal tab on the session, where you answer it.
- **New Project…** (File menu, and the rail's project menu; KYO-460) chooses a machine —
  this one or a dev box the app has connected to — then a folder in an ordinary folder
  picker driven by listing commands over SSH, and runs `mkdir -p`, `kyo init` (which
  registers the project) and, if asked, `kyo controller start` there. It asks where the
  store lives (setup decision B1; KYO-573): a store of its own in the home folder,
  `kyo init --defaults`, selected unless you pick the other, or the store inside the code
  repository, `kyo init --in-repo`, which publishes its prose with the code. Without the app it
  is the same four commands in a terminal: `ssh`, `mkdir`, `kyo init --defaults` (or
  `--in-repo`), `kyo orchestrator start`. macOS needs Remote Login enabled on the machine the commands go to.
- **To open a record a message names, click it in the claude tab** (KYO-263). What is
  clickable is a record id written as a **markdown link with a `kyosei://` target** —
  `[KYO-261](kyosei://KYO/KYO-261)`, which is the form the `/kyosei` skill tells a
  orchestrator to write. claude.ai renders that as an anchor and **strips the
  non-http(s) `href`**, so what reaches you is link-styled text with no target: before
  this, clicking it did nothing at all; now it opens the record here, in the same tab,
  column and window-on-its-project a clicked link anywhere else would. A **bare id, or
  a bare `kyosei://` URL, written as plain text is not clickable** — claude.ai leaves
  it as prose, so there is no element to click — while that same URL pasted into a
  browser's location bar still opens the app, since the scheme is registered. The tab's
  header says this where it is used. This is the one place a script of this app's runs
  inside a page you are logged into, and its boundary is deliberate
  rather than a habit: its own preload file, sandboxed and
  context-isolated, exposing nothing to the page, mutating nothing in it, reading the
  clicked element's text and sending that one string to the app, which drops whatever
  does not resolve.
- **The app runs the `kyo` you install, and ships no Python of its own** (KYO-450). It finds `kyo` on your **login shell's PATH** — the same PATH
  Terminal.app and every hook see, captured once at launch — and nowhere else, so the
  app, its terminal panes, your Claude sessions and your hooks all run one build.
  Install it with `uv tool install kyosei` ("Install" in [install.md](install.md)). With none on that PATH,
  the app says so and names that command; one it finds is judged by its own `kyo
  version --json` against the releases the app reads (`KYO_ACCEPTS`), and too old, too
  new or not Kyosei's is a status-bar chip naming what was found and what the app reads,
  with Check again. Builds before KYO-450 carried a CPython runtime and offered
  Kyosei → **Install Command Line Tools…** to link it onto your PATH (KYO-315). At
  launch the app removes the links that action made, only those pointing into its own
  data directory, and says once that it did ("Desktop builds that carried their own
  `kyo`", in [install.md](install.md)).
- **One resolver decides how the app invokes `kyo`**, from two named sources: the
  working tree of a Kyosei checkout (so an edit under `src/kyosei/` is live with no
  rebuild) and the `kyo` on your login shell's PATH. In a checkout it is the working
  tree, and the status bar says which is in use rather than leaving it to be
  inferred. `npm --prefix apps/desktop run pack` builds the packaged form into
  `apps/desktop/release/`: an application bundle that carries no Python runtime —
  the pack re-reads the finished bundle and refuses one that does (KYO-450). A packed
  app has no checkout above it, so it runs the PATH's `kyo`; a bundle left inside the
  checkout still resolves the working tree first, by the rule above, so copy the
  `.app` out of the repository to exercise it as a user would.

  **A pack signs itself when your keychain can** (KYO-386). With a **Developer ID
  Application** identity in the login keychain, `npm run pack` signs every Mach-O in
  the bundle with the hardened runtime and a secure timestamp, and refuses a bundle that
  `codesign --verify --deep --strict` or its own per-file check does not accept. The
  signature is what keeps the macOS firewall's "accept incoming connections" answer
  from being asked again after every re-pack. Two environment variables steer it, and
  neither holds a secret:

  - `KYOSEI_SIGN_IDENTITY` names the identity (its name, or its SHA-1 from
    `security find-identity -v -p codesigning`) when the keychain's only Developer ID
    one is not the one you want; `-` packs ad-hoc on a machine that could sign.
  - `KYOSEI_NOTARY_PROFILE` names a `notarytool` keychain profile (made once with
    `xcrun notarytool store-credentials <name>`). When it is set, the pack submits the
    signed bundle to Apple, staples the ticket and requires
    `spctl --assess --type execute` to accept it; the submission id and, on a
    rejection, Apple's log land in `apps/desktop/release/`. That is what lets a copy
    moved to another Mac open with a double-click and no `xattr` step. Unset, the
    pack signs and does not notarize, and says so.
  - `KYOSEI_NOTARY_KEY`, `KYOSEI_NOTARY_KEY_ID` and `KYOSEI_NOTARY_ISSUER` notarize
    with an **App Store Connect API key** instead (KYO-399): the path of the
    `AuthKey_<KEYID>.p8` file App Store Connect issued, its key id, and the issuer id.
    **This is the route that works from ssh and from a tmux server** — an orchestrator
    session included — because it needs no keychain. The keychain profile resolves
    only from the GUI login session; from anywhere else `notarytool` answers "No
    Keychain password item found". Keep the key outside every repository, for example in
    `~/.config/notary/`, and run
    `KYOSEI_NOTARY_KEY=<path>/AuthKey_<KEYID>.p8 KYOSEI_NOTARY_KEY_ID=<KEYID>
    KYOSEI_NOTARY_ISSUER=<issuer> npm --prefix apps/desktop run pack`.

  A pack uses one route: setting the profile and any key variable together is refused,
  as is setting only some of the three key variables or naming a `.p8` path that is
  not a file. Each refusal names what is missing, and all of them come before any
  signing starts. `node apps/desktop/build.mjs --notary-plan` prints which route this
  environment names, and the `xcrun notarytool history ...` command that checks it
  without submitting anything. A failed submission names that same command. The plan
  line names the profile, or the key id and the `.p8` path.

  The Apple ID, password and team never pass through the pack: the keychain holds
  them, and the pack only ever names the profile. The API key is passed to
  `notarytool` by path. The pack never opens, copies or prints the file, so it stays
  out of the repository, `apps/desktop/release/` and the bundle. **With no Developer ID identity,
  a pack is ad-hoc exactly as before** and says so in one line — it opens on the
  Mac that built it, and a copy moved elsewhere needs its quarantine removed by hand.
  The first signing pack on a machine may raise a keychain dialog asking whether
  `codesign` may use the key: answer **Always Allow**. The pack asks for it in its
  first second and refuses after two minutes unanswered, naming this, rather than
  waiting inside the build for someone who is not there.
- **The app reads every project through `kyo serve` and opens no store database**
  (KYO-459). The title bar's `project · branch · N records` and the status bar's
  schema version are `kyo`'s own `--json` answers, and every *write* goes through
  `kyo serve`'s write boundary. The app has no second write path to your store — the
  same law that forbids editing `kyosei/export/` by hand.
- **A sidebar list you have left open follows the store** (KYO-310). File, move or
  close a record from the terminal — the app's own pane, or any other — and the
  Records, Notes or Ideas list showing beside it re-reads itself within a second or
  two, keeping your scroll position, your search, your sort, your filters and the
  marks on rows you have open. It is the store's own change that wakes it, not a
  timer: nothing is re-read while nobody writes, a list you are not looking at costs
  nothing, and read-only `kyo` commands in that pane wake nothing at all.
- **The status bar names which side of the `kyosei.db` / `kyosei/export/` seam
  moved**, in the four states this tool distinguishes: current, database ahead,
  export ahead, and cannot tell. The verdict is `kyo`'s own (`kyo list --json`'s
  `db_export_currency`), with its sentence and its remedy in the tooltip; the app
  compares nothing itself. If you have pulled a store repository and not yet run
  `kyo rebuild`, that is where you find out — before you trust the record count
  beside it.
- **The terminal tab is the window onto your project's orchestrator session**
  (KYO-293, KYO-474) — the one `kyo orchestrator start` runs claude in, on its own
  `tmux -L kyosei` server, so a permission prompt can be answered right there. On
  this machine the tab attaches to it directly, on the same socket `kyo orchestrator`
  uses (`KYOSEI_CONTROLLER_SOCKET` included); in a window on a dev box's project it
  runs `ssh -t <host> zsh -lc 'kyo controller attach <prefix>'` with your own SSH, to
  the SSH host Start orchestrator uses. When there is no session the tab says so and
  offers **Start orchestrator**; when ssh, kyo or tmux refuses, it shows their own
  words; when the connection drops it says so and offers **Reconnect**. The tab never
  creates a session and never touches the machine's default tmux server. Closing the
  tab, the window or the app, or losing the connection, only detaches: the session
  and claude keep running. Above the screen is a row of pills, one per session on the
  orchestrator's server (a dev box's from its `kyo controller list`), your project's
  first and set apart; the highlighted pill is the one you are in, and clicking
  another moves this pane to it. Nothing in the app ever ends a session. On a machine
  with no tmux the pane opens bare and says why.

  On this machine the leftmost control gives you a **bare login shell** instead (on a
  dev box it only detaches): your own shell, on
  the environment a new terminal window would give it (KYO-229), with its working
  directory at your project root — and `kyo` typed into either surface is the `kyo` on
  your login PATH, the same one Terminal.app runs: the app puts nothing in front of
  that shell's `PATH` (KYO-450).
  That shell ends when the app quits, and the strip says so.

  The two are **two ptys with two screens**, not one shell being switched. The tmux
  client is a process of its own — the app spawns `tmux` itself rather than typing an
  attach command at a prompt — so a pill click attaches at once even while your bare
  shell is busy in a long command, and that command keeps running, with its own
  screen and scrollback, until you come back to it. If you detach with your own
  prefix key, or the session ends, the pane drops to the bare shell and says which of
  the two happened.
- **The app serves nothing, and reads everything through `kyo serve`** (KYO-459): this
  machine's projects at `http://127.0.0.1:47370`, another machine's at its Tailscale
  Serve address. When nothing answers the app says *kyo serve is not running* and names
  `kyo serve --install --apply` and `kyo serve`, with Check again, and asks again every
  3 s until it answers. A server that stops under an open window is noticed as soon as
  its events stream closes: the window asks again at once and shows that state within a
  few seconds, with no click (KYO-471). A server that takes the connection and answers
  late is *slow to answer*, a status-bar note, not that state. One that answers as a release the app does
  not read, or as something other than kyo serve, gets the same words as the PATH's
  `kyo`, naming the server's address -- see "The desktop app reads every
  project through kyo serve" under "Operating a Kyosei project". One still running an
  older build than the kyo installed on disk (`/v1/version` says `stale`) keeps serving,
  and the status bar carries its `stale_detail` and `kyo serve --restart` (KYO-550).
- **The app writes exactly two things of yours, and never your store** (KYO-114).
  The compose tabs are a real editor, so they do write: files under your project's
  `ideas/`, and a session scratchpad at `~/kyosei/<project>/scratchpad.md` — your
  own surface, deliberately outside `ideas/` and outside the store, so `kyo
  promote` and `kyo sweep` cannot see it by construction. (The switcher's memory of
  which projects you have opened, and a log of what the app's login-shell capture
  did at each launch — its outcome, the shell asked and how long it took, and no
  variable's value — are two further files, but neither is one of yours: both live
  in the app's own data directory, belong to the app, and name projects and
  outcomes rather than containing anything from them.) Nothing under `kyosei/`
  is writable from the app at all: every write is a request to `kyo serve`'s file
  routes, which refuse any path outside `ideas/` and the scratchpad by real path. Promotion, records and
  the export stay with this CLI, where they are one transaction.
- **Two editors of one file do not overwrite each other** (KYO-575). The scratchpad or
  an idea file can be open in two windows, or on two machines. Each tab saves against
  the version it loaded or last saved, and when the file has changed since, the save is
  merged three ways rather than written over it: changes to different lines both stay,
  and where both sides changed the same lines both texts are kept, between three marker
  lines that start with `━━`, the other version first and this window's below it, for
  you to tidy and then delete the markers. A tab with no unsaved typing follows the file
  instead: when kyo serve's events stream says the file changed (`scratchpad` is a kind
  of its own, since the file lives outside `ideas/`), the tab reloads it, keeping the
  caret, and writes nothing back. Nothing is locked, so a sleeping laptop leaves nothing
  behind.
- **The app never creates a record** (KYO-439). The scratchpad's "Promote to idea"
  writes an idea *file* under `ideas/`; turning an idea into a record — its kind,
  title, criteria and touches — is the orchestrator's act, run through `kyo promote`
  in the orchestrator session. An idea tab offers no promotion button, and the app's
  one route to the CLI refuses `kyo promote` by name.

If the app cannot read your store it says so and names the remedy rather than
showing a count it could not verify. A fresh clone of a split store has no
`kyosei.db` at all — it is gitignored — and the title bar sends you to
`kyo rebuild` instead of reporting zero records.

A project whose store is at a different `schema_version` than the app gets a screen
of its own, and which direction it is matters (KYO-168, KYO-178). If the store is
older than the app, that is the case `kyo rebuild --force` fixes and the screen says
so. If the store is **newer** — the ordinary case once you point one app at several
projects, since each project's own `kyo` moves on its own schedule — the app asks you
to bring that project's orchestrator session to upgrade and rebuild it, and offers you
no button that would do it for you. That is not caution for its
own sake: `kyo rebuild --force` rewrites `config.yml`'s `schema_version` too, so an
app that "fixed" a store older than itself would leave that project's own `kyo`
refusing to open it. Either way the screen is re-checkable in place — when the
migration has happened elsewhere, the same window opens the project.
