"""
Full integration test for all 3 Hermes agents.
Run from hermes root:  python -m tests.test_all_agents <company_name>

Requires:
  - Valid Snowflake credentials (env vars or config.py)
  - Valid Perplexity API key  (config.py)
"""
import json
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from snowflake_client import get_snowflake_session, fetch_all_snowflake_data
from agents.researcher import agent_researcher
from agents.contextualizer import agent_contextualizer
from agents.strategist import agent_strategist


# ── helpers ──────────────────────────────────────────────────────────────
DIVIDER = "=" * 70


def _header(title):
    print(f"\n{DIVIDER}")
    print(f"  {title}")
    print(DIVIDER)


def _timed(label, func, *args, **kwargs):
    """Run *func* and print elapsed time."""
    print(f"\n⏳  {label} ...")
    t0 = time.time()
    result = func(*args, **kwargs)
    elapsed = time.time() - t0
    print(f"✅  {label} finished in {elapsed:.1f}s")
    return result


# ── individual test steps ────────────────────────────────────────────────
def test_session():
    """1. Create a Snowflake session."""
    _header("Step 1: Snowflake Session")
    session = _timed("get_snowflake_session", get_snowflake_session)
    print(f"   Session type: {type(session).__name__}")
    return session


def test_snowflake_data(session, company_name):
    """2. Fetch raw Snowflake data for the company."""
    _header(f"Step 2: Fetch Snowflake Data  ({company_name})")
    raw_data = _timed("fetch_all_snowflake_data", fetch_all_snowflake_data, session, company_name)

    for key, value in raw_data.items():
        status = "OK" if value else "EMPTY / NONE"
        print(f"   {key}: {status}")
        if value:
            print(f"      {json.dumps(value, indent=4, default=str)}")

    return raw_data


def test_researcher(session, company_name):
    """3. Agent 1 – Market Researcher (Perplexity + Cortex)."""
    _header("Step 3: Agent 1 – Market Researcher")
    result = _timed("agent_researcher", agent_researcher, session, company_name)
    print("\n--- AGENT 1 OUTPUT (truncated to 500 chars) ---")
    print(result[:500] if result else "(empty)")
    return result


def test_contextualizer(session, company_name, raw_data, researcher_output):
    """4. Agent 2 – Data Contextualizer (Cortex)."""
    _header("Step 4: Agent 2 – Data Contextualizer")
    data_label = "WITH" if raw_data else "WITHOUT"
    research_label = "WITH" if researcher_output else "WITHOUT"
    print(f"   Running {data_label} internal data, {research_label} researcher output")

    result = _timed(
        "agent_contextualizer",
        agent_contextualizer,
        session, company_name, raw_data, researcher_output,
    )
    print("\n--- AGENT 2 OUTPUT (truncated to 500 chars) ---")
    print(result[:500] if result else "(empty)")
    return result


def test_strategist(session, company_name, researcher_output, contextualizer_output, raw_data):
    """5. Agent 3 – Sales Strategist (Cortex)."""
    _header("Step 5: Agent 3 – Sales Strategist")
    result = _timed(
        "agent_strategist",
        agent_strategist,
        session, company_name, researcher_output, contextualizer_output, raw_data,
    )
    print("\n--- AGENT 3 OUTPUT (truncated to 500 chars) ---")
    print(result[:500] if result else "(empty)")
    return result


# ── full pipeline runners ────────────────────────────────────────────────
def run_existing_account_test(session, company_name, raw_data):
    """Mirror the existing-account orchestrator flow."""
    _header("EXISTING ACCOUNT PIPELINE")
    researcher_output = test_researcher(session, company_name)
    contextualizer_output = test_contextualizer(session, company_name, raw_data, None)
    strategist_output = test_strategist(session, company_name, researcher_output, contextualizer_output, raw_data)
    return {
        "researcher": researcher_output,
        "contextualizer": contextualizer_output,
        "strategist": strategist_output,
    }


def run_new_account_test(session, company_name):
    """Mirror the new-account orchestrator flow (sequential, no Snowflake data)."""
    _header("NEW ACCOUNT (PROSPECT) PIPELINE")
    researcher_output = test_researcher(session, company_name)
    contextualizer_output = test_contextualizer(session, company_name, None, researcher_output)
    strategist_output = test_strategist(session, company_name, researcher_output, contextualizer_output, None)
    return {
        "researcher": researcher_output,
        "contextualizer": contextualizer_output,
        "strategist": strategist_output,
    }


# ── entry point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    company = sys.argv[1] if len(sys.argv) > 1 else "NetJets"

    # Optional flag:  --new  to test the prospect pipeline instead
    run_as_new = "--new" in sys.argv

    print(f"\n🚀  Hermes All-Agent Test")
    print(f"   Company : {company}")
    print(f"   Mode    : {'NEW account (prospect)' if run_as_new else 'EXISTING account'}")
    print()

    t_total = time.time()

    # 1. Snowflake session (needed for Agents 2 & 3)
    session = test_session()

    if run_as_new:
        # ── New / Prospect path ──────────────────────────────────────────
        results = run_new_account_test(session, company)
    else:
        # ── Existing account path ────────────────────────────────────────
        raw_data = test_snowflake_data(session, company)
        results = run_existing_account_test(session, company, raw_data)

    # ── Final summary ────────────────────────────────────────────────────
    _header("ALL DONE")
    elapsed_total = time.time() - t_total
    for agent, output in results.items():
        length = len(output) if output else 0
        print(f"   {agent:20s}  →  {length:,} chars")
    print(f"\n   Total elapsed: {elapsed_total:.1f}s")
    print(f"\n{'─' * 70}")
