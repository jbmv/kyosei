// New project (KYO-460): choose a machine, then a folder in a plain directory picker,
// and the app runs `mkdir -p`, `kyo init` (which registers the project, KYO-455) and,
// when asked, `kyo controller start` there.
//
// THE MACHINE. This one, or a dev box the app has connected to before -- each dev box
// with the SSH host its commands go to (`ssh.ts`), shown and editable here because it
// is the one fact about the entry the person may need to change (`me@devbox`, an alias
// from `~/.ssh/config`). It is the entry's host name until someone sets another.
//
// A DIRECTORY PICKER, NOT A TERMINAL: the dialog looks like a normal directory picker.
// Breadcrumbs, a folder list, double-click
// to descend, New Folder, Choose. The list is fed by a listing command run on that
// machine, over SSH for a dev box, and its text is never shown: only folder names
// reach this dialog. What IS shown verbatim is a failure -- whatever ssh or the far
// end printed on stderr -- because that is the remedy's first clue.
//
// WHERE THE STORE LIVES (KYO-573, setup decision B1): two choices, each labelled with
// its fixed text from `STORE_LOCATION_LABELS`, the split store selected by
// default -- `kyo init --defaults` -- and the store inside the code repository --
// `kyo init --in-repo` -- the other. Costly to undo (KYO-557), so it is asked, not taken.
//
// CHOOSE picks the selected folder, or the folder being shown when none is selected,
// as a Mac's own "Choose" does. The project is made there and the dialog then says
// what was done, in sentences built from `kyo`'s JSON, and offers to open it.
//
// Reuses the prompt classes (`.prompt-*`, `.remote-field`) the connect dialog uses:
// functional and conventional, no polish pass.

import {
  DEFAULT_STORE_LOCATION,
  type FolderListing,
  type KyoseiBridge,
  type NewProjectOutcome,
  type ProjectServerEntry,
  type RemoteFacts,
  STORE_LOCATION_LABELS,
  type StoreLocation,
} from "../../shared/protocol";
import { el } from "../dom";
import { icon } from "../icons";
import { saidBy } from "./remote-dialog";

export interface NewProjectDialogOptions {
  bridge: KyoseiBridge;
  /** The dialog closed, with or without a project made. */
  onClose(): void;
  /** Open the new project, in a new window. Returns a refusal sentence, or null. */
  onOpen(root: string, server: RemoteFacts | null): Promise<string | null>;
}

/** `name` inside `parent`, as the main process builds it too. */
function childOf(parent: string, name: string): string {
  return parent === "/" ? `/${name}` : `${parent.replace(/\/+$/, "")}/${name}`;
}

/** `/`, then each folder down to `path`. */
function crumbsOf(path: string): { label: string; path: string }[] {
  const crumbs = [{ label: "/", path: "/" }];
  let at = "";
  for (const part of path.split("/").filter((segment) => segment !== "")) {
    at = `${at}/${part}`;
    crumbs.push({ label: part, path: at });
  }
  return crumbs;
}

/** The sentences a made project is reported in. Exported for the checks. */
export function outcomeLines(outcome: NewProjectOutcome): string[] {
  const what = outcome.prefix === null ? "a Kyosei project" : `project ${outcome.prefix}`;
  const lines = [`Made ${what} in ${outcome.root} on ${outcome.machine}.`];
  lines.push(
    outcome.registered
      ? `It is in ${outcome.machine}'s project registry${outcome.name === null ? "" : ` as ${outcome.name}`}.`
      : `kyo init did not say it registered it on ${outcome.machine}.`,
  );
  const started = outcome.controller;
  if (started !== null) {
    lines.push(
      `Orchestrator session ${started.session} is running there` +
        `${started.pid === null ? "" : ` (pid ${started.pid})`}` +
        `${started.remoteControlName === null ? "" : `; claude.ai lists it as ${started.remoteControlName}`}.`,
    );
    if (started.attach !== null) lines.push(`Attach to it there with: ${started.attach}`);
  }
  return lines;
}

