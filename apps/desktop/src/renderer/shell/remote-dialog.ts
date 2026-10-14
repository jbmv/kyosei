// "Connect to a dev box" (KYO-370, KYO-374, KYO-459): an address, then which project.
//
// Another machine's projects are read through ITS kyo serve, reached at an address --
// `https://<machine>.<tailnet>.ts.net`, the name Tailscale Serve gives it
// (docs/reference/topics/serve-and-desktop.md, "Reaching it from another machine") -- with
// no password, no token and no certificate
// to trust: the tailnet's access rule is the boundary, and the certificate is one the
// system already trusts. So the dialog asks for the address, and the SSH host
// KYO-460's commands go to, and nothing else.
//
// It connects in two steps. "Connect" asks the address which projects it serves
// (`bridge.probeRemote`): a refusal -- nothing answered, or kyo serve's own sentence,
// naming the address (KYO-370.c5) -- is shown where the reader typed, and the dialog
// stays open with what they typed rather than closing on an empty store. An answer
// lists the projects; "Open" then moves THIS window onto the chosen one through
// `bridge.connectRemote`, the same call the switcher's remembered rows make. A success
// closes the dialog; the `projectChanged` push does the rest, as a local switch does.
//
// A project is not ADDED here: kyo serve adds none (KYO-452). A server that lists no
// project says how to add one there -- `kyo projects add` over SSH, or New Project….
//
// EVERY REFUSAL IS A SENTENCE (KYO-387). The main process returns refusals rather than
// throwing them; a call that throws anyway is shown by its own message, never behind
// Electron's "Error invoking remote method…: Error:" frame.
//
// THE SSH HOST (KYO-460). A dev box's entry also carries the SSH host its commands go
// to -- New project and Start controller run `kyo` there over the person's own SSH. It
// is the address's host name unless this field says otherwise (`me@devbox`, an alias
// from `~/.ssh/config`), and it is kept once the connection has been made.
//
// Reuses the prompt classes (`.prompt-*`, KYO-250) rather than adding a style of its
// own. KYO-459's standard: conventional controls, no polish pass.

import type {
  KyoseiBridge,
  ProjectChoice,
  ProjectEntry,
  RemoteProbeFacts,
} from "../../shared/protocol";
import { el } from "../dom";

export interface RemoteDialogOptions {
  bridge: KyoseiBridge;
  /** The window moved: the dialog has closed. */
  onConnected(choice: ProjectChoice): void;
  onCancel(): void;
  /** What to start the address on: the last server connected to, when there is one. */
  initial?: { address?: string };
  /** Open the chosen project in a NEW window (the New Window chooser) rather than here. */
  newWindow?: boolean;
}

/** The address a Tailscale Serve name takes, as the placeholder. */
const ADDRESS_HINT = "https://<machine>.<tailnet>.ts.net";

/**
 * What a rejected IPC call says, without the frame Electron puts around it: the
 * sentence the main process threw, alone (KYO-387).
 */
export function saidBy(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  return text
    .replace(/^Error invoking remote method '[^']*':\s*/, "")
    .replace(/^(?:[A-Za-z]*(?:Error|Refusal):\s*)+/, "");
}

/** The last path segment, for a project the server could not read a prefix from. */
function baseName(root: string): string {
  const parts = root.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || root;
}

