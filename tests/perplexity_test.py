"""
Quick test script for Perplexity researcher functions.
Run from hermes root: python -m tests.perplexity_test

Ensure PERPLEXITY_API_KEY is set in config (local mode) or env (AWS mode).
"""
import json
import sys
import os

# Add hermes root so imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from snowflake_client import get_snowflake_session

from agents.researcher import (
    _get_client,
    _research_sustainability_esg,
    _research_market_position,
    _research_strategic_profile,
    agent_researcher,
)


def test_client():
    """Test Perplexity client creation."""
    print("--- Test: _get_client() ---")
    client = _get_client()
    print(f"Client created: {type(client).__name__}")
    return client


def test_individual_calls(company_name="NetJets"):
    """Test each research function separately."""
    print(f"\n--- Test: Individual research calls for '{company_name}' ---")
    client = _get_client()

    print("\n1. Sustainability & ESG (sonar-deep-research)...")
    esg = _research_sustainability_esg(client, company_name)
    print(f"   Result: {'OK' if esg else 'FAILED'}")
    if esg:
        print(json.dumps(esg, indent=2))

    print("\n2. Market Position (sonar)...")
    mp = _research_market_position(client, company_name)
    print(f"   Result: {'OK' if mp else 'FAILED'}")
    if mp:
        print(json.dumps(mp, indent=2))

    print("\n3. Strategic Profile (sonar-pro)...")
    sp = _research_strategic_profile(client, company_name)
    print(f"   Result: {'OK' if sp else 'FAILED'}")
    if sp:
        print(json.dumps(sp, indent=2))


def test_full_agent(company_name="NetJets"):
    """Test full agent_researcher pipeline."""
    print(f"\n--- Test: agent_researcher('{company_name}') ---")
    session = get_snowflake_session()
    result = agent_researcher(session, company_name)
    print(result)


if __name__ == "__main__":
    company = sys.argv[1] if len(sys.argv) > 1 else "NetJets"
    print(f"Testing Perplexity functions (company: {company})\n")

    test_client()
    test_individual_calls(company)
    test_full_agent(company)

    print("\n--- Done ---")
