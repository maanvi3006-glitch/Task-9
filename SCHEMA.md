# PlaceMux · SCHEMA.md
## Database Schema Reference — Task 9

**Database:** SQLite 3 (`placemux.db`)
**Encoding:** UTF-8
**Journal mode:** WAL (Write-Ahead Logging — enables concurrent Streamlit reads)
**Foreign keys:** ON

---

## Tables

### `users`

Stores every registered user with their signup date and cohort assignment.

```sql
CREATE TABLE users (
    user_id      TEXT    PRIMARY KEY,        -- UUID v4
    signup_date  DATE    NOT NULL,           -- ISO-8601: YYYY-MM-DD
    cohort_month TEXT    NOT NULL,           -- YYYY-MM derived from signup_date
    active       INTEGER DEFAULT 1           -- 1=active  0=churned  NULL=dirty data
);
```

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `user_id` | TEXT | PK | UUID v4 string |
| `signup_date` | DATE | NOT NULL | ISO-8601 string (SQLite has no native DATE) |
| `cohort_month` | TEXT | NOT NULL | Always `STRFTIME('%Y-%m', signup_date)` |
| `active` | INTEGER | nullable | NULL rows are intentional dirty data for validation testing |

**Indexes:**
```sql
CREATE INDEX idx_users_cohort ON users(cohort_month);
```

**Row count (generated):** ~2,000

---

### `payments`

Every payment attempt — successful, failed, refunded, or pending.
This is the central fact table; all revenue metrics derive from it.

