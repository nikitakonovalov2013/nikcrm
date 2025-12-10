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
