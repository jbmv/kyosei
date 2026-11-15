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
        #if KYOSEI_CHECK
        // A check build (KYO-944) is started by a runner on a machine a person may be using.
        // It runs as an accessory app (KYO-945): it has no Dock icon and no menu bar of its
        // own, and nothing brings it in front of the app the person is using; its bundle
        // says the same with LSUIElement, so that it is one from its first moment. A run
        // that asks for the front with KYOSEI_CHECK_ACTIVATE gets a regular app, activated.
        // The window's place is taken from the run, and the channel starts only when the
        // run names a socket.
        let environment = ProcessInfo.processInfo.environment
        let asked = environment[CheckBuild.activateVariable] == "1"
        NSApp.setActivationPolicy(asked ? .regular : .accessory)
        if asked { NSApp.activate() }
        checkChannel = CheckChannel.startFromEnvironment(feed: feed)
        if let frame = CheckBuild.frame(in: environment) {
            Task {
                for _ in 0..<100 {
                    if let window = feed.webView?.window {
                        window.setFrame(frame, display: true)
                        return
                    }
                    try? await Task.sleep(for: .milliseconds(50))
                }
            }
        }
        #else
        // Run as a plain executable, the process is otherwise not a foreground app.
        NSApp.setActivationPolicy(.regular)
        NSApp.activate()
        #endif
    }

    #if KYOSEI_CHECK
    private var checkChannel: CheckChannel?

    func applicationWillTerminate(_ notification: Notification) {
        CheckPasteboard.release()
    }
    #endif

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

/// The executable's entry. Run as `Kyosei seal-key read|add|delete` it makes one keychain
/// call and exits before any window is made (KYO-789, `SealKeyItem`); otherwise it is the app.
@main
enum Entry {
    static func main() {
        let arguments = CommandLine.arguments
        #if KYOSEI_CHECK
        // A check build (KYO-944) refuses to start unless its run names a test server, and
        // has no keychain command; `CheckBuild.refusal` says why.
        if let refusal = CheckBuild.refusal(
            arguments: arguments, environment: ProcessInfo.processInfo.environment,
            bundleIdentifier: Bundle.main.bundleIdentifier)
        {
            FileHandle.standardError.write(Data((refusal + "\n").utf8))
            exit(64)
        }
        // Its pasteboard is its own from before the app exists (KYO-947), so that nothing it
        // cuts, copies or pastes reads or writes the person's.
        guard CheckPasteboard.install() else {
            FileHandle.standardError.write(
                Data("A check build does not start without a pasteboard of its own.\n".utf8))
            exit(65)
        }
        #endif
        if arguments.count >= 2, arguments[1] == "seal-key" {
            let outcome = SealKeyItem.run(Array(arguments.dropFirst(2))) {
                String(decoding: FileHandle.standardInput.readDataToEndOfFile(), as: UTF8.self)
            }
            FileHandle.standardOutput.write(Data(outcome.output.utf8))
            FileHandle.standardError.write(Data(outcome.error.utf8))
            exit(outcome.exit.rawValue)
        }
        KyoseiMacApp.main()
    }
}

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
            SpellingCommands()
        }

        Settings {
            SettingsView(feed: feed)
        }
    }
}

/// The Edit menu's Spelling and Grammar (KYO-918). The right-click menu on a composer box no
/// longer holds WebKit's submenu of that name, so the setting it kept is here: whether
/// misspelled words are underlined. The item sends the web view the action WebKit's own item
/// sent, which stores the choice under the key `ChatPage` registers a default for (KYO-796),
/// and shows that key's value as its tick. There is no Correct Spelling Automatically item:
/// the page corrects nothing that is typed, by design (KYO-796), and the item changed nothing
/// when chosen, so the menu no longer offers what the app does not do.
struct SpellingCommands: Commands {
    @AppStorage("WebContinuousSpellCheckingEnabled") private var underlines = true

    private func send(_ action: String) {
        NSApp.sendAction(Selector((action)), to: nil, from: nil)
    }

    var body: some Commands {
        CommandGroup(after: .pasteboard) {
            Menu("Spelling and Grammar") {
                Button("Show Spelling and Grammar") { send("showGuessPanel:") }
                Button("Check Document Now") { send("checkSpelling:") }
                Divider()
                Toggle(
                    "Check Spelling While Typing",
                    isOn: Binding(get: { underlines }, set: { _ in send("toggleContinuousSpellChecking:") }))
            }
        }
    }
}
