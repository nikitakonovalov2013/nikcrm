import builtins

import asyncio

from fastapi import FastAPI, Depends, Request, Response, HTTPException, status, Form, UploadFile, File, Header
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import jwt, JWTError
from datetime import datetime, timezone, timedelta, time
from typing import Optional, List
import json
import logging
import httpx
import re
import calendar

from shared.config import settings
from shared.db import get_async_session
from sqlalchemy.ext.asyncio import AsyncSession
from shared.enums import UserStatus, Schedule, Position, TaskStatus, TaskPriority, TaskEventType, ShiftInstanceStatus, PurchaseStatus
from shared.models import User
from shared.models import MaterialType, Material, MaterialConsumption, MaterialSupply
from shared.models import Task, TaskComment, TaskCommentPhoto, TaskEvent
from shared.models import Purchase, PurchaseEvent
from shared.models import WorkShiftDay
from shared.models import ShiftInstance
from shared.models import ShiftInstanceEvent
from shared.models import ShiftSwapRequest
from shared.models import Broadcast, BroadcastDelivery, BroadcastRating
from sqlalchemy import select, delete
from sqlalchemy import case
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from decimal import Decimal
from shared.services.material_stock import (
    recalculate_material_stock,
    update_stock_on_new_consumption,
    update_stock_on_new_supply,
)

from shared.services.materials_remains import set_material_remains

from shared.db import add_after_commit_callback
from shared.services.stock_events_notify import notify_reports_chat_about_stock_event, StockEventActor

from .services.stocks_dashboard import (
    build_chart_rows,
    build_cast_by_masters,
    build_history_rows,
    build_pie_data,
    build_stock_rows,
    format_dt_ru,
)

from .config import get_config
from .services.messenger import Messenger
from .repository import AdminLogRepo
from shared.enums import AdminActionType

from pathlib import Path
from uuid import uuid4

from .dependencies import (
    require_admin,
    require_admin_or_manager,
    require_authenticated_user,
    require_staff,
    require_user,
    ensure_manager_allowed,
)

from shared.utils import format_number
from shared.utils import MOSCOW_TZ, utc_now, format_moscow
from shared.services.task_notifications import TaskNotificationService
from shared.permissions import role_flags, can_use_tasks_archive
from shared.services.task_permissions import task_permissions, validate_status_transition
from shared.services.task_audit import diff_task_for_audit
from shared.services.task_edit import update_task_with_audit
from shared.services.purchases_domain import purchase_take_in_work, purchase_cancel, purchase_mark_bought
from shared.services.purchases_render import purchases_chat_message_text, purchases_chat_kb_dict, purchase_created_user_message
from shared.services.tasks_flow import add_task_comment as shared_add_task_comment
from shared.services.tasks_flow import return_task_to_rework as shared_return_task_to_rework
from shared.services.tasks_flow import enqueue_task_taken_in_work_notifications, enqueue_task_sent_to_review_notifications
from shared.services.tasks_flow import enqueue_task_status_changed_notifications

from shared.services.shifts_domain import (
    calc_int_hours_from_times,
    emergency_preset_times,
    normalize_shift_times as shared_normalize_shift_times,
)


DEFAULT_SHIFT_START = time(10, 0)
DEFAULT_SHIFT_END = time(18, 0)

MAX_TASK_PHOTO_MB = 20
MAX_TASK_PHOTO_BYTES = MAX_TASK_PHOTO_MB * 1024 * 1024

MAX_PURCHASE_PHOTO_MB = 20
MAX_PURCHASE_PHOTO_BYTES = MAX_PURCHASE_PHOTO_MB * 1024 * 1024

MAX_TG_TEXT = 4096

_TASK_REMIND_LAST_TS: dict[int, float] = {}


def _format_hours_from_times(st: time, et: time) -> str:
    minutes = (et.hour * 60 + et.minute) - (st.hour * 60 + st.minute)
    if minutes <= 0:
        return "—"

    h = minutes / 60.0
    if abs(h - round(h)) < 1e-9:
        return f"{int(round(h))}"
    s = f"{h:.2f}".rstrip("0").rstrip(".")
    return s


def _dt_msk_for_day_time(day, t: time) -> datetime:
    return datetime(
        year=day.year,
        month=day.month,
        day=day.day,
        hour=t.hour,
        minute=t.minute,
        second=0,
        microsecond=0,
        tzinfo=MOSCOW_TZ,
    )


async def _notify_shift_if_due_after_commit(*, user_id: int, day, start_time: time, end_time: time) -> None:
    try:
        now_msk = datetime.now(MOSCOW_TZ)
        if day != now_msk.date():
            return

        start_dt = _dt_msk_for_day_time(day, start_time)
        end_dt = _dt_msk_for_day_time(day, end_time)

        async with get_async_session() as s2:
            u = (
                await s2.execute(select(User).where(User.id == int(user_id)).where(User.is_deleted == False))
            ).scalar_one_or_none()
            if u is None:
                return
            chat_id = int(getattr(u, "tg_id", 0) or 0)
            if not chat_id:
                return

            wsd = (
                await s2.execute(
                    select(WorkShiftDay)
                    .where(WorkShiftDay.user_id == int(user_id))
                    .where(WorkShiftDay.day == day)
                    .where(WorkShiftDay.kind == "work")
                )
            ).scalar_one_or_none()
            if wsd is None:
                return

            messenger = Messenger(settings.BOT_TOKEN)
            iso_day = str(day)
            hrs = _format_hours_from_times(start_time, end_time)

            if now_msk >= end_dt:
                if getattr(wsd, "end_notified_at", None) is not None:
                    return
                text = (
                    f"🏁 <b>Смена по графику закончилась</b>\n\n"
                    f"Конец по графику: <b>{end_time.strftime('%H:%M')}</b>.\n"
                    f"Завершить смену?"
                )
                kb = {
                    "inline_keyboard": [
                        [{"text": "✅ Завершить", "callback_data": f"shift:close_by_day:{iso_day}"}],
                        [{"text": "⏰ Ещё работаю", "callback_data": f"shift:end_snooze:{iso_day}"}],
                        [{"text": "📅 Меню графика", "callback_data": "sched_menu:open"}],
                    ]
                }
                ok = await messenger.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")
                if ok:
                    wsd.end_notified_at = utc_now()
                    wsd.end_snooze_until = None
                    wsd.end_followup_notified_at = None
                    await s2.flush()
                return

            if now_msk >= start_dt:
                if getattr(wsd, "start_notified_at", None) is not None:
                    return
                text = (
                    f"⏰ <b>Начало смены</b>\n\n"
                    f"Сегодня у тебя смена: <b>{start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')}</b> ({hrs} часов).\n"
                    f"Начать смену?"
                )
                kb = {
                    "inline_keyboard": [
                        [{"text": "✅ Начать", "callback_data": f"shift:start:{iso_day}"}],
                        [{"text": "📅 Меню графика", "callback_data": "sched_menu:open"}],
                    ]
                }
                ok = await messenger.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")
                if ok:
                    wsd.start_notified_at = utc_now()
                    await s2.flush()
    except Exception:
        logger.exception("failed to send immediate shift notification")


def _parse_hhmm_time(raw: object, *, field_name: str) -> time:
    if raw is None:
        raise HTTPException(status_code=422, detail=f"Не задано время: {field_name}")
    s = str(raw).strip()
    if not s:
        raise HTTPException(status_code=422, detail=f"Не задано время: {field_name}")
    try:
        return datetime.strptime(s, "%H:%M").time()
    except Exception:
        raise HTTPException(status_code=422, detail=f"Неверный формат времени: {field_name}")


def _time_to_hhmm(t: time | None) -> str | None:
    if t is None:
        return None
    return f"{t.hour:02d}:{t.minute:02d}"


def _normalize_shift_times(*, kind: str, start_time: time | None, end_time: time | None) -> tuple[time | None, time | None]:
    if kind != "work":
        return None, None
    st = start_time or DEFAULT_SHIFT_START
    et = end_time or DEFAULT_SHIFT_END
    try:
        return shared_normalize_shift_times(kind=kind, start_time=st, end_time=et)
    except ValueError as e:
        code = str(e)
        if code == "start_equals_end":
            raise HTTPException(status_code=422, detail="Начало и конец смены не должны совпадать")
        if code == "end_before_start":
            raise HTTPException(status_code=422, detail="Конец смены должен быть позже начала")
        raise


logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates" 
FAVICON_DIR = STATIC_DIR / "favicon" / "icons"

print("STATIC_DIR:", STATIC_DIR)
print("TEMPLATES_DIR:", TEMPLATES_DIR)

app = FastAPI(title="Admin Panel", root_path="/crm")

# Make app aware of reverse proxy (X-Forwarded-Proto/Host) so url_for builds https URLs behind nginx
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Register Jinja helper(s)
from shared.utils import format_date  # noqa: E402
templates.env.globals["format_date"] = format_date


@app.middleware("http")
async def restrict_designer_access_middleware(request: Request, call_next):
    """Hard access restriction for designers.

    Policy:
    - Designers may use only Tasks (board + task APIs) and About page.
    - Everything else must be blocked server-side (even via direct URL/API).
    """

    path = str(getattr(request.url, "path", "") or "")
    # Always allow static/assets and auth endpoints.
    if path.startswith("/crm/static") or path.startswith("/static"):
        return await call_next(request)
    if path.startswith("/auth") or path.startswith("/crm/auth"):
        return await call_next(request)
    if path.startswith("/openapi") or path.startswith("/crm/openapi"):
        return await call_next(request)

    token = request.cookies.get("admin_token")
    if not token:
        return await call_next(request)

    try:
        data = jwt.decode(token, settings.WEB_JWT_SECRET, algorithms=["HS256"])
        sub = int(data.get("sub"))
        role = str(data.get("role") or "")
    except Exception:
        return await call_next(request)

    # Admin/manager override designer restrictions.
    if role in {"admin", "manager"}:
        return await call_next(request)

    try:
        async with get_async_session() as session:
            actor = (
                (await session.execute(select(User).where(User.tg_id == int(sub)).where(User.is_deleted == False)))
            ).scalar_one_or_none()
    except Exception:
        actor = None

    if not actor:
        return await call_next(request)

    if actor.status != UserStatus.APPROVED or actor.position != Position.DESIGNER:
        return await call_next(request)

    # Allow only tasks + about.
    allowed_prefixes = (
        "/tasks/public",
        "/crm/tasks",
        "/tasks/public/archive",
        "/api/public/tasks",
        "/api/tasks",
        "/crm/api/tasks",
        "/tasks/",  # photo proxy: /tasks/{id}/photo
        "/crm/tasks/",  # photo proxy alias
        "/about",
        "/crm/about",
    )
    if any(path.startswith(p) for p in allowed_prefixes):
        return await call_next(request)

    # Block everything else.
    if path.startswith("/api") or path.startswith("/crm/api"):
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    return RedirectResponse(url=request.url_for("tasks_board_public"), status_code=302)


def _favicon_path(filename: str) -> Path:
    name = str(filename).lstrip("/")
    p = (FAVICON_DIR / name).resolve()
    try:
        p.relative_to(FAVICON_DIR.resolve())
    except Exception:
        raise HTTPException(status_code=404)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404)
    return p


@app.get("/favicon.ico")
async def crm_favicon_ico() -> FileResponse:
    return FileResponse(str(_favicon_path("favicon.ico")))


@app.get("/manifest.json")
async def crm_manifest_json() -> FileResponse:
    return FileResponse(str(_favicon_path("manifest.json")), media_type="application/manifest+json")


@app.get("/browserconfig.xml")
async def crm_browserconfig_xml() -> FileResponse:
    return FileResponse(str(_favicon_path("browserconfig.xml")), media_type="application/xml")


@app.get("/favicon-{size}.png")
async def crm_favicon_png(size: str) -> FileResponse:
    filename = f"favicon-{str(size)}.png"
    return FileResponse(str(_favicon_path(filename)))


UPLOADS_DIR = STATIC_DIR / "uploads" / "tasks"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

BROADCAST_UPLOADS_DIR = STATIC_DIR / "uploads" / "broadcasts"
BROADCAST_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

PURCHASE_UPLOADS_DIR = STATIC_DIR / "uploads" / "purchases"
PURCHASE_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def _task_photo_path_from_key(photo_key: str | None) -> str | None:
    if not photo_key:
        return None
    key = str(photo_key).lstrip("/")
    return f"/crm/static/uploads/{key}"


def _task_photo_url_from_key(photo_key: str | None) -> str | None:
    path = _task_photo_path_from_key(photo_key)
    return _to_public_url(path)


def _task_photo_fs_path_from_key(photo_key: str) -> Path:
    key = str(photo_key).lstrip("/")
    return STATIC_DIR / "uploads" / key


async def _save_task_photo(*, photo: UploadFile) -> tuple[str, str]:
    ext = Path(getattr(photo, "filename", "") or "").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        ext = ".jpg"
    name = f"{uuid4().hex}{ext}"
    photo_key = f"tasks/{name}"
    fs_path = _task_photo_fs_path_from_key(photo_key)
    fs_path.parent.mkdir(parents=True, exist_ok=True)

    data = await photo.read(MAX_TASK_PHOTO_BYTES + 1)
    if not data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Пустой файл")
    if len(data) > MAX_TASK_PHOTO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл слишком большой. Максимум: {MAX_TASK_PHOTO_MB} MB.",
        )
    fs_path.write_bytes(data)

    photo_path = _task_photo_path_from_key(photo_key)
    if not photo_path:
        raise HTTPException(status_code=500, detail="Не удалось сформировать путь фото")
    return str(photo_key), str(photo_path)


def _purchase_photo_path_from_key(photo_key: str | None) -> str | None:
    if not photo_key:
        return None
    key = str(photo_key).lstrip("/")
    return f"/crm/static/uploads/{key}"


def _purchase_photo_url_from_key(photo_key: str | None) -> str | None:
    path = _purchase_photo_path_from_key(photo_key)
    return _to_public_url(path)


def _purchase_photo_fs_path_from_key(photo_key: str) -> Path:
    key = str(photo_key).lstrip("/")
    return STATIC_DIR / "uploads" / key


async def _save_purchase_photo(*, photo: UploadFile) -> tuple[str, str]:
    ext = Path(getattr(photo, "filename", "") or "").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        ext = ".jpg"
    name = f"{uuid4().hex}{ext}"
    photo_key = f"purchases/{name}"
    fs_path = _purchase_photo_fs_path_from_key(photo_key)
    fs_path.parent.mkdir(parents=True, exist_ok=True)

    data = await photo.read(MAX_PURCHASE_PHOTO_BYTES + 1)
    if not data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Пустой файл")
    if len(data) > MAX_PURCHASE_PHOTO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл слишком большой. Максимум: {MAX_PURCHASE_PHOTO_MB} MB.",
        )
    fs_path.write_bytes(data)

    photo_path = _purchase_photo_path_from_key(photo_key)
    if not photo_path:
        raise HTTPException(status_code=500, detail="Не удалось сформировать путь фото")
    return str(photo_key), str(photo_path)


def _broadcast_media_fs_path_from_key(media_key: str) -> Path:
    key = str(media_key).lstrip("/")
    return STATIC_DIR / "uploads" / key


def _broadcast_media_path_from_key(media_key: str | None) -> str | None:
    if not media_key:
        return None
    key = str(media_key).lstrip("/")
    return f"/crm/static/uploads/{key}"


async def _save_broadcast_media(*, media: UploadFile) -> tuple[str, str, str]:
    ext = Path(getattr(media, "filename", "") or "").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".mkv", ".webm"}:
        ext = ".bin"
    name = f"{uuid4().hex}{ext}"
    media_key = f"broadcasts/{name}"
    fs_path = _broadcast_media_fs_path_from_key(media_key)
    fs_path.parent.mkdir(parents=True, exist_ok=True)
    data = await media.read(50 * 1024 * 1024 + 1)
    if not data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Пустой файл")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Файл слишком большой")
    fs_path.write_bytes(data)

    media_path = _broadcast_media_path_from_key(media_key)
    if not media_path:
        raise HTTPException(status_code=500, detail="Не удалось сформировать путь файла")
    return str(media_key), str(media_path), str(_to_public_url(media_path) or media_path)


def _public_base_url() -> str:
    raw = str(getattr(settings, "PUBLIC_BASE_URL", "") or "").strip()
    if not raw:
        raw = str(getattr(settings, "APP_URL", "") or "").strip()
    if not raw:
        raw = str(getattr(settings, "BASE_URL", "") or "").strip()
    if not raw:
        raw = str(getattr(settings, "admin_panel_url", "") or "").strip()
    if not raw:
        return ""
    if raw.endswith("/"):
        raw = raw[:-1]
    if raw.endswith("/crm"):
        raw = raw[: -len("/crm")]
    return raw


def _to_public_url(path: str | None) -> str | None:
    if not path:
        return None
    base = _public_base_url()
    if not base:
        return str(path)
    p = str(path)
    if not p.startswith("/"):
        p = "/" + p
    return base + p


async def get_db() -> AsyncSession:
    async with get_async_session() as session:
        yield session


def _broadcast_rating_kb(*, broadcast_id: int) -> dict:
    rows: list[list[dict]] = []
    rows.append([{ "text": "⭐ Оценить новость", "callback_data": f"broadcast_rate:{int(broadcast_id)}" }])
    return {"inline_keyboard": rows}


def _rating_pick_kb(*, broadcast_id: int) -> dict:
    row = []
    for n in range(1, 6):
        row.append({"text": f"⭐{n}", "callback_data": f"broadcast_rate_set:{int(broadcast_id)}:{int(n)}"})
    return {"inline_keyboard": [row]}


def _user_fio(u: User | None) -> str:
    if not u:
        return "—"
    fio = (
        f"{(getattr(u, 'first_name', '') or '').strip()} {(getattr(u, 'last_name', '') or '').strip()}".strip()
    )
    return fio or f"#{int(getattr(u, 'id', 0) or 0)}"


@app.get("/api/broadcast/targets")
@app.get("/crm/api/broadcast/targets")
async def api_broadcast_targets(
    request: Request,
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)

    pos_res = await session.execute(
        select(User.position, func.count(User.id))
        .where(User.is_deleted == False)
        .group_by(User.position)
        .order_by(User.position)
    )
    positions = []
    for pos, cnt in pos_res.all():
        name = pos.value if hasattr(pos, "value") else (str(pos) if pos is not None else "")
        positions.append({"name": str(name), "count": int(cnt)})

    users_res = await session.execute(
        select(User)
        .where(User.is_deleted == False)
        .order_by(User.first_name, User.last_name, User.id)
    )
    users = []
    for u in users_res.scalars().all():
        full_name = (f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip() or str(getattr(u, "username", "") or "") or f"#{int(u.id)}")
        users.append(
            {
                "id": int(u.id),
                "full_name": full_name,
                "position": (u.position.value if hasattr(u.position, "value") else (str(u.position) if u.position is not None else "")),
                "color": str(getattr(u, "color", "") or ""),
                "tg_chat_id": int(getattr(u, "tg_id", 0) or 0) or None,
                "approved": bool(u.status == UserStatus.APPROVED),
            }
        )
    return {"positions": positions, "users": users}


