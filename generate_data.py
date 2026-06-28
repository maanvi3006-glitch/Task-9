"""
PlaceMux · Phase 2 · Task 9 — Failure Handling & Resilience
generate_data.py

Responsibility:
    Generate realistic synthetic datasets and persist them as CSVs
    under data/.  All randomness is seeded for reproducibility.

Datasets produced:
    data/users.csv
    data/payments.csv
    data/applications.csv
    data/revenue_events.csv
    data/payment_failures.csv

Realism features baked in:
    - Cohort-aware signup dates (12-month window)
    - Payment amount spikes on configured spike months
    - Deliberate duplicate rows  (DUPLICATE_RATE from config)
    - Deliberate NULL injection  (NULL_RATES from config)
    - Gateway-specific failure reasons
    - Failed → recovered payment chains
    - Revenue events linked to every payment

Usage:
    python generate_data.py
"""

import uuid
import random
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

from config import (
    DATA_DIR,
    NUM_USERS,
    NUM_PAYMENTS,
    NUM_APPLICATIONS,
    RANDOM_SEED,
    PAYMENT_STATUS_WEIGHTS,
    GATEWAY_WEIGHTS,
    FAILURE_REASONS,
    AMOUNT_MIN,
    AMOUNT_MAX,
    SPIKE_MONTHS,
    DUPLICATE_RATE,
    NULL_RATES,
    RECOVERY_RATE,
    EVENT_TYPES,
    LOG_LEVEL,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  [%(levelname)s]  %(name)s — %(message)s",
)
log = logging.getLogger("generate_data")

# ---------------------------------------------------------------------------
# Seed all RNGs for reproducibility
# ---------------------------------------------------------------------------
random.seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)
fake = Faker("en_IN")
Faker.seed(RANDOM_SEED)

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
SIM_START = date(2023, 1, 1)
SIM_END   = date(2024, 12, 31)
SIM_DAYS  = (SIM_END - SIM_START).days


def random_date(start: date = SIM_START, end: date = SIM_END) -> date:
    """Return a uniformly random date in [start, end]."""
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def random_datetime(base: date) -> str:
    """Return an ISO-8601 datetime string on the given date."""
    t = datetime(
        base.year, base.month, base.day,
        random.randint(0, 23),
        random.randint(0, 59),
        random.randint(0, 59),
    )
    return t.isoformat()


def cohort_month(d: date) -> str:
    """Return YYYY-MM string for cohort bucketing."""
    return d.strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Weighted-choice helpers
# ---------------------------------------------------------------------------
def weighted_choice(weight_dict: dict) -> str:
    """Pick a key from a {key: weight} dict."""
    keys   = list(weight_dict.keys())
    probs  = list(weight_dict.values())
    return random.choices(keys, weights=probs, k=1)[0]


# ---------------------------------------------------------------------------
# 1. USERS
# ---------------------------------------------------------------------------

def generate_users(n: int) -> pd.DataFrame:
    """
    Generate n user rows.
    ~1 % of rows will have NULL in 'active' to simulate dirty data.
    """
    log.info("Generating %d users ...", n)
    records = []
    for _ in range(n):
        signup = random_date()
        active: int | None = 1 if random.random() > 0.25 else 0   # 75 % active

        # Inject NULL active flag per config rate
        if random.random() < NULL_RATES.get("users.active", 0.01):
            active = None

        records.append({
            "user_id":      str(uuid.uuid4()),
            "signup_date":  signup.isoformat(),
            "cohort_month": cohort_month(signup),
            "active":       active,
        })

    df = pd.DataFrame(records)
    log.info("Users generated: %d rows", len(df))
    return df


# ---------------------------------------------------------------------------
# 2. PAYMENTS
# ---------------------------------------------------------------------------

def _payment_amount(payment_date: date) -> float:
    """
    Return a payment amount.
    Spike months have 2x higher amounts to simulate campaign surges.
    """
    base = round(random.uniform(AMOUNT_MIN, AMOUNT_MAX), 2)
    if payment_date.month in SPIKE_MONTHS:
        base = round(base * random.uniform(1.5, 2.5), 2)
    return base


