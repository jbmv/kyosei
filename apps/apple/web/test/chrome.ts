// A page of the renderer in a headless Chrome, for the tests that need a web view: keys, the
// box's own undo history and what is painted in the chat. Not a test file itself. A machine
// with no Chrome gets `null` from `openPage`, and the tests that asked skip.
import { spawn, type ChildProcess } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const CHROMES = [
  process.env.CHROME_PATH ?? "",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
];

/** Command, as the Mac app's web view reads the key. */
export const META = 4;
export const SHIFT = 8;

export interface Page {
  /** Runs an expression in the page and returns its value, which must be JSON. */
  run<T = unknown>(expression: string): Promise<T>;
  /** Types text into the focused element as keys would, one input event for each character. */
  type(text: string): Promise<void>;
  /**
   * Presses a key. `command` is the editing command the system binds to it, which Chrome runs
   * as the key's default action: a page that cancels the keydown cancels the command too.
   */
  press(key: string, modifiers?: number, command?: string): Promise<void>;
  /** Loads the page again with nothing kept from before. */
  reload(): Promise<void>;
  close(): Promise<void>;
}

/** Builds the renderer into a temporary folder and opens its page with a stub of the app. */
export async function openPage(): Promise<Page | null> {
  const chrome = CHROMES.find((path) => path && existsSync(path));
  if (!chrome) return null;
  const folder = mkdtempSync(join(tmpdir(), "kyosei-renderer-"));
  const site = join(folder, "site");
  cpSync(fileURLToPath(new URL("../static", import.meta.url)), site, { recursive: true });
  await build({
    entryPoints: [fileURLToPath(new URL("../src/chat.ts", import.meta.url))],
    bundle: true, format: "iife", target: "safari18", outfile: join(site, "chat.js"), logLevel: "silent",
  });
  const child: ChildProcess = spawn(chrome, [
    "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${join(folder, "profile")}`,
    "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--window-size=1200,900", "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  const close = async () => {
    child.kill("SIGKILL");
    await new Promise((done) => (child.exitCode !== null || child.signalCode ? done(null) : child.once("exit", done)));
    rmSync(folder, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  };
  try {
    const address = await new Promise<string>((found, failed) => {
      let said = "";
      const timer = setTimeout(() => failed(new Error(`Chrome did not start: ${said}`)), 20000);
      child.stderr!.on("data", (chunk: Buffer) => {
        said += chunk.toString();
        const match = said.match(/DevTools listening on (ws:\/\/\S+)/);
        if (!match) return;
        clearTimeout(timer);
        found(match[1]);
      });
      child.once("exit", () => failed(new Error(`Chrome exited: ${said}`)));
    });
    const socket = new WebSocket(address);
    await new Promise((opened, failed) => {
      socket.addEventListener("open", opened, { once: true });
      socket.addEventListener("error", failed, { once: true });
    });
    let next = 1;
    const waiting = new Map<number, { done: (value: any) => void; failed: (error: Error) => void }>();
    const loaded: (() => void)[] = [];
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (message.method === "Page.loadEventFired") loaded.splice(0).forEach((done) => done());
      const call = waiting.get(message.id);
      if (!call) return;
      waiting.delete(message.id);
      if (message.error) call.failed(new Error(message.error.message));
      else call.done(message.result);
    });
    const send = (method: string, params: object = {}, sessionId?: string) =>
      new Promise<any>((done, failed) => {
        const id = next++;
        waiting.set(id, { done, failed });
        socket.send(JSON.stringify({ id, method, params, sessionId }));
      });
    const { targetId } = await send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
    const call = (method: string, params: object = {}) => send(method, params, sessionId);
    await call("Page.enable");
    // The app's handlers, which the page posts to; what it posted is kept for a test to read.
    await call("Page.addScriptToEvaluateOnNewDocument", {
      source: `window.posted = []; window.webkit = { messageHandlers: new Proxy({}, { get: (_, name) =>
        ({ postMessage: (body) => window.posted.push({ name, body }) }) }) };`,
    });
    const reload = async () => {
      const load = new Promise<void>((done) => loaded.push(done));
      await call("Page.navigate", { url: pathToFileURL(join(site, "chat.html")).href });
      await load;
    };
    await reload();
    const run = async <T>(expression: string): Promise<T> => {
      const answer = await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
      if (answer.exceptionDetails) {
        throw new Error(answer.exceptionDetails.exception?.description ?? answer.exceptionDetails.text);
      }
      return answer.result.value as T;
    };
    const press = async (key: string, modifiers = 0, command?: string) => {
      const code = key.length === 1 ? `Key${key.toUpperCase()}` : key;
      const windowsVirtualKeyCode = key.length === 1 ? key.toUpperCase().charCodeAt(0)
        : ({ Backspace: 8, Enter: 13, Escape: 27 } as Record<string, number>)[key] ?? 0;
      const event = { key, code, modifiers, windowsVirtualKeyCode };
      await call("Input.dispatchKeyEvent", {
        ...event, type: "rawKeyDown", ...(command ? { commands: [command] } : {}),
      });
      await call("Input.dispatchKeyEvent", { ...event, type: "keyUp" });
    };
    const type = async (text: string) => {
      for (const letter of text) await call("Input.insertText", { text: letter });
    };
    return {
      run, type, press, close,
      reload: async () => {
        await run("localStorage.clear()");
        await reload();
      },
    };
  } catch (error) {
    await close();
    throw error;
  }
}
