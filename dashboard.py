"""
PlaceMux · Phase 2 · Task 9 — Failure Handling & Resilience
dashboard.py

Responsibility:
    Streamlit multi-page Revenue Intelligence Dashboard.
    Every visualisation reads directly from live SQLite — no static data.

Pages:
    1. Revenue Overview   — Total Revenue, ARPU, Monthly Trend
    2. Cohort Revenue     — Cohort Heatmap, Retention, Lifetime ARPU
    3. Failure Monitoring — Failure %, Gateway Health, Revenue Loss
    4. Data Quality       — Validation Status, Duplicates, Freshness

Run:
    streamlit run dashboard.py
"""

import logging

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from cohort_engine import CohortEngine
from config import (
    DASHBOARD_LAYOUT,
    DASHBOARD_PAGE_ICON,
    DASHBOARD_TITLE,
    LOG_LEVEL,
)
from metrics_engine import MetricsEngine
from payment_failure import FailureMonitor
from validation import Status, Validator

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  [%(levelname)s]  dashboard — %(message)s",
)
log = logging.getLogger("dashboard")

# ---------------------------------------------------------------------------
# Page config — must be first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title=DASHBOARD_TITLE,
    page_icon=DASHBOARD_PAGE_ICON,
    layout=DASHBOARD_LAYOUT,
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Shared colour palette
# ---------------------------------------------------------------------------
BRAND_TEAL   = "#0FA3B1"
BRAND_ORANGE = "#F7A072"
BRAND_GREEN  = "#06D6A0"
BRAND_RED    = "#EF233C"
BRAND_PURPLE = "#7B2D8B"
BRAND_GREY   = "#6C757D"

STATUS_COLOURS = {
    "PASS":  BRAND_GREEN,
    "WARN":  BRAND_ORANGE,
    "FAIL":  BRAND_RED,
    "ERROR": BRAND_PURPLE,
}

GATEWAY_COLOURS = {
    "stripe":        BRAND_TEAL,
    "razorpay":      BRAND_ORANGE,
    "paypal":        BRAND_PURPLE,
    "bank_transfer": BRAND_GREEN,
}


# ---------------------------------------------------------------------------
# Cached data loaders — TTL 5 min so live DB changes refresh automatically
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_summary() -> dict:
    return MetricsEngine().top_summary()


@st.cache_data(ttl=300)
def load_monthly() -> pd.DataFrame:
    return MetricsEngine().monthly_revenue()


@st.cache_data(ttl=300)
def load_growth() -> pd.DataFrame:
    return MetricsEngine().revenue_growth()


@st.cache_data(ttl=300)
def load_daily_trend() -> pd.DataFrame:
    return MetricsEngine().daily_revenue_trend()


@st.cache_data(ttl=300)
def load_gateway_revenue() -> pd.DataFrame:
    return MetricsEngine().revenue_by_gateway()


@st.cache_data(ttl=300)
def load_cohort_matrix(max_months: int = 12) -> pd.DataFrame:
    return CohortEngine().cohort_revenue_matrix(max_months=max_months)


@st.cache_data(ttl=300)
def load_cohort_retention(max_months: int = 12) -> pd.DataFrame:
    return CohortEngine().cohort_retention(max_months=max_months)


@st.cache_data(ttl=300)
def load_cohort_arpu() -> pd.DataFrame:
    return CohortEngine().cohort_arpu()


@st.cache_data(ttl=300)
def load_cohort_lifetime() -> pd.DataFrame:
    return CohortEngine().cohort_lifetime()


@st.cache_data(ttl=300)
def load_failure_monitor() -> pd.DataFrame:
    return FailureMonitor().failure_monitor()


@st.cache_data(ttl=300)
def load_gateway_health() -> pd.DataFrame:
    return FailureMonitor().gateway_health()


@st.cache_data(ttl=300)
def load_failure_trend() -> pd.DataFrame:
    return FailureMonitor().failure_trend()


