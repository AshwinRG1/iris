@testable import CameraAccess
import CoreGraphics
import Foundation
import Testing

struct DetectionHUDSnapshotTests {
  private let start = Date(timeIntervalSince1970: 1_000)

  @Test func keepsDirectionsOrderedAndUnknownDirectionsExplicit() {
    let detections = [person("right"), person("above"), person("left"), person("ahead"), person("behind")]
    var held = DetectionHold()
    held.apply(detections, at: start)
    let hud = snapshot(held)
    #expect(hud.groups.map(\.side) == ["left", "ahead", "right", "above", "behind"])
    #expect(hud.groups.flatMap(\.detections).count == detections.count)
    #expect(hud.accessibilityDescription.contains("above: person 90%"))
  }

  @Test func missDimsOnlyTheHeldDirectionAndAHitRestoresIt() {
    var held = DetectionHold()
    held.apply([person("ahead")], at: start)
    held.apply([person("right")], at: start.addingTimeInterval(0.4))
    let missed = snapshot(held, after: 0.4)
    #expect(missed.groups.first { $0.side == "ahead" }?.isDimmed == true)
    #expect(missed.groups.first { $0.side == "right" }?.isDimmed == false)
    held.apply([person("ahead")], at: start.addingTimeInterval(0.8))
    #expect(snapshot(held, after: 0.8).groups.first { $0.side == "ahead" }?.isDimmed == false)
  }

  @Test func expiryWorksWithoutMutatingUploaderHistoryOrReceivingMoreReplies() {
    var held = DetectionHold()
    held.apply([person("left")], at: start)
    #expect(snapshot(held, after: 1.199).groups.count == 1)
    #expect(snapshot(held, after: 1.2).groups.isEmpty)
    #expect(snapshot(held, after: 1.2).status == .unavailable)
    #expect(snapshot(held, after: 2.001).status == .unavailable)
    #expect(held.sides.count == 1)
  }

  @Test func expiredPositiveReplyNeverClaimsThatTheLatestFrameHadNoDetections() {
    var held = DetectionHold()
    held.apply([person("left")], at: start)
    // The camera overlay may already have expired the stored hold as well.
    held.expire(at: start.addingTimeInterval(1.2))
    #expect(snapshot(held, after: 1.3).status == .unavailable)
    held.apply([], at: start.addingTimeInterval(1.4))
    #expect(snapshot(held, after: 1.4).status == .scanning)
  }

  @Test func disablingStoppingAndPausingHidePreviousDetectionsImmediately() {
    var held = DetectionHold()
    held.apply([person("left")], at: start)
    let off = DetectionHUDSnapshot(held: held, isEnabled: false, isStreaming: true, hasError: false, at: start)
    let stopped = DetectionHUDSnapshot(held: held, isEnabled: true, isStreaming: false, hasError: false, at: start)
    let paused = DetectionHUDSnapshot(held: held, isEnabled: true, isStreaming: false, hasError: false, isPaused: true, at: start)
    #expect(off.groups.isEmpty && off.status == .off)
    #expect(stopped.groups.isEmpty && stopped.status == .waitingForStream)
    #expect(paused.groups.isEmpty && paused.status == .paused)
    #expect(paused.footer == "Tap your glasses to resume")
  }

  @Test func waitingAndFailedRepliesNeverClaimAnEmptySuccessfulFrame() {
    let empty = DetectionHold()
    #expect(snapshot(empty).status == .waitingForReply)
    #expect(snapshot(empty, hasError: true).status == .unavailable)
    var quiet = empty
    quiet.apply([], at: start)
    #expect(snapshot(quiet).status == .scanning)
    #expect(snapshot(quiet).footer == "No detections in latest frame")
  }

  @Test func transientFailurePreservesRecentCardsButCannotKeepThemPastExpiry() {
    var held = DetectionHold()
    held.apply([person("right")], at: start)
    let recent = snapshot(held, after: 0.4, hasError: true)
    #expect(recent.groups.count == 1)
    #expect(recent.footer == "Waiting for a fresh frame")
    let expired = snapshot(held, after: 1.2, hasError: true)
    #expect(expired.groups.isEmpty)
    #expect(expired.status == .unavailable)
  }

  @Test func identicalVisibleRepliesDoNotRequestAnotherDisplayImage() {
    var held = DetectionHold()
    held.apply([person("ahead")], at: start)
    let first = snapshot(held)
    held.apply([person("ahead")], at: start.addingTimeInterval(0.4))
    #expect(snapshot(held, after: 0.4) == first)
    #expect(snapshot(held, after: 0.8) == first)
  }

  @Test func futureClassesAndDuplicateDetectionsArePreserved() {
    var held = DetectionHold()
    let limb = FrameDetection(label: "limb", conf: 0.77, side: "above")
    held.apply([limb, limb], at: start)
    let hud = snapshot(held)
    #expect(hud.heading == "DETECTIONS")
    #expect(hud.groups.first?.detections == [limb, limb])
  }

  @Test func boxMovementAndFrameAspectChangesUpdateTheDisplay() {
    var held = DetectionHold()
    let first = FrameDetection(label: "person", conf: 0.9, side: "ahead",
                               box: FrameDetectionBox(x: 0.4, y: 0.1, w: 0.2, h: 0.4))
    held.apply([first], at: start)
    let initial = snapshot(held)
    let moved = FrameDetection(label: "person", conf: 0.9, side: "ahead",
                               box: FrameDetectionBox(x: 0.4, y: 0.2, w: 0.2, h: 0.4))
    held.apply([moved], at: start.addingTimeInterval(0.4))
    #expect(snapshot(held, after: 0.4) != initial)
    let portrait = DetectionHUDSnapshot(held: held, isEnabled: true, isStreaming: true, hasError: false,
                                        frameSize: CGSize(width: 480, height: 640), at: start.addingTimeInterval(0.4))
    #expect(portrait != snapshot(held, after: 0.4))
  }

  private func snapshot(_ held: DetectionHold, after delay: TimeInterval = 0, hasError: Bool = false) -> DetectionHUDSnapshot {
    DetectionHUDSnapshot(held: held, isEnabled: true, isStreaming: true, hasError: hasError, at: start.addingTimeInterval(delay))
  }

  private func person(_ side: String) -> FrameDetection {
    FrameDetection(label: "person", conf: 0.9, side: side)
  }
}
