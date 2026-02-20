from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    llm_model: str = "claude-sonnet-4-6"
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    hotel_provider: str = "amadeus"  # "amadeus" or "expedia"
    amadeus_client_id: str = ""
    amadeus_client_secret: str = ""
    expedia_eps_client_id: str = ""
    expedia_eps_client_secret: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
