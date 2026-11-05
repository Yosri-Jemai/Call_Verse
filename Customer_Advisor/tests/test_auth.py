from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src import api
from src.auth import dependencies, repository
from src.auth.security import create_access_token, hash_password, verify_password
from src.config import settings


@pytest.fixture
def configured_jwt(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "jwt_issuer", "cashy-agent-test")
    monkeypatch.setattr(settings, "jwt_audience", "cashy-api-test")
    monkeypatch.setattr(settings, "jwt_expire_minutes", 30)


def make_user(**overrides):
    user = {
        "id": 7,
        "username": "reviewer",
        "password_hash": hash_password("Correct horse battery staple"),
        "role": "approver",
        "is_active": True,
        "failed_attempts": 0,
        "locked_until": None,
    }
    user.update(overrides)
    return user


def test_password_hashing_uses_argon2():
    encoded = hash_password("strong demo passphrase")

    assert encoded.startswith("$argon2")
    assert verify_password("strong demo passphrase", encoded)
    assert not verify_password("wrong passphrase", encoded)


def test_jwt_contains_required_claims_and_expires(configured_jwt):
    token, expires_at = create_access_token(7, "approver")
    claims = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )

    assert claims["sub"] == "7"
    assert claims["role"] == "approver"
    assert claims["iss"] == settings.jwt_issuer
    assert claims["aud"] == settings.jwt_audience
    assert datetime.fromtimestamp(claims["exp"], timezone.utc) == expires_at
    remaining = expires_at - datetime.now(timezone.utc)
    assert timedelta(minutes=29, seconds=59) <= remaining <= timedelta(minutes=30)


def test_jwt_rejects_short_secret(configured_jwt, monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "too-short")

    with pytest.raises(RuntimeError, match="32 bytes"):
        create_access_token(7, "approver")


def test_login_uses_same_error_for_unknown_and_wrong_password(configured_jwt, monkeypatch):
    user = make_user()
    monkeypatch.setattr(
        repository,
        "get_user_by_username",
        lambda username: user if username == "reviewer" else None,
    )
    failed_attempts = []
    monkeypatch.setattr(repository, "record_failed_attempt", failed_attempts.append)

    client = TestClient(api.app)
    unknown = client.post(
        "/auth/login",
        json={"username": "missing", "password": "Correct horse battery staple"},
    )
    wrong = client.post(
        "/auth/login",
        json={"username": "reviewer", "password": "wrong"},
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json() == {"detail": "Invalid username or password."}
    assert failed_attempts == ["reviewer"]


def test_login_issues_token_and_resets_failed_attempts(configured_jwt, monkeypatch):
    user = make_user()
    updated = []
    monkeypatch.setattr(repository, "get_user_by_username", lambda username: user)
    monkeypatch.setattr(
        repository,
        "record_successful_login",
        lambda user_id: updated.append(user_id) or user,
    )

    response = TestClient(api.app).post(
        "/auth/login",
        json={"username": "Reviewer", "password": "Correct horse battery staple"},
    )

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"]
    assert updated == [7]


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_active": False},
        {"locked_until": datetime.now(timezone.utc) + timedelta(minutes=1)},
    ],
)
def test_login_rejects_inactive_or_locked_accounts(configured_jwt, monkeypatch, overrides):
    user = make_user(**overrides)
    failed_attempts = []
    monkeypatch.setattr(repository, "get_user_by_username", lambda username: user)
    monkeypatch.setattr(repository, "record_failed_attempt", failed_attempts.append)

    response = TestClient(api.app).post(
        "/auth/login",
        json={"username": "reviewer", "password": "Correct horse battery staple"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password."}
    assert failed_attempts == []


def test_login_fails_closed_when_jwt_secret_is_missing(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "")
    user = make_user()
    monkeypatch.setattr(repository, "get_user_by_username", lambda username: user)
    monkeypatch.setattr(repository, "record_successful_login", lambda user_id: user)

    response = TestClient(api.app).post(
        "/auth/login",
        json={"username": "reviewer", "password": "Correct horse battery staple"},
    )

    assert response.status_code == 503


def test_me_uses_current_active_user_from_database(configured_jwt, monkeypatch):
    token, _ = create_access_token(7, "viewer")
    current_user = {
        "id": 7,
        "username": "reviewer",
        "role": "approver",
        "is_active": True,
    }
    monkeypatch.setattr(repository, "get_active_user_by_id", lambda user_id: current_user)

    response = TestClient(api.app).get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"id": 7, "username": "reviewer", "role": "approver"}


def test_me_rejects_missing_or_invalid_token(configured_jwt):
    client = TestClient(api.app)

    assert client.get("/auth/me").status_code == 401
    assert client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid"},
    ).status_code == 401


def test_auth_cors_allows_authorization_header():
    response = TestClient(api.app).options(
        "/auth/me",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 200
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_require_role_checks_database_role():
    dependency = dependencies.require_role("approver")
    viewer = {
        "id": 8,
        "username": "reader",
        "role": "viewer",
        "is_active": True,
    }

    with pytest.raises(HTTPException) as exc_info:
        dependency(user=viewer)

    assert exc_info.value.status_code == 403


def test_failed_attempt_repository_applies_five_attempt_lockout(monkeypatch):
    class Cursor:
        query = ""
        params = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def execute(self, query, params):
            self.query = query
            self.params = params

    cursor = Cursor()

    @contextmanager
    def connection():
        yield SimpleNamespace(cursor=lambda: cursor)

    monkeypatch.setattr(repository, "get_connection", connection)
    repository.record_failed_attempt("reviewer")

    assert "failed_attempts + 1 >= %s" in cursor.query
    assert "INTERVAL '15 minutes'" in cursor.query
    assert cursor.params == (5, "reviewer")
    assert "lower(username) = lower(%s)" in cursor.query
