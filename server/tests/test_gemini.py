"""The Gemini adapter's pure helpers, build_describer, the prompt file and the new settings."""

import asyncio
import logging
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

import gemini
from config import Settings
from main import build_describer

SECRET = "sekret-gemini-key"
PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


def test_request_puts_the_key_in_a_header_and_never_in_the_url() -> None:
    url, headers, body = gemini.build_request(
        "gem-x", SECRET, "be brief", b"\xff\xd8jpg", "Boxes: x"
    )
    assert url == ("https://generativelanguage.googleapis.com/v1beta/models/gem-x:generateContent")
    assert headers["x-goog-api-key"] == SECRET
    assert SECRET not in url
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    user = body["contents"][0]
    assert user["role"] == "user"
    assert user["parts"][0] == {"text": "Boxes: x"}
    assert user["parts"][1]["inlineData"]["mimeType"] == "image/jpeg"
    assert user["parts"][1]["inlineData"]["data"] == "/9hqcGc="
    assert body["generationConfig"] == {
        "thinkingConfig": {"thinkingLevel": "low"},
        "maxOutputTokens": 1024,
    }


def _reply(*parts: dict[str, object]) -> dict[str, object]:
    return {"candidates": [{"content": {"parts": list(parts)}}]}


def test_parse_skips_thought_parts_and_tidies_whitespace() -> None:
    payload = _reply(
        {"text": "thinking...", "thought": True},
        {"text": "  Possible person\n to your  left. "},
        {"text": "Another."},
    )
    assert gemini.parse_text(payload) == "Possible person to your left. Another."


def test_parse_caps_the_text_at_300_characters() -> None:
    assert len(gemini.parse_text(_reply({"text": "a " * 400}))) <= 300


@pytest.mark.parametrize(
    "payload", [{}, {"promptFeedback": {"blockReason": "SAFETY"}}, {"candidates": []}, _reply()]
)
def test_parse_gives_empty_text_when_nothing_came_back(payload: dict[str, object]) -> None:
    assert gemini.parse_text(payload) == ""


def test_parse_tolerates_a_part_without_text() -> None:
    assert gemini.parse_text(_reply({"text": None}, {"text": "ok"}, {"inlineData": {}})) == "ok"


def test_parse_cuts_over_long_text_at_the_last_space() -> None:
    assert gemini.parse_text(_reply({"text": "a" * 295 + " bbbbbbbbbb"})) == "a" * 295


def test_the_http_timeout_follows_the_gemini_timeout() -> None:
    d = gemini.GeminiDescriber("k", "m", "s", timeout_s=3)
    assert d._client.timeout.read == 4.0
    asyncio.run(d.aclose())


def test_build_describer_strips_the_key_and_passes_the_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[object, ...]] = []

    class Spy:
        def __init__(self, *args: object) -> None:
            seen.append(args)

    monkeypatch.setattr("main.GeminiDescriber", Spy)
    build_describer(
        _settings(tmp_path, gemini_api_key=SecretStr(f"  {SECRET}\n"), gemini_timeout_s=2)
    )
    assert seen == [(SECRET, "gemini-3.8-flash", "system text", 2.0)]


def test_build_describer_logs_one_info_line_when_on(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="iris")
    d = build_describer(_settings(tmp_path, gemini_timeout_s=2))
    assert d is not None
    asyncio.run(d.aclose())  # type: ignore[attr-defined]
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert len(lines) == 1 and "gemini-3.8-flash" in lines[0] and "2" in lines[0]
    assert SECRET not in caplog.text


def test_a_blank_key_means_off(tmp_path: Path) -> None:
    assert build_describer(_settings(tmp_path, gemini_api_key=SecretStr(" \t"))) is None


def _settings(tmp_path: Path, **kw: object) -> Settings:
    prompt = tmp_path / "p.txt"
    prompt.write_text("system text")
    values: dict[str, object] = {
        "gemini_api_key": SecretStr(SECRET),
        "gemini_prompt_file": str(prompt),
    }
    return Settings(**{**values, **kw})  # type: ignore[arg-type]


def test_build_describer_with_everything_set_returns_an_adapter(tmp_path: Path) -> None:
    d = build_describer(_settings(tmp_path))
    assert isinstance(d, gemini.GeminiDescriber)
    asyncio.run(d.aclose())


def test_build_describer_uses_the_default_prompt_file_when_unset(tmp_path: Path) -> None:
    d = build_describer(_settings(tmp_path, gemini_prompt_file=""))
    assert isinstance(d, gemini.GeminiDescriber)
    asyncio.run(d.aclose())


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"gemini_api_key": ""}, "GEMINI_API_KEY"),
        ({"gemini_model": "  "}, "GEMINI_MODEL"),
        ({"gemini_prompt_file": "/nonexistent/p.txt"}, "GEMINI_PROMPT_FILE"),
    ],
)
def test_build_describer_turns_off_with_one_warning_naming_the_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, override: dict[str, object], reason: str
) -> None:
    caplog.set_level(logging.INFO, logger="iris")
    assert build_describer(_settings(tmp_path, **override)) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and reason in warnings[0].getMessage()
    assert SECRET not in caplog.text


def test_describe_off_is_the_kill_switch(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="iris")
    assert build_describer(_settings(tmp_path, describe="off")) is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert SECRET not in caplog.text


def test_the_key_is_hidden_in_the_settings_repr() -> None:
    assert SECRET not in repr(Settings(gemini_api_key=SecretStr(SECRET)))


@pytest.mark.parametrize(
    "bad",
    [
        {"gemini_timeout_s": 0},
        {"gemini_timeout_s": float("inf")},
        {"describe_cooldown_s": -1},
        {"describe_max_age_s": 0},
        {"describe_max_age_s": 10.5},
        {"describe": "maybe"},
    ],
)
def test_bad_describe_settings_fail_startup(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**bad)  # type: ignore[arg-type]


def test_prompt_file_has_the_instruction_and_an_indoor_example() -> None:
    text = (PROMPT_DIR / "describe.txt").read_text()
    assert "You assist search-and-rescue teams." in text
    assert "Speak directly to the rescuer" in text
    assert "seated at a desk indoors" in text
    assert text.count("Boxes:") >= 2
