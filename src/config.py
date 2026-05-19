from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: SecretStr
    database_url: str
    owner_telegram_id: int

    postgres_user: str = "mynota"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "mynota"

    log_level: str = "INFO"
    tz: str = "Europe/Moscow"

    # AI provider selection. "anthropic" uses ANTHROPIC_API_KEY + Claude
    # models; "kimi" uses KIMI_API_KEY against an OpenAI-compatible
    # endpoint (Moonshot AI). The latter is useful for cheap/local testing.
    ai_provider: str = "anthropic"

    # Anthropic — required when ai_provider == "anthropic".
    anthropic_api_key: SecretStr = SecretStr("")
    claude_haiku_model: str = "claude-haiku-4-5"
    claude_sonnet_model: str = "claude-sonnet-4-5"

    # Kimi (Moonshot AI) — required when ai_provider == "kimi".
    kimi_api_key: SecretStr = SecretStr("")
    kimi_base_url: str = "https://api.moonshot.ai/v1"
    kimi_model_fast: str = "moonshot-v1-8k"
    kimi_model_smart: str = "kimi-k2-0905-preview"
    kimi_model_vision: str = "moonshot-v1-32k-vision-preview"

    profile_update_hour: int = 3
    daily_digest_hour: int = 9
    reminder_check_interval_minutes: int = 5

    # ---- role → concrete model resolution ---------------------------------

    @property
    def fast_model(self) -> str:
        """Cheap/fast classifier model (Haiku-tier)."""
        if self.ai_provider.lower() == "kimi":
            return self.kimi_model_fast
        return self.claude_haiku_model

    @property
    def smart_model(self) -> str:
        """Generation model used by the responder and profiler (Sonnet-tier)."""
        if self.ai_provider.lower() == "kimi":
            return self.kimi_model_smart
        return self.claude_sonnet_model

    @property
    def vision_model(self) -> str:
        """Vision-capable model used by the OCR helper."""
        if self.ai_provider.lower() == "kimi":
            return self.kimi_model_vision
        return self.claude_sonnet_model


settings = Settings()  # type: ignore[call-arg]