```sql
CREATE TABLE payments (
    payment_id     TEXT  PRIMARY KEY,
    user_id        TEXT  NOT NULL,
    payment_date   DATE  NOT NULL,
    amount         REAL  NOT NULL,
    status         TEXT  NOT NULL,
    gateway        TEXT  NOT NULL,
    failure_reason TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `payment_id` | TEXT | PK | UUID v4 |
| `user_id` | TEXT | NOT NULL, FK | References `users.user_id` |
| `payment_date` | DATE | NOT NULL | Always ≥ user's `signup_date` |
| `amount` | REAL | NOT NULL | INR; range ₹99–₹9,999 (×1.5–2.5 in spike months) |
| `status` | TEXT | NOT NULL | `success` \| `failed` \| `refunded` \| `pending` |
| `gateway` | TEXT | NOT NULL | `stripe` \| `razorpay` \| `paypal` \| `bank_transfer` |
| `failure_reason` | TEXT | nullable | NULL when `status = 'success'` or `'pending'` |

**Status distribution (generated):**

| Status | Weight |
|---|---|
| success | 72% |
| failed | 18% |
| refunded | 7% |
| pending | 3% |

**Gateway distribution (generated):**

| Gateway | Weight |
|---|---|
| stripe | 40% |
| razorpay | 30% |
| paypal | 20% |
| bank_transfer | 10% |

**Failure reasons by gateway:**

| Gateway | Possible failure_reason values |
|---|---|
| stripe | `card_declined`, `fraud_detected`, `invalid_card`, `expired_card` |
| razorpay | `insufficient_funds`, `bank_error`, `gateway_timeout`, `network_error` |
| paypal | `card_declined`, `fraud_detected`, `network_error`, `invalid_card` |
| bank_transfer | `bank_error`, `gateway_timeout`, `insufficient_funds` |

**Dirty data injected:**
- ~2% of rows are deliberate duplicates (same `user_id + amount + payment_date`, new `payment_id`)
- Spike months (March, September) have amounts ×1.5–2.5 higher

**Indexes:**
```sql
CREATE INDEX idx_payments_user_id     ON payments(user_id);
CREATE INDEX idx_payments_status      ON payments(status);
CREATE INDEX idx_payments_payment_date ON payments(payment_date);
CREATE INDEX idx_payments_gateway     ON payments(gateway);
```

**Row count (generated):** ~7,650 (7,500 base + ~150 injected duplicates)

---

### `applications`

User job/internship applications to companies, each with a platform fee.

```sql
CREATE TABLE applications (
    application_id TEXT  PRIMARY KEY,
    user_id        TEXT  NOT NULL,
    company        TEXT  NOT NULL,
    fee            REAL  NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `application_id` | TEXT | PK | UUID v4 |
| `user_id` | TEXT | NOT NULL, FK | References `users.user_id` |
| `company` | TEXT | NOT NULL | Faker company name + sector tag |
| `fee` | REAL | NOT NULL | Platform fee in INR; range ₹199–₹4,999 |

**Indexes:**
```sql
CREATE INDEX idx_applications_user_id ON applications(user_id);
```

**Row count (generated):** ~5,000

---

### `revenue_events`

Immutable event log for every money movement.
One event per payment at creation; additional events for chargebacks and retries.

```sql
CREATE TABLE revenue_events (
    event_id    TEXT PRIMARY KEY,
    payment_id  TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    event_time  TEXT NOT NULL,
    FOREIGN KEY (payment_id) REFERENCES payments(payment_id)
);
```

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `event_id` | TEXT | PK | UUID v4 |
| `payment_id` | TEXT | NOT NULL, FK | References `payments.payment_id` |
| `event_type` | TEXT | NOT NULL | See event types below |
| `event_time` | TEXT | NOT NULL | ISO-8601 datetime (YYYY-MM-DDTHH:MM:SS) |

**Event types:**

| event_type | When generated |
|---|---|
| `payment_received` | Every payment attempt (all statuses) |
| `refund_issued` | When `payments.status = 'refunded'` |
| `chargeback` | ~3% of successful payments, 1–30 days after payment |
| `retry_success` | For each recovered failure in `payment_failures` |

**Indexes:**
```sql
CREATE INDEX idx_revenue_events_pid ON revenue_events(payment_id);
```

**Row count (generated):** ~8,265
- ~7,650 initial events (one per payment)
- ~230 chargeback events (3% of ~7,500 × 72% success ≈ 162 + rounding)
- ~467 retry_success events (injected by `load_data.py` after `payment_failures` loads)

---

### `payment_failures`

Detailed record of every failed payment, including recovery tracking.
Derived from `payments WHERE status = 'failed'` during data generation.

```sql
CREATE TABLE payment_failures (
    failure_id      TEXT    PRIMARY KEY,
    payment_id      TEXT    NOT NULL,
    user_id         TEXT    NOT NULL,
    failure_date    DATE    NOT NULL,
    failure_reason  TEXT    NOT NULL,
    gateway         TEXT    NOT NULL,
    amount          REAL    NOT NULL,
    recovered       INTEGER NOT NULL DEFAULT 0,
    recovery_date   DATE,
    recovery_amount REAL,
    FOREIGN KEY (payment_id) REFERENCES payments(payment_id),
    FOREIGN KEY (user_id)    REFERENCES users(user_id)
);
```

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `failure_id` | TEXT | PK | UUID v4 |
| `payment_id` | TEXT | NOT NULL, FK | Original failed payment |
| `user_id` | TEXT | NOT NULL, FK | User who attempted payment |
| `failure_date` | DATE | NOT NULL | Same as original `payment_date` |
| `failure_reason` | TEXT | NOT NULL | Reason string (never NULL in this table) |
| `gateway` | TEXT | NOT NULL | Gateway that failed |
| `amount` | REAL | NOT NULL | Original attempted amount |
| `recovered` | INTEGER | NOT NULL | `1` = retried successfully, `0` = still failed |
| `recovery_date` | DATE | nullable | NULL when `recovered = 0` |
| `recovery_amount` | REAL | nullable | 95–100% of original amount; NULL when `recovered = 0` |

**Recovery distribution (generated):**
- ~35% of failures are recovered (retry within 1–7 days)
- Recovery amount is 95–100% of original (simulates partial recovery fees)

**Indexes:**
```sql
CREATE INDEX idx_failures_user_id ON payment_failures(user_id);
CREATE INDEX idx_failures_gateway ON payment_failures(gateway);
```

**Row count (generated):** ~1,350 (all `status = 'failed'` payments minus orphans)

---

## Entity-relationship summary

```
users (1) ──────────────────── (N) payments
  │                                    │
  │                                    ├── (N) revenue_events
  │                                    │
  │                                    └── (N) payment_failures
  │
  └────────────────────────── (N) applications
```

---

## Key SQL patterns used across the project

### ARPU
```sql
SELECT ROUND(SUM(amount) / NULLIF(COUNT(DISTINCT user_id), 0), 2) AS arpu
FROM payments WHERE status = 'success';
```

### Cohort month offset
```sql
(CAST(STRFTIME('%Y', payment_date) AS INTEGER) -
 CAST(SUBSTR(cohort_month, 1, 4) AS INTEGER)) * 12
+
(CAST(STRFTIME('%m', payment_date) AS INTEGER) -
 CAST(SUBSTR(cohort_month, 6, 2) AS INTEGER))
AS months_since_signup
```

### Revenue growth (MoM)
```sql
LAG(revenue) OVER (ORDER BY month) AS prev_revenue
```

### Duplicate fingerprint
```sql
GROUP BY user_id, payment_date, amount HAVING COUNT(payment_id) > 1
```

### Stale pending (anti-join)
```sql
LEFT JOIN revenue_events re ON p.payment_id = re.payment_id
WHERE p.status = 'pending' AND re.event_id IS NULL
```

---

## SQLite-specific notes

- `DATE` columns are stored as `TEXT` in ISO-8601 format (`YYYY-MM-DD`).
  Use `STRFTIME()` and `DATE()` functions for date arithmetic.
- `JULIANDAY()` is used for computing day differences between dates.
- `FILTER (WHERE ...)` syntax is supported from SQLite 3.23+ (2018).
- `LAG()` window function supported from SQLite 3.25+ (2018).
- WAL mode (`PRAGMA journal_mode=WAL`) allows concurrent reads from
  Streamlit while the pipeline writes — essential for live dashboards.
