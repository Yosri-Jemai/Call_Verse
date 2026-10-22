import time
import logging
import psycopg2
from contextlib import contextmanager
from src.config import settings

logger = logging.getLogger("cashy.db")

NEON_RETRY_DELAY = 3  # seconds to wait for Neon cold start


def _connect():
    """Create a psycopg2 connection, retrying once on cold-start failures."""
    conn_kwargs = dict(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.resolved_db_name,
        user=settings.db_user,
        password=settings.db_password,
    )
    if settings.db_sslmode:
        conn_kwargs["sslmode"] = settings.db_sslmode

    try:
        return psycopg2.connect(**conn_kwargs)
    except psycopg2.OperationalError:
        logger.info("DB connection failed -- retrying in %ds (Neon cold start?)", NEON_RETRY_DELAY)
        time.sleep(NEON_RETRY_DELAY)
        return psycopg2.connect(**conn_kwargs)


@contextmanager
def get_connection():
    """Context manager for database connections.

    Usage:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ...")
                rows = cur.fetchall()
    """
    logger.debug("Opening DB connection")
    conn = _connect()
    try:
        yield conn
    except Exception:
        logger.warning("DB error -- rolling back")
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()
        logger.debug("DB connection closed")
