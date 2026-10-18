import CoreGraphics
import Foundation

/// Geometry shared by the phone preview and the square glasses display.
enum DetectionBoxLayout {
  static func rect(for box: FrameDetectionBox, frameSize: CGSize, canvasSize: CGSize) -> CGRect? {
    guard [box.x, box.y, box.w, box.h].allSatisfy(\.isFinite), box.w > 0, box.h > 0,
      frameSize.width.isFinite, frameSize.height.isFinite, frameSize.width > 0, frameSize.height > 0,
      canvasSize.width.isFinite, canvasSize.height.isFinite, canvasSize.width > 0, canvasSize.height > 0
    else { return nil }

    // Match the camera preview's aspect-fit transform, including its black margins.
    let scale = min(canvasSize.width / frameSize.width, canvasSize.height / frameSize.height)
    let shown = CGSize(width: frameSize.width * scale, height: frameSize.height * scale)
    let offset = CGPoint(x: (canvasSize.width - shown.width) / 2, y: (canvasSize.height - shown.height) / 2)
    let left = min(1, max(0, box.x))
    let top = min(1, max(0, box.y))
    let right = min(1, max(0, box.x + box.w))
    let bottom = min(1, max(0, box.y + box.h))
    guard right > left, bottom > top else { return nil }

    return CGRect(
      x: offset.x + CGFloat(left) * shown.width,
      y: offset.y + CGFloat(top) * shown.height,
      width: CGFloat(right - left) * shown.width,
      height: CGFloat(bottom - top) * shown.height
    )
  }

  static func labelRect(for box: CGRect, labelSize: CGSize, canvasSize: CGSize, gap: CGFloat = 4) -> CGRect {
    let width = min(labelSize.width, canvasSize.width)
    let height = min(labelSize.height, canvasSize.height)
    let above = box.minY - gap - height
    let y = above >= 0 ? above : box.maxY + gap

    // A box can span the whole screen. Keep its bottom label visible even when
    // there isn't enough room outside either edge; wide labels also stay onscreen.
    return CGRect(
      x: min(max(0, box.minX), canvasSize.width - width),
      y: min(max(0, y), canvasSize.height - height),
      width: width,
      height: height
    )
  }
}
