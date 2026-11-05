from datetime import datetime
from typing import TypedDict

from src.db.connection import get_connection


class AuthUser(TypedDict):
    id: int
    username: str
    role: str
    is_active: bool


class LoginUser(AuthUser):
    password_hash: str
    failed_attempts: int
    locked_until: datetime | None


def get_user_by_username(username: str) -> LoginUser | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, password_hash, role, is_active,
                       failed_attempts, locked_until
                FROM admin_users
                WHERE lower(username) = lower(%s)
                """,
                (username,),
            )
            row = cur.fetchone()

    if row is None:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "password_hash": row[2],
        "role": row[3],
        "is_active": row[4],
        "failed_attempts": row[5],
        "locked_until": row[6],
    }


def get_active_user_by_id(user_id: int) -> AuthUser | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, role, is_active
                FROM admin_users
                WHERE id = %s AND is_active = TRUE
                """,
                (user_id,),
            )
            row = cur.fetchone()

    if row is None:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "role": row[2],
        "is_active": row[3],
    }


def record_failed_attempt(username: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE admin_users
                SET failed_attempts = failed_attempts + 1,
                    locked_until = CASE
                        WHEN failed_attempts + 1 >= %s
                        THEN CURRENT_TIMESTAMP + INTERVAL '15 minutes'
                        ELSE locked_until
                    END
                WHERE lower(username) = lower(%s)
                """,
                (5, username),
            )


def record_successful_login(user_id: int) -> AuthUser | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE admin_users
                SET failed_attempts = 0,
                    locked_until = NULL,
                    last_login_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND is_active = TRUE
                  AND (locked_until IS NULL OR locked_until <= CURRENT_TIMESTAMP)
                RETURNING id, username, role, is_active
                """,
                (user_id,),
            )
            row = cur.fetchone()

    if row is None:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "role": row[2],
        "is_active": row[3],
    }
