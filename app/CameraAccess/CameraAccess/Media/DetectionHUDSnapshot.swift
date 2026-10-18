import Foundation

/// Reply timestamps are excluded from equality, so fresh but identical content
/// doesn't cause another image transfer to the glasses.
struct DetectionHUDSnapshot: Equatable, Sendable {
  enum Status: String, Sendable {
    case off = "DETECTION OFF"
    case waitingForStream = "START CAMERA PREVIEW"
    case paused = "CAMERA PAUSED"
    case waitingForReply = "WAITING FOR DETECTIONS"
    case scanning = "SCANNING"
    case detected = "DETECTIONS"
    case unavailable = "WAITING FOR UPDATE"
  }

  struct Group: Equatable, Sendable {
    let side: String
    let detections: [FrameDetection]
    let isDimmed: Bool
  }

  static let directions = ["left", "ahead", "right"]
  static let off = Self(held: DetectionHold(), isEnabled: false, isStreaming: false, hasError: false, at: .distantPast)

  let status: Status
  let groups: [Group]
  let footer: String

  init(held: DetectionHold, isEnabled: Bool, isStreaming: Bool, hasError: Bool, isPaused: Bool = false, at date: Date) {
    var visible = held
    visible.expire(at: date)
    if !isEnabled || !isStreaming { visible = DetectionHold() }

    let sides = Self.directions + visible.sides.keys.filter { !Self.directions.contains($0) }.sorted()
    groups = sides.compactMap { side in
      guard let state = visible.sides[side] else { return nil }
      return Group(side: side, detections: state.detections, isDimmed: state.isDimmed)
    }

    if !isEnabled {
      status = .off
      footer = "Enable detections in Iris status"
    } else if !isStreaming {
      status = isPaused ? .paused : .waitingForStream
      footer = isPaused ? "Tap your glasses to resume" : "Start the live camera preview"
    } else if !groups.isEmpty {
      // Labels are open-ended; don't turn a future class into a person claim.
      status = .detected
      footer = hasError ? "Waiting for a fresh frame" : "Directions relative to the camera"
    } else if hasError || (held.lastSuccessfulReplyAt != nil && (held.isStale(at: date) || !held.lastReplyWasEmpty)) {
      status = .unavailable
      footer = "Detection data is unavailable"
    } else if held.lastSuccessfulReplyAt == nil {
      status = .waitingForReply
      footer = "Waiting for the first frame reply"
    } else {
      status = .scanning
      footer = "No detections in latest frame"
    }
  }

  var heading: String { status.rawValue }

  var accessibilityDescription: String {
    ([heading] + groups.map { group in
      let labels = group.detections.map {
        "\($0.label) \($0.conf.formatted(.percent.precision(.fractionLength(0))))"
      }.joined(separator: ", ")
      return "\(group.side): \(labels)\(group.isDimmed ? ", last seen" : "")"
    } + [footer]).joined(separator: ". ")
  }
}
