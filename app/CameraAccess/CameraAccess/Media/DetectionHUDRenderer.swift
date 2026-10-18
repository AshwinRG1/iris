import UIKit

/// Shared transparent artwork for the phone's camera overlay. Flatten the same
/// artwork onto black for the glasses, where black pixels remain unlit.
@MainActor
enum DetectionHUDRenderer {
  static let size = CGSize(width: 600, height: 600)
  private static let accent = UIColor(red: 0.3, green: 0.95, blue: 1, alpha: 1)

  static func render(_ snapshot: DetectionHUDSnapshot) -> UIImage {
    let format = UIGraphicsImageRendererFormat()
    format.scale = 1
    format.opaque = false
    return UIGraphicsImageRenderer(size: size, format: format).image { context in
      var bottom: CGFloat = 16

      for (index, side) in DetectionHUDSnapshot.directions.enumerated() {
        guard let group = snapshot.groups.first(where: { $0.side == side }) else { continue }
        let count = min(group.detections.count, 3)
        let overflow = group.detections.count - count
        let height = CGFloat(52 + count * 36 + (overflow > 0 ? 28 : 0))
        let bounds = CGRect(x: CGFloat(24 + index * 188), y: 16, width: 176, height: height)
        bottom = max(bottom, bounds.maxY)
        context.cgContext.saveGState()
        context.cgContext.setAlpha(group.isDimmed ? 0.5 : 1)
        cardBorder(bounds)
        let arrow = ["←", "↑", "→"][index]
        text("\(arrow) \(side.uppercased())", in: CGRect(x: bounds.minX + 12, y: 30, width: 152, height: 28), size: 22, color: accent, weight: .bold, alignment: .center)

        for (row, detection) in group.detections.prefix(3).enumerated() {
          let y = CGFloat(66 + row * 36)
          let confidence = detection.conf.formatted(.percent.precision(.fractionLength(0)))
          let confidenceWidth = min(70, (confidence as NSString).size(withAttributes: [
            .font: UIFont.monospacedDigitSystemFont(ofSize: 22, weight: .regular),
          ]).width.rounded(.up))
          text(detection.label.capitalized, in: CGRect(x: bounds.minX + 12, y: y, width: 146 - confidenceWidth, height: 30), size: 24, weight: .semibold)
          text(confidence, in: CGRect(x: bounds.maxX - 12 - confidenceWidth, y: y + 2, width: confidenceWidth, height: 28), size: 22, alignment: .right)
        }
        if overflow > 0 {
          text("+\(overflow) more", in: CGRect(x: bounds.minX + 12, y: CGFloat(66 + count * 36), width: 152, height: 24), size: 20, color: accent, alignment: .center)
        }
        context.cgContext.restoreGState()
      }

      // Unknown directions remain explicitly named, never placed in a known column.
      let otherGroups = snapshot.groups.filter { !DetectionHUDSnapshot.directions.contains($0.side) }
      let otherDetections = otherGroups.flatMap { group in group.detections.map { (group, $0) } }
      for (row, entry) in otherDetections.prefix(2).enumerated() {
        let (group, detection) = entry
        let overflow = row == 1 && otherDetections.count > 2 ? " (+\(otherDetections.count - 2) more)" : ""
        let bounds = CGRect(x: 24, y: bottom + 12 + CGFloat(row * 48), width: 552, height: 40)
        context.cgContext.saveGState()
        context.cgContext.setAlpha(group.isDimmed ? 0.5 : 1)
        cardBorder(bounds)
        text("\(group.side): \(detection.label.capitalized) \(detection.conf.formatted(.percent.precision(.fractionLength(0))))\(overflow)",
             in: bounds.insetBy(dx: 12, dy: 6), size: 23, alignment: .center)
        context.cgContext.restoreGState()
      }
    }
  }

  static func displayImage(from overlay: UIImage) -> UIImage {
    let format = UIGraphicsImageRendererFormat()
    format.scale = 1
    format.opaque = true
    return UIGraphicsImageRenderer(size: size, format: format).image { context in
      UIColor.black.setFill()
      context.fill(CGRect(origin: .zero, size: size))
      overlay.draw(at: .zero)
    }
  }

  private static func cardBorder(_ rect: CGRect) {
    let border = UIBezierPath(roundedRect: rect, cornerRadius: 14)
    accent.withAlphaComponent(0.9).setStroke()
    border.lineWidth = 2
    border.stroke()
  }

  private static func text(
    _ value: String, in rect: CGRect, size: CGFloat, color: UIColor = .white,
    weight: UIFont.Weight = .regular, alignment: NSTextAlignment = .left
  ) {
    // Server-provided labels must remain on one line in the fixed display canvas.
    let line = value.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    let paragraph = NSMutableParagraphStyle()
    paragraph.lineBreakMode = .byTruncatingTail
    paragraph.alignment = alignment
    (line as NSString).draw(in: rect, withAttributes: [
      .font: UIFont.monospacedDigitSystemFont(ofSize: size, weight: weight),
      .foregroundColor: color,
      .paragraphStyle: paragraph,
    ])
  }
}
