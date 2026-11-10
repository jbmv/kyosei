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

## Views

A bar above the top bar lists the views, and choosing one replaces the pane under it. The
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

The app reads `kyo serve` at `http://127.0.0.1:47370` unless its Settings name another server
and project, which `KYOSEI_SERVE_URL` and `KYOSEI_PROJECT` override. Sending needs a device
token from `kyo devices issue`, entered in Settings and kept in the Keychain. A project with
no live session is shown a Start orchestrator button, which needs the token to hold the `start`
permission. The terminal drawer is a terminal on the session's pane, and needs the token to hold
the `terminal` permission; without it the drawer shows the pane's text and the server's reason.
Files and file links need it to hold the `files` permission.

`package.sh` builds `Kyosei.app` under `.build/package`. It signs with `KYOSEI_SIGN_IDENTITY`,
and notarizes with `KYOSEI_NOTARY_KEY`, `KYOSEI_NOTARY_KEY_ID` and `KYOSEI_NOTARY_ISSUER`.
