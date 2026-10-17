import SwiftUI

/// HUD for detections returned by this phone's POST /frame. The server's /latest
/// boxes belong to its saved JPEG, which may come from another uploader or replay.
/// Alert policy and speech remain driven exclusively by POST /frame's alert field.
struct DetectionOverlayView: View {
  let uploader: FrameUploader

  private let knownSides = ["left", "ahead", "right"]

  var body: some View {
    TimelineView(.periodic(from: .now, by: 0.5)) { context in
      if uploader.hasFreshDetections(at: context.date) {
        VStack(spacing: 16) {
          HStack(alignment: .top, spacing: 8) {
            directionColumn("left", symbol: "arrow.left")
            directionColumn("ahead", symbol: "arrow.up")
            directionColumn("right", symbol: "arrow.right")
          }

          // Future directions remain readable without assigning them a false position.
          let otherDetections = uploader.detections.filter { !knownSides.contains($0.side) }
          if !otherDetections.isEmpty {
            VStack(spacing: 6) {
              ForEach(Array(otherDetections.enumerated()), id: \.offset) { _, detection in
                detectionLabel(detection, showSide: true)
              }
            }
            .padding(10)
            .background(.black.opacity(0.65), in: RoundedRectangle(cornerRadius: 12))
          }
        }
        .padding(.horizontal, 12)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
      }
    }
    .allowsHitTesting(false)
    .accessibilityLabel("Live server detections")
    .accessibilityIdentifier("detection_overlay")
  }

  private func directionColumn(_ side: String, symbol: String) -> some View {
    let detections = uploader.detections.filter { $0.side == side }
    return VStack(spacing: 8) {
      if !detections.isEmpty {
        Label(side.capitalized, systemImage: symbol)
          .font(.caption.weight(.bold))
          .foregroundStyle(.yellow)
        ForEach(Array(detections.enumerated()), id: \.offset) { _, detection in
          detectionLabel(detection, showSide: false)
        }
      }
    }
    .padding(.vertical, detections.isEmpty ? 0 : 12)
    .padding(.horizontal, 6)
    .frame(maxWidth: .infinity)
    .background {
      if !detections.isEmpty {
        RoundedRectangle(cornerRadius: 12)
          .fill(.black.opacity(0.65))
          .overlay {
            RoundedRectangle(cornerRadius: 12)
              .strokeBorder(.yellow.opacity(0.65), lineWidth: 1)
          }
      }
    }
    .accessibilityElement(children: .combine)
  }

  private func detectionLabel(_ detection: FrameDetection, showSide: Bool) -> some View {
    VStack(spacing: 3) {
      Text(detection.label.capitalized)
        .font(.subheadline.weight(.semibold))
      if showSide {
        Text(detection.side)
          .font(.caption)
      }
      Text(detection.conf, format: .percent.precision(.fractionLength(0)))
        .font(.caption.monospacedDigit())
    }
    .foregroundStyle(.white)
    .multilineTextAlignment(.center)
    .fixedSize(horizontal: false, vertical: true)
  }
}
