"""
Agent 1: The Strategic Researcher - Perplexity API powered.
Replaces the original Cortex-based internal knowledge agent.

Runs 3 sequential Perplexity searches:
  1. Sustainability and ESG (deep research - runs first)
  2. Market Position
  3. Strategic Profile

Returns combined markdown matching the original Agent 1 output template.
"""
import json
from datetime import datetime, timedelta
from perplexity import Perplexity
from config import PERPLEXITY_API_KEY
from agents.schemas import market_position, strategic_profile, sustainability_esg, latest_news_partnerships
from snowflake_client import call_cortex_complete


def _get_client():
    """Create Perplexity API client."""
    return Perplexity(api_key=PERPLEXITY_API_KEY)


def _call_perplexity(client, prompt, schema, model, search_context_size="medium", max_tokens=6000):
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
            # Clean up potential markdown JSON block fences
            clean_content = content.replace("```json", "").replace("```", "").strip()
            try:
                return json.loads(clean_content)
            except json.JSONDecodeError as e:
                print(f"Perplexity JSON parse error ({model}): {e}")
                # Attempt truncated JSON recovery
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
    to find the deepest valid closing brace, then adding enough
    closing braces/brackets to make it parseable.
    """
    # Try progressively shorter prefixes ending at a } or ]
    for i in range(len(text) - 1, 0, -1):
        if text[i] in ('}', ']'):
            candidate = text[:i + 1]
            # Count unmatched openers
            opens = 0
            for ch in candidate:
                if ch in ('{', '['):
                    opens += 1
                elif ch in ('}', ']'):
                    opens -= 1
            # Add closing braces to balance
            suffix = '}' * max(opens, 0)
            try:
                return json.loads(candidate + suffix)
            except json.JSONDecodeError:
                continue
    return None


# ================================================================
# INDIVIDUAL RESEARCH FUNCTIONS
# ================================================================

def _research_sustainability_esg(client, company_name):
    """Call 1: Deep ESG research (sonar-deep-research)."""
    prompt = f"""
ROLE: Senior marine/bunker fuel BI analyst for commercial shipping.

TASK: Research {company_name} and produce ONLY a JSON object matching the provided schema for section "3. Sustainability and ESG".

SCOPE: Go deep - search sustainability reports, corporate filings, news articles, and executive public statements.
Carbon/Sustainability Goals: Any known public commitments to sustainability,
  carbon neutrality, or ESG initiatives? Include mentions of Carbon Credits and EU ETS.
- Marine Biofuels/Alternative Fuels Adoption: Any specific mention of alternative marine fuels (biofuels, LNG, methanol, ammonia) usage,
  pilot programs, or carbon offsets? If no shipping-specific data exists,
  highlight corporate sustainability data to support a repositioning conversation.
- Corporate-Wide Initiatives: Beyond shipping, identify broad company actions
  such as general going green campaigns, or corporate-wide carbon
  reduction targets. Note: Corporate goals eventually trickle down to the
  fleet operations department.
- Executive Public Stance: Has the CEO or leadership spoken publicly in TED
  talks, interviews, or articles regarding environmental impact or sustainability? Include any references to IMO 2020/2030, CII, EEXI, EU ETS, or Carbon Credits.
- Strategic Opportunity: Based on the above, identify potential alignment gaps
  where maritime solutions such as offsets, EU ETS compliance, or fuel efficiency tools could help the
  fleet operations department match the corporate sustainability mandate.

RULES:
- Lens: marine fuels and shipping services.
- Be direct and concise.
- If a fact is unknown, use the exact string: Data not available.
- Prefix interpretations with: Inferred:
"""
    schema = sustainability_esg.get_schema(company_name)
    return _call_perplexity(client, prompt, schema, "sonar-deep-research", "high", max_tokens=16000)


def _research_market_position(client, company_name):
    """Call 2: Market position research (sonar)."""
    prompt = f"""
You are a senior strategic profile analyst.
Provide decision-ready competitive intelligence to sales teams.

