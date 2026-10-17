import pytest

from scripts.replay import Sighting, check_sightings, parse_sightings, percentile


def test_alert_inside_the_two_second_start_window_passes() -> None:
    assert check_sightings([11.9], [Sighting(10.0, 15.0)]) == []


def test_sighting_without_an_alert_within_two_seconds_fails() -> None:
    problems = check_sightings([12.1], [Sighting(10.0, 15.0)])
    assert any("10.0" in p for p in problems)


def test_alert_after_the_sighting_plus_one_second_grace_fails() -> None:
    problems = check_sightings([10.5, 16.5], [Sighting(10.0, 15.0)])
    assert len(problems) == 1
    assert "16.5" in problems[0]


def test_alert_within_the_grace_period_passes() -> None:
    assert check_sightings([10.5, 16.0], [Sighting(10.0, 15.0)]) == []


def test_alert_before_any_sighting_fails() -> None:
    assert len(check_sightings([3.0, 10.5], [Sighting(10.0, 15.0)])) == 1


def test_no_sightings_and_no_alerts_passes_but_any_alert_fails() -> None:
    assert check_sightings([], []) == []
    assert len(check_sightings([1.0], [])) == 1


def test_two_sightings_each_need_their_own_alert() -> None:
    sightings = [Sighting(0.0, 4.0), Sighting(20.0, 24.0)]
    assert check_sightings([1.2, 21.0], sightings) == []
    assert len(check_sightings([1.2], sightings)) == 1


def test_parse_sightings_reads_start_end_pairs() -> None:
    assert parse_sightings('[{"start": 1.5, "end": 4}, {"start": 8, "end": 9.5}]') == [
        Sighting(1.5, 4.0),
        Sighting(8.0, 9.5),
    ]


@pytest.mark.parametrize("text", ['{"start": 1}', '[{"start": 1}]', '[{"start": 5, "end": 2}]'])
def test_parse_sightings_rejects_malformed_files(text: str) -> None:
    with pytest.raises(ValueError, match="sightings"):
        parse_sightings(text)


def test_percentile_is_nearest_rank() -> None:
    values = [float(v) for v in range(10, 210, 10)]
    assert percentile(values, 0.50) == 100.0
    assert percentile(values, 0.95) == 190.0
    assert percentile([], 0.5) is None
