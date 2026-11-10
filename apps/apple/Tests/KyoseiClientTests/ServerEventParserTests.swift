import Foundation
import Testing

@testable import KyoseiClient

private func frames(_ lines: [String]) -> [ServerEvent] {
    var parser = ServerEventParser()
    return lines.compactMap { parser.feed(line: $0) }
}

@Test func aFrameEndsAtTheBlankLine() {
    let result = frames(["id: 7", "event: transcript", #"data: {"a": 1}"#, ""])
    #expect(result == [ServerEvent(name: "transcript", data: #"{"a": 1}"#, id: "7")])
}

@Test func aKeepaliveIsNotAFrame() {
    #expect(frames([": keepalive", ""]).isEmpty)
}

@Test func theFieldsOfOneFrameDoNotCarryIntoTheNext() {
    let result = frames(["id: 7", "event: ready", "data: 1", "", "event: reply", "data: 2", ""])
    #expect(result.map(\.id) == ["7", nil])
    #expect(result.map(\.name) == ["ready", "reply"])
}

@Test func aColonInTheDataIsKept() {
    #expect(frames(["data: a: b", ""]).first?.data == "a: b")
}

@Test func severalDataLinesAreJoined() {
    #expect(frames(["data: a", "data: b", ""]).first?.data == "a\nb")
}

private func weeklyFacts(_ weekly: String, start: String?) throws -> SessionFacts {
    let first = start.map { #","weekly_at_start":\#($0)"# } ?? ""
    let json = #"{"limits":{"seven_day":\#(weekly)}\#(first)}"#
    return try JSONDecoder().decode(SessionFacts.self, from: Data(json.utf8))
}

@Test func weeklyPointsUsedAreCountedFromTheSessionsFirstFigure() throws {
    let now = #"{"used_percentage":15.2,"resets_at":"T1"}"#
    let start = #"{"used_percentage":12.0,"resets_at":"T1"}"#
    #expect(try weeklyFacts(now, start: start).weeklyUsedThisSession == 3)
}

@Test func weeklyUseIsNotShownWithoutAFirstFigureAfterAResetOrAtZero() throws {
    let now = #"{"used_percentage":15.2,"resets_at":"T1"}"#
    #expect(try weeklyFacts(now, start: nil).weeklyUsedThisSession == nil)
    let other = #"{"used_percentage":12.0,"resets_at":"T0"}"#
    #expect(try weeklyFacts(now, start: other).weeklyUsedThisSession == nil)
    let same = #"{"used_percentage":15.0,"resets_at":"T1"}"#
    #expect(try weeklyFacts(now, start: same).weeklyUsedThisSession == nil)
}
