import pytest

from scripts.replay import Sighting, auth_headers, check_health, check_sightings, parse_sightings

S = Sighting(10.0, 15.0)


def test_alert_at_exactly_start_plus_two_passes_and_just_after_fails() -> None:
    assert check_sightings([12.0], [S]) == []
    assert any("10.0" in p for p in check_sightings([12.01], [S]))


def test_alert_at_exactly_end_plus_grace_is_allowed_and_just_after_fails() -> None:
    assert check_sightings([10.5, 16.0], [S]) == []
    problems = check_sightings([10.5, 16.01], [S])
    assert len(problems) == 1
    assert "16.01" in problems[0]


def test_alert_a_tenth_before_the_start_is_outside_the_sighting() -> None:
    problems = check_sightings([9.9, 10.5], [S])
    assert len(problems) == 1
    assert "9.9" in problems[0]


def test_float_sums_do_not_fail_a_boundary_alert() -> None:
    assert check_sightings([2.36], [Sighting(0.36, 5.0)]) == []
    assert check_sightings([0.1 + 0.2 + 1.0], [Sighting(0.0, 0.3)]) == []


def test_sighting_without_any_alert_fails() -> None:
    assert any("10.0" in p for p in check_sightings([], [S]))


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


def test_health_passes_when_ready_with_a_detector() -> None:
    assert check_health({"ready": True, "detector": "yolo26s"}, None) == []


def test_health_fails_when_not_ready() -> None:
    assert check_health({"ready": False, "detector": "yolo26s", "load_error": "boom"}, None)


def test_health_commit_must_start_with_the_given_prefix() -> None:
    ok = {"ready": True, "detector": "d", "commit": "abc1234def"}
    assert check_health(ok, "abc12") == []
    assert check_health(ok, "zzz")
    assert check_health({"ready": True, "detector": "d", "commit": None}, "abc12")


def test_key_in_environment_becomes_the_iris_key_header() -> None:
    assert auth_headers({"IRIS_API_KEY": "k" * 32}) == {"X-Iris-Key": "k" * 32}


def test_unset_or_empty_key_sends_no_header() -> None:
    assert auth_headers({}) == {}
    assert auth_headers({"IRIS_API_KEY": ""}) == {}
