"""Replay harness API-key behaviour, through main() with fake frames and a MockTransport."""

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

import scripts.replay as replay_mod
from scripts.replay import auth_headers

KEY = "k3y-" + "x" * 32
Handler = Callable[[httpx.Request], httpx.Response]


def test_key_is_stripped_and_blank_means_no_header() -> None:
    assert auth_headers({"IRIS_API_KEY": "abc\n"}) == {"X-Iris-Key": "abc"}
    assert auth_headers({"IRIS_API_KEY": "  \n"}) == {}


@pytest.mark.parametrize("bad", ["ab\x00cd", "ab\x7fcd", "ab\ncd", "cafékey"])
def test_key_with_control_or_non_ascii_characters_is_rejected_without_echo(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid characters") as exc:
        auth_headers({"IRIS_API_KEY": bad})
    assert bad not in str(exc.value)


class Harness:
    """Runs main() over fake frames and a MockTransport, recording every request."""

    def __init__(self, mp: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        self.mp, self.capsys = mp, capsys
        self.requests: list[httpx.Request] = []
        self.times = (0.0, 0.0, 0.0)

    def run(self, handler: Handler, url: str, key: str | None, *flags: str) -> tuple[int, str]:
        times = self.times

        def frames(_clip: Path) -> Iterator[tuple[float, bytes]]:
            yield from ((t, b"jpeg") for t in times)

        def recording(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        real_client = httpx.Client
        self.mp.setattr(replay_mod, "sample_frames", frames)
        self.mp.setattr(
            httpx,
            "Client",
            lambda **kw: real_client(transport=httpx.MockTransport(recording), **kw),
        )
        if key is None:
            self.mp.delenv("IRIS_API_KEY", raising=False)
        else:
            self.mp.setenv("IRIS_API_KEY", key)
        code = replay_mod.main(["clip.mp4", "--url", url, *flags])
        out = self.capsys.readouterr()
        return code, out.out + out.err


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Harness:
    return Harness(monkeypatch, capsys)


def ok(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health":
        return httpx.Response(200, json={"ready": True, "detector": "yolo", "commit": "abc"})
    return httpx.Response(200, json={"server_ms": 5.0, "alert": None})


def test_key_header_is_on_frame_posts_and_not_on_health(harness: Harness, tmp_path: Path) -> None:
    expect = tmp_path / "s.json"
    expect.write_text("[]")
    code, _ = harness.run(ok, "http://localhost:8001", KEY, "--preflight", "--expect", str(expect))
    assert code == 0
    posts = [r for r in harness.requests if r.method == "POST"]
    gets = [r for r in harness.requests if r.url.path == "/health"]
    assert len(posts) == 3 and len(gets) == 1
    assert all(r.headers["X-Iris-Key"] == KEY for r in posts)
    assert "X-Iris-Key" not in gets[0].headers


def test_401_on_every_frame_warns_once_counts_and_fails_without_leaking_key(
    harness: Harness,
) -> None:
    def deny(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": f"bad key {KEY}"})

    code, out = harness.run(deny, "http://localhost:8001", KEY)
    assert code != 0
    assert len(harness.requests) == 3
    assert out.count("401 from /frame") == 1
    assert "auth_errors=3" in out
    assert "FAIL" in out and "IRIS_API_KEY" in out
    assert KEY not in out


def test_401_fails_even_with_empty_expectations(harness: Harness, tmp_path: Path) -> None:
    expect = tmp_path / "s.json"
    expect.write_text("[]")
    code, out = harness.run(
        lambda _r: httpx.Response(401), "http://localhost:8001", None, "--expect", str(expect)
    )
    assert code != 0
    assert "PASS" not in out


def test_key_with_trailing_newline_is_stripped_and_works(harness: Harness) -> None:
    code, _ = harness.run(ok, "http://localhost:8001", KEY + "\n")
    assert code == 0
    assert all(r.headers["X-Iris-Key"] == KEY for r in harness.requests)


@pytest.mark.parametrize("bad", ["ab\x01cd" + KEY, "café" + KEY])
def test_invalid_key_exits_2_before_any_request_and_leaks_nothing(
    harness: Harness, bad: str
) -> None:
    code, out = harness.run(ok, "http://localhost:8001", bad)
    assert code == 2
    assert harness.requests == []
    assert KEY not in out
    assert "IRIS_API_KEY has invalid characters" in out


@pytest.mark.parametrize(
    ("url", "warns"),
    [
        ("http://203.0.113.5:8001", True),
        ("http://localhost:8001", False),
        ("http://127.0.0.1:8001", False),
        ("http://[::1]:8001", False),
        ("https://iris.example.com", False),
    ],
)
def test_cleartext_warning_only_for_non_local_http(harness: Harness, url: str, warns: bool) -> None:
    _, out = harness.run(ok, url, KEY)
    assert ("cleartext" in out) is warns
    assert KEY not in out


def test_no_cleartext_warning_without_a_key(harness: Harness) -> None:
    _, out = harness.run(ok, "http://203.0.113.5:8001", None)
    assert "cleartext" not in out


def test_local_protocol_error_prints_a_fixed_line_without_the_key(harness: Harness) -> None:
    def broken(_r: httpx.Request) -> httpx.Response:
        raise httpx.LocalProtocolError(f"Illegal header value {KEY!r}")

    code, out = harness.run(broken, "http://localhost:8001", KEY)
    assert KEY not in out
    assert "refused to send" in out
    assert code == 0  # an ordinary error, not an auth failure


def test_round_trip_counts_only_2xx_responses(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    monkeypatch.setattr(time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    harness.times = (0.0, 1000.0, 2000.0)  # never "late", so no frame is skipped
    statuses = iter([200, 401, 200])

    def mixed(_r: httpx.Request) -> httpx.Response:
        status = next(statuses)
        clock[0] += 1.0 if status == 200 else 100.0  # a 2xx takes 1 s, a 401 takes 100 s
        if status == 200:
            return httpx.Response(200, json={"server_ms": 5.0, "alert": None})
        return httpx.Response(status)

    _, out = harness.run(mixed, "http://localhost:8001", KEY)
    assert "round_trip_ms p50=1000 p95=1000" in out
