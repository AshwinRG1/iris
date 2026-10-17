@testable import CameraAccess
import Foundation
import Testing

struct DetectionHoldTests {
  private let start = Date(timeIntervalSince1970: 1_000)

  @Test func hitMissHitKeepsTheCardAndRestoresItsLatestContents() throws {
    var hold = DetectionHold()
    let first = person("ahead")
    hold.apply([first], at: start)
    #expect(hold.sides["ahead"]?.isDimmed == false)

    hold.apply([], at: start.addingTimeInterval(0.4))
    let missed = try #require(hold.sides["ahead"])
    #expect(missed.detections == [first])
    #expect(missed.missCount == 1)
    #expect(missed.isDimmed)
    #expect(missed.lastSeenAt == start)

    let updated = person("ahead", confidence: 0.94)
    let seenAgainAt = start.addingTimeInterval(0.8)
    hold.apply([updated], at: seenAgainAt)
    let recovered = try #require(hold.sides["ahead"])
    #expect(recovered.detections == [updated])
    #expect(recovered.missCount == 0)
    #expect(!recovered.isDimmed)
    #expect(recovered.lastSeenAt == seenAgainAt)
  }

  @Test func thirdConsecutiveMissRemovesTheCardBeforeItsTimeLimit() {
    var hold = DetectionHold()
    hold.apply([person("left")], at: start)
    for miss in 1...2 {
      hold.apply([], at: start.addingTimeInterval(Double(miss) * 0.3))
      #expect(hold.sides["left"]?.missCount == miss)
    }
    hold.apply([], at: start.addingTimeInterval(0.9))
    #expect(hold.sides.isEmpty)
  }

  @Test func timerRemovesACardAtTheHoldDeadlineWithoutMoreReplies() {
    var hold = DetectionHold()
    hold.apply([person("ahead")], at: start)
    hold.expire(at: start.addingTimeInterval(1.199))
    #expect(hold.sides["ahead"]?.missCount == 0)
    hold.expire(at: start.addingTimeInterval(1.2))
    #expect(hold.sides.isEmpty)
  }

  @Test func quietRepliesDoNotExtendTheLastSeenDeadline() {
    var hold = DetectionHold()
    hold.apply([person("ahead")], at: start)
    let missedAt = start.addingTimeInterval(0.8)
    hold.apply([], at: missedAt)
    #expect(hold.sides["ahead"]?.missCount == 1)
    #expect(hold.lastSuccessfulReplyAt == missedAt)
    hold.expire(at: start.addingTimeInterval(1.2))
    #expect(hold.sides.isEmpty)
    #expect(!hold.isStale(at: start.addingTimeInterval(2.5)))
  }

  @Test func movingToAnotherSideShowsItImmediatelyAndExpiresSidesIndependently() {
    var hold = DetectionHold()
    hold.apply([person("ahead")], at: start)
    hold.apply([person("right")], at: start.addingTimeInterval(0.4))
    #expect(hold.sides.count == 2)
    #expect(hold.sides["ahead"]?.isDimmed == true)
    #expect(hold.sides["right"]?.isDimmed == false)
    hold.expire(at: start.addingTimeInterval(1.2))
    #expect(hold.sides["ahead"] == nil)
    #expect(hold.sides["right"] != nil)
    hold.expire(at: start.addingTimeInterval(1.6))
    #expect(hold.sides.isEmpty)
  }

  @Test func futureSidesAndMultipleDetectionsKeepTheirOwnHistory() {
    var hold = DetectionHold()
    let above = [person("above"), FrameDetection(label: "limb", conf: 0.7, side: "above")]
    hold.apply(above + [person("left")], at: start)
    #expect(hold.sides["above"]?.detections == above)
    hold.apply([person("left", confidence: 0.95)], at: start.addingTimeInterval(0.4))
    #expect(hold.sides["above"]?.detections == above)
    #expect(hold.sides["above"]?.missCount == 1)
    #expect(hold.sides["left"]?.missCount == 0)
    hold.expire(at: start.addingTimeInterval(1.2))
    #expect(hold.sides["above"] == nil)
    #expect(hold.sides["left"] != nil)
  }

  @Test func staleStreamClearsEverySideAfterTwoSecondsWithoutASuccessfulReply() {
    var hold = DetectionHold()
    #expect(hold.isStale(at: start))
    hold.apply(["left", "ahead", "right", "above"].map { person($0) }, at: start)
    #expect(!hold.isStale(at: start.addingTimeInterval(2)))
    let staleAt = start.addingTimeInterval(2.001)
    #expect(hold.isStale(at: staleAt))
    hold.expire(at: staleAt)
    #expect(hold.sides.isEmpty)
  }

  @Test func timerTimestampBeforeANewReplyDoesNotDiscardItsCards() {
    var hold = DetectionHold()
    hold.apply([person("right")], at: start.addingTimeInterval(0.1))
    hold.expire(at: start)
    #expect(hold.sides["right"]?.isDimmed == false)
  }

  private func person(_ side: String, confidence: Double = 0.9) -> FrameDetection {
    FrameDetection(label: "person", conf: confidence, side: side)
  }
}
