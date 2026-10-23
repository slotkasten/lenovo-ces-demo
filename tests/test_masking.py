"""PII masking tests."""
from app.masking import mask_ssn, mask_email, mask_phone, mask_account, mask_customer


def test_mask_ssn():
    assert mask_ssn("000-41-1874") == "***-**-1874"
    assert mask_ssn("000411874") == "***-**-1874"
    assert mask_ssn("") == ""
    assert mask_ssn(None) == ""


def test_mask_email():
    assert mask_email("eleanor.whitfield@example.com").endswith("@example.com")
    assert mask_email("eleanor.whitfield@example.com").startswith("e")
    assert "*" in mask_email("eleanor.whitfield@example.com")
    assert mask_email("ab@x.com") == "a*@x.com"


def test_mask_phone():
    assert mask_phone("617-555-0141") == "***-***-0141"


def test_mask_account():
    assert mask_account("4123456789") == "•••• 6789"


def test_mask_customer_hides_pii():
    row = {"first_name": "Eleanor", "ssn": "000-41-1874", "email": "e.w@example.com",
           "phone": "617-555-0141", "address": "18 Beacon Street"}
    m = mask_customer(row)
    assert m["ssn"] == "***-**-1874"
    assert "*" in m["email"]
    assert m["address"] == "[on file]"
    assert m["first_name"] == "Eleanor"  # non-PII preserved
    assert row["ssn"] == "000-41-1874"   # original not mutated
