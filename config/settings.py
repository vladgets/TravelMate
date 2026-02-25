from pydantic_settings import BaseSettings, SettingsConfigDict

# Supported models (display label → LiteLLM model ID)
SUPPORTED_MODELS: dict[str, str] = {
    "Claude Sonnet 4.6 — best quality": "claude-sonnet-4-6",
    "Claude Haiku 4.5 — faster / cheaper": "claude-haiku-4-5-20251001",
    "GPT-4.1 — OpenAI": "gpt-4.1",
    "GPT-4.1 mini — OpenAI faster / cheaper": "gpt-4.1-mini",
}


class Settings(BaseSettings):
    llm_model: str = "claude-sonnet-4-6" # "gpt-4.1-mini"       # Reasoning model: tool decisions, parsing, routing
    writer_model: str = "claude-sonnet-4-6"  # Writer model: format_itinerary only
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
