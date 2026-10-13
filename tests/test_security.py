from datetime import timedelta

import pytest

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_is_hashed_and_verifiable():
    password = "Test-password-123!"
    hashed = hash_password(password)

    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("Wrong-password!", hashed)


def test_jwt_contains_subject_and_role(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-more-than-32-characters")
    token = create_access_token("42", "doctor")

    payload = decode_access_token(token)

    assert payload["sub"] == "42"
    assert payload["role"] == "doctor"


def test_expired_jwt_is_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-more-than-32-characters")
    token = create_access_token(
        "42",
        "doctor",
        expires_delta=timedelta(seconds=-1),
    )

    with pytest.raises(Exception):
        decode_access_token(token)


def test_missing_jwt_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError):
        create_access_token("42", "doctor")
