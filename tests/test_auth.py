import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import User
from app.main import app


@pytest.fixture
def client_and_db(monkeypatch):
    monkeypatch.setenv(
        "JWT_SECRET_KEY",
        "test-secret-key-for-careslot-auth-tests-32-chars-minimum",
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
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    try:
        yield TestClient(app), db
    finally:
        app.dependency_overrides.clear()
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def create_user(db, email="doctor@example.com", active=True, role="doctor"):
    user = User(
        full_name="Test Doctor",
        email=email,
        password_hash=hash_password("CorrectPassword123!"),
        role=role,
        is_active=active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_valid_login_and_me(client_and_db):
    client, db = client_and_db
    user = create_user(db)

    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == user.email
    assert "password_hash" not in data["user"]

    token = data["access_token"]
    me_response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert me_response.status_code == 200
    assert me_response.json()["id"] == user.id
    assert me_response.json()["role"] == "doctor"


def test_wrong_password_returns_401(client_and_db):
    client, db = client_and_db
    create_user(db)

    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "doctor@example.com",
            "password": "WrongPassword123!",
        },
    )

    assert response.status_code == 401


def test_unknown_email_returns_401(client_and_db):
    client, _ = client_and_db

    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "unknown@example.com",
            "password": "WrongPassword123!",
        },
    )

    assert response.status_code == 401


def test_inactive_user_cannot_login(client_and_db):
    client, db = client_and_db
    create_user(db, active=False)

    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "doctor@example.com",
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 401


def test_me_requires_authentication(client_and_db):
    client, _ = client_and_db

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401


def test_me_rejects_invalid_token(client_and_db):
    client, _ = client_and_db

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401
