// The app's one transport: an HTTP(S) client of `kyo serve` (KYO-459, KYO-452).
//
// Every project the app shows -- this machine's and another's alike -- is read and
// written through a `kyo serve`: the CLI serving its own `--json` over loopback HTTP
// (`src/kyosei/serve.py`, docs/reference/topics/serve-and-desktop.md "Serving a store").
// There is no second path: no
// `kyo` subprocess for a store read, no SQLite, no file write of the app's own. The
// write boundary is `serve_files.py`'s, in Python, once, for every viewer; the
// change events are `serve_events.py`'s.
//
// A SERVER IS AN ADDRESS. `{host, port}`, the same pair `RemoteFacts` has always
// carried, and the scheme follows from the host rather than being a third field:
// `kyo serve` binds loopback and nothing else (no password or token: the tailnet's
// access rule is the boundary), so a loopback host is spoken to in plain HTTP, and any
// other name can only be a proxy in front of one -- Tailscale Serve, which terminates
// HTTPS with a certificate the system already trusts. So `127.0.0.1:47370` is this
// machine's server and `<machine>.<tailnet>.ts.net` (port 443) is another's, and
// `http://` to a non-loopback name is refused before anything is
// sent: it would be a LAN listener `kyo serve` refuses to be.
//
// A PROJECT IS (SERVER, NAME). The name is the registry's (`/v1/projects`), and a
// window still carries the project's root, which that same listing reports: the root
// is a path on the SERVER's disk, so it is only ever compared with roots from the same
// server (KYO-463). `ServedProject` resolves the name from the root on first use and
// keeps it; a root the server does not list is refused with the remedy (`kyo projects
// add`), never guessed at.
//
// This machine's server is `127.0.0.1:47370` unless `KYOSEI_SERVE_URL` names another
// address -- the seam the desktop checks use to point every launch at a `kyo serve`
// of their own on an ephemeral port, and never at the real one.
//
// NODE:HTTP, NOT fetch (KYO-468). Every request and the change stream go through
// `node:http` (loopback) or `node:https` (a Tailscale Serve name), and the stream is
// read by the hand-rolled `text/event-stream` reader below -- the choice the old
// `remote.ts` made in this same Electron (44.0.0, Node 24.18.1, undici 7.29.0) for its
// own reasons. Node's global `fetch` is undici, whose `writeH1` calls
// `socket.setTypeOfService` on every HTTP/1 request, and `net.Socket.setTypeOfService`
// THROWS on any non-zero errno outside Windows; on macOS `setsockopt(IP_TOS)` answers
// EINVAL on a socket in some closed or reset states. That surfaced as an Electron
// "Uncaught Exception" dialog while desktop checks were stopping their kyo serves; the
// trigger was not reproduced, so the class is removed instead:
// `node:http`'s client never sets a type of service (read from this Electron's bundled
// Node: only `net` and undici name it). And every failure a socket can have -- refused,
// reset, EINVAL, a server stopped mid-answer or mid-stream -- lands on an `error`
// listener that stays attached for the socket's life and settles one promise once:
// a request becomes `ServeUnreachable` (not running, or slow when it ran out of
// time), a stream ends and is re-opened after its wait. `tests/seams.spec.ts` fails if
// `fetch` appears anywhere under `src/main`; `tests/kyoserve-transport.spec.ts` stops a
// real kyo serve under an open stream and in-flight requests, in this process and in
// the app's.
//
// Electron-free, like every other seam: `tests/kyoserve.spec.ts` drives it against a
// real `kyo serve` started on port 0.

import {
  type ClientRequest,
  request as httpRequest,
  type IncomingHttpHeaders,
  type IncomingMessage,
} from "node:http";
import { request as httpsRequest } from "node:https";
import { StringDecoder } from "node:string_decoder";
import type { ServeFacts, StoreChangeKind } from "../shared/protocol";
import { kyoCompatFrom } from "./kyo";

/** `kyo serve`'s default port. */
export const DEFAULT_SERVE_PORT = 47370;

/** Names the address of THIS machine's kyo serve, when it is not the default. */
export const SERVE_URL_ENV = "KYOSEI_SERVE_URL";

/** The one API prefix `kyo serve` speaks. */
export const API_PREFIX = "/v1";

/** How long one request waits before it is said to have gone unanswered. */
export const REQUEST_TIMEOUT_MS = 30_000;

/**
 * How long "is anything there" waits before the server is said to be slow to answer
 * (KYO-449.c7). A not-running server REFUSES at once, whatever this is, so this bounds
 * only how long a server that took the connection gets -- and `/v1/version` runs `kyo
 * version --json` under the same lock as every served command (`serve.py`,
 * `version_payload`), so a loaded server answers it only after whatever it is running.
 * 3 s turned a cold kyo serve on a loaded desktop run into the sticky "not running"
 * state; an unloaded one answers in about 50 ms, measured.
 */
export const PROBE_TIMEOUT_MS = 20_000;

