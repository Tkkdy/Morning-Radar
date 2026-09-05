"""Project-level AI failures, independent of any provider SDK."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class AIErrorKind(StrEnum):
    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    BILLING_UNAVAILABLE = "billing_unavailable"
    RETRYABLE_TRANSPORT = "retryable_transport"
    INVALID_OUTPUT = "invalid_output"


class AIError(RuntimeError):
    kind: AIErrorKind

    def __init__(self, message: str, *, kind: AIErrorKind) -> None:
        super().__init__(message)
        self.kind = kind


class AIConfigurationError(AIError):
    def __init__(self, message: str) -> None:
        super().__init__(message, kind=AIErrorKind.CONFIGURATION)


class AIAuthenticationError(AIError):
    def __init__(self, message: str) -> None:
        super().__init__(message, kind=AIErrorKind.AUTHENTICATION)


class AIProviderUnavailable(AIError):
    def __init__(self, message: str) -> None:
        super().__init__(message, kind=AIErrorKind.PROVIDER_UNAVAILABLE)


class AIOutputError(AIError):
    def __init__(self, message: str) -> None:
        super().__init__(message, kind=AIErrorKind.INVALID_OUTPUT)


class AIBillingUnavailable(AIProviderUnavailable, AIOutputError):
    def __init__(self, message: str) -> None:
        AIError.__init__(self, message, kind=AIErrorKind.BILLING_UNAVAILABLE)


class AIRetryableTransportError(AIProviderUnavailable):
    def __init__(self, message: str) -> None:
        AIError.__init__(self, message, kind=AIErrorKind.RETRYABLE_TRANSPORT)


def normalize_provider_error(exc: Exception, provider: str) -> AIError:
    """Translate SDK-specific errors without exposing request or credential data."""
    status = getattr(exc, "status_code", None)
    response: Any = getattr(exc, "response", None)
    status = status or getattr(response, "status_code", None)
    message = str(exc).casefold()
    safe = f"{provider} request failed ({type(exc).__name__})"
    if status == 402 or "insufficient balance" in message or "billing unavailable" in message:
        return AIBillingUnavailable(safe)
    if status in {401, 403} or "authentication" in message or "invalid api key" in message:
        return AIAuthenticationError(safe)
    if status in {400, 404} and "model" in message:
        return AIConfigurationError(safe)
    if status == 429 or (isinstance(status, int) and status >= 500):
        return AIRetryableTransportError(safe)
    name = type(exc).__name__.casefold()
    if any(value in name for value in ("timeout", "connection", "ratelimit", "internalserver")):
        return AIRetryableTransportError(safe)
    return AIProviderUnavailable(safe)
