from shared.config import settings as shared_settings
from pydantic import BaseModel
from typing import List


class BotConfig(BaseModel):
    token: str
    admin_ids: List[int]


def get_config() -> BotConfig:
    return BotConfig(token=shared_settings.BOT_TOKEN, admin_ids=shared_settings.ADMIN_IDS)
