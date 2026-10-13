from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

AppointmentStatus = Literal[
    "scheduled",
    "in_progress",
    "completed",
    "cancelled",
    "no_show",
]


class AppointmentCreate(BaseModel):
    patient_id: int
    practitioner_id: int | None = None
    start_at: datetime
    end_at: datetime


class AppointmentUpdate(BaseModel):
    patient_id: int | None = None
    practitioner_id: int | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    status: AppointmentStatus | None = None


class AppointmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    practitioner_id: int
    start_at: datetime
    end_at: datetime
    status: str
    created_by_user_id: int
    created_at: datetime
