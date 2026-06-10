"""
Snowflake Data Layer - Snowpark session, queries, and Cortex helper.

Data source: MARINE_SALES_PLANNING_V (fully qualified name built at runtime from
  SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA, and SNOWFLAKE_TABLE env vars)
Grain: One row per marine fuel transaction (POI).
Date column: LIFT_ETA_DATE (fuel delivery or expected delivery date).

Metrics are computed side-by-side for two periods:
  - prior_year : full prior calendar year (Jan 1 - Dec 31 of last year)
  - ytd        : current calendar year-to-date (Jan 1 of this year - today)
"""
from datetime import date
import streamlit as st
import pandas as pd
from snowflake.snowpark import Session
from config import SNOWFLAKE_CONNECTION, SNOWFLAKE_TABLE


TABLE_FQN = (
    f"{SNOWFLAKE_CONNECTION['database']}"
    f".{SNOWFLAKE_CONNECTION['schema']}"
    f".{SNOWFLAKE_TABLE}"
)
DATE_COL = "LIFT_ETA_DATE"


# =============================================================================
# METRIC & DIMENSION DICTIONARIES
# =============================================================================
# Authoritative definitions consumed by the agent prompts. Margin MUST always
# be recomputed from aggregated GP / aggregated Volume -- never summed/averaged
# from a raw margin column.
FIELD_DICTIONARY = {
    # Metrics
    "VOLUME": "Total marine fuel tons delivered. SQL: SUM(FIXED_TONS).",
    "GP": "Total gross profit (USD). SQL: SUM(GROSS_PROFIT).",
    "MARGIN": (
        "Profit per ton (USD/ton). SQL: SUM(GROSS_PROFIT) / NULLIF(SUM(FIXED_TONS), 0). "
        "NEVER sum or average the raw margin column -- always compute as a ratio of aggregated values."
    ),
    "NUM_WON": "Count of transactions won. SQL: SUM(\"#_FIXTURES\").",
    "NUM_INQUIRIES": "Total inquiries (1 per row). SQL: SUM(\"#_INQUIRIES\").",
    "NUM_LOST": "Inquiries that did not convert. SQL: SUM(\"#_INQUIRIES\") - SUM(\"#_FIXTURES\").",
    # Dimensions (available for drill-down / GROUP BY)
    "CUST_NM": "Customer (Customer category).",
    "SUPPLIER_NM": "Supplier (Supplier category).",
    "LOC_NM": "Port (Location category).",
    "SUPPLY_TEAM_NAME": "Supply Region (Location category).",
    "SUPP_BRKR": "Supply Broker (Broker category).",
    "PRIMARY_SUPPLY_TEAM_OFFICE": "Supply Team Office (Broker category).",
    "PRIMARY_SUPPLY_TEAM_OFFICE_REGION": "Supply Team Region (Broker category).",
    "PRIMARY_BRKR": "Primary Broker (Broker category).",
    "PRIMARY_BRKR_OFFICE": "Primary Broker Office (Broker category).",
    "PRIMARY_BRKR_REGION": "Primary Broker Region (Broker category).",
    "CUSTOMER_BRKR": "Customer Broker (Broker category).",
    "CUSTOMER_TEAM_NAME": "Customer Broker Office (Broker category).",
    "CBO_REGION": "Customer Broker Region (Broker category).",
    "DEAL_CLASSIFICATION_TRADE_CUT": "Deal Class (Deal Type category).",
    "VESSEL_DASHBOARD_SHIPTYPE_GRP": "Ship Type (Vessel category) — vessel-classified ship type group.",
    "CUST_DASHBOARD_SHIPTYPE_GRP": "Customer Ship Type (Vessel category) — customer-classified ship type group; compare against VESSEL_DASHBOARD_SHIPTYPE_GRP to spot classification divergence.",
}

# Canonical SELECT expressions used across all metric queries.
_METRICS_SELECT_SQL = """
    SUM(FIXED_TONS) AS VOLUME,
    SUM(GROSS_PROFIT) AS GP,
    SUM(GROSS_PROFIT) / NULLIF(SUM(FIXED_TONS), 0) AS MARGIN,
    SUM("#_FIXTURES") AS NUM_WON,
    SUM("#_INQUIRIES") AS NUM_INQUIRIES,
    SUM("#_INQUIRIES") - SUM("#_FIXTURES") AS NUM_LOST
""".strip()


# =============================================================================
# HELPERS
# =============================================================================
def _sanitize_int(value, default=None):
    try:
        return int(value)
    except Exception:
        return default


def _sql_escape(value):
    """Escape single quotes for safe inlining in SQL string literals."""
    return str(value).replace("'", "''")


def _period_bounds():
    """
    Return the two comparison windows.

    Returns:
        dict with keys 'prior_year' and 'ytd', each a (start_date, end_date, label) tuple.
    """
    today = date.today()
    py_start = date(today.year - 1, 1, 1)
    py_end = date(today.year - 1, 12, 31)
    ytd_start = date(today.year, 1, 1)
    ytd_end = today
    return {
        "prior_year": (py_start, py_end, f"Prior Year {today.year - 1}"),
        "ytd": (ytd_start, ytd_end, f"YTD {today.year}"),
    }


