from datetime import time

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkingHourCreate(BaseModel):
    practitioner_id: int | None = None
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def check_time_range(self) -> "WorkingHourCreate":
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be earlier than end_time")
        return self


class WorkingHourUpdate(BaseModel):
    practitioner_id: int | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    start_time: time | None = None
    end_time: time | None = None

    @model_validator(mode="after")
    def check_time_range(self) -> "WorkingHourUpdate":
        if self.start_time is not None and self.end_time is not None:
            if self.start_time >= self.end_time:
                raise ValueError("start_time must be earlier than end_time")
        return self


class WorkingHourResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    practitioner_id: int
    weekday: int
    start_time: time
    end_time: time