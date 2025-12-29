from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from math import ceil
from typing import Iterable

from sqlalchemy import Select, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Material, MaterialConsumption, MaterialSupply, User
from shared.utils import format_moscow


FORECAST_DAYS_WINDOW = 4
LOW_STOCK_THRESHOLD_DAYS = 15
HISTORY_LIMIT_DEFAULT = 20


@dataclass(frozen=True)
class ChartRow:
    material_name: str
    total_in: Decimal
    total_out: Decimal


@dataclass(frozen=True)
class HistoryRow:
    ts: datetime
    actor_name: str
    actor_tg_id: int | None
    kind: str  # 'in' | 'out'
    amount: Decimal
    material_name: str


@dataclass(frozen=True)
class StockRow:
    material_name: str
    current_stock: Decimal
    avg_daily_out: Decimal | None
    forecast_days: int | None
    is_low: bool


def format_dt_ru(dt: datetime) -> str:
    return format_moscow(dt)


def _coalesce_decimal(expr) -> Select:
    return func.coalesce(expr, 0)


async def get_period_sums(
    session: AsyncSession,
    date_from: date,
    date_to: date,
) -> dict[int, dict[str, Decimal]]:
    """Return per-material sums for supplies/consumptions over [date_from, date_to]."""

    supplies_q = (
        select(
            MaterialSupply.material_id.label("material_id"),
            _coalesce_decimal(func.sum(MaterialSupply.amount)).label("total_in"),
        )
        .where(MaterialSupply.date >= date_from)
        .where(MaterialSupply.date <= date_to)
        .group_by(MaterialSupply.material_id)
    )

    consumptions_q = (
        select(
            MaterialConsumption.material_id.label("material_id"),
            _coalesce_decimal(func.sum(MaterialConsumption.amount)).label("total_out"),
        )
        .where(MaterialConsumption.date >= date_from)
        .where(MaterialConsumption.date <= date_to)
        .group_by(MaterialConsumption.material_id)
    )

    supplies_rows = (await session.execute(supplies_q)).all()
    cons_rows = (await session.execute(consumptions_q)).all()

    out: dict[int, dict[str, Decimal]] = {}
    for material_id, total_in in supplies_rows:
        out.setdefault(int(material_id), {})["total_in"] = Decimal(total_in)
    for material_id, total_out in cons_rows:
        out.setdefault(int(material_id), {})["total_out"] = Decimal(total_out)

    for mid in list(out.keys()):
        out[mid].setdefault("total_in", Decimal("0"))
        out[mid].setdefault("total_out", Decimal("0"))

    return out


async def build_chart_rows(
    session: AsyncSession,
    date_from: date,
    date_to: date,
) -> list[ChartRow]:
    sums = await get_period_sums(session, date_from, date_to)

    # We want chart to include all materials to keep it stable.
    materials = (await session.execute(select(Material.id, Material.name).order_by(Material.name))).all()
    rows: list[ChartRow] = []
    for mid, name in materials:
        d = sums.get(int(mid), {"total_in": Decimal("0"), "total_out": Decimal("0")})
        rows.append(
            ChartRow(
                material_name=str(name),
                total_in=Decimal(d["total_in"]),
                total_out=Decimal(d["total_out"]),
            )
        )
    return rows


