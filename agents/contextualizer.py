"""
Agent 2: The Data Contextualizer - Snowflake Cortex powered.
Translates raw data into operational reality.

For NEW accounts: Uses Perplexity deep-research to find voyage info,
vessel types, IMO numbers, and preferred ports.
For EXISTING accounts: Uses Snowflake data + Cortex (unchanged).
"""
import json
from datetime import datetime, timedelta
from perplexity import Perplexity
from config import PERPLEXITY_API_KEY
from snowflake_client import call_cortex_complete, FIELD_DICTIONARY, TABLE_FQN
from agents.schemas import operational_profile


# ================================================================
# PERPLEXITY HELPERS (New accounts only)
# ================================================================

def _get_client():
    """Create Perplexity API client."""
    return Perplexity(api_key=PERPLEXITY_API_KEY)


def _call_perplexity(client, prompt, schema, model, search_context_size="high", max_tokens=16000):
    """
    Call Perplexity API with structured JSON output.
    Returns parsed JSON dict on success, None on failure.
    """
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format=schema,
            search_after_date_filter=(
                datetime.today() - timedelta(days=365)
            ).strftime("%m/%d/%Y"),
            web_search_options={"search_context_size": search_context_size},
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content
        if isinstance(content, str):
            clean_content = content.replace("```json", "").replace("```", "").strip()
            try:
                return json.loads(clean_content)
            except json.JSONDecodeError as e:
                print(f"Perplexity JSON parse error ({model}): {e}")
                recovered = _recover_truncated_json(clean_content)
                if recovered is not None:
                    print(f"  -> Recovered truncated JSON ({len(clean_content):,} chars)")
                    return recovered
                print(f"  -> Recovery failed. First 500 chars:\n{clean_content[:500]}")
                return None
        return content
    except Exception as e:
        print(f"Perplexity API error ({model}): {e}")
        return None


def _recover_truncated_json(text):
    """
    Attempt to salvage a truncated JSON object by walking backwards
    to find the deepest valid closing brace.
    """
    for i in range(len(text) - 1, 0, -1):
        if text[i] in ('}', ']'):
            candidate = text[:i + 1]
            opens = 0
            for ch in candidate:
                if ch in ('{', '['):
                    opens += 1
                elif ch in ('}', ']'):
                    opens -= 1
            suffix = '}' * max(opens, 0)
            try:
                return json.loads(candidate + suffix)
            except json.JSONDecodeError:
                continue
    return None


# ================================================================
# PERPLEXITY RESEARCH (New accounts only)
# ================================================================

def _research_operational_profile(client, company_name):
    """Search Perplexity for maritime operational profile data (deep-research)."""
    prompt = f"""
ROLE: Senior marine/bunker fuel business intelligence analyst specializing in commercial shipping fleet research.

TASK: Research {company_name} and produce ONLY a JSON object matching the provided schema for a maritime operational profile.

SCOPE: Search deeply for the following information about {company_name}:

1. VOYAGE INFORMATION: What kind of voyage operations does {company_name} conduct?
   - Container shipping, dry bulk, tanker, RoRo, cruise, etc.
   - Route types (coastal, deep-sea, transoceanic, regional)
   - Approximate voyage frequency or port calls
   - Operational patterns (seasonal peaks, liner services, tramp shipping)

2. VESSEL TYPES: What vessels does {company_name} operate or manage?
   - Specific vessel classes (e.g., Panamax, Suezmax, VLCC, Capesize)
   - Categories (container ship, bulk carrier, oil tanker, ferry, offshore)
   - Typical use cases for each type

3. VESSEL IMO NUMBERS: What IMO numbers are registered under {company_name}?
   - Search IMO registry, maritime databases, AIS vessel tracking sources
   - Include registration status (active, decommissioned)
   - Associate each IMO number with its vessel type if possible

4. PREFERRED PORTS: What ports does {company_name} frequently use?
   - Home base / headquarters port
   - Frequent destinations or regional hubs
   - Terminal partnerships or maintenance drydocks
   - Use UN/LOCODEs where possible

RULES:
- Lens: marine fuel and shipping services.
- Be direct and concise.
- If a specific fact is unknown, use the exact string: "Couldn't find information"
- Place any strategic interpretations in the inferred_points array.
- List any data you could not find in the missing_data array.
"""
    schema = operational_profile.get_schema(company_name)
    return _call_perplexity(client, prompt, schema, "sonar-deep-research", "high", max_tokens=16000)


