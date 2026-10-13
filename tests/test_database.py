import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, configure_mappers
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db import models  # noqa: F401
from app.db.models import Practitioner, Patient, User


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @__import__("sqlalchemy").event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)

    with Session(engine) as session:
        yield session

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_sqlalchemy_mapper_configures():
    configure_mappers()
    relationship = Practitioner.patients.property
    assert relationship.secondary.name == "doctor_patients"
    assert relationship.back_populates == "practitioners"
    inverse = Patient.practitioners.property
    assert inverse.secondary.name == "doctor_patients"
    assert inverse.back_populates == "patients"


def test_all_tables_are_created(db_session):
    inspector = inspect(db_session.get_bind())
    expected = {
        "users",
        "practitioners",
        "patients",
        "working_hours",
        "appointments",
        "queue_entries",
        "doctor_patients",
    }
    assert expected.issubset(set(inspector.get_table_names()))


def test_user_role_must_be_valid(db_session):
    db_session.add(
        User(
            full_name="Test Admin",
            email="admin@example.test",
            password_hash="not-a-real-password-hash",
            role="superuser",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_duplicate_email_is_rejected(db_session):
    db_session.add_all(
        [
            User(
                full_name="First User",
                email="same@example.test",
                password_hash="test-hash-1",
                role="admin",
            ),
            User(
                full_name="Second User",
                email="same@example.test",
                password_hash="test-hash-2",
                role="receptionist",
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_foreign_keys_are_enforced(db_session):
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO practitioners (user_id, specialty) "
                "VALUES (9999, 'General')"
            )
        )
        db_session.commit()
