import Foundation
import Security
import Testing

@testable import KyoseiClient

/// The contract `kyo` relies on when it runs the app's executable as `seal-key` (KYO-789).
/// The keychain itself is replaced: a test process holds no entitlement.
@Suite struct SealKeyItemTests {
    static let key = String(repeating: "0a", count: 32)

    @Test func readPrintsTheKeyAndANewline() {
        let outcome = SealKeyItem.run(["read"], input: { "" }) { command, _ in
            #expect(command == .read)
            return (errSecSuccess, Data(Self.key.utf8))
        }
        #expect(outcome == .init(exit: .done, output: Self.key + "\n"))
    }

    @Test func readWithNoItemExitsThree() {
        let outcome = SealKeyItem.run(["read"], input: { "" }) { _, _ in (errSecItemNotFound, nil) }
        #expect(outcome == .init(exit: .noItem))
    }

    @Test func aRefusalNamesTheStatusOnStandardError() {
        for command in ["read", "add", "delete"] {
            let outcome = SealKeyItem.run([command], input: { Self.key }) { _, _ in
                (errSecMissingEntitlement, nil)
            }
            #expect(outcome == .init(exit: .refused, error: "OSStatus -34018\n"))
        }
    }

    @Test func addTakesTheKeyFromStandardInputAndNeverFromAnArgument() {
        var stored: Data?
        let outcome = SealKeyItem.run(["add"], input: { Self.key.uppercased() + "\n" }) { command, data in
            #expect(command == .add)
            stored = data
            return (errSecSuccess, nil)
        }
        #expect(outcome == .init(exit: .done))
        #expect(stored == Data(Self.key.utf8))
        #expect(SealKeyItem.run(["add", Self.key], input: { "" }) { _, _ in (errSecSuccess, nil) }.exit == .usage)
    }

    @Test func addOverAnItemExitsFive() {
        let outcome = SealKeyItem.run(["add"], input: { Self.key }) { _, _ in (errSecDuplicateItem, nil) }
        #expect(outcome == .init(exit: .itemExists))
    }

    @Test func addRefusesWhatIsNotAKeyWithoutACall() {
        for input in ["", "zz", String(repeating: "g", count: 64), Self.key + "00"] {
            let outcome = SealKeyItem.run(["add"], input: { input }) { _, _ in
                Issue.record("the keychain was called for \(input)")
                return (errSecSuccess, nil)
            }
            #expect(outcome.exit == .usage)
        }
    }

    @Test func deleteSucceedsWhenThereWasNoItem() {
        #expect(SealKeyItem.run(["delete"], input: { "" }) { _, _ in (errSecItemNotFound, nil) }.exit == .done)
        #expect(SealKeyItem.run(["delete"], input: { "" }) { _, _ in (errSecSuccess, nil) }.exit == .done)
    }

    @Test func anythingElseIsAUsageError() {
        for arguments in [[], ["show"], ["read", "more"]] {
            #expect(SealKeyItem.run(arguments, input: { "" }) { _, _ in (errSecSuccess, nil) }.exit == .usage)
        }
    }
}
