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
class UserOutgoingAgg:
    user_id: int | None
    fio: str
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
    silicone_in: Decimal
    silicone_out: Decimal
    silicone_out_by_user: list[UserOutgoingAgg]
    outgoing_by_user: list[UserOutgoingAgg]


def _fio(u: User | None) -> str:
    if not u:
        return "—"
    fio = f"{u.first_name or ''} {u.last_name or ''}".strip()
    return fio or f"User #{u.id}"


def _is_silicone(name: str | None) -> bool:
    if not name:
        return False
    return "силикон" in str(name).lower()


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
        .join(User, User.id == MaterialConsumption.employee_id, isouter=True)
        .where(MaterialConsumption.created_at >= start)
        .where(MaterialConsumption.created_at <= end)
    )

    supplies_rows = (await session.execute(supplies_q)).all()
    cons_rows = (await session.execute(cons_q)).all()

    mats: dict[int, MaterialAgg] = {}
    events: list[StockEvent] = []

    silicone_in = Decimal(0)
    silicone_out = Decimal(0)
    silicone_out_by_user: dict[int | None, UserOutgoingAgg] = {}
    outgoing_by_user: dict[int | None, UserOutgoingAgg] = {}

    for rec, m, u in supplies_rows:
        mid = int(m.id)
        if mid not in mats:
            mats[mid] = MaterialAgg(material_id=mid, name=m.name, unit=m.unit, incoming=Decimal(0), outgoing=Decimal(0))
        mats[mid].incoming += Decimal(rec.amount)

        if _is_silicone(getattr(m, "name", None)):
            silicone_in += Decimal(rec.amount)
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

        if _is_silicone(getattr(m, "name", None)):
            amt = Decimal(rec.amount)
            silicone_out += amt
            uid = int(getattr(rec, "employee_id", None)) if getattr(rec, "employee_id", None) is not None else None
            if uid not in silicone_out_by_user:
                silicone_out_by_user[uid] = UserOutgoingAgg(user_id=uid, fio=_fio(u), outgoing=Decimal(0))
            silicone_out_by_user[uid].outgoing += amt

        try:
            amt2 = Decimal(rec.amount)
        except Exception:
            amt2 = Decimal(0)
        uid2 = int(getattr(rec, "employee_id", None)) if getattr(rec, "employee_id", None) is not None else None
        if uid2 not in outgoing_by_user:
            outgoing_by_user[uid2] = UserOutgoingAgg(user_id=uid2, fio=_fio(u), outgoing=Decimal(0))
        outgoing_by_user[uid2].outgoing += amt2
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

    silicone_users_sorted = sorted(silicone_out_by_user.values(), key=lambda x: x.outgoing, reverse=True)
    outgoing_users_sorted = sorted(outgoing_by_user.values(), key=lambda x: x.outgoing, reverse=True)

    return ReportData(
        start=start,
        end=end,
        materials=materials,
        total_in=total_in,
        total_out=total_out,
        top_out=top_out,
        events=events_sorted,
        silicone_in=silicone_in,
        silicone_out=silicone_out,
        silicone_out_by_user=silicone_users_sorted,
        outgoing_by_user=outgoing_users_sorted,
    )
