# Hermes — Strategic Intelligence Platform

## Overview

Hermes is an AI-powered strategic intelligence dashboard designed for aviation fuel sales teams. Named after the Greek god of commerce and communication, Hermes transforms raw data into actionable sales intelligence by combining external market research with internal business data.

The platform automates the research and analysis process that traditionally requires hours of manual work, delivering comprehensive customer intelligence reports in minutes.

## What Hermes Does

Hermes answers one critical question for every customer engagement: **What do we need to know about this company to win their business?**

It does this through three specialized AI agents that run in sequence, with each result appearing on screen as soon as it's ready — so you can start reading while the next agent works.

### 1. Data Analysis Agent (fastest — appears first)
Pulls and interprets internal business data from Snowflake, including:
- 12-month fuel volume and gross profit trends
- Addressable market capture rates
- Top airport locations not yet won
- Non-home-region flight activity (expansion opportunities)
- Aircraft fleet composition (tails and types)
- Credit and payment history

### 2. Market Research Agent (runs second)
Conducts real-time web research on any company using Perplexity AI, then synthesizes findings into a single executive report covering:
- Company overview, mission, and industry classification
- Competitive positioning and market share
- Financial performance and business model
- Sustainability commitments, carbon reduction goals, and SAF strategy
- ESG regulatory exposure and executive leadership stance
- Strategic opportunities for aviation fuel and services

### 3. Strategy Agent (runs last)
Synthesizes research and data into a ready-to-use strategic package:
- CRM-ready account summary fields
- Recommended sales approach and talking points
- Risk factors and opportunity areas
- Competitive positioning strategy

## Capabilities

### Existing Account Analysis
For companies already in the system, Hermes displays 12 key performance metrics at a glance and then runs all three agents progressively — each result appears as soon as it's ready, so you can start reading immediately.

### New Prospect Research
For companies not yet in the system, Hermes runs the same three agents in sequence. The Data Analysis agent infers operational context from public data, while the Research agent conducts full external research.

### Report Export
Every analysis can be exported as a professionally formatted PDF report with:
- Company branding and Hermes header
- All metric summaries (existing accounts)
- Full agent analysis sections
- Royal blue themed layout

## Security and Access

- Protected by Auth0 single sign-on (SSO) authentication
- Role-based access: only authorized sales team members can access the platform
- All internal data stays within the corporate Snowflake environment
- External research is conducted through secure API connections

## Technology

Hermes is built on:
- **Perplexity AI** for real-time web research with structured output
- **Claude Sonnet 4.5** for AI synthesis and strategy generation (via Snowflake Cortex)
- **Snowflake** for internal data storage and AI processing
- **Streamlit** for the interactive web dashboard
- **Auth0** for enterprise authentication
- **AWS** for cloud deployment and secrets management

## Application Structure

```
hermes/
├── app.py                        # Main application
├── config.py                     # Configuration settings
├── auth.py                       # Login and access control
├── snowflake_client.py           # Internal data connections + AI helpers
├── pdf_generator.py              # Report generation
├── agents/                       # AI agent modules
│   ├── contextualizer.py         # Data analysis (Snowflake Cortex)
│   ├── researcher.py             # Market research (Perplexity + Cortex)
│   ├── strategist.py             # Strategy synthesis (Snowflake Cortex)
│   ├── orchestrator.py           # Agent coordination
│   └── schemas/                  # Research output templates
├── tests/                        # Test scripts
├── CONFIG_GUIDE.md               # Configuration explainer
├── README_BUSINESS.md            # This document
└── requirements.txt              # Software dependencies
```

## Who Is Hermes For?

Hermes is built for aviation fuel sales professionals who need to:
- Prepare for customer meetings with comprehensive intelligence
- Identify growth opportunities in existing accounts
- Research new prospects before outreach
- Generate consistent, data-driven account strategies
- Save time on manual research and report creation