/** The two commands that start this machine's kyo serve, as the app states them. */
export const START_SERVE_COMMANDS = ["kyo serve --install --apply", "kyo serve"] as const;

/** A kyo serve, by where it listens. */
export interface ServerAddress {
  host: string;
  port: number;
}

const LOOPBACK_NAMES = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

/** Is this a loopback name -- a `kyo serve` spoken to directly, in plain HTTP? */
export function isLoopbackHost(host: string): boolean {
  const bare = host.trim().toLowerCase();
  return LOOPBACK_NAMES.has(bare) || /^127(\.\d{1,3}){3}$/.test(bare);
}

/** The scheme, host and port every request to this server goes to. */
export function baseUrl(address: ServerAddress): string {
  const host =
    address.host.includes(":") && !address.host.startsWith("[")
      ? `[${address.host}]`
      : address.host;
  if (isLoopbackHost(address.host)) return `http://${host}:${address.port}`;
  return address.port === 443 ? `https://${host}` : `https://${host}:${address.port}`;
}

/** How every sentence names a server: its URL, which is also what a person types. */
export function addressLabel(address: ServerAddress): string {
  return baseUrl(address);
}

/** Two addresses name the same server. */
export function sameServer(a: ServerAddress | null, b: ServerAddress | null): boolean {
  if (a === null || b === null) return a === b;
  return a.host.toLowerCase() === b.host.toLowerCase() && a.port === b.port;
}

/**
 * An address as a person typed it, checked before anything is sent to it: a URL
 * (`https://<machine>.<tailnet>.ts.net`, `http://127.0.0.1:47370`), or a host with an
 * optional port. A loopback host without a port is kyo serve's default port; any other
 * host without one is HTTPS's.
 */
export function parseServerAddress(
  typed: string,
): { address: ServerAddress; problem: null } | { address: null; problem: string } {
  let text = typed.trim();
  if (text === "") return { address: null, problem: "a server needs an address" };
  let scheme: "http" | "https" | null = null;
  const schemed = /^([a-z][a-z0-9+.-]*):\/\/(.*)$/i.exec(text);
  if (schemed !== null) {
    const named = (schemed[1] ?? "").toLowerCase();
    if (named !== "http" && named !== "https") {
      return {
        address: null,
        problem: `${typed.trim()} is not an http or https address of a kyo serve`,
      };
    }
    scheme = named;
    text = schemed[2] ?? "";
  }
  text = text.replace(/\/+$/, "").replace(/\/v1$/, "");
  if (text.includes("/")) {
    return {
      address: null,
      problem: `${typed.trim()} has a path; a server is an address alone (host, or host:port)`,
    };
  }
  const match = /^(\[[^\]]+\]|[^:]+)(?::(\d+))?$/.exec(text);
  if (match === null) return { address: null, problem: `${typed.trim()} is not an address` };
  const host = (match[1] ?? "").replace(/^\[(.*)\]$/, "$1");
  const loopback = isLoopbackHost(host);
  const port =
    match[2] === undefined
      ? loopback
        ? scheme === "https"
          ? 443
          : DEFAULT_SERVE_PORT
        : 443
      : Number(match[2]);
  if (!(Number.isInteger(port) && port >= 1 && port <= 65535)) {
    return { address: null, problem: "the port must be a whole number from 1 to 65535" };
  }
  if (scheme === "http" && !loopback) {
    return {
      address: null,
      problem:
        `http://${host} is not an address a kyo serve answers: kyo serve listens on loopback ` +
        "only, and another machine's is reached over HTTPS through Tailscale Serve " +
        `(https://${host})`,
    };
  }
  if (scheme === "https" && loopback) {
    return {
      address: null,
      problem: `https://${host} is loopback, where kyo serve speaks plain HTTP: use http://${host}${match[2] === undefined ? "" : `:${port}`}`,
    };
  }
  return { address: { host, port }, problem: null };
}

/** This machine's kyo serve: `KYOSEI_SERVE_URL` when set, else 127.0.0.1:47370. */
export function localServer(env: Record<string, string | undefined> = process.env): ServerAddress {
  const named = env[SERVE_URL_ENV];
  if (named !== undefined && named.trim() !== "") {
    const parsed = parseServerAddress(named);
    if (parsed.address !== null) return parsed.address;
  }
  return { host: "127.0.0.1", port: DEFAULT_SERVE_PORT };
}

// ---------------------------------------------------------------- the refusals

/**
 * Nothing answered at the address. For this machine's server the sentence names the
 * two ways to start one (KYO-459.c2); for another's, where it was asked.
 */
export class ServeUnreachable extends Error {
  readonly address: ServerAddress;
  /**
   * It took the connection and did not answer in time: running and slow, which is not
   * the "not running" state (KYO-449.c6). False for a refusal, a name that does not
   * resolve, a reset -- everything that says nothing is there.
   */
  readonly slow: boolean;
  constructor(address: ServerAddress, cause: string, local: boolean, slow = false) {
    super(slow ? slowSentence(address) : unreachableSentence(address, cause, local));
    this.name = "ServeUnreachable";
    this.address = address;
    this.slow = slow;
  }
}

