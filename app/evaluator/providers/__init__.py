"""
Provider package for AI evaluators.

The modules are imported lazily so the rest of the evaluation pipeline can be
tested even when a specific provider SDK is unavailable in the environment.
"""

from __future__ import annotations

from importlib import import_module

from app.evaluator.providers.base_evaluator import BaseEvaluator

__all__ = ["BaseEvaluator", "GroqEvaluator", "GeminiEvaluator"]


def __getattr__(name: str):
    if name == "GroqEvaluator":
        return import_module("app.evaluator.providers.groq_provider").GroqEvaluator
    if name == "GeminiEvaluator":
        return import_module("app.evaluator.providers.gemini_provider").GeminiEvaluator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
