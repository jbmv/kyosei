import AppKit
import KyoseiClient
import Quartz
import SwiftUI
import UniformTypeIdentifiers
import WebKit

struct ChatView: View {
    let feed: SessionFeed

    /// A model id's family word, capitalized: `claude-opus-5-5` is `Opus`.
    static func family(of model: String?) -> String? {
        guard let model else { return nil }
        let word = model.split(separator: "-").first {
            $0 != "claude" && !$0.isEmpty && $0.allSatisfy(\.isLetter)
        }
        return word.map { $0.prefix(1).uppercased() + $0.dropFirst() } ?? model
    }

    var body: some View {
        VStack(spacing: 0) {
            ChatWebView(feed: feed)
            Divider()
            HStack(spacing: 14) {
                // KYO-883: how long ago the session was last active, where the project's name
                // was; the project is named in the page's dropdown above.
                if !feed.activity.text.isEmpty {
                    Text(feed.activity.text)
                        .monospacedDigit()
                        .foregroundStyle(
                            feed.activity.level == "red"
                                ? Color.red : feed.activity.level == "amber" ? Color.orange : Color.secondary
                        )
                        .help(feed.activity.title)
                }
                if let percent = feed.facts?.context?.percent {
                    ContextMeter(percent: percent)
                }
                if let limits = feed.facts?.limits {
                    if let window = limits.five_hour { LimitLabel(name: "5-hour", window: window) }
                    if let window = limits.seven_day {
                        LimitLabel(
                            name: "Weekly", window: window,
                            usedThisSession: feed.facts?.weeklyUsedThisSession)
                    }
                }
                if let facts = feed.facts {
                    // The model, then its effort, spaced as the bar's other items are (KYO-883).
                    // KYO-883: the model's family alone, `Opus` or `Fable`.
                    Text(Self.family(of: facts.model) ?? "model unknown")
                    if let effort = facts.effort { Text(effort) }
                }
                if let mode = feed.facts?.permission_mode { Text(mode) }
                if let lines = feed.facts?.lines {
                    // Green and red, as a diff states them. The running subagents are stated
                    // under the composer, in the page.
                    HStack(spacing: 4) {
                        Text("+\(lines.added.formatted())").foregroundStyle(Color.green)
                        Text("−\(lines.removed.formatted())").foregroundStyle(Color.red)
                    }
                    .monospacedDigit()
                }
                Spacer()
                // With no project chosen there is no session to be live or not.
                if let needed = feed.tokenNeeded {
                    // Said whichever project the window shows, the list of projects included:
                    // without a token the server lists no session at all (KYO-791).
                    TokenNeededLabel(reason: needed)
                } else if !feed.project.isEmpty {
                    FeedStateLabel(state: feed.state)
                }
                if let remote = feed.remote {
                    HStack(spacing: 4) {
                        Text(remote)
                        if let ahead = feed.ahead, ahead > 0 {
                            Text("↑\(ahead)").monospacedDigit()
                                .help("\(ahead) commit\(ahead == 1 ? "" : "s") not on the remote")
                        }
                    }
                }
                if let branch = feed.facts?.git_branch {
                    HStack(spacing: 4) {
                        Image(systemName: "arrow.triangle.branch")
                        Text(branch)
                    }
                }
            }
            .font(.system(size: 11))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 12)
            .frame(height: 24)
        }
    }
}

/// Says when the chat is not following its session, so that a stale chat is not read as a
/// current one (KYO-725), and nothing while it is. Hovering shows why, and that the feed is
/// connecting again.
struct FeedStateLabel: View {
    let state: FeedState
    @State private var shown = false

    private static func moment(_ date: Date) -> String {
        Calendar.current.isDateInToday(date)
            ? date.formatted(date: .omitted, time: .shortened)
            : date.formatted(date: .abbreviated, time: .shortened)
    }

    private static func sentence(_ reason: String) -> String {
        let said = reason.hasSuffix(".") ? reason : reason + "."
        return said + " Connecting again."
    }

