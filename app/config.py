"""Application settings, loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # GitHub App
    github_app_id: str = ""
    github_webhook_secret: str = ""
    github_private_key: str = ""
    github_private_key_path: str = ""

    # LLM — provider-agnostic via LangChain. Swap models with env vars, no code change.
    #   groq/llama-3.3-70b-versatile   anthropic/claude-sonnet-5   openai/gpt-4o
    llm_provider: str = "groq"
    llm_model: str = "llama-3.3-70b-versatile"

    # API keys — only the one for your chosen provider is needed.
    groq_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Infra
    redis_url: str = "redis://localhost:6379"

    # Billing
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # Behaviour caps
    max_files: int = 40
    max_diff_bytes: int = 400_000

    @model_validator(mode="after")
    def _load_private_key_from_path(self) -> "Settings":
        # Allow supplying the App private key as a file path instead of inline.
        if not self.github_private_key and self.github_private_key_path:
            path = Path(self.github_private_key_path).expanduser()
            if path.is_file():
                self.github_private_key = path.read_text()
        # Normalise escaped newlines if the key was pasted as a single line.
        if "\\n" in self.github_private_key:
            self.github_private_key = self.github_private_key.replace("\\n", "\n")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
