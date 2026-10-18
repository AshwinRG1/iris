/// One in-flight update and one replaceable latest value. A generation prevents
/// completions from a stopped display changing a newly attached display's state.
struct HUDDeliveryBuffer<Value: Equatable & Sendable> {
  struct Ticket: Equatable, Sendable {
    let generation: Int
    let value: Value
  }

  private(set) var latest: Value
  private(set) var delivered: Value?
  private(set) var inFlight: Ticket?
  private var generation = 0

  init(_ value: Value) { latest = value }

  var isDelivered: Bool { delivered == latest && inFlight == nil }

  mutating func update(_ value: Value) { latest = value }

  mutating func begin() -> Ticket? {
    guard inFlight == nil, delivered != latest else { return nil }
    let ticket = Ticket(generation: generation, value: latest)
    inFlight = ticket
    return ticket
  }

  @discardableResult
  mutating func complete(_ ticket: Ticket, succeeded: Bool) -> Bool {
    guard inFlight == ticket else { return false }
    inFlight = nil
    if succeeded { delivered = ticket.value }
    return true
  }

  mutating func reset() {
    generation += 1
    delivered = nil
    inFlight = nil
  }
}