@st.cache_data(ttl=300)
def load_top_failure_reasons() -> pd.DataFrame:
    return FailureMonitor().top_failure_reasons()


@st.cache_data(ttl=300)
def load_recovery() -> dict:
    return MetricsEngine().payment_recovery()


@st.cache_data(ttl=300)
def load_recon() -> dict:
    return FailureMonitor().reconciliation_check()


@st.cache_data(ttl=300)
def load_validation() -> list:
    return Validator().run_all()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def fmt_inr(value: float) -> str:
    """Format a float as Indian Rupees with lakh/crore suffix."""
    if value >= 1_00_00_000:
        return f"₹{value / 1_00_00_000:.2f} Cr"
    if value >= 1_00_000:
        return f"₹{value / 1_00_000:.2f} L"
    return f"₹{value:,.0f}"


def fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def section(title: str, icon: str = "") -> None:
    """Render a styled section header."""
    st.markdown(f"### {icon} {title}" if icon else f"### {title}")
    st.markdown("---")


def status_badge(status: str) -> str:
    """Return an HTML badge string for a validation status."""
    colours = {
        "PASS": "#06D6A0", "WARN": "#F7A072",
        "FAIL": "#EF233C", "ERROR": "#7B2D8B",
    }
    colour = colours.get(status, "#6C757D")
    return (
        f'<span style="background:{colour};color:#fff;'
        f'padding:2px 8px;border-radius:4px;font-size:12px;'
        f'font-weight:600">{status}</span>'
    )


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

def render_sidebar() -> str:
    with st.sidebar:
        st.image(
            "https://img.icons8.com/fluency/96/combo-chart.png",
            width=60,
        )
        st.title("PlaceMux RI")
        st.caption("Revenue Intelligence · Task 9")
        st.markdown("---")

        page = st.radio(
            "Navigation",
            [
                "💰 Revenue Overview",
                "👥 Cohort Revenue",
                "⚠️ Failure Monitoring",
                "🔍 Data Quality",
            ],
            label_visibility="collapsed",
        )

        st.markdown("---")
        st.caption("Data refreshes every 5 min")
        if st.button("🔄 Refresh Now"):
            st.cache_data.clear()
            st.rerun()

    return page


# ---------------------------------------------------------------------------
# PAGE 1 — Revenue Overview
# ---------------------------------------------------------------------------

