from fastapi import FastAPI, Depends, Request, Response, HTTPException, status, Form
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import jwt, JWTError
from datetime import datetime, timezone
from typing import Optional, List
import json

from shared.config import settings
from shared.db import get_async_session
from sqlalchemy.ext.asyncio import AsyncSession
from shared.enums import UserStatus, Schedule, Position
from shared.models import User
from shared.models import MaterialType, Material, MaterialConsumption, MaterialSupply
from sqlalchemy import select, delete
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

from .dependencies import require_admin, require_staff, ensure_manager_allowed

from shared.utils import format_number

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
    res = await session.execute(select(User).where(User.id == user_id).where(User.is_deleted == False))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(404)
    return user


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
