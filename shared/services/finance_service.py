from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from typing import Any

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import FinanceCategory, FinanceOperation, FinanceOperationFile
from shared.utils import utc_now

_DEC0 = Decimal("0")
_Q2 = lambda v: Decimal(str(v or 0)).quantize(Decimal("0.01"))


# ── Categories ───────────────────────────────────────────────────────────────

async def list_categories(
    *,
    session: AsyncSession,
    type_filter: str | None = None,
    include_archived: bool = False,
) -> list[FinanceCategory]:
    q = select(FinanceCategory).order_by(FinanceCategory.type.asc(), FinanceCategory.name.asc())
    if type_filter:
        q = q.where(FinanceCategory.type == str(type_filter))
    if not include_archived:
        q = q.where(FinanceCategory.is_archived == False)
    return list((await session.execute(q)).scalars().all())


async def get_category(*, session: AsyncSession, category_id: int) -> FinanceCategory | None:
    return (await session.execute(
        select(FinanceCategory).where(FinanceCategory.id == int(category_id))
    )).scalars().first()


async def create_category(*, session: AsyncSession, type: str, name: str) -> FinanceCategory:
    type_v = str(type or "").strip().lower()
    name_v = str(name or "").strip()
    if type_v not in ("income", "expense"):
        raise ValueError("invalid_type")
    if not name_v:
        raise ValueError("name_required")
    existing = (await session.execute(
        select(FinanceCategory).where(
            FinanceCategory.type == type_v,
            FinanceCategory.name == name_v,
        )
    )).scalars().first()
    if existing is not None:
        if existing.is_archived:
            existing.is_archived = False
            session.add(existing)
            await session.flush()
        return existing
    cat = FinanceCategory(type=type_v, name=name_v)
    session.add(cat)
    await session.flush()
    return cat


async def update_category(
    *,
    session: AsyncSession,
    category_id: int,
    name: str | None = None,
    is_archived: bool | None = None,
) -> FinanceCategory:
    cat = (await session.execute(
        select(FinanceCategory).where(FinanceCategory.id == int(category_id))
    )).scalars().first()
    if cat is None:
        raise ValueError("not_found")
    if name is not None:
        n = str(name).strip()
        if not n:
            raise ValueError("name_required")
        cat.name = n
    if is_archived is not None:
        cat.is_archived = bool(is_archived)
    session.add(cat)
    await session.flush()
    return cat


# ── Operations ────────────────────────────────────────────────────────────────

def _serialize_operation(op: FinanceOperation) -> dict:
    cat = getattr(op, "category", None)
    actor = getattr(op, "created_by_user", None)
    files = getattr(op, "files", None) or []
    actor_name = None
    if actor is not None:
        actor_name = (
            " ".join([
                str(getattr(actor, "first_name", "") or "").strip(),
                str(getattr(actor, "last_name", "") or "").strip(),
            ]).strip() or str(getattr(actor, "username", "") or "").strip() or None
        )
    return {
        "id": int(getattr(op, "id", 0) or 0),
        "type": str(getattr(op, "type", "") or ""),
        "amount": f"{_Q2(getattr(op, 'amount', 0))}",
        "occurred_at": str(getattr(op, "occurred_at", "") or ""),
        "category_id": (int(getattr(op, "category_id", 0) or 0) or None),
        "category_name": (str(getattr(cat, "name", "") or "") if cat else None),
        "category_type": (str(getattr(cat, "type", "") or "") if cat else None),
        "subcategory": (str(getattr(op, "subcategory", "") or "") or None),
        "counterparty": (str(getattr(op, "counterparty", "") or "") or None),
        "payment_method": (str(getattr(op, "payment_method", "") or "") or None),
        "comment": (str(getattr(op, "comment", "") or "") or None),
        "created_by_user_id": (int(getattr(op, "created_by_user_id", 0) or 0) or None),
        "actor_name": actor_name,
        "created_at": str(getattr(op, "created_at", "") or ""),
        "updated_at": str(getattr(op, "updated_at", "") or ""),
        "files": [
            {
                "id": int(getattr(f, "id", 0) or 0),
                "file_url": (str(getattr(f, "file_url", "") or "") or None),
                "tg_file_id": (str(getattr(f, "tg_file_id", "") or "") or None),
            }
            for f in files
        ],
    }


