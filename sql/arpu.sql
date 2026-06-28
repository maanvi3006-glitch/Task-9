-- PlaceMux · sql/arpu.sql
-- ARPU: Average Revenue Per (paying) User
-- Run directly:  sqlite3 placemux.db < sql/arpu.sql

SELECT
    ROUND(
        CAST(SUM(amount) AS REAL) / NULLIF(COUNT(DISTINCT user_id), 0),
        2
    )                          AS arpu,
    SUM(amount)                AS total_revenue,
    COUNT(DISTINCT user_id)    AS paying_users,
    COUNT(payment_id)          AS successful_transactions,
    ROUND(AVG(amount), 2)      AS avg_transaction_value
FROM payments
WHERE status = 'success';
