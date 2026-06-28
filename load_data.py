"""
PlaceMux · Phase 2 · Task 9 — Failure Handling & Resilience
load_data.py

Responsibility:
    Read CSVs from data/ and bulk-load them into the SQLite database.
    Also injects retry_success revenue events for recovered failures.

Load order (respects FK dependencies):
    1. users
    2. payments
    3. applications
    4. revenue_events
    5. payment_failures
    6. retry_success events  (derived — appended to revenue_events)

Safety:
    - Idempotent: truncates target tables before each load (full-refresh).
    - Validates row counts post-load vs CSV source.
    - Logs every step with timing.

Usage:
    python load_data.py
    python load_data.py --skip-truncate   # append mode (dev only)
"""

import sys
import time
import logging
import uuid
from pathlib import Path
from datetime import date, datetime, timedelta
import random

import pandas as pd
from sqlalchemy import create_engine, text

from config import (
    DB_URL,
    DATA_DIR,
    RANDOM_SEED,
    LOG_LEVEL,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  [%(levelname)s]  %(name)s — %(message)s",
)
log = logging.getLogger("load_data")

random.seed(RANDOM_SEED)

# ---------------------------------------------------------------------------
# Load order — (csv_stem, table_name, fk_parent_tables)
# ---------------------------------------------------------------------------
LOAD_PLAN = [
    ("users",            "users",            []),
    ("payments",         "payments",         ["users"]),
    ("applications",     "applications",     ["users"]),
    ("revenue_events",   "revenue_events",   ["payments"]),
    ("payment_failures", "payment_failures", ["payments", "users"]),
]

# ---------------------------------------------------------------------------
# Column dtype maps — ensure SQLite receives correct types
# ---------------------------------------------------------------------------
DTYPE_MAP: dict[str, dict] = {
    "users": {
        "user_id":      str,
        "signup_date":  str,
        "cohort_month": str,
        "active":       "Int64",     # nullable integer
    },
    "payments": {
        "payment_id":     str,
        "user_id":        str,
        "payment_date":   str,
        "amount":         float,
        "status":         str,
        "gateway":        str,
        "failure_reason": str,
    },
    "applications": {
        "application_id": str,
        "user_id":        str,
        "company":        str,
        "fee":            float,
    },
    "revenue_events": {
        "event_id":   str,
        "payment_id": str,
        "event_type": str,
        "event_time": str,
    },
    "payment_failures": {
        "failure_id":      str,
        "payment_id":      str,
        "user_id":         str,
        "failure_date":    str,
        "failure_reason":  str,
        "gateway":         str,
        "amount":          float,
        "recovered":       "Int64",
        "recovery_date":   str,
        "recovery_amount": float,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_csv(name: str) -> pd.DataFrame:
    """Read and type-cast a dataset CSV."""
    path = DATA_DIR / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"CSV not found: {path}. Run generate_data.py first."
        )
    dtypes = DTYPE_MAP.get(name, {})
    # Separate nullable-int columns (pandas extension type) from normal dtypes
    ext_cols = {k: v for k, v in dtypes.items() if v == "Int64"}
    std_cols  = {k: v for k, v in dtypes.items() if v != "Int64"}

    df = pd.read_csv(path, dtype=std_cols, low_memory=False)

    for col, dtype in ext_cols.items():
        if col in df.columns:
            df[col] = pd.array(df[col], dtype=dtype)

    log.debug("Read %s: %d rows, %d cols", name, len(df), len(df.columns))
    return df


def truncate_table(conn, table: str) -> None:
    """Delete all rows from table (SQLite has no TRUNCATE)."""
    conn.execute(text(f"DELETE FROM {table};"))
    log.info("Truncated table: %s", table)


def get_db_count(engine, table: str) -> int:
    """Return current row count for a table."""
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) FROM {table};"))
        return result.scalar()


def validate_load(engine, table: str, expected: int) -> bool:
    """Assert DB row count matches expected. Returns True if OK."""
    actual = get_db_count(engine, table)
    if actual != expected:
        log.error(
            "Row count mismatch for %s: expected %d, got %d",
            table, expected, actual,
        )
        return False
    log.info("Validated %s: %d rows loaded correctly.", table, actual)
    return True


