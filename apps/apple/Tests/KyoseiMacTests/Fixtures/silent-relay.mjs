// A stand-in for `kyo serve` whose connections can go silent without closing, as they can
// across a sleep (KYO-989). Started by SilentConnectionTests.swift; not part of the app.
//
// It answers the routes the Mac app's feed reads for one project `p` with one live session
// `s`, over HTTP/2 with TLS (the protocol Tailscale Serve answers in) or, with PLAIN=1, over
// plain HTTP closing each connection after its answer (as `kyo serve` itself does). Clients
// reach it through a TCP relay that the test drives with signals:
//
//   SIGUSR1  sleep: every relayed connection stops carrying bytes in both directions and is
//            never closed, and a connection made while asleep is accepted and never relayed.
//   SIGUSR2  wake: for REJOIN_MS milliseconds a new connection is closed at once, as while
//            the network is still being joined; after that new connections are relayed.
//            Connections from before stay silent for good, as connections whose peer forgot
//            them during the sleep.
//
// It prints `PORT <n>` once it listens, and `REQ <path>` for each request it answers.
import fs from "node:fs";
import http from "node:http";
import http2 from "node:http2";
import net from "node:net";

const plain = process.env.PLAIN === "1";
const rejoin = Number(process.env.REJOIN_MS || 0);

const ready = JSON.stringify({
  screen: { state: "idle" },
  terminal: { text: "A", working_line: null },
  held: null,
});
const routes = {
  "/v1/projects": { projects: [{ name: "p", can_open: true, problem: null }] },
  "/v1/projects/p/sessions": { sessions: [{ session_id: "s", live: true, has_pane: true }] },
  "/v1/projects/p/sessions/s/turns": { turns: [], cursor: "c0" },
  "/v1/projects/p/sessions/s/screen": {
    ok: true,
    screen: { state: "idle" },
    terminal: { text: "A", working_line: null },
  },
  "/v1/projects/p/sessions/s/changes": { root: "/r", remote: null },
};

// One answer, written through `respond(status, headers)`, `write(text)` and `end(text)`.
function answer(path, io) {
  const bare = path.split("?")[0];
  console.log(`REQ ${bare}`);
  if (bare.endsWith("/events")) {
    io.respond(200, { "content-type": "text/event-stream" });
    if (bare.includes("/sessions/")) io.write(`event: ready\ndata: ${ready}\n\n`);
    const timer = setInterval(() => io.write(": keepalive\n\n"), 1000);
    io.onClose(() => clearInterval(timer));
    return;
  }
  const body = routes[bare];
  io.respond(body ? 200 : 404, { "content-type": "application/json" });
  io.end(JSON.stringify(body ?? { ok: false, error: "no such route" }));
}

let server;
if (plain) {
  server = http.createServer((request, response) => {
    response.shouldKeepAlive = false;
    answer(request.url, {
      respond: (status, headers) => response.writeHead(status, { ...headers, connection: "close" }),
      write: (text) => response.write(text),
      end: (text) => response.end(text),
      onClose: (done) => response.on("close", done),
    });
  });
} else {
  const here = new URL(".", import.meta.url).pathname;
  server = http2.createSecureServer({
    key: fs.readFileSync(process.env.KEY || here + "key.pem"),
    cert: fs.readFileSync(process.env.CERT || here + "cert.pem"),
  });
  server.on("stream", (stream, headers) => {
    stream.on("error", () => {});
    answer(headers[":path"], {
      respond: (status, more) => stream.respond({ ":status": status, ...more }),
      write: (text) => stream.write(text),
      end: (text) => stream.end(text),
      onClose: (done) => stream.on("close", done),
    });
  });
}

server.listen(0, "127.0.0.1", () => {
  const inner = server.address().port;
  let asleep = false;
  let rejoiningUntil = 0;
  const relayed = [];
  const relay = net.createServer((client) => {
    client.on("error", () => {});
    if (asleep) return; // accepted, and never relayed
    if (Date.now() < rejoiningUntil) return client.destroy();
    const upstream = net.connect(inner, "127.0.0.1");
    const pair = { frozen: false };
    relayed.push(pair);
    upstream.on("error", () => {});
    client.on("data", (bytes) => { if (!pair.frozen) upstream.write(bytes); });
    upstream.on("data", (bytes) => { if (!pair.frozen) client.write(bytes); });
    client.on("close", () => { if (!pair.frozen) upstream.destroy(); });
    upstream.on("close", () => { if (!pair.frozen) client.destroy(); });
  });
  relay.listen(0, "127.0.0.1", () => console.log(`PORT ${relay.address().port}`));
  process.on("SIGUSR1", () => {
    asleep = true;
    for (const pair of relayed) pair.frozen = true;
    console.log(`SLEEP ${relayed.length}`);
  });
  process.on("SIGUSR2", () => {
    asleep = false;
    rejoiningUntil = Date.now() + rejoin;
    console.log("WAKE");
  });
});
// The test's end of stdin closes when the test process ends, however it ends.
process.stdin.on("end", () => process.exit(0));
process.stdin.resume();
