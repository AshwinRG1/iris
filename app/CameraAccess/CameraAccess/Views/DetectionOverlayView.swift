import SwiftUI

/// Composite the shared HUD artwork over the phone's camera preview. Transparent
/// areas reveal the camera; the glasses use unlit pixels to reveal the scene.
struct DetectionOverlayView: View {
  let display: GlassesDisplayController

  var body: some View {
    GeometryReader { geometry in
      Image(uiImage: display.previewImage)
        .resizable()
        .frame(width: geometry.size.width, height: geometry.size.width, alignment: .top)
        .accessibilityLabel(display.snapshot.accessibilityDescription)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    .clipped()
    .allowsHitTesting(false)
    .accessibilityIdentifier("detection_overlay")
  }
}
