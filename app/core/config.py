"""
DBAutonomy — Configuration

All configuration is read from environment variables.
Pydantic Settings handles validation and type conversion.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -----------------------------------------------------------------------
    # Application
    # -----------------------------------------------------------------------
    APP_NAME: str = "DBAutonomy"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = False
    AI_MODE: str = "real"  # "real" or "mock"
    DEMO_MODE: bool = False

    # -----------------------------------------------------------------------
    # Primary (Production) Database
    # -----------------------------------------------------------------------
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "dbautonomy_primary"
    DB_USER: str = "dbautonomy"
    DB_PASSWORD: str = "dbautonomy"

    @property
    def db_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    # -----------------------------------------------------------------------
    # Shadow Database
    # -----------------------------------------------------------------------
    SHADOW_DB_HOST: str = "localhost"
    SHADOW_DB_PORT: int = 5433
    SHADOW_DB_NAME: str = "dbautonomy_shadow"
    SHADOW_DB_USER: str = "dbautonomy"
    SHADOW_DB_PASSWORD: str = "dbautonomy"

    @property
    def shadow_db_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.SHADOW_DB_USER}:{self.SHADOW_DB_PASSWORD}"
            f"@{self.SHADOW_DB_HOST}:{self.SHADOW_DB_PORT}/{self.SHADOW_DB_NAME}"
        )

    # -----------------------------------------------------------------------
    # Redis
    # -----------------------------------------------------------------------
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None
    REDIS_STREAM_PENDING: str = "jobs:pending"
    REDIS_STREAM_RETRY: str = "jobs:retry"
    REDIS_CONSUMER_GROUP: str = "dbautonomy-workers"
    REDIS_PUBSUB_CHANNEL: str = "dbautonomy:events"
    REDIS_SCHEMA_CACHE_TTL_S: int = 300

    @property
    def redis_url(self) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # -----------------------------------------------------------------------
    # Ollama (Local AI — Qwen2.5-Coder 3B)
    # -----------------------------------------------------------------------
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5-coder:3b"
    OLLAMA_TIMEOUT_S: int = 30
    OLLAMA_MAX_INPUT_CHARS: int = 2048  # FL-001 mitigation

    # -----------------------------------------------------------------------
    # Gemini Flash (Remote AI)
    # -----------------------------------------------------------------------
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.6-flash"
    GEMINI_TIMEOUT_S: int = 60
    GEMINI_MAX_RETRIES: int = 3
    GEMINI_RETRY_BASE_DELAY_S: float = 2.0
    GROQ_API_KEY: str = ""

    # -----------------------------------------------------------------------
    # Agent Behaviour
    # -----------------------------------------------------------------------
    AGENT_MAX_RETRIES: int = 3
    AGENT_JOB_TIMEOUT_S: int = 300
    AGENT_BENCHMARK_ITERATIONS: int = 10
    AGENT_BENCHMARK_WRITE_ITERATIONS: int = 100
    BENCHMARK_WRITE_IMPACT: bool = True

    # -----------------------------------------------------------------------
    # Bandit (LinUCB)
    # -----------------------------------------------------------------------
    BANDIT_ALPHA: float = 1.0
    BANDIT_CONTEXT_DIM: int = 20
    BANDIT_MIN_OBSERVATIONS: int = 3

    # -----------------------------------------------------------------------
    # Safety Gate
    # -----------------------------------------------------------------------
    SAFETY_MIN_REWARD_THRESHOLD: float = 0.05
    SAFETY_MAX_WRITE_REGRESSION: float = 0.10
    SAFETY_ALLOWED_TABLES: str = ""  # comma-separated; empty = all forbidden
    SAFETY_INDEX_TYPE_WHITELIST: str = "btree,hash,gin,gist,brin"
    SAFETY_MAX_BANDIT_UNCERTAINTY: float = 2.0
    SAFETY_REQUIRE_IF_NOT_EXISTS: bool = True

    @property
    def allowed_tables(self) -> list[str]:
        configured = [t.strip() for t in self.SAFETY_ALLOWED_TABLES.split(",") if t.strip()]
        if self.DEMO_MODE:
            demo_allowed = {"orders", "products", "events"}
            if not configured:
                return []
            return [t for t in configured if t in demo_allowed]
        return configured

    @property
    def allowed_index_types(self) -> list[str]:
        return [t.strip() for t in self.SAFETY_INDEX_TYPE_WHITELIST.split(",") if t.strip()]

    # -----------------------------------------------------------------------
    # Log Monitor
    # -----------------------------------------------------------------------
    LOG_MONITOR_SLOW_THRESHOLD_MS: float = 500.0
    LOG_MONITOR_POLL_INTERVAL_S: float = 10.0
    LOG_MONITOR_SOURCE: str = "pg_stat_statements"  # or "log_file"
    LOG_MONITOR_LOG_FILE_PATH: str = "/var/log/postgresql/postgresql.log"

    # -----------------------------------------------------------------------
    # Reward Weights
    # -----------------------------------------------------------------------
    REWARD_WEIGHT_LATENCY: float = 0.80
    REWARD_WEIGHT_WRITE: float = 0.20


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return singleton settings instance."""
    return Settings()