def _operational_profile_to_data_context(data):
    """
    Convert Perplexity operational profile JSON into a data-context string
    for the Cortex prompt (mirrors the Snowflake data block format).
    """
    if not data:
        return "\nNo operational profile data found from research.\n"

    lines = ["\n### SOURCE: PERPLEXITY RESEARCH (Maritime Profile)"]

    # Voyage information
    voyage = data.get("voyage_information", {})
    _default = "Couldn't find information"
    lines.append(f"- Operational Overview: {voyage.get('operational_overview', _default)}")
    lines.append(f"- Route Types: {voyage.get('route_types', _default)}")
    lines.append(f"- Voyage Frequency: {voyage.get('voyage_frequency', _default)}")
    lines.append(f"- Operational Patterns: {voyage.get('operational_patterns', _default)}")

    # Vessel types
    vessels = data.get("vessel_types", [])
    if vessels:
        lines.append(f"- Vessel Types ({len(vessels)} found):")
        for v in vessels:
            lines.append(f"  * {v.get('type_name', '?')} ({v.get('category', '?')}) - {v.get('typical_use', '?')}")
    else:
        lines.append("- Vessel Types: Couldn't find information")

    # IMO numbers
    imo_numbers = data.get("vessel_imo_numbers", [])
    if imo_numbers:
        lines.append(f"- Vessel IMO Numbers ({len(imo_numbers)} found):")
        for i in imo_numbers:
            lines.append(f"  * {i.get('imo_number', '?')} - {i.get('vessel_type', '?')} ({i.get('registration_status', '?')})")
    else:
        lines.append("- Vessel IMO Numbers: Couldn't find information")

    # Preferred ports
    ports = data.get("preferred_ports", [])
    if ports:
        lines.append(f"- Preferred Ports ({len(ports)} found):")
        for p in ports:
            lines.append(f"  * {p.get('unlocode', '?')} - {p.get('port_name', '?')} ({p.get('usage_context', '?')})")
    else:
        lines.append("- Preferred Ports: Couldn't find information")

    # Notes
    notes = data.get("notes", {})
    inferred = notes.get("inferred_points", [])
    if inferred:
        lines.append("- Inferred Points:")
        for pt in inferred:
            lines.append(f"  * {pt}")
    missing = notes.get("missing_data", [])
    if missing:
        lines.append("- Missing Data:")
        for m in missing:
            lines.append(f"  * {m}")

    return "\n".join(lines)


# ================================================================
# MAIN AGENT FUNCTION
# ================================================================

