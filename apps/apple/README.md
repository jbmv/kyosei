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

## Running it

```
cd web && npm ci && npm run build && cd ..
swift run KyoseiMac
```

The app reads `kyo serve` at `http://127.0.0.1:47370`. `KYOSEI_SERVE_URL` names another
address and `KYOSEI_PROJECT` another project.
