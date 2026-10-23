"""Create the schema and load sample bank data + knowledge base.

Idempotent by default: tables are created if missing and only populated when
empty, so re-running the Helm hook on upgrade is safe. ``--force`` drops and
recreates everything (used to reset the demo without a Kasten restore).

Usage:  python -m app.seed [--force] [--wait-seconds N]
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg

from . import db
from .config import settings
from .embeddings import get_embedder, to_pgvector

log = logging.getLogger("seed")

# All PII below is synthetic. SSNs use the 000 area number, which is never issued.
CUSTOMERS = [
    # first, last, email, phone, ssn, address, city, state, zip, dob, segment
    ("Eleanor", "Whitfield", "eleanor.whitfield@example.com", "617-555-0141", "000-41-1874", "18 Beacon Street", "Boston", "MA", "02108", "1968-03-14", "private"),
    ("Marcus", "Chen", "marcus.chen@example.com", "617-555-0128", "000-28-5531", "402 Commonwealth Avenue", "Boston", "MA", "02215", "1985-11-02", "retail"),
    ("Priya", "Raman", "priya.raman@example.com", "781-555-0163", "000-63-2209", "77 Elm Street", "Lexington", "MA", "02421", "1990-07-21", "retail"),
    ("Daniel", "Okafor", "daniel.okafor@example.com", "508-555-0117", "000-17-9048", "9 Mill Pond Road", "Worcester", "MA", "01602", "1979-01-30", "business"),
    ("Sofia", "Alvarez", "sofia.alvarez@example.com", "617-555-0195", "000-95-3316", "230 Hanover Street", "Boston", "MA", "02113", "1994-09-09", "retail"),
    ("Thomas", "Lindqvist", "thomas.lindqvist@example.com", "978-555-0152", "000-52-7781", "14 Harbor View Lane", "Gloucester", "MA", "01930", "1961-05-27", "private"),
    ("Aisha", "Rahman", "aisha.rahman@example.com", "617-555-0136", "000-36-4402", "88 Summer Street", "Somerville", "MA", "02143", "1988-12-15", "retail"),
    ("Robert", "Kowalski", "robert.kowalski@example.com", "413-555-0109", "000-09-6675", "501 Main Street", "Springfield", "MA", "01105", "1972-08-03", "business"),
    ("Hannah", "O'Brien", "hannah.obrien@example.com", "617-555-0184", "000-84-1193", "36 Dorchester Avenue", "Boston", "MA", "02127", "1999-02-19", "student"),
    ("Kenji", "Watanabe", "kenji.watanabe@example.com", "857-555-0171", "000-71-8820", "12 Kendall Square", "Cambridge", "MA", "02139", "1983-06-11", "retail"),
    ("Grace", "Mbeki", "grace.mbeki@example.com", "781-555-0146", "000-46-5057", "203 Washington Street", "Newton", "MA", "02458", "1976-10-24", "private"),
    ("Liam", "Fitzgerald", "liam.fitzgerald@example.com", "508-555-0122", "000-22-3368", "61 Ocean Avenue", "Plymouth", "MA", "02360", "1965-04-08", "retail"),
]

# (account_type, nickname, balance range)
ACCOUNT_PLANS = {
    "private": [("checking", "Everyday Checking", (18_000, 65_000)), ("savings", "Summit Savings", (120_000, 480_000)), ("money_market", "Premier Money Market", (250_000, 900_000))],
    "retail": [("checking", "Everyday Checking", (1_200, 9_500)), ("savings", "Summit Savings", (4_000, 42_000))],
    "business": [("checking", "Business Checking", (35_000, 220_000)), ("savings", "Business Savings", (50_000, 300_000))],
    "student": [("checking", "Student Checking", (150, 1_800)), ("savings", "Youth Savings", (300, 2_500))],
}

MERCHANTS = {
    "checking": [
        ("Whole Harvest Market", "Groceries", -40, -220), ("Harbor Coffee Roasters", "Dining", -4, -18),
        ("MBTA Fare", "Transport", -2.4, -90), ("Northwind Mortgage Payment", "Housing", -1850, -3200),
        ("Blue Hill Electric", "Utilities", -80, -240), ("Streamline Media Subscription", "Entertainment", -12, -22),
        ("Payroll Deposit - Employer", "Income", 2200, 6800), ("Beacon Pharmacy", "Health", -9, -75),
        ("Commonwealth Fuel", "Transport", -35, -82), ("Online Retailer", "Shopping", -18, -260),
        ("Transfer to Summit Savings", "Transfer", -200, -1500), ("Zelle Payment Received", "Income", 25, 400),
    ],
    "savings": [("Interest Credit", "Interest", 3, 95), ("Transfer from Checking", "Transfer", 200, 1500)],
    "money_market": [("Interest Credit", "Interest", 150, 900), ("Dividend Deposit", "Income", 400, 2500), ("Wire Transfer Out", "Transfer", -5000, -25000)],
}

KNOWLEDGE_PATH = Path(__file__).parent / "data" / "knowledge.json"


def wait_for_db(timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    attempt = 0
    while True:
        attempt += 1
        try:
            with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
                conn.execute("SELECT 1")
            log.info("PostgreSQL is reachable")
            return
        except Exception as exc:  # noqa: BLE001
            if time.time() > deadline:
                log.error("PostgreSQL not reachable after %ss: %s", timeout_s, exc)
                sys.exit(1)
            log.info("attempt %d: database not ready (%s); retrying in 5s", attempt, exc)
            time.sleep(5)


def wait_for_embedder(embedder, timeout_s: int):
    """When the Ollama embedder is used, wait until its model has been pulled."""
    if embedder.name != "ollama":
        return
    deadline = time.time() + timeout_s
    while True:
        try:
            embedder.embed("warm-up")
            log.info("Ollama embedding model %s is ready", embedder.model)
            return
        except Exception as exc:  # noqa: BLE001
            if time.time() > deadline:
                log.error("Ollama embedding model not ready after %ss: %s", timeout_s, exc)
                sys.exit(1)
            log.info("waiting for Ollama embedding model (%s)...", exc)
            time.sleep(10)


def seed_bank_data(conn: psycopg.Connection, rng: random.Random) -> None:
    existing = conn.execute("SELECT count(*) AS n FROM customers").fetchone()["n"]
    if existing:
        log.info("customers table already has %d rows; skipping bank data", existing)
        return

    now = datetime.now(timezone.utc)
    account_seq = 1000
    for first, last, email, phone, ssn, addr, city, state, postal, dob, segment in CUSTOMERS:
        cust = conn.execute(
            """INSERT INTO customers (first_name, last_name, email, phone, ssn, address, city, state,
                                      postal_code, date_of_birth, segment)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (first, last, email, phone, ssn, addr, city, state, postal, date.fromisoformat(dob), segment),
        ).fetchone()["id"]

        for acct_type, nickname, (lo, hi) in ACCOUNT_PLANS[segment]:
            account_seq += 1
            number = f"4{rng.randint(10, 99)}{account_seq:04d}{rng.randint(100, 999)}"
            balance = Decimal(str(round(rng.uniform(lo, hi), 2)))
            opened = date.today() - timedelta(days=rng.randint(400, 5000))
            acct_id = conn.execute(
                """INSERT INTO accounts (customer_id, account_number, account_type, nickname, balance, opened_at)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                (cust, number, acct_type, nickname, balance, opened),
            ).fetchone()["id"]

            # Generate transactions backwards from the current balance.
            running = balance
            merchants = MERCHANTS[acct_type]
            count = rng.randint(9, 14) if acct_type == "checking" else rng.randint(4, 7)
            rows = []
            posted = now - timedelta(hours=rng.randint(2, 30))
            for _ in range(count):
                name, category, lo_amt, hi_amt = rng.choice(merchants)
                amount = Decimal(str(round(rng.uniform(min(lo_amt, hi_amt), max(lo_amt, hi_amt)), 2)))
                rows.append((acct_id, posted, name, category, amount, running))
                running -= amount
                posted -= timedelta(days=rng.randint(1, 4), hours=rng.randint(0, 20))
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO transactions (account_id, posted_at, description, category, amount, balance_after)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    rows,
                )
    log.info("seeded %d customers with accounts and transactions", len(CUSTOMERS))


def seed_knowledge(conn: psycopg.Connection, embedder) -> None:
    existing = conn.execute("SELECT count(*) AS n FROM knowledge_documents").fetchone()["n"]
    if existing:
        log.info("knowledge_documents already has %d rows; skipping", existing)
        return
    docs = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    texts = [f"{d['title']}. {d['content']}" for d in docs]
    vectors = embedder.embed_batch(texts)
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO knowledge_documents (slug, title, category, content, embedding, embedder)
               VALUES (%s,%s,%s,%s,%s::vector,%s)""",
            [
                (d["slug"], d["title"], d["category"], d["content"], to_pgvector(v), embedder.name)
                for d, v in zip(docs, vectors)
            ],
        )
    log.info("seeded %d knowledge documents using the %s embedder", len(docs), embedder.name)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="drop and recreate all tables first")
    parser.add_argument("--wait-seconds", type=int, default=300, help="how long to wait for dependencies")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    wait_for_db(args.wait_seconds)
    embedder = get_embedder()
    wait_for_embedder(embedder, args.wait_seconds)

    rng = random.Random(20260106)  # deterministic data: CES 2026 opening day
    with psycopg.connect(settings.database_url, autocommit=True, row_factory=psycopg.rows.dict_row) as conn:
        if args.force:
            log.warning("--force: dropping all demo tables")
            conn.execute("DROP TABLE IF EXISTS " + ", ".join(db.ALL_TABLES) + " CASCADE")
        db.ensure_schema(conn)
        seed_bank_data(conn, rng)
        seed_knowledge(conn, embedder)
    log.info("seed complete")


if __name__ == "__main__":
    main()
