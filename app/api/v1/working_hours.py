from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_practitioner_for_user
from app.db.models import Practitioner, User, WorkingHour
from app.schemas.working_hour import (
    WorkingHourCreate,
    WorkingHourResponse,
    WorkingHourUpdate,
)

router = APIRouter(prefix="/working_hours", tags=["Working Hours"])


def _get_working_hour(db: Session, working_hour_id: int) -> WorkingHour:
    working_hour = db.get(WorkingHour, working_hour_id)
    if working_hour is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Working hour not found",
        )
    return working_hour


def _authorize_working_hour(
    db: Session, user: User, working_hour: WorkingHour
) -> None:
    if user.role == "doctor":
        practitioner = get_practitioner_for_user(db, user)
        if working_hour.practitioner_id != practitioner.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this working hour",
            )


def _get_practitioner(db: Session, practitioner_id: int) -> Practitioner:
    practitioner = (
        db.query(Practitioner)
        .filter(Practitioner.id == practitioner_id)
        .first()
    )
    if practitioner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Practitioner not found",
        )
    return practitioner


def _resolve_practitioner_id(
    db: Session, user: User, requested_id: int | None
) -> int:
    if user.role == "doctor":
        return get_practitioner_for_user(db, user).id
    if requested_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="practitioner_id is required",
        )
    return _get_practitioner(db, requested_id).id


def _ensure_no_duplicate_weekday(
    db: Session,
    practitioner_id: int,
    weekday: int,
    exclude_id: int | None = None,
) -> None:
    stmt = select(WorkingHour).where(
        WorkingHour.practitioner_id == practitioner_id,
        WorkingHour.weekday == weekday,
    )
    if exclude_id is not None:
        stmt = stmt.where(WorkingHour.id != exclude_id)

    if db.scalars(stmt).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A working-hour record already exists for this practitioner on this weekday",
        )


@router.get("", response_model=list[WorkingHourResponse])
def list_working_hours(
    practitioner_id: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WorkingHour]:
    stmt = select(WorkingHour).order_by(
        WorkingHour.practitioner_id, WorkingHour.weekday, WorkingHour.id
    )

    if current_user.role == "doctor":
        practitioner = get_practitioner_for_user(db, current_user)
        stmt = stmt.where(WorkingHour.practitioner_id == practitioner.id)
    elif practitioner_id is not None:
        stmt = stmt.where(WorkingHour.practitioner_id == practitioner_id)

    return list(db.scalars(stmt).all())


@router.post(
    "", response_model=WorkingHourResponse, status_code=status.HTTP_201_CREATED
)
def create_working_hour(
    payload: WorkingHourCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkingHour:
    practitioner_id = _resolve_practitioner_id(
        db, current_user, payload.practitioner_id
    )
    _ensure_no_duplicate_weekday(db, practitioner_id, payload.weekday)

    working_hour = WorkingHour(
        practitioner_id=practitioner_id,
        weekday=payload.weekday,
        start_time=payload.start_time,
        end_time=payload.end_time,
    )
    db.add(working_hour)
    db.commit()
    db.refresh(working_hour)
    return working_hour


@router.get("/{working_hour_id}", response_model=WorkingHourResponse)
def get_working_hour(
    working_hour_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkingHour:
    working_hour = _get_working_hour(db, working_hour_id)
    _authorize_working_hour(db, current_user, working_hour)
    return working_hour


@router.patch("/{working_hour_id}", response_model=WorkingHourResponse)
def update_working_hour(
    working_hour_id: int,
    payload: WorkingHourUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkingHour:
    working_hour = _get_working_hour(db, working_hour_id)
    _authorize_working_hour(db, current_user, working_hour)

    if current_user.role == "doctor":
        practitioner = get_practitioner_for_user(db, current_user)
        if (
            payload.practitioner_id is not None
            and payload.practitioner_id != practitioner.id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot reassign a working hour to another practitioner",
            )
        practitioner_id = practitioner.id
    elif payload.practitioner_id is not None:
        practitioner_id = _get_practitioner(db, payload.practitioner_id).id
    else:
        practitioner_id = working_hour.practitioner_id

    weekday = (
        payload.weekday if payload.weekday is not None else working_hour.weekday
    )
    start_time = (
        payload.start_time
        if payload.start_time is not None
        else working_hour.start_time
    )
    end_time = (
        payload.end_time if payload.end_time is not None else working_hour.end_time
    )

    _ensure_no_duplicate_weekday(
        db, practitioner_id, weekday, exclude_id=working_hour.id
    )

    working_hour.practitioner_id = practitioner_id
    working_hour.weekday = weekday
    working_hour.start_time = start_time
    working_hour.end_time = end_time
    db.commit()
    db.refresh(working_hour)
    return working_hour


@router.delete("/{working_hour_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_working_hour(
    working_hour_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    working_hour = _get_working_hour(db, working_hour_id)
    _authorize_working_hour(db, current_user, working_hour)

    db.delete(working_hour)
    db.commit()