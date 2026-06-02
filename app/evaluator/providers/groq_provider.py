"""
Groq provider implementation.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

from app.evaluator.errors import (
    ProviderConfigurationError,
    ProviderQuotaError,
    ProviderTransientError,
)
from app.evaluator.providers.base_evaluator import BaseEvaluator


class GroqEvaluator(BaseEvaluator):
    """Groq-backed evaluator."""

    provider_name = "Groq"

    def __init__(self, prompt_path: Path, profile_path: Path) -> None:
        load_dotenv()
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ProviderConfigurationError("GROQ_API_KEY missing from .env")

        self.model_id = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
        self._client = Groq(api_key=api_key)
        super().__init__(prompt_path, profile_path)

    def _generate_response(self, prompt: str) -> str:
        try:
            completion = self._client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a careful job evaluation engine. "
                            "Return only JSON that matches the requested schema."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            content = completion.choices[0].message.content or ""
            return content
        except Exception as exc:  # noqa: BLE001
            raise self._classify_exception(exc) from exc

    def _classify_exception(self, exc: Exception) -> Exception:
        message = str(exc).lower()
        status_code = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)

        if status_code == 429 and any(
            token in message
            for token in ("quota", "insufficient_quota", "quota exceeded")
        ):
            return ProviderQuotaError(f"Groq quota exceeded: {exc}")

        if status_code == 401 or "api key" in message or "authentication" in message:
            return ProviderConfigurationError(f"Groq authentication failed: {exc}")

        if status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
            return ProviderTransientError(f"Groq transient error: {exc}")

        if any(token in message for token in ("timeout", "timed out", "connection", "network")):
            return ProviderTransientError(f"Groq transient error: {exc}")

        return ProviderTransientError(f"Groq transient error: {exc}")
