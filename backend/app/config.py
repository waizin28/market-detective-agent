"""Application settings, loaded from environment variables / backend/.env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Both locations are read; backend/.env wins over the project-root .env.
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        # A blank line like "OPENAI_API_KEY=" doesn't wipe out a value set elsewhere.
        env_ignore_empty=True,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        # .env files take precedence over shell-exported variables, so a stale
        # key exported in ~/.zshrc can't silently override the project's key.
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    # --- LLM -------------------------------------------------------------
    openai_api_key: str | None = None
    # Any OpenAI chat model id; check https://developers.openai.com/docs/models
    llm_model: str = "gpt-6.1-sol"
    # Reasoning effort per role: reasoning-heavy roles get more, extraction roles less.
    # Set to "" to omit the parameter for models that don't support it.
    effort_analyst: str = "high"
    effort_critic: str = "high"
    effort_researcher: str = "medium"
    effort_coordinator: str = "medium"

    # --- Data providers ----------------------------------------------------
    alpha_vantage_api_key: str | None = None
    newsapi_key: str | None = None
    fred_api_key: str | None = None
    # SEC requires a descriptive User-Agent with contact info for EDGAR access.
    sec_user_agent: str = "MarketDetective/0.1 (research prototype; set SEC_USER_AGENT)"
    # "auto" = Alpha Vantage when a key is present, falling back to yfinance.
    market_data_provider: str = "auto"

    # --- Investigation limits (from the Week 4-6 design) ----------------------
    anomaly_lookback_days: int = 60
    anomaly_z_threshold: float = 2.0
    anomaly_scan_days: int = 20
    beam_width: int = 2
    max_rounds: int = 3
    max_evidence_checks: int = 10
    max_initial_hypotheses: int = 4
    max_refinements_per_branch: int = 2
    prune_below_score: int = 2
    leading_min_score: int = 3
    researcher_max_tool_calls: int = 4
    max_api_calls_per_case: int = 120
    case_timeout_seconds: int = 900

    # --- Retrieval ----------------------------------------------------------
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chunk_tokens: int = 400
    chunk_overlap_tokens: int = 50
    retrieval_top_k: int = 5
    # Wide timeline: the lead-up story is searched week by week over this window.
    news_lookback_days: int = 30
    filings_lookback_days: int = 30

    # --- Storage / server ---------------------------------------------------
    data_dir: Path = BACKEND_DIR / "data"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def checkpoint_db(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"

    @property
    def app_db(self) -> Path:
        return self.data_dir / "app.sqlite"

    @property
    def http_cache_db(self) -> Path:
        return self.data_dir / "http_cache.sqlite"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
