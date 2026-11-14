import AppKit
import Foundation
import Testing

@testable import KyoseiMac

// KYO-944: the check channel is compiled only into a check build, and a check build cannot
// reach the person's own server, settings or keychain. Both are read here from the executable
// the test run built, which stands beside the test bundle. `swift test` builds it without the
// `KYOSEI_CHECK` condition, as the shipped app is built, and
// `swift test -Xswiftc -DKYOSEI_CHECK` builds it with the condition, as `package.sh check`
// does. Each half is the other's control: a string looked for and not found in one build is
// found by the same search in the other, so an absence is not a search that finds nothing.

/// A class of the test bundle, by which the bundle is found.
private final class Here {}

/// The bytes of the `KyoseiMac` executable this test run built.
private func builtExecutable() throws -> Data {
    let file = Bundle(for: Here.self).bundleURL.deletingLastPathComponent().appending(path: "KyoseiMac")
    return try Data(contentsOf: file)
}

private func holds(_ data: Data, _ text: String) -> Bool {
    data.range(of: Data(text.utf8)) != nil
}

/// The name of the variable that starts the channel, which only the channel's file holds.
private let marker = "KYOSEI_CHECK_SOCKET"
/// What the shipped app reads its token with, and the server it falls back to.
private let securityTool = "/usr/bin/security"
private let defaultServer = "http://127.0.0.1:47370"

#if KYOSEI_CHECK
@Suite
struct CheckBuildTests {
    @Test func aCheckBuildHoldsTheChannelAndNeitherTheSecurityToolNorTheDefaultServer() throws {
        let executable = try builtExecutable()
        #expect(holds(executable, marker))
        #expect(!holds(executable, securityTool))
        #expect(!holds(executable, defaultServer))
    }

    private let served = ["KYOSEI_SERVE_URL": "http://127.0.0.1:50123"]

    @Test func itRefusesToStartWithoutATestServer() {
        let none = CheckBuild.refusal(arguments: ["Kyosei"], environment: [:], bundleIdentifier: nil)
        #expect(none?.contains("KYOSEI_SERVE_URL") == true)
        for server in ["", "not an address", "127.0.0.1:47370"] {
            #expect(
                CheckBuild.refusal(
                    arguments: ["Kyosei"], environment: ["KYOSEI_SERVE_URL": server],
                    bundleIdentifier: "dev.kyosei.apple.check") != nil, "started with \(server)")
        }
        #expect(
            CheckBuild.refusal(
                arguments: ["Kyosei"], environment: served, bundleIdentifier: "dev.kyosei.apple.check")
                == nil)
    }

    @Test func itRefusesTheShippedAppsIdentifierAndTheKeychainCommand() {
        #expect(
            CheckBuild.refusal(
                arguments: ["Kyosei"], environment: served, bundleIdentifier: "dev.kyosei.apple") != nil)
        #expect(
            CheckBuild.refusal(
                arguments: ["Kyosei", "seal-key", "read"], environment: served,
                bundleIdentifier: "dev.kyosei.apple.check") != nil)
    }

    @MainActor
    @Test func itsServerIsTheEnvironmentsAndASavedOneIsNotRead() {
        // The key the shipped app saves its server under. A check build's own defaults could
        // hold it after a save from Settings, and it is still not read.
        UserDefaults.standard.set("http://127.0.0.1:47370", forKey: "serverURL")
        defer { UserDefaults.standard.removeObject(forKey: "serverURL") }
        let named = ProcessInfo.processInfo.environment["KYOSEI_SERVE_URL"] ?? ""
        #expect(SessionFeed.savedServer == named)
    }

    @MainActor
    @Test func itsTokenIsTheEnvironmentsAndSavingOneIsRefused() {
        #expect(CheckBuild.token(in: [:]) == nil)
        #expect(CheckBuild.token(in: ["KYOSEI_CHECK_TOKEN": "  "]) == nil)
        #expect(CheckBuild.token(in: ["KYOSEI_CHECK_TOKEN": "abc\n"]) == "abc")
        #expect(!SessionFeed.storeDeviceToken("anything"))
    }

    @Test func itsWindowsPlaceIsReadFromTheEnvironment() {
        #expect(CheckBuild.frame(in: [:]) == nil)
        #expect(CheckBuild.frame(in: ["KYOSEI_CHECK_FRAME": "10,20,0,300"]) == nil)
        #expect(
            CheckBuild.frame(in: ["KYOSEI_CHECK_FRAME": "10, 20, 900,700"])
                == NSRect(x: 10, y: 20, width: 900, height: 700))
    }
}
#else
@Suite
struct ShippedBuildTests {
    /// Fails if the channel's marker is in an executable built without the check condition,
    /// which is how the shipped app is built.
    @Test func aBuildWithoutTheCheckConditionHoldsNoCheckChannel() throws {
        let executable = try builtExecutable()
        #expect(!holds(executable, marker))
        // The same search finds what this build does hold, so it can find a string.
        #expect(holds(executable, securityTool))
        #expect(holds(executable, defaultServer))
    }
}
#endif
