"""
PlaceMux · Phase 2 · Task 9 — Failure Handling & Resilience
create_database.py

Responsibility:
    Create and initialise the PlaceMux SQLite schema.
    Drops existing tables only when `--reset` flag is passed,
    so re-runs are safe by default.

Usage:
    python create_database.py           # create if not exists
    python create_database.py --reset   # drop & recreate
"""

import sys
import logging
from pathlib import Path

from sqlalchemy import create_engine, text
from config import DB_PATH, LOG_LEVEL

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  [%(levelname)s]  %(name)s — %(message)s",
)
log = logging.getLogger("create_database")


# ---------------------------------------------------------------------------
# DDL statements
# ---------------------------------------------------------------------------

DDL_USERS = """
CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT    PRIMARY KEY,
    signup_date  DATE    NOT NULL,
    cohort_month TEXT    NOT NULL,   -- YYYY-MM
    active       INTEGER DEFAULT 1           -- 1 = active, 0 = churned, NULL = dirty data
);
"""

DDL_PAYMENTS = """
CREATE TABLE IF NOT EXISTS payments (
    payment_id     TEXT    PRIMARY KEY,
    user_id        TEXT    NOT NULL,
    payment_date   DATE    NOT NULL,
    amount         REAL    NOT NULL,
    status         TEXT    NOT NULL,   -- success | failed | refunded | pending
    gateway        TEXT    NOT NULL,   -- stripe | razorpay | paypal | bank_transfer
    failure_reason TEXT,               -- NULL when status = 'success'
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
"""

DDL_APPLICATIONS = """
CREATE TABLE IF NOT EXISTS applications (
    application_id TEXT  PRIMARY KEY,
    user_id        TEXT  NOT NULL,
    company        TEXT  NOT NULL,
    fee            REAL  NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
"""

DDL_REVENUE_EVENTS = """
CREATE TABLE IF NOT EXISTS revenue_events (
    event_id    TEXT PRIMARY KEY,
    payment_id  TEXT NOT NULL,
    event_type  TEXT NOT NULL,    -- payment_received | refund_issued | chargeback | retry_success
    event_time  TEXT NOT NULL,    -- ISO-8601 datetime
    FOREIGN KEY (payment_id) REFERENCES payments(payment_id)
);
"""

DDL_PAYMENT_FAILURES = """
CREATE TABLE IF NOT EXISTS payment_failures (
    failure_id      TEXT    PRIMARY KEY,
    payment_id      TEXT    NOT NULL,
    user_id         TEXT    NOT NULL,
    failure_date    DATE    NOT NULL,
    failure_reason  TEXT    NOT NULL,
    gateway         TEXT    NOT NULL,
    amount          REAL    NOT NULL,
    recovered       INTEGER NOT NULL DEFAULT 0,   -- 1 = recovered via retry
    recovery_date   DATE,
    recovery_amount REAL,
    FOREIGN KEY (payment_id) REFERENCES payments(payment_id),
    FOREIGN KEY (user_id)    REFERENCES users(user_id)
);
"""

# ---------------------------------------------------------------------------
# Index DDL  (non-blocking: CREATE INDEX IF NOT EXISTS)
# ---------------------------------------------------------------------------

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_payments_user_id      ON payments(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_payments_status        ON payments(status);",
    "CREATE INDEX IF NOT EXISTS idx_payments_payment_date  ON payments(payment_date);",
    "CREATE INDEX IF NOT EXISTS idx_payments_gateway       ON payments(gateway);",
    "CREATE INDEX IF NOT EXISTS idx_applications_user_id  ON applications(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_revenue_events_pid    ON revenue_events(payment_id);",
    "CREATE INDEX IF NOT EXISTS idx_failures_user_id      ON payment_failures(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_failures_gateway      ON payment_failures(gateway);",
    "CREATE INDEX IF NOT EXISTS idx_users_cohort          ON users(cohort_month);",
]


# ---------------------------------------------------------------------------
# Helper — drop all tables (used only with --reset)
# ---------------------------------------------------------------------------

DROP_ORDER = [
    "DROP TABLE IF EXISTS payment_failures;",
    "DROP TABLE IF EXISTS revenue_events;",
    "DROP TABLE IF EXISTS applications;",
    "DROP TABLE IF EXISTS payments;",
    "DROP TABLE IF EXISTS users;",
]


def reset_schema(engine) -> None:
    """Drop all PlaceMux tables in dependency-safe order."""
    log.warning("RESET requested — dropping all tables ...")
    with engine.begin() as conn:
        for stmt in DROP_ORDER:
            conn.execute(text(stmt))
    log.info("All tables dropped.")


def create_schema(engine) -> None:
    """Create tables and indexes (idempotent -- IF NOT EXISTS guards)."""
    ddl_statements = [
        ("users",            DDL_USERS),
        ("payments",         DDL_PAYMENTS),
        ("applications",     DDL_APPLICATIONS),
        ("revenue_events",   DDL_REVENUE_EVENTS),
        ("payment_failures", DDL_PAYMENT_FAILURES),
    ]
    with engine.begin() as conn:
        # Enable WAL for better concurrency with Streamlit reads
        conn.execute(text("PRAGMA journal_mode=WAL;"))
        conn.execute(text("PRAGMA foreign_keys=ON;"))

        for table_name, ddl in ddl_statements:
            log.info("Creating table (if not exists): %s", table_name)
            conn.execute(text(ddl))

        for idx_stmt in INDEXES:
            conn.execute(text(idx_stmt))

    log.info(
        "Schema creation complete -- %d tables, %d indexes.",
        len(ddl_statements),
        len(INDEXES),
    )


def verify_schema(engine) -> bool:
    """
    Quick sanity-check: assert every expected table exists.
    Returns True if all tables found.
    """
    expected = {"users", "payments", "applications", "revenue_events", "payment_failures"}
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table';")
        ).fetchall()
    found = {r[0] for r in rows}
    missing = expected - found
    if missing:
        log.error("Missing tables after creation: %s", missing)
        return False
    log.info("Schema verified -- all tables present: %s", sorted(found))
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    reset = "--reset" in sys.argv

    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection_url = f"sqlite:///{db_path}"
    log.info("Connecting to database: %s", connection_url)
    engine = create_engine(connection_url, echo=False)

    if reset:
        reset_schema(engine)

    create_schema(engine)

    ok = verify_schema(engine)
    if not ok:
        log.critical("Schema verification FAILED. Exiting.")
        sys.exit(1)

    log.info("Database initialised successfully at: %s", db_path.resolve())


if __name__ == "__main__":
    main()