    var body: some View {
        switch state {
        case .live:
            EmptyView()
        case .connecting:
            Text("Connecting…")
        case .noSession:
            Text("No session")
        case .notLive(let since, let reason):
            HStack(spacing: 4) {
                Image(systemName: "exclamationmark.triangle.fill")
                Text("Not live since \(Self.moment(since))")
            }
            .foregroundStyle(.orange)
            .contentShape(Rectangle())
            .onHover { shown = $0 }
            .onTapGesture { shown.toggle() }
            .popover(isPresented: $shown, arrowEdge: .top) {
                Text(Self.sentence(reason))
                    .font(.system(size: 12))
                    .frame(maxWidth: 320, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(10)
            }
        }
    }
}

/// Says that the server shows this device no session until it holds a device token it accepts
/// (KYO-791). The words are always shown, and pressing or hovering gives the sentence that
/// says what is wrong with the token and how one is issued.
struct TokenNeededLabel: View {
    let reason: String
    @State private var shown = false

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "key.fill")
            Text("Device token needed")
        }
        .foregroundStyle(.orange)
        .contentShape(Rectangle())
        .onHover { shown = $0 }
        .onTapGesture { shown.toggle() }
        .popover(isPresented: $shown, arrowEdge: .top) {
            Text(reason)
                .font(.system(size: 12))
                .frame(maxWidth: 320, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(10)
        }
    }
}

/// How full the context is: the number, and a bar coloured by how near the limit it is.
struct ContextMeter: View {
    let percent: Int

    private var colour: Color {
        switch percent {
        case ..<40: .green
        case ..<60: .blue
        case ..<85: .orange
        default: .red
        }
    }

    var body: some View {
        HStack(spacing: 6) {
            Text("context \(percent)%").monospacedDigit()
            Capsule().fill(.quaternary)
                .frame(width: 64, height: 6)
                .overlay(alignment: .leading) {
                    Capsule().fill(colour)
                        .frame(width: 64 * CGFloat(min(max(percent, 0), 100)) / 100)
                }
        }
    }
}

/// How much of one usage window is spent. Hovering shows when the window resets.
struct LimitLabel: View {
    let name: String
    let window: SessionFacts.Limits.Window
    /// Points of this window used since the session's first reading, when known (KYO-717).
    var usedThisSession: Int?

    private var resets: String {
        let stamps = ISO8601DateFormatter()
        let plain = stamps.date(from: window.resets_at ?? "")
        stamps.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = plain ?? stamps.date(from: window.resets_at ?? "") else {
            return "Reset time unknown"
        }
        return "Resets \(date.formatted(date: .abbreviated, time: .shortened))"
    }

    @State private var shown = false

    var body: some View {
        Text(
            "\(name): \(Int(window.used_percentage.rounded()))%"
                + (usedThisSession.map { " ↓\($0)" } ?? "")
        )
            .monospacedDigit()
            .foregroundStyle(window.used_percentage >= 90 ? Color.red : window.used_percentage >= 75 ? Color.orange : Color.secondary)
            .contentShape(Rectangle())
            // A tooltip did not appear over the status bar, so the reset time opens on its own.
            .onHover { shown = $0 }
            .onTapGesture { shown.toggle() }
            .popover(isPresented: $shown, arrowEdge: .top) {
                Text(resets).font(.system(size: 12)).padding(10)
            }
    }
}

/// The web view of the chat. Command-F reaches the page's own handler as a key press when the
/// view is first responder, but a key equivalent is offered to the menu first, so the view
/// takes it here and asks the page to open its search field (KYO-741).
///
/// Spelling (KYO-796). WebKit on macOS underlines misspelled words only when the app's
/// `WebContinuousSpellCheckingEnabled` default is true, and a missing key reads as false. It
/// asks the system's checker for corrections, which include the capital at the start of a
/// sentence, when words are being marked and `WebAutomaticSpellingCorrectionEnabled` is true,
/// and a missing key there reads as the system-wide setting. The page is therefore built over
/// registered defaults that turn the underline on and the correction off. They are registered
/// rather than set because the Spelling and Grammar menu stores the person's choice under the
/// same keys, and a stored value is read before a registered one. They are registered before
/// the first web view exists because WebKit reads the keys once in the life of the process.
///
/// The capital at the start of a sentence still came back with the correction turned off
/// whenever the underline was on (KYO-796.e3, the maintainer, 2026-11-11). It is taken to come
/// from the system's own capitalisation setting, `NSAutomaticCapitalizationEnabled`, which the
/// checker reads from the app's domain before the global one, so that key is registered off too.
/// That this is the source is a hypothesis the maintainer's next look decides.
final class ChatPage: WKWebView {
    static let spellingDefaults: [String: Any] = [
        "WebContinuousSpellCheckingEnabled": true,
        "WebAutomaticSpellingCorrectionEnabled": false,
        "NSAutomaticCapitalizationEnabled": false,
    ]