/** The words for a server that took the connection and has not answered yet. */
export function slowSentence(address: ServerAddress): string {
  return `kyo serve at ${addressLabel(address)} is slow to answer: it took the connection and has not answered yet`;
}

/** The one state's words: kyo serve is not running, and how to start it. */
export function unreachableSentence(address: ServerAddress, cause: string, local: boolean): string {
  const at = addressLabel(address);
  if (local) {
    return (
      `kyo serve is not running: nothing answered at ${at} (${cause}). Start it with ` +
      `\`${START_SERVE_COMMANDS[0]}\` (at login, from now on) or \`${START_SERVE_COMMANDS[1]}\` ` +
      "(in a terminal, for now)"
    );
  }
  return `nothing answered at ${at} (${cause}): is kyo serve running there, and Tailscale Serve in front of it?`;
}

/**
 * The transport itself failed: nothing answered (`code` is the socket's -- ECONNREFUSED,
 * ECONNRESET, ENOTFOUND, even an EINVAL from the socket layer), or the answer did not
 * arrive in time (`timedOut`).
 */
class TransportFailure extends Error {
  readonly code: string | null;
  readonly timedOut: boolean;
  /** It went out on a kept-alive socket an earlier request had used. */
  reused = false;
  constructor(message: string, code: string | null, timedOut: boolean) {
    super(message);
    this.name = "TransportFailure";
    this.code = code;
    this.timedOut = timedOut;
  }
}

/** Anything a request, its socket or its response threw, as a `TransportFailure`. */
function failureOf(error: unknown): TransportFailure {
  if (error instanceof TransportFailure) return error;
  const failure = error as { code?: unknown; message?: unknown } | null;
  const code = typeof failure?.code === "string" ? failure.code : null;
  const message = typeof failure?.message === "string" ? failure.message : String(error);
  return new TransportFailure(message, code, false);
}

/** Did this failure run out of time, rather than find nothing there? */
function timedOut(error: TransportFailure): boolean {
  return error.timedOut;
}

function causeOf(error: TransportFailure): string {
  if (error.timedOut) return "no answer in time";
  if (error.code !== null) return error.code === "ECONNREFUSED" ? "connection refused" : error.code;
  return error.message;
}

/** One response header, as a single string (Node lists a repeated one). */
function headerOf(headers: IncomingHttpHeaders, name: string): string | null {
  const value = headers[name];
  if (Array.isArray(value)) return value.join(", ");
  return typeof value === "string" ? value : null;
}

/** A raw HTTP answer, before `ServeClient.request` reads it as kyo serve's. */
interface RawAnswer {
  status: number;
  headers: IncomingHttpHeaders;
  text: string;
}

/** The URL every request to this server's `path` goes to. */
function urlFor(address: ServerAddress, path: string): URL {
  return new URL(`${baseUrl(address)}${API_PREFIX}${path}`);
}

/**
 * One HTTP exchange over `node:http` (loopback) or `node:https` (anything else), never
 * `fetch` -- see "NODE:HTTP, NOT fetch" at the top of this file. It settles exactly once:
 * with the whole answer, or with a `TransportFailure` for a refusal, a reset, a socket
 * error of any kind, or `timeoutMs` passing before the body has ended. Its `error`
 * listeners stay attached after it settles, so a socket that fails LATER -- a server
 * stopped after the answer arrived -- is heard and ignored rather than thrown.
 */
function exchange(
  address: ServerAddress,
  method: string,
  path: string,
  headers: Record<string, string>,
  body: string | undefined,
  timeoutMs: number,
): Promise<RawAnswer> {
  return new Promise((resolve, reject) => {
    const url = urlFor(address, path);
    const secure = url.protocol === "https:";
    const sent: Record<string, string> = { ...headers };
    // kyo serve refuses a body without a Content-Length (411), and `node:http` would
    // otherwise send a string body chunked.
    if (body !== undefined) sent["Content-Length"] = String(Buffer.byteLength(body));
    let settled = false;
    let request: ClientRequest | null = null;
    const settle = (error: TransportFailure | null, answer?: RawAnswer): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error !== null) {
        error.reused = request?.reusedSocket === true;
        request?.destroy();
        reject(error);
      } else if (answer !== undefined) {
        resolve(answer);
      }
    };
    const timer = setTimeout(
      () => settle(new TransportFailure(`no answer within ${timeoutMs} ms`, null, true)),
      timeoutMs,
    );
    const onResponse = (response: IncomingMessage): void => {
      const chunks: Buffer[] = [];
      response.on("data", (chunk: Buffer) => chunks.push(chunk));
      response.on("error", (error) => settle(failureOf(error)));
      response.on("end", () =>
        settle(null, {
          status: response.statusCode ?? 0,
          headers: response.headers,
          text: Buffer.concat(chunks).toString("utf8"),
        }),
      );
      response.on("close", () => {
        if (!response.complete) {
          settle(new TransportFailure("the connection closed mid-answer", "ECONNRESET", false));
        }
      });
    };
    try {
      // Loopback takes a fresh connection per request (`kyo serve` speaks HTTP/1.0 and
      // closes each one anyway); HTTPS keeps Node's default kept-alive agent, so a
      // Tailscale Serve name is not a TLS handshake per request.
      request = secure
        ? httpsRequest(url, { method, headers: sent }, onResponse)
        : httpRequest(url, { method, headers: sent, agent: false }, onResponse);
    } catch (error) {
      settle(failureOf(error));
      return;
    }
    request.on("error", (error) => settle(failureOf(error)));
    request.end(body);
  });
}

