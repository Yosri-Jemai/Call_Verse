from typing import Literal, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App mode — use seeded demo data by default; personal mode expects an existing database.
    app_mode: Literal["demo", "personal"] = "demo"

    # Database — auto-reads DB_HOST, DB_PORT, etc. from .env
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "cashy_banking"  # fallback; overridden by resolved_db_name
    db_name_demo: str = "cashy_banking_demo"
    db_name_personal: str = "cashy_banking"
    db_user: str = "postgres"
    db_password: str = ""
    db_sslmode: str = ""  # "require" for Neon; empty for local

    # Local API — keep loopback binding unless deploying behind authenticated infrastructure.
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # LLM provider — set explicitly or auto-detected from API keys
    llm_provider: Optional[
        Literal["openai", "anthropic", "google", "groq", "huggingface", "free-tier", ""]
    ] = None

    # Per-provider API keys (only one needed)
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""
    groq_api_key: str = ""
    hf_token: str = ""

    # Model configuration
    model_name: str = ""  # Optional override; defaults per provider
    model_max_tokens: int = 512
    model_temperature: float = 0.1

    # Local multilingual embeddings for policy-document retrieval
    embedding_model: str = "intfloat/multilingual-e5-small"
    similarity_threshold: float = Field(default=0.55, ge=0.0, le=1.0)

    # HuggingFace-specific
    hf_inference_provider: str = "together"

    # LangSmith — auto-reads LANGSMITH_* from .env
    langsmith_tracing: str = "true"
    langsmith_api_key: str = ""
    langsmith_project: str = "cashy-financial-advisor"

    # App
    environment: str = "development"
    debug: bool = True

    # Customer scoping: in a real deployment, this would come from authenticated session data.
    current_customer_id: int = 1

    @property
    def resolved_db_name(self) -> str:
        """Return the database name based on app_mode."""
        if self.app_mode == "demo":
            return self.db_name_demo
        return self.db_name_personal

    @property
    def database_url(self) -> str:
        """Build DATABASE_URL from individual DB components."""
        url = (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.resolved_db_name}"
        )
        if self.db_sslmode:
            url += f"?sslmode={self.db_sslmode}"
        return url

    @property
    def database_url_safe(self) -> str:
        """Database URL with password redacted for logging."""
        return self.database_url.replace(f":{self.db_password}@", ":***@")

    @property
    def resolved_provider(self) -> Optional[str]:
        """Return the active LLM provider: explicit setting, auto-detected from keys, or None."""
        if self.llm_provider and self.llm_provider != "":
            return self.llm_provider

        # Auto-detect from populated API keys (priority order)
        if self.openai_api_key and self.openai_api_key != "sk-...":
            return "openai"
        if self.anthropic_api_key and self.anthropic_api_key != "sk-ant-...":
            return "anthropic"
        if self.google_api_key and self.google_api_key != "AI...":
            return "google"
        if self.groq_api_key and self.groq_api_key != "gsk_...":
            return "groq"
        if self.hf_token and self.hf_token != "hf_...":
            # In demo mode, default to free-tier (user can switch to huggingface BYOK)
            if self.app_mode == "demo":
                return "free-tier"
            return "huggingface"

        return None


settings = Settings()
