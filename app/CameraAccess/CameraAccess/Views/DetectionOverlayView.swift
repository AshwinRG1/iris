import SwiftUI

/// HUD for detections returned by this phone's POST /frame. The server's /latest
/// boxes belong to its saved JPEG, which may come from another uploader or replay.
/// Alert policy and speech remain driven exclusively by POST /frame's alert field.
struct DetectionOverlayView: View {
  let uploader: FrameUploader

  private let knownSides = ["left", "ahead", "right"]

  var body: some View {
    TimelineView(.periodic(from: .now, by: 0.5)) { context in
      VStack(spacing: 16) {
        HStack(alignment: .top, spacing: 8) {
          directionColumn("left", symbol: "arrow.left")
          directionColumn("ahead", symbol: "arrow.down")
          directionColumn("right", symbol: "arrow.right")
        }

        // Future directions get their own cards and expiry without a false position.
        let otherSides = uploader.held.sides.keys.filter { !knownSides.contains($0) }.sorted()
        if !otherSides.isEmpty {
          VStack(spacing: 6) {
            ForEach(otherSides, id: \.self) { side in
              if let state = uploader.held.sides[side] {
                detectionCard(side, state: state)
              }
            }
          }
          .transition(.opacity)
        }
      }
      .padding(.horizontal, 12)
      .frame(maxWidth: .infinity, alignment: .top)
      // Expiry owns the stale-stream rule; lastResponseAt can be nil after one failure.
      .onChange(of: context.date, initial: true) { _, date in
        uploader.expireHeldDetections(at: date)
      }
      // A lifecycle/settings reset clears immediately; ordinary card changes fade.
      .animation(uploader.held.lastSuccessfulReplyAt == nil ? nil : .easeInOut(duration: 0.2), value: uploader.held)
    }
    .allowsHitTesting(false)
    .accessibilityLabel("Live server detections")
    .accessibilityIdentifier("detection_overlay")
  }

  private func directionColumn(_ side: String, symbol: String) -> some View {
    VStack(spacing: 0) {
      if let state = uploader.held.sides[side] {
        detectionCard(side, state: state, symbol: symbol)
      }
    }
    .frame(maxWidth: .infinity)
  }

  private func detectionCard(_ side: String, state: DetectionHold.SideState, symbol: String? = nil) -> some View {
    VStack(spacing: 8) {
      Group {
        if let symbol {
          Label(side.capitalized, systemImage: symbol)
        } else {
          Text(side.capitalized)
        }
      }
      .font(.caption.weight(.bold))
      .foregroundStyle(.cyan)
      ForEach(Array(state.detections.enumerated()), id: \.offset) { _, detection in
        detectionLabel(detection)
      }
    }
    .padding(.vertical, 12)
    .padding(.horizontal, 6)
    .frame(maxWidth: .infinity)
    .background {
      RoundedRectangle(cornerRadius: 12)
        .fill(.black.opacity(0.25))
        .overlay {
          RoundedRectangle(cornerRadius: 12)
            .strokeBorder(.cyan.opacity(0.65), lineWidth: 1)
        }
    }
    .opacity(state.isDimmed ? 0.5 : 1)
    .transition(.opacity)
    .accessibilityElement(children: .combine)
  }

  private func detectionLabel(_ detection: FrameDetection) -> some View {
    HStack(alignment: .firstTextBaseline, spacing: 6) {
      Text(detection.label.capitalized)
        .font(.subheadline.weight(.semibold))
      Text(detection.conf, format: .percent.precision(.fractionLength(0)))
        .font(.caption.monospacedDigit())
    }
    .foregroundStyle(.white)
    .lineLimit(1)
    .minimumScaleFactor(0.8)
    .fixedSize(horizontal: false, vertical: true)
  }
}