/** One HTTP answer: the status, the parsed JSON body (or null), the raw text, the ETag. */
export interface ServeAnswer {
  status: number;
  data: Record<string, unknown> | null;
  text: string;
  etag: string | null;
}

/** The sentence a refusal carries: `error` from kyo serve's body, else the status. */
export function refusalOf(answer: ServeAnswer, what: string): string {
  const said = answer.data?.error;
  if (typeof said === "string" && said !== "") return said;
  return `${what} answered ${answer.status}${answer.text.trim() === "" ? "" : `: ${answer.text.trim().slice(0, 200)}`}`;
}

// ----------------------------------------------------------------- the client

/** One registry entry, as `/v1/projects` lists it. */
export interface ServedEntry {
  name: string;
  root: string;
  storeRoot: string | null;
  prefix: string | null;
  canOpen: boolean;
  problem: string | null;
}

/** What `/v1/projects` answered: the list, or why there is none. */
export type ServedProjects =
  | { ok: true; registry: string | null; projects: ServedEntry[] }
  | { ok: false; problem: string; unreachable: boolean; error?: Error };

/** A `kyo <command> --json` answer, in the shape `runKyoJson` always returned. */
export interface KyoJson {
  code: number;
  data: Record<string, unknown> | null;
  stderr: string;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function encodePath(relpath: string): string {
  return relpath
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

/**
 * A command's argv as its route: positionals become path segments, and `--flag value`,
 * `--flag=value` and a bare `--flag` become the query string -- `kyo serve` hands the
 * query back to the CLI's own parser, so nothing here knows a flag's meaning.
 * Every argv this app builds puts its positionals first, which is what lets a word
 * after a flag be read as that flag's value.
 */
export function routeOf(args: readonly string[]): { command: string; path: string; query: string } {
  const [command = "", ...rest] = args;
  const positionals: string[] = [];
  const query: string[] = [];
  let index = 0;
  while (index < rest.length && !(rest[index] ?? "").startsWith("--")) {
    positionals.push(rest[index] ?? "");
    index += 1;
  }
  while (index < rest.length) {
    const word = rest[index] ?? "";
    index += 1;
    if (!word.startsWith("--")) {
      throw new Error(
        `kyosei: ${JSON.stringify(word)} follows the flags of \`kyo ${command}\`; a route puts positionals first`,
      );
    }
    const flag = word.slice(2);
    const eq = flag.indexOf("=");
    if (eq >= 0) {
      query.push(
        `${encodeURIComponent(flag.slice(0, eq))}=${encodeURIComponent(flag.slice(eq + 1))}`,
      );
      continue;
    }
    const next = rest[index];
    if (next !== undefined && !next.startsWith("--")) {
      query.push(`${encodeURIComponent(flag)}=${encodeURIComponent(next)}`);
      index += 1;
    } else {
      query.push(encodeURIComponent(flag));
    }
  }
  const path = [command, ...positionals].map((word) => encodeURIComponent(word)).join("/");
  return { command, path, query: query.join("&") };
}

/** How the change stream is followed: re-opened after a drop, with this much wait. */
export const EVENTS_RETRY_MS = [500, 1_000, 2_000, 5_000] as const;

/** A kyo serve, spoken to. One per address; stateless apart from the name cache. */
export class ServeClient {
  readonly address: ServerAddress;
  /** Is this the machine's own server (the one the "not running" state is about)? */
  readonly local: boolean;
  private names = new Map<string, string>();

  constructor(address: ServerAddress, local: boolean) {
    this.address = address;
    this.local = local;
  }

  get label(): string {
    return addressLabel(this.address);
  }

  /** One request. Throws `ServeUnreachable` when nothing answered at all. */
  async request(
    method: string,
    path: string,
    options: { body?: string; headers?: Record<string, string>; timeoutMs?: number } = {},
  ): Promise<ServeAnswer> {
    const send = () =>
      exchange(
        this.address,
        method,
        path,
        options.headers ?? {},
        options.body,
        options.timeoutMs ?? REQUEST_TIMEOUT_MS,
      );
    let answer: RawAnswer;
    try {
      answer = await send();
    } catch (error) {
      let failure = failureOf(error);
      // A kept-alive HTTPS socket the far end had already closed is reset on reuse,
      // before anything was read: once more, for a read only (Node's own advice).
      if (
        failure.reused &&
        failure.code === "ECONNRESET" &&
        (method === "GET" || method === "HEAD")
      ) {
        try {
          answer = await send();
        } catch (again) {
          failure = failureOf(again);
          throw new ServeUnreachable(this.address, causeOf(failure), this.local, timedOut(failure));
        }
      } else {
        throw new ServeUnreachable(this.address, causeOf(failure), this.local, timedOut(failure));
      }
    }
    const text = answer.text;
    let data: Record<string, unknown> | null = null;
    const type = headerOf(answer.headers, "content-type") ?? "";
    if (type.includes("json")) {
      try {
        const parsed: unknown = JSON.parse(text);
        if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
          data = parsed as Record<string, unknown>;
        }
      } catch {
        data = null;
      }
    }
    return { status: answer.status, data, text, etag: headerOf(answer.headers, "etag") };
  }

  /** `/v1/version`: the build it runs, or why nothing could be asked. */
  async version(timeoutMs = PROBE_TIMEOUT_MS): Promise<VersionProbe> {
    try {
      const answer = await this.request("GET", "/version", { timeoutMs });
      if (answer.status === 200 && answer.data !== null) return { ok: true, data: answer.data };
      return {
        ok: false,
        problem: refusalOf(answer, `${this.label}/v1/version`),
        unreachable: false,
        slow: false,
        refusedByServe: answer.data?.refused_by === "kyo serve",
      };
    } catch (error) {
      return {
        ok: false,
        problem: (error as Error).message,
        unreachable: error instanceof ServeUnreachable,
        slow: error instanceof ServeUnreachable && error.slow,
        refusedByServe: false,
      };
    }
  }

  /** `/v1/projects`: the registry as the server reads it now. */
  async projects(timeoutMs = REQUEST_TIMEOUT_MS): Promise<ServedProjects> {
    let answer: ServeAnswer;
    try {
      answer = await this.request("GET", "/projects", { timeoutMs });
    } catch (error) {
      return {
        ok: false,
        problem: (error as Error).message,
        unreachable: error instanceof ServeUnreachable,
        error: error as Error,
      };
    }
    if (answer.status !== 200 || !Array.isArray(answer.data?.projects)) {
      return {
        ok: false,
        problem: refusalOf(answer, `${this.label}/v1/projects`),
        unreachable: false,
      };
    }
    const projects: ServedEntry[] = [];
    for (const raw of answer.data.projects as unknown[]) {
      if (raw === null || typeof raw !== "object") continue;
      const row = raw as Record<string, unknown>;
      const name = str(row.name);
      const root = str(row.root);
      if (name === null || root === null) continue;
      projects.push({
        name,
        root,
        storeRoot: str(row.store_root),
        prefix: str(row.prefix),
        canOpen: row.can_open === true,
        problem: str(row.problem),
      });
      this.names.set(root, name);
    }
    return { ok: true, registry: str(answer.data.registry), projects };
  }

  /**
   * The registry name of the project at `root` on this server: kept once known, asked
   * of `/v1/projects` otherwise. A root the server does not list is refused by a
   * sentence naming the remedy.
   */
  async nameFor(root: string): Promise<string> {
    const held = this.names.get(root);
    if (held !== undefined) return held;
    const listed = await this.projects();
    if (!listed.ok) throw listed.error ?? new Error(listed.problem);
    const found = listed.projects.find((entry) => entry.root === root || entry.storeRoot === root);
    if (found !== undefined) {
      this.names.set(root, found.name);
      return found.name;
    }
    throw new Error(notServedSentence(root, this.label, this.local, listed.projects));
  }

  /** A project on this server, by its root there. */
  project(root: string): ServedProject {
    return new ServedProject(this, root);
  }
}

/** What asking `/v1/version` came to. */
export type VersionProbe =
  | { ok: true; data: Record<string, unknown> }
  | {
      ok: false;
      problem: string;
      /** Nothing answered at all (refused, or slow: see `slow`). */
      unreachable: boolean;
      /** It took the connection and did not answer in time (KYO-449.c6). */
      slow: boolean;
      /** An answer, and it was kyo serve's own refusal (a Host it does not answer, say). */
      refusedByServe: boolean;
    };

/**
 * A window's kyo serve, as the window states it (KYO-459.c2, KYO-449.c5/c6): "not
 * running" only when nothing took the connection; slow when something took it and did
 * not answer in time; otherwise its `/v1/version` judged against `KYO_ACCEPTS`, naming
 * this address, and whether it says it is stale (KYO-550). Electron-free, so each branch
 * has its own check (`tests/seams.spec.ts`).
 */
export function serveFactsFrom(client: ServeClient, probe: VersionProbe): ServeFacts {
  const base = {
    address: client.label,
    local: client.local,
    commands: [...START_SERVE_COMMANDS],
  };
  if (!probe.ok) {
    const slow = probe.unreachable && probe.slow;
    const running = !probe.unreachable || slow;
    // An answer that was not kyo serve's own refusal is something else answering there.
    const compat =
      running && !slow && !probe.refusedByServe
        ? kyoCompatFrom(
            { kind: "serve", address: client.label, local: client.local },
            { data: null, detail: probe.problem },
          )
        : null;
    return {
      ...base,
      running,
      slow,
      problem: probe.problem,
      compat,
      stale: false,
      staleDetail: null,
    };
  }
  const kyosei = probe.data.kyosei;
  const payload =
    kyosei !== null && typeof kyosei === "object" && !Array.isArray(kyosei)
      ? (kyosei as Record<string, unknown>)
      : null;
  const compat = kyoCompatFrom(
    { kind: "serve", address: client.label, local: client.local },
    {
      data: payload,
      detail:
        payload === null ? `${client.label}/v1/version answered without a \`kyosei\` build` : "",
    },
  );
  // KYO-550: the server's own word that the build on disk has moved past it.
  const stale = probe.data.stale === true;
  const detail = probe.data.stale_detail;
  return {
    ...base,
    running: true,
    slow: false,
    problem: null,
    compat,
    stale,
    staleDetail: stale && typeof detail === "string" && detail !== "" ? detail : null,
  };
}

/** Why a root is not served, and the command that serves it. */
export function notServedSentence(
  root: string,
  label: string,
  local: boolean,
  listed: readonly ServedEntry[],
): string {
  const names = listed.map((entry) => entry.name).join(", ");
  return (
    `${root} is not a project the kyo serve at ${label} serves` +
    (names === "" ? " (its registry lists none)" : ` (it serves ${names})`) +
    `: add it with \`kyo projects add ${root}\`` +
    (local ? "" : " on that machine")
  );
}

/** A `changed` event's kind, when the stream names one this build knows. */
function changedKind(data: string): StoreChangeKind | null {
  try {
    const parsed = JSON.parse(data) as { kind?: unknown };
    // `scratchpad` since KYO-575: an older kyo serve never sends it, and a tab on the
    // scratchpad then hears nothing until its own save merges what changed.
    return parsed.kind === "store" ||
      parsed.kind === "ideas" ||
      parsed.kind === "sessions" ||
      parsed.kind === "scratchpad"
      ? parsed.kind
      : null;
  } catch {
    return null;
  }
}

/**
 * One project on one server: every read (`json`), every file (`ideas`, `scratchpad`) and
 * the change stream (`subscribe`). This is what the store modules take where they once
 * took a `KyoResolution` and a directory to run `kyo` in.
 */
export class ServedProject {
  readonly client: ServeClient;
  /** The project's root ON THE SERVER'S MACHINE. */
  readonly root: string;