RULES:
- Be direct, concise, and professional. No filler.
- Do not use prefixes like "Inferred:" or "Unknown:" in the main data fields.
- If a specific fact is unknown, add it to the "unknowns" array in the notes object.
- If you make a strategic interpretation or deduction, add it to the "inferred_points" array in the notes object.
- Return ONLY a JSON object that conforms to the provided schema. Do not output markdown, code fences, bold text, or citations.

TASK:
Research {company_name} and provide a structured strategic market position report.
Focus only on publicly available, verifiable information to populate the following areas:

1. Corporate Identity: Find or deduce the company official Vision and Mission statements.
2. Industry Classification: Identify their specific sector and primary business activity.
3. Market Position: Define their strategic role (e.g., Market Leader, Challenger) and approximate standing/market share.
4. Competitive Landscape: Identify their main competitors and their unique differentiation or value proposition.
"""
    schema = market_position.get_schema(company_name)
    return _call_perplexity(client, prompt, schema, "sonar", "medium")


def _research_strategic_profile(client, company_name):
    """Call 3: Strategic profile research (sonar-pro)."""
    prompt = f"""
ROLE:
You are a senior strategic profile analyst.
Provide decision-ready competitive intelligence to marine fuel sales teams.

RULES:
- Be direct, concise, and professional. No filler.
- If a specific string or financial figure is unknown, use the exact string: "Data not available".
- Place any strategic interpretations, deductions, or educated guesses inside the inferred_points array.
- List any specific metrics or fields you could not find during your research inside the missing_data array.
- Return ONLY a valid JSON object that conforms to the provided schema. Do not output markdown, code fences, bold text, conversational filler, or citations.

TASK:
Research {company_name} and provide a structured strategic profile report. Focus only on publicly available, verifiable information. Ensure your research covers the following areas to populate the JSON schema:

Business Model: What are their key value propositions? What are their key products/services? Who is their primary target audience?
Financials: How do they generate revenue (revenue model)? What is their growth status (expanding, stable, or contracting) and the rationale behind it? Provide specific revenue figures or growth percentages for the previous year, current year, and projected next year.
Market Presence: What specific regions, countries, or markets do they operate in? Who are their primary competitors?
"""
    schema = strategic_profile.get_schema(company_name)
    return _call_perplexity(client, prompt, schema, "sonar-pro", "high")


def _research_latest_news_partnerships(client, company_name):
    """Call 4: Latest news and partnerships research (sonar-reasoning for deep thinking)."""
    prompt = f"""
ROLE: Senior maritime business intelligence analyst.

TASK: Research {company_name} and produce ONLY a JSON object matching the provided schema for section "4. Latest News and Partnerships".

SCOPE: Focus on the most recent news, press releases, and strategic partnerships announced by {company_name} over the last 12-18 months.
- Recent News: Major corporate announcements, product launches, leadership changes, or market expansion.
- Strategic Partnerships: Mergers, acquisitions, joint ventures, or key vendor/supplier partnerships. Focus on maritime-related partnerships if applicable.

