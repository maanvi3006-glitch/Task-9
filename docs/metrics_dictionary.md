# PlaceMux · Metrics Dictionary
## Task 9 — Revenue Intelligence System

Every metric has: definition · SQL source · decision it enables.

---

### ARPU — Average Revenue Per User

**Definition:** Total successful payment revenue divided by the number
of distinct users who made at least one successful payment.

**Formula:** `SUM(amount WHERE status='success') / COUNT(DISTINCT user_id WHERE status='success')`

**SQL file:** `sql/arpu.sql` · **Module:** `metrics_engine.MetricsEngine.arpu()`

**Decision:** If ARPU drops below ₹1,000 month-over-month, review
pricing tiers or investigate churn in high-value cohorts.

---

### Monthly Revenue

**Definition:** Sum of successful payment amounts grouped by calendar month (YYYY-MM).

**SQL:** `STRFTIME('%Y-%m', payment_date)` group-by on `status='success'`

**Module:** `MetricsEngine.monthly_revenue()`

**Decision:** Month with revenue below 3-month rolling average triggers
a growth-vs-churn investigation.

---

### MoM Revenue Growth %

**Definition:** Month-over-month percentage change in successful revenue.

**Formula:** `(This Month Revenue − Last Month Revenue) / Last Month Revenue × 100`

**SQL:** LAG window function on monthly CTE. **File:** `sql/revenue_growth.sql`

**Decision:** Two consecutive negative months = pricing or retention action required.

---

### Cohort Revenue Matrix

**Definition:** Revenue earned from each signup cohort in each
month-since-signup period (0 = signup month, 1 = next month, …).

**Module:** `cohort_engine.CohortEngine.cohort_revenue_matrix()`

**Decision:** Cohorts with revenue collapsing after Month 1 have a
monetisation problem; cohorts growing after Month 3 indicate upsell success.

---

### Cohort Revenue Retention %

**Definition:** Each cohort's revenue in month N expressed as a
percentage of that cohort's Month-0 (signup month) revenue.

**Formula:** `Revenue(cohort, month N) / Revenue(cohort, month 0) × 100`

**Module:** `CohortEngine.cohort_retention()`

**Decision:** Retention below 40% by Month 3 = feature or pricing change needed.
Retention above 100% = expansion revenue (upsell working).

---

### Cohort ARPU

**Definition:** Lifetime revenue from a cohort divided by total users in that cohort.

**Formula:** `SUM(successful payments) / COUNT(users in cohort)`

**Module:** `CohortEngine.cohort_arpu()`

**Decision:** Low ARPU cohorts from specific months may correlate with
lower-quality acquisition channels.

---

### Failed Payment %

**Definition:** Percentage of all payment attempts that have `status='failed'`.

**Formula:** `COUNT(status='failed') / COUNT(*) × 100`

**SQL file:** `sql/failure_rate.sql` · **Module:** `MetricsEngine.failed_payment_pct()`

**Threshold:** WARN at 30%, FAIL at 40%.

**Decision:** Failure rate above threshold per gateway = SLA renegotiation
or gateway switch.

---

### Gateway Success %

**Definition:** Percentage of payments per gateway with `status='success'`.

**Module:** `MetricsEngine.gateway_success_pct()`

**Decision:** Gateway with success rate below 70% over 30 days = performance review.

---

### Payment Recovery %

**Definition:** Percentage of failed payments that were subsequently
retried successfully (tracked in `payment_failures.recovered`).

**Formula:** `SUM(recovered=1) / COUNT(*) × 100` on `payment_failures`

**Module:** `MetricsEngine.payment_recovery()`

**Decision:** Recovery rate below 20% = retry logic is broken or retry
window too short.

---

### Revenue Lost

**Definition:** Total amount from unrecovered payment failures
(`recovered=0` in `payment_failures`).

**Module:** `MetricsEngine.revenue_lost()` and `FailureMonitor.revenue_leakage()`

**Decision:** Top failure-reason buckets by revenue lost are prioritised
for engineering work (e.g. card-expiry emails for `expired_card` failures).

---

### Net Revenue Lost

**Definition:** Revenue at risk minus recovered amounts:
`SUM(amount) - SUM(recovery_amount WHERE recovered=1)`

**Module:** `FailureMonitor.failure_monitor()`

**Decision:** Ranked gateway×reason table drives where to spend
engineering effort first.

---

### Data Freshness

**Definition:** Hours elapsed since the most recent successful payment date.

**Threshold:** SLA = 24 hours (configurable in `config.FRESHNESS_SLA_HOURS`).

**Module:** `Validator.freshness_check()`

**Decision:** Freshness breach = pipeline stalled, data team to investigate.
Note: always WARNs in dev/test (data ends 2024-12-31).

---

### Duplicate Rate

**Definition:** Fraction of payments sharing a `user_id + amount + payment_date`
fingerprint with at least one other payment.

**Formula:** `excess_rows / total_payments` where excess = rows beyond the first per group.

**Threshold:** WARN threshold 5%.

**Module:** `Validator.detect_duplicates()`

**Decision:** Duplicate rate above 5% = ingestion pipeline investigation.
