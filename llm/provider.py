"""
LLM provider abstraction layer.
generate_json(prompt) -> dict  is the single interface used by all callers.

Priority order:
  1. LLM_PROVIDER env var ("gemini" | "claude")
  2. Whichever API key is present
  3. NoLLMProvider (offline fallback — callers must handle None return)
"""
from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod

from dotenv import load_dotenv

load_dotenv()


# ── JSON extraction ──────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | list:
    """Strip markdown fences and parse JSON from LLM response."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()
    return json.loads(text)


# ── Abstract base ────────────────────────────────────────────────────────────

class LLMProvider(ABC):
    @abstractmethod
    def _call(self, prompt: str) -> str:
        """Return raw text response from the model."""

    def generate_json(self, prompt: str, retries: int = 2) -> dict | list | None:
        """
        Call the model and return parsed JSON.
        Returns None on persistent failure — callers must fall back gracefully.
        """
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                raw = self._call(prompt)
                return _extract_json(raw)
            except json.JSONDecodeError as e:
                last_error = e
            except Exception as e:
                last_error = e
                break  # non-JSON errors (network, auth) won't improve with retries
        return None

    @property
    def available(self) -> bool:
        return True


# ── Gemini ───────────────────────────────────────────────────────────────────

class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str):
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(
            "gemini-2.5-flash",
            generation_config={"temperature": 0.1, "response_mime_type": "application/json"},
        )

    def _call(self, prompt: str) -> str:
        response = self._model.generate_content(prompt)
        return response.text


# ── Claude ───────────────────────────────────────────────────────────────────

class ClaudeProvider(LLMProvider):
    def __init__(self, api_key: str):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)

    def _call(self, prompt: str) -> str:
        msg = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text


# ── Offline fallback ─────────────────────────────────────────────────────────

class NoLLMProvider(LLMProvider):
    def _call(self, prompt: str) -> str:
        raise RuntimeError("No LLM provider configured. Set GEMINI_API_KEY or ANTHROPIC_API_KEY.")

    def generate_json(self, prompt: str, retries: int = 2) -> None:
        return None

    @property
    def available(self) -> bool:
        return False


# ── Factory ──────────────────────────────────────────────────────────────────

_cached_provider: LLMProvider | None = None


def get_provider(force_refresh: bool = False) -> LLMProvider:
    """Return a cached LLM provider. Thread-safe for single-process Streamlit."""
    global _cached_provider
    if _cached_provider is not None and not force_refresh:
        return _cached_provider

    preferred = os.getenv("LLM_PROVIDER", "gemini").lower()
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    claude_key  = os.getenv("ANTHROPIC_API_KEY", "")

    def _try_gemini():
        if gemini_key:
            try:
                return GeminiProvider(gemini_key)
            except Exception:
                pass
        return None

    def _try_claude():
        if claude_key:
            try:
                return ClaudeProvider(claude_key)
            except Exception:
                pass
        return None

    if preferred == "claude":
        provider = _try_claude() or _try_gemini()
    else:
        provider = _try_gemini() or _try_claude()

    _cached_provider = provider or NoLLMProvider()
    return _cached_provider


def provider_status() -> dict:
    """Return availability info for the settings page."""
    p = get_provider()
    return {
        "available": p.available,
        "type": type(p).__name__,
        "gemini_key_set": bool(os.getenv("GEMINI_API_KEY")),
        "claude_key_set": bool(os.getenv("ANTHROPIC_API_KEY")),
    }