@app.post("/api/broadcasts/upload")
@app.post("/crm/api/broadcasts/upload")
async def api_broadcasts_upload(
    request: Request,
    file: UploadFile = File(...),
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    filename = str(getattr(file, "filename", "") or "")
    ct = str(getattr(file, "content_type", "") or "")
    media_type: str | None = None
    if ct.startswith("image/"):
        media_type = "photo"
    elif ct.startswith("video/"):
        media_type = "video"
    else:
        # Fallback by extension
        ext = Path(filename).suffix.lower()
        if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            media_type = "photo"
        elif ext in {".mp4", ".mov", ".mkv", ".webm"}:
            media_type = "video"
    if media_type not in {"photo", "video"}:
        raise HTTPException(status_code=422, detail="Разрешены только фото или видео")

    media_key, media_path, media_url = await _save_broadcast_media(media=file)
    return {
        "media_type": media_type,
        "media_key": media_key,
        "media_path": media_path,
        "media_url": media_url,
        "filename": filename,
    }


async def _run_broadcast_send(*, broadcast_id: int) -> None:
    # Runs after commit. Uses its own DB session.
    try:
        async with get_async_session() as session:
            b = (
                (await session.execute(select(Broadcast).where(Broadcast.id == int(broadcast_id))))
            ).scalar_one_or_none()
            if b is None:
                return

            # Load deliveries + users
            rows = (
                await session.execute(
                    select(BroadcastDelivery)
                    .where(BroadcastDelivery.broadcast_id == int(broadcast_id))
                    .options(selectinload(BroadcastDelivery.user))
                    .order_by(BroadcastDelivery.id.asc())
                )
            ).scalars().all()

            messenger = Messenger(settings.BOT_TOKEN)
            kb = _broadcast_rating_kb(broadcast_id=int(broadcast_id))

            delivered = int(getattr(b, "delivered_count", 0) or 0)
            failed = int(getattr(b, "failed_count", 0) or 0)
            no_tg = int(getattr(b, "no_tg_count", 0) or 0)

            media_type = str(getattr(b, "media_type", "") or "") or None
            media_key = str(getattr(b, "media_path", "") or "") or None
            # We stored media_path as URL-like (/crm/static/uploads/...), derive key if possible
            if media_key and media_key.startswith("/crm/static/uploads/"):
                media_key = media_key[len("/crm/static/uploads/") :]
            if media_key and media_key.startswith("static/uploads/"):
                media_key = media_key[len("static/uploads/") :]

            media_bytes: bytes | None = None
            media_filename: str | None = None
            if media_type in {"photo", "video"} and media_key:
                try:
                    fs_path = _broadcast_media_fs_path_from_key(str(media_key))
                    if fs_path.exists():
                        media_bytes = fs_path.read_bytes()
                        media_filename = fs_path.name
                except Exception:
                    media_bytes = None
                    media_filename = None

            for d in rows:
                u = getattr(d, "user", None)
                chat_id = int(getattr(u, "tg_id", 0) or 0) if u is not None else 0
                if not chat_id:
                    if str(getattr(d, "delivery_status", "") or "") != "no_tg":
                        d.delivery_status = "no_tg"
                    no_tg += 1
                    b.no_tg_count = int(no_tg)
                    await session.flush()
                    continue

                # default: mark pending before sending
                d.delivery_status = "pending"
                await session.flush()

                try:
                    msg_text = str(getattr(b, "text", "") or "")
                    msg_id: int | None = None
                    if media_type in {"photo", "video"} and media_bytes is not None and media_filename is not None:
                        # Telegram caption is limited; if too long -> send media without caption, then text+kb
                        if len(msg_text) > 1024:
                            if media_type == "photo":
                                ok1, _, err1 = await messenger.send_photo_ex(
                                    chat_id=chat_id,
                                    file_bytes=media_bytes,
                                    filename=media_filename,
                                    caption=None,
                                    reply_markup=None,
                                    parse_mode="HTML",
                                )
                            else:
                                ok1, _, err1 = await messenger.send_video_ex(
                                    chat_id=chat_id,
                                    file_bytes=media_bytes,
                                    filename=media_filename,
                                    caption=None,
                                    reply_markup=None,
                                    parse_mode="HTML",
                                )
                            if not ok1:
                                raise Exception(err1 or "media send failed")

                            ok2, msg_id2, err2 = await messenger.send_message_ex(
                                chat_id=chat_id,
                                text=msg_text,
                                reply_markup=kb,
                                parse_mode="HTML",
                            )
                            if not ok2:
                                raise Exception(err2 or "text send failed")
                            msg_id = msg_id2
                        else:
                            if media_type == "photo":
                                okm, msg_idm, errm = await messenger.send_photo_ex(
                                    chat_id=chat_id,
                                    file_bytes=media_bytes,
                                    filename=media_filename,
                                    caption=msg_text,
                                    reply_markup=kb,
                                    parse_mode="HTML",
                                )
                            else:
                                okm, msg_idm, errm = await messenger.send_video_ex(
                                    chat_id=chat_id,
                                    file_bytes=media_bytes,
                                    filename=media_filename,
                                    caption=msg_text,
                                    reply_markup=kb,
                                    parse_mode="HTML",
                                )
                            if not okm:
                                raise Exception(errm or "media send failed")
                            msg_id = msg_idm
                    else:
                        ok, mid, err = await messenger.send_message_ex(
                            chat_id=chat_id,
                            text=msg_text,
                            reply_markup=kb,
                            parse_mode="HTML",
                        )
                        if not ok:
                            raise Exception(err or "send failed")
                        msg_id = mid

                    d.tg_chat_id = int(chat_id)
                    d.tg_message_id = int(msg_id) if msg_id is not None else None
                    d.delivered_at = utc_now()
                    d.delivery_status = "success"
                    d.error_text = None
                    delivered += 1
                    b.delivered_count = int(delivered)
                except Exception as e:
                    d.tg_chat_id = int(chat_id)
                    d.tg_message_id = None
                    d.delivered_at = None
                    d.delivery_status = "failed"
                    d.error_text = str(e)
                    failed += 1
                    b.failed_count = int(failed)

                await session.flush()

            b.status = "sent"
            b.sent_at = utc_now()
            await session.flush()
    except Exception:
        try:
            async with get_async_session() as session2:
                b2 = (
                    (await session2.execute(select(Broadcast).where(Broadcast.id == int(broadcast_id))))
                ).scalar_one_or_none()
                if b2 is not None:
                    b2.status = "failed"
                    await session2.flush()
        except Exception:
            pass


@app.get("/api/users/positions")
@app.get("/crm/api/users/positions")
async def api_users_positions(
    request: Request,
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(
        select(User.position, func.count(User.id))
        .where(User.is_deleted == False)
        .group_by(User.position)
        .order_by(User.position)
    )
    items = []
    for pos, cnt in res.all():
        label = pos.value if hasattr(pos, "value") else (str(pos) if pos is not None else "")
        items.append({"value": label, "count": int(cnt)})
    return {"items": items}


@app.post("/api/broadcasts/send")
@app.post("/crm/api/broadcasts/send")
async def api_broadcasts_send(
    request: Request,
    text: str = Form(...),
    target_mode: str = Form("all"),
    positions: str | None = Form(None),
    user_ids: str | None = Form(None),
    media_type: str | None = Form(None),
    media_key: str | None = Form(None),
    cta_label: str | None = Form(None),
    cta_url: str | None = Form(None),
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)

    actor = (await session.execute(select(User).where(User.tg_id == int(admin_id)).where(User.is_deleted == False))).scalar_one_or_none()

    msg_text = (text or "").strip()
    if not msg_text:
        raise HTTPException(status_code=422, detail="Пустой текст")
    if len(msg_text) > MAX_TG_TEXT:
        raise HTTPException(status_code=422, detail=f"Слишком длинный текст (>{MAX_TG_TEXT})")

    tm = str(target_mode or "all").strip()
    if tm not in {"all", "approved_only"}:
        raise HTTPException(status_code=422, detail="Некорректный режим")

    _ = (cta_label or "")
    _ = (cta_url or "")

    pos_list: list[str] = []
    if positions:
        pos_list = [p.strip() for p in str(positions).split(",") if p.strip()]

    uids_list: list[int] = []
    if user_ids:
        for part in str(user_ids).split(","):
            s = part.strip()
            if not s:
                continue
            try:
                v = int(s)
            except Exception:
                continue
            if v > 0:
                uids_list.append(int(v))
    uids_list = list(sorted(set(uids_list)))

    # must choose recipients
    if not pos_list and not uids_list:
        raise HTTPException(status_code=422, detail="Выберите получателей")

    mt = (str(media_type or "").strip() or None)
    mk = (str(media_key or "").strip() or None)
    if (mt and not mk) or (mk and not mt):
        raise HTTPException(status_code=422, detail="Некорректные данные медиа")
    if mt is not None and mt not in {"photo", "video"}:
        raise HTTPException(status_code=422, detail="media_type должен быть photo или video")

    # OR logic: users explicitly selected OR users in selected positions
    base_q = select(User).where(User.is_deleted == False)
    if tm == "approved_only":
        base_q = base_q.where(User.status == UserStatus.APPROVED)
    clauses = []
    if pos_list:
        clauses.append(User.position.in_([p for p in pos_list]))
    if uids_list:
        clauses.append(User.id.in_(uids_list))
    if clauses:
        from sqlalchemy import or_ as _or
        base_q = base_q.where(_or(*clauses))
    res_u = await session.execute(base_q.order_by(User.first_name, User.last_name, User.id))
    users = list(res_u.scalars().all())

    b = Broadcast(
        text=msg_text,
        sent_by_user_id=(int(actor.id) if actor is not None else None),
        target_mode=tm,
        filter_positions=pos_list or None,
        filter_user_ids=uids_list or None,
        cta_label=None,
        cta_url=None,
        status="sending",
        total_recipients=0,
        delivered_count=0,
        failed_count=0,
        no_tg_count=0,
        media_type=mt,
        media_path=(_broadcast_media_path_from_key(mk) if mk else None),
        media_url=(_to_public_url(_broadcast_media_path_from_key(mk)) if mk else None),
    )
    session.add(b)
    await session.flush()

    # Create delivery placeholders so UI can show progress while sending.
    seen_user_ids: set[int] = set()
    total = 0
    no_tg = 0
    for u in users:
        uid = int(getattr(u, "id"))
        if uid in seen_user_ids:
            continue
        seen_user_ids.add(uid)
        total += 1
        chat_id = int(getattr(u, "tg_id", 0) or 0)
        if not chat_id:
            no_tg += 1
            session.add(
                BroadcastDelivery(
                    broadcast_id=int(b.id),
                    user_id=int(uid),
                    tg_chat_id=None,
                    tg_message_id=None,
                    delivered_at=None,
                    delivery_status="no_tg",
                    error_text=None,
                )
            )
        else:
            session.add(
                BroadcastDelivery(
                    broadcast_id=int(b.id),
                    user_id=int(uid),
                    tg_chat_id=int(chat_id),
                    tg_message_id=None,
                    delivered_at=None,
                    delivery_status="pending",
                    error_text=None,
                )
            )

    b.total_recipients = int(total)
    b.no_tg_count = int(no_tg)
    await session.flush()

    add_after_commit_callback(session, lambda: asyncio.create_task(_run_broadcast_send(broadcast_id=int(b.id))))

    return {
        "id": int(b.id),
        "status": str(getattr(b, "status", "") or ""),
        "total": int(getattr(b, "total_recipients", 0) or 0),
        "success": int(getattr(b, "delivered_count", 0) or 0),
        "failed": int(getattr(b, "failed_count", 0) or 0),
        "no_tg": int(getattr(b, "no_tg_count", 0) or 0),
    }


@app.get("/api/broadcasts")
@app.get("/crm/api/broadcasts")
async def api_broadcasts_list(
    request: Request,
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)

    res = await session.execute(select(Broadcast).order_by(Broadcast.id.desc()).limit(200))
    items = list(res.scalars().all())
    out = []
    for b in items:
        bid = int(b.id)
        # Prefer persisted counters if present (for sending progress), fallback to aggregation.
        total = getattr(b, "total_recipients", None)
        succ = getattr(b, "delivered_count", None)
        fail = getattr(b, "failed_count", None)
        no_tg = getattr(b, "no_tg_count", None)
        if total is None or succ is None or fail is None or no_tg is None:
            agg = await session.execute(
                select(
                    func.count(BroadcastDelivery.id),
                    func.sum(case((BroadcastDelivery.delivery_status == "success", 1), else_=0)),
                    func.sum(case((BroadcastDelivery.delivery_status == "failed", 1), else_=0)),
                    func.sum(case((BroadcastDelivery.delivery_status == "no_tg", 1), else_=0)),
                ).where(BroadcastDelivery.broadcast_id == bid)
            )
            total, succ, fail, no_tg = agg.first() or (0, 0, 0, 0)

        ragg = await session.execute(
            select(func.count(BroadcastRating.id), func.avg(BroadcastRating.rating)).where(BroadcastRating.broadcast_id == bid)
        )
        ratings_count, ratings_avg = ragg.first() or (0, None)

        fail_reason = None
        if str(getattr(b, "status", "") or "") == "failed":
            fr = await session.execute(
                select(BroadcastDelivery.error_text)
                .where(BroadcastDelivery.broadcast_id == bid)
                .where(BroadcastDelivery.delivery_status == "failed")
                .where(BroadcastDelivery.error_text.is_not(None))
                .limit(1)
            )
            fail_reason = fr.scalar_one_or_none()
        out.append(
            {
                "id": bid,
                "created_at": (b.created_at.isoformat() if b.created_at else None),
                "sent_at": (b.sent_at.isoformat() if b.sent_at else None),
                "target_mode": str(getattr(b, "target_mode", "") or ""),
                "status": str(getattr(b, "status", "") or ""),
                "total": int(total or 0),
                "success": int(succ or 0),
                "failed": int(fail or 0),
                "no_tg": int(no_tg or 0),
                "fail_reason": (str(fail_reason)[:180] if fail_reason else None),
                "ratings_count": int(ratings_count or 0),
                "ratings_avg": (float(ratings_avg) if ratings_avg is not None else None),
            }
        )
    return {"items": out}


@app.get("/api/broadcasts/{broadcast_id}")
@app.get("/crm/api/broadcasts/{broadcast_id}")
async def api_broadcasts_detail(
    broadcast_id: int,
    request: Request,
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)

    b = (await session.execute(select(Broadcast).where(Broadcast.id == int(broadcast_id)))).scalar_one_or_none()
    if not b:
        raise HTTPException(404)

    dels = (
        await session.execute(
            select(BroadcastDelivery)
            .where(BroadcastDelivery.broadcast_id == int(broadcast_id))
            .options(selectinload(BroadcastDelivery.user))
            .order_by(BroadcastDelivery.id.asc())
        )
    ).scalars().all()

    ratings = (
        await session.execute(
            select(BroadcastRating)
            .where(BroadcastRating.broadcast_id == int(broadcast_id))
            .options(selectinload(BroadcastRating.user))
        )
    ).scalars().all()
    r_by_uid = {int(r.user_id): r for r in ratings}

    items = []
    for d in dels:
        u = getattr(d, "user", None)
        uid = int(getattr(d, "user_id", 0) or 0)
        rr = r_by_uid.get(uid)
        items.append(
            {
                "user_id": uid,
                "name": ((str(getattr(u, "first_name", "") or "").strip() + " " + str(getattr(u, "last_name", "") or "").strip()).strip() if u else ""),
                "color": (str(getattr(u, "color", "") or "") if u else ""),
                "position": ((getattr(u, "position", None).value if hasattr(getattr(u, "position", None), "value") else str(getattr(u, "position", "") or "")) if u else ""),
                "delivery_status": str(getattr(d, "delivery_status", "") or ""),
                "delivered_at": (getattr(d, "delivered_at", None).isoformat() if getattr(d, "delivered_at", None) else None),
                "error_text": (str(getattr(d, "error_text", "") or "") or None),
                "rating": (int(getattr(rr, "rating", 0) or 0) if rr else None),
                "rated_at": (getattr(rr, "rated_at", None).isoformat() if rr and getattr(rr, "rated_at", None) else None),
            }
        )

    # sort: not rated first
    items.sort(key=lambda x: (x.get("rating") is not None, x.get("name") or ""))

    ragg = await session.execute(
        select(func.count(BroadcastRating.id), func.avg(BroadcastRating.rating)).where(BroadcastRating.broadcast_id == int(broadcast_id))
    )
    ratings_count, ratings_avg = ragg.first() or (0, None)

    fail_reason = None
    if str(getattr(b, "status", "") or "") == "failed":
        fr = await session.execute(
            select(BroadcastDelivery.error_text)
            .where(BroadcastDelivery.broadcast_id == int(broadcast_id))
            .where(BroadcastDelivery.delivery_status == "failed")
            .where(BroadcastDelivery.error_text.is_not(None))
            .limit(1)
        )
        fail_reason = fr.scalar_one_or_none()

    total = getattr(b, "total_recipients", None)
    succ = getattr(b, "delivered_count", None)
    fail = getattr(b, "failed_count", None)
    no_tg = getattr(b, "no_tg_count", None)
    if total is None or succ is None or fail is None or no_tg is None:
        agg = await session.execute(
            select(
                func.count(BroadcastDelivery.id),
                func.sum(case((BroadcastDelivery.delivery_status == "success", 1), else_=0)),
                func.sum(case((BroadcastDelivery.delivery_status == "failed", 1), else_=0)),
                func.sum(case((BroadcastDelivery.delivery_status == "no_tg", 1), else_=0)),
            ).where(BroadcastDelivery.broadcast_id == int(broadcast_id))
        )
        total, succ, fail, no_tg = agg.first() or (0, 0, 0, 0)

    return {
        "id": int(b.id),
        "text": str(getattr(b, "text", "") or ""),
        "created_at": (b.created_at.isoformat() if b.created_at else None),
        "sent_at": (b.sent_at.isoformat() if b.sent_at else None),
        "target_mode": str(getattr(b, "target_mode", "") or ""),
        "filter_positions": list(getattr(b, "filter_positions", None) or []),
        "filter_user_ids": list(getattr(b, "filter_user_ids", None) or []),
        "status": str(getattr(b, "status", "") or ""),
        "total": int(total or 0),
        "success": int(succ or 0),
        "failed": int(fail or 0),
        "no_tg": int(no_tg or 0),
        "media_type": (str(getattr(b, "media_type", "") or "") or None),
        "media_url": (str(getattr(b, "media_url", "") or "") or None),
        "fail_reason": (str(fail_reason)[:180] if fail_reason else None),
        "ratings_count": int(ratings_count or 0),
        "ratings_avg": (float(ratings_avg) if ratings_avg is not None else None),
        "deliveries": items,
    }


@app.post("/api/broadcasts/{broadcast_id}/rate")
@app.post("/crm/api/broadcasts/{broadcast_id}/rate")
async def api_broadcasts_rate(
    broadcast_id: int,
    rating: int = Form(...),
    tg_id: int | None = Form(None),
    session: AsyncSession = Depends(get_db),
):
    # Called by bot: identify user by tg_id.
    tg = int(tg_id or 0)
    if not tg:
        raise HTTPException(status_code=401, detail="tg_id required")
    if int(rating) < 1 or int(rating) > 5:
        raise HTTPException(status_code=422, detail="rating must be 1..5")

    u = (await session.execute(select(User).where(User.tg_id == int(tg)).where(User.is_deleted == False))).scalar_one_or_none()
    if not u:
        raise HTTPException(404)
    b = (await session.execute(select(Broadcast).where(Broadcast.id == int(broadcast_id)))).scalar_one_or_none()
    if not b:
        raise HTTPException(404)

    r = (
        await session.execute(
            select(BroadcastRating)
            .where(BroadcastRating.broadcast_id == int(broadcast_id))
            .where(BroadcastRating.user_id == int(u.id))
        )
    ).scalar_one_or_none()
    if r is None:
        r = BroadcastRating(broadcast_id=int(broadcast_id), user_id=int(u.id), rating=int(rating), rated_at=utc_now())
        session.add(r)
    else:
        r.rating = int(rating)
        r.rated_at = utc_now()
    await session.flush()
    return {"ok": True}

async def load_user(session: AsyncSession, user_id: int) -> User:
    res = await session.execute(select(User).where(User.id == user_id).where(User.is_deleted == False))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(404)
    return user


async def load_staff_user(session: AsyncSession, staff_tg_id: int) -> User:
    res = await session.execute(select(User).where(User.tg_id == int(staff_tg_id)).where(User.is_deleted == False))
    u = res.scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=403)
    return u


def _ensure_task_visible_to_actor(*, t: Task, actor: User, is_admin: bool, is_manager: bool) -> None:
    if is_admin or is_manager:
        return

    assignees = list(getattr(t, "assignees", None) or [])
    is_assignee = any(int(getattr(u, "id", 0) or 0) == int(getattr(actor, "id", 0) or 0) for u in assignees)
    if is_assignee:
        return

    # Designers must only see tasks where they are explicitly assigned.
    if actor.status == UserStatus.APPROVED and actor.position == Position.DESIGNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    st = t.status.value if hasattr(t.status, "value") else str(t.status)
    if len(assignees) == 0 and st == TaskStatus.NEW.value:
        return

    if len(assignees) == 0 and int(getattr(t, "started_by_user_id", 0) or 0) == int(getattr(actor, "id", 0) or 0):
        return

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def _parse_due_at_msk(value: str | None) -> datetime | None:
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    try:
        # datetime-local: "YYYY-MM-DDTHH:MM" (no tz). Assume Moscow.
        dt_naive = datetime.strptime(v, "%Y-%m-%dT%H:%M")
        dt_msk = dt_naive.replace(tzinfo=MOSCOW_TZ)
        return dt_msk.astimezone(timezone.utc)
    except Exception:
        return None


async def _save_uploads(files: list[UploadFile] | None) -> list[str]:
    if not files:
        return []
    urls: list[str] = []
    for f in files:
        if not f or not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            ext = ".jpg"
        name = f"{uuid4().hex}{ext}"
        path = UPLOADS_DIR / name
        data = await f.read(MAX_TASK_PHOTO_BYTES + 1)
        if not data:
            continue
        if len(data) > MAX_TASK_PHOTO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Файл слишком большой. Максимум: {MAX_TASK_PHOTO_MB} MB.",
            )
        path.write_bytes(data)
        urls.append(f"/crm/static/uploads/tasks/{name}")
    return urls


def _user_short(u: User) -> dict:
    return {
        "id": int(u.id),
        "first_name": u.first_name,
        "last_name": u.last_name,
        "tg_id": int(u.tg_id),
        "color": str(getattr(u, "color", "") or ""),
    }


def _event_view(e: TaskEvent) -> dict:
    actor = getattr(e, "actor_user", None)
    actor_str = "—"
    if actor is not None:
        actor_str = (f"{(actor.first_name or '').strip()} {(actor.last_name or '').strip()}".strip() or f"#{actor.id}")
    typ = e.type.value if hasattr(e.type, "value") else str(e.type)
    payload = dict(getattr(e, "payload", None) or {})

    status_display = {
        TaskStatus.NEW.value: "Новая",
        TaskStatus.IN_PROGRESS.value: "В работе",
        TaskStatus.REVIEW.value: "На проверке",
        TaskStatus.DONE.value: "Выполнено",
        TaskStatus.ARCHIVED.value: "Архив",
    }

    title = {
        TaskEventType.CREATED.value: "Создано",
        TaskEventType.ASSIGNED_ADDED.value: "Назначен исполнитель",
        TaskEventType.ASSIGNED_REMOVED.value: "Снят исполнитель",
        TaskEventType.EDITED.value: "Изменено",
        TaskEventType.STATUS_CHANGED.value: "Смена статуса",
        TaskEventType.COMMENT_ADDED.value: "Добавлен комментарий",
        TaskEventType.ARCHIVED.value: "Архивировано",
        TaskEventType.UNARCHIVED.value: "Разархивировано",
    }.get(typ, typ)

    extra: dict = {}
    if typ == TaskEventType.STATUS_CHANGED.value:
        fr = str(payload.get("from") or "")
        to = str(payload.get("to") or "")
        extra = {
            "from_display": status_display.get(fr, fr),
            "to_display": status_display.get(to, to),
        }
    return {
        "id": int(e.id),
        "type": typ,
        "title": title,
        "created_at_str": format_moscow(getattr(e, "created_at", None), "%d.%m.%Y %H:%M"),
        "actor": {"id": int(getattr(actor, "id", 0) or 0), "name": actor_str},
        "payload": payload,
        **extra,
    }


def _task_card_view(t: Task, *, actor_id: int | None = None) -> dict:
    assignees = list(getattr(t, "assignees", None) or [])
    assignees_str = ", ".join(
        [
            (f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip() or f"#{u.id}")
            for u in assignees
        ]
    )
    assignees_view = [
        {
            "id": int(getattr(u, "id", 0) or 0),
            "first_name": (getattr(u, "first_name", None) or None),
            "last_name": (getattr(u, "last_name", None) or None),
            "color": (getattr(u, "color", None) or None),
        }
        for u in assignees
    ]
    is_assigned_to_me = False
    if actor_id is not None:
        is_assigned_to_me = any(int(u.id) == int(actor_id) for u in assignees)
    due_at_utc = getattr(t, "due_at", None)
    due_at_str = format_moscow(due_at_utc, "%d.%m.%Y %H:%M") if due_at_utc else ""
    due_at_ts: int | None = None
    if due_at_utc is not None:
        try:
            due_at_ts = int(due_at_utc.timestamp())
        except Exception:
            due_at_ts = None
    created_at_utc = getattr(t, "created_at", None)
    created_at_ts: int | None = None
    if created_at_utc is not None:
        try:
            created_at_ts = int(created_at_utc.timestamp())
        except Exception:
            created_at_ts = None

    created_by = getattr(t, "created_by_user", None)
    created_by_str = ""
    created_by_view: dict | None = None
    if created_by is not None:
        created_by_str = (
            f"{(created_by.first_name or '').strip()} {(created_by.last_name or '').strip()}".strip()
            or f"#{created_by.id}"
        )
        created_by_view = {
            "id": int(getattr(created_by, "id", 0) or 0),
            "name": created_by_str,
            "color": (getattr(created_by, "color", None) or None),
        }
    is_overdue = bool(due_at_utc and due_at_utc < utc_now())

    # Attachment indicator (reuse the same effective photo logic as in detail modal)
    photo_url = str(getattr(t, "photo_url", "") or "").strip()
    photo_path = str(getattr(t, "photo_path", "") or "").strip()
    tg_photo_file_id = str(getattr(t, "tg_photo_file_id", "") or getattr(t, "photo_file_id", "") or "").strip()
    proxy_photo_url = f"/crm/tasks/{int(t.id)}/photo" if tg_photo_file_id else ""
    attachment_url = photo_url or photo_path or proxy_photo_url
    has_attachment = bool(attachment_url)

    # Permissions (same logic as detail modal)
    perms_view: dict | None = None
    try:
        if actor_id is not None:
            # derive actor flags from task board globals (passed via closure in tasks_board)
            # NOTE: tasks_board passes actor_id only; permissions on board are computed there.
            pass
    except Exception:
        perms_view = None

    return {
        "id": int(t.id),
        "title": t.title,
        "description": (str(getattr(t, "description", "") or "").strip() or None),
        "priority": t.priority.value if hasattr(t.priority, "value") else str(t.priority),
        "status": t.status.value if hasattr(t.status, "value") else str(t.status),
        "due_at_str": due_at_str,
        "due_at_ts": due_at_ts,
        "created_at_str": format_moscow(created_at_utc, "%d.%m.%Y %H:%M"),
        "created_at_ts": created_at_ts,
        "created_by_str": created_by_str,
        "created_by": created_by_view,
        "assignees": assignees_view,
        "assignees_str": assignees_str,
        "is_assigned_to_me": is_assigned_to_me,
        "is_overdue": is_overdue,
        "has_attachment": has_attachment,
        "attachment_url": attachment_url or None,
        "permissions": perms_view,
    }


def _purchase_event_view(e: PurchaseEvent) -> dict:
    actor = getattr(e, "actor_user", None)
    actor_str = "—"
    if actor is not None:
        actor_str = (f"{(actor.first_name or '').strip()} {(actor.last_name or '').strip()}".strip() or f"#{actor.id}")
    typ = str(getattr(e, "type", "") or "")
    type_ru = {
        "created": "Создано",
        "updated": "Обновлено",
        "edited": "Обновлено",
        "comment": "Комментарий",
        "comment_added": "Комментарий",
        "bought": "Сделано",
        "done": "Сделано",
        "completed": "Сделано",
        "rejected": "Отклонено",
        "declined": "Отклонено",
        "returned": "Возвращено",
        "taken": "Взято в работу",
        "unarchived": "Возвращено из архива",
        "archived": "В архив",
        "photo_added": "Фото добавлено",
        "photo_replaced": "Фото обновлено",
        "photo_removed": "Фото удалено",
    }.get(typ, typ)
    payload = dict(getattr(e, "payload", None) or {})
    return {
        "id": int(e.id),
        "type": typ,
        "type_ru": type_ru,
        "text": getattr(e, "text", None),
        "created_at_str": format_moscow(getattr(e, "created_at", None), "%d.%m.%Y %H:%M"),
        "actor": {"id": int(getattr(actor, "id", 0) or 0), "name": actor_str},
        "payload": payload,
    }


def _purchase_photo_proxy_url(p: Purchase) -> str | None:
    fid = getattr(p, "tg_photo_file_id", None) or getattr(p, "photo_file_id", None) or None
    if not fid:
        return None
    return f"/crm/purchases/{int(getattr(p, 'id', 0) or 0)}/photo"


def _purchase_card_view(p: Purchase, *, actor_id: int | None = None) -> dict:
    created_at_utc = getattr(p, "created_at", None)
    created_at_ts: int | None = None
    if created_at_utc is not None:
        try:
            created_at_ts = int(created_at_utc.timestamp())
        except Exception:
            created_at_ts = None

    creator = getattr(p, "user", None)
    creator_str = _user_fio(creator)
    creator_view: dict | None = None
    if creator is not None:
        creator_view = {
            "id": int(getattr(creator, "id", 0) or 0),
            "name": creator_str,
            "color": (getattr(creator, "color", None) or None),
        }

    taken_by = getattr(p, "taken_by_user", None)
    taken_by_str = ""
    taken_by_view: dict | None = None
    if taken_by is not None:
        taken_by_str = _user_fio(taken_by)
        taken_by_view = {
            "id": int(getattr(taken_by, "id", 0) or 0),
            "name": taken_by_str,
            "color": (getattr(taken_by, "color", None) or None),
        }

    bought_by = getattr(p, "bought_by_user", None)
    bought_by_str = ""
    bought_by_view: dict | None = None
    if bought_by is not None:
        bought_by_str = _user_fio(bought_by)
        bought_by_view = {
            "id": int(getattr(bought_by, "id", 0) or 0),
            "name": bought_by_str,
            "color": (getattr(bought_by, "color", None) or None),
        }

    archived_by = getattr(p, "archived_by_user", None)
    archived_by_str = ""
    archived_by_view: dict | None = None
    if archived_by is not None:
        archived_by_str = _user_fio(archived_by)
        archived_by_view = {
            "id": int(getattr(archived_by, "id", 0) or 0),
            "name": archived_by_str,
            "color": (getattr(archived_by, "color", None) or None),
        }

    is_taken_by_me = False
    if actor_id is not None:
        try:
            is_taken_by_me = int(getattr(p, "taken_by_user_id", 0) or 0) == int(actor_id)
        except Exception:
            is_taken_by_me = False

    st = getattr(p, "status", None)
    st_val = st.value if hasattr(st, "value") else str(st)
    photo_url = getattr(p, "photo_url", None) or _purchase_photo_url_from_key(getattr(p, "photo_key", None)) or _to_public_url(getattr(p, "photo_path", None))
    if not photo_url:
        photo_url = _purchase_photo_proxy_url(p)
    return {
        "id": int(p.id),
        "text": getattr(p, "text", "") or "",
        "description": getattr(p, "description", None),
        "priority": getattr(p, "priority", None),
        "status": st_val,
        "approved_at": format_moscow(getattr(p, "approved_at", None), "%d.%m.%Y %H:%M") if getattr(p, "approved_at", None) else "",
        "archived_at": format_moscow(getattr(p, "archived_at", None), "%d.%m.%Y %H:%M") if getattr(p, "archived_at", None) else "",
        "bought_at": format_moscow(getattr(p, "bought_at", None), "%d.%m.%Y %H:%M") if getattr(p, "bought_at", None) else "",
        "photo_url": photo_url,
        "created_at_str": format_moscow(created_at_utc, "%d.%m.%Y %H:%M"),
        "created_at_ts": created_at_ts,
        "creator_str": creator_str,
        "creator": creator_view,
        "taken_by_str": taken_by_str,
        "taken_by": taken_by_view,
        "bought_by_str": bought_by_str,
        "bought_by": bought_by_view,
        "archived_by_str": archived_by_str,
        "archived_by": archived_by_view,
        "is_taken_by_me": is_taken_by_me,
    }


