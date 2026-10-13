import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import Patient, Practitioner, User, doctor_patients
from app.main import app

PASSWORD = "CorrectPassword123!"


@pytest.fixture
def client_and_db(monkeypatch):
    monkeypatch.setenv(
        "JWT_SECRET_KEY",
        "test-secret-key-for-careslot-patients-tests-min-32-char",
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


def login(client, email):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_header(client, email):
    return {"Authorization": f"Bearer {login(client, email)}"}


def test_unauthorized_patient_access_returns_401(client_and_db):
    client, _ = client_and_db

    assert client.get("/api/v1/patients").status_code == 401
    assert client.post("/api/v1/patients", json={"full_name": "X"}).status_code == 401


def test_doctor_creates_patient_and_is_auto_associated(client_and_db):
    client, db = client_and_db
    doctor = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    practitioner = create_practitioner(db, doctor)
    headers = auth_header(client, doctor.email)

    response = client.post(
        "/api/v1/patients",
        json={"full_name": "Alice Martin", "phone": "0612345678"},
        headers=headers,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["full_name"] == "Alice Martin"
    assert data["phone"] == "0612345678"
    assert data["practitioner_ids"] == [practitioner.id]

    patient = db.query(Patient).filter(Patient.id == data["id"]).first()
    assert patient is not None
    assert patient.created_by_user_id == doctor.id


def test_admin_and_receptionist_can_create_and_list_patients(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    receptionist = create_user(db, "Receptionist", "rec@example.com", "receptionist")

    response = client.post(
        "/api/v1/patients",
        json={"full_name": "Bob", "phone": "0600000001"},
        headers=auth_header(client, admin.email),
    )
    assert response.status_code == 201

    response = client.post(
        "/api/v1/patients",
        json={"full_name": "Chloe", "phone": "0600000002"},
        headers=auth_header(client, receptionist.email),
    )
    assert response.status_code == 201

    response = client.get("/api/v1/patients", headers=auth_header(client, admin.email))
    assert response.status_code == 200
    assert [p["full_name"] for p in response.json()] == ["Bob", "Chloe"]


def test_doctor_lists_only_own_patients(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)

    patient_shared = create_patient(db, "Shared Patient", created_by=admin)
    patient_doc2 = create_patient(db, "Doc2 Only", "0600000009", created_by=admin)
    associate(db, pract1, patient_shared)
    associate(db, pract2, patient_shared)
    associate(db, pract2, patient_doc2)

    headers1 = auth_header(client, doctor1.email)
    response = client.get("/api/v1/patients", headers=headers1)
    assert response.status_code == 200
    assert [p["full_name"] for p in response.json()] == ["Shared Patient"]
    assert response.json()[0]["practitioner_ids"] == sorted([pract1.id, pract2.id])

    headers2 = auth_header(client, doctor2.email)
    response = client.get("/api/v1/patients", headers=headers2)
    assert response.status_code == 200
    assert [p["full_name"] for p in response.json()] == ["Shared Patient", "Doc2 Only"]


def test_patient_can_be_associated_with_multiple_doctors(client_and_db):
    client, db = client_and_db
    receptionist = create_user(db, "Receptionist", "rec@example.com", "receptionist")
    doctor1 = create_user(db, "GP", "gp@example.com", "doctor")
    doctor2 = create_user(db, "Cardiologist", "cardio@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1, "General")
    pract2 = create_practitioner(db, doctor2, "Cardiology")

    patient = create_patient(db, "Multi Doctor Patient", created_by=receptionist)

    response = client.post(
        f"/api/v1/patients/{patient.id}/associate",
        headers=auth_header(client, doctor1.email),
    )
    assert response.status_code == 200
    assert sorted(response.json()["practitioner_ids"]) == [pract1.id]

    response = client.post(
        f"/api/v1/patients/{patient.id}/associate",
        headers=auth_header(client, doctor2.email),
    )
    assert response.status_code == 200
    assert sorted(response.json()["practitioner_ids"]) == [pract1.id, pract2.id]

    response = client.get(
        f"/api/v1/patients/{patient.id}",
        headers=auth_header(client, doctor1.email),
    )
    assert response.status_code == 200
    assert sorted(response.json()["practitioner_ids"]) == [pract1.id, pract2.id]


def test_duplicate_association_returns_409(client_and_db):
    client, db = client_and_db
    receptionist = create_user(db, "Receptionist", "rec@example.com", "receptionist")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Patient", created_by=receptionist)
    associate(db, pract, patient)

    response = client.post(
        f"/api/v1/patients/{patient.id}/associate",
        headers=auth_header(client, doctor.email),
    )
    assert response.status_code == 409


def test_doctor_isolation_on_get_and_update(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    create_practitioner(db, doctor2)

    patient = create_patient(db, "Private Patient", created_by=admin)
    associate(db, pract1, patient)

    headers2 = auth_header(client, doctor2.email)
    assert client.get(f"/api/v1/patients/{patient.id}", headers=headers2).status_code == 403

    response = client.patch(
        f"/api/v1/patients/{patient.id}",
        json={"full_name": "Hacked Name"},
        headers=headers2,
    )
    assert response.status_code == 403

    response = client.patch(
        f"/api/v1/patients/{patient.id}",
        json={"full_name": "Renamed"},
        headers=auth_header(client, doctor1.email),
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "Renamed"


def test_doctor_removes_only_own_association(client_and_db):
    client, db = client_and_db
    receptionist = create_user(db, "Receptionist", "rec@example.com", "receptionist")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Shared Patient", created_by=receptionist)
    associate(db, pract1, patient)
    associate(db, pract2, patient)

    response = client.delete(
        f"/api/v1/patients/{patient.id}",
        headers=auth_header(client, doctor1.email),
    )
    assert response.status_code == 204

    db.expire_all()
    count = db.execute(
        text("SELECT COUNT(*) FROM doctor_patients WHERE patient_id = :pid")
        .bindparams(pid=patient.id),
    ).scalar()
    assert count == 1

    rows = db.execute(
        text("SELECT practitioner_id FROM doctor_patients WHERE patient_id = :pid")
        .bindparams(pid=patient.id),
    ).all()
    assert [r[0] for r in rows] == [pract2.id]

    patient_row = db.query(Patient).filter(Patient.id == patient.id).first()
    assert patient_row is not None
    assert patient_row.is_deleted is False

    response = client.get(
        f"/api/v1/patients/{patient.id}",
        headers=auth_header(client, doctor2.email),
    )
    assert response.status_code == 200

    response = client.get(
        f"/api/v1/patients/{patient.id}",
        headers=auth_header(client, doctor1.email),
    )
    assert response.status_code == 403


def test_disassociate_endpoint_removes_only_doctor_association(client_and_db):
    client, db = client_and_db
    receptionist = create_user(db, "Receptionist", "rec@example.com", "receptionist")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract1 = create_practitioner(db, doctor1)
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Shared Patient", created_by=receptionist)
    associate(db, pract1, patient)
    associate(db, pract2, patient)

    response = client.delete(
        f"/api/v1/patients/{patient.id}/associate",
        headers=auth_header(client, doctor1.email),
    )
    assert response.status_code == 204

    rows = db.execute(
        text("SELECT practitioner_id FROM doctor_patients WHERE patient_id = :pid")
        .bindparams(pid=patient.id),
    ).all()
    assert [r[0] for r in rows] == [pract2.id]
    assert db.query(Patient).filter(Patient.id == patient.id).first() is not None

    response = client.delete(
        f"/api/v1/patients/{patient.id}/associate",
        headers=auth_header(client, doctor1.email),
    )
    assert response.status_code == 404


def test_doctor_cannot_delete_another_doctors_patient(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor1 = create_user(db, "Doctor One", "doc1@example.com", "doctor")
    doctor2 = create_user(db, "Doctor Two", "doc2@example.com", "doctor")
    pract2 = create_practitioner(db, doctor2)
    patient = create_patient(db, "Doc2 Patient", created_by=admin)
    associate(db, pract2, patient)

    response = client.delete(
        f"/api/v1/patients/{patient.id}",
        headers=auth_header(client, doctor1.email),
    )
    assert response.status_code == 403
    db.expire_all()
    assert db.query(Patient).filter(Patient.id == patient.id).first() is not None


def test_receptionist_soft_deletes_patient(client_and_db):
    client, db = client_and_db
    receptionist = create_user(db, "Receptionist", "rec@example.com", "receptionist")
    patient = create_patient(db, "To Delete", created_by=receptionist)

    response = client.delete(
        f"/api/v1/patients/{patient.id}",
        headers=auth_header(client, receptionist.email),
    )
    assert response.status_code == 204

    db.expire_all()
    row = db.query(Patient).filter(Patient.id == patient.id).first()
    assert row is not None
    assert row.is_deleted is True

    assert client.get(
        f"/api/v1/patients/{patient.id}",
        headers=auth_header(client, receptionist.email),
    ).status_code == 404

    response = client.get("/api/v1/patients", headers=auth_header(client, receptionist.email))
    assert response.status_code == 200
    assert response.json() == []


def test_admin_can_view_any_patient(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    doctor = create_user(db, "Doctor", "doc@example.com", "doctor")
    pract = create_practitioner(db, doctor)
    patient = create_patient(db, "Some Patient", created_by=admin)
    associate(db, pract, patient)

    response = client.get(
        f"/api/v1/patients/{patient.id}",
        headers=auth_header(client, admin.email),
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "Some Patient"


def test_doctor_without_practitioner_record_gets_403(client_and_db):
    client, db = client_and_db
    doctor = create_user(db, "No Practitioner", "nopract@example.com", "doctor")
    headers = auth_header(client, doctor.email)

    assert client.get("/api/v1/patients", headers=headers).status_code == 403
    assert client.post(
        "/api/v1/patients", json={"full_name": "X"}, headers=headers
    ).status_code == 403

    receptionist = create_user(db, "Receptionist", "rec@example.com", "receptionist")
    patient = create_patient(db, "Patient", created_by=receptionist)
    assert client.post(
        f"/api/v1/patients/{patient.id}/associate", headers=headers
    ).status_code == 403


def test_receptionist_can_update_patient_details(client_and_db):
    client, db = client_and_db
    receptionist = create_user(db, "Receptionist", "rec@example.com", "receptionist")
    patient = create_patient(db, "Old Name", created_by=receptionist)

    response = client.patch(
        f"/api/v1/patients/{patient.id}",
        json={"phone": "0600000777"},
        headers=auth_header(client, receptionist.email),
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "Old Name"
    assert response.json()["phone"] == "0600000777"


def test_get_missing_patient_returns_404(client_and_db):
    client, db = client_and_db
    admin = create_user(db, "Admin", "admin@example.com", "admin")
    assert client.get(
        "/api/v1/patients/99999", headers=auth_header(client, admin.email)
    ).status_code == 404