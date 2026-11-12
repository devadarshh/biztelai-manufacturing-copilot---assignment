from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_relative_path(value: str | None, default: str) -> str:
    if not value:
        return str(PROJECT_ROOT / default)
    path = Path(value).expanduser()
    if not path.is_absolute():
        return str((PROJECT_ROOT / path).resolve())
    return str(path.resolve())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_TIMEOUT_SECONDS: int = 45
    HUGGINGFACE_API_KEY: str | None = None
    HF_EMBEDDING_PROVIDER: Literal["local", "api"] = "local"
    HF_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    HF_EMBEDDING_DIMENSION: int = Field(default=384, gt=0)
    SUPABASE_URL: str | None = None
    SUPABASE_SERVICE_ROLE_KEY: str | None = None
    SUPABASE_STORAGE_BUCKET: str = "files"
    SUPABASE_CACHE_PATH: str = "./storage/supabase_cache"
    DOCUMENT_STORAGE_MODE: Literal["auto", "local", "supabase"] = "auto"
    DOCUMENT_UPLOAD_PATH: str = "./storage/uploads"
    DOCUMENT_EXCLUDE_FILES: str = ""
    QDRANT_MODE: Literal["auto", "local", "cloud"] = "auto"
    QDRANT_URL: str | None = None
    QDRANT_API_KEY: str | None = None
    QDRANT_STORAGE_PATH: str = Field(default="./storage/qdrant_db")
    QDRANT_COLLECTION_NAME: str = "manufacturing_documents"
    DOCUMENT_STORAGE_PATH: str = Field(default="./storage/documents")
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 200
    TOP_K: int = 4
    RETRIEVAL_THRESHOLD: float | None = None
    INGESTION_MODE: Literal["auto", "sync", "queue"] = "auto"
    INGESTION_QUEUE_NAME: str = "document-ingestion"
    INGESTION_JOB_TIMEOUT_SECONDS: int = 900
    MAX_UPLOAD_SIZE_MB: int = Field(default=50, gt=0, le=500)
    REDIS_URL: str | None = None
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8001
    CORS_ORIGINS: str = "*"

    @property
    def resolved_qdrant_path(self) -> str:
        return resolve_relative_path(self.QDRANT_STORAGE_PATH, "storage/qdrant_db")

    @property
    def resolved_documents_path(self) -> str:
        return resolve_relative_path(self.DOCUMENT_STORAGE_PATH, "storage/documents")

    @property
    def resolved_supabase_cache_path(self) -> str:
        return resolve_relative_path(self.SUPABASE_CACHE_PATH, "storage/supabase_cache")

    @property
    def resolved_document_upload_path(self) -> str:
        return resolve_relative_path(self.DOCUMENT_UPLOAD_PATH, "storage/uploads")


settings = Settings()
