---
name: weekly-business-review
description: Generates a VP-ready Weekly Business Review (WBR) brief analyzing week-over-week product adoption, data ingestion, ARR trends, and root-cause support signals. Use when asked to run a WBR, weekly metrics review, or diagnose product usage anomalies.
allowed-tools: mcp__ipa-analytics__get_metric_definitions, mcp__ipa-analytics__list_schema, mcp__ipa-analytics__run_readonly_sql, Write(outputs/**)
---

# Weekly Business Review (WBR) Workflow

Follow these steps in order. Keep a running log of every SQL statement and its `row_count`; it becomes the Query Appendix.

1. **Load Governance:** Call `get_metric_definitions()` and `list_schema()`. Use `product_adoption_rate`, `weekly_ingestion_volume`, `total_arr`, `net_dollar_retention` and `expansion_arr` exactly as defined, including their `filters`. Resolve the calendar from `reporting_calendar` (latest complete week = `MAX(week_start)`; prior week = `MAX(week_start) - INTERVAL 7 DAY`).
2. **Macro Portfolio Snapshot:** With `run_readonly_sql()`, compare the latest vs prior week for all four products (`Infra Monitoring`, `APM`, `Log Management`, `Cloud Security`):
   - Product Adoption Rate (%) and WoW delta in **bps**
   - Weekly Ingestion Volume (GB) and WoW **%** delta
   - Total ARR, NDR and Expansion ARR by `arr_tier` (quarterly metrics: state them as context, not WoW movers)
3. **Isolate Anomalies:** Re-run step 2 at `product_name x arr_tier` grain. Flag every cell with ingestion moving more than +/-5% WoW or adoption moving more than +/-300 bps. For each flagged cell, run an account-level query: how many accounts declined/grew >15%, how many effectively paused (<10% of prior week), their combined `current_arr`, how many renew in the next 90 days (per `renewal_window_rule`) and their `health_status` mix. Note when a segment anomaly is masked at the platform level.
4. **Qualitative Root-Cause Check:** Query `support_tickets` for the flagged product over the 14 days ending at the as-of date, joined to `accounts` for `arr_tier`. Count tickets and distinct accounts per tier, measure overlap with the affected accounts from step 3, and synthesize the top 2-3 recurring pain points with example `ticket_id`s. If tickets do not explain the move, say so explicitly — do not force a narrative.
5. **Format Executive Output:** Save to `outputs/sample_wbr_brief.md` using this template. Status legend: 🔴 ingestion <= -5% or adoption <= -300 bps; 🟢 ingestion >= +5%; 🟡 otherwise.

```markdown
# Weekly Product Analytics Business Review — Week of [Latest Week Date]

## Executive Summary
- **Headline:** [1-sentence takeaway on overall platform health and primary anomaly]
- **Critical Alert:** [Specific product + segment drop, exact % delta, and ARR at risk]
- **Bright Spot:** [Top-performing product or expansion driver]

## Product Performance Scorecard (WoW)

| Product | Adoption Rate | WoW Delta | Ingestion (GB) | WoW Delta | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |

## Segment Heatmap — Ingestion WoW % by ARR Tier
| Product | SMB | Mid-Market | Enterprise |

## Revenue Context (Quarter-to-Date)
| ARR Tier | Accounts | Total ARR | NDR | Expansion ARR |

## Deep Dive: Primary Anomaly & Root Cause
- **Affected Cohort:** [Product + ARR Tier + Count of impacted accounts]
- **Quantitative Signal:** [Exact usage/ingestion metrics]
- **Revenue Exposure:** [ARR of affected accounts, 90-day renewals, health mix]
- **Qualitative Evidence (Support Tickets):** [Ticket themes with example ticket IDs]

## Recommended PM Actions
1. **[Immediate Action]:** [Owner + specific intervention]
2. **[Follow-Up Analysis]:** [Next analytical cut to validate]

## Query Appendix
[Numbered list: purpose, row_count, SQL in a fenced block]
```