  constructor(client: ServeClient, root: string) {
    this.client = client;
    this.root = root;
  }

  get server(): ServerAddress {
    return this.client.address;
  }

  /** The server is this machine's, so `root` is a path on this disk. */
  get local(): boolean {
    return this.client.local;
  }

  private async base(): Promise<string> {
    return `/projects/${encodeURIComponent(await this.client.nameFor(this.root))}`;
  }

  /**
   * `kyo <args> --json`, run by the server. The same `{code, data, stderr}` a local run
   * returned: 200 is exit 0, 400 a usage error (64), 422 the CLI's own failure (1). A
   * refusal kyo serve makes itself (an unserved command, an unknown project) comes back
   * as code 1 with its sentence in both `data.error` and `stderr`.
   */
  async json(args: readonly string[]): Promise<KyoJson> {
    // KYO-203.c3 / KYO-184: the tally a check reads, now counted at the one place a
    // command is asked of kyo serve -- the successor of `runKyo`'s own count.
    const globals = globalThis as {
      __kyoInvocations?: number;
      __kyoCommands?: Record<string, number>;
    };
    globals.__kyoInvocations = (globals.__kyoInvocations ?? 0) + 1;
    const verb = args.find((word) => !word.startsWith("-")) ?? "";
    const tally = globals.__kyoCommands ?? {};
    tally[verb] = (tally[verb] ?? 0) + 1;
    globals.__kyoCommands = tally;
    const route = routeOf(args);
    const answer = await this.client.request(
      "GET",
      `${await this.base()}/cmd/${route.path}${route.query === "" ? "" : `?${route.query}`}`,
    );
    const code = answer.status === 200 ? 0 : answer.status === 400 ? 64 : 1;
    const refused = answer.data?.refused_by === "kyo serve" || answer.data === null;
    return {
      code,
      data: answer.data,
      stderr: code === 0 ? "" : refused ? refusalOf(answer, `kyo ${route.command}`) : "",
    };
  }

