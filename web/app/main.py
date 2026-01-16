from fastapi import FastAPI, Depends, Request, Response, HTTPException, status, Form, UploadFile, File, Header
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import jwt, JWTError
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import json
import logging
import httpx
import re
import calendar

from shared.config import settings
from shared.db import get_async_session
from sqlalchemy.ext.asyncio import AsyncSession
from shared.enums import UserStatus, Schedule, Position, TaskStatus, TaskPriority, TaskEventType
from shared.models import User
from shared.models import MaterialType, Material, MaterialConsumption, MaterialSupply
from shared.models import Task, TaskComment, TaskCommentPhoto, TaskEvent
from shared.models import WorkShiftDay
from shared.models import ShiftInstance
from shared.models import ShiftSwapRequest
from sqlalchemy import select, delete
from sqlalchemy import case
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from decimal import Decimal
from shared.services.material_stock import (
    recalculate_material_stock,
    update_stock_on_new_consumption,
    update_stock_on_new_supply,
)

from shared.db import add_after_commit_callback
from shared.services.stock_events_notify import notify_reports_chat_about_stock_event, StockEventActor

from .services.stocks_dashboard import (
    build_chart_rows,
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
from shared.permissions import role_flags
from shared.services.task_permissions import task_permissions, validate_status_transition
from shared.services.task_audit import diff_task_for_audit
from shared.services.task_edit import update_task_with_audit


logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates" 

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


UPLOADS_DIR = STATIC_DIR / "uploads" / "tasks"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


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

    data = await photo.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Пустой файл")
    fs_path.write_bytes(data)

    photo_path = _task_photo_path_from_key(photo_key)
    if not photo_path:
        raise HTTPException(status_code=500, detail="Не удалось сформировать путь фото")
    return str(photo_key), str(photo_path)


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
        data = await f.read()
        if not data:
            continue
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
    is_overdue = bool(due_at_utc and due_at_utc < utc_now())
    return {
        "id": int(t.id),
        "title": t.title,
        "priority": t.priority.value if hasattr(t.priority, "value") else str(t.priority),
        "status": t.status.value if hasattr(t.status, "value") else str(t.status),
        "due_at_str": due_at_str,
        "created_at_str": format_moscow(getattr(t, "created_at", None), "%d.%m.%Y %H:%M"),
        "assignees": assignees_view,
        "assignees_str": assignees_str,
        "is_assigned_to_me": is_assigned_to_me,
        "is_overdue": is_overdue,
    }


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


@app.get("/schedule/public", response_class=HTMLResponse, name="schedule_page_public")
async def schedule_page_public(
    request: Request, admin_id: int = Depends(require_authenticated_user), session: AsyncSession = Depends(get_db)
):
    actor = await load_staff_user(session, admin_id)
    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)
    return templates.TemplateResponse(
        "schedule/calendar.html",
        {
            "request": request,
            "base_template": "base_public.html",
            "is_admin": is_admin,
            "is_manager": is_manager,
        },
    )


