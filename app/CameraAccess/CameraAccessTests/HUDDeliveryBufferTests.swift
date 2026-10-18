@testable import CameraAccess
import Testing

struct HUDDeliveryBufferTests {
  @Test func serializesTransfersAndCoalescesToTheLatestDetections() throws {
    var buffer = HUDDeliveryBuffer("left")
    let first = try begin(&buffer)
    buffer.update("ahead")
    buffer.update("right")
    let blocked = buffer.begin()
    #expect(blocked == nil)
    let accepted = buffer.complete(first, succeeded: true)
    #expect(accepted)
    #expect(!buffer.isDelivered)
    let next = try begin(&buffer)
    #expect(next.value == "right")
    buffer.complete(next, succeeded: true)
    #expect(buffer.isDelivered)
    let unchanged = buffer.begin()
    #expect(unchanged == nil)
  }

  @Test func identicalSuccessfulUpdatesAreNotResent() throws {
    var buffer = HUDDeliveryBuffer("same HUD")
    let first = try begin(&buffer)
    buffer.complete(first, succeeded: true)
    buffer.update("same HUD")
    let unchanged = buffer.begin()
    #expect(unchanged == nil)
    #expect(buffer.isDelivered)
  }

  @Test func failedTransferRetriesTheLatestValueWithoutAcknowledgingIt() throws {
    var buffer = HUDDeliveryBuffer("old")
    let first = try begin(&buffer)
    buffer.update("new")
    buffer.complete(first, succeeded: false)
    #expect(buffer.delivered == nil)
    #expect(!buffer.isDelivered)
    let retry = buffer.begin()
    #expect(retry?.value == "new")
  }

  @Test func oldSessionCompletionsCannotAcknowledgeOrReleaseANewTransfer() throws {
    var buffer = HUDDeliveryBuffer("same HUD")
    let oldSession = try begin(&buffer)
    buffer.reset()
    let newSession = try begin(&buffer)
    let acceptedOld = buffer.complete(oldSession, succeeded: true)
    #expect(!acceptedOld)
    #expect(buffer.inFlight == newSession)
    #expect(buffer.delivered == nil)
    let acceptedNew = buffer.complete(newSession, succeeded: true)
    #expect(acceptedNew)
    #expect(buffer.isDelivered)
  }

  @Test func returningToAnEarlierHUDDuringATransferStillSendsTheCorrectImage() throws {
    var buffer = HUDDeliveryBuffer("empty")
    let empty = try begin(&buffer)
    buffer.complete(empty, succeeded: true)
    buffer.update("person left")
    let person = try begin(&buffer)
    buffer.update("empty")
    #expect(!buffer.isDelivered)
    buffer.complete(person, succeeded: true)
    let restored = buffer.begin()
    #expect(restored?.value == "empty")
  }

  @Test func reconnectResendsTheHUDToTheNewDisplay() throws {
    var buffer = HUDDeliveryBuffer("current HUD")
    let first = try begin(&buffer)
    buffer.complete(first, succeeded: true)
    buffer.reset()
    #expect(!buffer.isDelivered)
    let reconnected = buffer.begin()
    #expect(reconnected?.value == "current HUD")
  }

  private func begin<Value>(_ buffer: inout HUDDeliveryBuffer<Value>) throws -> HUDDeliveryBuffer<Value>.Ticket {
    let ticket = buffer.begin()
    return try #require(ticket)
  }
}
