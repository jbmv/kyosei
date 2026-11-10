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

`package.sh` builds `Kyosei.app` under `.build/package`. It signs with `KYOSEI_SIGN_IDENTITY`,
and notarizes with `KYOSEI_NOTARY_KEY`, `KYOSEI_NOTARY_KEY_ID` and `KYOSEI_NOTARY_ISSUER`.
