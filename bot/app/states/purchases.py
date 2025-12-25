from aiogram.fsm.state import StatesGroup, State


class PurchasesState(StatesGroup):
    waiting_text = State()