@app.get("/api/schedule/month")
async def schedule_api_month(
    request: Request,
    year: int,
    month: int,
    user_id: int | None = None,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    await ensure_manager_allowed(request, admin_id, session)
    actor = await load_staff_user(session, admin_id)

    rflags = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin_or_manager = bool(rflags.is_admin or rflags.is_manager)
    target_user_id = int(actor.id)
    if user_id is not None:
        if not is_admin_or_manager:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        target_user_id = int(user_id)

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
                "hours": getattr(wsd, "hours", None),
                "is_emergency": bool(getattr(wsd, "is_emergency", False)),
                "shift_status": str(getattr(fact, "status", "") or "") if fact is not None else "",
                "shift_approval_required": bool(getattr(fact, "approval_required", False)) if fact is not None else False,
            }
        )

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

    out: dict[str, dict] = {}
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
        out[day_key] = {
            "kind": str(getattr(r, "kind", "") or ""),
            "hours": getattr(r, "hours", None),
            "is_emergency": bool(getattr(r, "is_emergency", False)),
            "shift_status": status,
            "shift_amount": amount,
            "shift_approval_required": approval_required,
            "staff_total": len(day_staff),
            "staff_preview": day_staff[:3],
        }

    # Include days where the selected user has no plan record, but we still want staff preview and/or fact
    for day_key, day_staff in staff_by_day.items():
        if day_key in out:
            continue
        fact = fact_by_day.get(day_key)
        status: str | None = None
        approval_required: bool | None = None
        amount: int | None = None
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
        out[day_key] = {
            "kind": "",
            "hours": None,
            "is_emergency": bool(getattr(fact, "is_emergency", False)) if fact is not None else False,
            "shift_status": status,
            "shift_amount": amount,
            "shift_approval_required": approval_required,
            "staff_total": len(day_staff),
            "staff_preview": day_staff[:3],
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
            "is_emergency": bool(getattr(fact, "is_emergency", False)),
            "shift_status": status,
            "shift_amount": amount,
            "shift_approval_required": approval_required,
            "staff_total": 0,
            "staff_preview": [],
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
        if existing is not None:
            await session.delete(existing)
            await session.flush()
        return {"ok": True}

    if kind not in {"work", "off"}:
        raise HTTPException(status_code=422, detail="Неверный тип")

    hours: int | None = None
    if kind == "work":
        try:
            hours = int(hours_raw) if hours_raw is not None else 8
        except Exception:
            hours = 8
        if hours not in {8, 10, 12}:
            raise HTTPException(status_code=422, detail="Неверные часы")
    else:
        hours = None

    if existing is None:
        existing = WorkShiftDay(user_id=int(uid), day=day, kind=kind, hours=hours, is_emergency=False)
        session.add(existing)
    else:
        existing.kind = kind
        existing.hours = hours
        existing.is_emergency = bool(getattr(existing, "is_emergency", False))

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
        out.append(
            {
                "user_id": uid,
                "name": name,
                "color": str(getattr(u, "color", "#94a3b8") or "#94a3b8"),
                "kind": str(getattr(shift, "kind", "") or ""),
                "hours": getattr(shift, "hours", None),
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
    hours_raw = body.get("hours")
    comment = str(body.get("comment") or "").strip() or None
    replace = bool(body.get("replace") or False)
    target_user_id = body.get("user_id")

    if not day_raw:
        raise HTTPException(status_code=422, detail="Не задан день")
    try:
        d = datetime.strptime(day_raw, "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=422, detail="Неверная дата")

    try:
        hours = int(hours_raw)
    except Exception:
        raise HTTPException(status_code=422, detail="Неверные часы")
    if hours not in {8, 10, 12}:
        raise HTTPException(status_code=422, detail="Неверные часы")

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
            await session.flush()
            return {"ok": True, "updated": True}

        # Existing planned shift: require explicit replace
        if not replace:
            raise HTTPException(status_code=409, detail="Смена уже запланирована. Заменить?")

        existing.kind = "work"
        existing.hours = hours
        existing.is_emergency = True
        existing.comment = comment
        await session.flush()
        return {"ok": True, "replaced": True}

    row = WorkShiftDay(user_id=int(uid), day=d, kind="work", hours=hours, is_emergency=True, comment=comment)
    session.add(row)
    await session.flush()
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


@app.get("/broadcast", response_class=HTMLResponse)
async def broadcast_modal(request: Request, admin_id: int = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(User).where(User.status == UserStatus.APPROVED).where(User.is_deleted == False))
    users = res.scalars().all()
    return templates.TemplateResponse("partials/broadcast_modal.html", {"request": request, "users": users})


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

    q = (request.query_params.get("q") or "").strip()
    mine = (request.query_params.get("mine") or "").strip() in {"1", "true", "yes", "on"}
    priority = (request.query_params.get("priority") or "").strip()
    due = (request.query_params.get("due") or "").strip()
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

    query = (
        select(Task)
        .where(Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.DONE]))
        .options(selectinload(Task.assignees), selectinload(Task.created_by_user))
        .order_by(urgent_first.asc(), Task.due_at.asc().nullslast(), Task.created_at.desc(), Task.id.desc())
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

    if assignee_id is not None:
        has_selected = exists(
            select(1).where(and_(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == int(assignee_id)))
        )
        query = query.where(has_selected)
    if mine:
        pass

    res = await session.execute(query)
    tasks = list(res.scalars().unique().all())

    items_by = {TaskStatus.NEW.value: [], TaskStatus.IN_PROGRESS.value: [], TaskStatus.REVIEW.value: [], TaskStatus.DONE.value: []}
    for t in tasks:
        items_by[(t.status.value if hasattr(t.status, "value") else str(t.status))].append(
            _task_card_view(t, actor_id=int(actor.id))
        )

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
            "users_json": users_json,
            "base_template": "base.html",
            "archive_url": request.url_for("tasks_archive"),
        },
    )


@app.get("/tasks/public", response_class=HTMLResponse, name="tasks_board_public")
async def tasks_board_public(request: Request, admin_id: int = Depends(require_authenticated_user), session: AsyncSession = Depends(get_db)):
    actor = await load_staff_user(session, admin_id)

    # Default for public board: show "Мои задачи" only on the very first visit.
    # After that the user must be able to disable the filter (URL without mine).
    seen = (request.cookies.get("public_board_seen") or "").strip() == "1"
    if (not request.query_params) and (not seen):
        url = str(request.url)
        resp = RedirectResponse(url + "?mine=1", status_code=302)
        resp.set_cookie(
            "public_board_seen",
            "1",
            httponly=False,
            secure=False,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
        )
        return resp

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)

    q = (request.query_params.get("q") or "").strip()
    mine = (request.query_params.get("mine") or "").strip() in {"1", "true", "yes", "on"}
    priority = (request.query_params.get("priority") or "").strip()
    due = (request.query_params.get("due") or "").strip()

    # Public board: show all tasks (same as admin board), but without sidebar
    from shared.models import task_assignees
    from sqlalchemy import or_ as _or, exists, and_

    urgent_first = case((Task.priority == TaskPriority.URGENT, 0), else_=1)
    query = (
        select(Task)
        .where(Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.DONE]))
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

    if mine:
        has_actor = exists(
            select(1).where(and_(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == int(actor.id)))
        )
        query = query.where(has_actor)

    res = await session.execute(query)
    tasks = list(res.scalars().unique().all())

    items_by = {TaskStatus.NEW.value: [], TaskStatus.IN_PROGRESS.value: [], TaskStatus.REVIEW.value: [], TaskStatus.DONE.value: []}
    for t in tasks:
        items_by[(t.status.value if hasattr(t.status, "value") else str(t.status))].append(_task_card_view(t, actor_id=int(actor.id)))

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
            "users_json": users_json,
            "base_template": "base_public.html",
            "archive_url": request.url_for("tasks_archive_public"),
        },
    )

    if not seen:
        resp.set_cookie(
            "public_board_seen",
            "1",
            httponly=False,
            secure=False,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
        )

    return resp


