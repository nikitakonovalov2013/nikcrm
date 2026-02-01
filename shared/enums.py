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
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    BOUGHT = "BOUGHT"
    CANCELED = "CANCELED"


class TaskStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    ARCHIVED = "archived"


class TaskPriority(StrEnum):
    NORMAL = "normal"
    URGENT = "urgent"

    FREE_TIME = "free_time"


class TaskEventType(StrEnum):
    CREATED = "created"
    ASSIGNED_ADDED = "assigned_added"
    ASSIGNED_REMOVED = "assigned_removed"
    EDITED = "edited"
    STATUS_CHANGED = "status_changed"
    COMMENT_ADDED = "comment_added"
    ARCHIVED = "archived"
    UNARCHIVED = "unarchived"


class ShiftInstanceStatus(StrEnum):
    PLANNED = "planned"
    STARTED = "started"
    CLOSED = "closed"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REWORK = "needs_rework"


class ShiftSwapRequestStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