export function openRemoteDialog(host: HTMLElement, options: RemoteDialogOptions): void {
  const { bridge } = options;
  const addressInput = el("input", {
    class: "prompt-input",
    data: { region: "remote-address" },
    attrs: { type: "text", placeholder: ADDRESS_HINT, autocapitalize: "off", spellcheck: "false" },
  });
  addressInput.value = options.initial?.address ?? "";
  const sshInput = el("input", {
    class: "prompt-input",
    data: { region: "remote-ssh-host" },
    attrs: {
      type: "text",
      placeholder: "same as the address's host; for New project and Start orchestrator",
      autocapitalize: "off",
      spellcheck: "false",
    },
  });
  const projectSelect = el("select", {
    class: "prompt-input",
    data: { region: "remote-project" },
  });
  const projectField = el("label", { class: "remote-field", data: { field: "project" } }, [
    el("span", { text: "Project" }),
    projectSelect,
  ]);
  projectField.hidden = true;
  const problem = el("div", {
    class: "prompt-problem",
    data: { region: "remote-problem", state: "none" },
  });
  const connectButton = el("button", {
    class: "prompt-button prompt-ok",
    data: { region: "remote-connect", action: "remote-connect" },
    attrs: { type: "button" },
    text: "Connect",
  });
  const cancelButton = el("button", {
    class: "prompt-button",
    data: { region: "remote-cancel", action: "remote-cancel" },
    attrs: { type: "button" },
    text: "Cancel",
  });
  const dialog = el(
    "div",
    {
      class: "prompt-dialog remote-dialog",
      data: { region: "remote-dialog" },
      attrs: { role: "dialog" },
    },
    [
      el("div", {
        class: "prompt-question",
        text: options.newWindow === true ? "New window on a dev box" : "Connect to a dev box",
      }),
      el("div", {
        class: "remote-hint",
        text:
          "The other machine's kyo serve, at the address Tailscale Serve gives it " +
          "(kyo serve --install --allow-host <name> --apply and tailscale serve --bg 47370 there).",
      }),
      el("label", { class: "remote-field", data: { field: "address" } }, [
        el("span", { text: "Address" }),
        addressInput,
      ]),
      el("label", { class: "remote-field", data: { field: "ssh-host" } }, [
        el("span", { text: "SSH host" }),
        sshInput,
      ]),
      projectField,
      problem,
      el("div", { class: "prompt-actions" }, [cancelButton, connectButton]),
    ],
  );
  const overlay = el(
    "div",
    // `overlay`: the claude tab steps aside while this is open (`overlays.ts`).
    { class: "prompt-overlay", data: { region: "remote-overlay", overlay: "remote" } },
    [dialog],
  );
  host.append(overlay);

  let busy = false;
  /** The address the server's list answered for, once it has. */
  let probed: { facts: RemoteProbeFacts; address: string } | null = null;
  const close = (): void => {
    overlay.remove();
  };
  const cancel = (): void => {
    if (busy) return;
    close();
    options.onCancel();
  };
  cancelButton.addEventListener("click", cancel);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) cancel();
  });
  for (const input of [addressInput, sshInput, projectSelect] as HTMLElement[]) {
    input.addEventListener("keydown", (event: KeyboardEvent) => {
      if (event.key === "Enter") void proceed();
      if (event.key === "Escape") cancel();
    });
  }
  // Editing the address invalidates the answer: the list is that address's.
  addressInput.addEventListener("input", () => {
    if (probed === null) return;
    probed = null;
    projectField.hidden = true;
    connectButton.textContent = "Connect";
  });
  connectButton.addEventListener("click", () => void proceed());

  async function proceed(): Promise<void> {
    if (busy) return;
    const address = addressInput.value.trim();
    if (address === "") {
      refuse(`The dev box needs an address, such as ${ADDRESS_HINT}.`);
      return;
    }
    if (probed === null || probed.address !== address) {
      await probe(address);
      return;
    }
    if (projectField.hidden || projectSelect.value === "") {
      refuse("Choose a project.");
      return;
    }
    await connect(address, projectSelect.value);
  }

  /** Step one: is it there, and which projects does it serve. */
  async function probe(address: string): Promise<void> {
    busy = true;
    connectButton.setAttribute("aria-disabled", "true");
    problem.dataset.state = "pending";
    problem.textContent = `Connecting to ${address}…`;
    let facts: RemoteProbeFacts;
    try {
      facts = await bridge.probeRemote({ address });
    } catch (error) {
      done();
      return refuse(saidBy(error));
    }
    done();
    if (!facts.ok) return refuse(`Could not open ${address}: ${facts.problem}`);
    const projects: ProjectEntry[] = facts.recents;
    if (projects.length === 0) {
      return refuse(
        `The kyo serve at ${address} serves no project yet. Add one there with ` +
          "`kyo projects add <path>` (over SSH), or make one with New Project….",
      );
    }
    probed = { facts, address };
    problem.dataset.state = "none";
    problem.textContent = "";
    connectButton.textContent = "Open";
    projectSelect.replaceChildren(
      ...projects.map((entry) =>
        el("option", {
          text: `${entry.project ?? baseName(entry.root)} — ${entry.root}`,
          attrs: { value: entry.root },
        }),
      ),
    );
    projectSelect.value = projects[0]?.root ?? "";
    projectField.hidden = false;
    projectSelect.focus();
  }

  /** Step two: move this window (or a new one) onto the chosen project. */
  async function connect(address: string, root: string): Promise<void> {
    busy = true;
    connectButton.setAttribute("aria-disabled", "true");
    problem.dataset.state = "pending";
    problem.textContent = `Opening ${root} on ${address}…`;
    let choice: ProjectChoice;
    try {
      choice = await bridge.connectRemote({
        address,
        root,
        newWindow: options.newWindow === true,
      });
    } catch (error) {
      done();
      return refuse(saidBy(error));
    }
    done();
    if (choice.refused !== null) {
      return refuse(`Could not open ${choice.refused.root}: ${choice.refused.problem}`);
    }
    // The entry exists now, so it can carry the SSH host (KYO-460). A refusal is said
    // where the reader typed it, and the dialog stays: the connection itself is made.
    const sshHost = sshInput.value.trim();
    const server = choice.project.remote;
    if (sshHost !== "" && server !== null) {
      const kept = await bridge
        .setSshHost({ host: server.host, port: server.port }, sshHost)
        .catch((error: unknown) => ({ ok: false, problem: saidBy(error), sshHost: "" }));
      if (!kept.ok)
        return refuse(`Connected, but could not keep the SSH host: ${kept.problem ?? ""}`);
    }
    close();
    options.onConnected(choice);
  }

  function done(): void {
    busy = false;
    connectButton.removeAttribute("aria-disabled");
  }

  function refuse(text: string): void {
    problem.dataset.state = "refused";
    problem.textContent = text;
  }

  addressInput.focus();
}
