"""Alert policy (ADR 0002): a pure decision over state, hits, time and tuning."""

from domain import Detection, PolicyState, Side, Tuning


def decide(
    state: PolicyState, hits: list[Detection], now_ms: int, tuning: Tuning
) -> tuple[PolicyState, str | None]:
    """Return the new state and the alert text, or None. `hits` are this frame's hits."""
    fresh = (e for e in state.recent if now_ms - e[0] <= tuning.window_max_age_ms)
    recent = (*fresh, (now_ms, bool(hits)))[-tuning.window :]
    if not hits or sum(hit for _, hit in recent) < tuning.needed:
        return _with(state, recent), None
    side: Side = max(hits, key=lambda d: d.conf).side
    if state.last_alert_ms is not None:
        since = now_ms - state.last_alert_ms
        if since < tuning.cooldown_ms:
            return _with(state, recent), None
        if since < tuning.same_side_repeat_ms and side == state.last_alert_side:
            return _with(state, recent), None
    return PolicyState(recent, now_ms, side), f"possible person, {side}"


def _with(state: PolicyState, recent: tuple[tuple[int, bool], ...]) -> PolicyState:
    return PolicyState(recent, state.last_alert_ms, state.last_alert_side)
