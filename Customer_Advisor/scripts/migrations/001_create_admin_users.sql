-- Migration 001 : comptes administrateurs (login + rôles)
-- Idempotente : peut être rejouée sans erreur.

BEGIN;

CREATE TABLE IF NOT EXISTS admin_users (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username        TEXT        NOT NULL,
    password_hash   TEXT        NOT NULL,              -- hash argon2, jamais le mot de passe
    role            TEXT        NOT NULL,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ,
    failed_attempts INTEGER     NOT NULL DEFAULT 0,
    locked_until    TIMESTAMPTZ,
    CONSTRAINT admin_users_role_check
        CHECK (role IN ('approver', 'viewer')),
    CONSTRAINT admin_users_username_not_blank
        CHECK (length(btrim(username)) > 0),
    CONSTRAINT admin_users_failed_attempts_check
        CHECK (failed_attempts >= 0)
);

-- Unicité du nom d'utilisateur sans tenir compte de la casse
CREATE UNIQUE INDEX IF NOT EXISTS admin_users_username_lower_uidx
    ON admin_users (lower(username));

COMMIT;