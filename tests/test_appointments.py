from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import (
    Appointment,
    Patient,
    Practitioner,
    User,
    WorkingHour,
    doctor_patients,
)
from app.main import app

PASSWORD = "CorrectPassword123!"
BASE_TIME = datetime(2026, 1, 5, 9, 0, 0)


@pytest.fixture
def client_and_db(monkeypatch):
    monkeypatch.setenv(
        "JWT_SECRET_KEY",
        "test-secret-key-for-careslot-appointments-tests-min-32-char",
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    def override_get_db():
        request_db = TestingSessionLocal()
        try:
            yield request_db
        finally:
            request_db.close()

    app.dependency_overrides[get_db] = override_get_db

    try:
        yield TestClient(app), db
    finally:
        app.dependency_overrides.clear()
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def create_user(db, full_name, email, role):
    user = User(
        full_name=full_name,
        email=email,
        password_hash=hash_password(PASSWORD),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_practitioner(db, user, specialty="General"):
    practitioner = Practitioner(user_id=user.id, specialty=specialty)
    db.add(practitioner)
    db.commit()
    db.refresh(practitioner)
    return practitioner


def create_patient(db, full_name="Jean Dupont", phone="0600000000", created_by=None):
    patient = Patient(
        full_name=full_name,
        phone=phone,
        created_by_user_id=created_by.id,
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


def associate(db, practitioner, patient):
    db.execute(
        doctor_patients.insert().values(
            practitioner_id=practitioner.id,
            patient_id=patient.id,
        )
    )
    db.commit()


def create_appointment(
    db,
    patient,
    practitioner,
    start_at,
    end_at,
    created_by,
    status="scheduled",
):
    appointment = Appointment(
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        start_at=start_at,
        end_at=end_at,
        status=status,
        created_by_user_id=created_by.id,
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return appointment


def create_working_hour(
    db, practitioner, weekday=0, start_time="09:00", end_time="17:00"
):
    working_hour = WorkingHour(
        practitioner_id=practitioner.id,
        weekday=weekday,
        start_time=time.fromisoformat(start_time),
        end_time=time.fromisoformat(end_time),
    )
    db.add(working_hour)
    db.commit()
    db.refresh(working_hour)
    return working_hour


def login(client, email):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_header(client, email):
    return {"Authorization": f"Bearer {login(client, email)}"}


def payload(patient_id, practitioner_id, start_at, end_at):
    return {
        "patient_id": patient_id,
        "practitioner_id": practitioner_id,
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
    }


def test_appointment_endpoints_require_authentication(client_and_db):
    client, _ = client_and_db

    assert client.get("/api/v1/appointments").status_code == 401
    assert client.get("/api/v1/appointments/1").status_code == 401
    assert client.post("/api/v1/appointments", json={}).status_code == 401
    assert client.patch("/api/v1/appointments/1", json={}).status_code == 401
    assert client.post("/api/v1/appointments/1/cancel").status_code == 401


def test_admin_can_create_and_list_appointments(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Alice", created_by=admin)

    response = client.post(
        "/api/v1/appointments",
        json=payload(patient.id, pract.id, BASE_TIME, BASE_TIME + timedelta(hours=1)),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["patient_id"] == patient.id
    assert data["practitioner_id"] == pract.id
    assert data["status"] == "scheduled"
    assert data["created_by_user_id"] == admin.id

    listing = client.get(
        "/api/v1/appointments", headers=auth_header(client, admin.email)
    )
    assert listing.status_code == 200
    assert [a["id"] for a in listing.json()] == [data["id"]]


def test_receptionist_can_create_appointment(client_and_db):
    client, db = client_and_db
    receptionist = create_user(db, "Receptionist", "rec@example.com", "receptionist")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Bob", created_by=receptionist)

    response = client.post(
        "/api/v1/appointments",
        json=payload(patient.id, pract.id, BASE_TIME, BASE_TIME + timedelta(minutes=30)),
        headers=auth_header(client, receptionist.email),
    )

    assert response.status_code == 201, response.text
    assert response.json()["practitioner_id"] == pract.id


def test_doctor_creation_uses_own_practitioner_not_client_supplied(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Patient", created_by=admin)
    associate(db, pract1, patient)

    response = client.post(
        "/api/v1/appointments",
        json=payload(
            patient.id, pract2.id, BASE_TIME, BASE_TIME + timedelta(minutes=30)
        ),
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 201, response.text
    assert response.json()["practitioner_id"] == pract1.id


def test_admin_must_supply_practitioner_id(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    patient = create_patient(db, "Patient", created_by=admin)

    response = client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient.id,
            "start_at": BASE_TIME.isoformat(),
            "end_at": (BASE_TIME + timedelta(minutes=30)).isoformat(),
        },
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 422


def test_create_with_missing_patient_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)

    response = client.post(
        "/api/v1/appointments",
        json=payload(99999, pract.id, BASE_TIME, BASE_TIME + timedelta(minutes=30)),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 404


def test_create_with_soft_deleted_patient_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Deleted", created_by=admin)
    patient.is_deleted = True
    db.commit()

    response = client.post(
        "/api/v1/appointments",
        json=payload(patient.id, pract.id, BASE_TIME, BASE_TIME + timedelta(minutes=30)),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 404


def test_create_with_missing_practitioner_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    patient = create_patient(db, "Patient", created_by=admin)

    response = client.post(
        "/api/v1/appointments",
        json=payload(patient.id, 99999, BASE_TIME, BASE_TIME + timedelta(minutes=30)),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 404


def test_create_with_invalid_time_range_returns_422(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)

    for start_at, end_at in [
        (BASE_TIME, BASE_TIME),
        (BASE_TIME + timedelta(hours=1), BASE_TIME),
    ]:
        response = client.post(
            "/api/v1/appointments",
            json=payload(patient.id, pract.id, start_at, end_at),
            headers=auth_header(client, admin.email),
        )
        assert response.status_code == 422


def test_conflict_detection_for_same_practitioner(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    create_appointment(
        db, patient, pract, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )

    response = client.post(
        "/api/v1/appointments",
        json=payload(
            patient.id,
            pract.id,
            BASE_TIME + timedelta(minutes=30),
            BASE_TIME + timedelta(minutes=90),
        ),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 409


def test_adjacent_appointments_are_allowed(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    create_appointment(
        db, patient, pract, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )

    response = client.post(
        "/api/v1/appointments",
        json=payload(
            patient.id,
            pract.id,
            BASE_TIME + timedelta(hours=1),
            BASE_TIME + timedelta(hours=2),
        ),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 201, response.text


def test_different_practitioners_can_overlap(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Patient", created_by=admin)
    create_appointment(
        db, patient, pract1, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )

    response = client.post(
        "/api/v1/appointments",
        json=payload(
            patient.id, pract2.id, BASE_TIME, BASE_TIME + timedelta(hours=1)
        ),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 201, response.text


def test_inactive_appointments_do_not_block_new_slots(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    create_appointment(
        db,
        patient,
        pract,
        BASE_TIME,
        BASE_TIME + timedelta(hours=1),
        admin,
        status="cancelled",
    )
    create_appointment(
        db,
        patient,
        pract,
        BASE_TIME + timedelta(hours=2),
        BASE_TIME + timedelta(hours=3),
        admin,
        status="completed",
    )

    response = client.post(
        "/api/v1/appointments",
        json=payload(
            patient.id,
            pract.id,
            BASE_TIME + timedelta(minutes=15),
            BASE_TIME + timedelta(minutes=45),
        ),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 201, response.text


def test_doctor_lists_only_own_appointments(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Patient", created_by=admin)
    appt1 = create_appointment(
        db, patient, pract1, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )
    create_appointment(
        db,
        patient,
        pract2,
        BASE_TIME + timedelta(hours=2),
        BASE_TIME + timedelta(hours=3),
        admin,
    )

    response = client.get(
        "/api/v1/appointments", headers=auth_header(client, doctor1.email)
    )

    assert response.status_code == 200
    assert [a["id"] for a in response.json()] == [appt1.id]


def test_admin_can_filter_appointments(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient1 = create_patient(db, "Patient One", created_by=admin)
    patient2 = create_patient(db, "Patient Two", created_by=admin)
    create_appointment(
        db, patient1, pract1, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )
    create_appointment(
        db,
        patient2,
        pract2,
        BASE_TIME + timedelta(hours=2),
        BASE_TIME + timedelta(hours=3),
        admin,
        status="completed",
    )

    headers = auth_header(client, admin.email)

    by_practitioner = client.get(
        "/api/v1/appointments", params={"practitioner_id": pract1.id}, headers=headers
    )
    assert len(by_practitioner.json()) == 1
    assert by_practitioner.json()[0]["practitioner_id"] == pract1.id

    by_patient = client.get(
        "/api/v1/appointments", params={"patient_id": patient2.id}, headers=headers
    )
    assert len(by_patient.json()) == 1
    assert by_patient.json()[0]["patient_id"] == patient2.id

    by_status = client.get(
        "/api/v1/appointments", params={"status": "completed"}, headers=headers
    )
    assert len(by_status.json()) == 1
    assert by_status.json()[0]["status"] == "completed"


def test_invalid_status_query_param_returns_422(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    response = client.get(
        "/api/v1/appointments",
        params={"status": "bogus"},
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 422


def test_doctor_cannot_view_other_doctors_appointment(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract2 = create_practitioner(db, doctor2)
    create_practitioner(db, doctor1)
    patient = create_patient(db, "Patient", created_by=admin)
    appt = create_appointment(
        db, patient, pract2, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )

    response = client.get(
        f"/api/v1/appointments/{appt.id}",
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 403


def test_doctor_cannot_update_other_doctors_appointment(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract2 = create_practitioner(db, doctor2)
    create_practitioner(db, doctor1)
    patient = create_patient(db, "Patient", created_by=admin)
    appt = create_appointment(
        db, patient, pract2, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )

    response = client.patch(
        f"/api/v1/appointments/{appt.id}",
        json={"status": "completed"},
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 403
    db.expire_all()
    assert db.get(Appointment, appt.id).status == "scheduled"


def test_doctor_cannot_cancel_other_doctors_appointment(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract2 = create_practitioner(db, doctor2)
    create_practitioner(db, doctor1)
    patient = create_patient(db, "Patient", created_by=admin)
    appt = create_appointment(
        db, patient, pract2, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )

    response = client.post(
        f"/api/v1/appointments/{appt.id}/cancel",
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 403
    db.expire_all()
    assert db.get(Appointment, appt.id).status == "scheduled"


def test_doctor_cannot_reassign_appointment_to_other_practitioner(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Patient", created_by=admin)
    appt = create_appointment(
        db, patient, pract1, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )

    response = client.patch(
        f"/api/v1/appointments/{appt.id}",
        json={"practitioner_id": pract2.id},
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 403
    db.expire_all()
    assert db.get(Appointment, appt.id).practitioner_id == pract1.id


def test_doctor_can_reschedule_own_appointment_without_self_conflict(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    appt = create_appointment(
        db, patient, pract, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )

    response = client.patch(
        f"/api/v1/appointments/{appt.id}",
        json={
            "start_at": (BASE_TIME + timedelta(minutes=15)).isoformat(),
            "end_at": (BASE_TIME + timedelta(minutes=45)).isoformat(),
        },
        headers=auth_header(client, doctor.email),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["start_at"].startswith("2026-01-05T09:15:00")
    assert data["end_at"].startswith("2026-01-05T09:45:00")


def test_reschedule_into_existing_appointment_returns_409(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    create_appointment(
        db, patient, pract, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )
    appt2 = create_appointment(
        db,
        patient,
        pract,
        BASE_TIME + timedelta(hours=2),
        BASE_TIME + timedelta(hours=3),
        admin,
    )

    response = client.patch(
        f"/api/v1/appointments/{appt2.id}",
        json={
            "start_at": (BASE_TIME + timedelta(minutes=30)).isoformat(),
            "end_at": (BASE_TIME + timedelta(minutes=90)).isoformat(),
        },
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 409


def test_get_missing_appointment_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    response = client.get(
        "/api/v1/appointments/99999", headers=auth_header(client, admin.email)
    )

    assert response.status_code == 404


def test_update_missing_appointment_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    response = client.patch(
        "/api/v1/appointments/99999",
        json={"status": "completed"},
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 404


def test_cancel_missing_appointment_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    response = client.post(
        "/api/v1/appointments/99999/cancel",
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 404


def test_update_with_invalid_status_returns_422(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    appt = create_appointment(
        db, patient, pract, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )

    response = client.patch(
        f"/api/v1/appointments/{appt.id}",
        json={"status": "bogus"},
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 422


def test_cancel_sets_status_and_keeps_record(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    appt = create_appointment(
        db, patient, pract, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )

    response = client.post(
        f"/api/v1/appointments/{appt.id}/cancel",
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    db.expire_all()
    stored = db.get(Appointment, appt.id)
    assert stored is not None
    assert stored.status == "cancelled"


def test_patient_can_have_appointments_with_multiple_practitioners(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "GP", "gp@example.com", "doctor")
    doctor2 = create_user(db, "Cardiologist", "cardio@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1, "General")
    pract2 = create_practitioner(db, doctor2, "Cardiology")
    patient = create_patient(db, "Multi Doctor Patient", created_by=admin)

    first = client.post(
        "/api/v1/appointments",
        json=payload(patient.id, pract1.id, BASE_TIME, BASE_TIME + timedelta(hours=1)),
        headers=auth_header(client, admin.email),
    )
    second = client.post(
        "/api/v1/appointments",
        json=payload(
            patient.id,
            pract2.id,
            BASE_TIME + timedelta(hours=1),
            BASE_TIME + timedelta(hours=2),
        ),
        headers=auth_header(client, admin.email),
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    listing = client.get(
        "/api/v1/appointments",
        params={"patient_id": patient.id},
        headers=auth_header(client, admin.email),
    )
    practitioner_ids = {a["practitioner_id"] for a in listing.json()}
    assert practitioner_ids == {pract1.id, pract2.id}


def test_admin_can_update_and_cancel_any_appointment(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Patient", created_by=admin)
    appt = create_appointment(
        db, patient, pract1, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )

    headers = auth_header(client, admin.email)

    response = client.patch(
        f"/api/v1/appointments/{appt.id}",
        json={"practitioner_id": pract2.id, "status": "completed"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["practitioner_id"] == pract2.id
    assert response.json()["status"] == "completed"

    response = client.post(f"/api/v1/appointments/{appt.id}/cancel", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_doctor_without_practitioner_record_gets_403(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "No Practitioner", "nopract@example.com", "doctor")
    doctor2 = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor2)
    patient = create_patient(db, "Patient", created_by=admin)
    appt = create_appointment(
        db, patient, pract, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )

    headers = auth_header(client, doctor.email)

    assert client.get("/api/v1/appointments", headers=headers).status_code == 403
    assert client.post(
        "/api/v1/appointments",
        json=payload(patient.id, pract.id, BASE_TIME, BASE_TIME + timedelta(hours=1)),
        headers=headers,
    ).status_code == 403
    assert client.get(f"/api/v1/appointments/{appt.id}", headers=headers).status_code == 403


def test_openapi_contains_appointment_paths(client_and_db):
    client, _ = client_and_db

    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    assert "/api/v1/appointments" in paths
    assert "get" in paths["/api/v1/appointments"]
    assert "post" in paths["/api/v1/appointments"]
    assert "/api/v1/appointments/{appointment_id}" in paths
    assert "get" in paths["/api/v1/appointments/{appointment_id}"]
    assert "patch" in paths["/api/v1/appointments/{appointment_id}"]
    assert "/api/v1/appointments/{appointment_id}/cancel" in paths


def test_appointment_within_working_hours_allowed(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    create_working_hour(db, pract, weekday=0)

    response = client.post(
        "/api/v1/appointments",
        json=payload(
            patient.id,
            pract.id,
            BASE_TIME + timedelta(minutes=30),
            BASE_TIME + timedelta(hours=1, minutes=30),
        ),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 201, response.text


def test_appointment_outside_working_hours_rejected_on_create(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    create_working_hour(db, pract, weekday=0)

    response = client.post(
        "/api/v1/appointments",
        json=payload(
            patient.id,
            pract.id,
            BASE_TIME + timedelta(hours=9),
            BASE_TIME + timedelta(hours=10),
        ),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 422
    db.expire_all()
    count = db.query(Appointment).count()
    assert count == 0


def test_appointment_reschedule_outside_working_hours_rejected(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    appt = create_appointment(
        db, patient, pract, BASE_TIME, BASE_TIME + timedelta(hours=1), admin
    )
    create_working_hour(db, pract, weekday=0)

    response = client.patch(
        f"/api/v1/appointments/{appt.id}",
        json={
            "start_at": (BASE_TIME + timedelta(hours=9)).isoformat(),
            "end_at": (BASE_TIME + timedelta(hours=10)).isoformat(),
        },
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 422
    db.expire_all()
    stored = db.get(Appointment, appt.id)
    assert stored.start_at == BASE_TIME
    assert stored.end_at == BASE_TIME + timedelta(hours=1)


def test_status_only_update_not_blocked_by_working_hours(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    appt = create_appointment(
        db,
        patient,
        pract,
        BASE_TIME + timedelta(hours=9),
        BASE_TIME + timedelta(hours=10),
        admin,
    )
    create_working_hour(db, pract, weekday=0)

    response = client.patch(
        f"/api/v1/appointments/{appt.id}",
        json={"status": "completed"},
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"


def test_appointment_crossing_midnight_outside_working_hours_rejected(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    create_working_hour(db, pract, weekday=0)

    response = client.post(
        "/api/v1/appointments",
        json=payload(
            patient.id,
            pract.id,
            BASE_TIME + timedelta(hours=7),
            BASE_TIME + timedelta(hours=24),
        ),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 422


def test_no_working_hours_configured_behavior_preserved(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)

    response = client.post(
        "/api/v1/appointments",
        json=payload(
            patient.id,
            pract.id,
            BASE_TIME + timedelta(hours=18),
            BASE_TIME + timedelta(hours=19),
        ),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 201, response.text