def agent_contextualizer(session, company_name, raw_data, researcher_output=None):
    """
    Agent 2: The Data Enrichment & Contextualization Agent
    Role: Raw Data Interpreter
    Task: Takes raw data and applies intelligence layer to explain operational significance
    
    For Existing accounts: raw_data comes from Snowflake
    For New accounts: raw_data is searched via Perplexity (voyage info, vessels, ports)
    """
    
    # 1. Prepare the Data Block (Handling the "Existing vs New" Logic)
    if raw_data:
        # EXISTING ACCOUNT: marine sales planning metrics (Prior Year vs YTD)
        periods = raw_data.get("periods") or {"prior_year": "Prior Year", "ytd": "YTD"}
        metrics = raw_data.get("customer_metrics") or {}
        ports = raw_data.get("top5_ports") or {}

        py_label = periods.get("prior_year", "Prior Year")
        ytd_label = periods.get("ytd", "YTD")

        data_context = f"""

### SOURCE: INTERNAL MARINE SALES PLANNING DATA (HARD FACTS)
Source view: {TABLE_FQN} (grain: one row per transaction, filtered by LIFT_ETA_DATE).
Comparison periods: {py_label} vs {ytd_label}.

- Headline metrics ({py_label}): {json.dumps(metrics.get('prior_year', {}), default=str)}
- Headline metrics ({ytd_label}): {json.dumps(metrics.get('ytd', {}), default=str)}
- Top 5 ports by volume ({py_label}): {json.dumps(ports.get('prior_year', []), default=str)}
- Top 5 ports by volume ({ytd_label}): {json.dumps(ports.get('ytd', []), default=str)}
"""
    else:
        # NEW ACCOUNT: search Perplexity for operational profile
        print(f"[Contextualizer] New account detected — searching Perplexity for {company_name}...")
        client = _get_client()
        profile_data = _research_operational_profile(client, company_name)
        data_context = _operational_profile_to_data_context(profile_data)
        print(f"[Contextualizer] Perplexity research complete for {company_name}")

    # Data dictionary to teach the model the semantics of each field
    dictionary_block = "### DATA DICTIONARY (Authoritative field definitions)\n" + \
        "\n".join([f"- {k}: {v}" for k, v in FIELD_DICTIONARY.items()])


    # 2. The Core Prompt
    prompt = f"""
### ROLE
You are Agent 2: The Operational Architect. 
Your goal is to define the "Operational Reality" of the client—how they actually buy, operate, and pay: typical fleet size, voyage routes, ports of call, and operational scale.

### INPUTS
CUSTOMER_NAME: {company_name}

### INTERNAL DATA (Marine sales planning metrics for this customer — Prior Year vs YTD)
{data_context}

### FIELD DICTIONARY (Defines the meaning of each metric/dimension field name)
{dictionary_block}

### CONTEXT FROM RESEARCHER (AGENT 1)
{researcher_output or "None"}

### ANALYSIS INSTRUCTIONS
You must perform a two-step process:
STEP 1: INTERNAL REASONING (The "Think" Step)
Before filling the output template, analyze the inputs:

**IF Internal Data exists:**
1. **State the fact** — report Volume, GP, Margin, # Won, # Inquiries, and # Lost for both periods.
2. **Compare Prior Year vs YTD** — call out direction of change (growing / flat / shrinking) and note win-rate (#Won / #Inquiries).
3. **Read the port mix** — use the Top 5 ports per period to describe geographic concentration and any shifts between periods.
4. **Identify the WFS opportunity** — where can World Fuel Services grow volume, lift margin, or convert more inquiries?

CRITICAL METRIC RULE: Margin is profit per ton and is already computed as SUM(GP) / SUM(Volume). Treat it as-is. Never average or sum margin values across rows.

**IF Internal Data is MISSING (Prospect):**
Perform a "Simulated Analysis" based on your knowledge of {company_name} and the industry:
1. **Expand on Agent 1:** Agent 1 gave you the *Market* position. You must determine the *Operational* consequence.
2. **Apply Domain Knowledge:** If Agent 1 says "Global Logistics," you must infer "High bunker fuel volume, 24/7 voyage planning needs, complex port-state and tax compliance."
3. **Profile the Fleet:** Based on your internal knowledge of this company/sector, estimate their fleet complexity (Single hub vs. Global multi-stop).
4. **NO BOLD TEXT:** Do not use bold formatting in your output (no **text**).
5. **NO DOLLAR SIGNS:** Use "USD" instead of the "$" symbol (e.g., "700k USD" instead of "$700k") to avoid markdown math rendering errors.

STEP 2: FINAL OUTPUT GENERATION
Fill out the Markdown template below based on your reasoning.

### OUTPUT FORMAT (Strict Markdown)
<thinking>
Briefly summarize your logic here. E.g., {company_name} is a massive logistics player, so I am inferring high complexity despite missing data. OR Internal data shows declining volume, which contradicts their market growth.
</thinking>

## 1. Fleet & Asset Analysis
* Analyze any fleet information (vessel types, counts, capabilities)
* Infer operational complexity, range requirements, fuel needs
* Example insight: "A mix of Capesize and Panamax suggests complex operations requiring both deep-sea handling and regional logistics."

## 2. Operational Footprint Analysis
* Analyze geographic presence and voyage patterns
* Identify regulatory considerations, bunker fuel sourcing challenges
* Example insight: "Operations traversing the EU imply specific EU ETS compliance considerations."

## 3. Financial & Performance Analysis
* Summarize Volume, GP, Margin, # Won, # Inquiries, # Lost for Prior Year vs YTD
* Note the trend (expanding, flat, contracting) and compute win-rate (# Won / # Inquiries) for each period
* Comment on port concentration using the Top 5 ports list (any shifts between Prior Year and YTD?)
* Flag any noteworthy gaps (e.g., YTD pacing far below Prior Year)

## 4.Technology & Integration Analysis
* Assess their tech stack and WFS integration depth
* Identify opportunities for deeper embedding

## 5.Risk & Opportunity Summary
* Summarize key risks (operational, regulatory, competitive concentration)
* Highlight top 3 opportunities for WFS engagement (port expansion, margin lift, inquiry-to-win conversion)

Be specific and actionable. Connect data points to real business implications.

"""
    return call_cortex_complete(session, prompt)