def page_revenue_overview() -> None:
    st.title("💰 Revenue Overview")
    st.caption("All figures from successful payments · SQLite live query")

    # --- KPI header row ---
    summary = load_summary()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Revenue",     fmt_inr(summary.get("total_revenue", 0)))
    c2.metric("ARPU",              fmt_inr(summary.get("arpu", 0)))
    c3.metric("Paying Users",      f"{int(summary.get('active_paying_users', 0)):,}")
    c4.metric("Total Transactions",f"{int(summary.get('total_transactions', 0)):,}")
    c5.metric("Failure Rate",      fmt_pct(summary.get("overall_failure_pct", 0)))

    st.markdown("---")

    # --- Revenue trend (daily) ---
    section("Daily Revenue Trend", "📈")
    daily = load_daily_trend()
    if not daily.empty:
        fig = px.line(
            daily,
            x="payment_date",
            y="daily_revenue",
            title="Daily Successful Revenue (INR)",
            labels={"payment_date": "Date", "daily_revenue": "Revenue (₹)"},
            color_discrete_sequence=[BRAND_TEAL],
        )
        fig.update_traces(line_width=1.5)
        fig.update_layout(
            hovermode="x unified",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(showgrid=False),
            yaxis=dict(gridcolor="#2D2D2D"),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No daily trend data available.")

    col_left, col_right = st.columns(2)

    # --- Monthly revenue bar ---
    with col_left:
        section("Monthly Revenue", "📅")
        monthly = load_monthly()
        if not monthly.empty:
            fig = px.bar(
                monthly,
                x="month",
                y="revenue",
                title="Monthly Revenue (INR)",
                labels={"month": "Month", "revenue": "Revenue (₹)"},
                color="revenue",
                color_continuous_scale=[BRAND_TEAL, BRAND_GREEN],
            )
            fig.update_layout(
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                coloraxis_showscale=False,
                xaxis_tickangle=-45,
            )
            st.plotly_chart(fig, use_container_width=True)

    # --- Revenue growth % line ---
    with col_right:
        section("Month-over-Month Growth %", "📊")
        growth = load_growth()
        if not growth.empty:
            growth_clean = growth.dropna(subset=["growth_pct"])
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=growth_clean["month"],
                y=growth_clean["growth_pct"],
                mode="lines+markers",
                name="Growth %",
                line=dict(color=BRAND_ORANGE, width=2),
                marker=dict(size=6),
            ))
            fig.add_hline(
                y=0, line_dash="dash",
                line_color=BRAND_GREY, opacity=0.5,
            )
            fig.update_layout(
                title="MoM Revenue Growth (%)",
                xaxis_title="Month",
                yaxis_title="Growth %",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                xaxis_tickangle=-45,
                hovermode="x unified",
            )
            st.plotly_chart(fig, use_container_width=True)

    # --- Revenue by gateway ---
    section("Revenue by Gateway", "🏦")
    gw_rev = load_gateway_revenue()
    if not gw_rev.empty:
        col_a, col_b = st.columns([1, 2])
        with col_a:
            fig = px.pie(
                gw_rev,
                names="gateway",
                values="revenue",
                title="Revenue Share by Gateway",
                color="gateway",
                color_discrete_map=GATEWAY_COLOURS,
                hole=0.4,
            )
            fig.update_traces(textposition="inside", textinfo="percent+label")
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)
        with col_b:
            st.dataframe(
                gw_rev.rename(columns={
                    "gateway": "Gateway",
                    "revenue": "Revenue (₹)",
                    "transactions": "Transactions",
                    "unique_payers": "Unique Payers",
                    "avg_transaction": "Avg Txn (₹)",
                }),
                use_container_width=True,
                hide_index=True,
            )


# ---------------------------------------------------------------------------
# PAGE 2 — Cohort Revenue
# ---------------------------------------------------------------------------