RULES:
- Be direct and concise.
- If a fact is unknown, use the exact string: Data not available.
- Return ONLY a valid JSON object that conforms to the provided schema. Do not output markdown, code fences, or citations.
"""
    schema = latest_news_partnerships.get_schema(company_name)
    return _call_perplexity(client, prompt, schema, "sonar-reasoning-pro", "high")


# ================================================================
# MARKDOWN CONVERSION
# ================================================================

def _market_position_to_md(data):
    if not data:
        return "Data not available for this section."

    lines = []
    lines.append("## 1. Market Position & Industry Context")

    ic = data.get("industry_classification", {})
    lines.append("* Industry Classification: " + ic.get("sector", "Data not available") +
                 ". Primary business: " + ic.get("primary_business", "Data not available"))

    mp = data.get("market_position", {})
    lines.append("* Market Position: " + mp.get("role", "Data not available") +
                 ". Standing: " + mp.get("approximate_standing", "Data not available"))

    cl = data.get("competitive_landscape", {})
    comps = cl.get("main_competitors", [])
    comp_str = ", ".join(comps) if comps else "Data not available"
    lines.append("* Competitive Landscape: Main competitors: " + comp_str +
                 ". How they differentiate: " + cl.get("differentiation", "Data not available"))

    vision = data.get("vision", {}).get("statement", "")
    mission = data.get("mission", {}).get("statement", "")
    if vision:
        lines.append("* Vision: " + vision)
    if mission:
        lines.append("* Mission: " + mission)

    notes = data.get("notes", {})
    for pt in notes.get("inferred_points", []):
        lines.append("* Inferred: " + pt)
    for unk in notes.get("unknowns", []):
        lines.append("* Data not available: " + unk)

    return "\n".join(lines)


def _strategic_profile_to_md(data):
    if not data:
        return "Data not available for this section."

    lines = []
    lines.append("## 2. Strategic Profile")
    sp = data.get("strategic_profile", {})

    bm = sp.get("business_model", {})
    vps = bm.get("value_propositions", [])
    lines.append("* Business Model: " + ("; ".join(vps) if vps else "Data not available"))
    prods = bm.get("key_products", [])
    lines.append("* Key Products/Services: " + (", ".join(prods) if prods else "Data not available"))
    lines.append("* Target Audience: " + bm.get("target_audience", "Data not available"))

    fin = sp.get("financials", {})
    lines.append("* Growth Trajectory: " + fin.get("growth_status", "Data not available") + " - " + fin.get("growth_rationale", ""))
    lines.append("* Revenue Model: " + fin.get("revenue_model", "Data not available"))

    mp_data = sp.get("market_presence", {})
    regions = mp_data.get("regions", [])
    lines.append("* Geographic Footprint: " + (", ".join(regions) if regions else "Data not available"))

    notes = data.get("notes", {})
    for pt in notes.get("inferred_points", []):
        lines.append("* Inferred: " + pt)

    return "\n".join(lines)


def _sustainability_esg_to_md(data):
    if not data:
        return "Data not available for this section."

    lines = []
    lines.append("## 3. Sustainability & ESG")
    esg = data.get("sustainability_esg", {})

    carbon = esg.get("carbon_and_sustainability_goals", {})
    commitments = carbon.get("commitments", [])
    if commitments:
        lines.append("* Carbon/Sustainability Goals:")
        for c in commitments:
            lines.append("  - " + str(c))

    sbt = carbon.get("science_based_targets", {})
    if sbt:
        lines.append("* SBTi Status: " + sbt.get("sbti_status", "Data not available"))

    saf = esg.get("saf_strategy_and_context", {})
    if saf:
        adoption = saf.get("current_adoption_status", "")
        if adoption:
            lines.append("* SAF Adoption: " + str(adoption))

    corp = esg.get("corporate_initiatives_and_spillover", {})
    if corp:
        initiatives = corp.get("broad_initiatives", [])
        if initiatives:
            lines.append("* Corporate-Wide Initiatives:")
            for init_item in initiatives[:5]:
                lines.append("  - " + str(init_item))

    exec_gov = esg.get("executive_leadership_and_governance", {})
    if exec_gov:
        stmts = exec_gov.get("public_statements", [])
        if stmts:
            lines.append("* Executive Public Stance:")
            for s in stmts[:3]:
                lines.append("  - " + str(s))

    opps = esg.get("maritime_strategic_opportunities", {})
    if opps:
        gaps = opps.get("alignment_gaps", [])
        if gaps:
            lines.append("* Strategic Opportunity:")
            for g in gaps[:5]:
                lines.append("  - " + str(g))

    return "\n".join(lines)


def _latest_news_partnerships_to_md(data):
    if not data:
        return "Data not available for this section."

    lines = []
    lines.append("## 4. Latest News & Partnerships")
    
    news = data.get("recent_news", [])
    if news:
        lines.append("* Recent News:")
        for item in news[:5]:
            lines.append(f"  - {item.get('date', 'Unknown Date')}: {item.get('title', 'No Title')} - {item.get('summary', '')}")
            
    partnerships = data.get("strategic_partnerships", [])
    if partnerships:
        lines.append("* Strategic Partnerships:")
        for item in partnerships[:5]:
            lines.append(f"  - {item.get('partner_name', 'Unknown Partner')} ({item.get('date', 'Unknown Date')}): {item.get('purpose', '')} - {item.get('impact', '')}")
            
    notes = data.get("notes", {})
    for pt in notes.get("inferred_points", []):
        lines.append("* Inferred: " + pt)
    for unk in notes.get("unknowns", []):
        lines.append("* Data not available: " + unk)
        
    return "\n".join(lines)


# ================================================================
# CORTEX SUMMARISATION
# ================================================================

def _summarize_with_cortex(session, company_name, market_md, profile_md, esg_md, news_md):
    """
    Send the four research sections to Snowflake Cortex and return
    a concise executive summary that synthesises the key findings.
    """
    prompt = f"""
