from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: str
    telegram_bot_token: str
    database_url: str

    bot_poll_interval: float = 2.0
    claude_model: str = "claude-sonnet-4-6"
    purchase_history_days: int = 90
    restock_check_hour: int = 9
    instacart_api_key: str = ""


settings = Settings()
