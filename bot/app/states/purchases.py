from aiogram.fsm.state import StatesGroup, State


class PurchasesState(StatesGroup):
    waiting_input = State()
    waiting_text_after_photo = State()
