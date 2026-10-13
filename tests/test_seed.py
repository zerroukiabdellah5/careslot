import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.core.security import verify_password
from app.db.base import Base
from app.db.models import (
    Appointment,
    Patient,
    Practitioner,
    QueueEntry,
    User,
    WorkingHour,
    doctor_patients,
)
from app.db.seed import (
    DEMO_ASSOCIATIONS,
    DEMO_PASSWORD,
    DEMO_USERS,
    seed_database,
)
from app.main import app

EXPECTED_COUNTS = {
    "users": 4,
    "practitioners": 2,
    "patients": 4,
    "doctor_patients": 5,
    "working_hours": 5,
    "appointments": 6,
    "queue_entries": 4,
}

VALID_APPOINTMENT_STATUSES = {
    "scheduled",
    "in_progress",
    "completed",
    "cancelled",
    "no_show",
}
VALID_QUEUE_STATUSES = {"waiting", "in_service", "completed", "cancelled"}


@pytest.fixture
def client_and_db(monkeypatch):
    monkeypatch.setenv(
        "JWT_SECRET_KEY",
        "test-secret-key-for-careslot-seed-tests-min-32-chars",
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


def counts(db):
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


def test_seed_populates_all_tables(client_and_db):
    _, db = client_and_db

    seed_database(db)

    assert counts(db) == EXPECTED_COUNTS


def test_seed_users_are_documented_demo_accounts(client_and_db):
    _, db = client_and_db

    seed_database(db)

    stored = {user.email: user for user in db.query(User).all()}
    assert set(stored) == {spec["email"] for spec in DEMO_USERS}

    for spec in DEMO_USERS:
        user = stored[spec["email"]]
        assert user.role == spec["role"]
        assert user.full_name == spec["full_name"]
        assert user.is_active is True


def test_seed_hashes_passwords_with_verify_password(client_and_db):
    _, db = client_and_db

    seed_database(db)

    for spec in DEMO_USERS:
        user = (
            db.query(User).filter(User.email == spec["email"]).one()
        )
        assert user.password_hash != DEMO_PASSWORD
        assert verify_password(DEMO_PASSWORD, user.password_hash)
        assert not verify_password("WrongPassword123!", user.password_hash)


def test_seed_links_each_doctor_to_its_practitioner(client_and_db):
    _, db = client_and_db

    seed_database(db)

    users = {user.email: user for user in db.query(User).all()}
    for spec in DEMO_USERS:
        if spec["role"] != "doctor":
            assert users[spec["email"]].practitioner is None
            continue
        practitioner = users[spec["email"]].practitioner
        assert practitioner is not None
        assert practitioner.user_id == users[spec["email"]].id
        assert practitioner.specialty == spec["specialty"]


def test_seed_creates_valid_working_hours(client_and_db):
    _, db = client_and_db

    seed_database(db)

    working_hours = db.query(WorkingHour).all()
    assert len(working_hours) == EXPECTED_COUNTS["working_hours"]

    seen = set()
    for working_hour in working_hours:
        assert 0 <= working_hour.weekday <= 6
        assert working_hour.start_time < working_hour.end_time
        key = (working_hour.practitioner_id, working_hour.weekday)
        assert key not in seen
        seen.add(key)


def test_seed_associates_doctors_and_patients(client_and_db):
    _, db = client_and_db

    seed_database(db)

    users = {user.email: user for user in db.query(User).all()}
    for doctor_email, patient_names in DEMO_ASSOCIATIONS.items():
        practitioner = users[doctor_email].practitioner
        associated = {patient.full_name for patient in practitioner.patients}
        assert associated == set(patient_names)


def test_seed_creates_valid_appointments_and_queue_entries(client_and_db):
    _, db = client_and_db

    seed_database(db)

    practitioner_ids = {p.id for p in db.query(Practitioner).all()}
    patient_ids = {p.id for p in db.query(Patient).all()}

    appointments = db.query(Appointment).all()
    for appointment in appointments:
        assert appointment.practitioner_id in practitioner_ids
        assert appointment.patient_id in patient_ids
        assert appointment.start_at < appointment.end_at
        assert appointment.status in VALID_APPOINTMENT_STATUSES

    queue_entries = db.query(QueueEntry).all()
    positions = set()
    for entry in queue_entries:
        assert entry.practitioner_id in practitioner_ids
        assert entry.patient_id in patient_ids
        assert entry.position >= 1
        assert entry.status in VALID_QUEUE_STATUSES
        positions.add(
            (entry.practitioner_id, entry.queue_date, entry.position)
        )
        if entry.appointment_id is not None:
            appointment = db.get(Appointment, entry.appointment_id)
            assert appointment is not None
            assert appointment.practitioner_id == entry.practitioner_id
            assert appointment.patient_id == entry.patient_id

    assert len(positions) == len(queue_entries)


def test_seed_is_idempotent_and_preserves_passwords(client_and_db):
    _, db = client_and_db

    seed_database(db)
    first_counts = counts(db)
    first_users = {
        user.email: (user.id, user.password_hash)
        for user in db.query(User).all()
    }

    seed_database(db)

    assert counts(db) == first_counts
    second_users = {
        user.email: (user.id, user.password_hash)
        for user in db.query(User).all()
    }
    assert second_users == first_users


def test_seeded_demo_accounts_can_login(client_and_db):
    client, db = client_and_db

    seed_database(db)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@careslot.dev", "password": DEMO_PASSWORD},
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["role"] == "admin"
    assert "password_hash" not in response.json()["user"]

    doctor_response = client.post(
        "/api/v1/auth/login",
        json={"email": "dr.smith@careslot.dev", "password": DEMO_PASSWORD},
    )
    assert doctor_response.status_code == 200, doctor_response.text
    token = doctor_response.json()["access_token"]

    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me.status_code == 200
    assert me.json()["role"] == "doctor"

    bad = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@careslot.dev", "password": "not-the-password"},
    )
    assert bad.status_code == 401
