from pydantic import BaseModel, Field
from typing import Optional
from datetime import date, datetime
from .enums import UserStatus, Schedule, Position


INT64_MAX = 2**63 - 1


class UserCreate(BaseModel):
    tg_id: int = Field(ge=0, le=INT64_MAX)
    first_name: str
    last_name: str
    birth_date: date
    rate_k: int
    schedule: Schedule
    position: Position


class UserOut(BaseModel):
    id: int
    tg_id: int = Field(ge=0, le=INT64_MAX)
    first_name: Optional[str]
    last_name: Optional[str]
    birth_date: Optional[date]
    rate_k: Optional[int]
    schedule: Optional[Schedule]
    position: Optional[Position]
    status: UserStatus
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    birth_date: Optional[date] = None
    rate_k: Optional[int] = None
    schedule: Optional[Schedule] = None
    position: Optional[Position] = None
    status: Optional[UserStatus] = None


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class BroadcastRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    user_ids: Optional[list[int]] = None
