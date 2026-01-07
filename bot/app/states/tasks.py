from aiogram.fsm.state import StatesGroup, State


class TasksState(StatesGroup):
    comment_text = State()
    comment_photos = State()
    rework_text = State()
