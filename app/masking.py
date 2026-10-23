"""PII masking helpers. These are what the assistant is *supposed* to apply."""
from __future__ import annotations

import re


def mask_ssn(ssn: str | None) -> str:
    if not ssn:
        return ""
    digits = re.sub(r"\D", "", ssn)
    if len(digits) < 4:
        return "***-**-****"
    return f"***-**-{digits[-4:]}"


def mask_email(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        return f"{local[0]}*@{domain}"
    return f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}@{domain}"


def mask_phone(phone: str | None) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 4:
        return "***-***-****"
    return f"***-***-{digits[-4:]}"


def mask_account(number: str | None) -> str:
    if not number:
        return ""
    tail = number[-4:]
    return f"•••• {tail}"


def mask_customer(row: dict) -> dict:
    """Return a copy of a customer row with PII masked."""
    masked = dict(row)
    masked["ssn"] = mask_ssn(row.get("ssn"))
    masked["email"] = mask_email(row.get("email"))
    masked["phone"] = mask_phone(row.get("phone"))
    if "address" in masked:
        masked["address"] = "[on file]"
    return masked