def page_cohort_revenue() -> None:
    st.title("👥 Cohort Revenue")
    st.caption(
        "Cohorts defined by user signup month · "
        "Revenue in INR from successful payments"
    )

    max_months = st.slider(
        "Months of history to show", min_value=3, max_value=12, value=6, step=1
    )

    # --- Cohort Revenue Heatmap ---
    section("Cohort Revenue Matrix (INR)", "🔥")
    matrix = load_cohort_matrix(max_months)
    if not matrix.empty:
        fig = px.imshow(
            matrix,
            labels=dict(x="Months Since Signup", y="Cohort", color="Revenue (₹)"),
            title="Cohort Revenue Heatmap",
            color_continuous_scale="Teal",
            aspect="auto",
            text_auto=".0f",
        )
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title="Months Since Signup",
            yaxis_title="Signup Cohort",
            height=550,
        )
        fig.update_xaxes(side="bottom")
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Row = signup cohort  ·  Column = months after signup  ·  "
            "Value = total revenue from that cohort in that period"
        )
    else:
        st.info("No cohort matrix data available.")

    # --- Retention heatmap ---
    section("Revenue Retention % (relative to Month 0)", "🔁")
    retention = load_cohort_retention(max_months)
    if not retention.empty:
        fig = px.imshow(
            retention,
            labels=dict(
                x="Months Since Signup",
                y="Cohort",
                color="Retention %",
            ),
            title="Cohort Revenue Retention (%)",
            color_continuous_scale=[
                [0.0, BRAND_RED],
                [0.3, BRAND_ORANGE],
                [0.7, BRAND_TEAL],
                [1.0, BRAND_GREEN],
            ],
            aspect="auto",
            zmin=0,
            zmax=150,
            text_auto=".0f",
        )
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            xaxis_title="Months Since Signup",
            yaxis_title="Signup Cohort",
            height=550,
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "100% = same revenue as Month 0  ·  "
            ">100% = expansion  ·  <100% = contraction"
        )

    # --- Cohort ARPU bar ---
    section("Cohort ARPU & Conversion Rate", "💡")
    arpu_df = load_cohort_arpu()
    if not arpu_df.empty:
        col_l, col_r = st.columns(2)
        with col_l:
            fig = px.bar(
                arpu_df,
                x="cohort_month",
                y="arpu_per_user",
                title="Lifetime ARPU by Cohort (₹)",
                labels={
                    "cohort_month": "Cohort",
                    "arpu_per_user": "ARPU (₹)",
                },
                color="arpu_per_user",
                color_continuous_scale=[BRAND_TEAL, BRAND_GREEN],
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_tickangle=-45,
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig, use_container_width=True)
        with col_r:
            fig = px.bar(
                arpu_df,
                x="cohort_month",
                y="conversion_pct",
                title="Conversion Rate by Cohort (%)",
                labels={
                    "cohort_month": "Cohort",
                    "conversion_pct": "Conversion %",
                },
                color="conversion_pct",
                color_continuous_scale=[BRAND_ORANGE, BRAND_GREEN],
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_tickangle=-45,
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig, use_container_width=True)

    # --- Cohort table ---
    section("Cohort Detail Table", "📋")
    lifetime = load_cohort_lifetime()
    if not lifetime.empty:
        st.dataframe(
            lifetime.rename(columns={
                "cohort_month":       "Cohort",
                "cohort_size":        "Users",
                "paying_users":       "Paying",
                "lifetime_revenue":   "Lifetime Rev (₹)",
                "total_transactions": "Transactions",
            }),
            use_container_width=True,
            hide_index=True,
        )


# ---------------------------------------------------------------------------
# PAGE 3 — Failure Monitoring
# ---------------------------------------------------------------------------

