from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def tasks_root_kb(*, can_view_all: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append(
        [
            InlineKeyboardButton(text="➕ Новая задача", callback_data="tasks:new"),
            InlineKeyboardButton(text="👤 Мои задачи", callback_data="tasks:mine"),
        ]
    )
    if can_view_all:
        rows.append([InlineKeyboardButton(text="📋 Все задачи", callback_data="tasks:all")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="tasks:back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tasks_status_kb(*, scope: str, can_view_archive: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append(
        [
            InlineKeyboardButton(text="Новые", callback_data=f"tasks:status:{scope}:new"),
            InlineKeyboardButton(text="В работе", callback_data=f"tasks:status:{scope}:in_progress"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="На проверке", callback_data=f"tasks:status:{scope}:review"),
            InlineKeyboardButton(text="Выполнено", callback_data=f"tasks:status:{scope}:done"),
        ]
    )

    last_row: list[InlineKeyboardButton] = []
    if can_view_archive:
        last_row.append(InlineKeyboardButton(text="Архив", callback_data=f"tasks:status:{scope}:archived"))
    last_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="tasks:menu"))
    rows.append(last_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tasks_list_kb(
    *,
    scope: str,
    status: str,
    page: int,
    items: list[tuple[int, str]],
    has_prev: bool,
    has_next: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for task_id, title in items:
        rows.append([InlineKeyboardButton(text=title, callback_data=f"tasks:open:{task_id}:{scope}:{status}:{page}")])

    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"tasks:list:{scope}:{status}:{page-1}"))
    nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"tasks:{scope}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"tasks:list:{scope}:{status}:{page+1}"))
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
    back_cb: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    main: list[InlineKeyboardButton] = []

    if can_take:
        main.append(InlineKeyboardButton(text="▶️ Взять в работу", callback_data=f"tasks:chg:{task_id}:in_progress"))
    if can_to_review:
        main.append(InlineKeyboardButton(text="✅ На проверку", callback_data=f"tasks:chg:{task_id}:review"))
    if can_accept_done:
        main.append(InlineKeyboardButton(text="✅ Принять", callback_data=f"tasks:chg:{task_id}:done"))
    if can_send_back:
        main.append(InlineKeyboardButton(text="↩️ На доработку", callback_data=f"tasks:rework:{task_id}"))

    if main:
        rows.append(main[:4])

    back_callback = "tasks:menu"
    if back_cb is not None:
        back_callback = str(back_cb)
    elif back_kind is not None and back_page is not None:
        back_callback = f"tasks:list:{back_kind}:{int(back_page)}"

    rows.append(
        [
            InlineKeyboardButton(text="💬 Комментарий", callback_data=f"tasks:comment:{task_id}"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback),
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


def tasks_create_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="tasks:create_cancel")]])


def tasks_create_desc_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="tasks:create_desc_skip")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="tasks:create_cancel")],
        ]
    )


def tasks_create_photo_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="tasks:create_photo_skip")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="tasks:create_cancel")],
        ]
    )


def tasks_create_priority_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Обычная", callback_data="tasks:create_priority:normal"),
                InlineKeyboardButton(text="🔥 Срочная", callback_data="tasks:create_priority:urgent"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="tasks:create_cancel")],
        ]
    )


def tasks_create_due_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Без дедлайна", callback_data="tasks:create_due:none")],
            [
                InlineKeyboardButton(text="Сегодня до 18:00", callback_data="tasks:create_due:today18"),
                InlineKeyboardButton(text="Сегодня до 21:00", callback_data="tasks:create_due:today21"),
            ],
            [
                InlineKeyboardButton(text="Завтра до 18:00", callback_data="tasks:create_due:tomorrow18"),
                InlineKeyboardButton(text="Завтра до 21:00", callback_data="tasks:create_due:tomorrow21"),
            ],
            [
                InlineKeyboardButton(text="До конца недели", callback_data="tasks:create_due:eow"),
                InlineKeyboardButton(text="До конца месяца", callback_data="tasks:create_due:eom"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="tasks:create_back_priority"),
                InlineKeyboardButton(text="❌ Отменить", callback_data="tasks:create_cancel"),
            ],
        ]
    )


def tasks_create_assignees_kb(
    *,
    users: list[tuple[int, str]],
    selected_ids: set[int],
    page: int,
    has_prev: bool,
    has_next: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for uid, name in users:
        prefix = "✅ " if int(uid) in selected_ids else "☑️ "
        rows.append([InlineKeyboardButton(text=prefix + str(name), callback_data=f"tasks:create_assignee:{int(uid)}")])

    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"tasks:create_assignees_page:{int(page)-1}"))
    nav.append(InlineKeyboardButton(text="Готово", callback_data="tasks:create_assignees_done"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"tasks:create_assignees_page:{int(page)+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="tasks:create_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tasks_create_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Создать", callback_data="tasks:create_confirm")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="tasks:create_back_assignees")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="tasks:create_cancel")],
        ]
    )
