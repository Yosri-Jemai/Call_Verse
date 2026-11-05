import logging
from datetime import datetime, timezone

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from src.auth import repository
from src.auth.dependencies import get_current_user
from src.auth.repository import AuthUser
from src.auth.security import (
    create_access_token,
    dummy_password_verify,
    validate_jwt_configuration,
    verify_password,
)

logger = logging.getLogger("cashy.auth")
router = APIRouter(prefix="/auth", tags=["authentication"])
INVALID_CREDENTIALS = "Invalid username or password."


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class UserResponse(BaseModel):
    id: int
    username: str
    role: str


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=INVALID_CREDENTIALS,
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    try:
        validate_jwt_configuration()
    except RuntimeError as exc:
        logger.error("Authentication configuration error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        ) from exc

    if not payload.username:
        dummy_password_verify(payload.password)
        raise _invalid_credentials()

    try:
        user = repository.get_user_by_username(payload.username)
        if user is None:
            dummy_password_verify(payload.password)
            raise _invalid_credentials()

        password_matches = verify_password(payload.password, user["password_hash"])
        locked_until = user["locked_until"]
        is_locked = (
            locked_until is not None
            and locked_until > datetime.now(timezone.utc)
        )
        if not password_matches or not user["is_active"] or is_locked:
            if user["is_active"] and not is_locked and not password_matches:
                repository.record_failed_attempt(payload.username)
            raise _invalid_credentials()

        refreshed_user = repository.record_successful_login(user["id"])
        if refreshed_user is None:
            raise _invalid_credentials()
        token, expires_at = create_access_token(
            refreshed_user["id"],
            refreshed_user["role"],
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        logger.error("Authentication configuration error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        ) from exc
    except ValueError as exc:
        logger.error("Could not issue an access token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured correctly.",
        ) from exc
    except psycopg2.Error as exc:
        logger.exception("Authentication database operation failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is unavailable.",
        ) from exc

    return TokenResponse(access_token=token, expires_at=expires_at)


@router.get("/me", response_model=UserResponse)
def me(user: AuthUser = Depends(get_current_user)) -> UserResponse:
    return UserResponse(id=user["id"], username=user["username"], role=user["role"])


@router.post("/logout")
def logout(user: AuthUser = Depends(get_current_user)) -> dict[str, str]:
    del user
    return {"message": "Logged out. Discard the access token on the client."}
