from __future__ import annotations

from datetime import datetime

from shared.enums import PurchaseStatus
from shared.utils import format_moscow


def _fio(u) -> str:
    if not u:
        return "—"
    name = (
        " ".join(
            [
                str(getattr(u, "first_name", "") or "").strip(),
                str(getattr(u, "last_name", "") or "").strip(),
            ]
        ).strip()
    )
    return name or f"#{int(getattr(u, 'id', 0) or 0)}"


def purchase_priority_human(priority: str | None) -> str:
    p = str(priority or "").strip().lower()
    if p == "urgent":
        return "🔥 Срочно"
    return "Обычный"


def purchase_status_ru(status: PurchaseStatus) -> str:
    if status == PurchaseStatus.NEW:
        return "Новые"
    if status == PurchaseStatus.IN_PROGRESS:
        return "В работе"
    if status == PurchaseStatus.BOUGHT:
        return "Куплено"
    if status == PurchaseStatus.CANCELED:
        return "Отменено"
    return "—"


def purchases_chat_message_text(*, user, purchase) -> str:
    created_dt = getattr(purchase, "created_at", None)
    created_str = format_moscow(created_dt) if isinstance(created_dt, datetime) else ""
    pr = purchase_priority_human(getattr(purchase, "priority", None))
    status_ru = purchase_status_ru(getattr(purchase, "status", PurchaseStatus.NEW))
    taken_by = getattr(purchase, "taken_by_user", None)
    bought_by = getattr(purchase, "bought_by_user", None)
    archived_by = getattr(purchase, "archived_by_user", None)

    txt = (
        f"🛒 <b>Закупка #{int(purchase.id)}</b>\n\n"
        f"🛒 <b>Что купить:</b> {getattr(purchase, 'text', None) or '—'}\n"
        f"⚡ <b>Приоритет:</b> {pr}\n"
        f"👤 <b>Кто создал:</b> {_fio(user)}\n"
        f"⏱ <b>Когда:</b> {created_str or '—'}\n"
        f"📌 <b>Статус:</b> {status_ru}"
    )

    if taken_by is not None:
        txt += f"\n🛠 <b>Взял в работу:</b> {_fio(taken_by)}"
    if bought_by is not None:
        txt += f"\n✅ <b>Купил:</b> {_fio(bought_by)}"
    if archived_by is not None and getattr(purchase, "status", None) in {PurchaseStatus.BOUGHT, PurchaseStatus.CANCELED}:
        txt += f"\n📦 <b>Закрыл:</b> {_fio(archived_by)}"
    return txt


def purchases_chat_kb_dict(*, purchase_id: int, status: PurchaseStatus | str) -> dict | None:
    st = status.value if hasattr(status, "value") else str(status or "")
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
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ Куплено", "callback_data": f"purchase:{int(purchase_id)}:bought"},
                ]
            ]
        }
    return None
