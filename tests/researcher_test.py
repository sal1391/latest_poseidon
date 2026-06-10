"""
Quick test script for Agent 1: Strategic Researcher (Perplexity + Cortex).
Run from hermes root: python -m tests.researcher_test <company_name>

Requires:
  - Valid PERPLEXITY_API_KEY in config.py or .env
  - Valid Snowflake credentials (for the Cortex summary step)

Prints Snowflake token counts (input & output) for every Cortex call.
"""
import json
import sys
import os
import time

# Add hermes root so imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from snowflake_client import get_snowflake_session, count_tokens
from agents.researcher import (
    _get_client,
    _research_sustainability_esg,
    _research_market_position,
    _research_strategic_profile,
    _market_position_to_md,
    _strategic_profile_to_md,
    _sustainability_esg_to_md,
    _summarize_with_cortex,
    agent_researcher,
)

DIVIDER = "=" * 60


def _print_tokens(session, label, input_text, output_text):
    """Print input and output token counts for a Cortex call."""
    in_tokens = count_tokens(session, input_text) if input_text else None
    out_tokens = count_tokens(session, output_text) if output_text else None
    print(f"  🪙  {label} tokens  →  input: {in_tokens or 'N/A'}  |  output: {out_tokens or 'N/A'}")
    return in_tokens, out_tokens


def test_client():
    """Test Perplexity client creation."""
    print("--- Test: _get_client() ---")
    client = _get_client()
    print(f"Client created: {type(client).__name__}")
    return client


def test_sustainability_esg(client, company_name):
    """Test Call 1: Sustainability & ESG (sonar-deep-research)."""
    print(f"\n--- Test: Sustainability & ESG for '{company_name}' ---")
    t0 = time.time()
    result = _research_sustainability_esg(client, company_name)
    elapsed = time.time() - t0
    status = "OK" if result else "FAILED"
    print(f"  Result: {status}  ({elapsed:.1f}s)")
    if result:
        print(json.dumps(result, indent=2))
    return result


def test_market_position(client, company_name):
    """Test Call 2: Market Position (sonar)."""
    print(f"\n--- Test: Market Position for '{company_name}' ---")
    t0 = time.time()
    result = _research_market_position(client, company_name)
    elapsed = time.time() - t0
    status = "OK" if result else "FAILED"
    print(f"  Result: {status}  ({elapsed:.1f}s)")
    if result:
        print(json.dumps(result, indent=2))
    return result


def test_strategic_profile(client, company_name):
    """Test Call 3: Strategic Profile (sonar-pro)."""
    print(f"\n--- Test: Strategic Profile for '{company_name}' ---")
    t0 = time.time()
    result = _research_strategic_profile(client, company_name)
    elapsed = time.time() - t0
    status = "OK" if result else "FAILED"
    print(f"  Result: {status}  ({elapsed:.1f}s)")
    if result:
        print(json.dumps(result, indent=2))
    return result


def test_cortex_summary(session, company_name, esg_data, market_data, profile_data):
    """Test the Cortex summarisation step with token counting."""
    print(f"\n--- Test: Cortex Summary for '{company_name}' ---")

    market_md = _market_position_to_md(market_data)
    profile_md = _strategic_profile_to_md(profile_data)
    esg_md = _sustainability_esg_to_md(esg_data)

    # Build the same prompt the function will use, so we can count input tokens
    # (We call the function directly to get the actual output.)
    t0 = time.time()
    summary = _summarize_with_cortex(session, company_name, market_md, profile_md, esg_md)
    elapsed = time.time() - t0

    status = "OK" if summary else "FAILED"
    print(f"  Result: {status}  ({elapsed:.1f}s)")

    # Token counts for the Cortex call
    combined_input = f"{market_md}\n{profile_md}\n{esg_md}"
    _print_tokens(session, "Cortex summary", combined_input, summary or "")

    if summary:
        print("\n=== CORTEX EXECUTIVE SUMMARY ===")
        print(summary)
    return summary


def test_full_agent(session, company_name):
    """Test full agent_researcher pipeline (Perplexity + Cortex summary)."""
    print(f"\n--- Test: agent_researcher('{company_name}') ---")
    t0 = time.time()
    result = agent_researcher(session, company_name)
    elapsed = time.time() - t0
    print(f"  Completed in {elapsed:.1f}s  ({len(result):,} chars)")

    # Count tokens on the full final output
    out_tokens = count_tokens(session, result)
    print(f"  🪙  Full output tokens: {out_tokens or 'N/A'}")

    print("\n=== AGENT 1 OUTPUT ===")
    print(result)
    return result


if __name__ == "__main__":
    company = sys.argv[1] if len(sys.argv) > 1 else "NetJets"
    print(f"Testing Agent 1: Strategic Researcher (company: {company})\n")

    t_total = time.time()

    # 0. Create Snowflake session (needed for Cortex summary + token counting)
    print("--- Test: get_snowflake_session() ---")
    session = get_snowflake_session()
    print(f"Session created: {type(session).__name__}")

    # 1. Create Perplexity client
    client = test_client()

    # 2. Test each Perplexity research call individually
    esg_data = test_sustainability_esg(client, company)
    market_data = test_market_position(client, company)
    profile_data = test_strategic_profile(client, company)

    # 3. Test Cortex summary step separately (with token counts)
    print(f"\n{DIVIDER}")
    print("CORTEX SUMMARISATION (with token counts)")
    print(DIVIDER)
    test_cortex_summary(session, company, esg_data, market_data, profile_data)

    # 4. Test the full pipeline (runs all 3 Perplexity calls + Cortex summary)
    print(f"\n\n{DIVIDER}")
    print("FULL PIPELINE (agent_researcher)")
    print(DIVIDER)
    test_full_agent(session, company)

    elapsed_total = time.time() - t_total
    print(f"\n--- Done  (total: {elapsed_total:.1f}s) ---")
