from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

QueueEntryStatus = Literal["waiting", "in_service", "completed", "cancelled"]


class QueueEntryCreate(BaseModel):
    patient_id: int
    practitioner_id: int | None = None
    queue_date: date | None = None
    appointment_id: int | None = None


class QueueEntryUpdate(BaseModel):
    status: QueueEntryStatus | None = None


class QueueEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    practitioner_id: int
    patient_id: int
    appointment_id: int | None
    queue_date: date
    position: int
    status: str