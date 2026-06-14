"""
Gemini provider implementation using google.genai SDK.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.evaluator.errors import (
    ProviderConfigurationError,
    ProviderQuotaError,
    ProviderTransientError,
)
from app.evaluator.providers.base_evaluator import BaseEvaluator


class GeminiEvaluator(BaseEvaluator):
    """Gemini-backed fallback evaluator."""

    provider_name = "Gemini"

    def __init__(self, prompt_path: Path, profile_path: Path) -> None:
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ProviderConfigurationError("GEMINI_API_KEY missing from .env")

        self._client = genai.Client(api_key=api_key)
        self.model_id = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        super().__init__(prompt_path, profile_path)

    def _generate_response(self, prompt: str) -> str:
        try:
            response = self._client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
            )
            return response.text or ""
        except Exception as exc:  # noqa: BLE001
            raise self._classify_exception(exc) from exc

    def _classify_exception(self, exc: Exception) -> Exception:
        message = str(exc).lower()
        status_code = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)

        if status_code == 401 or "api key" in message or "authentication" in message:
            return ProviderConfigurationError(f"Gemini authentication failed: {exc}")

        if status_code == 429 and any(
            token in message
            for token in ("quota", "insufficient_quota", "quota exceeded", "resource_exhausted")
        ):
            return ProviderQuotaError(f"Gemini quota exceeded: {exc}")

        if status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
            return ProviderTransientError(f"Gemini transient error: {exc}")

        if any(token in message for token in ("timeout", "timed out", "connection", "network")):
            return ProviderTransientError(f"Gemini transient error: {exc}")

        return ProviderTransientError(f"Gemini transient error: {exc}")