export function openNewProjectDialog(host: HTMLElement, options: NewProjectDialogOptions): void {
  const { bridge } = options;
  const serverSelect = el("select", {
    class: "prompt-input",
    data: { region: "new-project-server" },
  });
  const sshInput = el("input", {
    class: "prompt-input",
    data: { region: "new-project-ssh-host" },
    attrs: { type: "text", autocapitalize: "off", spellcheck: "false" },
  });
  const sshField = el("label", { class: "remote-field", data: { field: "ssh-host" } }, [
    el("span", { text: "SSH host" }),
    sshInput,
  ]);
  sshField.hidden = true;
  const crumbs = el("nav", { class: "picker-crumbs", data: { region: "new-project-crumbs" } });
  const list = el("div", {
    class: "picker-list",
    data: { region: "new-project-folders" },
    attrs: { role: "listbox", tabindex: "0", "aria-label": "Folders" },
  });
  const folderName = el("input", {
    class: "prompt-input",
    data: { region: "new-project-folder-name" },
    attrs: { type: "text", placeholder: "untitled folder", spellcheck: "false" },
  });
  const folderCreate = el("button", {
    class: "prompt-button",
    data: { region: "new-project-folder-create" },
    attrs: { type: "button" },
    text: "Create",
  });
  const folderRow = el("div", { class: "picker-new-folder" }, [folderName, folderCreate]);
  folderRow.hidden = true;
  const chosenLine = el("div", { class: "remote-hint", data: { region: "new-project-chosen" } });
  const storeChoices = (["split", "in-repo"] as const).map((location) => {
    const radio = el("input", {
      data: { region: "new-project-store-choice", location },
      attrs: { type: "radio", name: "new-project-store", value: location },
    });
    radio.checked = location === DEFAULT_STORE_LOCATION;
    return { location, radio };
  });
  const storeField = el(
    "fieldset",
    { class: "picker-store", data: { region: "new-project-store" } },
    [
      el("legend", { text: "Where the Kyosei store lives" }),
      ...storeChoices.map(({ location, radio }) =>
        el("label", { class: "picker-check", data: { location } }, [
          radio,
          el("span", { text: STORE_LOCATION_LABELS[location] }),
        ]),
      ),
    ],
  );
  function storeLocation(): StoreLocation {
    return storeChoices.find(({ radio }) => radio.checked)?.location ?? DEFAULT_STORE_LOCATION;
  }
  const startBox = el("input", {
    data: { region: "new-project-start" },
    attrs: { type: "checkbox" },
  });
  startBox.checked = true;
  const startLabel = el("label", { class: "picker-check" }, [
    startBox,
    el("span", { text: "Start an orchestrator session there (kyo controller start)" }),
  ]);
  const problem = el("div", {
    class: "prompt-problem",
    data: { region: "new-project-problem", state: "none" },
  });
  const newFolderButton = el("button", {
    class: "prompt-button",
    data: { region: "new-project-new-folder" },
    attrs: { type: "button" },
    text: "New Folder",
  });
  const cancelButton = el("button", {
    class: "prompt-button",
    data: { region: "new-project-cancel" },
    attrs: { type: "button" },
    text: "Cancel",
  });
  const chooseButton = el("button", {
    class: "prompt-button prompt-ok",
    data: { region: "new-project-choose" },
    attrs: { type: "button" },
    text: "Choose",
  });
  const picker = el("div", { class: "picker", data: { region: "new-project-picker" } }, [
    el("label", { class: "remote-field", data: { field: "server" } }, [
      el("span", { text: "Machine" }),
      serverSelect,
    ]),
    sshField,
    crumbs,
    list,
    folderRow,
    chosenLine,
    storeField,
    startLabel,
    problem,
    el("div", { class: "prompt-actions" }, [
      newFolderButton,
      el("span", { class: "grow" }),
      cancelButton,
      chooseButton,
    ]),
  ]);
  const result = el("div", { class: "picker-result", data: { region: "new-project-result" } });
  result.hidden = true;
  const dialog = el(
    "div",
    {
      class: "prompt-dialog remote-dialog new-project-dialog",
      data: { region: "new-project-dialog" },
      attrs: { role: "dialog" },
    },
    [
      el("div", { class: "prompt-question", text: "New project" }),
      el("div", {
        class: "remote-hint",
        text:
          "Choose the folder the project lives in. The app runs mkdir, kyo init and kyo " +
          "controller start there -- on another machine, over your own SSH.",
      }),
      picker,
      result,
    ],
  );
  const overlay = el(
    "div",
    // `overlay`: the claude tab steps aside while this is open (`overlays.ts`).
    { class: "prompt-overlay", data: { region: "new-project-overlay", overlay: "new-project" } },
    [dialog],
  );
  host.append(overlay);

  let servers: ProjectServerEntry[] = [];
  /** The folder being shown, once one has been listed. */
  let current: string | null = null;
  let selected: string | null = null;
  let busy = false;
  /** Which listing is the latest asked for: an older answer arriving late is dropped. */
  let asked = 0;

  const close = (): void => {
    overlay.remove();
    options.onClose();
  };
  const cancel = (): void => {
    if (busy) return;
    close();
  };
  cancelButton.addEventListener("click", cancel);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) cancel();
  });
  overlay.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (!folderRow.hidden) {
        folderRow.hidden = true;
        list.focus();
        return;
      }
      cancel();
    }
  });

  function server(): ProjectServerEntry | null {
    return servers[Number(serverSelect.value)] ?? null;
  }

  function say(text: string, state: "refused" | "pending" | "none"): void {
    problem.dataset.state = state;
    problem.textContent = text;
  }

  function chosenPath(): string | null {
    if (current === null) return null;
    return selected === null ? current : childOf(current, selected);
  }

  function reflectChoice(): void {
    const path = chosenPath();
    chosenLine.textContent = path === null ? "" : `Project folder: ${path}`;
    chooseButton.toggleAttribute("disabled", path === null || busy);
    newFolderButton.toggleAttribute("disabled", current === null || busy);
  }

  function select(name: string | null): void {
    selected = name;
    for (const row of list.querySelectorAll<HTMLElement>("[data-folder]")) {
      const on = row.dataset.folder === name;
      row.dataset.selected = String(on);
      row.setAttribute("aria-selected", String(on));
      if (on) row.scrollIntoView({ block: "nearest" });
    }
    reflectChoice();
  }

  function draw(listing: FolderListing): void {
    current = listing.path;
    selected = null;
    crumbs.replaceChildren(
      ...crumbsOf(listing.path ?? "/").map((crumb, index, all) =>
        el("button", {
          class: "picker-crumb",
          data: { region: "new-project-crumb", path: crumb.path },
          attrs: { type: "button" },
          text: index === 0 ? "/" : crumb.label,
          on: {
            click: () => {
              if (index < all.length - 1) void show(crumb.path);
            },
          },
        }),
      ),
    );
    if (listing.folders.length === 0) {
      list.replaceChildren(el("div", { class: "picker-empty", text: "No folders here" }));
    } else {
      list.replaceChildren(
        ...listing.folders.map((name) =>
          el(
            "div",
            {
              class: "picker-row",
              data: { folder: name, selected: "false" },
              attrs: { role: "option", "aria-selected": "false" },
              on: {
                click: () => select(name),
                dblclick: () => {
                  if (current !== null) void show(childOf(current, name));
                },
              },
            },
            [icon("folder", { size: 14 }), el("span", { text: name })],
          ),
        ),
      );
    }
    reflectChoice();
  }

  /** List a folder on the chosen machine; its home when `path` is null. */
  async function show(path: string | null): Promise<void> {
    const entry = server();
    if (entry === null) return;
    const ticket = ++asked;
    say(`Listing ${path ?? "the home folder"} on ${entry.sshHost ?? "this machine"}…`, "pending");
    list.dataset.state = "loading";
    let listing: FolderListing;
    try {
      listing = await bridge.listFolder(entry.server, path);
    } catch (error) {
      if (ticket === asked) say(saidBy(error), "refused");
      return;
    }
    if (ticket !== asked) return;
    list.dataset.state = listing.ok ? "listed" : "failed";
    if (!listing.ok) {
      say(listing.problem ?? `could not list ${path ?? "the home folder"}`, "refused");
      if (current === null) {
        crumbs.replaceChildren();
        list.replaceChildren();
        reflectChoice();
      }
      return;
    }
    say("", "none");
    draw(listing);
  }

  serverSelect.addEventListener("change", () => {
    const entry = server();
    sshField.hidden = entry?.server == null;
    sshInput.value = entry?.sshHost ?? "";
    sshInput.placeholder = entry?.server?.host ?? "";
    current = null;
    void show(null);
  });

  /** Keep an edited SSH host on the entry, then list again through it. */
  async function saveSshHost(): Promise<void> {
    const entry = server();
    if (entry === null || entry.server === null) return;
    if (sshInput.value.trim() === (entry.sshHost ?? "")) return;
    const outcome = await bridge.setSshHost(entry.server, sshInput.value);
    if (!outcome.ok) {
      say(`Could not use that SSH host: ${outcome.problem ?? ""}`, "refused");
      return;
    }
    entry.sshHost = outcome.sshHost;
    sshInput.value = outcome.sshHost;
    void show(current);
  }
  sshInput.addEventListener("change", () => void saveSshHost());
  sshInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") void saveSshHost();
  });

  list.addEventListener("keydown", (event) => {
    const rows = [...list.querySelectorAll<HTMLElement>("[data-folder]")];
    const at = rows.findIndex((row) => row.dataset.folder === selected);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next =
        rows[Math.max(0, Math.min(rows.length - 1, at + (event.key === "ArrowDown" ? 1 : -1)))];
      if (next !== undefined) select(next.dataset.folder ?? null);
    } else if (event.key === "Enter" && selected !== null && current !== null) {
      event.preventDefault();
      void show(childOf(current, selected));
    } else if (
      (event.key === "Backspace" || (event.key === "ArrowUp" && event.metaKey)) &&
      current !== null &&
      current !== "/"
    ) {
      event.preventDefault();
      void show(current.replace(/\/[^/]*$/, "") || "/");
    }
  });

  newFolderButton.addEventListener("click", () => {
    if (current === null) return;
    folderRow.hidden = false;
    folderName.value = "";
    folderName.focus();
  });

  async function makeFolder(): Promise<void> {
    const entry = server();
    const name = folderName.value.trim();
    if (entry === null || current === null) return;
    if (name === "" || name.includes("/")) {
      say(
        name === "" ? "The folder needs a name." : "A folder name cannot contain '/'.",
        "refused",
      );
      return;
    }
    const where = current;
    const made = await bridge
      .makeFolder(entry.server, childOf(where, name))
      .catch((error: unknown) => ({ ok: false, problem: saidBy(error) }));
    if (!made.ok) {
      say(`Could not make ${name}: ${made.problem ?? ""}`, "refused");
      return;
    }
    folderRow.hidden = true;
    await show(where);
    select(name);
    list.focus();
  }
  folderCreate.addEventListener("click", () => void makeFolder());
  folderName.addEventListener("keydown", (event) => {
    if (event.key === "Enter") void makeFolder();
  });

  async function choose(): Promise<void> {
    const entry = server();
    const root = chosenPath();
    if (busy || entry === null || root === null) return;
    busy = true;
    reflectChoice();
    say(
      `Making the project in ${root} on ${entry.sshHost ?? "this machine"}` +
        `${startBox.checked ? ", then starting its orchestrator" : ""}…`,
      "pending",
    );
    let outcome: NewProjectOutcome;
    try {
      outcome = await bridge.createProject({
        server: entry.server,
        root,
        storeLocation: storeLocation(),
        startController: startBox.checked,
      });
    } catch (error) {
      busy = false;
      reflectChoice();
      say(saidBy(error), "refused");
      return;
    }
    busy = false;
    reflectChoice();
    if (!outcome.ok && outcome.failedStep !== "controller") {
      const step = outcome.failedStep === "init" ? "kyo init" : "mkdir";
      say(`${step} failed on ${outcome.machine}: ${outcome.problem ?? ""}`, "refused");
      return;
    }
    showResult(outcome, entry.server);
  }
  chooseButton.addEventListener("click", () => void choose());

  function showResult(outcome: NewProjectOutcome, where: RemoteFacts | null): void {
    picker.hidden = true;
    const failed =
      outcome.failedStep === "controller"
        ? el("div", {
            class: "prompt-problem",
            data: { region: "new-project-controller-problem", state: "refused" },
            text: `kyo controller start failed on ${outcome.machine}: ${outcome.problem ?? ""}`,
          })
        : null;
    const openProblem = el("div", {
      class: "prompt-problem",
      data: { region: "new-project-open-problem", state: "none" },
    });
    const openButton = el("button", {
      class: "prompt-button",
      data: { region: "new-project-open" },
      attrs: { type: "button" },
      text: "Open in New Window",
    });
    const doneButton = el("button", {
      class: "prompt-button prompt-ok",
      data: { region: "new-project-done" },
      attrs: { type: "button" },
      text: "Done",
    });
    openButton.addEventListener("click", () => {
      void options.onOpen(outcome.root, where).then((refusal) => {
        if (refusal === null) {
          close();
          return;
        }
        openProblem.dataset.state = "refused";
        openProblem.textContent = refusal;
      });
    });
    doneButton.addEventListener("click", close);
    result.replaceChildren(
      ...outcomeLines(outcome).map((line) =>
        el("div", {
          class: "picker-result-line",
          data: { region: "new-project-said" },
          text: line,
        }),
      ),
      ...(failed === null ? [] : [failed]),
      openProblem,
      el("div", { class: "prompt-actions" }, [openButton, doneButton]),
    );
    result.hidden = false;
    doneButton.focus();
  }

  reflectChoice();
  void bridge
    .newProjectServers()
    .then((entries) => {
      servers = entries;
      serverSelect.replaceChildren(
        ...entries.map((entry, index) =>
          el("option", {
            text: entry.server === null ? entry.label : `${entry.label} (over SSH)`,
            attrs: { value: String(index) },
          }),
        ),
      );
      serverSelect.value = "0";
      serverSelect.dispatchEvent(new Event("change"));
      list.focus();
    })
    .catch((error: unknown) => say(saidBy(error), "refused"));
}
