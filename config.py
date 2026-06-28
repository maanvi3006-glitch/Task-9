"""
PlaceMux · Phase 2 · Task 9
config.py

Central configuration.  All paths and tunable constants live here.
Import this module — never hardcode paths or magic numbers elsewhere.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Project root (directory that contains config.py)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.resolve()

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH = PROJECT_ROOT / "placemux.db"
DB_URL  = f"sqlite:///{DB_PATH}"

# ---------------------------------------------------------------------------
# Data directories
# ---------------------------------------------------------------------------
DATA_DIR    = PROJECT_ROOT / "data"
SQL_DIR     = PROJECT_ROOT / "sql"
REPORTS_DIR = PROJECT_ROOT / "reports"
DOCS_DIR    = PROJECT_ROOT / "docs"

# Ensure directories exist at import time
for _d in (DATA_DIR, SQL_DIR, REPORTS_DIR, DOCS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Data-generation parameters
# ---------------------------------------------------------------------------
NUM_USERS        = 2_000          # users rows
NUM_PAYMENTS     = 7_500          # payments rows
NUM_APPLICATIONS = 5_000          # applications rows

RANDOM_SEED = 42                  # reproducible Faker output

# Payment status distribution (must sum to 1.0)
PAYMENT_STATUS_WEIGHTS = {
    "success":  0.72,
    "failed":   0.18,
    "refunded": 0.07,
    "pending":  0.03,
}

# Gateway distribution
GATEWAY_WEIGHTS = {
    "stripe":        0.40,
    "razorpay":      0.30,
    "paypal":        0.20,
    "bank_transfer": 0.10,
}

# Failure reasons (used when status = 'failed')
FAILURE_REASONS = [
    "insufficient_funds",
    "card_declined",
    "gateway_timeout",
    "invalid_card",
    "fraud_detected",
    "bank_error",
    "expired_card",
    "network_error",
]

# Payment amount range (INR)
AMOUNT_MIN =   99.0
AMOUNT_MAX = 9_999.0

# Spike months — these months will have 2x payment volume (simulates campaigns)
SPIKE_MONTHS = [3, 9]   # March, September

# Duplicate injection rate (fraction of payments to duplicate)
DUPLICATE_RATE = 0.02

# Null injection rates per column
NULL_RATES = {
    "failure_reason": 0.0,   # controlled programmatically
    "users.active":   0.01,  # ~1 % of users will have NULL active flag
}

# Recovery rate — fraction of failed payments that get retried successfully
RECOVERY_RATE = 0.35

# ---------------------------------------------------------------------------
# Revenue-event types
# ---------------------------------------------------------------------------
EVENT_TYPES = [
    "payment_received",
    "refund_issued",
    "chargeback",
    "retry_success",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = "INFO"    # DEBUG | INFO | WARNING | ERROR

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
DASHBOARD_TITLE      = "PlaceMux · Revenue Intelligence"
DASHBOARD_PAGE_ICON  = "💰"
DASHBOARD_LAYOUT     = "wide"

# Freshness SLA: data older than this many hours triggers a warning
FRESHNESS_SLA_HOURS = 24

# ---------------------------------------------------------------------------
# Validation thresholds
# ---------------------------------------------------------------------------
MAX_DUPLICATE_RATE   = 0.05   # alert if > 5 % duplicates
MAX_NULL_RATE        = 0.10   # alert if > 10 % nulls in key columns
MAX_FAILURE_RATE     = 0.40   # alert if payment failure rate > 40 %
MIN_ARPU             = 1.0    # alert if ARPU < ₹1 (data quality guard)
