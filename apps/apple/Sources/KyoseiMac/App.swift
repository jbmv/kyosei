import AppKit
import KyoseiClient
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    /// The one window's feed. It is the delegate's so that a quit can settle the composer's
    /// draft through it before the process ends.
    let feed = SessionFeed.fromEnvironment()

    func applicationDidFinishLaunching(_ notification: Notification) {
        setvbuf(stdout, nil, _IOLBF, 0)
        // Run as a plain executable, the process is otherwise not a foreground app.
        NSApp.setActivationPolicy(.regular)
        NSApp.activate()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    /// A quit waits for the composer's last save (KYO-782): the page saves what is typed
    /// after a pause, so the characters typed just before a quit had not been sent. The
    /// feed asks the page for them, saves them and waits for the server's answer, for no
    /// longer than `SessionFeed.quitLimit`; what the server has not acknowledged by then is
    /// kept on this machine and restored at the next launch.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        Task {
            await feed.settleDraftBeforeQuit()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}

@main
struct KyoseiMacApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    private var feed: SessionFeed { delegate.feed }

    var body: some Scene {
        // One window, and no File > New Window: the feed drives one web view, so a second
        // window would take every frame from the first, whose composer would go on working
        // over a chat that no longer updates (KYO-725's report). Another machine's copy of
        // the app is its own process with its own stream, and is unaffected.
        // The window's title, and with it its entry in the Window menu, is the project's.
        // The one window is turned to another project from the dropdown at the start of its
        // top bar, which the page draws (KYO-761); with no project chosen it is the app's.
        Window("Kyosei", id: "main") {
            ChatView(feed: feed)
                .frame(minWidth: 560, minHeight: 400)
                .navigationTitle(feed.project.isEmpty ? "Kyosei" : feed.project)
        }
        .defaultSize(width: 980, height: 860)
        .commands {
            // The File menu's one item of the app's own (KYO-766): the list of projects, which
            // a first run shows, and which nothing else returns to once a project is chosen.
            CommandGroup(replacing: .newItem) {
                Button("Projects…") { feed.toggleProjectList() }
                    .keyboardShortcut("o", modifiers: .command)
            }
        }

        Settings {
            SettingsView(feed: feed)
        }
    }
}