  /** `ideas/` as kyo serve lists it: every path, with its type, size, mtime, created time and etag. */
  async ideaEntries(): Promise<
    | { ok: true; root: string | null; exists: boolean; entries: IdeaEntry[] }
    | { ok: false; problem: string }
  > {
    const answer = await this.client.request("GET", `${await this.base()}/ideas/`);
    if (answer.status !== 200 || !Array.isArray(answer.data?.entries)) {
      return { ok: false, problem: refusalOf(answer, "the ideas/ listing") };
    }
    const entries: IdeaEntry[] = [];
    for (const raw of answer.data.entries as unknown[]) {
      if (raw === null || typeof raw !== "object") continue;
      const row = raw as Record<string, unknown>;
      const path = str(row.path);
      const type = row.type;
      if (path === null || (type !== "file" && type !== "dir" && type !== "symlink")) continue;
      entries.push({
        path,
        type,
        size: typeof row.size === "number" ? row.size : null,
        mtimeNs: typeof row.mtime_ns === "number" ? row.mtime_ns : null,
        // KYO-467: the birth time where the server's filesystem keeps one, else the
        // earliest stamp it does keep; a server from before it sends none.
        createdNs: typeof row.created_ns === "number" ? row.created_ns : null,
        etag: str(row.etag),
      });
    }
    return { ok: true, root: str(answer.data.root), exists: answer.data.exists === true, entries };
  }

