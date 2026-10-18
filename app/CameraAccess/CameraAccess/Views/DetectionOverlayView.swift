import SwiftUI

/// Render in the camera preview's full bounds so aspect-fit boxes align with the
/// video. The glasses use the same renderer with their square display bounds.
struct DetectionOverlayView: View {
  let display: GlassesDisplayController

  var body: some View {
    GeometryReader { geometry in
      Image(uiImage: DetectionHUDRenderer.render(display.snapshot, size: geometry.size))
        .resizable()
        .frame(width: geometry.size.width, height: geometry.size.height)
        .accessibilityLabel(display.snapshot.accessibilityDescription)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .clipped()
    .allowsHitTesting(false)
    .accessibilityIdentifier("detection_overlay")
  }
}
