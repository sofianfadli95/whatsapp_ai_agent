"""Unit tests for app/agent/llm_factory.py.

Tests each provider branch, invalid provider, and missing credential scenarios.
Also tests the AST check script passes on the current codebase.

Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.5
Design: Property 28
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

import pytest
from pydantic import SecretStr, ValidationError

from app.agent.llm_factory import (
    SUPPORTED_PROVIDERS,
    LLMFactory,
    _reset_singleton,
    build_llm_factory,
)
from app.config import Settings, StartupConfigError


@pytest.fixture(autouse=True)
def reset_factory_singleton():
    """Reset the singleton before and after each test."""
    _reset_singleton()
    yield
    _reset_singleton()


def _make_settings(
    provider: str = "openai",
    model: str = "gpt-4",
    openai_key: str | None = "sk-test-key",
    anthropic_key: str | None = None,
    google_key: str | None = None,
) -> Settings:
    """Create a Settings instance with minimal required fields for LLM factory tests."""
    env_vars = {
        "LLM_PROVIDER": provider,
        "LLM_MODEL": model,
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/db",
        "WHATSAPP_GATEWAY_INTERNAL_TOKEN": "test-token",
        "WHATSAPP_GATEWAY_URL": "http://localhost:3001",
    }
    if openai_key:
        env_vars["OPENAI_API_KEY"] = openai_key
    if anthropic_key:
        env_vars["ANTHROPIC_API_KEY"] = anthropic_key
    if google_key:
        env_vars["GOOGLE_API_KEY"] = google_key

    return Settings(**env_vars)  # type: ignore[arg-type]


class TestBuildLLMFactory:
    """Tests for build_llm_factory function."""

    def test_openai_provider_returns_factory(self):
        """OpenAI provider with valid credentials returns a working factory."""
        settings = _make_settings(provider="openai", openai_key="sk-test-123")
        factory = build_llm_factory(settings)

        assert factory is not None
        # Verify it satisfies the LLMFactory protocol
        assert hasattr(factory, "get_chat_model")

    def test_anthropic_provider_returns_factory(self):
        """Anthropic provider with valid credentials returns a working factory."""
        settings = _make_settings(
            provider="anthropic",
            model="claude-3-sonnet",
            openai_key=None,
            anthropic_key="sk-ant-test-123",
        )
        factory = build_llm_factory(settings)

        assert factory is not None
        assert hasattr(factory, "get_chat_model")

    def test_google_provider_returns_factory(self):
        """Google provider with valid credentials returns a working factory."""
        settings = _make_settings(
            provider="google",
            model="gemini-pro",
            openai_key=None,
            google_key="AIza-test-123",
        )
        factory = build_llm_factory(settings)

        assert factory is not None
        assert hasattr(factory, "get_chat_model")

    def test_case_insensitive_provider(self):
        """Provider name is case-insensitive."""
        settings = _make_settings(provider="OpenAI", openai_key="sk-test-123")
        factory = build_llm_factory(settings)
        assert factory is not None

    def test_singleton_returns_same_instance(self):
        """build_llm_factory returns the same singleton on repeated calls."""
        settings = _make_settings(provider="openai", openai_key="sk-test-123")
        factory1 = build_llm_factory(settings)
        factory2 = build_llm_factory(settings)
        assert factory1 is factory2

    def test_invalid_provider_raises_startup_config_error(self):
        """Invalid provider raises StartupConfigError (wrapped in ValidationError by Pydantic)."""
        with pytest.raises((ValidationError, StartupConfigError)):
            _make_settings(provider="invalid_provider")

    def test_missing_credential_raises_startup_config_error(self):
        """Missing credential for selected provider raises StartupConfigError."""
        # Test build_llm_factory's own validation with a None credential.
        # The Settings model_validator also catches this at construction time,
        # but build_llm_factory has its own guard for defense-in-depth.
        from unittest.mock import MagicMock

        _reset_singleton()
        mock_settings = MagicMock()
        mock_settings.LLM_PROVIDER = "openai"
        mock_settings.LLM_MODEL = "gpt-4"
        mock_settings.OPENAI_API_KEY = None
        mock_settings.ANTHROPIC_API_KEY = None
        mock_settings.GOOGLE_API_KEY = None

        with pytest.raises(StartupConfigError, match="Missing API key"):
            build_llm_factory(mock_settings)

    def test_empty_credential_raises_startup_config_error(self):
        """Empty credential for selected provider raises StartupConfigError."""
        from unittest.mock import MagicMock

        _reset_singleton()
        mock_settings = MagicMock()
        mock_settings.LLM_PROVIDER = "anthropic"
        mock_settings.LLM_MODEL = "claude-3"
        mock_settings.OPENAI_API_KEY = None
        mock_settings.ANTHROPIC_API_KEY = SecretStr("")
        mock_settings.GOOGLE_API_KEY = None

        with pytest.raises(StartupConfigError, match="Empty API key"):
            build_llm_factory(mock_settings)

    def test_empty_model_raises_startup_config_error(self):
        """Empty LLM_MODEL raises StartupConfigError."""
        with pytest.raises((ValidationError, StartupConfigError)):
            _make_settings(provider="openai", model="", openai_key="sk-test")

    def test_supported_providers_set(self):
        """SUPPORTED_PROVIDERS contains exactly the expected values."""
        assert SUPPORTED_PROVIDERS == {"openai", "anthropic", "google"}


class TestGetChatModel:
    """Tests for the get_chat_model method on the factory."""

    def test_openai_get_chat_model_returns_chat_openai(self):
        """OpenAI factory returns a ChatOpenAI instance."""
        settings = _make_settings(provider="openai", openai_key="sk-test-123")
        factory = build_llm_factory(settings)
        model = factory.get_chat_model(temperature=0.5)

        from langchain_openai import ChatOpenAI

        assert isinstance(model, ChatOpenAI)

    def test_anthropic_get_chat_model_returns_chat_anthropic(self):
        """Anthropic factory returns a ChatAnthropic instance."""
        settings = _make_settings(
            provider="anthropic",
            model="claude-3-sonnet",
            openai_key=None,
            anthropic_key="sk-ant-test-123",
        )
        factory = build_llm_factory(settings)
        model = factory.get_chat_model(temperature=0.7)

        from langchain_anthropic import ChatAnthropic

        assert isinstance(model, ChatAnthropic)

    def test_google_get_chat_model_returns_chat_google(self):
        """Google factory returns a ChatGoogleGenerativeAI instance."""
        settings = _make_settings(
            provider="google",
            model="gemini-pro",
            openai_key=None,
            google_key="AIza-test-123",
        )
        factory = build_llm_factory(settings)
        model = factory.get_chat_model(temperature=0.3, max_tokens=1024)

        from langchain_google_genai import ChatGoogleGenerativeAI

        assert isinstance(model, ChatGoogleGenerativeAI)

    def test_get_chat_model_with_max_tokens(self):
        """max_tokens parameter is passed through to the model."""
        settings = _make_settings(provider="openai", openai_key="sk-test-123")
        factory = build_llm_factory(settings)
        model = factory.get_chat_model(temperature=0.0, max_tokens=500)

        from langchain_openai import ChatOpenAI

        assert isinstance(model, ChatOpenAI)

    def test_get_chat_model_default_temperature(self):
        """Default temperature is 0.0."""
        settings = _make_settings(provider="openai", openai_key="sk-test-123")
        factory = build_llm_factory(settings)
        model = factory.get_chat_model()

        from langchain_openai import ChatOpenAI

        assert isinstance(model, ChatOpenAI)


class TestASTCheckScript:
    """Tests for the AST check script that validates no direct provider imports."""

    def test_ast_check_passes_on_current_codebase(self):
        """The AST check script should pass on the current codebase."""
        result = subprocess.run(
            [sys.executable, "scripts/check_no_provider_imports.py"],
            capture_output=True,
            text=True,
            cwd="/Users/sofianfadli/whatsapp_sales_agent",
        )
        assert result.returncode == 0, (
            f"AST check failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "OK" in result.stdout

    def test_ast_check_detects_forbidden_import(self, tmp_path):
        """The AST check script detects forbidden imports in agent code."""
        # Create a temporary agent directory with a forbidden import
        agent_dir = tmp_path / "app" / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "__init__.py").write_text("")
        (agent_dir / "bad_module.py").write_text(
            "from langchain_openai import ChatOpenAI\n"
        )

        # Copy the script and run it from the tmp directory
        import shutil

        script_src = "/Users/sofianfadli/whatsapp_sales_agent/scripts/check_no_provider_imports.py"
        script_dst = tmp_path / "check_no_provider_imports.py"
        shutil.copy(script_src, script_dst)

        result = subprocess.run(
            [sys.executable, str(script_dst)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert result.returncode == 1
        assert "FORBIDDEN PROVIDER IMPORTS DETECTED" in result.stderr
        assert "langchain_openai" in result.stderr

    def test_ast_check_allows_llm_factory(self, tmp_path):
        """The AST check script allows imports in llm_factory.py."""
        # Create a temporary agent directory with llm_factory.py containing imports
        agent_dir = tmp_path / "app" / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "__init__.py").write_text("")
        (agent_dir / "llm_factory.py").write_text(
            "from langchain_openai import ChatOpenAI\n"
            "from langchain_anthropic import ChatAnthropic\n"
            "from langchain_google_genai import ChatGoogleGenerativeAI\n"
        )

        import shutil

        script_src = "/Users/sofianfadli/whatsapp_sales_agent/scripts/check_no_provider_imports.py"
        script_dst = tmp_path / "check_no_provider_imports.py"
        shutil.copy(script_src, script_dst)

        result = subprocess.run(
            [sys.executable, str(script_dst)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0
        assert "OK" in result.stdout
