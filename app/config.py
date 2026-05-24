"""Application configuration with Pydantic Settings and fail-fast validation.

All environment variables are declared here. The model_validator raises
StartupConfigError on invalid combinations so the process exits before
binding any port (Req 12.3, 12.4, 12.10).
"""

from __future__ import annotations

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StartupConfigError(ValueError):
    """Raised when configuration is invalid at startup.

    Inherits from ValueError so Pydantic wraps it in ValidationError during
    model construction. Caught by the application lifespan to abort before
    binding the HTTP listener.
    """


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # --- LLM ---
    LLM_PROVIDER: str = Field(description="LLM provider: openai, anthropic, or google")
    LLM_MODEL: str = Field(default="", description="Model identifier passed to the provider")
    OPENAI_API_KEY: SecretStr | None = None
    ANTHROPIC_API_KEY: SecretStr | None = None
    GOOGLE_API_KEY: SecretStr | None = None

    # --- Database ---
    DATABASE_URL: str = Field(description="PostgreSQL connection string")

    # --- HTTP ---
    PORT: int = Field(default=8080, ge=1, le=65535)

    # --- WhatsApp Gateway (Node.js + Baileys) ---
    WHATSAPP_GATEWAY_URL: AnyHttpUrl | None = None
    WHATSAPP_GATEWAY_INTERNAL_TOKEN: SecretStr = Field(
        description="Bearer token for gateway <-> backend auth"
    )
    WHATSAPP_BACKEND_INBOUND_URL: AnyHttpUrl | None = None

    # --- Payment ---
    PAYMENT_PROVIDER_NAME: str = Field(default="sandbox")
    PAYMENT_PROVIDER_BASE_URL: AnyHttpUrl | None = None
    PAYMENT_WEBHOOK_SECRET: SecretStr | None = None

    # --- RAG ---
    RAG_TOP_K: int = Field(default=5, ge=1, le=20)
    RAG_SIMILARITY_THRESHOLD: float = Field(default=0.7, ge=0.0, le=1.0)
    EMBEDDING_MODEL: str = Field(default="text-embedding-3-small")

    # --- Escalation ---
    ESCALATION_CONFIDENCE_THRESHOLD: float = Field(default=0.6, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_startup_config(self) -> "Settings":
        """Fail-fast validation for critical configuration.

        Raises StartupConfigError when:
        (a) LLM_PROVIDER is not one of the supported values
        (b) The matching credential for the provider is missing/empty
        (c) LLM_MODEL is empty
        (d) PORT is out of range (already handled by Field, but explicit check for clarity)
        (e) WHATSAPP_GATEWAY_URL is missing
        """
        supported_providers = {"openai", "anthropic", "google"}

        # (a) Validate LLM_PROVIDER
        provider = self.LLM_PROVIDER.lower().strip()
        if provider not in supported_providers:
            raise StartupConfigError(
                f"Invalid LLM_PROVIDER='{self.LLM_PROVIDER}'. "
                f"Supported values: {sorted(supported_providers)}"
            )

        # (c) Validate LLM_MODEL is non-empty
        if not self.LLM_MODEL or not self.LLM_MODEL.strip():
            raise StartupConfigError(
                "LLM_MODEL must be set to a non-empty value"
            )

        # (b) Validate matching credential is present and non-empty
        credential_map: dict[str, SecretStr | None] = {
            "openai": self.OPENAI_API_KEY,
            "anthropic": self.ANTHROPIC_API_KEY,
            "google": self.GOOGLE_API_KEY,
        }
        credential = credential_map[provider]
        if credential is None or not credential.get_secret_value().strip():
            raise StartupConfigError(
                f"Missing or empty API key for LLM_PROVIDER='{provider}'. "
                f"Set the corresponding credential environment variable."
            )

        # (d) PORT range (belt-and-suspenders; Field(ge=1, le=65535) handles this too)
        if not (1 <= self.PORT <= 65535):
            raise StartupConfigError(
                f"PORT={self.PORT} is out of valid range (1-65535)"
            )

        # (e) Validate WHATSAPP_GATEWAY_URL is present
        if not self.WHATSAPP_GATEWAY_URL:
            raise StartupConfigError(
                "WHATSAPP_GATEWAY_URL must be set"
            )

        return self