ROLE
You are a senior maritime fuel and services business intelligence analyst. Your output is a single comprehensive, decision-ready written report for commercial shipping marine fuel sales strategy.

CRITICAL CONSTRAINT
Use ONLY the information contained in the provided INPUTS below. Do NOT infer or use any internal model knowledge or prior memory outside these inputs. If a fact is not present in the inputs, treat it as unknown.

VARIABLE CONTEXT
Company: {company_name}

INPUTS
### Section 1 – Market Position
{market_md}

### Section 2 – Strategic Profile
{profile_md}

### Section 3 – Sustainability & ESG
{esg_md}

### Section 4 – Latest News & Partnerships
{news_md}

OBJECTIVE
Synthesize ALL four inputs into one cohesive, professional report about {company_name}. The report must be concise, analytical, and directly useful to marine fuel and services go-to-market teams.

OUTPUT FORMAT (NO JSON)
Write a structured narrative with the following sections and guidance:

1) Corporate Identity
   - State {company_name}’s vision and mission if present in the inputs. If not present, omit claims and list them later under “Unknowns.”
   - Describe {company_name}’s overall strategic posture.
   - Identify industry classification and primary business activity.

2) Market Position
   - Define {company_name}’s competitive role (leader, challenger, niche, disruptor, etc.).
   - Summarize market presence and geographic footprint.
   - Name core competitors and briefly state their differentiation versus {company_name}.

3) Business Model & Financial Profile
   - Key value propositions, core products/services, primary target customers.
   - Revenue model and monetization approach.
   - Growth status (expanding, stable, contracting) and brief rationale if present.
   - Any concrete financial results or guidance present in the inputs (state period and currency if given). If absent, do not speculate—list later under “Unknowns.”

4
) Sustainability & ESG (Maritime Lens)

   ### 4A. Corporate Environmental Commitments  
   - Summaries of environmental goals, net-zero targets, Scope 1/2/3 plans, EU ETS implications, carbon credit purchases, and any frameworks mentioned.

   ### 4B. Alternative Fuels & Marine Biofuels  
   - Any alternative fuel commitments, pilots, usage, carbon offset projects, or partnerships present in the inputs.

   ### 4C. Broader Sustainability Initiatives  
   - Any operational efficiency programs, vessel optimization, renewable energy actions, facility decarbonization, fleet initiatives, etc.

   ### 4D. Executive Leadership & Governance  
   - Summarize governance structures exactly as stated (e.g., CSO name, reporting line, committees).  
   - Present governance details in **short narrative paragraphs** — no bullet lists unless explicitly stated in inputs.

   ### 4E. Executive Statements (Bullet‑Style Paraphrased Summaries)  
   - Convert every executive statement provided in the inputs into a concise bullet‑style paraphrased paragraph.  
   - Each bullet must include:  
     • Speaker name  
     • Role  
     • Venue/medium  
     • Date  
     • Paraphrased stance summary (NO quotes)  
   - Tone must remain neutral, reflecting only the content provided.  
   - No additional interpretation.


