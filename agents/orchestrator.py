"""
Agent Orchestration - coordinates the 3-agent workflow.
"""
from concurrent.futures import ThreadPoolExecutor
from agents.researcher import agent_researcher
from agents.contextualizer import agent_contextualizer
from agents.strategist import agent_strategist


def run_agents_existing_account(session, company_name, snowflake_data):
    """
    Orchestrates the 3-agent workflow for EXISTING accounts.
    Flow: Agent 1 (Perplexity) + Agent 2 (Cortex) run in parallel -> Agent 3
    """
    results = {}

    with ThreadPoolExecutor(max_workers=2) as executor:
        # Agent 1 uses Perplexity API + Cortex for summary
        future_researcher = executor.submit(agent_researcher, session, company_name)
        # Agent 2 uses Snowflake data directly
        future_contextualizer = executor.submit(
            agent_contextualizer, session, company_name, snowflake_data, None
        )

        results["researcher"] = future_researcher.result()
        results["contextualizer"] = future_contextualizer.result()

    # Phase 2: Agent 3 with outputs from both
    results["strategist"] = agent_strategist(
        session, company_name,
        results["researcher"],
        results["contextualizer"],
        snowflake_data
    )

    return results


def run_agents_new_account(session, company_name):
    """
    Orchestrates the 3-agent workflow for NEW accounts.
    Flow: Agent 1 -> Agent 2 (uses Agent 1 output) -> Agent 3
    """
    results = {}

    # Phase 1: Agent 1 (Perplexity research)
    results["researcher"] = agent_researcher(session, company_name)

    # Phase 2: Agent 2 uses researcher output since no Snowflake data
    results["contextualizer"] = agent_contextualizer(
        session, company_name, None, results["researcher"]
    )

    # Phase 3: Agent 3 (Strategist)
    results["strategist"] = agent_strategist(
        session, company_name,
        results["researcher"],
        results["contextualizer"],
        None
    )

    return results
