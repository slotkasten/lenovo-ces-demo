"""Small helpers shared by the request handlers."""
from __future__ import annotations

import uuid
from decimal import Decimal

import psycopg

from .embeddings import get_embedder

_embedder = None


def embedder():
    global _embedder
    if _embedder is None:
        _embedder = get_embedder()
    return _embedder


def money(value) -> str:
    amount = Decimal(value or 0)
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def jsonable(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        clean = {}
        for key, value in row.items():
            if isinstance(value, Decimal):
                clean[key] = float(value)
            elif hasattr(value, "isoformat"):
                clean[key] = value.isoformat()
            elif isinstance(value, uuid.UUID):
                clean[key] = str(value)
            else:
                clean[key] = value
        out.append(clean)
    return out


class MissingData(Exception):
    """Raised when a table the handler needs no longer exists."""

    def __init__(self, table: str):
        super().__init__(table)
        self.table = table


def guard(fn, *args):
    """Run a DB call and translate 'relation does not exist' into MissingData."""
    try:
        return fn(*args)
    except psycopg.errors.UndefinedTable as exc:
        text = str(exc)
        table = text.split('"')[1] if '"' in text else "unknown"
        raise MissingData(table) from exc


def customer_display(customer: dict | None) -> str:
    if not customer:
        return "Guest"
    return f"{customer['first_name']} {customer['last_name']}"


def result(reply: str, kind: str = "text", data=None, sources=None, security_event=None, role_changed=False) -> dict:
    return {"reply": reply, "kind": kind, "data": data or [], "sources": sources or [],
            "security_event": security_event, "role_changed": role_changed}
