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
        WindowGroup("Kyosei") {
            ChatView(feed: feed)
                .frame(minWidth: 560, minHeight: 400)
        }
        .defaultSize(width: 980, height: 860)

        Settings {
            SettingsView(feed: feed)
        }
    }
}
