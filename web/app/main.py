from fastapi import FastAPI, Depends, Request, Response, HTTPException, status, Form
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import jwt, JWTError
from datetime import datetime, timezone
from typing import Optional, List

from shared.config import settings
from shared.db import get_async_session
from sqlalchemy.ext.asyncio import AsyncSession
from shared.enums import UserStatus, Schedule, Position
from shared.models import User
from sqlalchemy import select, delete

from .config import get_config
from .services.messenger import Messenger
from .repository import AdminLogRepo
from shared.enums import AdminActionType

from pathlib import Path

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


async def get_db() -> AsyncSession:
    async with get_async_session() as session:
        yield session

async def load_user(session: AsyncSession, user_id: int) -> User:
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(404)
    return user


def require_admin(request: Request):
    token = request.cookies.get("admin_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        data = jwt.decode(token, settings.WEB_JWT_SECRET, algorithms=["HS256"])
        if data.get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        sub = int(data.get("sub"))
        if sub not in settings.admin_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        exp = data.get("exp")
        if exp and datetime.fromtimestamp(exp, tz=timezone.utc) < datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return sub
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


@app.get("/auth")
async def auth(token: str, request: Request):
    # Validate token, set cookie, redirect to index
    try:
        data = jwt.decode(token, settings.WEB_JWT_SECRET, algorithms=["HS256"])
        if data.get("role") != "admin":
            raise HTTPException(status_code=403)
        sub = int(data.get("sub"))
        if sub not in settings.admin_ids:
            raise HTTPException(status_code=403)
    except JWTError:
        raise HTTPException(status_code=401)
    # Redirect to index using url_for to respect root_path (/crm)
    resp = RedirectResponse(url=request.url_for("index"), status_code=302)
    resp.set_cookie("admin_token", token, httponly=True, secure=False, samesite="lax")
    return resp


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, admin_id: int = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(User).order_by(User.created_at.desc()))
    users: List[User] = res.scalars().all()
    return templates.TemplateResponse("index.html", {"request": request, "users": users, "admin_id": admin_id})


@app.get("/users/{user_id}", response_class=HTMLResponse)
async def user_modal(user_id: int, request: Request, admin_id: int = Depends(require_admin), session: AsyncSession = Depends(get_db)):
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
    admin_id: int = Depends(require_admin),
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
                    reply_markup=main_menu_kb(user.status, user.tg_id),
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
                reply_markup=main_menu_kb(user.status, user.tg_id),
            )
        finally:
            await bot.session.close()
    except Exception:
        # non-fatal
        pass
    return templates.TemplateResponse(
        "partials/user_row.html",
        {"request": request, "u": user, "is_oob": True},
    )


@app.post("/users/{user_id}/delete")
async def user_delete(user_id: int, admin_id: int = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    # Ensure user exists first
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(404)
    # Log BEFORE deletion to keep user_id referencing existing row
    repo = AdminLogRepo(session)
    await repo.log(admin_tg_id=admin_id, user_id=user.id, action=AdminActionType.BLACKLIST, payload={"delete": True})
    # Now delete the user
    await session.execute(delete(User).where(User.id == user_id))
    return Response(status_code=204)


@app.post("/users/{user_id}/message")
async def user_message(user_id: int, text: str = Form(...), admin_id: int = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(User).where(User.id == user_id))
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
    return Response(status_code=204)


@app.get("/broadcast", response_class=HTMLResponse)
async def broadcast_modal(request: Request, admin_id: int = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    res = await session.execute(select(User).where(User.status == UserStatus.APPROVED))
    users = res.scalars().all()
    return templates.TemplateResponse("partials/broadcast_modal.html", {"request": request, "users": users})


@app.post("/broadcast")
async def broadcast(text: str = Form(...), user_ids: Optional[str] = Form(None), admin_id: int = Depends(require_admin), session: AsyncSession = Depends(get_db)):
    ids: Optional[List[int]] = None
    if user_ids:
        ids = [int(x) for x in user_ids.split(",") if x.strip()]
    q = select(User).where(User.status == UserStatus.APPROVED)
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
    return Response(status_code=204)