    override init(frame: CGRect, configuration: WKWebViewConfiguration) {
        UserDefaults.standard.register(defaults: Self.spellingDefaults)
        super.init(frame: frame, configuration: configuration)
    }

    required init?(coder: NSCoder) {
        fatalError("ChatPage is built in code")
    }

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        if flags == .command, event.charactersIgnoringModifiers == "f" {
            evaluateJavaScript("window.kyo.find()", completionHandler: nil)
            return true
        }
        // Command-Shift-F opens History (KYO-825), offered to the menu first like Command-F.
        if flags == [.command, .shift], event.charactersIgnoringModifiers?.lowercased() == "f" {
            evaluateJavaScript("window.kyo.history()", completionHandler: nil)
            return true
        }
        return super.performKeyEquivalent(with: event)
    }

    // MARK: Indent and Outdent on a box's words (KYO-912)

    /// Whether the last right-click was on a composer box's words, which the page posts as
    /// `composerMenu` from its `contextmenu` listener before WebKit's menu opens.
    var composerMenu = false

    // MARK: Quick Look on an image (KYO-883)

    /// WebKit's menu gains Indent and Outdent at its top when the right-click was on a box's
    /// words (KYO-912), and Quick Look for an image (KYO-883); the page says which. WebKit's own
    /// items stay, so a misspelled word keeps its suggestions below the added ones.
    override func willOpenMenu(_ menu: NSMenu, with event: NSEvent) {
        super.willOpenMenu(menu, with: event)
        if composerMenu {
            composerMenu = false
            let indent = NSMenuItem(title: "Indent", action: #selector(indentChosen), keyEquivalent: "")
            let outdent = NSMenuItem(title: "Outdent", action: #selector(outdentChosen), keyEquivalent: "")
            [indent, outdent].forEach { $0.target = self }
            menu.insertItem(indent, at: 0)
            menu.insertItem(outdent, at: 1)
            if menu.items.count > 2 { menu.insertItem(.separator(), at: 2) }
        }
        let imageItems = ["WKMenuItemIdentifierCopyImage", "WKMenuItemIdentifierDownloadImage"]
        guard menu.items.contains(where: { imageItems.contains($0.identifier?.rawValue ?? "") })
        else { return }
        // Open Image in New Window has no window to open here: the app makes no second page.
        menu.items.filter { $0.identifier?.rawValue == "WKMenuItemIdentifierOpenImageInNewWindow" }
            .forEach(menu.removeItem)
        let item = NSMenuItem(title: "Quick Look", action: #selector(quickLookChosen), keyEquivalent: "")
        item.target = self
        menu.insertItem(item, at: 0)
        menu.insertItem(.separator(), at: 1)
        // WebKit's Download Image has no download to hand to here; the app saves the image itself.
        if let download = menu.items.firstIndex(where: {
            $0.identifier?.rawValue == "WKMenuItemIdentifierDownloadImage"
        }) {
            let own = NSMenuItem(title: "Download Image", action: #selector(saveChosen), keyEquivalent: "")
            own.target = self
            menu.removeItem(at: download)
            menu.insertItem(own, at: download)
        }
    }

    @objc private func indentChosen() {
        evaluateJavaScript("window.kyo.indent()", completionHandler: nil)
    }

    @objc private func outdentChosen() {
        evaluateJavaScript("window.kyo.outdent()", completionHandler: nil)
    }

    @objc private func saveChosen() {
        evaluateJavaScript("window.kyo.saveImage()", completionHandler: nil)
    }

    // MARK: Files dragged from the Finder (KYO-910)

    /// A composer box is an editor, which takes no dropped file's contents, and WebKit inserts
    /// no file's path into it as it did into a text area. So the paths of the dragged files
    /// that the page does not attach, which are those that are not images or PDFs, are handed
    /// to the page with the point of the drop, in the page's coordinates, and the page inserts
    /// them where they fell. WebKit still takes the drop, and the page attaches the rest.
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let taken = super.performDragOperation(sender)
        let urls = sender.draggingPasteboard.readObjects(
            forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL] ?? []
        let paths = urls.filter { !Self.attachable($0) }.map(\.path)
        guard !paths.isEmpty else { return taken }
        let point = convert(sender.draggingLocation, from: nil)
        let zoom = pageZoom > 0 ? pageZoom : 1
        let frame: [String: Any] = [
            "paths": paths,
            "x": point.x / zoom,
            "y": (isFlipped ? point.y : bounds.height - point.y) / zoom,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: frame),
            let json = String(data: data, encoding: .utf8)
        else { return taken }
        evaluateJavaScript("window.kyo.dropPaths(\(json))", completionHandler: nil)
        return true
    }

    /// Whether the page attaches a file dropped on a box: an image or a PDF.
    static func attachable(_ url: URL) -> Bool {
        guard let type = UTType(filenameExtension: url.pathExtension) else { return false }
        return type.conforms(to: .image) || type.conforms(to: .pdf)
    }

    @objc private func quickLookChosen() {
        evaluateJavaScript("window.kyo.quickLook()", completionHandler: nil)
    }

    /// The file the Quick Look panel shows: a copy of the image in this app's temporary folder.
    private var previewItem: URL?

    /// A `data:` URL's bytes and media type.
    static func decode(dataURL: String) -> (bytes: Data, mediaType: String?)? {
        guard let comma = dataURL.firstIndex(of: ","), dataURL.hasPrefix("data:"),
            let bytes = Data(base64Encoded: String(dataURL[dataURL.index(after: comma)...]))
        else { return nil }
        let header = dataURL[dataURL.index(dataURL.startIndex, offsetBy: 5)..<comma]
        return (bytes, header.split(separator: ";").first.map(String.init))
    }

    /// A file name for an image: its name without a path or an extension, and the extension its bytes call for.
    private static func fileName(_ name: String, _ mediaType: String?, _ bytes: Data, number: Int = 1) -> String {
        let stem = (name as NSString).deletingPathExtension
        let safe = String(String(stem.map { "/:\\".contains($0) ? "-" : $0 }).prefix(80))
        let base = safe.isEmpty ? "image" : safe
        return "\(number > 1 ? "\(base) \(number)" : base).\(fileExtension(mediaType, bytes))"
    }

    /// KYO-883: Download Image saves the image to the Downloads folder, under a name not yet
    /// taken there, and the Downloads stack in the Dock is told, as a browser's download does.
    func saveToDownloads(bytes: Data, mediaType: String?, name: String) {
        guard let folder = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        else { return }
        var number = 1
        var file = folder.appendingPathComponent(Self.fileName(name, mediaType, bytes))
        while FileManager.default.fileExists(atPath: file.path) {
            number += 1
            file = folder.appendingPathComponent(Self.fileName(name, mediaType, bytes, number: number))
        }
        guard (try? bytes.write(to: file, options: .withoutOverwriting)) != nil else { return }
        DistributedNotificationCenter.default().post(
            name: Notification.Name("com.apple.DownloadFileFinished"), object: file.path)
    }

    func quickLook(bytes: Data, mediaType: String?, name: String) {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(
            "Kyosei Quick Look", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let file = folder.appendingPathComponent(Self.fileName(name, mediaType, bytes))
        guard (try? bytes.write(to: file, options: .atomic)) != nil else { return }
        previewItem = file
        // The panel asks the responder chain for its items, so this page must be in it.
        let inPage = (window?.firstResponder as? NSView)?.isDescendant(of: self) ?? false
        if !inPage { window?.makeFirstResponder(self) }
        guard let panel = QLPreviewPanel.shared() else { return }
        if panel.isVisible { panel.reloadData() } else { panel.makeKeyAndOrderFront(nil) }
    }

    private static func fileExtension(_ mediaType: String?, _ bytes: Data) -> String {
        switch mediaType {
        case "image/png": return "png"
        case "image/jpeg": return "jpg"
        case "image/gif": return "gif"
        case "image/webp": return "webp"
        default: break
        }
        let head = [UInt8](bytes.prefix(4))
        if head.starts(with: [0x89, 0x50]) { return "png" }
        if head.starts(with: [0xFF, 0xD8]) { return "jpg" }
        if head.starts(with: [0x47, 0x49]) { return "gif" }
        return "webp"
    }

    override func acceptsPreviewPanelControl(_ panel: QLPreviewPanel!) -> Bool {
        previewItem != nil
    }

    override func beginPreviewPanelControl(_ panel: QLPreviewPanel!) {
        panel.dataSource = self
        panel.reloadData()
    }

    override func endPreviewPanelControl(_ panel: QLPreviewPanel!) {
        panel.dataSource = nil
    }
}

extension ChatPage: @preconcurrency QLPreviewPanelDataSource {
    func numberOfPreviewItems(in panel: QLPreviewPanel!) -> Int {
        previewItem == nil ? 0 : 1
    }

    func previewPanel(_ panel: QLPreviewPanel!, previewItemAt index: Int) -> (any QLPreviewItem)! {
        previewItem as NSURL?
    }
}

/// The read-only chat: a page drawn from the renderer in the app's resources. It loads
/// nothing else, and a link in a message opens in the person's browser.
struct ChatWebView: NSViewRepresentable {
    let feed: SessionFeed

    func makeCoordinator() -> Coordinator {
        Coordinator(feed: feed)
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(context.coordinator, name: "log")
        configuration.userContentController.add(context.coordinator, name: "select")
        configuration.userContentController.add(context.coordinator, name: "image")
        configuration.userContentController.add(context.coordinator, name: "agent")
        configuration.userContentController.add(context.coordinator, name: "strip")
        for name in [
            "send", "retract", "interrupt", "saveDraft", "saveAgenda", "attach", "start",
            "sendNow", "copy", "attention", "quicklook", "composerMenu",
            "terminal", "records", "files", "history", "project", "projects",
        ] {
            configuration.userContentController.add(context.coordinator, name: name)
        }
        let view = ChatPage(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        view.isInspectable = true
        feed.webView = view
        // A packaged app holds the renderer in its own resources.
        let packaged = Bundle.main.url(
            forResource: "KyoseiApple_KyoseiMac", withExtension: "bundle"
        ).flatMap(Bundle.init(url:))
        let directory = (packaged ?? Bundle.module).resourceURL!.appending(path: "renderer")
        view.loadFileURL(
            directory.appending(path: "chat.html"), allowingReadAccessTo: directory)
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        /// The Dock bounce asked for when the session came to wait, until it is answered.
        private var attentionRequest: Int?
        let feed: SessionFeed

        init(feed: SessionFeed) {
            self.feed = feed
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            feed.start()
        }

        func webView(
            _ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
            decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = action.request.url, action.navigationType == .linkActivated else {
                decisionHandler(action.request.url?.isFileURL == true ? .allow : .cancel)
                return
            }
            if ["http", "https"].contains(url.scheme) { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }

        func userContentController(
            _ controller: WKUserContentController, didReceive message: WKScriptMessage
        ) {
            if message.name == "select", let body = message.body as? [String: Any],
                let session = body["session"] as? String
            {
                feed.select(session: session)
                return
            }
            if message.name == "send", let body = message.body as? [String: Any] {
                feed.sendMessage(body: body)
                return
            }
            if message.name == "retract", let body = message.body as? [String: Any],
                let id = body["id"] as? String
            {
                feed.retract(id: id)
                return
            }
            if message.name == "sendNow", let body = message.body as? [String: Any],
                let id = body["id"] as? String
            {
                feed.sendNow(id: id)
                return
            }
            // KYO-883: the session now waits on the person. The Dock icon bounces until the
            // app is in front again, when another app is in front, on whichever screen; in
            // front, the red line says it. A session that works again before then stops it.
            if message.name == "attention" {
                let waiting = (message.body as? [String: Any])?["waiting"] as? Bool ?? true
                if let request = attentionRequest {
                    NSApp.cancelUserAttentionRequest(request)
                    attentionRequest = nil
                }
                if waiting, !NSApp.isActive {
                    attentionRequest = NSApp.requestUserAttention(.criticalRequest)
                }
                return
            }
            // KYO-912: whether the right-click about to open WebKit's menu was on a box's words.
            if message.name == "composerMenu" {
                let body = message.body as? [String: Any]
                (message.webView as? ChatPage)?.composerMenu = body?["composer"] as? Bool ?? false
                return
            }
            // KYO-883: Quick Look on an image the page was right-clicked on: its data, or a
            // composer attachment's id, whose whole image the server holds.
            if message.name == "quicklook", let body = message.body as? [String: Any] {
                let name = body["name"] as? String ?? "image"
                let data = body["data"] as? String
                let save = body["save"] as? Bool ?? false
                let page = feed.webView as? ChatPage
                let show = { (bytes: Data, mediaType: String?) in
                    if save {
                        page?.saveToDownloads(bytes: bytes, mediaType: mediaType, name: name)
                    } else {
                        page?.quickLook(bytes: bytes, mediaType: mediaType, name: name)
                    }
                }
                Task { @MainActor in
                    if let attachment = body["attachment"] as? String,
                        let bytes = await feed.attachmentBytes(attachment)
                    {
                        show(bytes, nil)
                    } else if let data, let decoded = ChatPage.decode(dataURL: data) {
                        show(decoded.bytes, decoded.mediaType)
                    }
                }
                return
            }
            if message.name == "copy", let body = message.body as? [String: Any],
                let text = body["text"] as? String
            {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
                return
            }
            if message.name == "attach", let body = message.body as? [String: Any] {
                feed.attach(body: body)
                return
            }
            if message.name == "saveDraft", let body = message.body as? [String: Any] {
                feed.saveDraft(body: body)
                return
            }
            if message.name == "saveAgenda", let body = message.body as? [String: Any] {
                feed.saveAgenda(body: body)
                return
            }
            if message.name == "interrupt" {
                feed.interrupt()
                return
            }
            if message.name == "terminal", let body = message.body as? [String: Any] {
                feed.terminal(body: body)
                return
            }
            if message.name == "records", let body = message.body as? [String: Any] {
                feed.records(body: body)
                return
            }
            if message.name == "files", let body = message.body as? [String: Any] {
                feed.files(body: body)
                return
            }
            if message.name == "history", let body = message.body as? [String: Any] {
                feed.history(body: body)
                return
            }
            if message.name == "start" {
                feed.startOrchestrator()
                return
            }
            if message.name == "project", let body = message.body as? [String: Any],
                let name = body["name"] as? String
            {
                feed.choose(project: name, session: body["session"] as? String)
                return
            }
            if message.name == "projects", let body = message.body as? [String: Any] {
                feed.projects(body: body)
                return
            }
            if message.name == "strip", let body = message.body as? [String: Any],
                let activity = body["activity"] as? [String: Any]
            {
                feed.activity = (
                    activity["text"] as? String ?? "", activity["level"] as? String ?? "",
                    activity["title"] as? String ?? ""
                )
                return
            }
            if message.name == "strip", let body = message.body as? [String: Any],
                let running = body["subagents"] as? Int
            {
                feed.runningSubagents = running
                let stamps = ISO8601DateFormatter()
                stamps.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
                let agents = body["agents"] as? [[String: Any]] ?? []
                feed.runningAgents = agents.enumerated().map { index, agent in
                    RunningAgent(
                        id: index,
                        name: agent["name"] as? String ?? "",
                        type: agent["type"] as? String ?? "",
                        model: agent["model"] as? String ?? "",
                        effort: agent["effort"] as? String ?? "",
                        since: stamps.date(from: agent["since"] as? String ?? ""),
                        tokens: agent["tokens"] as? String ?? "")
                }
                return
            }
            if message.name == "agent", let body = message.body as? [String: Any],
                let agent = body["agent"] as? String
            {
                feed.loadAgent(agent)
                return
            }
            if message.name == "image", let body = message.body as? [String: Any],
                let attachment = body["attachment"] as? String
            {
                feed.loadAttachment(attachment, session: body["session"] as? String)
                return
            }
            if message.name == "image", let body = message.body as? [String: Any],
                let turn = body["turn"] as? String, let index = body["index"] as? Int
            {
                feed.loadImage(turn: turn, index: index, session: body["session"] as? String)
                return
            }
            print("[page] \(message.body)")
        }
    }
}
