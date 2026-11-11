import AppKit
import KyoseiClient
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        setvbuf(stdout, nil, _IOLBF, 0)
        // Run as a plain executable, the process is otherwise not a foreground app.
        NSApp.setActivationPolicy(.regular)
        NSApp.activate()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}

@main
struct KyoseiMacApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var feed = SessionFeed.fromEnvironment()

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

        Settings {
            SettingsView(feed: feed)
        }
    }
}