  /** One file under `ideas/`, or the scratchpad (`relpath` null): its text and ETag. */
  async readFile(relpath: string | null): Promise<FileRead> {
    const answer = await this.client.request("GET", await this.filePath(relpath));
    if (answer.status === 200)
      return { ok: true, text: answer.text, etag: answer.etag, missing: false };
    return {
      ok: false,
      missing: answer.status === 404,
      problem: refusalOf(answer, relpath === null ? "the scratchpad" : `ideas/${relpath}`),
    };
  }

  /**
   * One governed document's text (KYO-467): `relpath` is relative to the project's code
   * root, and kyo serve answers only for a file `kyo docs check` lists.
   */
  async readDocument(relpath: string): Promise<FileRead> {
    const answer = await this.client.request(
      "GET",
      `${await this.base()}/docs/${encodePath(relpath)}`,
    );
    if (answer.status === 200)
      return { ok: true, text: answer.text, etag: answer.etag, missing: false };
    return {
      ok: false,
      missing: answer.status === 404,
      problem: refusalOf(answer, relpath),
    };
  }

  /**
   * Write one file: `expect` is the ETag it replaces, or `"create"` for a file that must
   * not exist yet. Answers the new ETag, or the refusal (a 412 carries the current one).
   */
  async writeFile(relpath: string | null, text: string, expect: string): Promise<FileWrite> {
    const headers: Record<string, string> = { "Content-Type": "text/markdown; charset=utf-8" };
    if (expect === "create") headers["If-None-Match"] = "*";
    else headers["If-Match"] = expect;
    const answer = await this.client.request("PUT", await this.filePath(relpath), {
      body: text,
      headers,
    });
    if (answer.status === 200 || answer.status === 201) {
      return { ok: true, etag: str(answer.data?.etag) ?? answer.etag, status: answer.status };
    }
    return {
      ok: false,
      status: answer.status,
      etag: str(answer.data?.etag),
      problem: refusalOf(answer, relpath === null ? "the scratchpad" : `ideas/${relpath}`),
    };
  }

  /** Delete one file (with the ETag it was seen at) or one EMPTY folder (no ETag). */
  async deleteFile(
    relpath: string,
    etag: string | null,
  ): Promise<{ ok: true } | { ok: false; problem: string }> {
    const answer = await this.client.request("DELETE", await this.filePath(relpath), {
      headers: etag === null ? {} : { "If-Match": etag },
    });
    return answer.status === 200
      ? { ok: true }
      : { ok: false, problem: refusalOf(answer, `ideas/${relpath}`) };
  }

  /** Rename or move within `ideas/`; the destination must be free. */
  async move(from: string, to: string): Promise<{ ok: true } | { ok: false; problem: string }> {
    const answer = await this.client.request("POST", `${await this.base()}/ideas-move`, {
      body: JSON.stringify({ from, to }),
      headers: { "Content-Type": "application/json" },
    });
    return answer.status === 200
      ? { ok: true }
      : { ok: false, problem: refusalOf(answer, "the move") };
  }

  /** Make a folder under `ideas/` (and its parents). */
  async mkdir(
    path: string,
  ): Promise<{ ok: true; created: boolean } | { ok: false; problem: string }> {
    const answer = await this.client.request("POST", `${await this.base()}/ideas-mkdir`, {
      body: JSON.stringify({ path }),
      headers: { "Content-Type": "application/json" },
    });
    if (answer.status === 200 || answer.status === 201) {
      return { ok: true, created: answer.data?.created === true };
    }
    return { ok: false, problem: refusalOf(answer, `ideas/${path}`) };
  }

  private async filePath(relpath: string | null): Promise<string> {
    const base = await this.base();
    return relpath === null ? `${base}/scratchpad` : `${base}/ideas/${encodePath(relpath)}`;
  }

