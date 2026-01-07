from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def tasks_root_kb(*, can_view_archive: bool, can_view_all: bool) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text="Мои задачи", callback_data="tasks:list:my:0"),
        InlineKeyboardButton(text="Новые (общие)", callback_data="tasks:list:available:0"),
        InlineKeyboardButton(text="В работе", callback_data="tasks:list:in_progress:0"),
        InlineKeyboardButton(text="На проверке", callback_data="tasks:list:review:0"),
        InlineKeyboardButton(text="Выполнено", callback_data="tasks:list:done:0"),
    ]
    if can_view_archive:
        buttons.append(InlineKeyboardButton(text="Архив", callback_data="tasks:list:archived:0"))
    if can_view_all:
        buttons.append(InlineKeyboardButton(text="Все задачи", callback_data="tasks:list:all:0"))

    rows: list[list[InlineKeyboardButton]] = []
    i = 0
    while i < len(buttons):
        row = buttons[i : i + 2]
        rows.append(row)
        i += 2

    return InlineKeyboardMarkup(inline_keyboard=rows)


def tasks_list_kb(*, kind: str, page: int, items: list[tuple[int, str]], has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for task_id, title in items:
        rows.append([InlineKeyboardButton(text=title, callback_data=f"tasks:open:{task_id}:{kind}:{page}")])

    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"tasks:list:{kind}:{page-1}"))
    nav.append(InlineKeyboardButton(text="⬅️ Меню", callback_data="tasks:menu"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"tasks:list:{kind}:{page+1}"))
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def task_detail_kb(
    *,
    task_id: int,
    can_take: bool,
    can_to_review: bool,
    can_accept_done: bool,
    can_send_back: bool,
    back_kind: str | None = None,
    back_page: int | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    main: list[InlineKeyboardButton] = []

    if can_take:
        main.append(InlineKeyboardButton(text="▶️ Взять в работу", callback_data=f"tasks:status:{task_id}:in_progress"))
    if can_to_review:
        main.append(InlineKeyboardButton(text="✅ На проверку", callback_data=f"tasks:status:{task_id}:review"))
    if can_accept_done:
        main.append(InlineKeyboardButton(text="✅ Принять", callback_data=f"tasks:status:{task_id}:done"))
    if can_send_back:
        main.append(InlineKeyboardButton(text="↩️ На доработку", callback_data=f"tasks:rework:{task_id}"))

    if main:
        rows.append(main[:4])

    back_cb = "tasks:menu"
    if back_kind is not None and back_page is not None:
        back_cb = f"tasks:list:{back_kind}:{int(back_page)}"

    rows.append(
        [
            InlineKeyboardButton(text="💬 Комментарий", callback_data=f"tasks:comment:{task_id}"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def tasks_skip_photos_kb(*, allow_done: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if allow_done:
        rows.append([InlineKeyboardButton(text="Готово", callback_data="tasks:comment_done")])
    rows.append([InlineKeyboardButton(text="Пропустить", callback_data="tasks:comment_skip")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="tasks:comment_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tasks_text_cancel_kb(*, task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отменить", callback_data=f"tasks:cancel_text:{int(task_id)}")]]
    )
