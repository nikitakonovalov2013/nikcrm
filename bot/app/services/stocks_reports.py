from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import Material, MaterialSupply, MaterialConsumption, User


@dataclass
class StockEvent:
    dt: datetime
    kind: str  # in/out
    user_fio: str
    material_name: str
    amount: Decimal
    unit: str


@dataclass
class MaterialAgg:
    material_id: int
    name: str
    unit: str
    incoming: Decimal
    outgoing: Decimal


@dataclass
class ReportData:
    start: datetime
    end: datetime
    materials: list[MaterialAgg]
    total_in: Decimal
    total_out: Decimal
    top_out: MaterialAgg | None
    events: list[StockEvent]


def _fio(u: User | None) -> str:
    if not u:
        return "—"
    fio = f"{u.first_name or ''} {u.last_name or ''}".strip()
    return fio or f"User #{u.id}"


def _dt_range_for_dates(d_from: date, d_to: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(d_from, time(0, 0, 0), tzinfo=tz)
    end = datetime.combine(d_to, time(23, 59, 59), tzinfo=tz)
    return start, end


async def build_report(session: AsyncSession, *, start: datetime, end: datetime, events_limit: int = 10) -> ReportData:
    supplies_q = (
        select(MaterialSupply, Material, User)
        .join(Material, Material.id == MaterialSupply.material_id)
        .join(User, User.id == MaterialSupply.employee_id, isouter=True)
        .where(MaterialSupply.created_at >= start)
        .where(MaterialSupply.created_at <= end)
    )
    cons_q = (
        select(MaterialConsumption, Material, User)
        .join(Material, Material.id == MaterialConsumption.material_id)
        .join(User, User.id == MaterialConsumption.employee_id)
        .where(MaterialConsumption.created_at >= start)
        .where(MaterialConsumption.created_at <= end)
    )

    supplies_rows = (await session.execute(supplies_q)).all()
    cons_rows = (await session.execute(cons_q)).all()

    mats: dict[int, MaterialAgg] = {}
    events: list[StockEvent] = []

    for rec, m, u in supplies_rows:
        mid = int(m.id)
        if mid not in mats:
            mats[mid] = MaterialAgg(material_id=mid, name=m.name, unit=m.unit, incoming=Decimal(0), outgoing=Decimal(0))
        mats[mid].incoming += Decimal(rec.amount)
        events.append(
            StockEvent(
                dt=rec.created_at,
                kind="in",
                user_fio=_fio(u),
                material_name=m.name,
                amount=Decimal(rec.amount),
                unit=m.unit,
            )
        )

    for rec, m, u in cons_rows:
        mid = int(m.id)
        if mid not in mats:
            mats[mid] = MaterialAgg(material_id=mid, name=m.name, unit=m.unit, incoming=Decimal(0), outgoing=Decimal(0))
        mats[mid].outgoing += Decimal(rec.amount)
        events.append(
            StockEvent(
                dt=rec.created_at,
                kind="out",
                user_fio=_fio(u),
                material_name=m.name,
                amount=Decimal(rec.amount),
                unit=m.unit,
            )
        )

    materials = sorted(mats.values(), key=lambda x: (x.outgoing, x.incoming), reverse=True)
    total_in = sum((m.incoming for m in materials), Decimal(0))
    total_out = sum((m.outgoing for m in materials), Decimal(0))

    top_out = None
    if materials:
        top_out = max(materials, key=lambda x: x.outgoing)
        if top_out.outgoing <= 0:
            top_out = None

    events_sorted = sorted(events, key=lambda e: e.dt, reverse=True)[:events_limit]

    return ReportData(
        start=start,
        end=end,
        materials=materials,
        total_in=total_in,
        total_out=total_out,
        top_out=top_out,
        events=events_sorted,
    )
