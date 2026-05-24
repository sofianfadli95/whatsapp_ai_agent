"""LLM Factory — provider-agnostic chat model instantiation.

Reads LLM_PROVIDER and LLM_MODEL from settings. Validates the matching
credential env var. Returns a singleton factory that agent code uses to
obtain a BaseChatModel without importing provider-specific packages directly.

Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.5
Design: Property 28
"""

from __future__ import annotations

from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings, StartupConfigError

SUPPORTED_PROVIDERS = {"openai", "anthropic", "google"}

_CREDENTIAL_ENV_MAP: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
}


class LLMFactory(Protocol):
    """Protocol for the LLM factory exposed to agent code."""

    def get_chat_model(
        self, *, temperature: float = 0.0, max_tokens: int | None = None
    ) -> BaseChatModel: ...


class _LLMFactoryImpl:
    """Concrete LLM factory implementation."""

    def __init__(self, provider: str, model: str, api_key: str) -> None:
        self._provider = provider
        self._model = model
        self._api_key = api_key

    def get_chat_model(
        self, *, temperature: float = 0.0, max_tokens: int | None = None
    ) -> BaseChatModel:
        """Instantiate and return a BaseChatModel for the configured provider."""
        kwargs: dict = {
            "model": self._model,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        if self._provider == "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(api_key=self._api_key, **kwargs)  # type: ignore[arg-type]

        elif self._provider == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(api_key=self._api_key, **kwargs)  # type: ignore[arg-type]

        elif self._provider == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(google_api_key=self._api_key, **kwargs)  # type: ignore[arg-type]

        # Should never reach here due to validation in build_llm_factory
        raise StartupConfigError(f"Unsupported provider: {self._provider}")  # pragma: no cover


# Singleton instance
_singleton: _LLMFactoryImpl | None = None


def build_llm_factory(settings: Settings) -> LLMFactory:
    """Build and return the singleton LLM factory.

    Reads LLM_PROVIDER and LLM_MODEL from settings.
    Validates the required credential env var for the selected provider.
    Returns a singleton factory; raises StartupConfigError if invalid.

    Agent code MUST call factory.get_chat_model(); it MUST NOT import
    langchain_openai / langchain_anthropic / langchain_google_genai directly.
    """
    global _singleton

    if _singleton is not None:
        return _singleton

    # Validate provider
    provider = settings.LLM_PROVIDER.lower().strip()
    if provider not in SUPPORTED_PROVIDERS:
        raise StartupConfigError(
            f"Invalid LLM_PROVIDER='{settings.LLM_PROVIDER}'. "
            f"Supported values: {sorted(SUPPORTED_PROVIDERS)}"
        )

    # Validate model
    model = settings.LLM_MODEL.strip() if settings.LLM_MODEL else ""
    if not model:
        raise StartupConfigError("LLM_MODEL must be set to a non-empty value")

    # Validate credential
    credential_attr = _CREDENTIAL_ENV_MAP[provider]
    credential = getattr(settings, credential_attr, None)
    if credential is None:
        raise StartupConfigError(
            f"Missing API key for LLM_PROVIDER='{provider}'. "
            f"Set {credential_attr} environment variable."
        )

    # Extract the secret value
    api_key = credential.get_secret_value().strip()
    if not api_key:
        raise StartupConfigError(
            f"Empty API key for LLM_PROVIDER='{provider}'. "
            f"{credential_attr} must contain a non-empty value."
        )

    _singleton = _LLMFactoryImpl(provider=provider, model=model, api_key=api_key)
    return _singleton


def _reset_singleton() -> None:
    """Reset the singleton for testing purposes only."""
    global _singleton
    _singleton = None
