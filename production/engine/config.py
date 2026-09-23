import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADPE_", env_file=".env", extra="ignore", hide_input_in_errors=True)
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = Field("sqlite:///./data/engine.db", repr=False)
    database_url_file: Path | None = None
    db_connect_timeout_seconds: int = Field(3, ge=1, le=30)
    db_statement_timeout_ms: int = Field(5000, ge=100, le=60000)
    db_pool_timeout_seconds: int = Field(3, ge=1, le=30)
    request_body_timeout_seconds: int = Field(15, ge=1, le=60)
    upload_timeout_seconds: int = Field(180, ge=10, le=600)
    worker_identity_file: Path = Path(tempfile.gettempdir()) / "adpe-worker-id.json"
    data_dir: Path = Path("data")
    public_origin: str = "http://localhost:8000"
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    secure_cookies: bool = False
    trust_proxy_headers: bool = False
    trusted_proxy_ips: str = "127.0.0.1"
    session_hours: int = Field(8, ge=1, le=72)
    max_upload_bytes: int = Field(64 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    max_user_storage_bytes: int = Field(1024 * 1024 * 1024, ge=1024)
    max_rows: int = Field(2_000_000, ge=1, le=10_000_000)
    max_columns: int = Field(128, ge=1, le=512)
    max_field_chars: int = Field(16384, ge=32, le=1048576)
    sample_rows: int = Field(5000, ge=20, le=20000)
    max_pending_jobs: int = Field(10, ge=1, le=100)
    job_timeout_seconds: int = Field(300, ge=10, le=3600)
    worker_memory_mb: int = Field(1024, ge=256, le=16384)
    lease_seconds: int = Field(30, ge=10, le=120)
    max_attempts: int = Field(3, ge=1, le=5)
    ollama_enabled: bool = False
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"
    ollama_timeout_seconds: int = Field(30, ge=1, le=120)
    watcher_user: str = ""
    watch_stable_seconds: int = Field(10, ge=2, le=300)

    @model_validator(mode="after")
    def validate_production(self):
        if self.database_url_file:
            self.database_url = self.database_url_file.read_text(encoding="utf-8").strip()
        origin = urlparse(self.public_origin)
        if (origin.scheme not in {"http", "https"} or not origin.hostname or origin.path not in {"", "/"}
                or origin.username or origin.password or origin.query or origin.fragment):
            raise ValueError("public_origin must be a single http(s) origin without a path")
        if urlparse(self.ollama_url).scheme not in {"http", "https"}:
            raise ValueError("ollama_url must use http(s)")
        if self.environment == "production":
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("production requires PostgreSQL via psycopg")
            if not self.secure_cookies or origin.scheme != "https":
                raise ValueError("production requires HTTPS and secure cookies")
            if any("*" in host for host in self.allowed_hosts) or origin.hostname not in self.allowed_hosts:
                raise ValueError("production requires explicit allowed_hosts")
            if self.trust_proxy_headers and (not self.trusted_proxy_ips.strip() or "*" in self.trusted_proxy_ips):
                raise ValueError("production requires explicit trusted proxy addresses")
        return self

    def prepare_dirs(self):
        for name in ("uploads", "incoming", "processed", "rejected", "work"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True, mode=0o700)


@lru_cache
def settings() -> Settings:
    return Settings()
