"""
Agent 3: The Sales Strategist - Snowflake Cortex powered.
Synthesizes Agent 1 + Agent 2 into Salesforce CRM fields.
"""
import json
from snowflake_client import call_cortex_complete, FIELD_DICTIONARY, TABLE_FQN


def agent_strategist(session, company_name, researcher_output, contextualizer_output, raw_data=None):
    """
    Agent 3: The Sales Strategist (Salesforce Prep)
    Role: Synthesize insights into specific Salesforce field inputs.
    """
    
    # 1. Prepare Internal Data Context
    
    if raw_data:
        periods = raw_data.get("periods") or {"prior_year": "Prior Year", "ytd": "YTD"}
        metrics = raw_data.get("customer_metrics") or {}
        ports = raw_data.get("top5_ports") or {}

        py_label = periods.get("prior_year", "Prior Year")
        ytd_label = periods.get("ytd", "YTD")

        py_m = metrics.get("prior_year", {})
        ytd_m = metrics.get("ytd", {})

        internal_data_summary = f"""
INTERNAL DATA SUMMARY (Marine sales planning — {TABLE_FQN}):

Headline metrics — {py_label}:
- Volume (tons): {py_m.get('VOLUME')}
- GP (USD): {py_m.get('GP')}
- Margin (USD/ton, GP/Volume): {py_m.get('MARGIN')}
- # Won: {py_m.get('NUM_WON')}
- # Inquiries: {py_m.get('NUM_INQUIRIES')}
- # Lost: {py_m.get('NUM_LOST')}

Headline metrics — {ytd_label}:
- Volume (tons): {ytd_m.get('VOLUME')}
- GP (USD): {ytd_m.get('GP')}
- Margin (USD/ton, GP/Volume): {ytd_m.get('MARGIN')}
- # Won: {ytd_m.get('NUM_WON')}
- # Inquiries: {ytd_m.get('NUM_INQUIRIES')}
- # Lost: {ytd_m.get('NUM_LOST')}

Top 5 ports by volume — {py_label}: {json.dumps(ports.get('prior_year', []), default=str)}
Top 5 ports by volume — {ytd_label}: {json.dumps(ports.get('ytd', []), default=str)}
"""
    else:
        internal_data_summary = "INTERNAL DATA: None (Prospect). State 'None - Prospect' for Services."

    dictionary_block = "### DATA DICTIONARY (Authoritative field definitions)\n" + \
        "\n".join([f"- {k}: {v}" for k, v in FIELD_DICTIONARY.items()])


    # 2. The Core Prompt
    prompt = f"""
### ROLE
You are Agent 3: The Salesforce Data Strategist.
Your task is to populate specific CRM fields based on the research provided by Agent 1 and Agent 2.

### INPUTS
CUSTOMER_NAME: {company_name}

{internal_data_summary}
{dictionary_block}
---
### REPORT FROM AGENT 1 (Market Research)
{researcher_output or "None"}
---
### REPORT FROM AGENT 2 (Operational Context)
{contextualizer_output or "None"}
---
### INSTRUCTIONS
You must populate the fields below EXACTLY as requested.
1. **Consistency:** Do not invent data. If a specific metric (like a carbon date) is missing in the inputs, state "Not publicly available in current data."
2. **Strategy:** For 'Strategic Plan' and 'Relationship Growth', combine Agent 1's market view with Agent 2's operational view to create a logical sales approach.
3. **Services:** Only list services if they appear in 'INTERNAL DATA'. If none, write "Prospect - No current services."
4. **NO BOLD TEXT:** Do not use bold formatting in your output (no **text**).
5. **NO DOLLAR SIGNS:** Use "USD" instead of the "$" symbol (e.g., "700k USD" instead of "$700k") to avoid markdown math rendering errors.

### OUTPUT FORMAT
(You must use these exact headers. Do not use bolding or markdown inside the content, just plain text.)

<thinking>
[Briefly outline the logic: Connect Agent 1's "Market Challenge" to Agent 2's "Operational Hook" to define the "Solution".]
</thinking>

### Executive Summary
1.Account Business Overview: [2 sentences on who they are and their primary business]
2.Our Overall Strategic Plan: [Infer a strategy: e.g., "Leverage their expansion in Asia to pitch our global supply network."]
3.Relationship Growth – Phase 1: [Specific first step: e.g., "Secure meeting with Fleet Procurement to discuss EU ETS compliance."]

### Account Research – Who Are They?
1.What do they do (Industry Code): [Sector / Primary Industry]
2.Where And What?: [Geographic scope and primary fleet/asset type]
3.What marketplace(s) do they serve & why?: [End-customer profile: e.g., "Serves dry-bulk charterers and commodity shippers requiring reliable global bunker supply."]
4.What Services do they buy now?: [Summarize from INTERNAL DATA. If Prospect, state "None".]
5.Who do they compete against & how?: [List competitors from Agent 1]

### Carbon Reduction Goals
1.Published sustainability report?: [Yes/No + Year (Look at Agent 1 ESG section)]
2.Comments: [Key takeaways: e.g., "Focus on offsets rather than SAF."]
3.Defined carbon reduction target?: [Yes/No]
4.Comments: [Specific goals: e.g., "Net Zero by 2050."]

### Business Drivers / KPI’s
1.Identify Key Performance Indicators: [Infer financial/operational KPIs: e.g., "Cost per Tonne-Mile, Vessel Utilization, EEOI / Carbon Intensity Indicator (CII)."]
2.Which KPIs can we solution to?: [Connect our product to their KPI: e.g., "Fuel Efficiency -> Our Voyage Optimization Platform."]

### Customer Challenges / Needs 1
1.Challenge/Need: [The #1 Pain Point identified by Agent 2]
2.Solution: [The specific Fuel/Service offering that solves it]
"""

    return call_cortex_complete(session, prompt)
