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
    created_ddmm = format_moscow(created_dt, "%d.%m") if isinstance(created_dt, datetime) else ""
    created_hhmm = format_moscow(created_dt, "%H:%M") if isinstance(created_dt, datetime) else ""
    pr_raw = str(getattr(purchase, "priority", None) or "").strip().lower()
    emoji = "🔥" if pr_raw == "urgent" else "🛒"
    author = _fio(user)
    purchase_id = int(getattr(purchase, "id", 0) or 0)
    purchase_text = str(getattr(purchase, "text", None) or "—")
    desc = str(getattr(purchase, "description", None) or "").strip()

    header = f"{emoji} {author} создал(а) #{purchase_id}: {purchase_text}".strip()
    when_line = f"{created_ddmm} в {created_hhmm}".strip()

    if desc:
        return f"{header}\n{desc}\n\n{when_line}".strip()
    return f"{header}\n\n{when_line}".strip()


def purchase_created_user_message(*, purchase_id: int) -> str:
    return (
        f"✅ Успешно! Спасибо, закупка № {int(purchase_id)} создана.\n\n"
        "Ваш запрос получен и отправлен руководству! При изменении\n"
        "статуса у заявки вы получите уведомление. 🔔"
    )


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
