"""Runtime configuration, read once from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    bank_name: str = os.getenv("BANK_NAME", "Northwind Bank & Trust")
    bank_tagline: str = os.getenv("BANK_TAGLINE", "Private & Commercial Banking")
    assistant_name: str = os.getenv("ASSISTANT_NAME", "Northwind Assistant")

    # PostgreSQL (pgvector image). DATABASE_URL wins over the discrete parts.
    database_url: str = os.getenv("DATABASE_URL") or (
        "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
            user=os.getenv("PGUSER", "northwind"),
            pw=os.getenv("PGPASSWORD", "northwind"),
            host=os.getenv("PGHOST", "localhost"),
            port=os.getenv("PGPORT", "5432"),
            db=os.getenv("PGDATABASE", "northwind"),
        )
    )
    db_pool_min: int = int(os.getenv("DB_POOL_MIN", "1"))
    db_pool_max: int = int(os.getenv("DB_POOL_MAX", "6"))

    # Vector search
    embed_provider: str = os.getenv("EMBED_PROVIDER", "hashing")  # hashing | ollama
    embed_dim: int = int(os.getenv("EMBED_DIM", "384"))
    retrieval_k: int = int(os.getenv("RETRIEVAL_K", "3"))

    # LLM (Ollama). When disabled the assistant uses deterministic templates.
    llm_enabled: bool = _bool("LLM_ENABLED", False)
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    ollama_chat_model: str = os.getenv("OLLAMA_CHAT_MODEL", "llama3.2:1b")
    ollama_embed_model: str = os.getenv("OLLAMA_EMBED_MODEL", "all-minilm")
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "90"))
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "320"))

    # Demo behaviour
    default_customer_id: int = int(os.getenv("DEFAULT_CUSTOMER_ID", "1"))
    guardrails_enabled: bool = _bool("GUARDRAILS_ENABLED", False)
    session_cookie: str = os.getenv("SESSION_COOKIE", "nw_session")
    history_limit: int = int(os.getenv("HISTORY_LIMIT", "60"))

    @property
    def embed_provider_resolved(self) -> str:
        if self.embed_provider == "ollama" and not self.llm_enabled:
            return "hashing"
        return self.embed_provider


settings = Settings()