def _empty_metrics():
    return {
        "VOLUME": 0.0,
        "GP": 0.0,
        "MARGIN": 0.0,
        "NUM_WON": 0,
        "NUM_INQUIRIES": 0,
        "NUM_LOST": 0,
    }


def _coalesce_metrics(row):
    """Replace None/NaN in a metric row with zeros so downstream rendering is safe."""
    if not row:
        return _empty_metrics()
    out = {}
    for k in ("VOLUME", "GP", "MARGIN", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST"):
        v = row.get(k)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            out[k] = 0.0 if k in ("VOLUME", "GP", "MARGIN") else 0
        else:
            out[k] = v
    return out


# =============================================================================
# SESSION & CORTEX
# =============================================================================
def get_snowflake_session():
    """Create and return a Snowpark Session from config credentials."""
    return Session.builder.configs(SNOWFLAKE_CONNECTION).create()


def call_cortex_complete(session, prompt, model="claude-sonnet-4-5"):
    """Call Snowflake Cortex LLM completion."""
    safe_prompt = _sql_escape(prompt)
    query = f"""SELECT snowflake.cortex.complete('{model}', '{safe_prompt}') AS CONTENT"""
    result = session.sql(query).to_pandas()["CONTENT"][0]
    return result


# =============================================================================
# QUERY FUNCTIONS
# =============================================================================
def get_all_company_names(session):
    """Return distinct CUST_NM values from the marine sales planning view."""
    sql = f"""
        SELECT DISTINCT CUST_NM AS COMPANY_NAME
        FROM {TABLE_FQN}
        WHERE CUST_NM IS NOT NULL AND TRIM(CUST_NM) <> ''
        ORDER BY 1
    """
    try:
        df = session.sql(sql).to_pandas()
        return df["COMPANY_NAME"].tolist()
    except Exception as e:
        st.error(f"Failed to load company names: {e}")
        return []


def get_customer_metrics(session, company_name):
    """
    Return the six headline metrics for a customer, computed for both
    prior calendar year and current YTD.

    Shape:
        {
          "prior_year": {"VOLUME":..., "GP":..., "MARGIN":..., "NUM_WON":...,
                         "NUM_INQUIRIES":..., "NUM_LOST":...},
          "ytd":        {...same keys...}
        }
    """
    periods = _period_bounds()
    safe_name = _sql_escape(company_name)
    out = {}
    for key, (start, end, _label) in periods.items():
        sql = f"""
            SELECT
                {_METRICS_SELECT_SQL}
            FROM {TABLE_FQN}
            WHERE CUST_NM = '{safe_name}'
              AND {DATE_COL} BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'
        """
        try:
            df = session.sql(sql).to_pandas()
            row = df.to_dict("records")[0] if not df.empty else {}
            out[key] = _coalesce_metrics(row)
        except Exception as e:
            out[key] = {"error": str(e), **_empty_metrics()}
    return out


def get_top5_ports(session, company_name):
    """
    Return Top 5 ports (LOC_NM) by SUM(FIXED_TONS) for a customer,
    one list per period (prior_year, ytd).

    Each port record contains: PORT, VOLUME, GP, MARGIN, NUM_WON,
    NUM_INQUIRIES, NUM_LOST.
    """
    periods = _period_bounds()
    safe_name = _sql_escape(company_name)
    out = {}
    for key, (start, end, _label) in periods.items():
        sql = f"""
            SELECT
                LOC_NM AS PORT,
                {_METRICS_SELECT_SQL}
            FROM {TABLE_FQN}
            WHERE CUST_NM = '{safe_name}'
              AND {DATE_COL} BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'
              AND LOC_NM IS NOT NULL
            GROUP BY LOC_NM
            ORDER BY VOLUME DESC NULLS LAST
            LIMIT 5
        """
        try:
            df = session.sql(sql).to_pandas()
            out[key] = df.to_dict("records") if not df.empty else []
        except Exception as e:
            out[key] = {"error": str(e)}
    return out


def fetch_all_snowflake_data(session, company_name):
    """
    Consolidated marine sales planning data for the agents.

    Returns:
        {
          "customer_metrics": {prior_year: {...}, ytd: {...}},
          "top5_ports":       {prior_year: [...], ytd: [...]},
          "periods":          {prior_year: "Prior Year YYYY", ytd: "YTD YYYY"},
          "field_dictionary": FIELD_DICTIONARY,
        }
    """
    periods = _period_bounds()
    return {
        "customer_metrics": get_customer_metrics(session, company_name),
        "top5_ports": get_top5_ports(session, company_name),
        "periods": {
            "prior_year": periods["prior_year"][2],
            "ytd": periods["ytd"][2],
        },
        "field_dictionary": FIELD_DICTIONARY,
    }


def count_tokens(session, text, model="claude-sonnet-4-5"):
    """
    Count tokens in a text string using Snowflake AI_COUNT_TOKENS.
    Defaults to llama3.3-70b since claude-sonnet-4-5 is not supported
    by AI_COUNT_TOKENS. The count is approximate but useful for cost tracking.
    """
    safe_text = text.replace("'", "''")
    query = f"""SELECT AI_COUNT_TOKENS('ai_complete', '{model}', '{safe_text}') AS TOKEN_COUNT"""
    try:
        result = session.sql(query).to_pandas()["TOKEN_COUNT"][0]
        return int(result)
    except Exception as e:
        print(f"Token count error: {e}")
        return None
