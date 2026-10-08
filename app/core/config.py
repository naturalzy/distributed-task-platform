from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Distributed Task Platform"
    environment: str = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    secret_key: str = Field(min_length=32, repr=False)
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, gt=0)

    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "task_user"
    mysql_password: str = "task_password"
    mysql_database: str = "task_platform"

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_cache_db: int = 0
    redis_broker_db: int = 1
    redis_result_db: int = 2

    task_lease_seconds: int = Field(default=10, gt=0)
    task_heartbeat_seconds: int = Field(default=2, gt=0)
    task_max_attempts: int = Field(default=3, gt=0)
    task_recovery_scan_seconds: int = Field(default=1, gt=0)

    csv_upload_dir: Path = Path("uploads")
    csv_max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    csv_max_rows: int = Field(default=100_000, gt=0)
    csv_max_columns: int = Field(default=200, gt=0)

    @model_validator(mode="after")
    def validate_task_lease(self) -> Settings:
        if self.task_heartbeat_seconds * 2 >= self.task_lease_seconds:
            raise ValueError("TASK_LEASE_SECONDS must be more than twice TASK_HEARTBEAT_SECONDS")
        return self

    @property
    def database_url(self) -> str:
        password = quote_plus(self.mysql_password)
        return (
            f"mysql+asyncmy://{self.mysql_user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            "?charset=utf8mb4"
        )

    @property
    def sync_database_url(self) -> str:
        password = quote_plus(self.mysql_password)
        return (
            f"mysql+pymysql://{self.mysql_user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            "?charset=utf8mb4"
        )

    @property
    def redis_cache_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_cache_db}"

    @property
    def celery_broker_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_broker_db}"

    @property
    def celery_result_backend(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_result_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
