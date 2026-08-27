# CRM brief template — the 16-field Salesforce form

**Why this file exists.** The existing-customer brief currently emits a flat placeholder
structure. The agreed target is the 16-field Salesforce form the previous application produced,
which sales already works from. That form's exact section headers, field wording and authoring
rules lived only in the previous application's `agents/strategist.py`, which was removed when the
repository was cleaned up. This document preserves it verbatim so the port is not lost.

**Status: not yet ported.** None of the section headers below appear anywhere in
`backend/poseidon/tasks/customer_insight/` today — verified at the time of writing.

**Recovering the original.** The removed application remains in git history. The template lived
at `agents/strategist.py` and can be read with
`git show 51366f9:agents/strategist.py`. Its Perplexity response schemas — market position,
strategic profile, sustainability/ESG, operational profile, latest news and partnerships — are at
`git show 51366f9:agents/schemas/`, and are not transcribed here because no port of them is
outstanding.

## Output structure — 16 fields across 5 sections

Section headers and field numbering are reproduced exactly as the original prompt specified them.

### Executive Summary

1. **Account Business Overview** — two sentences on who they are and their primary business.
2. **Our Overall Strategic Plan** — an inferred strategy, e.g. "Leverage their expansion in Asia
   to pitch our global supply network."
3. **Relationship Growth – Phase 1** — a specific first step, e.g. "Secure meeting with Fleet
   Procurement to discuss EU ETS compliance."

### Account Research – Who Are They?

1. **What do they do (Industry Code)** — sector / primary industry.
2. **Where And What?** — geographic scope and primary fleet or asset type.
3. **What marketplace(s) do they serve & why?** — end-customer profile, e.g. "Serves dry-bulk
   charterers and commodity shippers requiring reliable global bunker supply."
4. **What Services do they buy now?** — summarised from internal data. For a prospect, "None".
5. **Who do they compete against & how?** — competitors, from the research step.

### Carbon Reduction Goals

1. **Published sustainability report?** — Yes/No plus year, from the ESG research.
2. **Comments** — key takeaways, e.g. "Focus on offsets rather than SAF."
3. **Defined carbon reduction target?** — Yes/No.
4. **Comments** — the specific goal, e.g. "Net Zero by 2050."

### Business Drivers / KPI's

1. **Identify Key Performance Indicators** — inferred financial and operational KPIs, e.g. "Cost
   per Tonne-Mile, Vessel Utilization, EEOI / Carbon Intensity Indicator (CII)."
2. **Which KPIs can we solution to?** — connects an offering to a KPI, e.g. "Fuel Efficiency ->
   Our Voyage Optimization Platform."

### Customer Challenges / Needs 1

1. **Challenge/Need** — the primary pain point identified from operational context.
2. **Solution** — the specific fuel or service offering that addresses it.

## Authoring rules the original enforced

These constrained the generated text and should carry across, since they encode both a
correctness requirement and two rendering bugs that were being worked around:

1. **Consistency** — do not invent data. Where a metric is missing from the inputs, state "Not
   publicly available in current data."
2. **Strategy** — the strategic plan and relationship-growth fields must combine the market view
   with the operational view, not restate either alone.
3. **Services** — list services only where they appear in internal data. Where none do, write
   "Prospect - No current services."
4. **No bold text** — no `**text**` anywhere in the output.
5. **No dollar signs** — write "USD" rather than `$`, e.g. "700k USD". The `$` character
   triggered markdown math rendering in the previous UI.

Rules 4 and 5 were renderer workarounds. Whether they still apply depends on how the current
frontend renders brief sections — worth confirming rather than carrying over blindly, since this
codebase renders briefs through a dedicated phase-section component rather than as loose
markdown.

## Notes for whoever ports this

- The original filled these fields with a single model call that received both the market
  research and the operational context as text. This codebase's brief flow already runs
  contextualize and research concurrently and synthesises afterwards, so the inputs exist; what
  is missing is the output contract.
- The deterministic-core rule still applies: any figure appearing in a field must come from the
  certified data path, not from the model.
- "Customer Challenges / Needs 1" is numbered, implying the original form supported more than one
  challenge block. Only one was implemented. Confirm with sales whether the port should support
  several.