@app.get("/purchases/{purchase_id}/photo")
async def purchases_photo_proxy(
    purchase_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    p = await _load_purchase_full(session, int(purchase_id))

    existing = getattr(p, "photo_url", None) or _purchase_photo_url_from_key(getattr(p, "photo_key", None)) or _to_public_url(getattr(p, "photo_path", None))
    if existing:
        logger.info("purchases_photo_proxy redirect", extra={"purchase_id": int(purchase_id), "url": str(existing)})
        return RedirectResponse(url=str(existing), status_code=302)

    fid = getattr(p, "tg_photo_file_id", None) or getattr(p, "photo_file_id", None) or None
    if not fid:
        logger.info(
            "purchases_photo_proxy no photo sources",
            extra={
                "purchase_id": int(purchase_id),
                "photo_url": getattr(p, "photo_url", None),
                "photo_path": getattr(p, "photo_path", None),
                "tg_photo_file_id": getattr(p, "tg_photo_file_id", None),
                "photo_file_id": getattr(p, "photo_file_id", None),
            },
        )
        raise HTTPException(status_code=404)

    data, content_type = await _download_tg_file_bytes(file_id=str(fid))
    if not data:
        logger.info(
            "purchases_photo_proxy tg download returned empty",
            extra={"purchase_id": int(purchase_id), "file_id": str(fid)},
        )
        raise HTTPException(status_code=404)

    return Response(content=data, media_type=content_type or "application/octet-stream")


@app.get("/crm/purchases/{purchase_id}/photo")
async def purchases_photo_proxy_crm(
    purchase_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    return await purchases_photo_proxy(purchase_id=purchase_id, request=request, admin_id=admin_id, session=session)


def _purchase_kanban_column(p: Purchase) -> str:
    # Keep PurchaseStatus enum unchanged for bot compatibility.
    # Web kanban must follow bot logic strictly.
    st = getattr(p, "status", None)
    st_val = st.value if hasattr(st, "value") else str(st or "")
    if st_val == PurchaseStatus.IN_PROGRESS.value:
        return "in_progress"
    return "new"


def _purchase_is_archived(p: Purchase) -> bool:
    st = getattr(p, "status", None)
    st_val = st.value if hasattr(st, "value") else str(st or "")
    return st_val in {PurchaseStatus.BOUGHT.value, PurchaseStatus.CANCELED.value}


async def _notify_purchases_chat_new_purchase_after_commit(*, purchase_id: int, user_name: str, created_at_str: str) -> None:
    # Keep signature for backward compatibility, but delegate to the unified notifier.
    # This guarantees that the very first notification (on create) is sent with photo
    # using the same mechanism as status updates.
    await _notify_purchases_chat_status_after_commit(purchase_id=int(purchase_id))


def _purchase_status_ru(st: str) -> str:
    s = str(st or "").strip()
    if s == PurchaseStatus.NEW.value:
        return "Новые"
    if s == PurchaseStatus.IN_PROGRESS.value:
        return "В работе"
    if s == PurchaseStatus.BOUGHT.value:
        return "Куплено"
    if s == PurchaseStatus.CANCELED.value:
        return "Отменено"
    return "—"


def _purchase_priority_human(priority: str | None) -> str:
    p = str(priority or "").strip().lower()
    if p == "urgent":
        return "🔥 Срочно"
    return "Обычный"


def _purchase_status_message_text(p: Purchase, *, status: str) -> str:
    purchase_id = int(getattr(p, "id", 0) or 0)
    text = str(getattr(p, "text", None) or "—")
    pr = _purchase_priority_human(getattr(p, "priority", None))
    status_ru = _purchase_status_ru(str(status))

    created_by = getattr(p, "user", None)
    taken_by = getattr(p, "taken_by_user", None)
    bought_by = getattr(p, "bought_by_user", None)
    archived_by = getattr(p, "archived_by_user", None)

    created_at_str = format_moscow(getattr(p, "created_at", None), "%d.%m.%Y %H:%M") if getattr(p, "created_at", None) else "—"

    lines: list[str] = []
    lines.append(f"🛒 <b>Закупка #{purchase_id}</b>")
    lines.append("")
    lines.append(f"🛒 <b>Что купить:</b> {text}")
    lines.append(f"⚡ <b>Приоритет:</b> {pr}")
    lines.append(f"📌 <b>Статус:</b> {status_ru}")
    lines.append(f"👤 <b>Кто создал:</b> {_user_fio(created_by)}")
    lines.append(f"⏱ <b>Когда:</b> {created_at_str}")

    if taken_by is not None:
        lines.append(f"🛠 <b>Взял в работу:</b> {_user_fio(taken_by)}")
    if bought_by is not None:
        lines.append(f"✅ <b>Купил:</b> {_user_fio(bought_by)}")
    if archived_by is not None and str(status).strip() in {PurchaseStatus.BOUGHT.value, PurchaseStatus.CANCELED.value}:
        lines.append(f"📦 <b>Закрыл:</b> {_user_fio(archived_by)}")

    return "\n".join(lines)


def _purchase_kb_for_status(*, purchase_id: int, status: str) -> dict | None:
    st = str(status or "").strip()
    if st == PurchaseStatus.NEW.value:
        return {
            "inline_keyboard": [
                [
                    {"text": "❌ Отменить", "callback_data": f"purchase:{int(purchase_id)}:cancel"},
                    {"text": "✅ Взять в работу", "callback_data": f"purchase:{int(purchase_id)}:take"},
                ]
            ]
        }
    if st == PurchaseStatus.IN_PROGRESS.value:
        return {"inline_keyboard": [[{"text": "✅ Куплено", "callback_data": f"purchase:{int(purchase_id)}:bought"}]]}
    return None


async def _notify_purchases_chat_status_after_commit(*, purchase_id: int) -> None:
    chat_id = int(getattr(settings, "PURCHASES_CHAT_ID", 0) or 0)
    if chat_id == 0:
        logger.error("[purchases_notify] PURCHASES_CHAT_ID is not configured, skipping", extra={"purchase_id": int(purchase_id)})
        return
    token = str(getattr(settings, "BOT_TOKEN", "") or "").strip()
    if not token:
        logger.error("[purchases_notify] BOT_TOKEN is not configured, skipping", extra={"purchase_id": int(purchase_id), "chat_id": int(chat_id)})
        return
    try:
        from shared.db import get_async_session

        async with get_async_session() as s2:
            p = await _load_purchase_full(s2, int(purchase_id))
            st = p.status.value if hasattr(p.status, "value") else str(p.status)
            text = purchases_chat_message_text(user=getattr(p, "user", None), purchase=p)
            kb = purchases_chat_kb_dict(purchase_id=int(purchase_id), status=getattr(p, "status", PurchaseStatus.NEW))

            tg_file_id = str(getattr(p, "tg_photo_file_id", None) or getattr(p, "photo_file_id", None) or "").strip()
            photo_path = str(getattr(p, "photo_path", None) or "").strip()
            photo_url = str(getattr(p, "photo_url", None) or "").strip()

            owner_tg_id = int(getattr(getattr(p, "user", None), "tg_id", 0) or 0)
            purchase_text_plain = str(getattr(p, "text", "") or "").strip() or "—"

        def _caption_safe(full_html: str, limit: int = 1024) -> tuple[str, str | None]:
            if len(full_html) <= limit:
                return full_html, None
            short = (
                "ℹ️ Текст заявки слишком длинный для подписи к фото. "
                "Полное описание — следующим сообщением."
            )
            return short[:limit], full_html

        logger.info("[purchases_notify] send", extra={"purchase_id": int(purchase_id), "chat_id": int(chat_id), "status": str(st)})
        messenger = Messenger(token)
        caption, extra_text = _caption_safe(str(text))
        ok = False
        mid = None
        err = None
        if tg_file_id:
            ok, mid, err = await messenger.send_photo_by_id_ex(chat_id=int(chat_id), photo=str(tg_file_id), caption=str(caption), reply_markup=kb)
            if ok and extra_text:
                await messenger.send_message_ex(chat_id=int(chat_id), text=str(extra_text))
        elif photo_path:
            # photo_path stored as /crm/static/uploads/...
            try:
                rel = str(photo_path).replace("/crm/static/uploads/", "").lstrip("/")
                fs_path = (STATIC_DIR / "uploads" / rel).resolve()
                file_bytes = fs_path.read_bytes()
                ok, mid, err = await messenger.send_photo_ex(
                    chat_id=int(chat_id),
                    file_bytes=file_bytes,
                    filename=str(fs_path.name),
                    caption=str(caption),
                    reply_markup=kb,
                )
                if ok and extra_text:
                    await messenger.send_message_ex(chat_id=int(chat_id), text=str(extra_text))
            except Exception as e:
                ok, mid, err = await messenger.send_message_ex(chat_id=int(chat_id), text=str(text), reply_markup=kb)
                if ok:
                    logger.warning(
                        "[purchases_notify] failed to send photo from photo_path, fallback to text",
                        extra={"purchase_id": int(purchase_id), "err": str(e)},
                    )
        elif photo_url:
            ok, mid, err = await messenger.send_photo_by_id_ex(chat_id=int(chat_id), photo=str(photo_url), caption=str(caption), reply_markup=kb)
            if ok and extra_text:
                await messenger.send_message_ex(chat_id=int(chat_id), text=str(extra_text))
        else:
            ok, mid, err = await messenger.send_message_ex(chat_id=int(chat_id), text=str(text), reply_markup=kb)
        if not ok:
            logger.warning("[purchases_notify] send_message_ex failed", extra={"purchase_id": int(purchase_id), "chat_id": int(chat_id), "status": str(st), "err": str(err)})
            return

        # Also notify purchase creator (NEW messages only, without photos)
        try:
            if int(owner_tg_id) > 0:
                s = str(st or "").strip()
                body = None
                if s == PurchaseStatus.IN_PROGRESS.value:
                    body = f"☑️ Ваша заявка на закупку № {int(purchase_id)} взята в работу!\n\n{purchase_text_plain}"
                elif s == PurchaseStatus.CANCELED.value:
                    body = f"❌ Ваша заявка на закупку № {int(purchase_id)} отклонена!\n\n{purchase_text_plain}"
                elif s == PurchaseStatus.BOUGHT.value:
                    body = f"✅ Ваша заявка на закупку № {int(purchase_id)} выполнена!\n\n{purchase_text_plain}"

                if body:
                    await messenger.send_message_ex(chat_id=int(owner_tg_id), text=str(body))
        except Exception:
            logger.exception("failed to notify purchase creator", extra={"purchase_id": int(purchase_id), "status": str(st)})
        try:
            async with get_async_session() as s3:
                pp = (await s3.execute(select(Purchase).where(Purchase.id == int(purchase_id)))).scalar_one_or_none()
                if pp is not None:
                    pp.tg_chat_id = int(chat_id)
                    pp.tg_message_id = int(mid or 0) or None
                    await s3.commit()
        except Exception:
            logger.exception("failed to save purchase tg link", extra={"purchase_id": int(purchase_id), "chat_id": int(chat_id), "message_id": int(mid or 0)})
    except Exception:
        logger.exception("failed to notify purchases chat", extra={"purchase_id": int(purchase_id), "chat_id": int(chat_id)})


async def _notify_purchases_chat_event_after_commit(*, purchase_id: int, kind: str, actor_name: str, text: str | None = None) -> None:
    chat_id = int(getattr(settings, "PURCHASES_CHAT_ID", 0) or 0)
    if chat_id == 0:
        logger.warning("PURCHASES_CHAT_ID is not configured, skipping purchases chat notification")
        return

    token = str(getattr(settings, "BOT_TOKEN", "") or "").strip()
    if not token:
        logger.warning("BOT_TOKEN is not configured, skipping purchases chat notification")
        return

    title = {
        "comment": "Комментарий",
        "bought": "Сделано",
        "canceled": "Отменено",
    }.get(str(kind or "").strip(), "Обновлено")

    body = (
        f"🛒 <b>Закупка #{int(purchase_id)}</b>\n"
        f"🧾 <b>Событие:</b> {title}\n"
        f"👤 <b>Кто:</b> {str(actor_name or '—')}"
    )
    if text:
        body += f"\n\n{text}"

    from web.app.services.messenger import Messenger

    messenger = Messenger(token)
    ok, _, err = await messenger.send_message_ex(chat_id=int(chat_id), text=body)
    if not ok:
        logger.warning("purchases chat event send failed", extra={"purchase_id": int(purchase_id), "kind": str(kind), "err": str(err)})


def _task_permissions(*, t: Task, actor: User, is_admin: bool, is_manager: bool) -> dict:
    st = t.status.value if hasattr(t.status, "value") else str(t.status)
    assignees = list(getattr(t, "assignees", None) or [])
    perms = task_permissions(
        status=str(st),
        actor_user_id=int(actor.id),
        created_by_user_id=int(getattr(t, "created_by_user_id", 0) or 0) or None,
        assignee_user_ids=[int(u.id) for u in assignees],
        started_by_user_id=(int(getattr(t, "started_by_user_id")) if getattr(t, "started_by_user_id", None) is not None else None),
        is_admin=bool(is_admin),
        is_manager=bool(is_manager),
    )
    return {
        "take_in_progress": bool(perms.take_in_progress),
        "finish_to_review": bool(perms.finish_to_review),
        "accept_done": bool(perms.accept_done),
        "send_back": bool(perms.send_back),
        "archive": bool(perms.archive),
        "unarchive": bool(perms.unarchive),
        "comment": bool(perms.comment),
    }


async def _load_task_full(session: AsyncSession, task_id: int) -> Task:
    res = await session.execute(
        select(Task)
        .where(Task.id == task_id)
        .options(
            selectinload(Task.assignees),
            selectinload(Task.created_by_user),
            selectinload(Task.comments).selectinload(TaskComment.author_user),
            selectinload(Task.comments).selectinload(TaskComment.photos),
            selectinload(Task.events).selectinload(TaskEvent.actor_user),
        )
    )
    t = res.scalar_one_or_none()
    if not t:
        raise HTTPException(404)
    return t


def _assignees_snapshot(t: Task) -> list[dict]:
    assignees = list(getattr(t, "assignees", None) or [])
    out: list[dict] = []
    for u in assignees:
        fio = f"{(getattr(u, 'first_name', '') or '').strip()} {(getattr(u, 'last_name', '') or '').strip()}".strip()
        out.append({"id": int(getattr(u, "id", 0) or 0), "name": fio or f"#{int(getattr(u, 'id', 0) or 0)}"})
    return out


def _task_snapshot(t: Task) -> dict:
    return {
        "title": str(getattr(t, "title", "") or ""),
        "description": str(getattr(t, "description", "") or ""),
        "priority": (getattr(getattr(t, "priority", None), "value", None) or str(getattr(t, "priority", "") or "")),
        "due_at": getattr(t, "due_at", None),
        "status": (getattr(getattr(t, "status", None), "value", None) or str(getattr(t, "status", "") or "")),
        "assignees": _assignees_snapshot(t),
        "has_photo": bool(getattr(t, "photo_key", None) or getattr(t, "photo_path", None) or getattr(t, "photo_url", None) or getattr(t, "tg_photo_file_id", None) or getattr(t, "photo_file_id", None)),
        "photo_key": getattr(t, "photo_key", None),
    }


def _parse_due_at_iso_or_msk(value: str | None) -> datetime | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        # API contract: naive timestamps are treated as Moscow
        dt = dt.replace(tzinfo=MOSCOW_TZ)
    return dt.astimezone(timezone.utc)


async def _apply_task_patch_with_audit(
    *,
    session: AsyncSession,
    actor: User,
    task_id: int,
    patch: dict,
    photo_action: str | None = None,
) -> bool:
    t = await _load_task_full(session, int(task_id))

    # permissions: only admin/manager can edit task fields
    r = role_flags(tg_id=int(getattr(actor, "tg_id", 0) or 0), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    if not (bool(r.is_admin) or bool(r.is_manager)):
        raise HTTPException(status_code=403, detail="Недостаточно прав для редактирования")

    before = _task_snapshot(t)

    # apply fields
    if "title" in patch and patch.get("title") is not None:
        t.title = str(patch.get("title") or "").strip()

    if "description" in patch:
        desc_raw = patch.get("description")
        desc = str(desc_raw).strip() if desc_raw is not None else ""
        t.description = desc or None

    if "priority" in patch and patch.get("priority") is not None:
        p = str(patch.get("priority") or "").strip()
        t.priority = TaskPriority.URGENT if p == TaskPriority.URGENT.value else TaskPriority.NORMAL

    if "due_at" in patch:
        due_raw = patch.get("due_at")
        t.due_at = _parse_due_at_iso_or_msk(str(due_raw) if due_raw is not None else None)

    # assignees
    if "assignee_ids" in patch and patch.get("assignee_ids") is not None:
        ids = [int(x) for x in (patch.get("assignee_ids") or []) if int(x) > 0]
        users: list[User] = []
        if ids:
            users = list(
                (
                    await session.scalars(
                        select(User)
                        .where(User.id.in_(ids))
                        .where(User.is_deleted == False)
                        .where(User.status == UserStatus.APPROVED)
                    )
                ).all()
            )
            if len(users) != len(set(ids)):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Исполнители не найдены")
        t.assignees = users

    # archived toggle (optional)
    if "archived" in patch and patch.get("archived") is not None:
        want_archived = bool(patch.get("archived"))
        old_status = t.status.value if hasattr(t.status, "value") else str(t.status)
        perms = task_permissions(
            status=str(old_status),
            actor_user_id=int(actor.id),
            created_by_user_id=int(getattr(t, "created_by_user_id", 0) or 0) or None,
            assignee_user_ids=[int(u.id) for u in list(getattr(t, "assignees", None) or [])],
            started_by_user_id=(int(getattr(t, "started_by_user_id")) if getattr(t, "started_by_user_id", None) is not None else None),
            is_admin=bool(r.is_admin),
            is_manager=bool(r.is_manager),
        )
        if want_archived:
            if not perms.archive:
                raise HTTPException(status_code=403, detail="Недостаточно прав для архивирования")
            t.status = TaskStatus.ARCHIVED
            t.archived_at = utc_now()
        else:
            if old_status == TaskStatus.ARCHIVED.value:
                if not perms.unarchive:
                    raise HTTPException(status_code=403, detail="Недостаточно прав для разархивирования")
                t.status = TaskStatus.DONE
                t.archived_at = None

    # status (keep current transition rules)
    if "status" in patch and patch.get("status") is not None:
        new_status = str(patch.get("status") or "").strip()
        old_status = t.status.value if hasattr(t.status, "value") else str(t.status)
        perms = task_permissions(
            status=str(old_status),
            actor_user_id=int(actor.id),
            created_by_user_id=int(getattr(t, "created_by_user_id", 0) or 0) or None,
            assignee_user_ids=[int(u.id) for u in list(getattr(t, "assignees", None) or [])],
            started_by_user_id=(int(getattr(t, "started_by_user_id")) if getattr(t, "started_by_user_id", None) is not None else None),
            is_admin=bool(r.is_admin),
            is_manager=bool(r.is_manager),
        )
        ok, code, msg = validate_status_transition(
            from_status=str(old_status),
            to_status=str(new_status),
            perms=perms,
            comment=None,
        )
        if not ok:
            raise HTTPException(status_code=int(code), detail=str(msg or "Ошибка"))
        if new_status == TaskStatus.IN_PROGRESS.value:
            t.status = TaskStatus.IN_PROGRESS
        elif new_status == TaskStatus.REVIEW.value:
            t.status = TaskStatus.REVIEW
            t.completed_by_user_id = int(actor.id)
            t.completed_at = utc_now()
        elif new_status == TaskStatus.DONE.value:
            t.status = TaskStatus.DONE

    # remove photo (keeps files best-effort)
    if bool(patch.get("remove_photo")):
        try:
            key = str(getattr(t, "photo_key", "") or "").strip()
            if key:
                fs_path = _task_photo_fs_path_from_key(key)
                if fs_path.exists():
                    fs_path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass
        t.photo_key = None
        t.photo_path = None
        t.photo_url = None
        try:
            t.tg_photo_file_id = None
        except Exception:
            pass
        try:
            t.photo_file_id = None
        except Exception:
            pass

    await session.flush()

    after = _task_snapshot(t)
    if photo_action:
        after["photo_action"] = str(photo_action)

    changes, human = diff_task_for_audit(before=before, after=after)
    if not changes:
        return False

    session.add(
        TaskEvent(
            task_id=int(t.id),
            actor_user_id=int(actor.id),
            type=TaskEventType.EDITED,
            payload={
                "changes": [
                    {"type": c.type, "field": c.field, "before": c.before, "after": c.after, "human": c.human}
                    for c in changes
                ],
                "human": list(human),
            },
        )
    )
    await session.flush()
    return True


@app.patch("/api/tasks/{task_id}")
async def tasks_api_patch(
    task_id: int,
    request: Request,
    admin_id: int = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Неверный формат")

    await update_task_with_audit(session=session, actor=actor, task_id=int(task_id), patch=dict(body or {}))
    return await tasks_api_detail(int(task_id), request, admin_id, session)


@app.patch("/crm/api/tasks/{task_id}")
async def tasks_api_patch_crm(
    task_id: int,
    request: Request,
    admin_id: int = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_db),
):
    return await tasks_api_patch(task_id=task_id, request=request, admin_id=admin_id, session=session)


@app.post("/crm/api/tasks")
async def tasks_api_create_crm(
    request: Request,
    title: str = Form(...),
    description: str | None = Form(None),
    priority: str = Form("normal"),
    due_at: str | None = Form(None),
    assignee_ids: list[int] = Form([]),
    photo: UploadFile | None = File(None),
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    return await tasks_api_create(
        request=request,
        title=title,
        description=description,
        priority=priority,
        due_at=due_at,
        assignee_ids=assignee_ids,
        photo=photo,
        admin_id=admin_id,
        session=session,
    )


@app.get("/crm/api/tasks/{task_id}")
async def tasks_api_detail_crm(
    task_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    return await tasks_api_detail(task_id=int(task_id), request=request, admin_id=admin_id, session=session)


@app.post("/crm/api/tasks/{task_id}/status")
async def tasks_api_change_status_crm(
    task_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    return await tasks_api_change_status(task_id=int(task_id), request=request, admin_id=admin_id, session=session)


@app.post("/crm/api/tasks/{task_id}/comments")
async def tasks_api_add_comment_crm(
    task_id: int,
    request: Request,
    text: str | None = Form(None),
    photos: list[UploadFile] | None = File(None),
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    return await tasks_api_add_comment(
        task_id=int(task_id),
        request=request,
        text=text,
        photos=photos,
        admin_id=admin_id,
        session=session,
    )


@app.post("/api/tasks/{task_id}/photo_web")
async def tasks_api_set_photo_web(
    task_id: int,
    request: Request,
    photo: UploadFile = File(...),
    admin_id: int = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    t = await _load_task_full(session, int(task_id))
    had_photo = bool(getattr(t, "photo_key", None) or getattr(t, "photo_path", None) or getattr(t, "photo_url", None))
    photo_key, photo_path = await _save_task_photo(photo=photo)
    t.photo_key = str(photo_key)
    t.photo_path = str(photo_path)
    t.photo_url = _task_photo_url_from_key(t.photo_key)
    await session.flush()

    action = "replaced" if had_photo else "added"
    await update_task_with_audit(session=session, actor=actor, task_id=int(task_id), patch={}, photo_action=action)
    return await tasks_api_detail(int(task_id), request, admin_id, session)


@app.post("/crm/api/tasks/{task_id}/photo_web")
async def tasks_api_set_photo_web_crm(
    task_id: int,
    request: Request,
    photo: UploadFile = File(...),
    admin_id: int = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_db),
):
    return await tasks_api_set_photo_web(task_id=task_id, request=request, photo=photo, admin_id=admin_id, session=session)


@app.delete("/api/tasks/{task_id}/photo")
async def tasks_api_delete_photo(
    task_id: int,
    request: Request,
    admin_id: int = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    await update_task_with_audit(session=session, actor=actor, task_id=int(task_id), patch={"remove_photo": True}, photo_action="removed")
    return await tasks_api_detail(int(task_id), request, admin_id, session)


@app.delete("/crm/api/tasks/{task_id}/photo")
async def tasks_api_delete_photo_crm(
    task_id: int,
    request: Request,
    admin_id: int = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_db),
):
    return await tasks_api_delete_photo(task_id=task_id, request=request, admin_id=admin_id, session=session)


async def _download_tg_file_bytes(*, file_id: str) -> tuple[bytes | None, str | None]:
    token = str(getattr(settings, "BOT_TOKEN", "") or "").strip()
    if not token:
        logger.warning("tg download skipped: BOT_TOKEN is empty")
        return None, None

    fid = str(file_id).strip()
    if not fid:
        return None, None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": fid})
            if r.status_code != 200:
                logger.warning("tg getFile failed", extra={"status_code": int(r.status_code), "file_id": fid})
                return None, None
            payload = dict(r.json() or {})
            result = dict(payload.get("result") or {})
            file_path = str(result.get("file_path") or "").strip()
            if not file_path:
                logger.warning("tg getFile returned empty file_path", extra={"file_id": fid, "payload": payload})
                return None, None
            d = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
            if d.status_code != 200:
                logger.warning(
                    "tg file download failed",
                    extra={"status_code": int(d.status_code), "file_id": fid, "file_path": file_path},
                )
                return None, None
            return bytes(d.content), "image/jpeg"
    except Exception:
        logger.exception("tg download exception", extra={"file_id": str(file_id)})
        return None, None


@app.get("/auth")
async def auth(token: str, request: Request):
    # Validate token, set cookie, redirect to index
    try:
        data = jwt.decode(token, settings.WEB_JWT_SECRET, algorithms=["HS256"])
        if data.get("role") not in {"admin", "manager"}:
            raise HTTPException(status_code=403)
        sub = int(data.get("sub"))
        if data.get("role") == "admin" and sub not in settings.admin_ids:
            raise HTTPException(status_code=403)
    except JWTError:
        raise HTTPException(status_code=401)
    # Redirect to index using url_for to respect root_path (/crm)
    resp = RedirectResponse(url=request.url_for("index"), status_code=302)
    resp.set_cookie("admin_token", token, httponly=True, secure=False, samesite="lax")
    return resp


@app.get("/auth/tg")
async def auth_tg(
    t: str,
    request: Request,
    next: str | None = None,
    scope: str | None = "tasks",
    session: AsyncSession = Depends(get_db),
):
    from shared.services.magic_links import validate_magic_token

    user = await validate_magic_token(session, token=str(t), scope=(str(scope) if scope is not None else None))
    if not user:
        raise HTTPException(status_code=401, detail="Ссылка недействительна или истекла")

    # Determine role for cookie JWT
    if int(getattr(user, "tg_id", 0) or 0) in settings.admin_ids:
        role = "admin"
    elif getattr(user, "status", None) == UserStatus.APPROVED and getattr(user, "position", None) == Position.MANAGER:
        role = "manager"
    else:
        role = "staff"

    # Safe redirect target (relative only)
    target = (next or "").strip() or ("/crm/tasks" if role in {"admin", "manager"} else "/crm/tasks/public")
    if not target.startswith("/") or target.startswith("//"):
        target = "/crm/tasks"
    if not target.startswith("/crm/"):
        target = "/crm/tasks"

    try:
        ttl_minutes = int(getattr(settings, "JWT_TTL_MINUTES", None) or 0)
    except Exception:
        ttl_minutes = 0
    if ttl_minutes <= 0:
        ttl_minutes = 60

    exp = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    payload = {"sub": str(int(getattr(user, "tg_id", 0) or 0)), "role": role, "exp": exp}
    token = jwt.encode(payload, settings.WEB_JWT_SECRET, algorithm="HS256")

    resp = RedirectResponse(url=target, status_code=302)
    resp.set_cookie("admin_token", token, httponly=True, secure=False, samesite="lax")
    return resp


# ========== Work schedule (CRM) ==========


@app.get("/schedule", response_class=HTMLResponse, name="schedule_page")
async def schedule_page(request: Request, admin_id: int = Depends(require_admin_or_manager), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)

    users_json = "[]"
    try:
        res_u = await session.execute(
            select(User)
            .where(User.is_deleted == False)
            .where(User.status == UserStatus.APPROVED)
            .order_by(User.first_name, User.last_name, User.id)
        )
        users = list(res_u.scalars().all())
        users_json = json.dumps(
            [
                {
                    "id": int(getattr(u, "id")),
                    "name": (" ".join([str(getattr(u, "first_name", "") or "").strip(), str(getattr(u, "last_name", "") or "").strip()]).strip())
                    or str(getattr(u, "username", "") or "")
                    or f"User #{int(getattr(u, 'id'))}",
                    "color": str(getattr(u, "color", "") or ""),
                }
                for u in users
            ]
        )
    except Exception:
        pass
    return templates.TemplateResponse(
        "schedule/calendar.html",
        {
            "request": request,
            "base_template": "base.html",
            "is_admin": is_admin,
            "is_manager": is_manager,
            "users_json": users_json,
        },
    )


# ========== Purchases (CRM) ==========


@app.get("/purchases", response_class=HTMLResponse, name="purchases_board")
async def purchases_board(request: Request, admin_id: int = Depends(require_admin_or_manager), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)

    q = (request.query_params.get("q") or "").strip()
    mine = (request.query_params.get("mine") or "").strip() in {"1", "true", "yes", "on"}

    from sqlalchemy import case

    urgent_first = case((Purchase.priority == "urgent", 0), else_=1)
    query = (
        select(Purchase)
        .options(selectinload(Purchase.user), selectinload(Purchase.taken_by_user))
        .order_by(urgent_first.asc(), Purchase.created_at.desc(), Purchase.id.desc())
    )
    if q:
        like = f"%{q}%"
        query = query.where(Purchase.text.ilike(like))
    if mine:
        query = query.where(Purchase.taken_by_user_id == int(actor.id))

    # Show only active statuses (NEW + IN_PROGRESS). Archive is separate page.
    query = query.where(Purchase.status.in_([PurchaseStatus.NEW, PurchaseStatus.IN_PROGRESS]))

    res = await session.execute(query)
    purchases = list(res.scalars().unique().all())

    col_new: list[dict] = []
    col_in_progress: list[dict] = []
    for p in purchases:
        col = _purchase_kanban_column(p)
        view = _purchase_card_view(p, actor_id=int(actor.id))
        if col == "new":
            col_new.append(view)
        else:
            col_in_progress.append(view)

    columns = [
        {"status": "new", "title": "Новые", "items": col_new},
        {"status": "in_progress", "title": "В работе", "items": col_in_progress},
    ]

    return templates.TemplateResponse(
        "purchases/board.html",
        {
            "request": request,
            "board_url": request.url_for("purchases_board"),
            "archive_url": request.url_for("purchases_archive"),
            "columns": columns,
            "q": q,
            "mine": mine,
            "is_admin": is_admin,
            "is_manager": is_manager,
            "base_template": "base.html",
        },
    )


@app.get("/purchases/archive", response_class=HTMLResponse, name="purchases_archive")
async def purchases_archive(request: Request, admin_id: int = Depends(require_admin_or_manager), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)

    q = (request.query_params.get("q") or "").strip()

    from sqlalchemy import case

    urgent_first = case((Purchase.priority == "urgent", 0), else_=1)
    query = (
        select(Purchase)
        .where(Purchase.status.in_([PurchaseStatus.BOUGHT, PurchaseStatus.CANCELED]))
        .options(selectinload(Purchase.user), selectinload(Purchase.taken_by_user))
        .order_by(urgent_first.asc(), Purchase.created_at.desc(), Purchase.id.desc())
    )
    if q:
        like = f"%{q}%"
        query = query.where(Purchase.text.ilike(like))

    res = await session.execute(query)
    purchases = list(res.scalars().unique().all())
    items = [_purchase_card_view(p, actor_id=int(actor.id)) for p in purchases]

    return templates.TemplateResponse(
        "purchases/archive.html",
        {
            "request": request,
            "board_url": request.url_for("purchases_board"),
            "archive_url": request.url_for("purchases_archive"),
            "items": items,
            "q": q,
            "is_admin": is_admin,
            "is_manager": is_manager,
            "base_template": "base.html",
        },
    )


@app.get("/api/purchases")
@app.get("/crm/api/purchases")
async def purchases_api_list(request: Request, admin_id: int = Depends(require_authenticated_user), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    q = (request.query_params.get("q") or "").strip()
    mine = (request.query_params.get("mine") or "").strip() in {"1", "true", "yes", "on"}

    from sqlalchemy import case

    urgent_first = case((Purchase.priority == "urgent", 0), else_=1)
    query = (
        select(Purchase)
        .options(selectinload(Purchase.user), selectinload(Purchase.taken_by_user))
        .order_by(urgent_first.asc(), Purchase.created_at.desc(), Purchase.id.desc())
    )
    if q:
        like = f"%{q}%"
        query = query.where(Purchase.text.ilike(like))
    if mine:
        query = query.where(Purchase.taken_by_user_id == int(actor.id))

    res = await session.execute(query)
    purchases = list(res.scalars().unique().all())
    return {"items": [_purchase_card_view(p, actor_id=int(actor.id)) for p in purchases]}


@app.post("/api/purchases")
@app.post("/crm/api/purchases")
async def purchases_api_create(
    request: Request,
    text: str = Form(...),
    photo: UploadFile | None = File(None),
    description: str | None = Form(None),
    priority: str | None = Form(None),
    status: str | None = Form(None),
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)

    pr_in = (str(priority or "").strip().lower() if priority is not None else "")
    pr_norm = "urgent" if pr_in in {"urgent", "срочно"} else ("normal" if pr_in in {"normal", "обычный"} else "normal")
    p = Purchase(
        user_id=int(actor.id),
        text=str(text or "").strip(),
        description=(str(description).strip() if description is not None and str(description).strip() else None),
        priority=pr_norm,
        status=PurchaseStatus.NEW,
    )

    # Newly created purchases are always NEW
    initial = "new"

    session.add(p)
    await session.flush()

    if photo is not None:
        try:
            filename = str(getattr(photo, "filename", "") or "").strip()
            size = int(getattr(photo, "size", 0) or 0)
        except Exception:
            filename = ""
            size = 0

        # Treat empty file as "no photo" (front can still send empty part)
        if filename and size != 0:
            photo_key, photo_path = await _save_purchase_photo(photo=photo)
            p.photo_key = str(photo_key)
            p.photo_path = str(photo_path)
            p.photo_url = _purchase_photo_url_from_key(p.photo_key)
            await session.flush()

    session.add(
        PurchaseEvent(
            purchase_id=int(p.id),
            actor_user_id=int(actor.id),
            type="created",
            text=None,
            payload={"initial": (initial if initial else None)},
        )
    )
    await session.flush()
    await session.refresh(p)

    add_after_commit_callback(
        session,
        lambda: _notify_purchases_chat_status_after_commit(purchase_id=int(p.id)),
    )

    add_after_commit_callback(
        session,
        lambda: _notify_purchase_creator_created_after_commit(purchase_id=int(p.id)),
    )
    return {"id": int(p.id)}


async def _notify_purchase_creator_created_after_commit(*, purchase_id: int) -> None:
    pid = int(purchase_id)
    if pid <= 0:
        return
    token = str(getattr(settings, "BOT_TOKEN", "") or "").strip()
    if not token:
        logger.error("[purchases_notify] BOT_TOKEN is not configured, skipping creator created notify", extra={"purchase_id": int(pid)})
        return
    try:
        async with get_async_session() as s2:
            p = await _load_purchase_full(s2, int(pid))
            tg_id = int(getattr(getattr(p, "user", None), "tg_id", 0) or 0)
        if tg_id <= 0:
            return
        messenger = Messenger(token)
        await messenger.send_message_ex(chat_id=int(tg_id), text=purchase_created_user_message(purchase_id=int(pid)))
    except Exception:
        logger.exception("failed to notify purchase creator about creation", extra={"purchase_id": int(pid)})


async def _load_purchase_full(session: AsyncSession, purchase_id: int) -> Purchase:
    res = await session.execute(
        select(Purchase)
        .where(Purchase.id == int(purchase_id))
        .options(
            selectinload(Purchase.user),
            selectinload(Purchase.taken_by_user),
            selectinload(Purchase.bought_by_user),
            selectinload(Purchase.archived_by_user),
            selectinload(Purchase.events).selectinload(PurchaseEvent.actor_user),
        )
    )
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404)
    return p


@app.get("/api/purchases/{purchase_id}")
@app.get("/crm/api/purchases/{purchase_id}")
async def purchases_api_detail(
    purchase_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    if not (bool(r.is_admin) or bool(r.is_manager)):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    p = await _load_purchase_full(session, int(purchase_id))

    creator = getattr(p, "user", None)
    creator_str = ""
    creator_view: dict | None = None
    if creator is not None:
        creator_str = (f"{(creator.first_name or '').strip()} {(creator.last_name or '').strip()}".strip() or f"#{creator.id}")
        creator_view = {"id": int(getattr(creator, "id", 0) or 0), "name": creator_str, "color": (getattr(creator, "color", None) or None)}

    taken_by = getattr(p, "taken_by_user", None)
    taken_by_str = ""
    taken_by_view: dict | None = None
    if taken_by is not None:
        taken_by_str = (f"{(taken_by.first_name or '').strip()} {(taken_by.last_name or '').strip()}".strip() or f"#{taken_by.id}")
        taken_by_view = {"id": int(taken_by.id), "name": taken_by_str, "color": (getattr(taken_by, "color", None) or None)}

    events = list(getattr(p, "events", None) or [])
    events_sorted = sorted(events, key=lambda x: x.created_at)

    st = p.status.value if hasattr(p.status, "value") else str(p.status)
    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)

    # Actions depend on status:
    # NEW: cancel, take
    # IN_PROGRESS: bought
    can_take = bool((is_admin or is_manager) and (st == PurchaseStatus.NEW.value))
    can_cancel = bool((is_admin or is_manager) and (st in {PurchaseStatus.NEW.value, PurchaseStatus.IN_PROGRESS.value}))
    can_bought = bool((is_admin or is_manager) and (st == PurchaseStatus.IN_PROGRESS.value))

    photo_url = getattr(p, "photo_url", None) or _purchase_photo_url_from_key(getattr(p, "photo_key", None)) or _to_public_url(getattr(p, "photo_path", None))
    if not photo_url:
        photo_url = _purchase_photo_proxy_url(p)

    return {
        "id": int(p.id),
        "text": getattr(p, "text", "") or "",
        "description": getattr(p, "description", None),
        "priority": getattr(p, "priority", None),
        "status": st,
        "created_at_str": format_moscow(getattr(p, "created_at", None), "%d.%m.%Y %H:%M"),
        "creator": creator_view or {"id": int(getattr(creator, "id", 0) or 0), "name": creator_str, "color": None},
        "taken_by": taken_by_view,
        "photo_url": photo_url,
        "tg_photo_file_id": getattr(p, "tg_photo_file_id", None) or getattr(p, "photo_file_id", None),
        "photo_file_id": getattr(p, "photo_file_id", None),
        "meta": {
            "taken_at": format_moscow(getattr(p, "taken_at", None), "%d.%m.%Y %H:%M") if getattr(p, "taken_at", None) else "",
            "bought_at": format_moscow(getattr(p, "bought_at", None), "%d.%m.%Y %H:%M") if getattr(p, "bought_at", None) else "",
            "approved_at": format_moscow(getattr(p, "approved_at", None), "%d.%m.%Y %H:%M") if getattr(p, "approved_at", None) else "",
            "archived_at": format_moscow(getattr(p, "archived_at", None), "%d.%m.%Y %H:%M") if getattr(p, "archived_at", None) else "",
        },
        "permissions": {
            "take": bool(can_take),
            "cancel": bool(can_cancel),
            "bought": bool(can_bought),
            "comment": bool(st in {PurchaseStatus.NEW.value, PurchaseStatus.IN_PROGRESS.value}),
            "edit": bool(st == PurchaseStatus.NEW.value and (getattr(p, "taken_by_user_id", None) in {None, int(actor.id)})),
            "approve": False,
            "archive": False,
            "unarchive": False,
            "mark_bought": bool(can_bought),
            "return_to_work": False,
            "photo": False,
        },
        "events": [_purchase_event_view(e) for e in events_sorted],
    }


@app.post("/api/purchases/{purchase_id}/take")
@app.post("/crm/api/purchases/{purchase_id}/take")
async def purchases_api_take(
    purchase_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    result = await purchase_take_in_work(session=session, purchase_id=int(purchase_id), actor_user_id=int(actor.id))
    if not bool(getattr(result, "changed", False)):
        return {"ok": True, "id": int(getattr(result, "purchase_id", purchase_id) or purchase_id), "updated": False}

    session.add(
        PurchaseEvent(
            purchase_id=int(purchase_id),
            actor_user_id=int(actor.id),
            type="taken",
            text=None,
            payload=None,
        )
    )
    await session.flush()
    add_after_commit_callback(session, lambda: _notify_purchases_chat_status_after_commit(purchase_id=int(purchase_id)))
    return {"ok": True, "id": int(purchase_id)}


@app.post("/api/purchases/{purchase_id}/cancel")
@app.post("/crm/api/purchases/{purchase_id}/cancel")
@app.post("/api/purchases/{purchase_id}/reject")
@app.post("/crm/api/purchases/{purchase_id}/reject")
async def purchases_api_cancel(
    purchase_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    if not (bool(r.is_admin) or bool(r.is_manager)):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    result = await purchase_cancel(session=session, purchase_id=int(purchase_id), actor_user_id=int(actor.id))
    if not bool(getattr(result, "changed", False)):
        return {"ok": True, "id": int(getattr(result, "purchase_id", purchase_id) or purchase_id), "updated": False}

    session.add(
        PurchaseEvent(
            purchase_id=int(purchase_id),
            actor_user_id=int(actor.id),
            type="canceled",
            text=None,
            payload=None,
        )
    )
    await session.flush()
    add_after_commit_callback(session, lambda: _notify_purchases_chat_status_after_commit(purchase_id=int(purchase_id)))
    return {"ok": True, "id": int(purchase_id)}


@app.post("/api/purchases/{purchase_id}/bought")
@app.post("/crm/api/purchases/{purchase_id}/bought")
@app.post("/api/purchases/{purchase_id}/mark_bought")
@app.post("/crm/api/purchases/{purchase_id}/mark_bought")
async def purchases_api_bought(
    purchase_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    if not (bool(r.is_admin) or bool(r.is_manager)):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    result = await purchase_mark_bought(session=session, purchase_id=int(purchase_id), actor_user_id=int(actor.id))
    if not bool(getattr(result, "changed", False)):
        return {"ok": True, "id": int(getattr(result, "purchase_id", purchase_id) or purchase_id), "updated": False}

    session.add(
        PurchaseEvent(
            purchase_id=int(purchase_id),
            actor_user_id=int(actor.id),
            type="bought",
            text=None,
            payload=None,
        )
    )
    await session.flush()
    add_after_commit_callback(session, lambda: _notify_purchases_chat_status_after_commit(purchase_id=int(purchase_id)))
    return {"ok": True, "id": int(purchase_id)}


@app.post("/api/purchases/{purchase_id}/return_to_work")
@app.post("/crm/api/purchases/{purchase_id}/return_to_work")
async def purchases_api_return_to_work(
    purchase_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    p = await _load_purchase_full(session, int(purchase_id))

    # Deprecated in new purchases workflow (no reopening from archive).
    raise HTTPException(status_code=400, detail="Возврат в работу не поддерживается")


@app.post("/api/purchases/{purchase_id}/approve")
@app.post("/crm/api/purchases/{purchase_id}/approve")
async def purchases_api_approve(
    purchase_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    if not (bool(r.is_admin) or bool(r.is_manager)):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    p = await _load_purchase_full(session, int(purchase_id))
    if getattr(p, "archived_at", None) is not None:
        raise HTTPException(status_code=400, detail="Закупка в архиве")
    if getattr(p, "approved_at", None) is not None:
        raise HTTPException(status_code=409, detail="Уже одобрено")

    p.approved_by_user_id = int(actor.id)
    p.approved_at = utc_now()
    session.add(
        PurchaseEvent(
            purchase_id=int(p.id),
            actor_user_id=int(actor.id),
            type="approved",
            text=None,
            payload=None,
        )
    )
    await session.flush()
    return {"ok": True, "id": int(p.id)}


@app.post("/api/purchases/{purchase_id}/archive")
@app.post("/crm/api/purchases/{purchase_id}/archive")
async def purchases_api_archive(
    purchase_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    if not (bool(r.is_admin) or bool(r.is_manager)):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    p = await _load_purchase_full(session, int(purchase_id))
    if getattr(p, "archived_at", None) is not None:
        raise HTTPException(status_code=409, detail="Уже в архиве")
    p.archived_by_user_id = int(actor.id)
    p.archived_at = utc_now()
    session.add(
        PurchaseEvent(
            purchase_id=int(p.id),
            actor_user_id=int(actor.id),
            type="archived",
            text=None,
            payload=None,
        )
    )
    await session.flush()
    return {"ok": True, "id": int(p.id)}


@app.post("/api/purchases/{purchase_id}/unarchive")
@app.post("/crm/api/purchases/{purchase_id}/unarchive")
async def purchases_api_unarchive(
    purchase_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    if not (bool(r.is_admin) or bool(r.is_manager)):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    p = await _load_purchase_full(session, int(purchase_id))
    if getattr(p, "archived_at", None) is None:
        raise HTTPException(status_code=409, detail="Не в архиве")
    p.archived_by_user_id = None
    p.archived_at = None
    session.add(
        PurchaseEvent(
            purchase_id=int(p.id),
            actor_user_id=int(actor.id),
            type="unarchived",
            text=None,
            payload=None,
        )
    )
    await session.flush()
    return {"ok": True, "id": int(p.id)}


@app.post("/api/purchases/{purchase_id}/photo_web")
@app.post("/crm/api/purchases/{purchase_id}/photo_web")
async def purchases_api_set_photo_web(
    purchase_id: int,
    request: Request,
    photo: UploadFile = File(...),
    admin_id: int = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    if not (bool(r.is_admin) or bool(r.is_manager)):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    p = await _load_purchase_full(session, int(purchase_id))
    had_photo = bool(getattr(p, "photo_key", None) or getattr(p, "photo_path", None) or getattr(p, "photo_url", None))
    photo_key, photo_path = await _save_purchase_photo(photo=photo)
    p.photo_key = str(photo_key)
    p.photo_path = str(photo_path)
    p.photo_url = _purchase_photo_url_from_key(p.photo_key)
    await session.flush()

    session.add(
        PurchaseEvent(
            purchase_id=int(p.id),
            actor_user_id=int(actor.id),
            type="photo_replaced" if had_photo else "photo_added",
            text=None,
            payload={"photo_key": p.photo_key},
        )
    )
    await session.flush()
    return await purchases_api_detail(int(p.id), request, admin_id, session)


@app.delete("/api/purchases/{purchase_id}/photo")
@app.delete("/crm/api/purchases/{purchase_id}/photo")
async def purchases_api_delete_photo(
    purchase_id: int,
    request: Request,
    admin_id: int = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    if not (bool(r.is_admin) or bool(r.is_manager)):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    p = await _load_purchase_full(session, int(purchase_id))
    try:
        key = str(getattr(p, "photo_key", "") or "").strip()
        if key:
            fs_path = _purchase_photo_fs_path_from_key(key)
            if fs_path.exists():
                fs_path.unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        pass

    p.photo_key = None
    p.photo_path = None
    p.photo_url = None
    try:
        p.tg_photo_file_id = None
    except Exception:
        pass
    try:
        p.photo_file_id = None
    except Exception:
        pass

    session.add(
        PurchaseEvent(
            purchase_id=int(p.id),
            actor_user_id=int(actor.id),
            type="photo_removed",
            text=None,
            payload=None,
        )
    )
    await session.flush()
    return await purchases_api_detail(int(p.id), request, admin_id, session)


@app.post("/api/purchases/{purchase_id}/comment")
@app.post("/crm/api/purchases/{purchase_id}/comment")
async def purchases_api_comment(
    purchase_id: int,
    request: Request,
    text: str = Form(...),
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    p = await _load_purchase_full(session, int(purchase_id))

    st = p.status.value if hasattr(p.status, "value") else str(p.status)
    if st not in {PurchaseStatus.NEW.value, PurchaseStatus.IN_PROGRESS.value}:
        raise HTTPException(status_code=400, detail="Нельзя комментировать")

    txt = str(text or "").strip()
    if not txt:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Комментарий пуст")

    session.add(
        PurchaseEvent(
            purchase_id=int(p.id),
            actor_user_id=int(actor.id),
            type="comment",
            text=txt,
            payload=None,
        )
    )
    await session.flush()
    try:
        actor_name = (
            f"{(actor.first_name or '').strip()} {(actor.last_name or '').strip()}".strip() or f"#{int(actor.id)}"
        )
    except Exception:
        actor_name = "—"
    add_after_commit_callback(
        session,
        lambda: _notify_purchases_chat_event_after_commit(
            purchase_id=int(p.id),
            kind="comment",
            actor_name=str(actor_name),
            text=f"💬 <b>Комментарий:</b>\n{txt}",
        ),
    )
    return {"ok": True}


@app.patch("/api/purchases/{purchase_id}")
@app.patch("/crm/api/purchases/{purchase_id}")
async def purchases_api_patch(
    purchase_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    p = await _load_purchase_full(session, int(purchase_id))

    body = await request.json()
    description = body.get("description") if isinstance(body, dict) else None
    priority = body.get("priority") if isinstance(body, dict) else None

    st = p.status.value if hasattr(p.status, "value") else str(p.status)
    if st != PurchaseStatus.NEW.value:
        raise HTTPException(status_code=400, detail="Нельзя редактировать")
    if getattr(p, "taken_by_user_id", None) not in {None, int(actor.id)}:
        raise HTTPException(status_code=403, detail="Нельзя редактировать чужую закупку")

    if description is not None:
        d = str(description).strip()
        p.description = d if d else None
    if priority is not None:
        pr = str(priority).strip()
        p.priority = pr if pr else None

    session.add(
        PurchaseEvent(
            purchase_id=int(p.id),
            actor_user_id=int(actor.id),
            type="edited",
            text=None,
            payload={"description": p.description, "priority": p.priority},
        )
    )
    await session.flush()
    return {"ok": True}


@app.get("/schedule/public", response_class=HTMLResponse, name="schedule_page_public")
async def schedule_page_public(
    request: Request, admin_id: int = Depends(require_authenticated_user), session: AsyncSession = Depends(get_db)
):
    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)

    # For regular users: selector defaults to self (no "All").
    users_json = json.dumps(
        [
            {
                "id": int(getattr(actor, "id")),
                "name": (
                    " ".join(
                        [
                            str(getattr(actor, "first_name", "") or "").strip(),
                            str(getattr(actor, "last_name", "") or "").strip(),
                        ]
                    ).strip()
                    or str(getattr(actor, "username", "") or "")
                    or f"User #{int(getattr(actor, 'id'))}"
                ),
                "color": str(getattr(actor, "color", "") or ""),
            }
        ]
    )
    return templates.TemplateResponse(
        "schedule/calendar.html",
        {
            "request": request,
            "base_template": "base_public.html",
            "is_admin": is_admin,
            "is_manager": is_manager,
            "users_json": users_json,
        },
    )


@app.get("/api/schedule/month")
async def schedule_api_month(
    request: Request,
    year: int,
    month: int,
    user_id: int | None = None,
    all_mode: bool | None = None,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    actor = await load_staff_user(session, admin_id)

    rflags = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin_or_manager = bool(rflags.is_admin or rflags.is_manager)

    target_user_id: int | None = int(actor.id)
    want_all = bool(all_mode)
    if want_all:
        target_user_id = None
    elif user_id is not None:
        req_uid = int(user_id)
        if is_admin_or_manager:
            target_user_id = req_uid
        else:
            if req_uid != int(actor.id):
                raise HTTPException(status_code=403, detail="Недостаточно прав")
            target_user_id = int(actor.id)
    else:
        # Regular users (and admins/managers without explicit filter) default to self.
        target_user_id = int(actor.id)

    y = int(year)
    m = int(month)
    if m < 1 or m > 12:
        raise HTTPException(status_code=422, detail="Неверный месяц")

    _, last_day = calendar.monthrange(y, m)
    start = datetime(y, m, 1, tzinfo=MOSCOW_TZ).date()
    end = datetime(y, m, last_day, tzinfo=MOSCOW_TZ).date()

    # Staff (transparency): who is working each day
    staff_rows = list(
        (
            await session.execute(
                select(WorkShiftDay, User)
                .join(User, User.id == WorkShiftDay.user_id)
                .where(WorkShiftDay.day >= start)
                .where(WorkShiftDay.day <= end)
                .where(WorkShiftDay.kind == "work")
                .where(User.is_deleted == False)
                .where(User.status == UserStatus.APPROVED)
                .order_by(User.first_name, User.last_name, User.id)
            )
        ).all()
    )

    # Shift facts for all staff in this range (to show per-user status in calendar cells)
    staff_user_ids = sorted({int(getattr(u, "id")) for _, u in staff_rows if getattr(u, "id", None) is not None})
    staff_facts: dict[tuple[int, str], ShiftInstance] = {}
    if staff_user_ids:
        fact_rows_all = list(
            (
                await session.scalars(
                    select(ShiftInstance)
                    .where(ShiftInstance.user_id.in_(staff_user_ids))
                    .where(ShiftInstance.day >= start)
                    .where(ShiftInstance.day <= end)
                )
            ).all()
        )
        for fr in fact_rows_all:
            d = getattr(fr, "day", None)
            uid = getattr(fr, "user_id", None)
            if d is None or uid is None:
                continue
            staff_facts[(int(uid), str(d))] = fr
    staff_by_day: dict[str, list[dict]] = {}
    for wsd, u in staff_rows:
        d = getattr(wsd, "day", None)
        if not d:
            continue
        day_key = str(d)
        kind = str(getattr(wsd, "kind", "") or "")
        st0, et0 = _normalize_shift_times(
            kind=kind,
            start_time=getattr(wsd, "start_time", None),
            end_time=getattr(wsd, "end_time", None),
        )
        uid = int(getattr(u, "id"))
        name = (
            " ".join(
                [
                    str(getattr(u, "first_name", "") or "").strip(),
                    str(getattr(u, "last_name", "") or "").strip(),
                ]
            ).strip()
            or str(getattr(u, "username", "") or "")
            or f"User #{int(getattr(u, 'id'))}"
        )
        fact = staff_facts.get((uid, day_key))
        staff_by_day.setdefault(day_key, []).append(
            {
                "user_id": uid,
                "name": name,
                "color": str(getattr(u, "color", "#94a3b8") or "#94a3b8"),
                "kind": kind,
                "hours": getattr(wsd, "hours", None),
                "start_time": _time_to_hhmm(st0),
                "end_time": _time_to_hhmm(et0),
                "is_emergency": bool(getattr(wsd, "is_emergency", False)),
                "shift_status": str(getattr(fact, "status", "") or "") if fact is not None else "",
                "shift_approval_required": bool(getattr(fact, "approval_required", False)) if fact is not None else False,
            }
        )

    out: dict[str, dict] = {}

    if target_user_id is None:
        # "All staff" mode: only staff preview for each day; no per-user plan/fact fields.
        for day_key, day_staff in staff_by_day.items():
            any_work = any(str(x.get("kind") or "") == "work" for x in (day_staff or []))
            any_off = any(str(x.get("kind") or "") == "off" for x in (day_staff or []))
            # Important: do not shadow Python built-ins like `all`/`any` (query params may be named `all`).
            agg_kind = "work" if any_work else (
                "off"
                if (
                    day_staff
                    and (not any_work)
                    and any_off
                    and builtins.all(str(x.get("kind") or "") == "off" for x in (day_staff or []))
                )
                else ""
            )
            work_staff = [x for x in (day_staff or []) if str(x.get("kind") or "") == "work"]
            out[day_key] = {
                "kind": agg_kind,
                "hours": None,
                "start_time": None,
                "end_time": None,
                "is_emergency": any(bool(x.get("is_emergency")) for x in (day_staff or [])) and any_work,
                "shift_status": None,
                "shift_amount": None,
                "shift_approval_required": None,
                "staff_total": len(work_staff),
                "staff_preview": work_staff[:3],
                "all_mode": True,
            }
    else:
        # Plan/fact for selected user (editing)
        fact_rows = list(
            (
                await session.scalars(
                    select(ShiftInstance)
                    .where(ShiftInstance.user_id == int(target_user_id))
                    .where(ShiftInstance.day >= start)
                    .where(ShiftInstance.day <= end)
                )
            ).all()
        )
        fact_by_day: dict[str, ShiftInstance] = {}
        for fr in fact_rows:
            d = getattr(fr, "day", None)
            if d is None:
                continue
            fact_by_day[str(d)] = fr

        plan_rows = list(
            (
                await session.scalars(
                    select(WorkShiftDay)
                    .where(WorkShiftDay.user_id == int(target_user_id))
                    .where(WorkShiftDay.day >= start)
                    .where(WorkShiftDay.day <= end)
                )
            ).all()
        )

        for r in plan_rows:
            d = getattr(r, "day", None)
            if not d:
                continue
            day_key = str(d)
            fact = fact_by_day.get(day_key)
            amount: int | None = None
            status: str | None = None
            approval_required: bool | None = None
            if fact is not None:
                status = str(getattr(fact, "status", None) or "") or None
                approval_required = bool(getattr(fact, "approval_required", False))
                amount = (
                    getattr(fact, "amount_approved", None)
                    if getattr(fact, "amount_approved", None) is not None
                    else (
                        getattr(fact, "amount_submitted", None)
                        if getattr(fact, "amount_submitted", None) is not None
                        else getattr(fact, "amount_default", None)
                    )
                )

            day_staff = staff_by_day.get(day_key, [])
            st, et = _normalize_shift_times(
                kind=str(getattr(r, "kind", "") or ""),
                start_time=getattr(r, "start_time", None),
                end_time=getattr(r, "end_time", None),
            )

            out[day_key] = {
                "kind": str(getattr(r, "kind", "") or ""),
                "hours": getattr(r, "hours", None),
                "start_time": _time_to_hhmm(st),
                "end_time": _time_to_hhmm(et),
                "is_emergency": bool(getattr(r, "is_emergency", False)),
                "shift_status": status,
                "shift_amount": amount,
                "shift_approval_required": approval_required,
                "staff_total": len(day_staff),
                "staff_preview": day_staff[:3],
                "all_mode": False,
            }

        # Include days where the selected user has no plan record, but we still want staff preview and/or fact
        for day_key, day_staff in staff_by_day.items():
            if day_key in out:
                continue
            fact = fact_by_day.get(day_key)
            status = str(getattr(fact, "status", None) or "") or None if fact is not None else None
            approval_required = bool(getattr(fact, "approval_required", False)) if fact is not None else False
            amount: int | None = None
            if fact is not None:
                amount = (
                    getattr(fact, "amount_approved", None)
                    if getattr(fact, "amount_approved", None) is not None
                    else (
                        getattr(fact, "amount_submitted", None)
                        if getattr(fact, "amount_submitted", None) is not None
                        else getattr(fact, "amount_default", None)
                    )
                )
            out[day_key] = {
                "kind": "",
                "hours": None,
                "start_time": None,
                "end_time": None,
                "is_emergency": bool(getattr(fact, "is_emergency", False)) if fact is not None else False,
                "shift_status": status,
                "shift_amount": amount,
                "shift_approval_required": approval_required,
                "staff_total": len(day_staff),
                "staff_preview": day_staff[:3],
                "all_mode": False,
            }

        # Include pure-fact days (if fact exists but plan is empty and there are no staff rows)
        for day_key, fact in fact_by_day.items():
            if day_key in out:
                continue
            status = str(getattr(fact, "status", None) or "") or None
            approval_required = bool(getattr(fact, "approval_required", False))
            amount = (
                getattr(fact, "amount_approved", None)
                if getattr(fact, "amount_approved", None) is not None
                else (
                    getattr(fact, "amount_submitted", None)
                    if getattr(fact, "amount_submitted", None) is not None
                    else getattr(fact, "amount_default", None)
                )
            )
            out[day_key] = {
                "kind": "",
                "hours": None,
                "start_time": None,
                "end_time": None,
                "is_emergency": bool(getattr(fact, "is_emergency", False)),
                "shift_status": status,
                "shift_amount": amount,
                "shift_approval_required": approval_required,
                "staff_total": 0,
                "staff_preview": [],
                "all_mode": False,
            }

    return {"year": y, "month": m, "days": out}


@app.post("/api/schedule/day")
async def schedule_api_day(
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    rflags = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin_or_manager = bool(rflags.is_admin or rflags.is_manager)

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Неверный формат")

    day_raw = str(body.get("day") or "").strip()
    kind = str(body.get("kind") or "").strip()
    hours_raw = body.get("hours")
    start_time_raw = body.get("start_time")
    end_time_raw = body.get("end_time")
    target_user_id = body.get("user_id")

    if not day_raw:
        raise HTTPException(status_code=422, detail="Не задан день")
    try:
        day = datetime.strptime(day_raw, "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=422, detail="Неверная дата")

    uid = int(actor.id)
    if target_user_id is not None:
        if not is_admin_or_manager:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        try:
            uid = int(target_user_id)
        except Exception:
            raise HTTPException(status_code=422, detail="Неверный user_id")

    # Load existing row
    existing = (
        await session.execute(
            select(WorkShiftDay)
            .where(WorkShiftDay.user_id == int(uid))
            .where(WorkShiftDay.day == day)
        )
    ).scalar_one_or_none()

    if not kind:
        fact = (
            await session.execute(
                select(ShiftInstance)
                .where(ShiftInstance.user_id == int(uid))
                .where(ShiftInstance.day == day)
            )
        ).scalar_one_or_none()
        if (
            fact is not None
            and getattr(fact, "ended_at", None) is None
            and getattr(fact, "status", None) == ShiftInstanceStatus.STARTED
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Нельзя очистить день: у сотрудника уже открыта активная смена. "
                    "Сначала завершите смену или отмените её через отдельное действие."
                ),
            )
        if existing is not None:
            await session.delete(existing)
            await session.flush()
        return {"ok": True}

    if kind not in {"work", "off"}:
        raise HTTPException(status_code=422, detail="Неверный тип")

    hours: int | None = None
    if kind == "work":
        # Legacy: keep storing integer hours when it is a whole number.
        # Primary source of truth is start_time/end_time.
        try:
            hours = int(hours_raw) if hours_raw is not None else None
            if hours is not None and hours <= 0:
                hours = None
        except Exception:
            hours = None
    else:
        hours = None

    # Defaults: if not provided, treat as default 10:00–18:00
    start_time: time | None = None
    end_time: time | None = None
    if kind == "work":
        if "start_time" in body or "end_time" in body:
            start_time = _parse_hhmm_time(start_time_raw, field_name="Начало")
            end_time = _parse_hhmm_time(end_time_raw, field_name="Конец")
        else:
            start_time = DEFAULT_SHIFT_START
            end_time = DEFAULT_SHIFT_END

    start_time, end_time = _normalize_shift_times(kind=kind, start_time=start_time, end_time=end_time)

    if kind == "work" and start_time is not None and end_time is not None:
        h_int = calc_int_hours_from_times(start_time=start_time, end_time=end_time)
        if h_int is None:
            raise HTTPException(status_code=422, detail="Часы должны быть целыми (например 10:00–18:00)")
        hours = int(h_int)

    if existing is None:
        existing = WorkShiftDay(
            user_id=int(uid),
            day=day,
            kind=kind,
            hours=hours,
            start_time=start_time,
            end_time=end_time,
            is_emergency=False,
        )
        session.add(existing)
    else:
        existing.kind = kind
        existing.hours = hours
        existing.start_time = start_time
        existing.end_time = end_time
        existing.is_emergency = bool(getattr(existing, "is_emergency", False))

    await session.flush()

    if kind == "work" and start_time is not None and end_time is not None:
        add_after_commit_callback(
            session,
            lambda: _notify_shift_if_due_after_commit(user_id=int(uid), day=day, start_time=start_time, end_time=end_time),
        )
    return {"ok": True}


@app.post("/api/schedule/delete")
async def schedule_api_delete(
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    rflags = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin_or_manager = bool(rflags.is_admin or rflags.is_manager)
    if not is_admin_or_manager:
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Неверный формат")

    day_raw = str(body.get("day") or "").strip()
    target_user_id = body.get("user_id")
    if not day_raw:
        raise HTTPException(status_code=422, detail="Не задан день")
    try:
        day = datetime.strptime(day_raw, "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=422, detail="Неверная дата")

    uid = int(actor.id)
    if target_user_id is not None:
        try:
            uid = int(target_user_id)
        except Exception:
            raise HTTPException(status_code=422, detail="Неверный user_id")

    plan = (
        await session.execute(
            select(WorkShiftDay)
            .where(WorkShiftDay.user_id == int(uid))
            .where(WorkShiftDay.day == day)
        )
    ).scalar_one_or_none()

    fact = (
        await session.execute(
            select(ShiftInstance)
            .where(ShiftInstance.user_id == int(uid))
            .where(ShiftInstance.day == day)
        )
    ).scalar_one_or_none()

    if fact is not None and getattr(fact, "ended_at", None) is None and getattr(fact, "status", None) == ShiftInstanceStatus.STARTED:
        raise HTTPException(
            status_code=409,
            detail=(
                "Нельзя удалить смену: у сотрудника сейчас активная смена. "
                "Сначала завершите смену."
            ),
        )

    if plan is not None:
        await session.delete(plan)
        await session.flush()

    if fact is not None:
        ev = ShiftInstanceEvent(
            shift_id=int(getattr(fact, "id")),
            actor_user_id=int(getattr(actor, "id")),
            type="system.shift_deleted",
            payload={"day": str(day), "user_id": int(uid)},
        )
        session.add(ev)
        await session.flush()

    return {"ok": True}


@app.get("/api/schedule/day/staff")
async def schedule_api_day_staff(
    request: Request,
    day: str,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    _ = await load_staff_user(session, admin_id)

    day_raw = str(day or "").strip()
    try:
        d = datetime.strptime(day_raw, "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=422, detail="Неверная дата")

    rows = list(
        (
            await session.execute(
                select(WorkShiftDay, User)
                .join(User, User.id == WorkShiftDay.user_id)
                .where(WorkShiftDay.day == d)
                .where(WorkShiftDay.kind == "work")
                .where(User.is_deleted == False)
                .where(User.status == UserStatus.APPROVED)
                .order_by(User.first_name, User.last_name, User.id)
            )
        ).all()
    )

    # Facts for this day to show per-user status
    user_ids = sorted({int(getattr(u, "id")) for _, u in rows if getattr(u, "id", None) is not None})
    facts_by_user: dict[int, ShiftInstance] = {}
    if user_ids:
        frs = list(
            (
                await session.scalars(
                    select(ShiftInstance)
                    .where(ShiftInstance.day == d)
                    .where(ShiftInstance.user_id.in_(user_ids))
                )
            ).all()
        )
        for fr in frs:
            uid = getattr(fr, "user_id", None)
            if uid is None:
                continue
            facts_by_user[int(uid)] = fr

    out = []
    for shift, u in rows:
        name = (" ".join([str(getattr(u, "first_name", "") or "").strip(), str(getattr(u, "last_name", "") or "").strip()]).strip())
        if not name:
            name = str(getattr(u, "username", "") or "") or f"User #{int(getattr(u, 'id'))}"
        uid = int(getattr(u, "id"))
        fact = facts_by_user.get(uid)
        st, et = _normalize_shift_times(
            kind=str(getattr(shift, "kind", "") or ""),
            start_time=getattr(shift, "start_time", None),
            end_time=getattr(shift, "end_time", None),
        )
        out.append(
            {
                "user_id": uid,
                "name": name,
                "color": str(getattr(u, "color", "#94a3b8") or "#94a3b8"),
                "kind": str(getattr(shift, "kind", "") or ""),
                "hours": getattr(shift, "hours", None),
                "start_time": _time_to_hhmm(st),
                "end_time": _time_to_hhmm(et),
                "is_emergency": bool(getattr(shift, "is_emergency", False)),
                "shift_status": str(getattr(fact, "status", "") or "") if fact is not None else "",
                "shift_approval_required": bool(getattr(fact, "approval_required", False)) if fact is not None else False,
            }
        )

    swap = (
        await session.execute(
            select(ShiftSwapRequest)
            .where(ShiftSwapRequest.day == d)
            .order_by(ShiftSwapRequest.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    swap_info = None
    if swap is not None:
        swap_info = {
            "id": int(getattr(swap, "id")),
            "status": str(getattr(swap, "status", "") or ""),
            "from_user_id": int(getattr(swap, "from_user_id")),
            "accepted_by_user_id": int(getattr(swap, "accepted_by_user_id")) if getattr(swap, "accepted_by_user_id", None) else None,
            "bonus_amount": getattr(swap, "bonus_amount", None),
            "reason": str(getattr(swap, "reason", "") or ""),
        }

    return {"day": str(d), "staff": out, "swap_request": swap_info}


@app.post("/api/schedule/emergency")
async def schedule_api_emergency(
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    rflags = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin_or_manager = bool(rflags.is_admin or rflags.is_manager)

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Неверный формат")

    day_raw = str(body.get("day") or "").strip()
    comment = str(body.get("comment") or "").strip() or None
    start_time_raw = body.get("start_time")
    end_time_raw = body.get("end_time")
    replace = bool(body.get("replace") or False)
    target_user_id = body.get("user_id")

    if not day_raw:
        raise HTTPException(status_code=422, detail="Не задан день")
    try:
        d = datetime.strptime(day_raw, "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=422, detail="Неверная дата")

    start_time: time | None = None
    end_time: time | None = None
    if "start_time" in body or "end_time" in body:
        start_time = _parse_hhmm_time(start_time_raw, field_name="Начало")
        end_time = _parse_hhmm_time(end_time_raw, field_name="Конец")
    else:
        start_time = DEFAULT_SHIFT_START
        end_time = DEFAULT_SHIFT_END

    start_time, end_time = _normalize_shift_times(kind="work", start_time=start_time, end_time=end_time)
    if start_time is None or end_time is None:
        raise HTTPException(status_code=422, detail="Не задано время смены")
    h_int = calc_int_hours_from_times(start_time=start_time, end_time=end_time)
    if h_int is None:
        raise HTTPException(status_code=422, detail="Можно только целые часы. Выберите другое время.")
    hours = int(h_int)

    uid = int(actor.id)
    if target_user_id is not None:
        if not is_admin_or_manager:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        try:
            uid = int(target_user_id)
        except Exception:
            raise HTTPException(status_code=422, detail="Неверный user_id")

    existing = (
        await session.execute(
            select(WorkShiftDay)
            .where(WorkShiftDay.user_id == int(uid))
            .where(WorkShiftDay.day == d)
        )
    ).scalar_one_or_none()

    if existing is not None:
        if bool(getattr(existing, "is_emergency", False)):
            # Don't create duplicates; allow editing existing emergency
            existing.kind = "work"
            existing.hours = hours
            existing.comment = comment
            existing.start_time = start_time
            existing.end_time = end_time
            await session.flush()
            if start_time is not None and end_time is not None:
                add_after_commit_callback(
                    session,
                    lambda: _notify_shift_if_due_after_commit(user_id=int(uid), day=d, start_time=start_time, end_time=end_time),
                )
            return {"ok": True, "updated": True}

        # Existing planned shift: require explicit replace
        if not replace:
            raise HTTPException(status_code=409, detail="Смена уже запланирована. Заменить?")

        existing.kind = "work"
        existing.hours = hours
        existing.is_emergency = True
        existing.comment = comment
        existing.start_time = start_time
        existing.end_time = end_time
        await session.flush()
        if start_time is not None and end_time is not None:
            add_after_commit_callback(
                session,
                lambda: _notify_shift_if_due_after_commit(user_id=int(uid), day=d, start_time=start_time, end_time=end_time),
            )
        return {"ok": True, "replaced": True}

    row = WorkShiftDay(
        user_id=int(uid),
        day=d,
        kind="work",
        hours=hours,
        start_time=start_time,
        end_time=end_time,
        is_emergency=True,
        comment=comment,
    )
    session.add(row)
    await session.flush()
    if start_time is not None and end_time is not None:
        add_after_commit_callback(
            session,
            lambda: _notify_shift_if_due_after_commit(user_id=int(uid), day=d, start_time=start_time, end_time=end_time),
        )
    return {"ok": True, "created": True}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(select(User).where(User.is_deleted == False).order_by(User.created_at.desc()))
    users: List[User] = res.scalars().all()
    return templates.TemplateResponse("index.html", {"request": request, "users": users, "admin_id": admin_id})


@app.get("/users/{user_id}", response_class=HTMLResponse)
async def user_modal(user_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    user = await load_user(session, user_id)
    old_status = user.status
    confirm_q = request.query_params.get("confirm")
    confirm_initial = False
    if confirm_q is not None and str(confirm_q).lower() in ("1", "true", "yes", "y"): 
        confirm_initial = True
    return templates.TemplateResponse(
        "partials/user_modal.html", {"request": request, "user": user, "confirm_initial": confirm_initial}
    )


@app.post("/users/{user_id}/update", response_class=HTMLResponse)
async def user_update(
    user_id: int,
    request: Request,
    first_name: Optional[str] = Form(None),
    last_name: Optional[str] = Form(None),
    birth_date: Optional[str] = Form(None),
    rate_k: Optional[int] = Form(None),
    schedule: Optional[str] = Form(None),
    position: Optional[str] = Form(None),
    status_value: Optional[str] = Form(None),
    color: Optional[str] = Form(None),
    admin_id: int = Depends(require_admin_or_manager),
    session: AsyncSession = Depends(get_db),
):
    user = await load_user(session, user_id)
    old_status = user.status
    logger.info(
        "user_update request",
        extra={
            "admin_id": int(admin_id),
            "user_id": int(user_id),
            "fields": {
                "first_name": first_name is not None,
                "last_name": last_name is not None,
                "birth_date": bool(birth_date),
                "rate_k": rate_k is not None,
                "schedule": schedule is not None,
                "position": position is not None,
                "status_value": status_value is not None,
                "color": color is not None,
            },
        },
    )
    if first_name is not None:
        user.first_name = first_name or None
    if last_name is not None:
        user.last_name = last_name or None
    if birth_date:
        try:
            from datetime import datetime as dt
            user.birth_date = dt.strptime(birth_date, "%Y-%m-%d").date()
        except Exception:
            pass
    if rate_k is not None:
        try:
            user.rate_k = int(rate_k)
        except Exception:
            pass
    if schedule is not None:
        user.schedule = Schedule(schedule) if schedule else None
    if position is not None:
        user.position = Position(position) if position else None
    if status_value is not None and status_value in {s.value for s in UserStatus}:
        user.status = UserStatus(status_value)
    if color is not None:
        from shared.services.user_color import assign_user_color

        c = str(color or "").strip()
        if not c:
            seed = int(getattr(user, "tg_id", 0) or getattr(user, "id", 0) or 0)
            user.color = await assign_user_color(session, seed=seed)
        else:
            if not re.match(r"^#[0-9A-Fa-f]{6}$", c):
                raise HTTPException(status_code=422, detail="Неверный цвет")
            user.color = c.upper()
    await session.flush()
    # log edit
    repo = AdminLogRepo(session)
    await repo.log(admin_tg_id=admin_id, user_id=user.id, action=AdminActionType.EDIT, payload=None)
    # if status changed, notify user with updated keyboard
    try:
        if old_status != user.status:
            from aiogram import Bot  # local import to avoid unnecessary dependency at startup
            from bot.app.keyboards.main import main_menu_kb
            bot = Bot(token=settings.BOT_TOKEN)
            try:
                await bot.send_message(
                    user.tg_id,
                    "Ваш статус обновлён.",
                    reply_markup=main_menu_kb(user.status, user.tg_id, user.position),
                )
            finally:
                await bot.session.close()
    except Exception:
        # non-fatal
        pass
    # return refreshed table row as OOB swap
    return templates.TemplateResponse(
        "partials/user_row.html",
        {"request": request, "u": user, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


@app.post("/users/{user_id}/blacklist", response_class=HTMLResponse)
async def user_blacklist(user_id: int, request: Request, admin_id: int = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    user = await load_user(session, user_id)
    user.status = UserStatus.BLACKLISTED
    await session.flush()
    repo = AdminLogRepo(session)
    await repo.log(admin_tg_id=admin_id, user_id=user.id, action=AdminActionType.BLACKLIST, payload=None)
    # notify user about status change with updated keyboard
    try:
        from aiogram import Bot  # local import to avoid heavy import at startup
        from bot.app.keyboards.main import main_menu_kb
        bot = Bot(token=settings.BOT_TOKEN)
        try:
            await bot.send_message(
                user.tg_id,
                "Ваш статус обновлён.",
                reply_markup=main_menu_kb(user.status, user.tg_id, user.position),
            )
        finally:
            await bot.session.close()
    except Exception:
        # non-fatal
        pass
    return templates.TemplateResponse(
        "partials/user_row.html",
        {"request": request, "u": user, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


@app.post("/users/{user_id}/delete")
async def user_delete(user_id: int, admin_id: int = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    # Ensure user exists first
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(404)
    # idempotent soft delete
    if not user.is_deleted:
        user.is_deleted = True
        await session.flush()
        repo = AdminLogRepo(session)
        await repo.log(
            admin_tg_id=admin_id,
            user_id=user.id,
            action=AdminActionType.BLACKLIST,
            payload={"delete": True},
        )
        try:
            from aiogram import Bot  # local import to avoid heavy import at startup
            from bot.app.utils.bot_commands import sync_commands_for_chat

            bot = Bot(token=settings.BOT_TOKEN)
            try:
                await sync_commands_for_chat(
                    bot=bot,
                    chat_id=int(user.tg_id),
                    is_admin=int(user.tg_id) in settings.admin_ids,
                    status=None,
                    position=None,
                )
            finally:
                await bot.session.close()
        except Exception:
            pass
    return HTMLResponse(
        f'<tr id="row-{user_id}" hx-swap-oob="delete"></tr>',
        headers={"HX-Trigger": "close-modal"},
    )


@app.post("/users/{user_id}/message")
async def user_message(user_id: int, text: str = Form(...), admin_id: int = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(User).where(User.id == user_id).where(User.is_deleted == False))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(404)
    if user.status == UserStatus.BLACKLISTED:
        raise HTTPException(400, detail="User blacklisted")
    messenger = Messenger(settings.BOT_TOKEN)
    ok = await messenger.send_message(user.tg_id, text)
    if not ok:
        raise HTTPException(502, detail="Failed to send message")
    repo = AdminLogRepo(session)
    await repo.log(admin_tg_id=admin_id, user_id=user.id, action=AdminActionType.MESSAGE, payload={"text": text})
    return Response(status_code=204, headers={"HX-Trigger": "close-modal"})


@app.get("/broadcast", response_class=HTMLResponse, name="broadcast_page")
async def broadcast_page(request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    # positions list for checkboxes
    res = await session.execute(
        select(User.position)
        .where(User.is_deleted == False)
        .group_by(User.position)
        .order_by(User.position)
    )
    positions = []
    for (pos,) in res.all():
        positions.append(pos.value if hasattr(pos, "value") else str(pos or ""))
    return templates.TemplateResponse("broadcast.html", {"request": request, "positions": positions})


@app.get("/broadcast_modal", response_class=HTMLResponse)
async def broadcast_modal(request: Request, admin_id: int = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(User).where(User.status == UserStatus.APPROVED).where(User.is_deleted == False))
    users = res.scalars().all()
    return templates.TemplateResponse("partials/broadcast_modal.html", {"request": request, "users": users})


@app.get("/sm-mold", response_class=HTMLResponse, name="sm_mold_page")
async def sm_mold_page(
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    # Access policy: same auth as CRM (cookie session). No extra role restrictions.
    await load_staff_user(session, admin_id)
    return templates.TemplateResponse(
        "crm/sm_mold.html",
        {
            "request": request,
        },
    )


@app.get("/about", response_class=HTMLResponse, name="about_page")
@app.get("/crm/about", response_class=HTMLResponse)
async def about_page(request: Request):
    # Public page (no auth). Telegram may prefetch links for preview.
    return templates.TemplateResponse(
        "crm/sm_mold.html",
        {
            "request": request,
        },
    )


@app.post("/broadcast")
async def broadcast(text: str = Form(...), user_ids: Optional[str] = Form(None), admin_id: int = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    ids: Optional[List[int]] = None
    if user_ids:
        ids = [int(x) for x in user_ids.split(",") if x.strip()]
    q = select(User).where(User.status == UserStatus.APPROVED).where(User.is_deleted == False)
    if ids:
        q = q.where(User.id.in_(ids))
    res = await session.execute(q)
    users = res.scalars().all()
    messenger = Messenger(settings.BOT_TOKEN)
    ok_count = 0
    for u in users:
        ok = await messenger.send_message(u.tg_id, text)
        if ok:
            ok_count += 1
            repo = AdminLogRepo(session)
            await repo.log(admin_tg_id=admin_id, user_id=u.id, action=AdminActionType.BROADCAST, payload={"text": text})
    return Response(status_code=204, headers={"HX-Trigger": "close-modal"})


# ========== Tasks (CRM) ==========


@app.get("/tasks", response_class=HTMLResponse, name="tasks_board")
async def tasks_board(request: Request, admin_id: int = Depends(require_admin_or_manager), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)
    is_designer = bool(getattr(r, "is_designer", False))
    can_use_archive = can_use_tasks_archive(r=r)

    q = (request.query_params.get("q") or "").strip()
    mine = (request.query_params.get("mine") or "").strip() in {"1", "true", "yes", "on"}
    priority = (request.query_params.get("priority") or "").strip()
    due = (request.query_params.get("due") or "").strip()

    if is_designer:
        mine = True
    assignee_id_raw = (request.query_params.get("assignee_id") or "").strip()
    assignee_id: int | None = None
    if assignee_id_raw:
        try:
            assignee_id = int(assignee_id_raw)
        except Exception:
            assignee_id = None

    if not (is_admin or is_manager):
        assignee_id = None

    from shared.models import task_assignees
    from sqlalchemy import exists, and_

    urgent_first = case((Task.priority == TaskPriority.URGENT, 0), else_=1)
    free_time_last = case((Task.priority == TaskPriority.FREE_TIME, 1), else_=0)

    query = (
        select(Task)
        .where(Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.DONE]))
        .options(selectinload(Task.assignees), selectinload(Task.created_by_user))
        .order_by(free_time_last.asc(), urgent_first.asc(), Task.due_at.asc().nullslast(), Task.created_at.desc(), Task.id.desc())
    )
    if q:
        from sqlalchemy import or_

        like = f"%{q}%"
        query = query.where(or_(Task.title.ilike(like), Task.description.ilike(like)))
    if priority == TaskPriority.URGENT.value:
        query = query.where(Task.priority == TaskPriority.URGENT)
    elif priority == TaskPriority.FREE_TIME.value:
        query = query.where(Task.priority == TaskPriority.FREE_TIME)
    elif priority == TaskPriority.NORMAL.value:
        query = query.where(Task.priority == TaskPriority.NORMAL)

    if due == "with_due":
        query = query.where(Task.due_at.is_not(None))
    elif due == "overdue":
        query = query.where(Task.due_at.is_not(None)).where(Task.due_at < utc_now())

    if assignee_id is not None:
        has_selected = exists(
            select(1).where(and_(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == int(assignee_id)))
        )
        query = query.where(has_selected)
    if mine or is_designer:
        has_actor = exists(
            select(1).where(and_(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == int(actor.id)))
        )
        query = query.where(has_actor)

    res = await session.execute(query)
    tasks = list(res.scalars().unique().all())

    items_by = {TaskStatus.NEW.value: [], TaskStatus.IN_PROGRESS.value: [], TaskStatus.REVIEW.value: [], TaskStatus.DONE.value: []}
    for t in tasks:
        view = _task_card_view(t, actor_id=int(actor.id))
        try:
            view["permissions"] = _task_permissions(t=t, actor=actor, is_admin=is_admin, is_manager=is_manager)
        except Exception:
            view["permissions"] = None
        items_by[(t.status.value if hasattr(t.status, "value") else str(t.status))].append(view)

    columns = [
        {"status": TaskStatus.NEW.value, "title": "Новые", "items": items_by[TaskStatus.NEW.value]},
        {"status": TaskStatus.IN_PROGRESS.value, "title": "В работе", "items": items_by[TaskStatus.IN_PROGRESS.value]},
        {"status": TaskStatus.REVIEW.value, "title": "На проверке", "items": items_by[TaskStatus.REVIEW.value]},
        {"status": TaskStatus.DONE.value, "title": "Выполнено", "items": items_by[TaskStatus.DONE.value]},
    ]

    res_u = await session.execute(
        select(User)
        .where(User.is_deleted == False)
        .where(User.status == UserStatus.APPROVED)
        .order_by(User.first_name, User.last_name, User.id)
    )
    users = list(res_u.scalars().all())
    users_json = json.dumps(
        [
            {
                "id": int(u.id),
                "first_name": u.first_name,
                "last_name": u.last_name,
                "is_admin": bool(int(getattr(u, "tg_id", 0) or 0) in settings.admin_ids),
                "is_manager": bool(u.status == UserStatus.APPROVED and u.position == Position.MANAGER),
                "color": str(getattr(u, "color", "") or ""),
            }
            for u in users
        ],
        ensure_ascii=False,
    )

    return templates.TemplateResponse(
        "tasks/board.html",
        {
            "request": request,
            "board_url": request.url_for("tasks_board"),
            "columns": columns,
            "q": q,
            "mine": mine,
            "priority": priority,
            "due": due,
            "assignee_id": assignee_id,
            "users": users,
            "is_admin": is_admin,
            "is_manager": is_manager,
            "is_designer": is_designer,
            "can_use_archive": bool(can_use_archive),
            "users_json": users_json,
            "base_template": "base.html",
            "archive_url": request.url_for("tasks_archive"),
        },
    )


@app.get("/tasks/public", response_class=HTMLResponse, name="tasks_board_public")
@app.get("/crm/tasks", response_class=HTMLResponse)
async def tasks_board_public(request: Request, admin_id: int = Depends(require_authenticated_user), session: AsyncSession = Depends(get_db)):
    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)
    is_designer = bool(getattr(r, "is_designer", False))
    can_use_archive = can_use_tasks_archive(r=r)

    mine_param_present = "mine" in dict(request.query_params)
    if not mine_param_present and not (is_admin or is_manager) and not is_designer:
        # Hard default: for non-admin/manager always open board with mine=1 unless explicitly set.
        return RedirectResponse(url=str(request.url.include_query_params(mine="1")), status_code=302)

    q = (request.query_params.get("q") or "").strip()
    mine = (request.query_params.get("mine") or "").strip() in {"1", "true", "yes", "on"}
    priority = (request.query_params.get("priority") or "").strip()
    due = (request.query_params.get("due") or "").strip()

    # Public board: show all tasks (same as admin board), but without sidebar
    from shared.models import task_assignees
    from sqlalchemy import or_ as _or, exists, and_

    urgent_first = case((Task.priority == TaskPriority.URGENT, 0), else_=1)
    free_time_last = case((Task.priority == TaskPriority.FREE_TIME, 1), else_=0)
    query = (
        select(Task)
        .where(Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.DONE]))
        .options(selectinload(Task.assignees), selectinload(Task.created_by_user))
        .order_by(free_time_last.asc(), urgent_first.asc(), Task.due_at.asc().nullslast(), Task.created_at.desc(), Task.id.desc())
    )
    if q:
        like = f"%{q}%"
        query = query.where(_or(Task.title.ilike(like), Task.description.ilike(like)))
    if priority == TaskPriority.URGENT.value:
        query = query.where(Task.priority == TaskPriority.URGENT)
    elif priority == TaskPriority.FREE_TIME.value:
        query = query.where(Task.priority == TaskPriority.FREE_TIME)
    elif priority == TaskPriority.NORMAL.value:
        query = query.where(Task.priority == TaskPriority.NORMAL)

    if due == "with_due":
        query = query.where(Task.due_at.is_not(None))
    elif due == "overdue":
        query = query.where(Task.due_at.is_not(None)).where(Task.due_at < utc_now())

    if mine or is_designer:
        has_actor = exists(
            select(1).where(and_(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == int(actor.id)))
        )
        query = query.where(has_actor)

    res = await session.execute(query)
    tasks = list(res.scalars().unique().all())

    items_by = {TaskStatus.NEW.value: [], TaskStatus.IN_PROGRESS.value: [], TaskStatus.REVIEW.value: [], TaskStatus.DONE.value: []}
    for t in tasks:
        view = _task_card_view(t, actor_id=int(actor.id))
        try:
            view["permissions"] = _task_permissions(t=t, actor=actor, is_admin=is_admin, is_manager=is_manager)
        except Exception:
            view["permissions"] = None
        items_by[(t.status.value if hasattr(t.status, "value") else str(t.status))].append(view)

    columns = [
        {"status": TaskStatus.NEW.value, "title": "Новые", "items": items_by[TaskStatus.NEW.value]},
        {"status": TaskStatus.IN_PROGRESS.value, "title": "В работе", "items": items_by[TaskStatus.IN_PROGRESS.value]},
        {"status": TaskStatus.REVIEW.value, "title": "На проверке", "items": items_by[TaskStatus.REVIEW.value]},
        {"status": TaskStatus.DONE.value, "title": "Выполнено", "items": items_by[TaskStatus.DONE.value]},
    ]

    res_u = await session.execute(
        select(User)
        .where(User.is_deleted == False)
        .where(User.status == UserStatus.APPROVED)
        .order_by(User.first_name, User.last_name, User.id)
    )
    users = list(res_u.scalars().all())
    users_json = json.dumps(
        [
            {
                "id": int(u.id),
                "first_name": u.first_name,
                "last_name": u.last_name,
                "is_admin": bool(int(getattr(u, "tg_id", 0) or 0) in settings.admin_ids),
                "is_manager": bool(u.status == UserStatus.APPROVED and u.position == Position.MANAGER),
                "color": str(getattr(u, "color", "") or ""),
            }
            for u in users
        ],
        ensure_ascii=False,
    )

    resp = templates.TemplateResponse(
        "tasks/board.html",
        {
            "request": request,
            "board_url": request.url_for("tasks_board_public"),
            "columns": columns,
            "q": q,
            "mine": mine,
            "priority": priority,
            "due": due,
            "assignee_id": None,
            "users": users,
            "is_admin": is_admin,
            "is_manager": is_manager,
            "is_designer": is_designer,
            "can_use_archive": bool(can_use_archive),
            "users_json": users_json,
            "base_template": "base_public.html",
            "archive_url": request.url_for("tasks_archive_public"),
        },
    )

    return resp


@app.get("/api/public/tasks")
async def tasks_api_public_list(
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(getattr(r, "is_admin", False))
    is_manager = bool(getattr(r, "is_manager", False))
    is_designer = bool(getattr(r, "is_designer", False))

    q = (request.query_params.get("q") or "").strip()
    mine = (request.query_params.get("mine") or "").strip() in {"1", "true", "yes", "on"}
    priority = (request.query_params.get("priority") or "").strip()
    due = (request.query_params.get("due") or "").strip()
    status_q = (request.query_params.get("status") or "").strip()

    if is_designer:
        mine = True

    from shared.models import task_assignees
    from sqlalchemy import or_ as _or, exists, and_

    urgent_first = case((Task.priority == TaskPriority.URGENT, 0), else_=1)
    query = (
        select(Task)
        .where(Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.DONE, TaskStatus.ARCHIVED]))
        .options(selectinload(Task.assignees), selectinload(Task.created_by_user))
        .order_by(urgent_first.asc(), Task.due_at.asc().nullslast(), Task.created_at.desc(), Task.id.desc())
    )
    if q:
        like = f"%{q}%"
        query = query.where(_or(Task.title.ilike(like), Task.description.ilike(like)))
    if priority == TaskPriority.URGENT.value:
        query = query.where(Task.priority == TaskPriority.URGENT)
    elif priority == TaskPriority.NORMAL.value:
        query = query.where(Task.priority == TaskPriority.NORMAL)
    if due == "with_due":
        query = query.where(Task.due_at.is_not(None))
    elif due == "overdue":
        query = query.where(Task.due_at.is_not(None)).where(Task.due_at < utc_now())
    if status_q and status_q in {s.value for s in TaskStatus}:
        query = query.where(Task.status == TaskStatus(status_q))
    if mine:
        has_actor = exists(
            select(1).where(and_(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == int(actor.id)))
        )
        query = query.where(has_actor)

    res = await session.execute(query)
    tasks = list(res.scalars().unique().all())
    return {
        "items": [
            {
                **_task_card_view(t, actor_id=int(actor.id)),
                "permissions": (
                    _task_permissions(t=t, actor=actor, is_admin=is_admin, is_manager=is_manager)
                    if True
                    else None
                ),
            }
            for t in tasks
        ],
        "mine": bool(mine),
    }


@app.get("/tasks/archive", response_class=HTMLResponse, name="tasks_archive")
async def tasks_archive(request: Request, admin_id: int = Depends(require_authenticated_user), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)

    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)
    is_designer = bool(getattr(r, "is_designer", False))

    if not can_use_tasks_archive(r=r):
        return RedirectResponse(url="/crm/tasks", status_code=302)

    q = (request.query_params.get("q") or "").strip()
    mine = (request.query_params.get("mine") or "").strip() in {"1", "true", "yes", "on"}
    priority = (request.query_params.get("priority") or "").strip()
    due = (request.query_params.get("due") or "").strip()
    status_q = (request.query_params.get("status") or "").strip()
    assignee_id_raw = (request.query_params.get("assignee_id") or "").strip()
    assignee_id: int | None = None
    if assignee_id_raw:
        try:
            assignee_id = int(assignee_id_raw)
        except Exception:
            assignee_id = None

    from shared.models import task_assignees
    from sqlalchemy import exists, and_, or_

    has_any_acl = exists(select(1).where(task_assignees.c.task_id == Task.id))
    has_actor = exists(
        select(1).where(and_(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == int(actor.id)))
    )

    query = (
        select(Task)
        .where(Task.status == TaskStatus.ARCHIVED)
        .options(selectinload(Task.assignees))
        .order_by(Task.archived_at.desc().nullslast(), Task.updated_at.desc(), Task.id.desc())
    )
    if q:
        from sqlalchemy import or_

        like = f"%{q}%"
        query = query.where(or_(Task.title.ilike(like), Task.description.ilike(like)))

    if priority == TaskPriority.URGENT.value:
        query = query.where(Task.priority == TaskPriority.URGENT)
    elif priority == TaskPriority.NORMAL.value:
        query = query.where(Task.priority == TaskPriority.NORMAL)

    if due == "with_due":
        query = query.where(Task.due_at.is_not(None))
    elif due == "overdue":
        query = query.where(Task.due_at.is_not(None)).where(Task.due_at < utc_now())

    if mine:
        pass

    if is_designer:
        has_actor_only = exists(
            select(1).where(and_(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == int(actor.id)))
        )
        query = query.where(has_actor_only)

    if assignee_id is not None:
        has_selected = exists(
            select(1).where(and_(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == int(assignee_id)))
        )
        query = query.where(has_selected)

    if status_q:
        if status_q in {s.value for s in TaskStatus}:
            query = query.where(Task.status == TaskStatus(status_q))

    res = await session.execute(query)
    tasks = list(res.scalars().unique().all())

    items = []
    for t in tasks:
        v = _task_card_view(t)
        v["archived_at_str"] = format_moscow(getattr(t, "archived_at", None), "%d.%m.%Y %H:%M")
        items.append(v)

    return templates.TemplateResponse(
        "tasks/archive.html",
        {
            "request": request,
            "items": items,
            "q": q,
            "mine": mine,
            "priority": priority,
            "due": due,
            "status": status_q,
            "assignee_id": assignee_id,
            "users": list(
                (
                    await session.scalars(
                        select(User)
                        .where(User.is_deleted == False)
                        .where(User.status == UserStatus.APPROVED)
                        .order_by(User.first_name, User.last_name, User.id)
                    )
                ).all()
            ),
            "is_admin": is_admin,
            "is_manager": is_manager,
            "base_template": "base.html",
            "board_url": request.url_for("tasks_board"),
            "archive_url": request.url_for("tasks_archive"),
        },
    )


@app.get("/tasks/public/archive", response_class=HTMLResponse, name="tasks_archive_public")
async def tasks_archive_public(request: Request, admin_id: int = Depends(require_authenticated_user), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)

    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)

    if not can_use_tasks_archive(r=r):
        return RedirectResponse(url="/crm/tasks", status_code=302)

    q = (request.query_params.get("q") or "").strip()
    mine = (request.query_params.get("mine") or "").strip() in {"1", "true", "yes", "on"}
    priority = (request.query_params.get("priority") or "").strip()
    due = (request.query_params.get("due") or "").strip()
    status_q = (request.query_params.get("status") or "").strip()
    assignee_id_raw = (request.query_params.get("assignee_id") or "").strip()
    assignee_id: int | None = None
    if assignee_id_raw:
        try:
            assignee_id = int(assignee_id_raw)
        except Exception:
            assignee_id = None

    from shared.models import task_assignees
    from sqlalchemy import exists, and_, or_

    has_any_acl = exists(select(1).where(task_assignees.c.task_id == Task.id))
    has_actor = exists(select(1).where(and_(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == int(actor.id))))

    query = (
        select(Task)
        .where(Task.status == TaskStatus.ARCHIVED)
        .options(selectinload(Task.assignees))
        .order_by(Task.archived_at.desc().nullslast(), Task.updated_at.desc(), Task.id.desc())
    )
    if q:
        from sqlalchemy import or_ as _or

        like = f"%{q}%"
        query = query.where(_or(Task.title.ilike(like), Task.description.ilike(like)))

    if priority == TaskPriority.URGENT.value:
        query = query.where(Task.priority == TaskPriority.URGENT)
    elif priority == TaskPriority.NORMAL.value:
        query = query.where(Task.priority == TaskPriority.NORMAL)

    if due == "with_due":
        query = query.where(Task.due_at.is_not(None))
    elif due == "overdue":
        query = query.where(Task.due_at.is_not(None)).where(Task.due_at < utc_now())

    if mine:
        pass

    if assignee_id is not None:
        has_selected = exists(select(1).where(and_(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == int(assignee_id))))
        query = query.where(has_selected)

    if status_q:
        if status_q in {s.value for s in TaskStatus}:
            query = query.where(Task.status == TaskStatus(status_q))

    res = await session.execute(query)
    tasks = list(res.scalars().unique().all())

    items = []
    for t in tasks:
        v = _task_card_view(t)
        v["archived_at_str"] = format_moscow(getattr(t, "archived_at", None), "%d.%m.%Y %H:%M")
        items.append(v)

    return templates.TemplateResponse(
        "tasks/archive.html",
        {
            "request": request,
            "items": items,
            "q": q,
            "mine": mine,
            "priority": priority,
            "due": due,
            "status": status_q,
            "assignee_id": assignee_id,
            "users": list(
                (
                    await session.scalars(
                        select(User)
                        .where(User.is_deleted == False)
                        .where(User.status == UserStatus.APPROVED)
                        .order_by(User.first_name, User.last_name, User.id)
                    )
                ).all()
            ),
            "is_admin": is_admin,
            "is_manager": is_manager,
            "base_template": "base_public.html",
            "board_url": request.url_for("tasks_board_public"),
            "archive_url": request.url_for("tasks_archive_public"),
        },
    )


@app.post("/api/tasks")
async def tasks_api_create(
    request: Request,
    title: str = Form(...),
    description: str | None = Form(None),
    priority: str = Form("normal"),
    due_at: str | None = Form(None),
    assignee_ids: list[int] = Form([]),
    photo: UploadFile | None = File(None),
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    if priority == TaskPriority.URGENT.value:
        pr = TaskPriority.URGENT
    elif priority == TaskPriority.FREE_TIME.value:
        pr = TaskPriority.FREE_TIME
    else:
        pr = TaskPriority.NORMAL
    due_dt = _parse_due_at_msk(due_at)

    users: list[User] = []
    assignee_ids_clean = [int(x) for x in (assignee_ids or []) if int(x) > 0]
    if assignee_ids_clean:
        users = list(
            (
                await session.scalars(
                    select(User)
                    .where(User.id.in_(assignee_ids_clean))
                    .where(User.is_deleted == False)
                    .where(User.status == UserStatus.APPROVED)
                )
            ).all()
        )
        if len(users) != len(set(assignee_ids_clean)):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Исполнители не найдены")

        r_actor = role_flags(
            tg_id=int(getattr(actor, "tg_id", 0) or 0),
            admin_ids=settings.admin_ids,
            status=actor.status,
            position=actor.position,
        )
        if not (bool(r_actor.is_admin) or bool(r_actor.is_manager)):
            forbidden = [
                u
                for u in users
                if (int(getattr(u, "tg_id", 0) or 0) in settings.admin_ids)
                or (u.status == UserStatus.APPROVED and u.position == Position.MANAGER)
            ]
            if forbidden:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нельзя назначать задачи руководителям/админам")

    t = Task(
        title=title.strip(),
        description=(description or None),
        priority=pr,
        due_at=due_dt,
        status=TaskStatus.NEW,
        created_by_user_id=int(actor.id),
        assignees=[],
    )
    session.add(t)
    if users:
        t.assignees = users
    await session.flush()

    session.add(TaskEvent(task_id=int(t.id), actor_user_id=int(actor.id), type=TaskEventType.CREATED, payload=None))

    try:
        # Notify assignees only (common tasks don't notify by default)
        users_assignees = list(getattr(t, "assignees", None) or [])
        if users_assignees:
            ns = TaskNotificationService(session)
            actor_name = (f"{(actor.first_name or '').strip()} {(actor.last_name or '').strip()}".strip() or f"#{int(actor.id)}")
            recipient_ids = [int(getattr(u, "id", 0) or 0) for u in users_assignees]
            tg_map = await ns.resolve_recipients_tg_ids(user_ids=list(recipient_ids))
            for u in users_assignees:
                rid = int(getattr(u, "id", 0) or 0)
                if rid <= 0:
                    continue
                if int(tg_map.get(int(rid), 0) or 0) <= 0:
                    continue
                await ns.enqueue(
                    task_id=int(t.id),
                    recipient_user_id=int(rid),
                    type="created",
                    payload={
                        "task_id": int(t.id),
                        "actor_user_id": int(actor.id),
                        "actor_name": actor_name,
                    },
                    dedupe_key=f"created:{int(t.id)}",
                )
    except Exception:
        pass

    if photo:
        try:
            photo_key, photo_path = await _save_task_photo(photo=photo)
            t.photo_key = str(photo_key)
            t.photo_path = str(photo_path)
            t.photo_url = _task_photo_url_from_key(t.photo_key)
        except Exception:
            pass

    return {"id": int(t.id)}


@app.get("/api/tasks/{task_id}")
async def tasks_api_detail(task_id: int, request: Request, admin_id: int = Depends(require_authenticated_user), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    t = await _load_task_full(session, task_id)
    is_admin = int(admin_id) in settings.admin_ids
    is_manager = bool(actor.position == Position.MANAGER)

    _ensure_task_visible_to_actor(t=t, actor=actor, is_admin=bool(is_admin), is_manager=bool(is_manager))

    assignees = list(getattr(t, "assignees", None) or [])
    comments = list(getattr(t, "comments", None) or [])
    events = list(getattr(t, "events", None) or [])

    def _comment_view(c: TaskComment) -> dict:
        photos = list(getattr(c, "photos", None) or [])
        return {
            "id": int(c.id),
            "text": c.text,
            "created_at_str": format_moscow(getattr(c, "created_at", None), "%d.%m.%Y %H:%M"),
            "author": _user_short(getattr(c, "author_user")),
            "photos": [{"url": p.tg_file_id} for p in photos],
        }

    created_by = getattr(t, "created_by_user", None)
    created_by_str = ""
    created_by_view: dict | None = None
    if created_by is not None:
        created_by_str = (f"{(created_by.first_name or '').strip()} {(created_by.last_name or '').strip()}".strip() or f"#{created_by.id}")
        created_by_view = {
            "id": int(getattr(created_by, "id", 0) or 0),
            "name": created_by_str,
            "color": (getattr(created_by, "color", None) or None),
        }

    created_at_utc = getattr(t, "created_at", None)
    created_at_ts: int | None = None
    if created_at_utc is not None:
        try:
            created_at_ts = int(created_at_utc.timestamp())
        except Exception:
            created_at_ts = None

    return {
        "id": int(t.id),
        "title": t.title,
        "description": t.description,
        "photo_file_id": getattr(t, "photo_file_id", None),
        "tg_photo_file_id": getattr(t, "tg_photo_file_id", None) or getattr(t, "photo_file_id", None),
        "photo_path": getattr(t, "photo_path", None),
        "photo_url": getattr(t, "photo_url", None) or _to_public_url(getattr(t, "photo_path", None)),
        "priority": t.priority.value,
        "status": t.status.value,
        "due_at_str": format_moscow(getattr(t, "due_at", None), "%d.%m.%Y %H:%M") if getattr(t, "due_at", None) else "",
        "created_at_str": format_moscow(created_at_utc, "%d.%m.%Y %H:%M"),
        "created_at_ts": created_at_ts,
        "created_by_str": created_by_str,
        "created_by": created_by_view,
        "assignees": [_user_short(u) for u in assignees],
        "comments": [_comment_view(c) for c in sorted(comments, key=lambda x: x.created_at)],
        "permissions": _task_permissions(t=t, actor=actor, is_admin=is_admin, is_manager=is_manager),
        "events": [_event_view(e) for e in sorted(events, key=lambda x: x.created_at)],
    }


@app.get("/tasks/{task_id}/photo")
async def tasks_photo_proxy(
    task_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    t = await _load_task_full(session, int(task_id))
    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    _ensure_task_visible_to_actor(t=t, actor=actor, is_admin=bool(r.is_admin), is_manager=bool(r.is_manager))

    # Prefer already stored public photo
    existing = getattr(t, "photo_url", None) or _to_public_url(getattr(t, "photo_path", None))
    if existing:
        logger.info(
            "tasks_photo_proxy redirect",
            extra={"task_id": int(task_id), "url": str(existing)},
        )
        return RedirectResponse(url=str(existing), status_code=302)

    fid = getattr(t, "tg_photo_file_id", None) or getattr(t, "photo_file_id", None) or None
    if not fid:
        logger.info(
            "tasks_photo_proxy no photo sources",
            extra={
                "task_id": int(task_id),
                "photo_url": getattr(t, "photo_url", None),
                "photo_path": getattr(t, "photo_path", None),
                "tg_photo_file_id": getattr(t, "tg_photo_file_id", None),
                "photo_file_id": getattr(t, "photo_file_id", None),
            },
        )
        raise HTTPException(status_code=404)

    data, content_type = await _download_tg_file_bytes(file_id=str(fid))
    if not data:
        logger.info(
            "tasks_photo_proxy tg download returned empty",
            extra={
                "task_id": int(task_id),
                "file_id": str(fid),
                "photo_url": getattr(t, "photo_url", None),
                "photo_path": getattr(t, "photo_path", None),
                "tg_photo_file_id": getattr(t, "tg_photo_file_id", None),
                "photo_file_id": getattr(t, "photo_file_id", None),
            },
        )
        raise HTTPException(status_code=404)

    # Optional cache: persist into uploads and fill photo_path/photo_url so we don't hit Telegram next time
    try:
        name = f"{uuid4().hex}.jpg"
        path = UPLOADS_DIR / name
        path.write_bytes(data)
        t.photo_path = f"/crm/static/uploads/tasks/{name}"
        t.photo_url = _to_public_url(t.photo_path)
        await session.flush()
    except Exception:
        pass

    return StreamingResponse(iter([data]), media_type=(content_type or "image/jpeg"))


# Local/direct access aliases for deployments where '/crm' is part of the actual URL path
# (root_path does not add a URL prefix in FastAPI).
@app.get("/crm/tasks/{task_id}/photo")
async def tasks_photo_proxy_crm(
    task_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    return await tasks_photo_proxy(task_id=task_id, request=request, admin_id=admin_id, session=session)


@app.get("/api/tasks/{task_id}/photo")
async def tasks_api_photo_proxy(
    task_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    return await tasks_photo_proxy(task_id=task_id, request=request, admin_id=admin_id, session=session)


@app.get("/crm/api/tasks/{task_id}/photo")
async def tasks_api_photo_proxy_crm(
    task_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    return await tasks_photo_proxy(task_id=task_id, request=request, admin_id=admin_id, session=session)


@app.get("/crm/openapi.json")
async def openapi_json_crm() -> dict:
    return app.openapi()


@app.post("/api/tasks/{task_id}/comments")
async def tasks_api_add_comment(
    task_id: int,
    request: Request,
    text: str | None = Form(None),
    photos: list[UploadFile] | None = File(None),
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    t = await _load_task_full(session, task_id)
    # Visibility is not restricted; permissions control actions.
    urls = await _save_uploads(photos)
    actor_name = (f"{(actor.first_name or '').strip()} {(actor.last_name or '').strip()}".strip() or f"#{int(actor.id)}")
    await shared_add_task_comment(
        session=session,
        task_id=int(t.id),
        author_user_id=int(actor.id),
        author_name=str(actor_name),
        text=(text or None),
        photo_file_ids=[str(x) for x in (urls or []) if str(x).strip()],
        notify=True,
        notify_self=True,
        hard_send_tg=True,
    )

    # return refreshed detail view for convenience
    return await tasks_api_detail(task_id, request, admin_id, session)


@app.post("/api/tasks/{task_id}/photo")
async def tasks_api_set_photo(
    task_id: int,
    photo: UploadFile = File(...),
    x_bot_token: str | None = Header(None),
    session: AsyncSession = Depends(get_db),
):
    # Bot-only endpoint. Use BOT_TOKEN as a shared secret.
    if not x_bot_token or str(x_bot_token) != str(settings.BOT_TOKEN):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    t = await _load_task_full(session, int(task_id))
    photo_key, photo_path = await _save_task_photo(photo=photo)
    t.photo_key = str(photo_key)
    t.photo_path = str(photo_path)
    t.photo_url = _task_photo_url_from_key(t.photo_key)
    await session.flush()
    return {"photo_key": t.photo_key, "photo_url": t.photo_url, "photo_path": t.photo_path}


@app.post("/api/internal/tasks/upload-photo")
async def tasks_api_internal_upload_photo(
    photo: UploadFile = File(...),
    x_internal_token: str | None = Header(None),
):
    if not x_internal_token or str(x_internal_token) != str(getattr(settings, "INTERNAL_API_TOKEN", "") or ""):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    photo_key, photo_path = await _save_task_photo(photo=photo)
    return {
        "photo_key": str(photo_key),
        "photo_path": str(photo_path),
        "photo_url": _task_photo_url_from_key(str(photo_key)),
    }


@app.post("/api/tasks/{task_id}/status")
async def tasks_api_change_status(task_id: int, request: Request, admin_id: int = Depends(require_authenticated_user), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    body = await request.json()
    new_status = (body.get("status") or "").strip()
    comment = (body.get("comment") or "").strip()

    try:
        logger.info(
            "TASK_STATUS_CHANGE_REQUEST task_id=%s actor_user_id=%s new_status=%s comment_len=%s",
            int(task_id),
            int(actor.id),
            str(new_status),
            int(len(comment or "")),
        )
    except Exception:
        pass

    t = await _load_task_full(session, task_id)
    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)

    # Visibility is not restricted; permissions control actions.
    old_status = t.status.value if hasattr(t.status, "value") else str(t.status)

    perms = task_permissions(
        status=str(old_status),
        actor_user_id=int(actor.id),
        created_by_user_id=int(getattr(t, "created_by_user_id", 0) or 0) or None,
        assignee_user_ids=[int(u.id) for u in list(getattr(t, "assignees", None) or [])],
        started_by_user_id=(int(getattr(t, "started_by_user_id")) if getattr(t, "started_by_user_id", None) is not None else None),
        is_admin=is_admin,
        is_manager=is_manager,
    )

    ok, code, msg = validate_status_transition(
        from_status=str(old_status),
        to_status=str(new_status),
        perms=perms,
        comment=comment,
    )
    if not ok:
        raise HTTPException(status_code=int(code), detail=str(msg or "Ошибка"))

    # Route explicit 'return to rework' through shared service to guarantee notification + comment.
    if str(old_status) == TaskStatus.REVIEW.value and str(new_status) == TaskStatus.IN_PROGRESS.value and (comment or "").strip() and bool(perms.send_back):
        actor_name = (f"{(actor.first_name or '').strip()} {(actor.last_name or '').strip()}".strip() or f"#{int(actor.id)}")
        await shared_return_task_to_rework(
            session=session,
            task_id=int(t.id),
            actor_user_id=int(actor.id),
            actor_name=str(actor_name),
            comment=str(comment),
            hard_send_tg=True,
        )
        return {"ok": True, "id": int(t.id), "status": TaskStatus.IN_PROGRESS.value}

    if new_status == TaskStatus.IN_PROGRESS.value:
        t.status = TaskStatus.IN_PROGRESS
        assignees = list(getattr(t, "assignees", None) or [])
        if len(assignees) == 0:
            t.started_by_user_id = int(actor.id)
            t.started_at = utc_now()
        if old_status == TaskStatus.REVIEW.value and perms.send_back:
            session.add(TaskComment(task_id=int(t.id), author_user_id=int(actor.id), text=comment))
    elif new_status == TaskStatus.REVIEW.value:
        t.status = TaskStatus.REVIEW
        t.completed_by_user_id = int(actor.id)
        t.completed_at = utc_now()
    elif new_status == TaskStatus.DONE.value:
        t.status = TaskStatus.DONE

    new_status_val = t.status.value if hasattr(t.status, "value") else str(t.status)
    ev = TaskEvent(
        task_id=int(t.id),
        actor_user_id=int(actor.id),
        type=TaskEventType.STATUS_CHANGED,
        payload={"from": old_status, "to": new_status_val, "comment": comment or None},
    )
    session.add(ev)

    await session.flush()

    # Shared TG notifications with strict rules.
    try:
        actor_name = (f"{(actor.first_name or '').strip()} {(actor.last_name or '').strip()}".strip() or f"#{int(actor.id)}")
        if str(new_status_val) == TaskStatus.IN_PROGRESS.value and str(old_status) != TaskStatus.IN_PROGRESS.value:
            await enqueue_task_taken_in_work_notifications(
                session=session,
                task=t,
                actor_user_id=int(actor.id),
                actor_name=str(actor_name),
                event_id=int(getattr(ev, "id", 0) or 0),
            )
        if str(new_status_val) == TaskStatus.REVIEW.value and str(old_status) != TaskStatus.REVIEW.value:
            await enqueue_task_sent_to_review_notifications(
                session=session,
                task=t,
                actor_user_id=int(actor.id),
                actor_name=str(actor_name),
                event_id=int(getattr(ev, "id", 0) or 0),
            )

        # Keep legacy status_changed notifications for other transitions.
        if not (
            (str(new_status_val) == TaskStatus.IN_PROGRESS.value and str(old_status) != TaskStatus.IN_PROGRESS.value)
            or (str(new_status_val) == TaskStatus.REVIEW.value and str(old_status) != TaskStatus.REVIEW.value)
        ):
            await enqueue_task_status_changed_notifications(
                session=session,
                task=t,
                actor_user_id=int(actor.id),
                actor_name=str(actor_name),
                from_status=str(old_status),
                to_status=str(new_status_val),
                comment=(comment or None),
                event_id=int(getattr(ev, "id", 0) or 0),
            )
    except Exception:
        try:
            logger.exception("TASK_NOTIFY_FAILED type=shared_status_rules task_id=%s", int(getattr(t, "id", task_id) or task_id))
        except Exception:
            pass

    try:
        logger.info(
            "TASK_STATUS_CHANGED task_id=%s old=%s new=%s actor_user_id=%s",
            int(t.id),
            str(old_status),
            str(new_status_val),
            int(actor.id),
        )
    except Exception:
        pass

    return {"ok": True, "id": int(t.id), "status": new_status_val}


@app.post("/crm/api/tasks/{task_id}/remind")
async def tasks_api_remind_crm(
    task_id: int,
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)
    if not (is_admin or is_manager):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    t = await _load_task_full(session, int(task_id))

    import time as _time

    now = float(_time.time())
    last = float(_TASK_REMIND_LAST_TS.get(int(task_id), 0.0) or 0.0)
    if last and (now - last) < 60.0:
        raise HTTPException(status_code=429, detail="Напоминание уже отправлялось недавно. Подождите минуту.")

    assignees = list(getattr(t, "assignees", None) or [])
    recipient_user_id: int | None = None
    if assignees:
        recipient_user_id = int(getattr(assignees[0], "id", 0) or 0) or None
    if recipient_user_id is None:
        sb = getattr(t, "started_by_user_id", None)
        recipient_user_id = int(sb) if sb is not None else None

    if recipient_user_id is None:
        raise HTTPException(status_code=400, detail="У задачи не назначен исполнитель")

    u = await session.get(User, int(recipient_user_id))
    tg_id = int(getattr(u, "tg_id", 0) or 0) if u is not None else 0
    if tg_id <= 0:
        raise HTTPException(status_code=400, detail="У исполнителя не привязан Telegram")

    # Enqueue reminder notification; sending is done by bot worker after commit.
    ns = TaskNotificationService(session)
    actor_name = (f"{(actor.first_name or '').strip()} {(actor.last_name or '').strip()}".strip() or f"#{int(actor.id)}")
    await ns.enqueue(
        task_id=int(t.id),
        recipient_user_id=int(recipient_user_id),
        type="remind",
        payload={
            "task_id": int(t.id),
            "actor_user_id": int(actor.id),
            "actor_name": actor_name,
        },
        dedupe_key=f"remind:{int(t.id)}:{int(now)}",
    )

    _TASK_REMIND_LAST_TS[int(task_id)] = float(now)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/remind")
async def tasks_api_remind(task_id: int, request: Request, admin_id: int = Depends(require_authenticated_user), session: AsyncSession = Depends(get_db)):
    # Alias for frontend environments that call /api instead of /crm/api.
    return await tasks_api_remind_crm(task_id=int(task_id), request=request, admin_id=admin_id, session=session)


@app.post("/api/tasks/{task_id}/archive")
async def tasks_api_archive(task_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    t = await _load_task_full(session, task_id)
    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)
    perms = task_permissions(
        status=str(t.status.value if hasattr(t.status, "value") else str(t.status)),
        actor_user_id=int(actor.id),
        created_by_user_id=int(getattr(t, "created_by_user_id", 0) or 0) or None,
        assignee_user_ids=[int(u.id) for u in list(getattr(t, "assignees", None) or [])],
        started_by_user_id=(int(getattr(t, "started_by_user_id")) if getattr(t, "started_by_user_id", None) is not None else None),
        is_admin=is_admin,
        is_manager=is_manager,
    )
    if not perms.archive:
        raise HTTPException(status_code=403, detail="Недостаточно прав для архивирования")
    t.status = TaskStatus.ARCHIVED
    t.archived_at = utc_now()
    session.add(TaskEvent(task_id=int(t.id), actor_user_id=int(actor.id), type=TaskEventType.ARCHIVED, payload=None))
    await session.flush()
    return {"ok": True}


@app.post("/crm/api/tasks/{task_id}/archive")
async def tasks_api_archive_crm(task_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    return await tasks_api_archive(task_id=int(task_id), request=request, admin_id=admin_id, session=session)


@app.post("/api/tasks/{task_id}/unarchive")
async def tasks_api_unarchive(task_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)
    t = await _load_task_full(session, task_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)
    st = t.status.value if hasattr(t.status, "value") else str(t.status)

    perms = task_permissions(
        status=str(st),
        actor_user_id=int(actor.id),
        created_by_user_id=int(getattr(t, "created_by_user_id", 0) or 0) or None,
        assignee_user_ids=[int(u.id) for u in list(getattr(t, "assignees", None) or [])],
        started_by_user_id=(int(getattr(t, "started_by_user_id")) if getattr(t, "started_by_user_id", None) is not None else None),
        is_admin=is_admin,
        is_manager=is_manager,
    )
    if not perms.unarchive:
        raise HTTPException(status_code=403, detail="Недостаточно прав для разархивирования")

    t.status = TaskStatus.DONE
    t.archived_at = None
    session.add(TaskEvent(task_id=int(t.id), actor_user_id=int(actor.id), type=TaskEventType.UNARCHIVED, payload=None))
    await session.flush()
    return {"ok": True}


@app.post("/crm/api/tasks/{task_id}/unarchive")
async def tasks_api_unarchive_crm(task_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    return await tasks_api_unarchive(task_id=int(task_id), request=request, admin_id=admin_id, session=session)


# ========== Materials Admin ==========

@app.get("/materials/types", response_class=HTMLResponse)
async def materials_types_list(request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(select(MaterialType).order_by(MaterialType.name))
    types = res.scalars().all()
    return templates.TemplateResponse("materials/types.html", {"request": request, "types": types})


@app.post("/materials/types/create")
async def materials_types_create(request: Request, name: str = Form(...), admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    mt = MaterialType(name=name)
    session.add(mt)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return templates.TemplateResponse(
            "materials/partials/types_create_modal.html",
            {"request": request, "name": name, "errors": {"name": "Такое название уже существует"}},
            status_code=400,
        )
    res = await session.execute(select(MaterialType).order_by(MaterialType.name))
    types = res.scalars().all()
    return templates.TemplateResponse(
        "materials/partials/types_table.html",
        {"request": request, "types": types, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


@app.post("/materials/types/{type_id}/update")
async def materials_types_update(type_id: int, request: Request, name: str = Form(...), admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(select(MaterialType).where(MaterialType.id == type_id))
    mt = res.scalar_one_or_none()
    if not mt:
        raise HTTPException(404)
    mt.name = name
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return templates.TemplateResponse(
            "materials/partials/types_edit_modal.html",
            {"request": request, "t": mt, "name": name, "errors": {"name": "Такое название уже существует"}},
            status_code=400,
        )
    res2 = await session.execute(select(MaterialType).order_by(MaterialType.name))
    types = res2.scalars().all()
    return templates.TemplateResponse(
        "materials/partials/types_table.html",
        {"request": request, "types": types, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


@app.post("/materials/types/{type_id}/delete")
async def materials_types_delete(type_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    # Cascade delete: consumptions/supplies for materials of this type, then materials, then the type
    from sqlalchemy import select as _select
    # collect material ids
    res_mats = await session.execute(_select(Material.id).where(Material.material_type_id == type_id))
    mat_ids = [mid for (mid,) in res_mats.all()]
    if mat_ids:
        await session.execute(delete(MaterialConsumption).where(MaterialConsumption.material_id.in_(mat_ids)))
        await session.execute(delete(MaterialSupply).where(MaterialSupply.material_id.in_(mat_ids)))
        await session.execute(delete(Material).where(Material.id.in_(mat_ids)))
    await session.execute(delete(MaterialType).where(MaterialType.id == type_id))
    res = await session.execute(select(MaterialType).order_by(MaterialType.name))
    types = res.scalars().all()
    return templates.TemplateResponse(
        "materials/partials/types_table.html",
        {"request": request, "types": types, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


# Modal endpoints for MaterialType CRUD
@app.get("/materials/types/modal/create", response_class=HTMLResponse)
async def materials_types_modal_create(request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    return templates.TemplateResponse("materials/partials/types_create_modal.html", {"request": request})


@app.get("/materials/types/{type_id}/modal/edit", response_class=HTMLResponse)
async def materials_types_modal_edit(type_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(select(MaterialType).where(MaterialType.id == type_id))
    mt = res.scalar_one_or_none()
    if not mt:
        raise HTTPException(404)
    return templates.TemplateResponse("materials/partials/types_edit_modal.html", {"request": request, "t": mt})


@app.get("/materials/types/{type_id}/modal/delete", response_class=HTMLResponse)
async def materials_types_modal_delete(type_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    from sqlalchemy import func
    mats = (await session.execute(select(func.count()).select_from(Material).where(Material.material_type_id == type_id))).scalar_one()
    cons = (await session.execute(select(func.count()).select_from(MaterialConsumption).join(Material, Material.id == MaterialConsumption.material_id).where(Material.material_type_id == type_id))).scalar_one()
    sups = (await session.execute(select(func.count()).select_from(MaterialSupply).join(Material, Material.id == MaterialSupply.material_id).where(Material.material_type_id == type_id))).scalar_one()
    return templates.TemplateResponse("materials/partials/types_delete_modal.html", {"request": request, "type_id": type_id, "mats": mats, "cons": cons, "sups": sups})


@app.get("/materials", response_class=HTMLResponse)
async def materials_list(request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(
        select(Material)
        .options(selectinload(Material.material_type), selectinload(Material.allowed_masters))
        .order_by(Material.name)
    )
    materials = res.scalars().all()
    res_t = await session.execute(select(MaterialType).order_by(MaterialType.name))
    types = res_t.scalars().all()
    return templates.TemplateResponse("materials/materials.html", {"request": request, "materials": materials, "types": types})


@app.get("/stocks", response_class=HTMLResponse, name="stocks_dashboard")
async def stocks_dashboard(request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    from datetime import date as _date, timedelta as _timedelta

    def _parse_date(val: str | None) -> _date | None:
        if not val:
            return None
        try:
            return datetime.strptime(val, "%Y-%m-%d").date()
        except Exception:
            return None

    today = _date.today()
    date_to = _parse_date(request.query_params.get("date_to")) or today
    date_from = _parse_date(request.query_params.get("date_from")) or (date_to - _timedelta(days=29))
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    chart_rows = await build_chart_rows(session, date_from=date_from, date_to=date_to)
    history_rows = await build_history_rows(session)
    stock_rows = await build_stock_rows(session)
    pie_rows = await build_pie_data(session)

    chart_json = json.dumps(
        [
            {
                "material_name": r.material_name,
                "total_in": str(r.total_in),
                "total_out": str(r.total_out),
            }
            for r in chart_rows
        ],
        ensure_ascii=False,
    )

    pie_json = json.dumps(pie_rows, ensure_ascii=False)

    history = [
        {
            "ts_str": format_dt_ru(r.ts),
            "actor_name": r.actor_name,
            "actor_color": (str(getattr(r, "actor_color", "") or "") or None),
            "actor_tg_id": r.actor_tg_id,
            "kind": r.kind,
            "amount": format_number(r.amount, max_decimals=3, decimal_sep=".", thousands_sep=" "),
            "material_name": r.material_name,
        }
        for r in history_rows
    ]

    stock_rows_view = []
    for r in stock_rows:
        stock_rows_view.append(
            {
                "material_name": r.material_name,
                "current_stock_str": str(r.current_stock),
                "avg_daily_out_str": "—" if r.avg_daily_out is None else str(r.avg_daily_out.quantize(Decimal('0.001'))),
                "forecast_days": r.forecast_days,
                "is_low": r.is_low,
            }
        )

    return templates.TemplateResponse(
        "stocks/dashboard.html",
        {
            "request": request,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "chart_json": chart_json,
            "pie_json": pie_json,
            "history": history,
            "stock_rows": stock_rows_view,
        },
    )


@app.get("/api/stocks/cast-by-masters")
@app.get("/crm/api/stocks/cast-by-masters")
async def crm_api_stocks_cast_by_masters(
    request: Request,
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    from datetime import date as _date, timedelta as _timedelta

    def _parse_date(val: str | None) -> _date | None:
        if not val:
            return None
        try:
            return datetime.strptime(val, "%Y-%m-%d").date()
        except Exception:
            return None

    today = _date.today()
    date_to = _parse_date(request.query_params.get("date_to")) or today
    date_from = _parse_date(request.query_params.get("date_from")) or (date_to - _timedelta(days=29))
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    try:
        _logger.info(
            "[stocks-dashboard] cast-by-masters date_from=%s date_to=%s",
            str(date_from),
            str(date_to),
        )
        # Diagnostic: how many consumptions exist in this period by created_at (UTC).
        from datetime import time as _time, timezone as _timezone

        dt_from = datetime.combine(date_from, _time.min).replace(tzinfo=MOSCOW_TZ).astimezone(_timezone.utc)
        dt_to = datetime.combine(date_to, _time.max).replace(tzinfo=MOSCOW_TZ).astimezone(_timezone.utc)
        _logger.info(
            "[stocks-dashboard] cast-by-masters dt_from_utc=%s dt_to_utc=%s",
            str(dt_from),
            str(dt_to),
        )
        cnt = (
            await session.execute(
                select(func.count())
                .select_from(MaterialConsumption)
                .where(MaterialConsumption.created_at >= dt_from)
                .where(MaterialConsumption.created_at <= dt_to)
            )
        ).scalar_one()
        _logger.info("[stocks-dashboard] cast-by-masters consumptions_in_period=%s", str(cnt))

        cnt_users = (
            await session.execute(
                select(func.count(func.distinct(MaterialConsumption.employee_id)))
                .select_from(MaterialConsumption)
                .where(MaterialConsumption.created_at >= dt_from)
                .where(MaterialConsumption.created_at <= dt_to)
            )
        ).scalar_one()
        _logger.info("[stocks-dashboard] cast-by-masters distinct_employees_in_period=%s", str(cnt_users))
    except Exception:
        pass

    rows = await build_cast_by_masters(session, date_from=date_from, date_to=date_to)
    try:
        _logger.info("[stocks-dashboard] cast-by-masters rows=%s", str(len(rows)))
        for r in rows[:2]:
            _logger.info(
                "[stocks-dashboard] cast-by-masters sample user_id=%s name=%s total=%s",
                str(getattr(r, "user_id", "")),
                str(getattr(r, "name", "")),
                str(getattr(r, "total", "")),
            )
    except Exception:
        pass
    return {
        "items": [
            {"user_id": int(r.user_id), "name": str(r.name), "color": str(r.color), "total": str(r.total)}
            for r in rows
        ]
    }


# Modal endpoints for Materials CRUD
@app.get("/materials/modal/create", response_class=HTMLResponse)
async def materials_modal_create(request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res_t = await session.execute(select(MaterialType).order_by(MaterialType.name))
    types = res_t.scalars().all()
    masters = (
        (
            await session.execute(
                select(User)
                .where(User.is_deleted == False)
                .where(User.status == UserStatus.APPROVED)
                .where(User.position == Position.MASTER)
                .order_by(User.first_name, User.last_name)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        "materials/partials/materials_create_modal.html",
        {"request": request, "types": types, "masters": masters, "selected_master_ids": []},
    )


@app.get("/materials/{material_id}/modal/edit", response_class=HTMLResponse)
async def materials_modal_edit(material_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(
        select(Material)
        .where(Material.id == material_id)
        .options(selectinload(Material.allowed_masters))
    )
    m = res.scalar_one_or_none()
    if not m:
        raise HTTPException(404)
    res_t = await session.execute(select(MaterialType).order_by(MaterialType.name))
    types = res_t.scalars().all()
    masters = (
        (
            await session.execute(
                select(User)
                .where(User.is_deleted == False)
                .where(User.status == UserStatus.APPROVED)
                .where(User.position == Position.MASTER)
                .order_by(User.first_name, User.last_name)
            )
        )
        .scalars()
        .all()
    )
    selected_master_ids = [int(u.id) for u in (getattr(m, "allowed_masters", None) or [])]
    return templates.TemplateResponse(
        "materials/partials/materials_edit_modal.html",
        {"request": request, "m": m, "types": types, "masters": masters, "selected_master_ids": selected_master_ids},
    )


@app.get("/materials/{material_id}/modal/delete", response_class=HTMLResponse)
async def materials_modal_delete(material_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    from sqlalchemy import func
    cons = (await session.execute(select(func.count()).select_from(MaterialConsumption).where(MaterialConsumption.material_id == material_id))).scalar_one()
    sups = (await session.execute(select(func.count()).select_from(MaterialSupply).where(MaterialSupply.material_id == material_id))).scalar_one()
    return templates.TemplateResponse("materials/partials/materials_delete_modal.html", {"request": request, "material_id": material_id, "cons": cons, "sups": sups})


@app.get("/materials/{material_id}/modal/set-remains", response_class=HTMLResponse, name="materials_modal_set_remains")
async def materials_modal_set_remains(material_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(select(Material).where(Material.id == material_id).options(selectinload(Material.material_type)))
    m = res.scalar_one_or_none()
    if not m:
        raise HTTPException(404)
    return templates.TemplateResponse("materials/partials/materials_set_remains_modal.html", {"request": request, "m": m})


@app.post("/materials/{material_id}/set-remains", name="materials_set_remains")
async def materials_set_remains(
    material_id: int,
    request: Request,
    new_remains: str = Form(...),
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(select(Material).where(Material.id == material_id).options(selectinload(Material.material_type), selectinload(Material.allowed_masters)))
    m = res.scalar_one_or_none()
    if not m:
        raise HTTPException(404)

    try:
        from decimal import Decimal

        v = Decimal(str(new_remains).strip())
    except Exception:
        return templates.TemplateResponse(
            "materials/partials/materials_set_remains_modal.html",
            {"request": request, "m": m, "new_remains": new_remains, "errors": {"new_remains": "Некорректное число"}},
            status_code=400,
        )

    try:
        result = await set_material_remains(session=session, material_id=int(material_id), new_remains=v)
    except ValueError as e:
        code = str(e)
        msg = "Ошибка"
        if "negative" in code:
            msg = "Значение не может быть отрицательным"
        elif "invalid" in code:
            msg = "Некорректное число"
        elif "not_found" in code:
            raise HTTPException(404)
        return templates.TemplateResponse(
            "materials/partials/materials_set_remains_modal.html",
            {"request": request, "m": m, "new_remains": str(v), "errors": {"new_remains": msg}},
            status_code=400,
        )

    try:
        arepo = AdminLogRepo(session)
        await arepo.log(
            admin_tg_id=int(admin_id),
            user_id=None,
            action=AdminActionType.EDIT,
            payload={
                "entity": "material",
                "material_id": int(material_id),
                "action": "set_remains",
                "old": str(result.old_remains),
                "new": str(result.new_remains),
                "delta": str(result.delta),
            },
        )
    except Exception:
        pass

    # Optional: notify reports chat (best-effort) so history reflects correction.
    try:
        from decimal import Decimal

        res_u = await session.execute(select(User).where(User.tg_id == admin_id).where(User.is_deleted == False))
        admin_user = res_u.scalar_one_or_none()
        full_name = ""
        if admin_user is not None:
            full_name = str(getattr(admin_user, "full_name", "") or "").strip()
            if not full_name:
                first = str(getattr(admin_user, "first_name", "") or "").strip()
                last = str(getattr(admin_user, "last_name", "") or "").strip()
                full_name = (first + " " + last).strip()
        if not full_name:
            full_name = f"Staff {admin_id}"
        actor = StockEventActor(name=full_name, tg_id=admin_id)
        happened_at = None
        stock_after = Decimal(getattr(m, "current_stock", 0) or 0)
        add_after_commit_callback(
            session,
            lambda: notify_reports_chat_about_stock_event(
                kind="adjustment",
                material_name=str(getattr(m, "name", "") or "—"),
                amount=Decimal(result.delta),
                unit=str(getattr(m, "unit", "") or ""),
                actor=actor,
                happened_at=happened_at,
                stock_after=stock_after,
            ),
        )
    except Exception:
        pass

    res2 = await session.execute(
        select(Material)
        .options(selectinload(Material.material_type), selectinload(Material.allowed_masters))
        .order_by(Material.name)
    )
    materials = res2.scalars().all()
    res_t = await session.execute(select(MaterialType).order_by(MaterialType.name))
    types = res_t.scalars().all()
    return templates.TemplateResponse(
        "materials/partials/materials_table.html",
        {"request": request, "materials": materials, "types": types, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


@app.post("/materials/create")
async def materials_create(
    request: Request,
    name: str = Form(...),
    short_name: str | None = Form(None),
    unit: str = Form("кг"),
    material_type_id: int = Form(...),
    is_active: bool = Form(True),
    master_ids: list[int] = Form([]),
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    m = Material(
        name=name,
        short_name=short_name or None,
        unit=unit or "кг",
        material_type_id=material_type_id,
        is_active=is_active,
    )
    session.add(m)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        res_t = await session.execute(select(MaterialType).order_by(MaterialType.name))
        types = res_t.scalars().all()
        masters = (
            (
                await session.execute(
                    select(User)
                    .where(User.is_deleted == False)
                    .where(User.status == UserStatus.APPROVED)
                    .where(User.position == Position.MASTER)
                    .order_by(User.first_name, User.last_name)
                )
            )
            .scalars()
            .all()
        )
        return templates.TemplateResponse(
            "materials/partials/materials_create_modal.html",
            {
                "request": request,
                "types": types,
                "masters": masters,
                "selected_master_ids": master_ids,
                "name": name,
                "short_name": short_name,
                "unit": unit,
                "material_type_id": material_type_id,
                "is_active": is_active,
                "errors": {"name": "Материал с таким названием уже существует"},
            },
            status_code=400,
        )

    if master_ids:
        res_m = await session.execute(
            select(User)
            .where(User.id.in_(master_ids))
            .where(User.is_deleted == False)
            .where(User.status == UserStatus.APPROVED)
            .where(User.position == Position.MASTER)
        )
        masters = res_m.scalars().all()
        if len(masters) != len(set(master_ids)):
            await session.rollback()
            res_t = await session.execute(select(MaterialType).order_by(MaterialType.name))
            types = res_t.scalars().all()
            all_masters = (
                (
                    await session.execute(
                        select(User)
                        .where(User.is_deleted == False)
                        .where(User.status == UserStatus.APPROVED)
                        .where(User.position == Position.MASTER)
                        .order_by(User.first_name, User.last_name)
                    )
                )
                .scalars()
                .all()
            )
            return templates.TemplateResponse(
                "materials/partials/materials_create_modal.html",
                {
                    "request": request,
                    "types": types,
                    "masters": all_masters,
                    "selected_master_ids": master_ids,
                    "name": name,
                    "short_name": short_name,
                    "unit": unit,
                    "material_type_id": material_type_id,
                    "is_active": is_active,
                    "errors": {"masters": "Некорректный список мастеров"},
                },
                status_code=400,
            )
        m.allowed_masters = list(masters)
        await session.flush()

    res = await session.execute(
        select(Material)
        .options(selectinload(Material.material_type), selectinload(Material.allowed_masters))
        .order_by(Material.name)
    )
    materials = res.scalars().all()
    res_t = await session.execute(select(MaterialType).order_by(MaterialType.name))
    types = res_t.scalars().all()
    return templates.TemplateResponse(
        "materials/partials/materials_table.html",
        {"request": request, "materials": materials, "types": types, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


@app.post("/materials/{material_id}/update")
async def materials_update(
    material_id: int,
    request: Request,
    name: str = Form(...),
    short_name: str | None = Form(None),
    unit: str = Form("кг"),
    material_type_id: int = Form(...),
    is_active: bool = Form(True),
    master_ids: list[int] = Form([]),
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(
        select(Material)
        .where(Material.id == material_id)
        .options(selectinload(Material.allowed_masters))
    )
    m = res.scalar_one_or_none()
    if not m:
        raise HTTPException(404)
    m.name = name
    m.short_name = short_name or None
    m.unit = unit or "кг"
    m.material_type_id = material_type_id
    m.is_active = is_active
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        res_m = await session.execute(
            select(Material)
            .where(Material.id == material_id)
            .options(selectinload(Material.allowed_masters))
        )
        m2 = res_m.scalar_one_or_none()
        if not m2:
            raise HTTPException(404)
        res_t = await session.execute(select(MaterialType).order_by(MaterialType.name))
        types = res_t.scalars().all()
        masters = (
            (
                await session.execute(
                    select(User)
                    .where(User.is_deleted == False)
                    .where(User.status == UserStatus.APPROVED)
                    .where(User.position == Position.MASTER)
                    .order_by(User.first_name, User.last_name)
                )
            )
            .scalars()
            .all()
        )
        return templates.TemplateResponse(
            "materials/partials/materials_edit_modal.html",
            {
                "request": request,
                "m": m2,
                "types": types,
                "masters": masters,
                "selected_master_ids": master_ids,
                "name": name,
                "short_name": short_name,
                "unit": unit,
                "material_type_id": material_type_id,
                "is_active": is_active,
                "errors": {"name": "Материал с таким названием уже существует"},
            },
            status_code=400,
        )

    if master_ids:
        res_m = await session.execute(
            select(User)
            .where(User.id.in_(master_ids))
            .where(User.is_deleted == False)
            .where(User.status == UserStatus.APPROVED)
            .where(User.position == Position.MASTER)
        )
        masters = res_m.scalars().all()
        if len(masters) != len(set(master_ids)):
            await session.rollback()
            res_m2 = await session.execute(
                select(Material)
                .where(Material.id == material_id)
                .options(selectinload(Material.allowed_masters))
            )
            m2 = res_m2.scalar_one_or_none()
            if not m2:
                raise HTTPException(404)
            res_t = await session.execute(select(MaterialType).order_by(MaterialType.name))
            types = res_t.scalars().all()
            all_masters = (
                (
                    await session.execute(
                        select(User)
                        .where(User.is_deleted == False)
                        .where(User.status == UserStatus.APPROVED)
                        .where(User.position == Position.MASTER)
                        .order_by(User.first_name, User.last_name)
                    )
                )
                .scalars()
                .all()
            )
            return templates.TemplateResponse(
                "materials/partials/materials_edit_modal.html",
                {
                    "request": request,
                    "m": m2,
                    "types": types,
                    "masters": all_masters,
                    "selected_master_ids": master_ids,
                    "name": name,
                    "short_name": short_name,
                    "unit": unit,
                    "material_type_id": material_type_id,
                    "is_active": is_active,
                    "errors": {"masters": "Некорректный список мастеров"},
                },
                status_code=400,
            )
        m.allowed_masters = list(masters)
    else:
        m.allowed_masters = []
    await session.flush()
    res2 = await session.execute(
        select(Material)
        .options(selectinload(Material.material_type), selectinload(Material.allowed_masters))
        .order_by(Material.name)
    )
    materials = res2.scalars().all()
    res_t = await session.execute(select(MaterialType).order_by(MaterialType.name))
    types = res_t.scalars().all()
    return templates.TemplateResponse(
        "materials/partials/materials_table.html",
        {"request": request, "materials": materials, "types": types, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


@app.post("/materials/{material_id}/delete")
async def materials_delete(material_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    # Cascade delete related records first
    await session.execute(delete(MaterialConsumption).where(MaterialConsumption.material_id == material_id))
    await session.execute(delete(MaterialSupply).where(MaterialSupply.material_id == material_id))
    await session.execute(delete(Material).where(Material.id == material_id))
    res = await session.execute(
        select(Material)
        .options(selectinload(Material.material_type), selectinload(Material.allowed_masters))
        .order_by(Material.name)
    )
    materials = res.scalars().all()
    res_t = await session.execute(select(MaterialType).order_by(MaterialType.name))
    types = res_t.scalars().all()
    return templates.TemplateResponse(
        "materials/partials/materials_table.html",
        {"request": request, "materials": materials, "types": types, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


@app.get("/materials/consumptions", response_class=HTMLResponse)
async def consumptions_list(request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(
        select(MaterialConsumption)
        .options(selectinload(MaterialConsumption.material), selectinload(MaterialConsumption.employee))
        .order_by(MaterialConsumption.date.desc(), MaterialConsumption.id.desc())
    )
    items = res.scalars().all()
    res_m = await session.execute(select(Material).where(Material.is_active == True).order_by(Material.name))
    materials = res_m.scalars().all()
    res_u = await session.execute(select(User).where(User.is_deleted == False).order_by(User.first_name, User.last_name))
    users = res_u.scalars().all()
    return templates.TemplateResponse("materials/consumptions.html", {"request": request, "items": items, "materials": materials, "users": users})


@app.get("/materials/consumptions/modal/create", response_class=HTMLResponse)
async def consumptions_modal_create(request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res_m = await session.execute(select(Material).where(Material.is_active == True).order_by(Material.name))
    materials = res_m.scalars().all()
    res_u = await session.execute(select(User).where(User.is_deleted == False).order_by(User.first_name, User.last_name))
    users = res_u.scalars().all()
    from datetime import date as _date
    today = _date.today()
    return templates.TemplateResponse("materials/partials/consumptions_create_modal.html", {"request": request, "materials": materials, "users": users, "today": today})


@app.get("/materials/consumptions/{item_id}/modal/delete", response_class=HTMLResponse)
async def consumptions_modal_delete(item_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    return templates.TemplateResponse("materials/partials/consumptions_delete_modal.html", {"request": request, "item_id": item_id})


@app.get("/materials/consumptions/{item_id}/modal/edit", response_class=HTMLResponse)
async def consumptions_modal_edit(item_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(select(MaterialConsumption).where(MaterialConsumption.id == item_id))
    rec = res.scalar_one_or_none()
    if not rec:
        raise HTTPException(404)
    res_m = await session.execute(select(Material).where(Material.is_active == True).order_by(Material.name))
    materials = res_m.scalars().all()
    res_u = await session.execute(select(User).where(User.is_deleted == False).order_by(User.first_name, User.last_name))
    users = res_u.scalars().all()
    return templates.TemplateResponse("materials/partials/consumptions_edit_modal.html", {"request": request, "rec": rec, "materials": materials, "users": users})


@app.post("/materials/consumptions/create")
async def consumptions_create(
    request: Request,
    material_id: int = Form(...),
    employee_id: int = Form(...),
    amount: Decimal = Form(...),
    date: str = Form(...),
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    from datetime import datetime as dt
    d = dt.strptime(date, "%Y-%m-%d").date()
    # validate amount > 0
    try:
        if Decimal(amount) <= 0:
            raise HTTPException(400, detail="amount must be > 0")
    except Exception:
        raise HTTPException(400, detail="invalid amount")
    rec = MaterialConsumption(material_id=material_id, employee_id=employee_id, amount=amount, date=d)
    session.add(rec)
    await session.flush()
    await update_stock_on_new_consumption(session, rec)

    # Notify reports chat after successful commit (no duplicates)
    res_m = await session.execute(select(Material).where(Material.id == material_id))
    mat = res_m.scalar_one_or_none()
    material_title = mat.name if mat else "—"
    # Expense notification should show a readable material label without bracketed codes
    if (not material_title) and mat and getattr(mat, "short_name", None):
        material_title = str(getattr(mat, "short_name") or "").strip() or material_title

    res_u = await session.execute(select(User).where(User.tg_id == admin_id).where(User.is_deleted == False))
    admin_user = res_u.scalar_one_or_none()
    full_name = ""
    if admin_user is not None:
        full_name = str(getattr(admin_user, "full_name", "") or "").strip()
        if not full_name:
            first = str(getattr(admin_user, "first_name", "") or "").strip()
            last = str(getattr(admin_user, "last_name", "") or "").strip()
            full_name = (first + " " + last).strip()
        if not full_name:
            full_name = str(getattr(admin_user, "username", "") or "").strip()
    if not full_name:
        full_name = f"Staff {admin_id}"
    actor = StockEventActor(name=full_name, tg_id=admin_id)
    stock_after = Decimal(mat.current_stock) if mat else None
    happened_at = getattr(rec, "created_at", None)
    add_after_commit_callback(
        session,
        lambda: notify_reports_chat_about_stock_event(
            kind="consumption",
            material_name=material_title,
            amount=Decimal(rec.amount),
            unit=(mat.unit if mat else ""),
            actor=actor,
            happened_at=happened_at,
            stock_after=stock_after,
        ),
    )
    # return updated table partial
    res = await session.execute(
        select(MaterialConsumption)
        .options(selectinload(MaterialConsumption.material), selectinload(MaterialConsumption.employee))
        .order_by(MaterialConsumption.date.desc(), MaterialConsumption.id.desc())
    )
    items = res.scalars().all()
    return templates.TemplateResponse(
        "materials/partials/consumptions_table.html",
        {"request": request, "items": items, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


@app.post("/materials/consumptions/{item_id}/delete")
async def consumptions_delete(item_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    await session.execute(delete(MaterialConsumption).where(MaterialConsumption.id == item_id))
    res = await session.execute(
        select(MaterialConsumption)
        .options(selectinload(MaterialConsumption.material), selectinload(MaterialConsumption.employee))
        .order_by(MaterialConsumption.date.desc(), MaterialConsumption.id.desc())
    )
    items = res.scalars().all()
    return templates.TemplateResponse(
        "materials/partials/consumptions_table.html",
        {"request": request, "items": items, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


@app.post("/materials/consumptions/{item_id}/update")
async def consumptions_update(
    item_id: int,
    request: Request,
    material_id: int = Form(...),
    employee_id: int = Form(...),
    amount: Decimal = Form(...),
    date: str = Form(...),
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    from datetime import datetime as dt
    d = dt.strptime(date, "%Y-%m-%d").date()
    res = await session.execute(select(MaterialConsumption).where(MaterialConsumption.id == item_id))
    rec = res.scalar_one_or_none()
    if not rec:
        raise HTTPException(404)
    # basic validation
    try:
        if Decimal(amount) <= 0:
            raise HTTPException(400, detail="amount must be > 0")
    except Exception:
        raise HTTPException(400, detail="invalid amount")
    rec.material_id = material_id
    rec.employee_id = employee_id
    rec.amount = amount
    rec.date = d
    await session.flush()
    res2 = await session.execute(
        select(MaterialConsumption)
        .options(selectinload(MaterialConsumption.material), selectinload(MaterialConsumption.employee))
        .order_by(MaterialConsumption.date.desc(), MaterialConsumption.id.desc())
    )
    items = res2.scalars().all()
    return templates.TemplateResponse(
        "materials/partials/consumptions_table.html",
        {"request": request, "items": items, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


@app.get("/materials/supplies", response_class=HTMLResponse)
async def supplies_list(request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(
        select(MaterialSupply)
        .options(selectinload(MaterialSupply.material), selectinload(MaterialSupply.employee))
        .order_by(MaterialSupply.date.desc(), MaterialSupply.id.desc())
    )
    items = res.scalars().all()
    res_m = await session.execute(select(Material).where(Material.is_active == True).order_by(Material.name))
    materials = res_m.scalars().all()
    res_u = await session.execute(select(User).where(User.is_deleted == False).order_by(User.first_name, User.last_name))
    users = res_u.scalars().all()
    return templates.TemplateResponse("materials/supplies.html", {"request": request, "items": items, "materials": materials, "users": users})


@app.get("/materials/supplies/modal/create", response_class=HTMLResponse)
async def supplies_modal_create(request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res_m = await session.execute(select(Material).where(Material.is_active == True).order_by(Material.name))
    materials = res_m.scalars().all()
    res_u = await session.execute(select(User).where(User.is_deleted == False).order_by(User.first_name, User.last_name))
    users = res_u.scalars().all()
    from datetime import date as _date
    today = _date.today()
    return templates.TemplateResponse("materials/partials/supplies_create_modal.html", {"request": request, "materials": materials, "users": users, "today": today})


@app.get("/materials/supplies/{item_id}/modal/delete", response_class=HTMLResponse)
async def supplies_modal_delete(item_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    return templates.TemplateResponse("materials/partials/supplies_delete_modal.html", {"request": request, "item_id": item_id})


@app.get("/materials/supplies/{item_id}/modal/edit", response_class=HTMLResponse)
async def supplies_modal_edit(item_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    res = await session.execute(select(MaterialSupply).where(MaterialSupply.id == item_id))
    rec = res.scalar_one_or_none()
    if not rec:
        raise HTTPException(404)
    res_m = await session.execute(select(Material).where(Material.is_active == True).order_by(Material.name))
    materials = res_m.scalars().all()
    res_u = await session.execute(select(User).where(User.is_deleted == False).order_by(User.first_name, User.last_name))
    users = res_u.scalars().all()
    return templates.TemplateResponse("materials/partials/supplies_edit_modal.html", {"request": request, "rec": rec, "materials": materials, "users": users})


@app.post("/materials/supplies/create")
async def supplies_create(
    request: Request,
    material_id: int = Form(...),
    employee_id: int | None = Form(None),
    amount: Decimal = Form(...),
    date: str = Form(...),
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    from datetime import datetime as dt
    d = dt.strptime(date, "%Y-%m-%d").date()
    # validate amount > 0
    try:
        if Decimal(amount) <= 0:
            raise HTTPException(400, detail="amount must be > 0")
    except Exception:
        raise HTTPException(400, detail="invalid amount")
    rec = MaterialSupply(material_id=material_id, employee_id=employee_id or None, amount=amount, date=d)
    session.add(rec)
    await session.flush()
    await update_stock_on_new_supply(session, rec)

    # Notify reports chat after successful commit (no duplicates)
    res_m = await session.execute(select(Material).where(Material.id == material_id))
    mat = res_m.scalar_one_or_none()
    material_title = mat.name if mat else "—"
    if mat and getattr(mat, "short_name", None):
        material_title = f"{mat.name} ({mat.short_name})"
    res_u = await session.execute(select(User).where(User.tg_id == admin_id).where(User.is_deleted == False))
    admin_user = res_u.scalar_one_or_none()
    full_name = ""
    if admin_user is not None:
        full_name = str(getattr(admin_user, "full_name", "") or "").strip()
        if not full_name:
            first = str(getattr(admin_user, "first_name", "") or "").strip()
            last = str(getattr(admin_user, "last_name", "") or "").strip()
            full_name = (first + " " + last).strip()
        if not full_name:
            full_name = str(getattr(admin_user, "username", "") or "").strip()
    if not full_name:
        full_name = f"Staff {admin_id}"
    actor = StockEventActor(name=full_name, tg_id=admin_id)
    stock_after = Decimal(mat.current_stock) if mat else None
    happened_at = getattr(rec, "created_at", None)
    add_after_commit_callback(
        session,
        lambda: notify_reports_chat_about_stock_event(
            kind="supply",
            material_name=material_title,
            amount=Decimal(rec.amount),
            unit=(mat.unit if mat else ""),
            actor=actor,
            happened_at=happened_at,
            stock_after=stock_after,
        ),
    )
    res = await session.execute(
        select(MaterialSupply)
        .options(selectinload(MaterialSupply.material), selectinload(MaterialSupply.employee))
        .order_by(MaterialSupply.date.desc(), MaterialSupply.id.desc())
    )
    items = res.scalars().all()
    return templates.TemplateResponse(
        "materials/partials/supplies_table.html",
        {"request": request, "items": items, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


@app.post("/materials/supplies/{item_id}/delete")
async def supplies_delete(item_id: int, request: Request, admin_id: int = Depends(require_staff), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)
    await session.execute(delete(MaterialSupply).where(MaterialSupply.id == item_id))
    res = await session.execute(
        select(MaterialSupply)
        .options(selectinload(MaterialSupply.material), selectinload(MaterialSupply.employee))
        .order_by(MaterialSupply.date.desc(), MaterialSupply.id.desc())
    )
    items = res.scalars().all()
    return templates.TemplateResponse(
        "materials/partials/supplies_table.html",
        {"request": request, "items": items, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )


@app.post("/materials/supplies/{item_id}/update")
async def supplies_update(
    item_id: int,
    request: Request,
    material_id: int = Form(...),
    employee_id: int | None = Form(None),
    amount: Decimal = Form(...),
    date: str = Form(...),
    admin_id: int = Depends(require_staff),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    from datetime import datetime as dt
    d = dt.strptime(date, "%Y-%m-%d").date()
    res = await session.execute(select(MaterialSupply).where(MaterialSupply.id == item_id))
    rec = res.scalar_one_or_none()
    if not rec:
        raise HTTPException(404)
    try:
        if Decimal(amount) <= 0:
            raise HTTPException(400, detail="amount must be > 0")
    except Exception:
        raise HTTPException(400, detail="invalid amount")
    rec.material_id = material_id
    rec.employee_id = employee_id or None
    rec.amount = amount
    rec.date = d
    await session.flush()
    res2 = await session.execute(
        select(MaterialSupply)
        .options(selectinload(MaterialSupply.material), selectinload(MaterialSupply.employee))
        .order_by(MaterialSupply.date.desc(), MaterialSupply.id.desc())
    )
    items = res2.scalars().all()
    return templates.TemplateResponse(
        "materials/partials/supplies_table.html",
        {"request": request, "items": items, "is_oob": True},
        headers={"HX-Trigger": "close-modal"},
    )
