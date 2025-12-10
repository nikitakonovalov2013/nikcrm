from aiogram.fsm.state import StatesGroup, State


class RegistrationState(StatesGroup):
    first_name = State()
    last_name = State()
    birth_date = State()
    rate_k = State()
    schedule = State()
    position = State()
