"""PostgreSQL access (psycopg 3 + connection pool) and schema definition."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

log = logging.getLogger(__name__)

# Tables that hold the *bank's* data (targets of the "drop all tables" attack).
BANK_TABLES = ("transactions", "accounts", "customers")
# Tables that hold the *assistant's* data.
ASSISTANT_TABLES = ("knowledge_documents", "chat_sessions", "chat_messages", "audit_log")
ALL_TABLES = BANK_TABLES + ASSISTANT_TABLES

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS customers (
    id            SERIAL PRIMARY KEY,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    email         TEXT NOT NULL,
    phone         TEXT NOT NULL,
    ssn           TEXT NOT NULL,
    address       TEXT NOT NULL,
    city          TEXT NOT NULL,
    state         TEXT NOT NULL,
    postal_code   TEXT NOT NULL,
    date_of_birth DATE NOT NULL,
    segment       TEXT NOT NULL DEFAULT 'retail',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS accounts (
    id             SERIAL PRIMARY KEY,
    customer_id    INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    account_number TEXT NOT NULL UNIQUE,
    account_type   TEXT NOT NULL,
    nickname       TEXT NOT NULL,
    balance        NUMERIC(14,2) NOT NULL DEFAULT 0,
    currency       TEXT NOT NULL DEFAULT 'USD',
    status         TEXT NOT NULL DEFAULT 'open',
    opened_at      DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id            SERIAL PRIMARY KEY,
    account_id    INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    posted_at     TIMESTAMPTZ NOT NULL,
    description   TEXT NOT NULL,
    category      TEXT NOT NULL,
    amount        NUMERIC(14,2) NOT NULL,
    balance_after NUMERIC(14,2) NOT NULL
);
CREATE INDEX IF NOT EXISTS transactions_account_posted_idx ON transactions(account_id, posted_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id         SERIAL PRIMARY KEY,
    slug       TEXT NOT NULL UNIQUE,
    title      TEXT NOT NULL,
    category   TEXT NOT NULL,
    content    TEXT NOT NULL,
    embedding  vector({dim}) NOT NULL,
    embedder   TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id          UUID PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    role        TEXT NOT NULL DEFAULT 'customer',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id         SERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    sender     TEXT NOT NULL,
    content    TEXT NOT NULL,
    intent     TEXT,
    kind       TEXT NOT NULL DEFAULT 'text',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_session_idx ON chat_messages(session_id, id);

CREATE TABLE IF NOT EXISTS audit_log (
    id         SERIAL PRIMARY KEY,
    session_id UUID,
    customer_id INTEGER,
    event      TEXT NOT NULL,
    severity   TEXT NOT NULL DEFAULT 'info',
    detail     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            kwargs={"row_factory": dict_row, "autocommit": True},
            open=True,
            timeout=15,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with get_pool().connection() as conn:
        yield conn


def fetch_all(sql: str, params: Any = None) -> list[dict]:
    with connection() as conn:
        return conn.execute(sql, params).fetchall()


def fetch_one(sql: str, params: Any = None) -> dict | None:
    with connection() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql: str, params: Any = None) -> int:
    with connection() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA_SQL.format(dim=settings.embed_dim))


def table_exists(name: str) -> bool:
    row = fetch_one("SELECT to_regclass(%s) AS reg", (f"public.{name}",))
    return bool(row and row["reg"])


def table_counts() -> dict[str, int | None]:
    """Row counts per table; ``None`` when the table does not exist."""
    counts: dict[str, int | None] = {}
    with connection() as conn:
        for table in ALL_TABLES:
            reg = conn.execute("SELECT to_regclass(%s) AS reg", (f"public.{table}",)).fetchone()
            if not reg or not reg["reg"]:
                counts[table] = None
                continue
            counts[table] = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
    return counts


def audit(event: str, severity: str = "info", detail: str = "", session_id: str | None = None,
          customer_id: int | None = None) -> None:
    try:
        execute(
            "INSERT INTO audit_log (session_id, customer_id, event, severity, detail) VALUES (%s, %s, %s, %s, %s)",
            (session_id, customer_id, event, severity, detail[:2000]),
        )
    except Exception as exc:  # noqa: BLE001 — auditing must never break the request
        log.warning("audit insert failed: %s", exc)
