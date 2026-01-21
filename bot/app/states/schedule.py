from aiogram.fsm.state import StatesGroup, State


class ScheduleEmergencyState(StatesGroup):
    pick_time = State()
    input_time = State()
    pick_date_mode = State()
    input_date = State()
    input_comment = State()
    confirm = State()
