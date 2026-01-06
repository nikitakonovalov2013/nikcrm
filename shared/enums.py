from enum import StrEnum


class UserStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    BLACKLISTED = "BLACKLISTED"


class Schedule(StrEnum):
    TWO_TWO = "2/2"
    FIVE_TWO = "5/2"
    FOUR_THREE = "4/3"


class Position(StrEnum):
    MANAGER = "Руководитель"
    PICKER = "Сборщик заказов"
    PACKER = "Упаковщик"
    MASTER = "Мастер"


class AdminActionType(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    BLACKLIST = "BLACKLIST"
    EDIT = "EDIT"
    MESSAGE = "MESSAGE"
    BROADCAST = "BROADCAST"


class PurchaseStatus(StrEnum):
    PENDING = "PENDING"
    DONE = "DONE"
    REJECTED = "REJECTED"


class TaskStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    ARCHIVED = "archived"


class TaskPriority(StrEnum):
    NORMAL = "normal"
    URGENT = "urgent"


class TaskEventType(StrEnum):
    CREATED = "created"
    ASSIGNED_ADDED = "assigned_added"
    ASSIGNED_REMOVED = "assigned_removed"
    STATUS_CHANGED = "status_changed"
    COMMENT_ADDED = "comment_added"
    ARCHIVED = "archived"
    UNARCHIVED = "unarchived"
