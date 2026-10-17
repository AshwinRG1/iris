"""The describer seam and its Gemini adapter (REST, text out only).

The pure helpers build the request and parse the reply, so tests need no network.
"""

import base64
from typing import Any, Protocol, runtime_checkable

import httpx

MAX_TEXT_CHARS = 300
_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class Describer(Protocol):
    async def describe(self, jpeg: bytes, boxes: str) -> str: ...


@runtime_checkable
class Closable(Protocol):
    async def aclose(self) -> None: ...


def build_request(
    model: str, api_key: str, system_instruction: str, jpeg: bytes, boxes: str
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """The URL, headers and JSON body. The key travels in a header, never in the URL."""
    body = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": boxes},
                    {
                        "inlineData": {
                            "mimeType": "image/jpeg",
                            "data": base64.b64encode(jpeg).decode("ascii"),
                        }
                    },
                ],
            }
        ],
        # Gemini 3 thinks at medium by default, too slow for a spoken line.
        "generationConfig": {"thinkingConfig": {"thinkingLevel": "low"}, "maxOutputTokens": 1024},
    }
    return _URL.format(model=model), {"x-goog-api-key": api_key}, body


def parse_text(payload: dict[str, Any]) -> str:
    """The answer text: thought parts skipped, whitespace collapsed, capped. "" if none."""
    candidates = payload.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") or [] if candidates else []
    text = " ".join((p.get("text") or "") for p in parts if not p.get("thought"))
    text = " ".join(text.split())
    if len(text) > MAX_TEXT_CHARS:
        cut = text[:MAX_TEXT_CHARS]
        text = cut[: cut.rfind(" ")] if " " in cut else cut  # end on a whole word
    return text


class GeminiDescriber:
    def __init__(self, api_key: str, model: str, system_instruction: str, timeout_s: float) -> None:
        self._api_key = api_key
        self._model = model
        self._system_instruction = system_instruction
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s + 1)
        )  # the caller's own timeout fires first

    async def describe(self, jpeg: bytes, boxes: str) -> str:
        url, headers, body = build_request(
            self._model, self._api_key, self._system_instruction, jpeg, boxes
        )
        response = await self._client.post(url, headers=headers, json=body)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return parse_text(payload)

    async def aclose(self) -> None:
        await self._client.aclose()
