@testable import CameraAccess
import CoreGraphics
import Foundation
import Testing

struct DetectionBoxLayoutTests {
  private let canvas = CGSize(width: 600, height: 600)
  private let frame = CGSize(width: 640, height: 480)

  @Test func decodesOptionalBoxAndCenter() throws {
    let data = Data(#"{"label":"person","conf":0.87,"side":"left","box":{"x":0.41,"y":0.22,"w":0.12,"h":0.46},"center":{"x":0.47,"y":0.45}}"#.utf8)
    let detection = try JSONDecoder().decode(FrameDetection.self, from: data)
    #expect(detection.box == FrameDetectionBox(x: 0.41, y: 0.22, w: 0.12, h: 0.46))
    #expect(detection.center == FrameDetectionCenter(x: 0.47, y: 0.45))
  }

  @Test(arguments: ["", ",\"box\":null,\"center\":null"])
  func acceptsRepliesWithoutGeometry(_ fields: String) throws {
    let data = Data("{\"label\":\"person\",\"conf\":0.87,\"side\":\"left\"\(fields)}".utf8)
    let detection = try JSONDecoder().decode(FrameDetection.self, from: data)
    #expect(detection.label == "person")
    #expect(detection.conf == 0.87)
    #expect(detection.side == "left")
    #expect(detection.box == nil)
    #expect(detection.center == nil)
  }

  @Test func mapsFrameCornersWithLetterboxOffsets() throws {
    let topLeft = try #require(DetectionBoxLayout.rect(
      for: FrameDetectionBox(x: 0, y: 0, w: 0.2, h: 0.2), frameSize: frame, canvasSize: canvas
    ))
    #expect(topLeft == CGRect(x: 0, y: 75, width: 120, height: 90))
    let bottomRight = try #require(DetectionBoxLayout.rect(
      for: FrameDetectionBox(x: 0.8, y: 0.8, w: 0.2, h: 0.2), frameSize: frame, canvasSize: canvas
    ))
    #expect(bottomRight.minX == 480)
    #expect(bottomRight.minY == 435)
    #expect(bottomRight.maxX == 600)
    #expect(bottomRight.maxY == 525)
  }

  @Test func fitsPortraitAndLandscapeSourcesIntoThePhonePreview() {
    let fullFrame = FrameDetectionBox(x: 0, y: 0, w: 1, h: 1)
    let phone = CGSize(width: 400, height: 800)
    #expect(DetectionBoxLayout.rect(for: fullFrame, frameSize: frame, canvasSize: phone)
      == CGRect(x: 0, y: 250, width: 400, height: 300))
    #expect(DetectionBoxLayout.rect(for: fullFrame, frameSize: CGSize(width: 400, height: 800), canvasSize: canvas)
      == CGRect(x: 150, y: 0, width: 300, height: 600))
  }

  @Test func clipsBoxesToTheFrameAndSkipsInvalidGeometry() {
    #expect(DetectionBoxLayout.rect(for: FrameDetectionBox(x: -0.25, y: -0.25, w: 1.5, h: 1.5),
                                    frameSize: frame, canvasSize: canvas)
      == CGRect(x: 0, y: 75, width: 600, height: 450))
    for box in [
      FrameDetectionBox(x: 0, y: 0, w: 0, h: 1),
      FrameDetectionBox(x: 0, y: 0, w: 1, h: -1),
      FrameDetectionBox(x: 2, y: 0, w: 1, h: 1),
      FrameDetectionBox(x: .nan, y: 0, w: 1, h: 1),
    ] {
      #expect(DetectionBoxLayout.rect(for: box, frameSize: frame, canvasSize: canvas) == nil)
    }
    #expect(DetectionBoxLayout.rect(for: FrameDetectionBox(x: 0, y: 0, w: 1, h: 1),
                                    frameSize: .zero, canvasSize: canvas) == nil)
  }

  @Test func placesTheLabelAboveWhenItFitsExactly() {
    let box = CGRect(x: 100, y: 34, width: 120, height: 180)
    let label = DetectionBoxLayout.labelRect(for: box, labelSize: CGSize(width: 140, height: 30), canvasSize: canvas)
    #expect(label == CGRect(x: 100, y: 0, width: 140, height: 30))
  }

  @Test(arguments: [CGFloat(0), CGFloat(33)])
  func movesTheLabelBelowWhenThereIsNotEnoughRoomAbove(_ top: CGFloat) {
    let box = CGRect(x: 100, y: top, width: 120, height: 180)
    let label = DetectionBoxLayout.labelRect(for: box, labelSize: CGSize(width: 140, height: 30), canvasSize: canvas)
    #expect(label.minX == box.minX)
    #expect(label.minY == box.maxY + 4)
    #expect(label.height == 30)
  }

  @Test func keepsBottomLabelsAndLongLabelsInsideTheDisplay() {
    let box = CGRect(x: 570, y: 0, width: 30, height: 600)
    let label = DetectionBoxLayout.labelRect(for: box, labelSize: CGSize(width: 140, height: 30), canvasSize: canvas)
    #expect(label == CGRect(x: 460, y: 570, width: 140, height: 30))
    let wide = DetectionBoxLayout.labelRect(for: box, labelSize: CGSize(width: 800, height: 30), canvasSize: canvas)
    #expect(wide == CGRect(x: 0, y: 570, width: 600, height: 30))
  }
}
