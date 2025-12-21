from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import List, Any, Iterable
import json


class Settings(BaseSettings):
    BOT_TOKEN: str = ""
    # Telegram admin IDs stored as Python ints (can hold int64). Alias allows env var ADMIN_IDS.
    admin_ids: List[int] = Field(default_factory=list, alias="ADMIN_IDS")

    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "app"
    POSTGRES_USER: str = "app"
    POSTGRES_PASSWORD: str = "app"

    DATABASE_URL: str = "postgresql+asyncpg://app:app@db:5432/app"

    WEB_BASE_URL: str = "http://localhost:8000/crm"
    # Public admin panel base URL (with /crm prefix), e.g. https://domain/crm
    admin_panel_url: str = Field(default="http://localhost:8000/crm", alias="WEB_BASE_URL")
    WEB_JWT_SECRET: str = "change_me"
    JWT_TTL_MINUTES: int = 10

    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v: Any) -> List[int]:
        # None -> []
        if v is None:
            return []
        # Already a list/iterable -> coerce elements to int
        if isinstance(v, (list, tuple, set)):
            return [int(x) for x in v]
        # Single int -> [int]
        if isinstance(v, int):
            return [int(v)]
        # String handling
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            # Try JSON first if looks like JSON array
            if s.startswith("[") and s.endswith("]"):
                try:
                    data = json.loads(s)
                    if isinstance(data, Iterable):
                        return [int(x) for x in data]
                except Exception:
                    pass
            # If contains comma -> CSV
            if "," in s:
                parts = [p.strip() for p in s.split(",") if p.strip()]
                return [int(p) for p in parts]
            # Otherwise single numeric string
            return [int(s)]
        # Fallback: attempt to cast to list[int]
        try:
            return [int(v)]
        except Exception:
            return []


settings = Settings()
