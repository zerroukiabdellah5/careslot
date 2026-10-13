from datetime import datetime, time as time_of_day, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_practitioner_for_user
from app.db.models import (
    Appointment,
    Patient,
    Practitioner,
    User,
    WorkingHour,
)
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentStatus,
    AppointmentUpdate,
)

router = APIRouter(prefix="/appointments", tags=["Appointments"])

ACTIVE_STATUSES = ("scheduled", "in_progress")


def _get_appointment(db: Session, appointment_id: int) -> Appointment:
    appointment = db.get(Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found",
        )
    return appointment


def _authorize_appointment(
    db: Session, user: User, appointment: Appointment
) -> None:
    if user.role == "doctor":
        practitioner = get_practitioner_for_user(db, user)
        if appointment.practitioner_id != practitioner.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this appointment",
            )


def _get_active_patient(db: Session, patient_id: int) -> Patient:
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.is_deleted.is_(False))
        .first()
    )
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found",
        )
    return patient


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


def _validate_time_range(start_at: datetime, end_at: datetime) -> None:
    if start_at >= end_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="start_at must be before end_at",
        )


def _minutes_from_midnight(value: datetime, day_start: datetime) -> float:
    return (value - day_start).total_seconds() / 60.0


def _validate_against_working_hours(
    db: Session,
    practitioner_id: int,
    start_at: datetime,
    end_at: datetime,
) -> None:
    start = start_at.replace(tzinfo=None)
    end = end_at.replace(tzinfo=None)

    day = start.date()
    while day <= end.date():
        day_start = datetime.combine(day, time_of_day.min)
        next_day_start = day_start + timedelta(days=1)
        seg_start = max(start, day_start)
        seg_end = min(end, next_day_start)

        working_hour = (
            db.query(WorkingHour)
            .filter(
                WorkingHour.practitioner_id == practitioner_id,
                WorkingHour.weekday == day.weekday(),
            )
            .first()
        )

        if working_hour is not None and seg_start < seg_end:
            open_minutes = (
                working_hour.start_time.hour * 60 + working_hour.start_time.minute
            )
            close_minutes = (
                working_hour.end_time.hour * 60 + working_hour.end_time.minute
            )
            offset_start = _minutes_from_midnight(seg_start, day_start)
            offset_end = _minutes_from_midnight(seg_end, day_start)
            if offset_start < open_minutes or offset_end > close_minutes:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Appointment must fall within the practitioner's configured working hours",
                )

        day += timedelta(days=1)


def _ensure_no_conflict(
    db: Session,
    practitioner_id: int,
    start_at: datetime,
    end_at: datetime,
    appointment_status: str,
    exclude_id: int | None = None,
) -> None:
    if appointment_status not in ACTIVE_STATUSES:
        return

    stmt = select(Appointment).where(
        Appointment.practitioner_id == practitioner_id,
        Appointment.status.in_(ACTIVE_STATUSES),
        Appointment.start_at < end_at,
        Appointment.end_at > start_at,
    )
    if exclude_id is not None:
        stmt = stmt.where(Appointment.id != exclude_id)

    if db.scalars(stmt).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Practitioner already has an appointment in this time range",
        )


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


@router.get("", response_model=list[AppointmentResponse])
def list_appointments(
    patient_id: int | None = None,
    practitioner_id: int | None = None,
    appointment_status: AppointmentStatus | None = Query(
        default=None, alias="status"
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Appointment]:
    stmt = select(Appointment).order_by(Appointment.start_at, Appointment.id)

    if current_user.role == "doctor":
        practitioner = get_practitioner_for_user(db, current_user)
        stmt = stmt.where(Appointment.practitioner_id == practitioner.id)
    elif practitioner_id is not None:
        stmt = stmt.where(Appointment.practitioner_id == practitioner_id)

    if patient_id is not None:
        stmt = stmt.where(Appointment.patient_id == patient_id)
    if appointment_status is not None:
        stmt = stmt.where(Appointment.status == appointment_status)

    return list(db.scalars(stmt).all())


@router.post(
    "", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED
)
def create_appointment(
    payload: AppointmentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Appointment:
    patient = _get_active_patient(db, payload.patient_id)
    practitioner_id = _resolve_practitioner_id(
        db, current_user, payload.practitioner_id
    )
    _validate_time_range(payload.start_at, payload.end_at)
    _validate_against_working_hours(
        db, practitioner_id, payload.start_at, payload.end_at
    )
    _ensure_no_conflict(
        db,
        practitioner_id,
        payload.start_at,
        payload.end_at,
        "scheduled",
    )

    appointment = Appointment(
        patient_id=patient.id,
        practitioner_id=practitioner_id,
        start_at=payload.start_at,
        end_at=payload.end_at,
        status="scheduled",
        created_by_user_id=current_user.id,
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return appointment


@router.get("/{appointment_id}", response_model=AppointmentResponse)
def get_appointment(
    appointment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Appointment:
    appointment = _get_appointment(db, appointment_id)
    _authorize_appointment(db, current_user, appointment)
    return appointment


@router.patch("/{appointment_id}", response_model=AppointmentResponse)
def update_appointment(
    appointment_id: int,
    payload: AppointmentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Appointment:
    appointment = _get_appointment(db, appointment_id)
    _authorize_appointment(db, current_user, appointment)

    if current_user.role == "doctor":
        practitioner = get_practitioner_for_user(db, current_user)
        if (
            payload.practitioner_id is not None
            and payload.practitioner_id != practitioner.id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot reassign an appointment to another practitioner",
            )
        practitioner_id = practitioner.id
    elif payload.practitioner_id is not None:
        practitioner_id = _get_practitioner(db, payload.practitioner_id).id
    else:
        practitioner_id = appointment.practitioner_id

    patient_id = appointment.patient_id
    if payload.patient_id is not None:
        patient_id = _get_active_patient(db, payload.patient_id).id

    start_at = (
        payload.start_at if payload.start_at is not None else appointment.start_at
    )
    end_at = payload.end_at if payload.end_at is not None else appointment.end_at
    new_status = (
        payload.status if payload.status is not None else appointment.status
    )

    _validate_time_range(start_at, end_at)
    if (
        payload.start_at is not None
        or payload.end_at is not None
        or payload.practitioner_id is not None
    ):
        _validate_against_working_hours(db, practitioner_id, start_at, end_at)
    _ensure_no_conflict(
        db,
        practitioner_id,
        start_at,
        end_at,
        new_status,
        exclude_id=appointment.id,
    )

    appointment.patient_id = patient_id
    appointment.practitioner_id = practitioner_id
    appointment.start_at = start_at
    appointment.end_at = end_at
    appointment.status = new_status
    db.commit()
    db.refresh(appointment)
    return appointment


@router.post("/{appointment_id}/cancel", response_model=AppointmentResponse)
def cancel_appointment(
    appointment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Appointment:
    appointment = _get_appointment(db, appointment_id)
    _authorize_appointment(db, current_user, appointment)

    appointment.status = "cancelled"
    db.commit()
    db.refresh(appointment)
    return appointment