async def list_operations(
    *,
    session: AsyncSession,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    type_filter: str | None = None,
    category_id: int | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    q = select(FinanceOperation).order_by(FinanceOperation.occurred_at.desc(), FinanceOperation.id.desc())
    filters = []
    if date_from:
        filters.append(FinanceOperation.occurred_at >= date_from)
    if date_to:
        filters.append(FinanceOperation.occurred_at <= date_to)
    if type_filter:
        filters.append(FinanceOperation.type == str(type_filter))
    if category_id:
        filters.append(FinanceOperation.category_id == int(category_id))
    if search:
        pat = f"%{search}%"
        filters.append(or_(
            FinanceOperation.comment.ilike(pat),
            FinanceOperation.counterparty.ilike(pat),
        ))
    if filters:
        q = q.where(and_(*filters))

    count_q = select(func.count()).select_from(q.subquery())
    total = int((await session.execute(count_q)).scalar_one() or 0)
    rows = list((await session.execute(q.limit(int(limit)).offset(int(offset)))).scalars().all())
    return [_serialize_operation(r) for r in rows], total


async def get_operation(*, session: AsyncSession, operation_id: int) -> FinanceOperation | None:
    return (await session.execute(
        select(FinanceOperation).where(FinanceOperation.id == int(operation_id))
    )).scalars().first()


async def create_operation(
    *,
    session: AsyncSession,
    type: str,
    amount: Decimal,
    occurred_at: datetime,
    category_id: int | None,
    subcategory: str | None = None,
    counterparty: str | None = None,
    payment_method: str | None = None,
    comment: str | None = None,
    created_by_user_id: int | None = None,
    file_paths: list[str] | None = None,
    tg_file_ids: list[str] | None = None,
) -> FinanceOperation:
    type_v = str(type or "").strip().lower()
    if type_v not in ("income", "expense"):
        raise ValueError("invalid_type")
    amt = _Q2(amount)
    if amt <= 0:
        raise ValueError("invalid_amount")
    op = FinanceOperation(
        type=type_v,
        amount=amt,
        occurred_at=occurred_at,
        category_id=category_id,
        subcategory=(str(subcategory or "").strip() or None),
        counterparty=(str(counterparty or "").strip() or None),
        payment_method=(str(payment_method or "").strip() or None),
        comment=(str(comment or "").strip() or None),
        created_by_user_id=created_by_user_id,
    )
    session.add(op)
    await session.flush()

    for path in (file_paths or []):
        f = FinanceOperationFile(
            operation_id=int(op.id),
            file_path=str(path),
            file_url=str(path) if str(path).startswith("/") else None,
        )
        session.add(f)

    for tg_fid in (tg_file_ids or []):
        f = FinanceOperationFile(
            operation_id=int(op.id),
            file_path=f"tg:{tg_fid}",
            tg_file_id=str(tg_fid),
        )
        session.add(f)

    await session.flush()
    return op


async def update_operation(
    *,
    session: AsyncSession,
    operation_id: int,
    type: str | None = None,
    amount: Decimal | None = None,
    occurred_at: datetime | None = None,
    category_id: int | None = None,
    subcategory: str | None = None,
    counterparty: str | None = None,
    payment_method: str | None = None,
    comment: str | None = None,
) -> FinanceOperation:
    op = await get_operation(session=session, operation_id=operation_id)
    if op is None:
        raise ValueError("not_found")
    if type is not None:
        t = str(type).strip().lower()
        if t not in ("income", "expense"):
            raise ValueError("invalid_type")
        op.type = t
    if amount is not None:
        amt = _Q2(amount)
        if amt <= 0:
            raise ValueError("invalid_amount")
        op.amount = amt
    if occurred_at is not None:
        op.occurred_at = occurred_at
    if category_id is not None:
        op.category_id = category_id
    if subcategory is not None:
        op.subcategory = str(subcategory).strip() or None
    if counterparty is not None:
        op.counterparty = str(counterparty).strip() or None
    if payment_method is not None:
        op.payment_method = str(payment_method).strip() or None
    if comment is not None:
        op.comment = str(comment).strip() or None
    session.add(op)
    await session.flush()
    return op


async def delete_operation(*, session: AsyncSession, operation_id: int) -> None:
    op = await get_operation(session=session, operation_id=operation_id)
    if op is None:
        raise ValueError("not_found")
    await session.delete(op)
    await session.flush()


# ── Dashboard ─────────────────────────────────────────────────────────────────

@dataclass
class FinanceDashboard:
    income: Decimal
    expense: Decimal
    profit: Decimal
    avg_expense_per_day: Decimal
    avg_income_per_day: Decimal
    days_in_period: int
    days_with_data: int
    by_day: list[dict]
    expense_by_category: list[dict]
    income_by_category: list[dict]
    top_expense_categories: list[dict]
    top_income_categories: list[dict]


async def get_dashboard(
    *,
    session: AsyncSession,
    date_from: datetime,
    date_to: datetime,
) -> FinanceDashboard:
    base_filter = and_(
        FinanceOperation.occurred_at >= date_from,
        FinanceOperation.occurred_at <= date_to,
    )

    inc_row = (await session.execute(
        select(func.coalesce(func.sum(FinanceOperation.amount), 0))
        .where(base_filter)
        .where(FinanceOperation.type == "income")
    )).scalar_one()
    exp_row = (await session.execute(
        select(func.coalesce(func.sum(FinanceOperation.amount), 0))
        .where(base_filter)
        .where(FinanceOperation.type == "expense")
    )).scalar_one()

    income = _Q2(inc_row)
    expense = _Q2(exp_row)
    profit = _Q2(income - expense)

    from sqlalchemy import cast, Date as SADate, text
    day_rows = (await session.execute(
        select(
            cast(FinanceOperation.occurred_at, SADate).label("day"),
            FinanceOperation.type,
            func.sum(FinanceOperation.amount).label("total"),
        )
        .where(base_filter)
        .group_by(cast(FinanceOperation.occurred_at, SADate), FinanceOperation.type)
        .order_by(cast(FinanceOperation.occurred_at, SADate))
    )).all()

    by_day_map: dict[str, dict] = {}
    for row in day_rows:
        day_s = str(row.day)
        if day_s not in by_day_map:
            by_day_map[day_s] = {"day": day_s, "income": "0.00", "expense": "0.00"}
        if row.type == "income":
            by_day_map[day_s]["income"] = f"{_Q2(row.total)}"
        else:
            by_day_map[day_s]["expense"] = f"{_Q2(row.total)}"
    by_day = sorted(by_day_map.values(), key=lambda x: x["day"])

    from datetime import timedelta
    days_in_period = max(1, (date_to.date() - date_from.date()).days + 1)
    avg_expense = _Q2(expense / days_in_period)
    avg_income = _Q2(income / days_in_period)

    cat_rows = (await session.execute(
        select(
            FinanceCategory.id,
            FinanceCategory.name,
            func.sum(FinanceOperation.amount).label("total"),
        )
        .join(FinanceOperation, FinanceOperation.category_id == FinanceCategory.id)
        .where(base_filter)
        .where(FinanceOperation.type == "expense")
        .group_by(FinanceCategory.id, FinanceCategory.name)
        .order_by(func.sum(FinanceOperation.amount).desc())
    )).all()

    expense_by_category = [
        {"category_id": int(r.id), "category_name": str(r.name), "total": f"{_Q2(r.total)}"}
        for r in cat_rows
    ]
    top_expense_categories = expense_by_category[:5]

    inc_cat_rows = (await session.execute(
        select(
            FinanceCategory.id,
            FinanceCategory.name,
            func.sum(FinanceOperation.amount).label("total"),
        )
        .join(FinanceOperation, FinanceOperation.category_id == FinanceCategory.id)
        .where(base_filter)
        .where(FinanceOperation.type == "income")
        .group_by(FinanceCategory.id, FinanceCategory.name)
        .order_by(func.sum(FinanceOperation.amount).desc())
    )).all()
    income_by_category = [
        {"category_id": int(r.id), "category_name": str(r.name), "total": f"{_Q2(r.total)}"}
        for r in inc_cat_rows
    ]
    top_income_categories = income_by_category[:5]

    return FinanceDashboard(
        income=income,
        expense=expense,
        profit=profit,
        avg_expense_per_day=avg_expense,
        avg_income_per_day=avg_income,
        days_in_period=days_in_period,
        days_with_data=len(by_day),
        by_day=by_day,
        expense_by_category=expense_by_category,
        income_by_category=income_by_category,
        top_expense_categories=top_expense_categories,
        top_income_categories=top_income_categories,
    )


# ── Export helper (returns list of dicts for csv/excel callers) ───────────────

async def export_operations(
    *,
    session: AsyncSession,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    type_filter: str | None = None,
    category_id: int | None = None,
    search: str | None = None,
    fields: list[str] | None = None,
    aggregate_by: str | None = None,
) -> list[dict]:
    items, _ = await list_operations(
        session=session,
        date_from=date_from,
        date_to=date_to,
        type_filter=type_filter,
        category_id=category_id,
        search=search,
        limit=10000,
        offset=0,
    )
    if not aggregate_by:
        if fields:
            items = [{k: v for k, v in row.items() if k in fields} for row in items]
        return items

    agg: dict[str, dict] = {}
    for row in items:
        if aggregate_by == "category":
            key = str(row.get("category_name") or "Без категории")
        elif aggregate_by == "day":
            key = str(row.get("occurred_at") or "")[:10]
        else:
            key = str(row.get(aggregate_by) or "")
        if key not in agg:
            agg[key] = {aggregate_by: key, "income": Decimal("0"), "expense": Decimal("0")}
        t = str(row.get("type") or "")
        amt = _Q2(row.get("amount") or 0)
        if t == "income":
            agg[key]["income"] += amt
        else:
            agg[key]["expense"] += amt

    result = []
    for v in sorted(agg.values(), key=lambda x: str(x.get(aggregate_by) or "")):
        result.append({
            aggregate_by: v[aggregate_by],
            "income": f"{_Q2(v['income'])}",
            "expense": f"{_Q2(v['expense'])}",
            "profit": f"{_Q2(v['income'] - v['expense'])}",
        })
    return result


# ── Bot summaries ─────────────────────────────────────────────────────────────

async def get_summary(
    *,
    session: AsyncSession,
    date_from: datetime,
    date_to: datetime,
) -> dict:
    dash = await get_dashboard(session=session, date_from=date_from, date_to=date_to)

    cat_lines = []
    for c in dash.top_expense_categories[:3]:
        cat_lines.append(f"  • {c['category_name']}: {c['total']} ₽")

    return {
        "income": f"{dash.income}",
        "expense": f"{dash.expense}",
        "profit": f"{dash.profit}",
        "top_categories": cat_lines,
    }
