"""
Shared evaluator exceptions.
"""


class ProviderError(RuntimeError):
    """Base class for provider failures."""


class ProviderQuotaError(ProviderError):
    """Raised when a provider reports quota exhaustion."""


class ProviderTransientError(ProviderError):
    """Raised for transient provider failures that can be retried."""


class ProviderValidationError(ProviderError):
    """Raised when a provider returns invalid JSON or schema data."""


class ProviderConfigurationError(ProviderError):
    """Raised when a provider is not configured correctly."""