@app.get("/api/public/tasks")
async def tasks_api_public_list(
    request: Request,
    admin_id: int = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_db),
):
    actor = await load_staff_user(session, admin_id)

    q = (request.query_params.get("q") or "").strip()
    mine = (request.query_params.get("mine") or "").strip() in {"1", "true", "yes", "on"}
    priority = (request.query_params.get("priority") or "").strip()
    due = (request.query_params.get("due") or "").strip()
    status_q = (request.query_params.get("status") or "").strip()

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
        "items": [_task_card_view(t, actor_id=int(actor.id)) for t in tasks],
        "mine": bool(mine),
    }


@app.get("/tasks/archive", response_class=HTMLResponse, name="tasks_archive")
async def tasks_archive(request: Request, admin_id: int = Depends(require_authenticated_user), session: AsyncSession = Depends(get_db)):
    await ensure_manager_allowed(request, admin_id, session)

    actor = await load_staff_user(session, admin_id)

    r = role_flags(tg_id=int(admin_id), admin_ids=settings.admin_ids, status=actor.status, position=actor.position)
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)

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

    pr = TaskPriority.URGENT if priority == TaskPriority.URGENT.value else TaskPriority.NORMAL
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
            for u in users_assignees:
                await ns.enqueue(
                    task_id=int(t.id),
                    recipient_user_id=int(u.id),
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

    # Visibility is not restricted; permissions control actions.

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
    if created_by is not None:
        created_by_str = (f"{(created_by.first_name or '').strip()} {(created_by.last_name or '').strip()}".strip() or f"#{created_by.id}")

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
        "created_by_str": created_by_str,
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
    c = TaskComment(task_id=int(t.id), author_user_id=int(actor.id), text=(text or None))
    session.add(c)
    await session.flush()

    urls = await _save_uploads(photos)

    session.add(
        TaskEvent(
            task_id=int(t.id),
            actor_user_id=int(actor.id),
            type=TaskEventType.COMMENT_ADDED,
            payload={"has_text": bool(text and text.strip()), "photos_count": int(len(urls))},
        )
    )
    for url in urls:
        session.add(TaskCommentPhoto(comment_id=int(c.id), tg_file_id=url))
    await session.flush()

    try:
        # Notify other side: executor <-> creator
        assignees = list(getattr(t, "assignees", None) or [])
        is_executor = any(int(u.id) == int(actor.id) for u in assignees) or (
            (len(assignees) == 0)
            and (getattr(t, "started_by_user_id", None) is not None)
            and int(getattr(t, "started_by_user_id")) == int(actor.id)
        )

        recipients: list[int] = []
        if is_executor:
            recipients = [int(getattr(t, "created_by_user_id"))]
        else:
            if assignees:
                recipients = [int(u.id) for u in assignees]
            else:
                sb = getattr(t, "started_by_user_id", None)
                if sb is not None:
                    recipients = [int(sb)]

        recipients = [r for r in recipients if int(r) != int(actor.id)]
        if recipients:
            ns = TaskNotificationService(session)
            actor_name = (
                f"{(actor.first_name or '').strip()} {(actor.last_name or '').strip()}".strip() or f"#{int(actor.id)}"
            )
            for rid in recipients:
                await ns.enqueue(
                    task_id=int(t.id),
                    recipient_user_id=int(rid),
                    type="comment",
                    payload={
                        "task_id": int(t.id),
                        "comment_id": int(getattr(c, "id", 0) or 0),
                        "text": (text or ""),
                        "photos_count": int(len(urls)),
                        "actor_user_id": int(actor.id),
                        "actor_name": actor_name,
                    },
                    dedupe_key=f"comment:{int(getattr(c, 'id', 0) or 0)}",
                )
    except Exception:
        pass

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

    try:
        # status notifications
        recipients: list[int] = []
        assignees = list(getattr(t, "assignees", None) or [])
        executor_ids: list[int] = [int(u.id) for u in assignees]
        if not executor_ids:
            sb = getattr(t, "started_by_user_id", None)
            if sb is not None:
                executor_ids = [int(sb)]

        if old_status == TaskStatus.NEW.value and new_status_val == TaskStatus.IN_PROGRESS.value:
            recipients = [int(getattr(t, "created_by_user_id"))]
        elif old_status == TaskStatus.IN_PROGRESS.value and new_status_val == TaskStatus.REVIEW.value:
            recipients = [int(getattr(t, "created_by_user_id"))]
        elif old_status == TaskStatus.REVIEW.value and new_status_val == TaskStatus.DONE.value:
            recipients = list(executor_ids)
        elif old_status == TaskStatus.REVIEW.value and new_status_val == TaskStatus.IN_PROGRESS.value:
            recipients = list(executor_ids)

        recipients = [r for r in recipients if r and int(r) != int(actor.id)]
        if recipients:
            ns = TaskNotificationService(session)
            actor_name = (f"{(actor.first_name or '').strip()} {(actor.last_name or '').strip()}".strip() or f"#{int(actor.id)}")
            for rid in recipients:
                await ns.enqueue(
                    task_id=int(t.id),
                    recipient_user_id=int(rid),
                    type="status_changed",
                    payload={
                        "task_id": int(t.id),
                        "from": str(old_status),
                        "to": str(new_status_val),
                        "comment": comment or None,
                        "actor_user_id": int(actor.id),
                        "actor_name": actor_name,
                        "event_id": int(getattr(ev, "id", 0) or 0),
                    },
                    dedupe_key=f"status:{int(getattr(ev, 'id', 0) or 0)}",
                )
    except Exception:
        pass
    return {"ok": True, "id": int(t.id), "status": new_status_val}


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
    if mat and getattr(mat, "short_name", None):
        material_title = f"{mat.name} ({mat.short_name})"
    actor = StockEventActor(name=f"Staff {admin_id}", tg_id=admin_id)
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
    actor = StockEventActor(name=f"Staff {admin_id}", tg_id=admin_id)
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