def generate_payments(n: int, user_ids: list[str], users_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Generate n payment rows linked to existing users.

    Includes:
        - Realistic status & gateway distributions
        - Gateway-specific failure reasons
        - Spike months with higher amounts
        - Deliberate duplicate injection
    """
    log.info("Generating %d payments ...", n)

    statuses  = list(PAYMENT_STATUS_WEIGHTS.keys())
    s_weights = list(PAYMENT_STATUS_WEIGHTS.values())

    gateways  = list(GATEWAY_WEIGHTS.keys())
    g_weights = list(GATEWAY_WEIGHTS.values())

    # Gateway → plausible failure reasons
    gateway_failure_map = {
        "stripe":        ["card_declined", "fraud_detected", "invalid_card", "expired_card"],
        "razorpay":      ["insufficient_funds", "bank_error", "gateway_timeout", "network_error"],
        "paypal":        ["card_declined", "fraud_detected", "network_error", "invalid_card"],
        "bank_transfer": ["bank_error", "gateway_timeout", "insufficient_funds"],
    }

    # Build signup-date floor so payments never predate their user's signup
    signup_floor: dict[str, date] = {}
    if users_df is not None:
        for _, urow in users_df.iterrows():
            signup_floor[urow["user_id"]] = date.fromisoformat(urow["signup_date"])

    records = []
    for _ in range(n):
        uid      = random.choice(user_ids)
        floor    = signup_floor.get(uid, SIM_START)
        pay_date = random_date(start=floor)           # never before signup
        gateway  = random.choices(gateways, weights=g_weights, k=1)[0]
        status   = random.choices(statuses, weights=s_weights, k=1)[0]
        amount   = _payment_amount(pay_date)

        # Failure reason only makes sense for failed / refunded
        failure_reason = None
        if status in ("failed", "refunded"):
            failure_reason = random.choice(gateway_failure_map[gateway])

        records.append({
            "payment_id":     str(uuid.uuid4()),
            "user_id":        uid,
            "payment_date":   pay_date.isoformat(),
            "amount":         amount,
            "status":         status,
            "gateway":        gateway,
            "failure_reason": failure_reason,
        })

    df = pd.DataFrame(records)

    # ------------------------------------------------------------------
    # Inject duplicates  (DUPLICATE_RATE fraction of rows)
    # ------------------------------------------------------------------
    n_dupes = max(1, int(len(df) * DUPLICATE_RATE))
    log.info("Injecting %d duplicate payment rows ...", n_dupes)
    dupe_rows = df.sample(n=n_dupes, random_state=RANDOM_SEED).copy()
    # Keep same data, new payment_id so PK stays unique per row
    dupe_rows["payment_id"] = [str(uuid.uuid4()) for _ in range(n_dupes)]
    df = pd.concat([df, dupe_rows], ignore_index=True)

    log.info("Payments generated: %d rows (incl. %d dupes)", len(df), n_dupes)
    return df


# ---------------------------------------------------------------------------
# 3. APPLICATIONS
# ---------------------------------------------------------------------------

COMPANY_SECTORS = [
    "FinTech", "EdTech", "HealthTech", "SaaS", "E-Commerce",
    "Logistics", "CleanTech", "Gaming", "Media", "Consulting",
]


def generate_applications(n: int, user_ids: list[str]) -> pd.DataFrame:
    """
    Generate n application rows.
    Each application has a fee that the user paid to apply to a company.
    """
    log.info("Generating %d applications ...", n)
    records = []
    for _ in range(n):
        sector = random.choice(COMPANY_SECTORS)
        fee    = round(random.uniform(199.0, 4999.0), 2)
        records.append({
            "application_id": str(uuid.uuid4()),
            "user_id":        random.choice(user_ids),
            "company":        f"{fake.company()} ({sector})",
            "fee":            fee,
        })

    df = pd.DataFrame(records)
    log.info("Applications generated: %d rows", len(df))
    return df


# ---------------------------------------------------------------------------
# 4. REVENUE EVENTS
# ---------------------------------------------------------------------------

# Map payment status → most likely event type
STATUS_TO_EVENT = {
    "success":  "payment_received",
    "failed":   "payment_received",    # event fires; then failure logged separately
    "refunded": "refund_issued",
    "pending":  "payment_received",
}


def generate_revenue_events(payments_df: pd.DataFrame) -> pd.DataFrame:
    """
    Generate one revenue event per payment row.
    A small fraction of successful payments also get a chargeback event.
    Recovered payments (see payment_failures) will add retry_success events later.
    """
    log.info("Generating revenue events for %d payments ...", len(payments_df))
    records = []
    for _, row in payments_df.iterrows():
        pay_date  = date.fromisoformat(row["payment_date"])
        event_type = STATUS_TO_EVENT.get(row["status"], "payment_received")

        records.append({
            "event_id":   str(uuid.uuid4()),
            "payment_id": row["payment_id"],
            "event_type": event_type,
            "event_time": random_datetime(pay_date),
        })

        # ~3 % of successful payments also generate a chargeback
        if row["status"] == "success" and random.random() < 0.03:
            chargeback_date = pay_date + timedelta(days=random.randint(1, 30))
            if chargeback_date <= SIM_END:
                records.append({
                    "event_id":   str(uuid.uuid4()),
                    "payment_id": row["payment_id"],
                    "event_type": "chargeback",
                    "event_time": random_datetime(chargeback_date),
                })

    df = pd.DataFrame(records)
    log.info("Revenue events generated: %d rows", len(df))
    return df


# ---------------------------------------------------------------------------
# 5. PAYMENT FAILURES
# ---------------------------------------------------------------------------

def generate_payment_failures(
    payments_df: pd.DataFrame,
    users_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build a dedicated payment_failures table from all failed payments.
    RECOVERY_RATE fraction of failures are marked as recovered (retried).
    A retry_success event is NOT added here — that happens via load_data.py
    so revenue_events stays consistent.
    """
    log.info("Generating payment failure records ...")

    failed = payments_df[payments_df["status"] == "failed"].copy()
    log.info("Source failed payments: %d rows", len(failed))

    user_set = set(users_df["user_id"].tolist())
    records  = []

    for _, row in failed.iterrows():
        # Skip orphaned user_ids (edge case with injected dupes)
        if row["user_id"] not in user_set:
            continue

        fail_date = date.fromisoformat(row["payment_date"])
        recovered = random.random() < RECOVERY_RATE

        recovery_date:   date | None  = None
        recovery_amount: float | None = None

        if recovered:
            # Recovery happens 1-7 days after original failure
            recovery_date   = fail_date + timedelta(days=random.randint(1, 7))
            if recovery_date > SIM_END:
                recovery_date   = None
                recovery_amount = None
                recovered       = False
            else:
                # Recovered amount may be slightly different (partial recovery)
                recovery_amount = round(row["amount"] * random.uniform(0.95, 1.0), 2)

        records.append({
            "failure_id":       str(uuid.uuid4()),
            "payment_id":       row["payment_id"],
            "user_id":          row["user_id"],
            "failure_date":     fail_date.isoformat(),
            "failure_reason":   row["failure_reason"] or "unknown",
            "gateway":          row["gateway"],
            "amount":           row["amount"],
            "recovered":        int(recovered),
            "recovery_date":    recovery_date.isoformat() if recovery_date else None,
            "recovery_amount":  recovery_amount,
        })

    df = pd.DataFrame(records)
    log.info(
        "Payment failures generated: %d rows  |  recovered: %d",
        len(df),
        df["recovered"].sum() if len(df) else 0,
    )
    return df


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def generate_all() -> dict[str, pd.DataFrame]:
    """
    Run all generators in dependency order and return the DataFrames.
    """
    log.info("=== PlaceMux data generation starting ===")

    users_df    = generate_users(NUM_USERS)
    user_ids    = users_df["user_id"].tolist()

    payments_df = generate_payments(NUM_PAYMENTS, user_ids, users_df=users_df)
    apps_df     = generate_applications(NUM_APPLICATIONS, user_ids)
    events_df   = generate_revenue_events(payments_df)
    failures_df = generate_payment_failures(payments_df, users_df)

    datasets = {
        "users":            users_df,
        "payments":         payments_df,
        "applications":     apps_df,
        "revenue_events":   events_df,
        "payment_failures": failures_df,
    }

    return datasets


def save_csvs(datasets: dict[str, pd.DataFrame]) -> None:
    """Persist each DataFrame as a CSV under DATA_DIR."""
    for name, df in datasets.items():
        out_path = DATA_DIR / f"{name}.csv"
        df.to_csv(out_path, index=False)
        log.info("Saved %-22s  →  %s  (%d rows)", name, out_path, len(df))


def print_summary(datasets: dict[str, pd.DataFrame]) -> None:
    """Log a human-readable summary of what was generated."""
    log.info("=== Generation Summary ===")
    for name, df in datasets.items():
        log.info("  %-22s  %6d rows  |  columns: %s", name, len(df), list(df.columns))

    pay = datasets["payments"]
    log.info("Payment status distribution:\n%s", pay["status"].value_counts().to_string())
    log.info("Gateway distribution:\n%s", pay["gateway"].value_counts().to_string())

    fail = datasets["payment_failures"]
    if len(fail):
        rec_rate = fail["recovered"].mean() * 100
        log.info("Payment failure recovery rate: %.1f %%", rec_rate)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    datasets = generate_all()
    save_csvs(datasets)
    print_summary(datasets)
    log.info("=== Data generation complete ===")
