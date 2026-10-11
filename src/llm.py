"""Thin Gemini wrapper: key rotation on quota/availability errors, call counting."""
from __future__ import annotations

import os
import re
import time

from google import genai
from google.genai import errors, types

RETRYABLE = {429, 500, 503, 504}


def api_keys() -> list[str]:
    """GEMINI_API_KEYS=key1,key2,... (rotated on 429/5xx), falling back to GEMINI_API_KEY / GOOGLE_API_KEY."""
    keys = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]
    for name in ("GEMINI_API_KEY", "GEMINI_KEY", "GOOGLE_API_KEY"):
        if os.getenv(name) and os.getenv(name) not in keys:
            keys.append(os.getenv(name).strip())
    return keys


def model_name() -> str:
    return os.getenv("MODEL_NAME") or "gemini-3.5-flash-lite"


class LLMUnavailable(RuntimeError):
    pass


class Gemini:
    # Shared across instances: rotation position, one live client per key (a discarded client closes its
    # HTTP connection), and keys the API rejected (401/403) so they are skipped for the rest of the process.
    _key_index = 0
    _clients: dict[str, genai.Client] = {}
    _rejected: set[str] = set()

    def __init__(self) -> None:
        self.keys = api_keys()
        if not self.keys:
            raise LLMUnavailable("No Gemini API key set (GEMINI_API_KEYS or GEMINI_API_KEY in .env)")
        self.calls = 0

    def _client(self, key: str) -> genai.Client:
        if key not in Gemini._clients:
            Gemini._clients[key] = genai.Client(api_key=key)
        return Gemini._clients[key]

    def generate(self, contents: list, config: types.GenerateContentConfig) -> types.GenerateContentResponse:
        last_error: Exception | None = None
        for attempt in range(len(self.keys) * 3):
            usable = [k for k in self.keys if k not in Gemini._rejected]
            if not usable:
                break
            key = usable[Gemini._key_index % len(usable)]
            try:
                self.calls += 1
                return self._client(key).models.generate_content(model=model_name(), contents=contents, config=config)
            except errors.APIError as exc:
                last_error = exc
                if exc.code in {401, 403}:
                    Gemini._rejected.add(key)
                elif exc.code not in RETRYABLE:
                    raise LLMUnavailable(f"Gemini error {exc.code}: {exc.message}") from exc
                Gemini._key_index += 1  # rotate to the next key
                if exc.code in RETRYABLE and (attempt + 1) % len(usable) == 0:
                    # every key tried: wait as long as the API asks (free tier is per-minute), capped at 65 s
                    hint = re.search(r"retry in ([\d.]+)s", str(exc))
                    time.sleep(min(float(hint.group(1)) + 1, 65) if hint else 2 * (attempt + 1) // len(usable))
        raise LLMUnavailable(f"Gemini unavailable: {last_error}")
