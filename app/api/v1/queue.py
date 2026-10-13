from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_practitioner_for_user
from app.db.models import (
    Appointment,
    Patient,
    Practitioner,
    QueueEntry,
    User,
    doctor_patients,
)
from app.schemas.queue_entry import (
    QueueEntryCreate,
    QueueEntryResponse,
    QueueEntryStatus,
    QueueEntryUpdate,
)

router = APIRouter(prefix="/queue", tags=["Queue"])

ACTIVE_STATUSES = ("waiting", "in_service")
TERMINAL_STATUSES = ("completed", "cancelled")


def _get_entry(db: Session, entry_id: int) -> QueueEntry:
    entry = db.get(QueueEntry, entry_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Queue entry not found",
        )
    return entry


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


def _patient_belongs_to_practitioner(
    db: Session, patient_id: int, practitioner_id: int
) -> bool:
    row = db.execute(
        select(doctor_patients.c.practitioner_id).where(
            doctor_patients.c.patient_id == patient_id,
            doctor_patients.c.practitioner_id == practitioner_id,
        )
    ).first()
    return row is not None


def _authorize_entry(db: Session, user: User, entry: QueueEntry) -> None:
    if user.role == "doctor":
        practitioner = get_practitioner_for_user(db, user)
        if entry.practitioner_id != practitioner.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this queue entry",
            )
        if not _patient_belongs_to_practitioner(db, entry.patient_id, practitioner.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this queue entry",
            )


def _ensure_no_active_duplicate(
    db: Session,
    patient_id: int,
    practitioner_id: int,
    queue_date: date,
    exclude_id: int | None = None,
) -> None:
    stmt = select(QueueEntry).where(
        QueueEntry.patient_id == patient_id,
        QueueEntry.practitioner_id == practitioner_id,
        QueueEntry.queue_date == queue_date,
        QueueEntry.status.in_(ACTIVE_STATUSES),
    )
    if exclude_id is not None:
        stmt = stmt.where(QueueEntry.id != exclude_id)

    if db.scalars(stmt).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Patient is already in the queue for this practitioner on this date",
        )


def _next_position(db: Session, practitioner_id: int, queue_date: date) -> int:
    current_max = (
        db.query(func.max(QueueEntry.position))
        .filter(
            QueueEntry.practitioner_id == practitioner_id,
            QueueEntry.queue_date == queue_date,
        )
        .scalar()
    )
    return (current_max or 0) + 1


def _validate_appointment_link(
    db: Session, appointment_id: int, patient_id: int, practitioner_id: int
) -> None:
    appointment = db.get(Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found",
        )
    if appointment.practitioner_id != practitioner_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Appointment does not belong to the selected practitioner",
        )
    if appointment.patient_id != patient_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Appointment does not belong to the selected patient",
        )


def _ensure_status_update_allowed(
    current_status: str, new_status: str
) -> None:
    if current_status in TERMINAL_STATUSES and new_status != current_status:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Completed or cancelled queue entries cannot be changed",
        )


@router.get("", response_model=list[QueueEntryResponse])
def list_queue_entries(
    patient_id: int | None = None,
    practitioner_id: int | None = None,
    queue_date: date | None = None,
    entry_status: QueueEntryStatus | None = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[QueueEntry]:
    stmt = select(QueueEntry).order_by(
        QueueEntry.queue_date, QueueEntry.position, QueueEntry.id
    )

    if current_user.role == "doctor":
        practitioner = get_practitioner_for_user(db, current_user)
        stmt = stmt.join(
            doctor_patients,
            doctor_patients.c.patient_id == QueueEntry.patient_id,
        ).where(
            QueueEntry.practitioner_id == practitioner.id,
            doctor_patients.c.practitioner_id == practitioner.id,
        )
    elif practitioner_id is not None:
        stmt = stmt.where(QueueEntry.practitioner_id == practitioner_id)

    if patient_id is not None:
        stmt = stmt.where(QueueEntry.patient_id == patient_id)
    if queue_date is not None:
        stmt = stmt.where(QueueEntry.queue_date == queue_date)
    if entry_status is not None:
        stmt = stmt.where(QueueEntry.status == entry_status)

    return list(db.scalars(stmt).all())


@router.post("", response_model=QueueEntryResponse, status_code=status.HTTP_201_CREATED)
def add_queue_entry(
    payload: QueueEntryCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QueueEntry:
    practitioner_id = _resolve_practitioner_id(
        db, current_user, payload.practitioner_id
    )
    patient = _get_active_patient(db, payload.patient_id)

    if current_user.role == "doctor" and not _patient_belongs_to_practitioner(
        db, patient.id, practitioner_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this patient",
        )

    queue_date = payload.queue_date or date.today()
    if payload.appointment_id is not None:
        _validate_appointment_link(
            db, payload.appointment_id, patient.id, practitioner_id
        )

    _ensure_no_active_duplicate(db, patient.id, practitioner_id, queue_date)

    entry = QueueEntry(
        practitioner_id=practitioner_id,
        patient_id=patient.id,
        appointment_id=payload.appointment_id,
        queue_date=queue_date,
        position=_next_position(db, practitioner_id, queue_date),
        status="waiting",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/{entry_id}", response_model=QueueEntryResponse)
def get_queue_entry(
    entry_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QueueEntry:
    entry = _get_entry(db, entry_id)
    _authorize_entry(db, current_user, entry)
    return entry


@router.patch("/{entry_id}", response_model=QueueEntryResponse)
def update_queue_entry(
    entry_id: int,
    payload: QueueEntryUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QueueEntry:
    entry = _get_entry(db, entry_id)
    _authorize_entry(db, current_user, entry)

    if payload.status is not None:
        _ensure_status_update_allowed(entry.status, payload.status)
        entry.status = payload.status
        db.commit()
        db.refresh(entry)

    return entry


@router.post("/{entry_id}/cancel", response_model=QueueEntryResponse)
def cancel_queue_entry(
    entry_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QueueEntry:
    entry = _get_entry(db, entry_id)
    _authorize_entry(db, current_user, entry)

    if entry.status == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Completed queue entries cannot be cancelled",
        )

    entry.status = "cancelled"
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_queue_entry(
    entry_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    entry = _get_entry(db, entry_id)
    _authorize_entry(db, current_user, entry)

    db.delete(entry)
    db.commit()