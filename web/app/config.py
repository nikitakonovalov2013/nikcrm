from shared.config import settings as shared_settings
from pydantic import BaseModel


class WebConfig(BaseModel):
    base_url: str
    jwt_secret: str
    jwt_ttl_minutes: int
    bot_token: str


def get_config() -> WebConfig:
    return WebConfig(
        base_url=shared_settings.WEB_BASE_URL,
        jwt_secret=shared_settings.WEB_JWT_SECRET,
        jwt_ttl_minutes=shared_settings.JWT_TTL_MINUTES,
        bot_token=shared_settings.BOT_TOKEN,
    )
