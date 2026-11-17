from datetime import datetime
import os

import clickhouse_connect
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from plotly.subplots import make_subplots

DEMO_MERCHANT_ID = 42
TIME_RANGES = {
    "Last 1 Hour": "created_at >= now() - INTERVAL 1 HOUR",
    "Last 4 Hours": "created_at >= now() - INTERVAL 4 HOUR",
    "Last 24 Hours": "created_at >= now() - INTERVAL 24 HOUR",
    "Last 7 Days": "created_at >= now() - INTERVAL 7 DAY",
    "Last 30 Days": "created_at >= now() - INTERVAL 30 DAY",
    "All Time": "1 = 1",
}
TIME_BUCKETS = {
    "Last 1 Hour": ("toStartOfHour", "hour"),
    "Last 4 Hours": ("toStartOfHour", "hour"),
    "Last 24 Hours": ("toStartOfHour", "hour"),
    "Last 7 Days": ("toStartOfHour", "hour"),
    "Last 30 Days": ("toStartOfDay", "day"),
    "All Time": ("toStartOfDay", "day"),
}

load_dotenv(".env")

st.set_page_config(
    page_title="PennLogic Merchant Analytics",
    page_icon="📊",
    layout="wide",
)

st.markdown(
    """
    <style>
    :root {
        color-scheme: dark;
        --primary-color: #FCE300;
        --background-color: #14171D;
        --secondary-background-color: #1B2029;
        --text-color: #F8FAFC;
        --pl-bg: #14171D;
        --pl-card: #222731;
        --pl-panel: #1B2029;
        --pl-border: #343A46;
        --pl-yellow: #FCE300;
        --pl-text: #F8FAFC;
        --pl-muted: #A3AAB8;
        --pl-green: #22C55E;
        --pl-red: #EF4444;
    }
    html, body, [data-testid="stAppViewContainer"], .stApp {
        background: var(--pl-bg);
        color: var(--pl-text);
    }
    [data-testid="stHeader"] { background: rgba(20, 23, 29, 0.96); }
    [data-testid="stSidebar"] { background: var(--pl-panel); }
    [data-testid="stMainBlockContainer"] {
        max-width: 1500px;
        padding-top: 1.35rem;
        padding-bottom: 1.5rem;
    }
    [data-testid="stVerticalBlockBorderWrapper"] {
        background: var(--pl-panel);
        border-color: var(--pl-border);
        border-radius: 14px;
    }
    [data-testid="stMetric"] {
        background: var(--pl-card);
        border: 1px solid var(--pl-border);
        border-radius: 12px;
        padding: 15px 18px 17px;
        min-height: 112px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
    }
    [data-testid="stMetric"] label,
    [data-testid="stMetricLabel"] { color: var(--pl-muted); }
    [data-testid="stMetricValue"] {
        color: var(--pl-text);
        font-size: clamp(1.55rem, 2vw, 2rem);
        font-weight: 650;
        letter-spacing: -0.035em;
    }
    [data-testid="stMetric"] [data-testid="stMetricLabel"]::before {
        content: "";
        display: inline-block;
        width: 6px;
        height: 6px;
        margin: 0 7px 2px 0;
        border-radius: 50%;
        background: var(--pl-yellow);
    }
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stCaptionContainer"] { color: var(--pl-muted); }
    h1, h2, h3, [data-testid="stSubheader"] { color: var(--pl-text); }
    [data-testid="stTextInput"] input,
    [data-testid="stSelectbox"] [role="combobox"],
    [data-testid="stMultiSelect"] [role="combobox"] {
        background: var(--pl-card);
        border-color: var(--pl-border);
        color: var(--pl-text);
    }
    [data-testid="stRadio"] input { accent-color: var(--pl-yellow); }
    [data-testid="stRadio"] label { color: var(--pl-text); }
    [data-testid="stRadio"] label:has(input:checked) {
        border: 1px solid var(--pl-yellow);
        border-radius: 8px;
        background: var(--pl-card);
        color: var(--pl-yellow);
        font-weight: 700;
    }
    [data-testid="stButton"] button {
        border: 1px solid var(--pl-border);
        border-radius: 9px;
        background: var(--pl-card);
        color: var(--pl-text);
        font-weight: 600;
    }
    [data-testid="stButton"] button:hover {
        border-color: var(--pl-yellow);
        color: var(--pl-yellow);
    }
    [data-testid="stButton"] button[kind="primary"] {
        border-color: var(--pl-yellow);
        background: var(--pl-yellow);
        color: #14171D;
    }
    [data-testid="stButton"] button[kind="primary"]:hover {
        border-color: #fff06a;
        background: #fff06a;
        color: #14171D;
    }
    [data-testid="stDataFrame"] {
        border: 1px solid var(--pl-border);
        border-radius: 10px;
        overflow: hidden;
    }
    [data-testid="stAlert"] { border-radius: 10px; }
    hr { border-color: var(--pl-border); }
    .pl-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 18px;
        margin: 0 0 1.1rem;
        padding: 4px 0 18px;
        border-bottom: 1px solid var(--pl-border);
    }
    .pl-brand { display: flex; align-items: center; }
    .pl-subtitle-row {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        column-gap: 20px;
        row-gap: 8px;
        margin-top: 8px;
        color: var(--pl-text);
        font-size: 1.4rem;
        font-weight: 600;
        line-height: 1.35;
    }
    .pl-title {
        margin: 0;
        color: var(--pl-text);
        font-size: clamp(1.45rem, 2.5vw, 2rem);
        font-weight: 720;
        letter-spacing: -0.04em;
        line-height: 1.15;
    }
    .pl-subtitle { margin: 0; color: inherit; font: inherit; }
    [data-testid="stMarkdownContainer"] .pl-subtitle-row .pl-slogan {
        margin: 0;
        color: var(--pl-yellow);
        font: inherit;
    }
    .pl-cloud-badge {
        padding: 7px 11px;
        border: 1px solid var(--pl-border);
        border-radius: 999px;
        color: var(--pl-muted);
        font-size: 0.68rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        white-space: nowrap;
    }
    .pl-section-label {
        margin: 0 0 0.6rem;
        color: var(--pl-muted);
        font-size: 0.68rem;
        font-weight: 750;
        letter-spacing: 0.12em;
        text-transform: uppercase;
    }
    @media (max-width: 700px) {
        .pl-cloud-badge { display: none; }
        [data-testid="stMainBlockContainer"] { padding: 1rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <header class="pl-header">
        <div class="pl-brand">
            <div>
                <h1 class="pl-title">PennLogic | Merchant Intelligence</h1>
                <div class="pl-subtitle-row">
                    <p class="pl-subtitle">Real-time merchant analytics powered by ClickHouse Cloud</p>
                    <p class="pl-slogan">Keep the experience. Upgrade the engine.</p>
                </div>
            </div>
        </div>
        <div class="pl-cloud-badge">ANALYTICS PORTAL</div>
    </header>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_clickhouse_client():
    return clickhouse_connect.get_client(
        host=os.environ["CH_HOST"],
        port=int(os.getenv("CH_PORT", "8443")),
        username=os.environ["CH_USER"],
        password=os.environ["CH_PASSWORD"],
        database=os.getenv("CH_DATABASE", "merchant_portal"),
        secure=True,
    )


try:
    client = get_clickhouse_client()
except Exception:
    st.error(
        "Unable to connect to analytics right now. Please check the demo connection."
    )
    st.stop()


@st.cache_data(ttl=300)
def get_merchant_ids():
    result = client.query("""
        SELECT DISTINCT merchant_id
        FROM merchant_portal.orders_analytics
        ORDER BY merchant_id
        """)
    return [int(row[0]) for row in result.result_rows if row[0] is not None]


def scoped_filter(scope, selected_merchants):
    """Return a parameterized merchant predicate for every dashboard query."""
    if scope == "all":
        return "1 = 1", {}
    if scope == "one":
        return "merchant_id = {merchant_id:UInt64}", {
            "merchant_id": int(selected_merchants[0])
        }
    return (
        "merchant_id IN {merchant_ids:Array(UInt64)}",
        {"merchant_ids": [int(merchant_id) for merchant_id in selected_merchants]},
    )


def query_frame(sql, parameters):
    return client.query_df(sql, parameters=parameters)


def query_orders_filter(merchant_predicate, time_filter):
    return f"({merchant_predicate}) AND ({time_filter})"


def aliased_time_filter(alias, selected_time_range):
    if selected_time_range == "All Time":
        return "1 = 1"
    return TIME_RANGES[selected_time_range].replace("created_at", f"{alias}.created_at")


def format_currency_compact(value):
    value = float(value or 0)
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:,.2f}K"
    return f"${value:,.2f}"


def style_chart(fig, height):
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=12, b=10),
        hovermode="x unified",
        paper_bgcolor="rgba(0, 0, 0, 0)",
        plot_bgcolor="rgba(0, 0, 0, 0)",
        font=dict(family="Inter, Segoe UI, sans-serif", color="#A3AAB8", size=12),
        hoverlabel=dict(bgcolor="#222731", bordercolor="#343A46", font_color="#F8FAFC"),
        legend=dict(font=dict(color="#A3AAB8")),
    )
    fig.update_xaxes(
        showgrid=False,
        zeroline=False,
        linecolor="#343A46",
        tickfont=dict(color="#A3AAB8"),
        title_font=dict(color="#A3AAB8"),
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="#343A46",
        gridwidth=0.7,
        zeroline=False,
        linecolor="#343A46",
        tickfont=dict(color="#A3AAB8"),
        title_font=dict(color="#A3AAB8"),
    )


try:
    with st.container(border=True):
        st.markdown(
            '<div class="pl-section-label">Demo preview · filters</div>',
            unsafe_allow_html=True,
        )
        control1, control2, control3 = st.columns([1.5, 1, 1])
        preview_mode = control1.radio(
            "Demo preview mode",
            ["Admin Preview", "Merchant Preview"],
            horizontal=True,
        )
        time_range = control2.selectbox(
            "Time range",
            list(TIME_RANGES),
            index=0,
        )
        auto_refresh = control3.toggle(
            "Auto-refresh",
            value=True,
            help="Refresh analytics from ClickHouse every 5 seconds.",
        )

        if preview_mode == "Admin Preview":
            merchant_ids = get_merchant_ids()
            if not merchant_ids:
                st.info("No merchants are available in the analytics view.")
                st.stop()

            scope_controls = st.columns([1, 1.5])
            admin_scope = scope_controls[0].selectbox(
                "Admin analytics scope",
                ["All Merchants", "Individual Merchant", "Compare Merchants"],
                index=0,
            )
            if admin_scope == "All Merchants":
                scope = "all"
                selected_merchants = []
                scope_label = "All Merchants"
            elif admin_scope == "Individual Merchant":
                selected_merchant = scope_controls[1].selectbox(
                    "Select merchant",
                    merchant_ids,
                    format_func=lambda value: f"Merchant {value}",
                )
                scope = "one"
                selected_merchants = [selected_merchant]
                scope_label = f"Merchant {selected_merchant}"
            else:
                selected_merchants = scope_controls[1].multiselect(
                    "Select merchants to compare",
                    merchant_ids,
                    default=merchant_ids[:3],
                    format_func=lambda value: f"Merchant {value}",
                )
                scope = "many"
                scope_label = f"{len(selected_merchants)} selected merchants"
        else:
            scope = "one"
            selected_merchants = [DEMO_MERCHANT_ID]
            scope_label = f"Merchant {DEMO_MERCHANT_ID} (fixed demo identity)"
            st.info(
                f"Merchant Preview is fixed to Merchant {DEMO_MERCHANT_ID}. "
                "This presentation mode is not authentication or authorization. "
                "Production tenant isolation requires authenticated server-side identity "
                "and enforced, properly scoped database access."
            )

    @st.fragment(run_every="5s" if auto_refresh else None)
    def render_analytics(scope, selected_merchants, scope_label, time_range, preview_mode):
        time_filter = TIME_RANGES[time_range]
        bucket_function, _ = TIME_BUCKETS[time_range]

        if scope == "many" and not selected_merchants:
            st.info("Choose one or more merchants to show the comparison.")
            st.stop()

        merchant_predicate, merchant_parameters = scoped_filter(scope, selected_merchants)
        orders_where = query_orders_filter(merchant_predicate, time_filter)

        order_metrics = query_frame(
            f"""
            SELECT
                count() AS total_orders,
                round(ifNull(sum(order_amount), 0), 2) AS gross_order_value,
                round(
                    100.0 * countIf(order_status = 'CANCELLED') / nullIf(count(), 0),
                    2
                ) AS cancellation_rate
            FROM merchant_portal.orders_analytics
            WHERE {orders_where}
            """,
            merchant_parameters,
        )
        if order_metrics.empty:
            total_orders, gross_order_value, cancellation_rate = 0, 0.0, None
        else:
            metric_row = order_metrics.iloc[0]
            total_orders = int(metric_row["total_orders"] or 0)
            gross_order_value = float(metric_row["gross_order_value"] or 0)
            cancellation_rate = metric_row["cancellation_rate"]

        delivery_predicate, delivery_parameters = scoped_filter(scope, selected_merchants)
        delivery_merchant_filter = "1 = 1" if scope == "all" else f"o.{delivery_predicate}"
        delivery_where = query_orders_filter(
            delivery_merchant_filter,
            aliased_time_filter("o", time_range),
        )
        # Delivery rates use the linked order's creation time so all four KPIs share one range.
        delivery_query = f"""
            SELECT round(
                100.0 * countIf(d.on_time = true) / nullIf(count(), 0),
                2
            ) AS on_time_rate
            FROM merchant_portal.deliveries_analytics AS d
            INNER JOIN merchant_portal.orders_analytics AS o ON d.order_id = o.order_id
            WHERE {delivery_where}
        """
        on_time_df = query_frame(delivery_query, delivery_parameters)
        on_time_rate = None if on_time_df.empty else on_time_df.iloc[0]["on_time_rate"]

        st.caption(f"Viewing: {scope_label} · {time_range}")
        kpi_cols = st.columns(4)
        kpi_cols[0].metric("Total Orders", f"{total_orders:,}")
        kpi_cols[1].metric(
            "Gross Order Value",
            format_currency_compact(gross_order_value),
            help=f"Precise value: ${gross_order_value:,.2f}",
        )
        kpi_cols[2].metric(
            "Cancellation Rate",
            "—" if cancellation_rate is None else f"{float(cancellation_rate):.2f}%",
        )
        kpi_cols[3].metric(
            "On-Time Delivery Rate",
            "—" if on_time_rate is None else f"{float(on_time_rate):.2f}%",
        )
        st.caption(
            "Gross Order Value includes canceled orders. "
            f"Precise value: ${gross_order_value:,.2f}."
        )

        if preview_mode == "Admin Preview" and scope == "many":
            st.subheader("Merchant Comparison")
            comparison_df = query_frame(
                f"""
                WITH
                    merchant_orders AS (
                        SELECT
                            merchant_id,
                            count() AS total_orders,
                            round(ifNull(sum(order_amount), 0), 2) AS gross_order_value,
                            round(100.0 * countIf(order_status = 'CANCELLED') / nullIf(count(), 0), 2)
                                AS cancellation_rate
                        FROM merchant_portal.orders_analytics
                        WHERE {orders_where}
                        GROUP BY merchant_id
                    ),
                    merchant_deliveries AS (
                        SELECT
                            o.merchant_id,
                            round(100.0 * countIf(d.on_time = true) / nullIf(count(), 0), 2)
                                AS on_time_rate
                        FROM merchant_portal.deliveries_analytics AS d
                        INNER JOIN merchant_portal.orders_analytics AS o ON d.order_id = o.order_id
                        WHERE {"1 = 1" if scope == "all" else "o." + merchant_predicate}
                            AND ({aliased_time_filter("o", time_range)})
                        GROUP BY o.merchant_id
                    )
                SELECT
                    m.merchant_id AS Merchant,
                    m.total_orders AS `Total Orders`,
                    m.gross_order_value AS `Gross Order Value`,
                    m.cancellation_rate AS `Cancellation Rate (%)`,
                    d.on_time_rate AS `On-Time Delivery Rate (%)`
                FROM merchant_orders AS m
                LEFT JOIN merchant_deliveries AS d USING (merchant_id)
                ORDER BY m.merchant_id
                """,
                {**merchant_parameters, **delivery_parameters},
            )
            st.dataframe(comparison_df, hide_index=True, use_container_width=True)

        st.divider()
        order_volume_df = query_frame(
            f"""
            SELECT
                {bucket_function}(created_at) AS time_bucket,
                count() AS orders,
                countIf(order_status = 'CANCELLED') AS canceled_orders,
                round(100.0 * countIf(order_status = 'CANCELLED') / nullIf(count(), 0), 2)
                    AS cancellation_rate
            FROM merchant_portal.orders_analytics
            WHERE {orders_where}
            GROUP BY time_bucket
            ORDER BY time_bucket
            """,
            merchant_parameters,
        )
        volume_col, cancellation_col = st.columns(2, gap="large")
        with volume_col:
            st.subheader("Order Volume Over Time")
            if order_volume_df.empty:
                st.info("No orders found for the selected merchant and time range.")
            else:
                fig = px.line(
                    order_volume_df,
                    x="time_bucket",
                    y="orders",
                    labels={"time_bucket": "Time", "orders": "Orders"},
                )
                fig.update_traces(
                    mode="lines+markers",
                    line=dict(color="#FCE300", width=3),
                    marker=dict(color="#FCE300", size=6),
                    hovertemplate="%{x|%b %d, %Y %H:%M}<br>Orders: %{y:,}<extra></extra>",
                )
                style_chart(fig, 350)
                st.plotly_chart(fig, use_container_width=True)

        with cancellation_col:
            st.subheader("Cancellation Analysis")
            cancellation_df = order_volume_df
            if cancellation_df.empty:
                st.info("No cancellation data for the selected merchant and time range.")
            else:
                cancellation_fig = make_subplots(specs=[[{"secondary_y": True}]])
                cancellation_fig.add_trace(
                    go.Bar(
                        x=cancellation_df["time_bucket"],
                        y=cancellation_df["canceled_orders"],
                        name="Canceled orders",
                        marker_color="#FCE300",
                        opacity=0.88,
                        hovertemplate="%{x|%b %d, %Y %H:%M}<br>Canceled: %{y:,}<extra></extra>",
                    ),
                    secondary_y=False,
                )
                cancellation_fig.add_trace(
                    go.Scatter(
                        x=cancellation_df["time_bucket"],
                        y=cancellation_df["cancellation_rate"],
                        name="Cancellation rate",
                        mode="lines+markers",
                        line=dict(color="#EF4444", width=2.5),
                        marker=dict(size=6),
                        hovertemplate="%{x|%b %d, %Y %H:%M}<br>Rate: %{y:.2f}%<extra></extra>",
                    ),
                    secondary_y=True,
                )
                cancellation_fig.update_yaxes(
                    title_text="Canceled orders", secondary_y=False
                )
                cancellation_fig.update_yaxes(
                    title_text="Cancellation rate (%)", secondary_y=True
                )
                style_chart(cancellation_fig, 350)
                cancellation_fig.update_layout(legend=dict(orientation="h", y=1.12, x=0))
                st.plotly_chart(cancellation_fig, use_container_width=True)

            with st.expander("Cancellation reasons", expanded=False):
                reasons_df = query_frame(
                    f"""
                    SELECT
                        ifNull(nullIf(cancellation_reason, ''), 'Not Specified') AS reason,
                        count() AS canceled_orders
                    FROM merchant_portal.orders_analytics
                    WHERE {orders_where} AND order_status = 'CANCELLED'
                    GROUP BY reason
                    ORDER BY canceled_orders DESC
                    """,
                    merchant_parameters,
                )
                if reasons_df.empty:
                    st.caption("No canceled orders in this selection.")
                else:
                    st.dataframe(
                        reasons_df,
                        hide_index=True,
                        use_container_width=True,
                        height=180,
                    )
                    st.caption("Reasons reflect values recorded in the source data.")

        st.divider()
        with st.container(border=True):
            activity_heading, activity_action = st.columns([4, 1])
            activity_heading.subheader("Recent Activity")
            activity_action.button(
            "Refresh Data",
                type="primary",
                help="Reloads the dashboard from ClickHouse",
            )
            recent_orders_df = query_frame(
                f"""
                SELECT
                    order_id AS `Order ID`,
                    merchant_id AS Merchant,
                    order_status AS Status,
                    order_amount AS Amount,
                    created_at AS `Created At`
                FROM merchant_portal.orders_analytics
                WHERE {orders_where}
                ORDER BY created_at DESC
                LIMIT 8
                """,
                merchant_parameters,
            )
            if recent_orders_df.empty:
                st.info("No recent orders found for this selection.")
            else:
                st.dataframe(
                    recent_orders_df,
                    hide_index=True,
                    use_container_width=True,
                    height=320,
                    column_config={
                        "Amount": st.column_config.NumberColumn("Amount", format="$%.2f")
                    },
                )
            st.caption(
                f"Dashboard refreshed {datetime.now().astimezone().strftime('%b %d, %Y · %I:%M:%S %p %Z')}"
            )

    render_analytics(scope, selected_merchants, scope_label, time_range, preview_mode)

except Exception:
    st.error(
        "Analytics could not be loaded. Please retry or check the ClickHouse connection."
    )
    st.stop()

st.markdown(
    """
    <div style="padding: 8px 2px 0; color: #A3AAB8; font-size: 0.75rem;">
        PostgreSQL OLTP &nbsp;→&nbsp; ClickPipes CDC &nbsp;→&nbsp;
        ClickHouse Cloud OLAP &nbsp;→&nbsp; PennLogic Portal
    </div>
    """,
    unsafe_allow_html=True,
)
