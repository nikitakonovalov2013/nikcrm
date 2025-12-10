from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import List


class Settings(BaseSettings):
    BOT_TOKEN: str = ""
    ADMIN_IDS: List[int] = Field(default_factory=list)

    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "app"
    POSTGRES_USER: str = "app"
    POSTGRES_PASSWORD: str = "app"

    DATABASE_URL: str = "postgresql+asyncpg://app:app@db:5432/app"

    WEB_BASE_URL: str = "http://localhost:8000"
    WEB_JWT_SECRET: str = "change_me"
    JWT_TTL_MINUTES: int = 10

    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, list):
            return [int(x) for x in v]
        if isinstance(v, str):
            parts = [p.strip() for p in v.split(",") if p.strip()]
            return [int(p) for p in parts]
        if v is None:
            return []
        return v


settings = Settings()
