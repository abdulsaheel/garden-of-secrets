"""Application configuration via environment variables."""

import os
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "Garden of Secrets v2"
    app_version: str = "1.0.0"
    secret_key: str = "change-me-in-production-use-openssl-rand-hex-32"
    debug: bool = False
    allowed_hosts: str = "*"

    # Database
    database_url: str = "postgresql+asyncpg://vault:vault@localhost:5432/vault"

    # S3-compatible storage
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "vault"
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = False
    s3_prefix: str = ""  # optional prefix for all keys
    s3_public_base_url: str = ""  # public bucket URL for direct access (e.g., https://bucket.s3.amazonaws.com or https://cdn.example.com)

    # Auth
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24 hours

    # Uploads
    max_file_size_mb: int = 100

    # First user becomes admin
    auto_admin_first_user: bool = True

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "case_sensitive": False}


@lru_cache()
def get_settings() -> Settings:
    return Settings()


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
