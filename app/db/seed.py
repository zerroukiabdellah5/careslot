"""Development-only database seed for CareSlot.

Populates the local SQLite database with fictional demo data so the API can
be exercised end to end. The seed is safe to run repeatedly: demo users are
matched by their documented email address, so existing accounts and their
password hashes are never overwritten, and every other record is inserted
only when it does not already exist.

DEVELOPMENT-ONLY LOGIN CREDENTIALS
----------------------------------
    role          email                     password
    ------------  ------------------------  ----------------
    admin         admin@careslot.dev        CareslotDev123!
    receptionist  reception@careslot.dev    CareslotDev123!
    doctor        dr.smith@careslot.dev     CareslotDev123!
    doctor        dr.lee@careslot.dev       CareslotDev123!

These accounts and passwords exist purely for local development. Never use
them in production or in any environment that handles real patient data.

Usage:
    python -m app.db.seed
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.init_db import init_db
from app.db.models import (
    Appointment,
    Patient,
    Practitioner,
    QueueEntry,
    User,
    WorkingHour,
    doctor_patients,
)
from app.db.session import SessionLocal

DEMO_PASSWORD = "CareslotDev123!"
DEMO_ANCHOR_DATE = date(2026, 1, 5)

DEMO_USERS = (
    {
        "email": "admin@careslot.dev",
        "full_name": "Alex Morgan",
        "role": "admin",
    },
    {
        "email": "reception@careslot.dev",
        "full_name": "Riley Chen",
        "role": "receptionist",
    },
    {
        "email": "dr.smith@careslot.dev",
        "full_name": "Dr. Sarah Smith",
        "role": "doctor",
        "specialty": "General Medicine",
    },
    {
        "email": "dr.lee@careslot.dev",
        "full_name": "Dr. David Lee",
        "role": "doctor",
        "specialty": "Cardiology",
    },
)

DEMO_PATIENTS = (
    {"full_name": "Emma Johnson", "phone": "5550000001"},
    {"full_name": "Liam Williams", "phone": "5550000002"},
    {"full_name": "Olivia Brown", "phone": "5550000003"},
    {"full_name": "Noah Davis", "phone": "5550000004"},
)

DEMO_WORKING_HOURS = {
    "dr.smith@careslot.dev": (
        (0, "09:00", "17:00"),
        (2, "09:00", "17:00"),
        (4, "09:00", "13:00"),
    ),
    "dr.lee@careslot.dev": (
        (1, "08:00", "16:00"),
        (3, "08:00", "16:00"),
    ),
}

DEMO_ASSOCIATIONS = {
    "dr.smith@careslot.dev": ("Emma Johnson", "Liam Williams", "Olivia Brown"),
    "dr.lee@careslot.dev": ("Liam Williams", "Noah Davis"),
}

DEMO_APPOINTMENTS = (
    {
        "doctor": "dr.smith@careslot.dev",
        "patient": "Emma Johnson",
        "start_at": datetime(2026, 1, 5, 9, 0),
        "end_at": datetime(2026, 1, 5, 9, 30),
        "status": "scheduled",
    },
    {
        "doctor": "dr.smith@careslot.dev",
        "patient": "Liam Williams",
        "start_at": datetime(2026, 1, 5, 10, 0),
        "end_at": datetime(2026, 1, 5, 10, 30),
        "status": "completed",
    },
    {
        "doctor": "dr.smith@careslot.dev",
        "patient": "Olivia Brown",
        "start_at": datetime(2026, 1, 7, 10, 0),
        "end_at": datetime(2026, 1, 7, 10, 30),
        "status": "no_show",
    },
    {
        "doctor": "dr.lee@careslot.dev",
        "patient": "Liam Williams",
        "start_at": datetime(2026, 1, 6, 8, 0),
        "end_at": datetime(2026, 1, 6, 8, 30),
        "status": "scheduled",
    },
    {
        "doctor": "dr.lee@careslot.dev",
        "patient": "Noah Davis",
        "start_at": datetime(2026, 1, 6, 9, 0),
        "end_at": datetime(2026, 1, 6, 9, 30),
        "status": "cancelled",
    },
    {
        "doctor": "dr.lee@careslot.dev",
        "patient": "Liam Williams",
        "start_at": datetime(2026, 1, 8, 9, 0),
        "end_at": datetime(2026, 1, 8, 10, 0),
        "status": "scheduled",
    },
)

DEMO_QUEUE_ENTRIES = (
    {
        "doctor": "dr.smith@careslot.dev",
        "patient": "Emma Johnson",
        "queue_date": date(2026, 1, 5),
        "position": 1,
        "status": "waiting",
        "appointment_start_at": datetime(2026, 1, 5, 9, 0),
    },
    {
        "doctor": "dr.smith@careslot.dev",
        "patient": "Liam Williams",
        "queue_date": date(2026, 1, 5),
        "position": 2,
        "status": "in_service",
        "appointment_start_at": datetime(2026, 1, 5, 10, 0),
    },
    {
        "doctor": "dr.lee@careslot.dev",
        "patient": "Liam Williams",
        "queue_date": date(2026, 1, 6),
        "position": 1,
        "status": "waiting",
        "appointment_start_at": datetime(2026, 1, 6, 8, 0),
    },
    {
        "doctor": "dr.lee@careslot.dev",
        "patient": "Noah Davis",
        "queue_date": date(2026, 1, 6),
        "position": 2,
        "status": "cancelled",
        "appointment_start_at": datetime(2026, 1, 6, 9, 0),
    },
)


def _get_or_create_user(db: Session, spec: dict) -> User:
    user = db.scalar(select(User).where(User.email == spec["email"]))
    if user is not None:
        return user

    user = User(
        full_name=spec["full_name"],
        email=spec["email"],
        password_hash=hash_password(DEMO_PASSWORD),
        role=spec["role"],
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _get_or_create_practitioner(
    db: Session, user: User, specialty: str | None
) -> Practitioner:
    practitioner = db.scalar(
        select(Practitioner).where(Practitioner.user_id == user.id)
    )
    if practitioner is not None:
        return practitioner

    practitioner = Practitioner(user_id=user.id, specialty=specialty)
    db.add(practitioner)
    db.flush()
    return practitioner


def _get_or_create_patient(
    db: Session, spec: dict, created_by_user_id: int
) -> Patient:
    patient = db.scalar(
        select(Patient).where(
            Patient.full_name == spec["full_name"],
            Patient.created_by_user_id == created_by_user_id,
        )
    )
    if patient is not None:
        return patient

    patient = Patient(
        full_name=spec["full_name"],
        phone=spec["phone"],
        created_by_user_id=created_by_user_id,
        is_deleted=False,
    )
    db.add(patient)
    db.flush()
    return patient


def _ensure_association(
    db: Session, practitioner: Practitioner, patient: Patient
) -> None:
    existing = db.execute(
        select(doctor_patients.c.practitioner_id).where(
            doctor_patients.c.practitioner_id == practitioner.id,
            doctor_patients.c.patient_id == patient.id,
        )
    ).first()
    if existing is not None:
        return

    db.execute(
        doctor_patients.insert().values(
            practitioner_id=practitioner.id,
            patient_id=patient.id,
        )
    )
    db.flush()


def _ensure_working_hour(
    db: Session,
    practitioner: Practitioner,
    weekday: int,
    start_time: str,
    end_time: str,
) -> None:
    existing = db.scalar(
        select(WorkingHour).where(
            WorkingHour.practitioner_id == practitioner.id,
            WorkingHour.weekday == weekday,
        )
    )
    if existing is not None:
        return

    db.add(
        WorkingHour(
            practitioner_id=practitioner.id,
            weekday=weekday,
            start_time=time.fromisoformat(start_time),
            end_time=time.fromisoformat(end_time),
        )
    )
    db.flush()


def _get_or_create_appointment(
    db: Session,
    patient: Patient,
    practitioner: Practitioner,
    spec: dict,
    created_by_user_id: int,
) -> Appointment:
    appointment = db.scalar(
        select(Appointment).where(
            Appointment.practitioner_id == practitioner.id,
            Appointment.start_at == spec["start_at"],
        )
    )
    if appointment is not None:
        return appointment

    appointment = Appointment(
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        start_at=spec["start_at"],
        end_at=spec["end_at"],
        status=spec["status"],
        created_by_user_id=created_by_user_id,
    )
    db.add(appointment)
    db.flush()
    return appointment


def _ensure_queue_entry(
    db: Session,
    practitioner: Practitioner,
    patient: Patient,
    spec: dict,
    appointment: Appointment | None,
) -> None:
    existing = db.scalar(
        select(QueueEntry).where(
            QueueEntry.practitioner_id == practitioner.id,
            QueueEntry.queue_date == spec["queue_date"],
            QueueEntry.position == spec["position"],
        )
    )
    if existing is not None:
        return

    db.add(
        QueueEntry(
            practitioner_id=practitioner.id,
            patient_id=patient.id,
            appointment_id=appointment.id if appointment is not None else None,
            queue_date=spec["queue_date"],
            position=spec["position"],
            status=spec["status"],
        )
    )
    db.flush()


def seed_database(db: Session) -> dict[str, int]:
    users: dict[str, User] = {}
    practitioners: dict[str, Practitioner] = {}

    for spec in DEMO_USERS:
        user = _get_or_create_user(db, spec)
        users[spec["email"]] = user
        if spec["role"] == "doctor":
            practitioners[spec["email"]] = _get_or_create_practitioner(
                db, user, spec.get("specialty")
            )

    admin = users["admin@careslot.dev"]

    patients: dict[str, Patient] = {}
    for spec in DEMO_PATIENTS:
        patients[spec["full_name"]] = _get_or_create_patient(db, spec, admin.id)

    for doctor_email, patient_names in DEMO_ASSOCIATIONS.items():
        practitioner = practitioners[doctor_email]
        for patient_name in patient_names:
            _ensure_association(db, practitioner, patients[patient_name])

    for doctor_email, shifts in DEMO_WORKING_HOURS.items():
        practitioner = practitioners[doctor_email]
        for weekday, start_time, end_time in shifts:
            _ensure_working_hour(db, practitioner, weekday, start_time, end_time)

    appointments: dict[tuple[str, str, datetime], Appointment] = {}
    for spec in DEMO_APPOINTMENTS:
        practitioner = practitioners[spec["doctor"]]
        patient = patients[spec["patient"]]
        appointment = _get_or_create_appointment(
            db, patient, practitioner, spec, admin.id
        )
        appointments[(spec["doctor"], spec["patient"], spec["start_at"])] = (
            appointment
        )

    for spec in DEMO_QUEUE_ENTRIES:
        practitioner = practitioners[spec["doctor"]]
        patient = patients[spec["patient"]]
        appointment = appointments.get(
            (spec["doctor"], spec["patient"], spec["appointment_start_at"])
        )
        _ensure_queue_entry(db, practitioner, patient, spec, appointment)

    db.commit()

    return {
        "users": db.query(User).count(),
        "practitioners": db.query(Practitioner).count(),
        "patients": db.query(Patient).count(),
        "doctor_patients": db.scalar(
            select(func.count()).select_from(doctor_patients)
        ),
        "working_hours": db.query(WorkingHour).count(),
        "appointments": db.query(Appointment).count(),
        "queue_entries": db.query(QueueEntry).count(),
    }


def main() -> None:
    init_db()
    with SessionLocal() as db:
        counts = seed_database(db)

    print("CareSlot development seed complete (idempotent).")
    for table, count in counts.items():
        print(f"  {table}: {count}")

    print("Demo login credentials (development only):")
    for spec in DEMO_USERS:
        print(
            f"  {spec['role']:<12} {spec['email']:<24} {DEMO_PASSWORD}"
        )


if __name__ == "__main__":
    main()
