from __future__ import annotations

import logging
from urllib.parse import quote

from shared.config import settings
from shared.services.magic_links import create_magic_token

_logger = logging.getLogger(__name__)


def _public_base_url() -> str:
    """Return public BASE URL (scheme+host[:port]) without trailing slash.

    We intentionally do not hardcode host. Source is config/env via shared.config.settings.

    Notes:
    - settings.admin_panel_url defaults to 'http://localhost:8000/crm'
    - For URL building we want BASE without '/crm' suffix.
    """

    raw = str(getattr(settings, "PUBLIC_BASE_URL", "") or "").strip()
    if not raw:
        raw = str(getattr(settings, "APP_URL", "") or "").strip()
    if not raw:
        raw = str(getattr(settings, "BASE_URL", "") or "").strip()
    if not raw:
        # Fallback to existing project setting (usually includes /crm)
        raw = str(getattr(settings, "admin_panel_url", "") or "").strip()

    if not raw:
        return ""

    if raw.endswith("/"):
        raw = raw[:-1]

    # If base already includes '/crm', strip it to get domain base.
    if raw.endswith("/crm"):
        raw = raw[: -len("/crm")]

    return raw


def get_tasks_board_url(*, is_admin: bool, is_manager: bool) -> str:
    """Role-based board URL.

    - admin/manager -> BASE + '/crm/tasks'
    - staff -> BASE + '/crm/tasks/public'

    If BASE is missing, returns relative path to avoid crashing bot.
    """

    path = "/crm/tasks" if (is_admin or is_manager) else "/crm/tasks/public"
    base = _public_base_url()
    if not base:
        _logger.error("PUBLIC base URL is empty; falling back to relative path: %s", path)
        return path
    return base + path


def get_schedule_url(*, is_admin: bool, is_manager: bool) -> str:
    path = "/crm/schedule" if (is_admin or is_manager) else "/crm/schedule/public"
    base = _public_base_url()
    if not base:
        _logger.error("PUBLIC base URL is empty; falling back to relative path: %s", path)
        return path
    return base + path


async def build_schedule_magic_link(
    *,
    session,
    user,
    is_admin: bool,
    is_manager: bool,
    ttl_minutes: int = 15,
) -> str:
    next_path = "/crm/schedule" if (is_admin or is_manager) else "/crm/schedule/public"
    tok = await create_magic_token(session, user_id=int(getattr(user, "id")), ttl_minutes=int(ttl_minutes), scope="schedule")

    base = _public_base_url()
    rel = f"/crm/auth/tg?t={quote(str(tok))}&next={quote(str(next_path), safe='') }&scope=schedule"
    if not base:
        _logger.warning("PUBLIC base URL is empty; returning relative magic-link")
        return rel
    return base + rel


async def build_tasks_board_magic_link(
    *,
    session,
    user,
    is_admin: bool,
    is_manager: bool,
    ttl_minutes: int = 15,
) -> str:
    next_path = "/crm/tasks" if (is_admin or is_manager) else "/crm/tasks/public"
    tok = await create_magic_token(session, user_id=int(getattr(user, "id")), ttl_minutes=int(ttl_minutes), scope="tasks")

    base = _public_base_url()
    rel = f"/crm/auth/tg?t={quote(str(tok))}&next={quote(str(next_path), safe='') }"
    if not base:
        _logger.warning("PUBLIC base URL is empty; returning relative magic-link")
        return rel
    return base + rel


async def build_task_board_magic_link(
    *,
    session,
    user,
    task_id: int,
    is_admin: bool,
    is_manager: bool,
    ttl_minutes: int = 15,
) -> str:
    next_path = ("/crm/tasks" if (is_admin or is_manager) else "/crm/tasks/public") + f"?open={int(task_id)}"
    tok = await create_magic_token(session, user_id=int(getattr(user, "id")), ttl_minutes=int(ttl_minutes), scope="tasks")

    base = _public_base_url()
    rel = f"/crm/auth/tg?t={quote(str(tok))}&next={quote(str(next_path), safe='') }"
    if not base:
        _logger.warning("PUBLIC base URL is empty; returning relative magic-link")
        return rel
    return base + rel


def get_task_board_url(*, task_id: int, is_admin: bool, is_manager: bool) -> str:
    """Board URL with task hint.

    Frontend may ignore the query param; it's safe.
    """

    url = get_tasks_board_url(is_admin=is_admin, is_manager=is_manager)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}open={int(task_id)}"
