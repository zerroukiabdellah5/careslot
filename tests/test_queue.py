from datetime import date, datetime

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
    QueueEntry,
    User,
    doctor_patients,
)
from app.main import app

PASSWORD = "CorrectPassword123!"
QUEUE_DATE = date(2026, 1, 5)


@pytest.fixture
def client_and_db(monkeypatch):
    monkeypatch.setenv(
        "JWT_SECRET_KEY",
        "test-secret-key-for-careslot-queue-tests-min-32-characters",
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


def create_appointment(db, patient, practitioner, created_by):
    start_at = datetime(2026, 1, 5, 9, 0, 0)
    appointment = Appointment(
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        start_at=start_at,
        end_at=start_at.replace(hour=10),
        status="scheduled",
        created_by_user_id=created_by.id,
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return appointment


def create_queue_entry(
    db,
    patient,
    practitioner,
    queue_date=QUEUE_DATE,
    position=1,
    status="waiting",
    appointment=None,
):
    entry = QueueEntry(
        practitioner_id=practitioner.id,
        patient_id=patient.id,
        appointment_id=appointment.id if appointment is not None else None,
        queue_date=queue_date,
        position=position,
        status=status,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def login(client, email):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_header(client, email):
    return {"Authorization": f"Bearer {login(client, email)}"}


def payload(
    patient_id,
    practitioner_id=None,
    queue_date=None,
    appointment_id=None,
):
    data = {"patient_id": patient_id}
    if practitioner_id is not None:
        data["practitioner_id"] = practitioner_id
    if queue_date is not None:
        data["queue_date"] = queue_date.isoformat()
    if appointment_id is not None:
        data["appointment_id"] = appointment_id
    return data


def test_queue_endpoints_require_authentication(client_and_db):
    client, _ = client_and_db

    assert client.get("/api/v1/queue").status_code == 401
    assert client.get("/api/v1/queue/1").status_code == 401
    assert client.post("/api/v1/queue", json={}).status_code == 401
    assert client.patch("/api/v1/queue/1", json={}).status_code == 401
    assert client.post("/api/v1/queue/1/cancel").status_code == 401
    assert client.delete("/api/v1/queue/1").status_code == 401


def test_admin_can_add_and_list_queue_entries(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Alice", created_by=admin)

    response = client.post(
        "/api/v1/queue",
        json=payload(patient.id, pract.id, QUEUE_DATE),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["patient_id"] == patient.id
    assert data["practitioner_id"] == pract.id
    assert data["queue_date"] == QUEUE_DATE.isoformat()
    assert data["position"] == 1
    assert data["status"] == "waiting"

    listing = client.get("/api/v1/queue", headers=auth_header(client, admin.email))
    assert listing.status_code == 200
    assert [e["id"] for e in listing.json()] == [data["id"]]


def test_receptionist_can_add_queue_entry(client_and_db):
    client, db = client_and_db
    receptionist = create_user(db, "Receptionist", "rec@example.com", "receptionist")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Bob", created_by=receptionist)

    response = client.post(
        "/api/v1/queue",
        json=payload(patient.id, pract.id, QUEUE_DATE),
        headers=auth_header(client, receptionist.email),
    )

    assert response.status_code == 201, response.text
    assert response.json()["practitioner_id"] == pract.id


def test_queue_positions_increment(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient1 = create_patient(db, "One", created_by=admin)
    patient2 = create_patient(db, "Two", created_by=admin)

    first = client.post(
        "/api/v1/queue",
        json=payload(patient1.id, pract.id, QUEUE_DATE),
        headers=auth_header(client, admin.email),
    )
    second = client.post(
        "/api/v1/queue",
        json=payload(patient2.id, pract.id, QUEUE_DATE),
        headers=auth_header(client, admin.email),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["position"] == 1
    assert second.json()["position"] == 2


def test_queue_date_defaults_to_today(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)

    response = client.post(
        "/api/v1/queue",
        json=payload(patient.id, pract.id),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 201, response.text
    assert response.json()["queue_date"] == date.today().isoformat()


def test_doctor_adding_uses_own_practitioner_not_client_supplied(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Patient", created_by=admin)
    associate(db, pract1, patient)

    response = client.post(
        "/api/v1/queue",
        json=payload(patient.id, pract2.id, QUEUE_DATE),
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 201, response.text
    assert response.json()["practitioner_id"] == pract1.id


def test_doctor_cannot_add_non_associated_patient(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Not Mine", created_by=admin)

    response = client.post(
        "/api/v1/queue",
        json=payload(patient.id, pract.id, QUEUE_DATE),
        headers=auth_header(client, doctor.email),
    )

    assert response.status_code == 403


def test_admin_must_supply_practitioner_id(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    patient = create_patient(db, "Patient", created_by=admin)

    response = client.post(
        "/api/v1/queue",
        json=payload(patient.id),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 422


def test_missing_patient_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)

    response = client.post(
        "/api/v1/queue",
        json=payload(99999, pract.id, QUEUE_DATE),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 404


def test_soft_deleted_patient_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Deleted", created_by=admin)
    patient.is_deleted = True
    db.commit()

    response = client.post(
        "/api/v1/queue",
        json=payload(patient.id, pract.id, QUEUE_DATE),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 404


def test_missing_practitioner_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    patient = create_patient(db, "Patient", created_by=admin)

    response = client.post(
        "/api/v1/queue",
        json=payload(patient.id, 99999, QUEUE_DATE),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 404


def test_missing_appointment_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)

    response = client.post(
        "/api/v1/queue",
        json=payload(patient.id, pract.id, QUEUE_DATE, appointment_id=99999),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 404


def test_appointment_of_different_practitioner_rejected(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Patient", created_by=admin)
    appointment = create_appointment(db, patient, pract2, admin)

    response = client.post(
        "/api/v1/queue",
        json=payload(patient.id, pract1.id, QUEUE_DATE, appointment_id=appointment.id),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 422


def test_appointment_of_different_patient_rejected(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient1 = create_patient(db, "One", created_by=admin)
    patient2 = create_patient(db, "Two", created_by=admin)
    appointment = create_appointment(db, patient1, pract, admin)

    response = client.post(
        "/api/v1/queue",
        json=payload(patient2.id, pract.id, QUEUE_DATE, appointment_id=appointment.id),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 422


def test_duplicate_active_entry_returns_409(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    create_queue_entry(db, patient, pract, QUEUE_DATE, position=1)

    response = client.post(
        "/api/v1/queue",
        json=payload(patient.id, pract.id, QUEUE_DATE),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 409


def test_cancelled_entry_allows_requeue(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    entry = create_queue_entry(db, patient, pract, QUEUE_DATE, position=1)

    cancel = client.post(
        f"/api/v1/queue/{entry.id}/cancel",
        headers=auth_header(client, admin.email),
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"

    response = client.post(
        "/api/v1/queue",
        json=payload(patient.id, pract.id, QUEUE_DATE),
        headers=auth_header(client, admin.email),
    )
    assert response.status_code == 201, response.text


def test_doctor_lists_only_own_queue_entries(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Patient", created_by=admin)
    associate(db, pract1, patient)
    associate(db, pract2, patient)
    my_entry = create_queue_entry(db, patient, pract1, QUEUE_DATE, position=1)
    create_queue_entry(db, patient, pract2, QUEUE_DATE, position=1)

    response = client.get(
        "/api/v1/queue", headers=auth_header(client, doctor1.email)
    )

    assert response.status_code == 200
    assert [e["id"] for e in response.json()] == [my_entry.id]


def test_admin_can_filter_queue_entries(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient1 = create_patient(db, "One", created_by=admin)
    patient2 = create_patient(db, "Two", created_by=admin)
    create_queue_entry(db, patient1, pract, QUEUE_DATE, position=1)
    create_queue_entry(
        db, patient2, pract, QUEUE_DATE, position=2, status="completed"
    )

    headers = auth_header(client, admin.email)

    by_patient = client.get(
        "/api/v1/queue", params={"patient_id": patient2.id}, headers=headers
    )
    assert len(by_patient.json()) == 1
    assert by_patient.json()[0]["patient_id"] == patient2.id

    by_status = client.get(
        "/api/v1/queue", params={"status": "completed"}, headers=headers
    )
    assert len(by_status.json()) == 1
    assert by_status.json()[0]["status"] == "completed"

    by_date = client.get(
        "/api/v1/queue", params={"queue_date": QUEUE_DATE.isoformat()}, headers=headers
    )
    assert len(by_date.json()) == 2


def test_invalid_status_query_param_returns_422(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    response = client.get(
        "/api/v1/queue",
        params={"status": "bogus"},
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 422


def test_doctor_cannot_view_other_doctors_entry(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Patient", created_by=admin)
    associate(db, pract2, patient)
    entry = create_queue_entry(db, patient, pract2, QUEUE_DATE, position=1)

    response = client.get(
        f"/api/v1/queue/{entry.id}",
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 403


def test_doctor_cannot_update_other_doctors_entry(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Patient", created_by=admin)
    associate(db, pract2, patient)
    entry = create_queue_entry(db, patient, pract2, QUEUE_DATE, position=1)

    response = client.patch(
        f"/api/v1/queue/{entry.id}",
        json={"status": "in_service"},
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 403
    db.expire_all()
    assert db.get(QueueEntry, entry.id).status == "waiting"


def test_doctor_cannot_cancel_other_doctors_entry(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Patient", created_by=admin)
    associate(db, pract2, patient)
    entry = create_queue_entry(db, patient, pract2, QUEUE_DATE, position=1)

    response = client.post(
        f"/api/v1/queue/{entry.id}/cancel",
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 403
    db.expire_all()
    assert db.get(QueueEntry, entry.id).status == "waiting"


def test_doctor_cannot_delete_other_doctors_entry(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Patient", created_by=admin)
    associate(db, pract2, patient)
    entry = create_queue_entry(db, patient, pract2, QUEUE_DATE, position=1)

    response = client.delete(
        f"/api/v1/queue/{entry.id}",
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 403
    db.expire_all()
    assert db.get(QueueEntry, entry.id) is not None


def test_doctor_cannot_access_entry_for_non_associated_patient(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Not Associated", created_by=admin)
    entry = create_queue_entry(db, patient, pract, QUEUE_DATE, position=1)

    headers = auth_header(client, doctor.email)
    assert client.get(f"/api/v1/queue/{entry.id}", headers=headers).status_code == 403
    assert (
        client.patch(
            f"/api/v1/queue/{entry.id}", json={"status": "in_service"}, headers=headers
        ).status_code
        == 403
    )


def test_get_missing_entry_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    assert client.get(
        "/api/v1/queue/99999", headers=auth_header(client, admin.email)
    ).status_code == 404


def test_update_missing_entry_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    response = client.patch(
        "/api/v1/queue/99999",
        json={"status": "completed"},
        headers=auth_header(client, admin.email),
    )
    assert response.status_code == 404


def test_cancel_missing_entry_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    response = client.post(
        "/api/v1/queue/99999/cancel",
        headers=auth_header(client, admin.email),
    )
    assert response.status_code == 404


def test_delete_missing_entry_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    response = client.delete(
        "/api/v1/queue/99999",
        headers=auth_header(client, admin.email),
    )
    assert response.status_code == 404


def test_invalid_status_returns_422(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    entry = create_queue_entry(db, patient, pract, QUEUE_DATE, position=1)

    response = client.patch(
        f"/api/v1/queue/{entry.id}",
        json={"status": "bogus"},
        headers=auth_header(client, admin.email),
    )
    assert response.status_code == 422


def test_terminal_status_cannot_be_changed(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    entry = create_queue_entry(
        db, patient, pract, QUEUE_DATE, position=1, status="completed"
    )

    response = client.patch(
        f"/api/v1/queue/{entry.id}",
        json={"status": "waiting"},
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 409
    db.expire_all()
    assert db.get(QueueEntry, entry.id).status == "completed"


def test_cancel_completed_entry_returns_409(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    entry = create_queue_entry(
        db, patient, pract, QUEUE_DATE, position=1, status="completed"
    )

    response = client.post(
        f"/api/v1/queue/{entry.id}/cancel",
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 409


def test_cancel_sets_cancelled_and_keeps_record(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    entry = create_queue_entry(db, patient, pract, QUEUE_DATE, position=1)

    response = client.post(
        f"/api/v1/queue/{entry.id}/cancel",
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    db.expire_all()
    stored = (
        db.query(QueueEntry)
        .filter(QueueEntry.id == entry.id)
        .first()
    )
    assert stored is not None
    assert stored.status == "cancelled"


def test_status_workflow_waiting_to_in_service_to_completed(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    entry = create_queue_entry(db, patient, pract, QUEUE_DATE, position=1)
    headers = auth_header(client, admin.email)

    in_service = client.patch(
        f"/api/v1/queue/{entry.id}",
        json={"status": "in_service"},
        headers=headers,
    )
    assert in_service.status_code == 200
    assert in_service.json()["status"] == "in_service"

    completed = client.patch(
        f"/api/v1/queue/{entry.id}",
        json={"status": "completed"},
        headers=headers,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"


def test_delete_removes_entry(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    entry = create_queue_entry(db, patient, pract, QUEUE_DATE, position=1)

    response = client.delete(
        f"/api/v1/queue/{entry.id}",
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 204
    deleted_id = entry.id
    db.expire_all()
    assert (
        db.query(QueueEntry)
        .filter(QueueEntry.id == deleted_id)
        .first()
        is None
    )


def test_admin_can_update_and_cancel_any_entry(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=admin)
    associate(db, pract, patient)
    entry = create_queue_entry(db, patient, pract, QUEUE_DATE, position=1)
    headers = auth_header(client, admin.email)

    updated = client.patch(
        f"/api/v1/queue/{entry.id}",
        json={"status": "in_service"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "in_service"

    cancelled = client.post(f"/api/v1/queue/{entry.id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_doctor_without_practitioner_record_gets_403(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "No Practitioner", "nopract@example.com", "doctor")

    headers = auth_header(client, doctor.email)

    assert client.get("/api/v1/queue", headers=headers).status_code == 403
    assert client.post(
        "/api/v1/queue", json=payload(1), headers=headers
    ).status_code == 403


def test_openapi_contains_queue_paths(client_and_db):
    client, _ = client_and_db

    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    assert "/api/v1/queue" in paths
    assert "get" in paths["/api/v1/queue"]
    assert "post" in paths["/api/v1/queue"]
    assert "/api/v1/queue/{entry_id}" in paths
    assert "get" in paths["/api/v1/queue/{entry_id}"]
    assert "patch" in paths["/api/v1/queue/{entry_id}"]
    assert "delete" in paths["/api/v1/queue/{entry_id}"]
    assert "/api/v1/queue/{entry_id}/cancel" in paths