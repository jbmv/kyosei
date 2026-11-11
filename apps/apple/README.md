# Kyosei for Apple platforms

Native clients of `kyo serve`, built for the maintainer's own devices and the current
macOS. A client displays what the server returns and holds no store logic. The server's
routes are described in
[`docs/reference/topics/serve-and-desktop.md`](../../docs/reference/topics/serve-and-desktop.md).

`Sources/KyoseiClient` holds the code that does not draw: requests to the server and the
parsing of its event stream, with tests in `Tests/`. `Sources/KyoseiMac` holds the views.
`web/` is the markdown renderer shown in a web view. It is adapted from
[MDHero](https://github.com/vaibhav-kakde-in/mdhero), whose MIT licence is kept in
`web/LICENSE-mdhero`.

The terminal drawer is [xterm.js](https://github.com/xtermjs/xterm.js), whose MIT licence is
kept in `web/LICENSE-xterm` and copied into the app beside the renderer.

## Projects

The window shows one project at a time. The bar above the top bar begins with a dropdown that
names it and lists every project the server serves (`GET /v1/projects`); a project whose store
cannot be opened is listed with the server's reason and cannot be chosen. Choosing a project
turns the window to it: the chat, the side panel, Records, Files and the status bar are then
that project's. After the dropdown the bar holds a link for each project that has a live session
in a pane, which turns the window to that project and follows that session. The server has no
route that lists live sessions across projects, so the app reads each project's session list
every five seconds, and the list of projects every thirty and whenever the dropdown is opened.

The composer's unsent prompts belong to the project they were written in. The server keeps a
draft for each session and one for the project (KYO-763). The app reads and saves the
followed session's draft while that session is live, and the project's draft when the
project has no session or the followed session has ended, so a prompt written between
sessions is found on any device that opens the project. The server moves the prompts between
the two: an ended session's go to the project's draft, and a new session's first read takes
them. The app learns that the followed session has ended from the session list it reads every
five seconds, and then reads the project's draft in place of what the window holds, so that
the copy another device saved last is the one shown. A save that is waiting is sent before
the composer turns from one draft to another. The page also keeps each project's prompts in
its own storage, under the server and the project, which is all a device with no token has.

A prompt carried into a later session keeps its tags, attachments and anchors. An anchor made
in a session other than the one shown is sent as its line of words, which the server reads
from the session the anchor names. Its chip in the composer is drawn dimmed with a dashed
outline and takes no press, because the chat on screen has no place for it to open. The choice
of draft is `DraftPlace` in `SessionFeed.swift`, and the page's part is in `web/src/projects.ts`.

Placing an anchor is a step of its own in a prompt's undo (KYO-780): Command-Z directly after
it takes the anchor back and leaves the text as it was, and Shift-Command-Z puts it back. The
text area's own history holds edits of the text only, so the page keeps the placings beside it
and steps through whichever was made last. What an unsent prompt is anchored to is highlighted
in the chat, and the highlight goes when the anchor does, unless another prompt holds an anchor
on the same words. An anchor of a sent paragraph that the server reports as stale (KYO-712)
cannot be highlighted, so the chip above that paragraph in the sent prompt reads "place lost"
and its title names the paragraph.

The page's tests are in `web/test/` and run with `npm test` in `web/`. Those in
`anchors.test.ts` need a web view: they build the page, open it in a headless Chrome and feed
it the frames the app would send, and are skipped on a machine with no Chrome.

A live session's task list (KYO-770, KYO-771) is drawn above the composer's prompts, in a
slightly dimmed bubble that is the first thing in the prompts' own scrolling column: it rises
as the prompts grow and scrolls out of view with them when they need the room. The person
adds items and edits, checks and removes their own; the orchestrator's items show their state
and take no press, since the server refuses a device's change to one. The app reads the list
when it turns to a live session and again when the session's stream says its etag changed,
and a save the server refuses as stale leaves the typed words in their field and says so. With
no live session there is no bubble, and a token that may not read the list is told why in it.
The app's part is `showAgenda` and `saveAgenda` in `SessionFeed.swift`; the page's is
`web/src/agenda.ts` and the section of `web/src/chat.ts` that draws it.

The app opens on the project it last showed. On a first run, or when the server no longer
lists that project, it shows the list of projects in place of the chat. The logic is in
`web/src/projects.ts`, and the switch is `SessionFeed.open`.

## Views

After the dropdown and the links, the bar lists the views, and choosing one replaces the pane
under it. The
chat stays drawn beneath the others and keeps following its session, so returning to it
reloads nothing. A view is one entry in `VIEWS` in `web/src/chat.ts` and the elements of
`web/static/chat.html` marked with its `data-view`.

Records shows the project's records, one at a time. With none open it lists them all by
kind; the side bar lists them newest first above the records viewed most recently. Search
and the three filters (Open, Dispatchable, Awaiting you) apply to both lists. A
`kyosei://` link in the chat opens its record there. The view reads `cmd/list`, `cmd/next`,
`cmd/gates` and `cmd/show/<id>`, which need no device token, and reads them again when the
project's event stream reports a change to the store. Its logic is in `web/src/records.ts`.
The viewing history is kept in the app's own defaults for each server and project, not in
the store.

Files shows the project's files as `kyo serve` serves them: the code repository and the store
repository, each as git shows it, and the followed session's scratchpad directory when the
server has one for it. The side bar holds a chip for each of these roots, the chosen root's
tree, a toggle for dotfiles, which are hidden at first, and the files the session's replies and
prompts have named, newest first; the chat's side bar lists those files too. A markdown file is
drawn as a reply is, and its relative links and images are read against its own folder. A source
or text file is read-only, with line numbers and with colouring for the types the renderer
knows. An image is shown, and any other file is described by its name, type and size. A text
file longer than 400,000 characters is shown as its first lines, with the rest offered, and the
whole of one longer than 1,500,000 characters is drawn without colouring. While the view is
shown, the open file is asked for again every three seconds with its `ETag`, which the server
answers without the bytes when nothing changed, and the open folders every twelve.

A file path in a reply, a prompt or a record's page is drawn as a link when the server's
`files/resolve` route places it in a served root, and a click opens it in Files, at the line
when one follows the path after a colon. The words asked about are absolute paths, paths under
`~/`, and relative paths that hold a slash and an extension; fenced code blocks are left as
they are. The logic is in `web/src/files.ts`. All of this needs the device token to hold the
`files` permission. Without it the view shows the server's refusal and no path is a link.

### Files outside the served roots

`kyo serve` serves no file outside those roots, so a path that lies elsewhere on the server's
machine cannot be opened in Files. Such a path is drawn as a dotted link when it reads as a
file: it is under `~/` or one of the usual top folders, or it has an extension. A click offers
Copy path, and Reveal in Finder when the file exists on the machine the app runs on. That is
the case in two situations:

- The app runs on the server's own machine, which it takes to be so when the server's address
  is loopback.
- Settings holds a path mapping, a prefix of a path on the server's machine and the folder it
  stands for on this one, and the mapped path exists. Mounting the server's home folder over
  the network and mapping it, for example `/Users/name` to `/Volumes/name`, is one way to
  arrange this.

Anything else, such as opening these files in the app itself or fetching them by another
route, is left to whoever needs it. The mapping is in `localPath` in `web/src/files.ts`, and
the check and the reveal are in `SessionFeed.files(body:)`.

## Running it

```
cd web && npm ci && npm run build && cd ..
swift run KyoseiMac
```

The app reads `kyo serve` at `http://127.0.0.1:47370` unless its Settings name another server,
which `KYOSEI_SERVE_URL` overrides. The project is chosen in the window, and `KYOSEI_PROJECT`
names the one a launch opens on. Sending needs a device
token from `kyo devices issue`, entered in Settings and kept in the Keychain. A project with
no live session is shown a Start orchestrator button, which needs the token to hold the `start`
permission. The terminal drawer is a terminal on the session's pane, and needs the token to hold
the `terminal` permission; without it the drawer shows the pane's text and the server's reason.
Files and file links need it to hold the `files` permission.

`package.sh` builds `Kyosei.app` under `.build/package`. It signs with `KYOSEI_SIGN_IDENTITY`,
and notarizes with `KYOSEI_NOTARY_KEY`, `KYOSEI_NOTARY_KEY_ID` and `KYOSEI_NOTARY_ISSUER`.