async def build_history_rows(
    session: AsyncSession,
    limit: int = HISTORY_LIMIT_DEFAULT,
) -> list[HistoryRow]:
    """Last N events from supplies/consumptions sorted by created_at desc."""

    # Actor is stored on the records as employee_id.
    # For supplies it can be NULL; for consumptions it's required.
    s = (
        select(
            MaterialSupply.created_at.label("ts"),
            literal("in").label("kind"),
            MaterialSupply.amount.label("amount"),
            Material.name.label("material_name"),
            MaterialSupply.employee_id.label("employee_id"),
            User.first_name.label("first_name"),
            User.last_name.label("last_name"),
            User.tg_id.label("tg_id"),
        )
        .join(Material, Material.id == MaterialSupply.material_id)
        .outerjoin(User, User.id == MaterialSupply.employee_id)
    )

    c = (
        select(
            MaterialConsumption.created_at.label("ts"),
            literal("out").label("kind"),
            MaterialConsumption.amount.label("amount"),
            Material.name.label("material_name"),
            MaterialConsumption.employee_id.label("employee_id"),
            User.first_name.label("first_name"),
            User.last_name.label("last_name"),
            User.tg_id.label("tg_id"),
        )
        .join(Material, Material.id == MaterialConsumption.material_id)
        .outerjoin(User, User.id == MaterialConsumption.employee_id)
    )

    union_q = s.union_all(c).subquery("events")

    q = (
        select(
            union_q.c.ts,
            union_q.c.kind,
            union_q.c.amount,
            union_q.c.material_name,
            union_q.c.employee_id,
            union_q.c.first_name,
            union_q.c.last_name,
            union_q.c.tg_id,
        )
        .order_by(union_q.c.ts.desc())
        .limit(limit)
    )

    res = (await session.execute(q)).all()
    out: list[HistoryRow] = []
    for ts, kind, amount, material_name, employee_id, first_name, last_name, tg_id in res:
        fio = f"{first_name or ''} {last_name or ''}".strip()
        if fio:
            actor_name = fio
        else:
            actor_name = f"Удалённый сотрудник (id={int(employee_id)})" if employee_id is not None else "—"
        out.append(
            HistoryRow(
                ts=ts,
                actor_name=actor_name if actor_name else "—",
                actor_tg_id=int(tg_id) if tg_id is not None else None,
                kind=str(kind),
                amount=Decimal(amount),
                material_name=str(material_name),
            )
        )
    return out


async def get_avg_daily_consumption_last_days(
    session: AsyncSession,
    days: int = FORECAST_DAYS_WINDOW,
    today: date | None = None,
) -> dict[int, Decimal]:
    today = today or date.today()
    date_from = today - timedelta(days=days - 1)

    q = (
        select(
            MaterialConsumption.material_id,
            _coalesce_decimal(func.sum(MaterialConsumption.amount)).label("sum_amount"),
        )
        .where(MaterialConsumption.date >= date_from)
        .where(MaterialConsumption.date <= today)
        .group_by(MaterialConsumption.material_id)
    )

    rows = (await session.execute(q)).all()
    out: dict[int, Decimal] = {}
    for material_id, sum_amount in rows:
        out[int(material_id)] = Decimal(sum_amount) / Decimal(days)
    return out


async def build_stock_rows(
    session: AsyncSession,
    days_window: int = FORECAST_DAYS_WINDOW,
    low_threshold_days: int = LOW_STOCK_THRESHOLD_DAYS,
) -> list[StockRow]:
    avg_map = await get_avg_daily_consumption_last_days(session, days=days_window)

    mats = (
        await session.execute(
            select(Material.id, Material.name, Material.current_stock, Material.unit)
            .order_by(Material.name)
        )
    ).all()

    rows: list[StockRow] = []
    for mid, name, current_stock, unit in mats:
        stock = Decimal(current_stock)
        avg = avg_map.get(int(mid))

        forecast_days: int | None
        if not avg or avg <= 0:
            forecast_days = None
        else:
            forecast_days = int(ceil((stock / avg)))

        is_low = forecast_days is not None and forecast_days < low_threshold_days

        rows.append(
            StockRow(
                material_name=str(name),
                current_stock=stock,
                avg_daily_out=avg,
                forecast_days=forecast_days,
                is_low=is_low,
            )
        )

    # Sort by urgency: first those with computed forecast, then by forecast asc.
    def _sort_key(r: StockRow):
        return (
            0 if r.forecast_days is not None else 1,
            r.forecast_days if r.forecast_days is not None else 10**9,
            r.material_name.lower(),
        )

    rows.sort(key=_sort_key)
    return rows


async def build_pie_data(session: AsyncSession) -> list[dict[str, str]]:
    mats = (await session.execute(select(Material.name, Material.current_stock).order_by(Material.name))).all()
    out: list[dict[str, str]] = []
    for name, stock in mats:
        out.append({"label": str(name), "value": str(Decimal(stock))})
    return out
