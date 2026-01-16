from aiogram.fsm.state import StatesGroup, State


class ShiftCloseEditState(StatesGroup):
    extra_hours = State()
    overtime_hours = State()
    amount = State()
    comment = State()


class ShiftManagerEditState(StatesGroup):
    amount = State()
    comment = State()
