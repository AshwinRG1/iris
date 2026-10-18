import Foundation

struct FrameDetectionBox: Codable, Equatable, Sendable {
  let x, y, w, h: Double
}

struct FrameDetectionCenter: Codable, Equatable, Sendable {
  let x, y: Double
}

/// Keep labels and directions open-ended so new server classes do not break decoding.
struct FrameDetection: Codable, Equatable, Sendable {
  let label: String
  let conf: Double
  let side: String
  let box: FrameDetectionBox?
  let center: FrameDetectionCenter?

  init(label: String, conf: Double, side: String, box: FrameDetectionBox? = nil, center: FrameDetectionCenter? = nil) {
    self.label = label
    self.conf = conf
    self.side = side
    self.box = box
    self.center = center
  }
}

/// Display history only. Raw replies and spoken alerts never use this state.
struct DetectionHold: Equatable, Sendable {
  static let maximumMissCount = 3
  static let holdDuration: TimeInterval = 1.2
  static let staleDuration: TimeInterval = 2

  struct SideState: Equatable, Sendable {
    let detections: [FrameDetection]
    let lastSeenAt: Date
    var missCount = 0

    var isDimmed: Bool { missCount > 0 }
  }

  private(set) var sides: [String: SideState] = [:]
  private(set) var lastSuccessfulReplyAt: Date?
  // Expiring a positive reply must not turn it into an empty-frame claim.
  private(set) var lastReplyWasEmpty = false

  /// Call only for successful /frame replies; failures are neither hits nor misses.
  mutating func apply(_ detections: [FrameDetection], at date: Date) {
    let bySide = Dictionary(grouping: detections, by: \.side)
    lastSuccessfulReplyAt = date
    lastReplyWasEmpty = detections.isEmpty

    for side in Array(sides.keys) where bySide[side] == nil {
      sides[side]?.missCount += 1
    }
    for (side, detections) in bySide {
      sides[side] = SideState(detections: detections, lastSeenAt: date)
    }
    expire(at: date)
  }

  func isStale(at date: Date) -> Bool {
    guard let lastSuccessfulReplyAt else { return true }
    return date > lastSuccessfulReplyAt.addingTimeInterval(Self.staleDuration)
  }

  /// Also run on the overlay's timer so a stalled stream cannot leave cards up.
  mutating func expire(at date: Date) {
    guard !isStale(at: date) else {
      sides.removeAll()
      return
    }
    sides = sides.filter { _, state in
      state.missCount < Self.maximumMissCount
        && date < state.lastSeenAt.addingTimeInterval(Self.holdDuration)
    }
  }
}
