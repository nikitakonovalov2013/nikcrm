from aiogram.fsm.state import StatesGroup, State


class ReportsRemindersState(StatesGroup):
    period_from = State()
    period_to = State()
    reminder_time = State()
    daily_report_time = State()