  /**
   * Follow the project's change stream until the returned function is called:
   * `onChanged` hears each `changed` event's kind. A dropped stream -- a server
   * restarted, a laptop that slept -- is re-opened after a short wait, so nobody has
   * to re-open the window to hear the store again. The drop and the return are told as
   * `server` (KYO-378.c4's kind, made here on the client side and never sent by kyo
   * serve): whatever a window shows was read from a server that went away, and is read
   * again, the way a first load reads it, when it is back.
   */
  subscribe(onChanged: (kind: StoreChangeKind) => void): () => void {
    let stopped = false;
    let current: ClientRequest | null = null;
    let attempt = 0;
    let wasUp: boolean | null = null;
    const upNow = (up: boolean): void => {
      if (wasUp !== null && wasUp !== up && !stopped) onChanged("server");
      wasUp = up;
    };
    const opened = (): void => {
      attempt = 0;
      upNow(true);
    };
    const run = async (): Promise<void> => {
      while (!stopped) {
        let path: string | null = null;
        try {
          path = `${await this.base()}/events`;
        } catch {
          // Unreachable, or not served: the wait below, then asked again.
        }
        if (path !== null && !stopped) {
          await followEvents(this.client.address, path, {
            opened,
            changed: onChanged,
            holding: (request) => {
              current = request;
              if (stopped) request.destroy();
            },
          });
          current = null;
        }
        if (stopped) return;
        upNow(false);
        const wait = EVENTS_RETRY_MS[Math.min(attempt, EVENTS_RETRY_MS.length - 1)] ?? 5_000;
        attempt += 1;
        await new Promise((resolve) => setTimeout(resolve, wait));
      }
    };
    void run();
    return () => {
      stopped = true;
      current?.destroy();
    };
  }
}

/**
 * Parse `text/event-stream` blocks out of what has arrived so far, calling `changed` for
 * each `changed` event this build knows the kind of, and answer what is left over (an
 * incomplete block). Electron-free and pure, so the framing has its own check.
 */
export function drainEvents(buffer: string, changed: (kind: StoreChangeKind) => void): string {
  let rest = buffer.replace(/\r\n/g, "\n");
  let end = rest.indexOf("\n\n");
  while (end >= 0) {
    const block = rest.slice(0, end);
    rest = rest.slice(end + 2);
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trim());
    }
    if (event === "changed") {
      const kind = changedKind(data.join("\n"));
      if (kind !== null) changed(kind);
    }
    end = rest.indexOf("\n\n");
  }
  return rest;
}

/**
 * One life of a project's change stream, over `node:http`/`node:https` like every
 * request: it resolves -- never rejects, never throws -- when the stream ends for any
 * reason at all: refused, a non-2xx answer, the server stopped or reset mid-stream, a
 * socket error, or `destroy()` on the request `holding` hands out. The caller decides
 * what the end means. Each listener stays attached for the socket's whole life, so an
 * error after the end is heard and ignored.
 */
function followEvents(
  address: ServerAddress,
  path: string,
  on: {
    opened: () => void;
    changed: (kind: StoreChangeKind) => void;
    holding: (request: ClientRequest) => void;
  },
): Promise<void> {
  return new Promise((resolve) => {
    let ended = false;
    const end = (): void => {
      if (ended) return;
      ended = true;
      resolve();
    };
    const url = urlFor(address, path);
    const onResponse = (response: IncomingMessage): void => {
      response.on("error", end);
      response.on("end", end);
      response.on("close", end);
      const status = response.statusCode ?? 0;
      if (status < 200 || status > 299) {
        response.resume();
        return;
      }
      on.opened();
      const decoder = new StringDecoder("utf8");
      let buffer = "";
      response.on("data", (chunk: Buffer) => {
        if (ended) return;
        buffer = drainEvents(buffer + decoder.write(chunk), on.changed);
      });
    };
    let request: ClientRequest;
    try {
      const options = { method: "GET", headers: { Accept: "text/event-stream" }, agent: false };
      request =
        url.protocol === "https:"
          ? httpsRequest(url, options, onResponse)
          : httpRequest(url, options, onResponse);
    } catch {
      end();
      return;
    }
    request.on("error", end);
    request.on("close", end);
    on.holding(request);
    request.end();
  });
}

/** One path under `ideas/`, as the listing reports it. */
export interface IdeaEntry {
  path: string;
  type: "file" | "dir" | "symlink";
  size: number | null;
  mtimeNs: number | null;
  /** When it came to be, in nanoseconds since the epoch (KYO-467); null from an older server. */
  createdNs: number | null;
  etag: string | null;
}

export type FileRead =
  | { ok: true; text: string; etag: string | null; missing: false }
  | { ok: false; missing: boolean; problem: string };

export type FileWrite =
  | { ok: true; etag: string | null; status: number }
  | { ok: false; status: number; etag: string | null; problem: string };

// ------------------------------------------------------------- the clients held

const clients = new Map<string, ServeClient>();

/**
 * The one client per address this process holds. `local` marks this machine's own
 * server, whose "not running" is the app's one waiting state.
 */
export function clientFor(address: ServerAddress, local = false): ServeClient {
  const key = `${address.host.toLowerCase()}:${address.port}`;
  let held = clients.get(key);
  if (held === undefined || held.local !== local) {
    held = new ServeClient(address, local);
    clients.set(key, held);
  }
  return held;
}