def page_failure_monitoring() -> None:
    st.title("⚠️ Failure Monitoring")
    st.caption("Payment failure tracking · revenue leakage · retry outcomes")

    # --- Recovery KPI row ---
    rec = load_recovery()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Failures",      f"{rec.get('total_failures', 0):,}")
    c2.metric("Recovered",           f"{rec.get('recovered_count', 0):,}")
    c3.metric("Recovery Rate",       fmt_pct(rec.get("recovery_pct", 0)))
    c4.metric("Revenue Lost",        fmt_inr(rec.get("unrecovered_revenue", 0)))

    st.markdown("---")

    # --- Gateway health bar chart ---
    section("Gateway Performance", "🏦")
    health = load_gateway_health()
    if not health.empty:
        col_l, col_r = st.columns(2)
        with col_l:
            fig = go.Figure()
            for col, colour, name in [
                ("success_rate_pct", BRAND_GREEN,  "Success %"),
                ("failure_rate_pct", BRAND_RED,    "Failure %"),
            ]:
                fig.add_trace(go.Bar(
                    name=name,
                    x=health["gateway"],
                    y=health[col],
                    marker_color=colour,
                ))
            fig.update_layout(
                barmode="group",
                title="Success vs Failure Rate by Gateway",
                xaxis_title="Gateway",
                yaxis_title="%",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", y=-0.2),
            )
            st.plotly_chart(fig, use_container_width=True)
        with col_r:
            fig = go.Figure()
            fig.add_trace(go.Bar(
                name="Recovered Revenue",
                x=health["gateway"],
                y=health["success_revenue"],
                marker_color=BRAND_GREEN,
            ))
            fig.add_trace(go.Bar(
                name="Unrecovered Loss",
                x=health["gateway"],
                y=health["unrecovered_revenue"],
                marker_color=BRAND_RED,
            ))
            fig.update_layout(
                barmode="stack",
                title="Revenue: Collected vs Lost by Gateway",
                xaxis_title="Gateway",
                yaxis_title="Revenue (₹)",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", y=-0.2),
            )
            st.plotly_chart(fig, use_container_width=True)

    # --- Failure trend ---
    section("Daily Failure Trend", "📉")
    trend = load_failure_trend()
    if not trend.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=trend["failure_date"],
            y=trend["failures"],
            name="Total Failures",
            mode="lines",
            line=dict(color=BRAND_RED, width=1.5),
            fill="tozeroy",
            fillcolor="rgba(239,35,60,0.1)",
        ))
        fig.add_trace(go.Scatter(
            x=trend["failure_date"],
            y=trend["recovered"],
            name="Recovered",
            mode="lines",
            line=dict(color=BRAND_GREEN, width=1.5),
        ))
        fig.update_layout(
            title="Daily Payment Failures vs Recovered",
            xaxis_title="Date",
            yaxis_title="Count",
            hovermode="x unified",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", y=-0.2),
        )
        st.plotly_chart(fig, use_container_width=True)

    col_l, col_r = st.columns(2)

    # --- Top failure reasons pie ---
    with col_l:
        section("Failure Reasons", "🔍")
        reasons = load_top_failure_reasons()
        if not reasons.empty:
            fig = px.pie(
                reasons,
                names="failure_reason",
                values="revenue_lost",
                title="Revenue Lost by Failure Reason",
                hole=0.4,
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig.update_traces(textposition="inside", textinfo="percent+label")
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)

    # --- Failure monitor table ---
    with col_r:
        section("Failure Monitor Detail", "📋")
        fm = load_failure_monitor()
        if not fm.empty:
            st.dataframe(
                fm.rename(columns={
                    "gateway":          "Gateway",
                    "failure_reason":   "Reason",
                    "failure_count":    "Count",
                    "revenue_at_risk":  "At Risk (₹)",
                    "recovered_count":  "Recovered",
                    "recovery_rate_pct":"Recovery %",
                    "net_revenue_lost": "Net Lost (₹)",
                }).style.background_gradient(
                    subset=["Net Lost (₹)"],
                    cmap="RdYlGn_r",
                ),
                use_container_width=True,
                hide_index=True,
            )


# ---------------------------------------------------------------------------
# PAGE 4 — Data Quality
# ---------------------------------------------------------------------------

