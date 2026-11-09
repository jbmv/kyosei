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
