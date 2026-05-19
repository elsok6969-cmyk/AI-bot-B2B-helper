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


settings = Settings()  # type: ignore[call-arg]
