// The home tab (KYO-246): what Cmd+T and the strip's "+" open.
//
// The page is the shell's (`shell/home.ts`), because an empty column draws the same
// page with no tab around it; this kind is the tab-shaped door to it. Choosing an
// entry NAVIGATES this tab to it (KYO-247) -- the way a browser's new-tab page becomes
// the page you chose, with back bringing the page back.

import { renderHome } from "../../shell/home";
import type { TabContext, TabInstance, TabKind } from "../kind";

const kind: TabKind = {
  kind: "home",
  label: "New tab",
  open(context: TabContext): TabInstance {
    return {
      title: () => "New tab",
      render(mount) {
        mount.replaceChildren(
          renderHome({
            open: (ref, event) => {
              // Cmd-click keeps this page and opens beside it; a plain click replaces it.
              if (event.metaKey || event.ctrlKey) {
                context.openTab({ ...ref, force: true });
                return;
              }
              context.navigate(ref);
            },
          }),
        );
      },
      close() {
        // Nothing held: the page is static markup over the registry.
      },
    };
  },
};

export default kind;
