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
    anthropic_api_key: SecretStr
    database_url: str
    owner_telegram_id: int

    postgres_user: str = "mynota"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "mynota"

    log_level: str = "INFO"
    tz: str = "Europe/Moscow"

    claude_haiku_model: str = "claude-haiku-4-5"
    claude_sonnet_model: str = "claude-sonnet-4-5"
    profile_update_hour: int = 3


settings = Settings()  # type: ignore[call-arg]
