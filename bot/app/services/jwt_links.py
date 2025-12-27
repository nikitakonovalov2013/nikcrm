from datetime import datetime, timedelta, timezone
from jose import jwt
from shared.config import settings


def create_admin_jwt(tg_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_TTL_MINUTES)
    payload = {"sub": str(tg_id), "role": "admin", "exp": expire}
    return jwt.encode(payload, settings.WEB_JWT_SECRET, algorithm="HS256")


def create_manager_jwt(tg_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_TTL_MINUTES)
    payload = {"sub": str(tg_id), "role": "manager", "exp": expire}
    return jwt.encode(payload, settings.WEB_JWT_SECRET, algorithm="HS256")