# ---------------------------------------------------------------------------
# Retry-success event injection
# ---------------------------------------------------------------------------

def build_retry_success_events(engine) -> pd.DataFrame:
    """
    For every recovered payment failure, generate a retry_success
    revenue event dated on the recovery_date.
    These are appended to revenue_events after the initial load.
    """
    sql = """
        SELECT
            pf.payment_id,
            pf.recovery_date
        FROM payment_failures pf
        WHERE pf.recovered = 1
          AND pf.recovery_date IS NOT NULL
    """
    with engine.connect() as conn:
        recovered = pd.read_sql(sql, conn)

    if recovered.empty:
        log.info("No recovered failures found — skipping retry_success events.")
        return pd.DataFrame()

    records = []
    for _, row in recovered.iterrows():
        try:
            rec_date = date.fromisoformat(row["recovery_date"])
        except (TypeError, ValueError):
            continue

        event_time = datetime(
            rec_date.year, rec_date.month, rec_date.day,
            random.randint(0, 23), random.randint(0, 59), random.randint(0, 59),
        ).isoformat()

        records.append({
            "event_id":   str(uuid.uuid4()),
            "payment_id": row["payment_id"],
            "event_type": "retry_success",
            "event_time": event_time,
        })

    df = pd.DataFrame(records)
    log.info("Built %d retry_success events for recovered failures.", len(df))
    return df


# ---------------------------------------------------------------------------
# Core load function
# ---------------------------------------------------------------------------

def load_table(
    engine,
    name: str,
    table: str,
    skip_truncate: bool = False,
) -> int:
    """
    Read CSV → optionally truncate → bulk insert → validate.
    Returns number of rows loaded.
    """
    t0  = time.perf_counter()
    df  = read_csv(name)
    src_rows = len(df)

    with engine.begin() as conn:
        if not skip_truncate:
            truncate_table(conn, table)

        # pandas to_sql with method='multi' for bulk insert performance
        df.to_sql(
            table,
            con=conn,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=500,
        )

    elapsed = time.perf_counter() - t0
    ok = validate_load(engine, table, src_rows)
    status = "OK" if ok else "MISMATCH"
    log.info(
        "Loaded %-22s  %6d rows  %.2fs  [%s]",
        table, src_rows, elapsed, status,
    )
    return src_rows if ok else -1


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def load_all(skip_truncate: bool = False) -> bool:
    """
    Execute the full load plan in FK-safe order.
    Returns True if all tables loaded without error.
    """
    log.info("=== PlaceMux load starting (skip_truncate=%s) ===", skip_truncate)
    engine  = create_engine(DB_URL, echo=False)
    success = True

    # Disable FK checks during bulk load for speed, re-enable after
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF;"))

    try:
        for csv_name, table_name, _parents in LOAD_PLAN:
            rows = load_table(engine, csv_name, table_name, skip_truncate)
            if rows == -1:
                log.error("Load FAILED for table: %s", table_name)
                success = False

        # --- Retry-success events (derived, appended after payment_failures) ---
        retry_df = build_retry_success_events(engine)
        if not retry_df.empty:
            t0 = time.perf_counter()
            with engine.begin() as conn:
                retry_df.to_sql(
                    "revenue_events",
                    con=conn,
                    if_exists="append",
                    index=False,
                    method="multi",
                    chunksize=500,
                )
            elapsed = time.perf_counter() - t0
            log.info(
                "Appended %-18s  %6d rows  %.2fs  [OK]",
                "retry_success events", len(retry_df), elapsed,
            )

    finally:
        # Always re-enable FK enforcement
        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON;"))

    # --- Final row count report ---
    log.info("=== Post-load row counts ===")
    all_tables = [t for _, t, _ in LOAD_PLAN]
    for tbl in all_tables:
        count = get_db_count(engine, tbl)
        log.info("  %-22s  %d rows", tbl, count)

    if success:
        log.info("=== Load complete — all tables OK ===")
    else:
        log.error("=== Load complete — ONE OR MORE tables FAILED ===")

    return success


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    skip = "--skip-truncate" in sys.argv
    ok   = load_all(skip_truncate=skip)
    sys.exit(0 if ok else 1)