5) Latest News & Strategic Partnerships
   - Summarize the most impactful recent news and corporate announcements from the inputs.
   - Detail any strategic partnerships, particularly those relevant to operations, expansion, or maritime alignment.
   - Highlight how these developments might influence {company_name}'s future trajectory.


6) Strategic Opportunities for Marine Fuel & Services
   - Identify specific, actionable opportunities where maritime decarbonization solutions (e.g., alternative fuels, EU ETS compliance, verified carbon credits/offsets, emissions tracking/analytics/MRV, vessel operational efficiency tools, route/fuel optimization) could help {company_name}’s fleet operations align with corporate mandates and ESG goals.
   - Keep this section concrete and grounded in the provided inputs.


7) Inferred Points (inferred from Perplexity inputs)
   - If you make any logical deductions from patterns in the inputs (e.g., likely need for carbon credits or compliance tools due to dispersed ops, potential bunker supplier alignment given port locations mentioned), summarize them here.
   - Clearly label this section exactly as “Inferred Points (inferred from Perplexity inputs)”.

MERGING & QUALITY RULES
- Use ONLY the four INPUTS above; do NOT use internal knowledge or external data.
- Merge facts across inputs; deduplicate repeated items and remove contradictions.
- If inputs conflict, choose the most specific, direct, and recent statement; mention the discarded viewpoint succinctly in the “Inferred Points” section only if it informs a useful interpretation.
- Do NOT present inferences as facts in the main narrative; keep them strictly in the “Inferred Points” section.
- If a detail isn’t in the inputs, treat it as unknown and list it under “Unknowns.”
- Maintain a professional, concise, and direct tone. No filler, no marketing jargon, no speculation.
- No citations or URLs in the output; this is a narrative synthesis of the provided inputs only.
- Write clearly for senior commercial and yield management stakeholders in marine fuel.

DELIVERABLE
Produce only the final report for {company_name} following the section structure above. No JSON, no tables, no bullet-wall dumps; use crisp paragraphs with subheadings.
``
"""
    try:
        return call_cortex_complete(session, prompt)
    except Exception as e:
        print(f"Cortex summary error: {e}")
        return None


# ================================================================
# PUBLIC API
# ================================================================

def agent_researcher(session, company_name):
    """
    Agent 1: Run 4 Perplexity calls sequentially, then synthesise
    the combined output via Snowflake Cortex.
    Returns only the Cortex summary + confidence check.
    """
    client = _get_client()

    esg_data = _research_sustainability_esg(client, company_name)
    market_data = _research_market_position(client, company_name)
    profile_data = _research_strategic_profile(client, company_name)
    news_data = _research_latest_news_partnerships(client, company_name)

    # Convert each JSON result to markdown (used as Cortex input only)
    market_md = _market_position_to_md(market_data)
    profile_md = _strategic_profile_to_md(profile_data)
    esg_md = _sustainability_esg_to_md(esg_data)
    news_md = _latest_news_partnerships_to_md(news_data)

    # Build the Cortex summary
    summary = _summarize_with_cortex(session, company_name, market_md, profile_md, esg_md, news_md)

    sections = []
    sections.append(summary or "Summary not available.")
    sections.append("")
    sections.append("## Confidence Check")
    sections.append("* Data Freshness: Perplexity research conducted on " + datetime.today().strftime("%Y-%m-%d"))

    available = sum(1 for d in [market_data, profile_data, esg_data, news_data] if d is not None)
    conf = {0: "Low", 1: "Low", 2: "Low", 3: "Medium", 4: "High"}.get(available, "Low")
    sections.append("* Confidence Score: " + conf + " (" + str(available) + "/4 research calls succeeded)")

    return "\n".join(sections)
