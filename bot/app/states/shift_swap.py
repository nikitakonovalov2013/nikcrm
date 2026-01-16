from aiogram.fsm.state import StatesGroup, State


class ShiftSwapCreateState(StatesGroup):
    reason = State()
    bonus_choice = State()
    bonus_custom = State()
    confirm = State()
