from aiogram.fsm.state import StatesGroup, State


class TasksState(StatesGroup):
    create_title = State()
    create_description = State()
    create_photo = State()
    create_priority = State()
    create_due = State()
    create_assignees = State()
    create_confirm = State()

    edit_menu = State()
    edit_title = State()
    edit_description = State()
    edit_priority = State()
    edit_due = State()
    edit_assignees = State()
    edit_photo = State()

    comment_text = State()
    comment_photos = State()
    rework_text = State()
