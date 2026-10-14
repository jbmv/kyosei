// Renderer entry point. One HTML document serves both window kinds: the main process
// tells the renderer, on first contact, whether it is the shell or a single tab that
// has been moved into a window of its own.

import type { AppState, KyoseiBridge } from "../shared/protocol";
import { el } from "./dom";
import { buildShell } from "./shell/shell";
import { tabKind, tabKindNames } from "./tabs/registry";

declare global {
  interface Window {
    kyosei: KyoseiBridge;
  }
}

/** How often the status bar re-asks `kyo` which side of the db/export seam moved. */
const CURRENCY_POLL_MS = 15_000;

/**
 * This app draws its own tooltips, and Chromium draws none.
 *
 * `dom.ts` writes `data-tip` and never `title` (KYO-122.c2): Chromium draws its own
 * tooltip from `title` on its own schedule, so a surface that also draws one gets two.
 * A popped-out tab gets this too -- its controls are the same controls.
 */
function installTooltips(): void {
  const tip = el("div", { class: "tooltip", data: { region: "tooltip" } });
  tip.hidden = true;
  document.body.append(tip);
  let timer: number | null = null;

  const hide = (): void => {
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
    tip.hidden = true;
  };

  document.addEventListener("pointerover", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return hide();
    const owner = target.closest<HTMLElement>("[data-tip]");
    if (owner === null) return hide();
    const text = owner.getAttribute("data-tip") ?? "";
    if (text === "") return hide();
    if (timer !== null) window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      const rect = owner.getBoundingClientRect();
      tip.textContent = text;
      tip.hidden = false;
      const width = tip.getBoundingClientRect().width;
      tip.style.left = `${Math.max(6, Math.min(window.innerWidth - width - 6, rect.left))}px`;
      tip.style.top = `${rect.bottom + 6}px`;
    }, 320);
  });
  document.addEventListener("pointerdown", hide);
  window.addEventListener("blur", hide);
}

/**
 * A window holding one tab and nothing else (KYO-232.c2's pop-out).
 *
 * "Nothing about a tab changes when it is moved" (KYO-227): this builds the SAME kind
 * with the same argument through the same registry, so what the reader gets is the tab
 * they had, in a window of its own -- and for the terminal, on the same pty, because
 * the session id moved with the window (KYO-124).
 */
function buildPaneWindow(mount: HTMLElement, state: AppState, bridge: KyoseiBridge): void {
  if (state.self.kind !== "pane") return;
  const asked = state.self.context.tab;
  const kind = asked === undefined ? undefined : tabKind(asked.kind);
  if (asked === undefined || kind === undefined) {
    mount.replaceChildren(
      el("div", { class: "empty", data: { region: "pane-problem" } }, [
        el("p", { text: "This window was opened for a tab kind this build does not have." }),
        el("p", { class: "muted", text: `asked for: ${asked?.kind ?? "(nothing)"}` }),
      ]),
    );
    return;
  }
  const body = el("div", {
    class: "tab-body pane-body",
    data: { region: "tab-body", tabKind: asked.kind, tabArg: asked.arg },
  });
  const instance = kind.open({
    arg: asked.arg,
    bridge,
    // A popped-out window has no columns, so a link inside it opens in the shell it
    // came from: the tab is somewhere else, the workspace is still over there.
    openTab: () => undefined,
    closeSelf: () => undefined,
    navigate: () => undefined,
    followLink: (url) => bridge.followLink(url),
    setTitle: () => {
      document.title = `Kyosei — ${instance.title()}`;
    },
  });
  mount.replaceChildren(body);
  instance.render(body);
  document.title = `Kyosei — ${instance.title()}`;
  window.addEventListener("beforeunload", () => instance.close());
}

async function start(): Promise<void> {
  const bridge = window.kyosei;
  const mount = document.getElementById("app");
  if (!mount) throw new Error("renderer: #app is missing from index.html");

  const state = await bridge.getState();
  if (!state) throw new Error("renderer: the main process did not recognise this window");

  installTooltips();
  document.body.dataset.tabKinds = tabKindNames().join(",");

  if (state.self.kind === "pane") {
    document.body.dataset.view = "pane";
    document.body.dataset.pane = state.self.pane;
    buildPaneWindow(mount, state, bridge);
    document.body.dataset.ready = "true";
    return;
  }

  const shell = buildShell(bridge);
  mount.replaceChildren(shell.root);
  document.body.dataset.view = "shell";
  shell.render(state);
  document.title = "Kyosei";

  await shell.start();

  bridge.onState((next: AppState) => shell.render(next));

  // The currency is the one fact that changes without this app doing anything -- a
  // `kyo` run in the terminal tab below, or in any other checkout, moves it.
  const poll = window.setInterval(() => {
    void bridge
      .getCurrency()
      .then((currency) => {
        const line = shell.root.querySelector<HTMLElement>('[data-region="currency"]');
        if (line === null) return;
        line.textContent = currency.says;
        line.dataset.state = currency.state;
      })
      .catch(() => window.clearInterval(poll));
  }, CURRENCY_POLL_MS);
  window.addEventListener("beforeunload", () => {
    window.clearInterval(poll);
    shell.dispose();
  });

  // The checks wait on this rather than on a timeout.
  document.body.dataset.ready = "true";
  // And the main process: a hidden spare is usable from this moment (KYO-261).
  bridge.shellBooted();
}

void start().catch((error: unknown) => {
  document.body.dataset.error = String(error);
  console.error(error);
});