def page_data_quality() -> None:
    st.title("🔍 Data Quality")
    st.caption("Validation status · duplicates · null monitoring · freshness")

    results  = load_validation()
    statuses = [r.status.value for r in results]
    n_pass   = statuses.count("PASS")
    n_warn   = statuses.count("WARN")
    n_fail   = statuses.count("FAIL")
    n_error  = statuses.count("ERROR")

    # --- Summary scorecard ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("✅ PASS",  n_pass,  delta=None)
    c2.metric("⚠️ WARN",  n_warn,  delta=None)
    c3.metric("❌ FAIL",  n_fail,  delta=None)
    c4.metric("💥 ERROR", n_error, delta=None)

    # Colour the border based on worst status
    worst = "FAIL" if n_fail else ("WARN" if n_warn else "PASS")
    border_col = STATUS_COLOURS.get(worst, BRAND_GREY)
    st.markdown(
        f'<div style="border-left:4px solid {border_col};'
        f'padding:8px 16px;margin-bottom:16px;'
        f'background:rgba(0,0,0,0.05);border-radius:4px">'
        f'<b>Overall status: {worst}</b></div>',
        unsafe_allow_html=True,
    )

    st.markdown("---")

    # --- Validation check table ---
    section("Validation Checks", "🧪")
    rows = []
    for r in results:
        rows.append({
            "Check":     r.check_name,
            "Status":    r.status.value,
            "Metric":    str(r.metric) if r.metric is not None else "—",
            "Threshold": str(r.threshold) if r.threshold is not None else "—",
            "Message":   r.message,
        })
    df_checks = pd.DataFrame(rows)

    # Colour rows by status
    def colour_status(val: str) -> str:
        return f"color: {STATUS_COLOURS.get(val, BRAND_GREY)}; font-weight: 600"

    st.dataframe(
        df_checks.style.applymap(colour_status, subset=["Status"]),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("---")

    col_l, col_r = st.columns(2)

    # --- Duplicate detail ---
    with col_l:
        section("Duplicate Transactions", "🔁")
        recon = load_recon()
        dupes = recon.get("duplicates", pd.DataFrame())
        if not dupes.empty:
            st.warning(f"{len(dupes)} duplicate groups detected.")
            st.dataframe(
                dupes.rename(columns={
                    "user_id":         "User",
                    "payment_date":    "Date",
                    "amount":          "Amount (₹)",
                    "gateway":         "Gateway",
                    "duplicate_count": "Dupes",
                }).head(15),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.success("No duplicate transactions detected.")

    # --- Null monitoring ---
    with col_r:
        section("Null Monitoring", "🕳️")
        for r in results:
            if r.check_name == "detect_nulls" and r.detail_df is not None:
                null_df = r.detail_df.copy()
                null_df["null_pct"] = (
                    null_df["null_count"] / null_df["total_rows"] * 100
                ).round(2)
                st.dataframe(
                    null_df.rename(columns={
                        "column_ref":  "Column",
                        "null_count":  "Nulls",
                        "total_rows":  "Total",
                        "null_pct":    "Null %",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )
                break

    # --- Validation status donut ---
    section("Validation Status Distribution", "📊")
    status_counts = pd.DataFrame({
        "Status": ["PASS", "WARN", "FAIL", "ERROR"],
        "Count":  [n_pass, n_warn, n_fail, n_error],
    })
    fig = px.pie(
        status_counts[status_counts["Count"] > 0],
        names="Status",
        values="Count",
        hole=0.5,
        color="Status",
        color_discrete_map=STATUS_COLOURS,
        title="Check Results Distribution",
    )
    fig.update_traces(textinfo="label+value")
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)")

    col_chart, col_info = st.columns([1, 2])
    with col_chart:
        st.plotly_chart(fig, use_container_width=True)
    with col_info:
        st.markdown("**What each status means:**")
        st.markdown(
            "- 🟢 **PASS** — metric within expected bounds, data clean\n"
            "- 🟡 **WARN** — metric approaching threshold or expected dirty data\n"
            "- 🔴 **FAIL** — metric breached threshold, action required\n"
            "- 🟣 **ERROR** — check itself failed (SQL / infra issue)"
        )
        st.markdown("**Expected WARNs in dev/test:**")
        st.markdown(
            "- `detect_duplicates` — 150 duplicates intentionally injected\n"
            "- `freshness_check` — data ends 2024-12-31 (static dataset)"
        )

    # --- Stale pending ---
    section("Stale Pending Payments", "⏳")
    stale = recon.get("stale_pending", pd.DataFrame())
    if not stale.empty:
        st.warning(f"{len(stale)} stale pending payments found.")
        st.dataframe(stale, use_container_width=True, hide_index=True)
    else:
        st.success("No stale pending payments — all pending transactions have events.")


# ---------------------------------------------------------------------------
# Main router
# ---------------------------------------------------------------------------

def main() -> None:
    page = render_sidebar()

    if page == "💰 Revenue Overview":
        page_revenue_overview()
    elif page == "👥 Cohort Revenue":
        page_cohort_revenue()
    elif page == "⚠️ Failure Monitoring":
        page_failure_monitoring()
    elif page == "🔍 Data Quality":
        page_data_quality()


if __name__ == "__main__":
    main()
