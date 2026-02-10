from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from shared.enums import Schedule, Position, PurchaseStatus


def approve_reject_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve:{user_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{user_id}"),
            ]
        ]
    )


def schedule_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=val.value, callback_data=f"schedule:{val.value}")]
        for val in (Schedule.TWO_TWO, Schedule.FIVE_TWO, Schedule.FOUR_THREE)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def position_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=val.value, callback_data=f"position:{val.value}")]
        for val in (Position.MANAGER, Position.PICKER, Position.PACKER, Position.MASTER, Position.DESIGNER)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def purchases_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="purchase:cancel")]]
    )


def purchases_priority_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Обычный", callback_data="purchase:priority:normal"),
                InlineKeyboardButton(text="🔥 Срочно", callback_data="purchase:priority:urgent"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="purchase:cancel")],
        ]
    )


def purchases_admin_kb(purchase_id: int) -> InlineKeyboardMarkup:
    # Backward-compat shim: default keyboard for NEW purchases.
    return purchases_workflow_kb(purchase_id=int(purchase_id), status=PurchaseStatus.NEW)


def purchases_workflow_kb(*, purchase_id: int, status: PurchaseStatus) -> InlineKeyboardMarkup | None:
    if status == PurchaseStatus.NEW:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="❌ Отменить", callback_data=f"purchase:{purchase_id}:cancel"),
                    InlineKeyboardButton(text="✅ Взять в работу", callback_data=f"purchase:{purchase_id}:take"),
                ]
            ]
        )
    if status == PurchaseStatus.IN_PROGRESS:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Куплено", callback_data=f"purchase:{purchase_id}:bought")]]
        )
    return None
