from aiogram.fsm.state import StatesGroup, State


class StocksState(StatesGroup):
    choosing_material = State()
    waiting_amount = State()
    confirming = State()
