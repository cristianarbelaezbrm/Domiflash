import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    project_id: str = os.getenv("GCP_PROJECT_ID", "coil-398415")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4.1-mini")
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))

    # Se cargan en startup desde Secret Manager
    telegram_token_env: str = "TELEGRAM_BOT_TOKEN"
    webhook_url_env: str = "TELEGRAM_WEBHOOK_URL"
    openai_key_env: str = "OPENAI_API_KEY"

settings = Settings()
