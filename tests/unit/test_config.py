"""Unit tests for app/config.py — covers each invalid-config branch.

Tests verify that StartupConfigError is raised for:
(a) Invalid LLM_PROVIDER
(b) Missing/empty credential for the selected provider
(c) Empty LLM_MODEL
(d) PORT out of range
(e) Missing WHATSAPP_GATEWAY_URL

Also verifies that a valid configuration loads successfully.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.config import Settings, StartupConfigError


# --- Helpers ---

def _valid_env() -> dict[str, str]:
    """Return a minimal valid environment dict."""
    return {
        "LLM_PROVIDER": "openai",
        "LLM_MODEL": "gpt-4.1-mini",
        "OPENAI_API_KEY": "sk-test-key-123",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/db",
        "PORT": "8080",
        "WHATSAPP_GATEWAY_URL": "http://localhost:3001",
        "WHATSAPP_GATEWAY_INTERNAL_TOKEN": "test-token-abc",
    }


def _build_settings(overrides: dict[str, str | None] | None = None) -> Settings:
    """Build Settings from a valid base env with optional overrides.

    Keys set to None are removed from the env dict.
    Uses patch.dict to isolate from the real system environment.
    """
    env = _valid_env()
    if overrides:
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
    # Isolate from real environment variables and .env file
    with patch.dict(os.environ, env, clear=True):
        return Settings(_env_file=None)


# --- Happy path ---

class TestValidConfig:
    def test_valid_openai_config(self):
        settings = _build_settings()
        assert settings.LLM_PROVIDER == "openai"
        assert settings.LLM_MODEL == "gpt-4.1-mini"
        assert settings.PORT == 8080

    def test_valid_anthropic_config(self):
        settings = _build_settings({
            "LLM_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "sk-ant-test-key",
        })
        assert settings.LLM_PROVIDER == "anthropic"

    def test_valid_google_config(self):
        settings = _build_settings({
            "LLM_PROVIDER": "google",
            "GOOGLE_API_KEY": "AIza-test-key",
        })
        assert settings.LLM_PROVIDER == "google"

    def test_provider_case_insensitive(self):
        settings = _build_settings({"LLM_PROVIDER": "OpenAI"})
        assert settings.LLM_PROVIDER == "OpenAI"  # stored as-is, validated case-insensitively

    def test_defaults_applied(self):
        settings = _build_settings()
        assert settings.RAG_TOP_K == 5
        assert settings.RAG_SIMILARITY_THRESHOLD == 0.7
        assert settings.EMBEDDING_MODEL == "text-embedding-3-small"
        assert settings.ESCALATION_CONFIDENCE_THRESHOLD == 0.6
        assert settings.PAYMENT_PROVIDER_NAME == "sandbox"


# --- (a) Invalid LLM_PROVIDER ---

class TestInvalidProvider:
    def test_unsupported_provider_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"LLM_PROVIDER": "azure"})
        assert "StartupConfigError" in str(exc_info.value) or "Invalid LLM_PROVIDER" in str(exc_info.value)

    def test_empty_provider_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"LLM_PROVIDER": ""})
        assert "LLM_PROVIDER" in str(exc_info.value)

    def test_whitespace_only_provider_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"LLM_PROVIDER": "   "})
        assert "LLM_PROVIDER" in str(exc_info.value)


# --- (b) Missing/empty credential ---

class TestMissingCredential:
    def test_openai_missing_key_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": None})
        assert "API key" in str(exc_info.value) or "Missing" in str(exc_info.value)

    def test_openai_empty_key_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": ""})
        assert "API key" in str(exc_info.value) or "Missing" in str(exc_info.value)

    def test_openai_whitespace_key_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "   "})
        assert "API key" in str(exc_info.value) or "Missing" in str(exc_info.value)

    def test_anthropic_missing_key_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({
                "LLM_PROVIDER": "anthropic",
                "ANTHROPIC_API_KEY": None,
            })
        assert "API key" in str(exc_info.value) or "Missing" in str(exc_info.value)

    def test_google_missing_key_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({
                "LLM_PROVIDER": "google",
                "GOOGLE_API_KEY": None,
            })
        assert "API key" in str(exc_info.value) or "Missing" in str(exc_info.value)


# --- (c) Empty LLM_MODEL ---

class TestEmptyModel:
    def test_empty_model_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"LLM_MODEL": ""})
        assert "LLM_MODEL" in str(exc_info.value)

    def test_whitespace_model_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"LLM_MODEL": "   "})
        assert "LLM_MODEL" in str(exc_info.value)


# --- (d) PORT out of range ---

class TestInvalidPort:
    def test_port_zero_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"PORT": "0"})
        assert "PORT" in str(exc_info.value) or "greater than" in str(exc_info.value)

    def test_port_negative_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"PORT": "-1"})
        assert "PORT" in str(exc_info.value) or "greater than" in str(exc_info.value)

    def test_port_too_high_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"PORT": "65536"})
        assert "PORT" in str(exc_info.value) or "less than" in str(exc_info.value)

    def test_port_non_integer_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"PORT": "abc"})
        assert "PORT" in str(exc_info.value) or "int" in str(exc_info.value).lower()


# --- (e) Missing WHATSAPP_GATEWAY_URL ---

class TestMissingGatewayUrl:
    def test_missing_gateway_url_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"WHATSAPP_GATEWAY_URL": None})
        assert "WHATSAPP_GATEWAY_URL" in str(exc_info.value)

    def test_empty_gateway_url_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            _build_settings({"WHATSAPP_GATEWAY_URL": ""})
        assert "WHATSAPP_GATEWAY_URL" in str(exc_info.value) or "url" in str(exc_info.value).lower()
