# Hermes — AI Strategic Intelligence Dashboard

Hermes is a 3-agent AI-powered strategic intelligence platform for aviation fuel sales. It combines real-time web research (Perplexity), LLM synthesis (Claude Sonnet 4.5 via Snowflake Cortex), and internal business data into a single Streamlit dashboard with progressive result display.

## Architecture

```
hermes/
├── app.py                        # Streamlit entry point (progressive display)
├── config.py                     # Dual-mode config (local / AWS)
├── auth.py                       # Auth0 authentication (toggleable)
├── snowflake_client.py           # Snowpark session, queries, Cortex, token counting
├── pdf_generator.py              # PDF/Markdown export (WeasyPrint)
├── agents/
│   ├── __init__.py
│   ├── contextualizer.py         # Agent 1 — Snowflake Cortex (operational analysis)
│   ├── researcher.py             # Agent 2 — Perplexity + Cortex (market research)
│   ├── strategist.py             # Agent 3 — Snowflake Cortex (sales strategy)
│   ├── orchestrator.py           # Workflow coordination (used by tests)
│   └── schemas/
│       ├── __init__.py
│       ├── market_position.py    # JSON schema for Perplexity call 1
│       ├── strategic_profile.py  # JSON schema for Perplexity call 2
│       └── sustainability_esg.py # JSON schema for Perplexity call 3
├── tests/
│   ├── __init__.py
│   ├── contextualizer_test.py    # Agent 1 standalone test
│   ├── researcher_test.py        # Agent 2 standalone test (with token counting)
│   ├── perplexity_test.py        # Perplexity API test
│   └── test_all_agents.py        # Full 3-agent integration test
├── .streamlit/config.toml
├── requirements.txt
├── .env.example
├── .gitignore
├── CONFIG_GUIDE.md
└── README_BUSINESS.md
```

## Quick Start (Local Development)

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

Open `config.py` and set the two toggles at the top:

```python
DEPLOY_MODE = "local"    # Keep as "local" for testing
AUTH0_ENABLED = False     # Keep False to skip login screen
```

Then fill in your Snowflake credentials in `_LOCAL_SNOWFLAKE_CONNECTION` and your Perplexity API key in `_LOCAL_PERPLEXITY_API_KEY`.

See `CONFIG_GUIDE.md` for a plain-language explanation.

### 3. Run the app

```bash
cd hermes
streamlit run app.py
```

### 4. Run tests

```bash
# Individual agents
python -m tests.contextualizer_test "NetJets"
python -m tests.researcher_test "Delta Air Lines"
python -m tests.perplexity_test "NetJets"

# Full 3-agent pipeline
python -m tests.test_all_agents "NetJets"
python -m tests.test_all_agents "NetJets" --new   # prospect mode
```

## Deployment (AWS Mode)

Set the toggles in `config.py`:

```python
DEPLOY_MODE = "aws"
AUTH0_ENABLED = True
```

In AWS mode:
- **Snowflake credentials** are pulled from AWS Secrets Manager (secret: `hermes_secret_json`)
- **Perplexity API key** is read from the `PERPLEXITY_API_KEY` environment variable
- **Auth0 config** is built from `CLIENTID` and `DOMAIN` environment variables

Required environment variables are listed in `.env.example`.

## How the Agents Work

### Agent 1 — Contextualizer (Snowflake Cortex / Claude Sonnet 4.5)
Analyzes internal Snowflake data (fuel volumes, profits, credit, fleet info) and produces an operational context report. For prospects, infers context from public data.

### Agent 2 — Researcher (Perplexity + Snowflake Cortex)
Runs 3 sequential web research calls using Perplexity structured output:
1. **Sustainability & ESG** (`sonar-deep-research`) — carbon goals, SAF strategy, executive stance
2. **Market Position** (`sonar`) — company overview, competitive landscape
3. **Strategic Profile** (`sonar-pro`) — business model, financials, market presence

Results are synthesized by Cortex into a single narrative report (no raw data sections passed downstream).

### Agent 3 — Strategist (Snowflake Cortex / Claude Sonnet 4.5)
Takes output from Agents 1 and 2 to generate CRM-ready fields and a strategic action plan.

### Workflow (Progressive Display)

Both existing and new account flows use the same order:

1. **Agent 1** (Contextualizer) runs first → results display immediately
2. **Agent 2** (Researcher) kicks off → results display when ready
3. **Agent 3** (Strategist) runs last → final strategy appears

Users can read earlier results while later agents are still running.

## Key Features

- 12 metric cards displaying Snowflake data (volumes, profits, capture rates, credit)
- Progressive agent display — no waiting for all 3 to finish
- Cortex-synthesized research summary (not raw Perplexity sections)
- Token counting via Snowflake `AI_COUNT_TOKENS` (in test scripts)
- Truncated JSON recovery for large Perplexity responses
- PDF export with royal blue themed formatting
- Dual account workflow (existing vs. new prospects)
- Auth0 role-based access control (role: `Poseidon:Sales`)

## Technology Stack

- **Streamlit** — interactive web dashboard
- **Perplexity AI** — real-time web research with structured output
- **Claude Sonnet 4.5** — LLM via Snowflake Cortex
- **Snowflake** — internal data + Cortex AI functions
- **Auth0** — enterprise authentication
- **AWS** — cloud deployment and secrets management

## Requirements

- Python 3.9+
- Snowflake account with access to the MCA schema
- Perplexity API key
- WeasyPrint system dependencies (for PDF generation)
- Auth0 tenant (production only)
