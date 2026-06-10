"""
Quick test script for Agent 2: Data Contextualizer.
Run from hermes root: python -m tests.contextualizer_test <company_name>

Requires a valid Snowflake connection (env vars must be set).
"""
import json
import sys
import os

# Add hermes root so imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from snowflake_client import (
    get_snowflake_session,
    fetch_all_snowflake_data,
)
from agents.contextualizer import agent_contextualizer


def test_session():
    """Test Snowflake session creation."""
    print("--- Test: get_snowflake_session() ---")
    session = get_snowflake_session()
    print(f"Session created: {type(session).__name__}")
    return session


def test_snowflake_data(session, company_name):
    """Test fetching raw Snowflake data for the company."""
    print(f"\n--- Test: fetch_all_snowflake_data('{company_name}') ---")
    raw_data = fetch_all_snowflake_data(session, company_name)

    for key, value in raw_data.items():
        status = "OK" if value else "EMPTY/NONE"
        print(f"  {key}: {status}")
        if value:
            print(f"    {json.dumps(value, indent=4, default=str)}")

    return raw_data


def test_contextualizer(session, company_name, raw_data, researcher_output=None):
    """Test full agent_contextualizer pipeline."""
    print(f"\n--- Test: agent_contextualizer('{company_name}') ---")
    label = "WITH" if raw_data else "WITHOUT"
    print(f"  Running {label} internal data, {'WITH' if researcher_output else 'WITHOUT'} researcher output")

    result = agent_contextualizer(session, company_name, raw_data, researcher_output)
    print("\n=== AGENT 2 OUTPUT ===")
    print(result)
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Test Agent 2: Contextualizer")
    parser.add_argument("--new", type=str, help="Run as NEW account (Perplexity -> Cortex)")
    parser.add_argument("--existing", type=str, help="Run as EXISTING account (Snowflake -> Cortex)")
    
    args = parser.parse_args()

    # Default to testing "Burgess" as a new account if no args provided
    if not args.new and not args.existing:
        print("No arguments provided. Defaulting to testing NEW account: Burgess")
        args.new = "Burgess"

    print("Testing Agent 2: Contextualizer")
    
    # 1. Create Snowflake session (needed for both since Cortex is always called)
    session = test_session()

    if args.existing:
        print(f"\n============================================================")
        print(f"TESTING EXISTING ACCOUNT: {args.existing} (Snowflake -> Cortex)")
        print(f"============================================================")
        # Fetch raw data from Snowflake
        raw_data = test_snowflake_data(session, args.existing)
        # Run contextualizer with real data
        test_contextualizer(session, args.existing, raw_data)

    if args.new:
        print(f"\n============================================================")
        print(f"TESTING NEW ACCOUNT: {args.new} (Perplexity deep-research -> Cortex)")
        print(f"============================================================")
        # Run contextualizer with NO data (triggers Perplexity)
        test_contextualizer(session, args.new, None)

    print("\n--- Done ---")
