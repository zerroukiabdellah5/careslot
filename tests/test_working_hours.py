from datetime import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import Practitioner, User, WorkingHour
from app.main import app

PASSWORD = "CorrectPassword123!"


@pytest.fixture
def client_and_db(monkeypatch):
    monkeypatch.setenv(
        "JWT_SECRET_KEY",
        "test-secret-key-for-careslot-working-hours-tests-min-32-char",
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


def payload(
    practitioner_id, weekday=0, start_time="09:00", end_time="17:00"
):
    return {
        "practitioner_id": practitioner_id,
        "weekday": weekday,
        "start_time": start_time,
        "end_time": end_time,
    }


def test_working_hour_endpoints_require_authentication(client_and_db):
    client, _ = client_and_db

    assert client.get("/api/v1/working_hours").status_code == 401
    assert client.get("/api/v1/working_hours/1").status_code == 401
    assert client.post("/api/v1/working_hours", json={}).status_code == 401
    assert client.patch("/api/v1/working_hours/1", json={}).status_code == 401
    assert client.delete("/api/v1/working_hours/1").status_code == 401


def test_admin_can_create_and_list_working_hours(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)

    response = client.post(
        "/api/v1/working_hours",
        json=payload(pract.id, weekday=0, start_time="09:00", end_time="17:00"),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["practitioner_id"] == pract.id
    assert data["weekday"] == 0
    assert data["start_time"] == "09:00:00"
    assert data["end_time"] == "17:00:00"

    listing = client.get(
        "/api/v1/working_hours", headers=auth_header(client, admin.email)
    )
    assert listing.status_code == 200
    assert [w["id"] for w in listing.json()] == [data["id"]]


def test_receptionist_can_create_working_hour(client_and_db):
    client, db = client_and_db
    receptionist = create_user(db, "Receptionist", "rec@example.com", "receptionist")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)

    response = client.post(
        "/api/v1/working_hours",
        json=payload(pract.id),
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

    response = client.post(
        "/api/v1/working_hours",
        json=payload(pract2.id, weekday=0),
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 201, response.text
    assert response.json()["practitioner_id"] == pract1.id


def test_admin_must_supply_practitioner_id(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    response = client.post(
        "/api/v1/working_hours",
        json={"weekday": 0, "start_time": "09:00", "end_time": "17:00"},
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 422


def test_create_with_missing_practitioner_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    response = client.post(
        "/api/v1/working_hours",
        json=payload(99999),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 404


def test_invalid_weekday_returns_422(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)

    for weekday in (-1, 7):
        response = client.post(
            "/api/v1/working_hours",
            json=payload(pract.id, weekday=weekday),
            headers=auth_header(client, admin.email),
        )
        assert response.status_code == 422


def test_invalid_time_range_returns_422(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)

    for start_time, end_time in [("17:00", "09:00"), ("09:00", "09:00")]:
        response = client.post(
            "/api/v1/working_hours",
            json=payload(pract.id, start_time=start_time, end_time=end_time),
            headers=auth_header(client, admin.email),
        )
        assert response.status_code == 422


def test_duplicate_weekday_returns_409(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    create_working_hour(db, pract, weekday=0)

    response = client.post(
        "/api/v1/working_hours",
        json=payload(pract.id, weekday=0),
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 409

    same_weekday_other_practitioner = create_practitioner(
        db, create_user(db, "Other", "other@example.com", "doctor")
    )
    response = client.post(
        "/api/v1/working_hours",
        json=payload(same_weekday_other_practitioner.id, weekday=0),
        headers=auth_header(client, admin.email),
    )
    assert response.status_code == 201


def test_update_to_duplicate_weekday_returns_409(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    create_working_hour(db, pract, weekday=0)
    second = create_working_hour(db, pract, weekday=1)

    response = client.patch(
        f"/api/v1/working_hours/{second.id}",
        json={"weekday": 0},
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 409

    db.expire_all()
    assert db.get(WorkingHour, second.id).weekday == 1


def test_doctor_lists_only_own_working_hours(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    wh1 = create_working_hour(db, pract1, weekday=0)
    create_working_hour(db, pract2, weekday=0)

    response = client.get(
        "/api/v1/working_hours", headers=auth_header(client, doctor1.email)
    )

    assert response.status_code == 200
    assert [w["id"] for w in response.json()] == [wh1.id]


def test_doctor_cannot_view_other_doctors_working_hour(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract2 = create_practitioner(db, doctor2)
    create_practitioner(db, doctor1)
    wh = create_working_hour(db, pract2, weekday=0)

    response = client.get(
        f"/api/v1/working_hours/{wh.id}",
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 403


def test_doctor_cannot_update_other_doctors_working_hour(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract2 = create_practitioner(db, doctor2)
    create_practitioner(db, doctor1)
    wh = create_working_hour(db, pract2, weekday=0)

    response = client.patch(
        f"/api/v1/working_hours/{wh.id}",
        json={"end_time": "18:00"},
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 403
    db.expire_all()
    assert db.get(WorkingHour, wh.id).end_time == time.fromisoformat("17:00")


def test_doctor_cannot_delete_other_doctors_working_hour(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract2 = create_practitioner(db, doctor2)
    create_practitioner(db, doctor1)
    wh = create_working_hour(db, pract2, weekday=0)

    response = client.delete(
        f"/api/v1/working_hours/{wh.id}",
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 403
    db.expire_all()
    assert db.get(WorkingHour, wh.id) is not None


def test_doctor_cannot_reassign_working_hour_to_other_practitioner(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    wh = create_working_hour(db, pract1, weekday=0)

    response = client.patch(
        f"/api/v1/working_hours/{wh.id}",
        json={"practitioner_id": pract2.id},
        headers=auth_header(client, doctor1.email),
    )

    assert response.status_code == 403
    db.expire_all()
    assert db.get(WorkingHour, wh.id).practitioner_id == pract1.id


def test_get_missing_working_hour_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    assert (
        client.get(
            "/api/v1/working_hours/99999", headers=auth_header(client, admin.email)
        ).status_code
        == 404
    )


def test_update_missing_working_hour_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    response = client.patch(
        "/api/v1/working_hours/99999",
        json={"end_time": "18:00"},
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 404


def test_delete_missing_working_hour_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")

    response = client.delete(
        "/api/v1/working_hours/99999",
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 404


def test_delete_removes_record(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    wh = create_working_hour(db, pract, weekday=0)
    create_working_hour(db, pract, weekday=1)

    response = client.delete(
        f"/api/v1/working_hours/{wh.id}",
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 204
    deleted_id = wh.id
    db.expire_all()
    assert (
        db.query(WorkingHour)
        .filter(WorkingHour.id == deleted_id)
        .first()
        is None
    )

    listing = client.get(
        "/api/v1/working_hours", headers=auth_header(client, admin.email)
    )
    assert [w["weekday"] for w in listing.json()] == [1]


def test_doctor_without_practitioner_record_gets_403(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "No Practitioner", "nopract@example.com", "doctor")
    doctor2 = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor2)
    wh = create_working_hour(db, pract, weekday=0)

    headers = auth_header(client, doctor.email)

    assert client.get("/api/v1/working_hours", headers=headers).status_code == 403
    assert client.post(
        "/api/v1/working_hours", json=payload(pract.id), headers=headers
    ).status_code == 403
    assert (
        client.get(f"/api/v1/working_hours/{wh.id}", headers=headers).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/working_hours/{wh.id}", json={}, headers=headers
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/api/v1/working_hours/{wh.id}", headers=headers).status_code
        == 403
    )


def test_admin_can_update_any_working_hour(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    wh = create_working_hour(db, pract, weekday=0)

    response = client.patch(
        f"/api/v1/working_hours/{wh.id}",
        json={"weekday": 2, "start_time": "08:00", "end_time": "16:00"},
        headers=auth_header(client, admin.email),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["weekday"] == 2
    assert data["start_time"] == "08:00:00"
    assert data["end_time"] == "16:00:00"


def test_doctor_can_manage_own_working_hours(client_and_db):
    client, db = client_and_db
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    headers = auth_header(client, doctor.email)

    created = client.post(
        "/api/v1/working_hours",
        json=payload(pract.id, weekday=0),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    wh_id = created.json()["id"]

    updated = client.patch(
        f"/api/v1/working_hours/{wh_id}",
        json={"end_time": "18:00"},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["end_time"] == "18:00:00"

    deleted = client.delete(f"/api/v1/working_hours/{wh_id}", headers=headers)
    assert deleted.status_code == 204

    listing = client.get("/api/v1/working_hours", headers=headers)
    assert listing.status_code == 200
    assert listing.json() == []


def test_openapi_contains_working_hour_paths(client_and_db):
    client, _ = client_and_db

    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    assert "/api/v1/working_hours" in paths
    assert "get" in paths["/api/v1/working_hours"]
    assert "post" in paths["/api/v1/working_hours"]
    assert "/api/v1/working_hours/{working_hour_id}" in paths
    assert "get" in paths["/api/v1/working_hours/{working_hour_id}"]
    assert "patch" in paths["/api/v1/working_hours/{working_hour_id}"]
    assert "delete" in paths["/api/v1/working_hours/{working_hour_id}"]