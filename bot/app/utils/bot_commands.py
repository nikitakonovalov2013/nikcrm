from __future__ import annotations

from aiogram.types import BotCommand, BotCommandScopeChat

from shared.enums import Position, UserStatus


def _commands_for(*, is_admin: bool, status: UserStatus | None, position: Position | None) -> list[BotCommand]:
    cmds: list[BotCommand] = [
        BotCommand(command="start", description="Запуск бота"),
        BotCommand(command="profile", description="Профиль"),
    ]

    if status in (None, UserStatus.PENDING, UserStatus.REJECTED):
        cmds.insert(1, BotCommand(command="register", description="Зарегистрироваться"))

    if is_admin or status == UserStatus.APPROVED:
        cmds.append(BotCommand(command="purchases", description="Закупки"))

        cmds.append(BotCommand(command="tasks", description="Задачи"))
        

    can_stocks = is_admin or (status == UserStatus.APPROVED and position in {Position.MANAGER, Position.MASTER})
    if can_stocks:
        cmds.append(BotCommand(command="stocks", description="Остатки"))

    can_reports = is_admin or (status == UserStatus.APPROVED and position == Position.MANAGER)
    if can_reports:
        cmds.append(BotCommand(command="reports", description="Отчёты и напоминания"))

    if is_admin:
        cmds.append(BotCommand(command="admin", description="Админ-панель"))

    return cmds


async def sync_commands_for_chat(*, bot, chat_id: int, is_admin: bool, status: UserStatus | None, position: Position | None) -> None:
    await bot.set_my_commands(
        commands=_commands_for(is_admin=is_admin, status=status, position=position),
        scope=BotCommandScopeChat(chat_id=chat_id),
    )
