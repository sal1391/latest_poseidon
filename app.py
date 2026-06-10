"""
Poseidon - Strategic Maritime Intelligence Dashboard
Main Streamlit Application
"""
import os
import streamlit as st
import pandas as pd
import base64
import time

from config import AUTH0_ENABLED
from auth import check_auth
from snowflake_client import (
    get_snowflake_session,
    get_all_company_names,
    fetch_all_snowflake_data,
    TABLE_FQN,
)
from agents.researcher import agent_researcher
from agents.contextualizer import agent_contextualizer
from agents.strategist import agent_strategist
from pdf_generator import generate_pdf


st.set_page_config(layout="wide", page_title="Poseidon")

# =========================================================================
# AUTH0 GATE
# =========================================================================
authenticated, user_name, roles = check_auth()

# =========================================================================
# SNOWFLAKE SESSION
# =========================================================================
try:
    session = get_snowflake_session()
except Exception as e:
    st.error(f"Failed to connect to Snowflake: {e}")
    st.info("Check your credentials in config.py (DEPLOY_MODE=local) or AWS Secrets Manager (DEPLOY_MODE=aws).")
    st.stop()


# Header
_trident_b64 = base64.b64encode(
    open(os.path.join(os.path.dirname(__file__), "assets", "trident.png"), "rb").read()
).decode()
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
.royal-title {{
  font-family: 'Inter', sans-serif;
  font-size: 52px;
  font-weight: 800;
  margin: 0;
  display: flex;
  align-items: flex-end;
  gap: 6px;
  letter-spacing: -1px;
  line-height: 1;
}}
.royal-symbol {{
  height: 68px;
  width: auto;
  display: block;
  filter: drop-shadow(0 2px 4px rgba(65,105,225,0.3));
}}
.hero-subtitle {{
  font-size: 16px;
  font-weight: 600;
  margin: 2px 0 6px 0;
  background: linear-gradient(90deg, #4169E1, #7B68EE);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  letter-spacing: 2px;
  text-transform: uppercase;
}}
.hero-pipeline {{
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0 4px 0;
  font-size: 13px;
  color: #888;
  font-family: 'Inter', sans-serif;
}}
.hero-pipeline .step {{
  background: #f0f2f6;
  padding: 4px 12px;
  border-radius: 20px;
  font-weight: 600;
  color: #444;
}}
.hero-pipeline .arrow {{ color: #bbb; font-size: 14px; }}
.hero-powered {{
  font-size: 11px;
  color: #aaa;
  margin: 6px 0 0 0;
  letter-spacing: 0.5px;
}}
.section-header {{
  background: linear-gradient(90deg, #4169E1 0%, #6495ED 100%);
  color: white;
  padding: 10px 15px;
  border-radius: 5px;
  margin: 20px 0 10px 0;
  font-size: 18px;
  font-weight: 600;
}}
.agent-card {{
  background: #f8f9fa;
  border-left: 4px solid #4169E1;
  padding: 15px;
  margin: 10px 0;
  border-radius: 0 5px 5px 0;
}}
</style>

<div class="royal-title">
  Poseidon <img class="royal-symbol" src="data:image/png;base64,{_trident_b64}" alt="trident">
</div>
<div class="hero-subtitle">Marine Fuel Sales Intelligence</div>
<div class="hero-pipeline">
  <span class="step">� Contextualizer</span>
  <span class="arrow">→</span>
  <span class="step">🔍 Researcher</span>
  <span class="arrow">→</span>
  <span class="step">🎯 Strategist</span>
</div>
<div class="hero-powered">Powered by Perplexity &middot; Claude Sonnet 4.5 &middot; Snowflake Cortex &middot; Streamlit</div>
""", unsafe_allow_html=True)

# Create Tab
tabs = st.tabs(["**Customer Insight**"])

with tabs[0]:
    st.write("Welcome to Customer Insight!")
    
    # Account Type Selection
    account_type_list = ["Select Account Type", "Existing", "New"]
    account_lookup = st.selectbox("Account Type", account_type_list, key="customer_account_type")
    

# =========================================================================
# EXISTING ACCOUNT WORKFLOW (Company-name driven)
# =========================================================================
    if account_lookup == "Existing":
        st.markdown("Select the company below.")
    
        with st.spinner("Loading companies..."):
            company_options = get_all_company_names(session)
    
        if not company_options:
            st.warning("No companies found in the database.")
        else:
            company_name = st.selectbox("Company Name", ["Select Company"] + company_options, index=0)
    
            if company_name == "Select Company":
                st.info("👆 Pick a company to begin.")
            else:
                try:
                    with st.spinner("📊 Fetching marine sales planning data from Snowflake..."):
                        snowflake_data = fetch_all_snowflake_data(session, company_name)

                    periods = snowflake_data.get('periods') or {}
                    py_label = periods.get('prior_year', 'Prior Year')
                    ytd_label = periods.get('ytd', 'YTD')
                    metrics = snowflake_data.get('customer_metrics') or {}
                    ports = snowflake_data.get('top5_ports') or {}
                    py_m = metrics.get('prior_year') or {}
                    ytd_m = metrics.get('ytd') or {}

                    st.markdown('<div class="section-header">📈 Key Metrics</div>', unsafe_allow_html=True)
                    st.caption(f"Comparing {py_label} vs {ytd_label} — source: {TABLE_FQN}")

                    def _fmt_int(v):
                        try:
                            return f"{float(v or 0):,.0f}"
                        except (TypeError, ValueError):
                            return "0"

                    def _fmt_money(v):
                        try:
                            return f"${float(v or 0):,.0f}"
                        except (TypeError, ValueError):
                            return "$0"

                    def _fmt_margin(v):
                        try:
                            return f"${float(v or 0):,.2f}/ton"
                        except (TypeError, ValueError):
                            return "$0.00/ton"

                    def _win_rate(m):
                        inq = float(m.get('NUM_INQUIRIES') or 0)
                        won = float(m.get('NUM_WON') or 0)
                        return f"{(won / inq * 100):.1f}%" if inq else "N/A"

                    def _render_period_metrics(label, m):
                        st.markdown(f"#### {label}")
                        c1, c2, c3, c4 = st.columns(4)
                        with c1:
                            st.metric("Volume (tons)", _fmt_int(m.get('VOLUME')))
                        with c2:
                            st.metric("Gross Profit", _fmt_money(m.get('GP')))
                        with c3:
                            st.metric("Margin (GP/Ton)", _fmt_margin(m.get('MARGIN')))
                        with c4:
                            st.metric("Win Rate", _win_rate(m))
                        c5, c6, c7, c8 = st.columns(4)
                        with c5:
                            st.metric("# Won", _fmt_int(m.get('NUM_WON')))
                        with c6:
                            st.metric("# Inquiries", _fmt_int(m.get('NUM_INQUIRIES')))
                        with c7:
                            st.metric("# Lost", _fmt_int(m.get('NUM_LOST')))
                        with c8:
                            st.empty()

                    _render_period_metrics(py_label, py_m)
                    _render_period_metrics(ytd_label, ytd_m)

                    def _render_ports(label, rows):
                        st.markdown(f"#### Top 5 Ports by Volume — {label}")
                        if not rows or not isinstance(rows, list):
                            st.info("No port data available for this period.")
                            return
                        df_p = pd.DataFrame(rows)
                        display_cols = {
                            'PORT': 'Port',
                            'VOLUME': 'Volume (tons)',
                            'GP': 'GP (USD)',
                            'MARGIN': 'Margin (USD/ton)',
                            'NUM_WON': '# Won',
                            'NUM_INQUIRIES': '# Inquiries',
                            'NUM_LOST': '# Lost',
                        }
                        df_p = df_p[[c for c in display_cols if c in df_p.columns]].rename(columns=display_cols)
                        for col in ('Volume (tons)', 'GP (USD)', '# Won', '# Inquiries', '# Lost'):
                            if col in df_p.columns:
                                df_p[col] = df_p[col].apply(lambda x: f"{float(x):,.0f}" if pd.notna(x) else x)
                        if 'Margin (USD/ton)' in df_p.columns:
                            df_p['Margin (USD/ton)'] = df_p['Margin (USD/ton)'].apply(
                                lambda x: f"{float(x):,.2f}" if pd.notna(x) else x
                            )
                        df_p.index = range(1, len(df_p) + 1)
                        st.dataframe(df_p, width='stretch')

                    _render_ports(py_label, ports.get('prior_year'))
                    _render_ports(ytd_label, ports.get('ytd'))
    
                    agent_results = {}

                    # --- Agent 1: Contextualizer (runs first, fastest) ---
                    with st.spinner("🧠 Agent 1: Analyzing operational data..."):
                        agent_results['contextualizer'] = agent_contextualizer(
                            session, company_name, snowflake_data, None
                        )

                    st.markdown('<div class="section-header">🧠 Agent 1: Data Enrichment</div>', unsafe_allow_html=True)
                    with st.expander("View Operational Analysis & Insights", expanded=True):
                        st.markdown(agent_results['contextualizer'])

                    # --- Agent 2: Researcher (runs second, slower) ---
                    with st.spinner("🔍 Agent 2: Perplexity Powered - Researching market position..."):
                        agent_results['researcher'] = agent_researcher(session, company_name)

                    st.markdown('<div class="section-header">🔍 Agent 2: Market Research</div>', unsafe_allow_html=True)
                    with st.expander("View Market Position & Industry Context", expanded=True):
                        st.markdown(agent_results['researcher'])

                    # --- Agent 3: Strategist (runs last, needs both outputs) ---
                    with st.spinner("🎯 Agent 3: Generating sales strategy..."):
                        agent_results['strategist'] = agent_strategist(
                            session, company_name,
                            agent_results['researcher'],
                            agent_results['contextualizer'],
                            snowflake_data
                        )

                    st.markdown('<div class="section-header">🎯 Agent 3: Salesforce Pre-Call Summary</div>', unsafe_allow_html=True)
                    st.markdown(
                        f"""<div style="background:#fff; padding:20px; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.08); border-left: 4px solid #4169E1;">
                                {agent_results['strategist']}
                            </div>""",
                        unsafe_allow_html=True
                    )
    
                    st.markdown("---")
                    pdf_bytes = generate_pdf(company_name, agent_results, snowflake_data)
                    if pdf_bytes:
                        b64_pdf = base64.b64encode(pdf_bytes).decode()
                        pdf_filename = f"{company_name.replace(' ', '_')}_salesforce_brief.pdf"
                        st.markdown(
                            f'<a href="data:application/pdf;base64,{b64_pdf}" '
                            f'download="{pdf_filename}" '
                            f'style="display:inline-block;padding:10px 24px;background:#4169E1;'
                            f'color:#fff;border-radius:6px;text-decoration:none;font-weight:600;'
                            f'font-size:14px;">📄 Download Salesforce Brief (PDF)</a>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.warning("PDF generation failed. Downloading as Markdown instead.")
                        md_sections = [
                            f"# Summarization of {company_name} by Poseidon\n",
                            "## Agent 1 – Data Enrichment\n",
                            agent_results.get('contextualizer', ''),
                            "\n## Agent 2 – Market Research\n",
                            agent_results.get('researcher', ''),
                            "\n## Agent 3 – Salesforce Strategy\n",
                            agent_results.get('strategist', ''),
                        ]
                        b64_md = base64.b64encode("\n".join(md_sections).encode()).decode()
                        md_filename = f"{company_name.replace(' ', '_')}_salesforce_brief.md"
                        st.markdown(
                            f'<a href="data:text/markdown;base64,{b64_md}" '
                            f'download="{md_filename}" '
                            f'style="display:inline-block;padding:10px 24px;background:#e67e22;'
                            f'color:#fff;border-radius:6px;text-decoration:none;font-weight:600;'
                            f'font-size:14px;">📄 Download Salesforce Brief (Markdown)</a>',
                            unsafe_allow_html=True,
                        )
                except Exception as e:
                    st.error(f"An error occurred: {e}")

    
    # =========================================================================
    # NEW ACCOUNT WORKFLOW
    # =========================================================================
    elif account_lookup == "New":
        company_name = st.text_input("Account Name:")
        
        if company_name == "":
            st.warning("Please type an Account name.")
        else:
            try:
                agent_results = {}

                # --- Agent 1: Contextualizer (runs first - prospect mode) ---
                with st.spinner("🧠 Agent 1: Analyzing operational context..."):
                    agent_results['contextualizer'] = agent_contextualizer(
                        session, company_name, None, None
                    )

                st.markdown('<div class="section-header">🧠 Agent 1: Data Enrichment</div>', unsafe_allow_html=True)
                with st.expander("View Operational Analysis & Insights", expanded=True):
                    st.markdown(agent_results['contextualizer'])

                # --- Agent 2: Researcher (runs second) ---
                with st.spinner("🔍 Agent 2: Researching market position..."):
                    agent_results['researcher'] = agent_researcher(session, company_name)

                st.markdown('<div class="section-header">🔍 Agent 2: Market Research</div>', unsafe_allow_html=True)
                with st.expander("View Market Position & Industry Context", expanded=True):
                    st.markdown(agent_results['researcher'])

                # --- Agent 3: Strategist (runs last, needs both outputs) ---
                with st.spinner("🎯 Agent 3: Generating sales strategy..."):
                    agent_results['strategist'] = agent_strategist(
                        session, company_name,
                        agent_results['researcher'],
                        agent_results['contextualizer'],
                        None
                    )

                st.markdown('<div class="section-header">🎯 Agent 3: Salesforce Pre-Call Summary</div>', unsafe_allow_html=True)
                st.markdown(
                    f"""
                    <div style="background:#fff; padding:20px; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.08); border-left: 4px solid #4169E1;">
                        {agent_results['strategist']}
                    </div>
                    """,
                    unsafe_allow_html=True
                )
                
                # --- PDF Download ---
                st.markdown("---")
                pdf_bytes = generate_pdf(company_name, agent_results, None)
                if pdf_bytes:
                    b64_pdf = base64.b64encode(pdf_bytes).decode()
                    pdf_filename = f"{company_name.replace(' ', '_')}_complete_summary.pdf"
                    st.markdown(
                        f'<a href="data:application/pdf;base64,{b64_pdf}" '
                        f'download="{pdf_filename}" '
                        f'style="display:inline-block;padding:10px 24px;background:#4169E1;'
                        f'color:#fff;border-radius:6px;text-decoration:none;font-weight:600;'
                        f'font-size:14px;">📄 Download Complete Intelligence Summary (PDF)</a>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.warning("PDF generation failed. Downloading as Markdown instead.")
                    md_sections = [
                        f"# Summarization of {company_name} by Poseidon\n",
                        "## Agent 1 – Market Research\n",
                        agent_results.get('researcher', ''),
                        "\n## Agent 2 – Operational Analysis\n",
                        agent_results.get('contextualizer', ''),
                        "\n## Agent 3 – Salesforce Strategy\n",
                        agent_results.get('strategist', ''),
                    ]
                    b64_md = base64.b64encode("\n".join(md_sections).encode()).decode()
                    md_filename = f"{company_name.replace(' ', '_')}_complete_summary.md"
                    st.markdown(
                        f'<a href="data:text/markdown;base64,{b64_md}" '
                        f'download="{md_filename}" '
                        f'style="display:inline-block;padding:10px 24px;background:#e67e22;'
                        f'color:#fff;border-radius:6px;text-decoration:none;font-weight:600;'
                        f'font-size:14px;">📄 Download Intelligence Summary (Markdown)</a>',
                        unsafe_allow_html=True,
                    )
                
            except Exception as e:
                st.error(f"An error occurred: {e}")
    
    elif account_lookup == "Select Account Type":
        st.info("👆 Please select an Account Type to begin.")

