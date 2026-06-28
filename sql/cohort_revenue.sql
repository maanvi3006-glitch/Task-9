-- PlaceMux · sql/cohort_revenue.sql
-- Cohort revenue by months-since-signup
-- Run directly:  sqlite3 placemux.db < sql/cohort_revenue.sql

SELECT
    u.cohort_month,
    (
        (CAST(STRFTIME('%Y', p.payment_date) AS INTEGER) -
         CAST(SUBSTR(u.cohort_month, 1, 4) AS INTEGER)) * 12
        +
        (CAST(STRFTIME('%m', p.payment_date) AS INTEGER) -
         CAST(SUBSTR(u.cohort_month, 6, 2) AS INTEGER))
    )                              AS months_since_signup,
    COUNT(DISTINCT p.user_id)      AS paying_users,
    ROUND(SUM(p.amount), 2)        AS revenue
FROM payments p
JOIN users u ON p.user_id = u.user_id
WHERE p.status = 'success'
  AND (
        (CAST(STRFTIME('%Y', p.payment_date) AS INTEGER) -
         CAST(SUBSTR(u.cohort_month, 1, 4) AS INTEGER)) * 12
        +
        (CAST(STRFTIME('%m', p.payment_date) AS INTEGER) -
         CAST(SUBSTR(u.cohort_month, 6, 2) AS INTEGER))
      ) >= 0
GROUP BY u.cohort_month, months_since_signup
ORDER BY u.cohort_month, months_since_signup;
