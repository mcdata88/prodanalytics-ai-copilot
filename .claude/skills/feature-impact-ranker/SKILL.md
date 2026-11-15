---
name: feature-impact-ranker
description: Classifies unstructured customer support and feature-request tickets, joins them to account ARR and renewal dates, and produces a revenue-weighted prioritization table for Product Managers. Use when asked to prioritize feature requests, analyze customer feedback, or assess ARR at risk.
allowed-tools: mcp__ipa-analytics__get_metric_definitions, mcp__ipa-analytics__list_schema, mcp__ipa-analytics__run_readonly_sql, Write(outputs/**)
---

# Revenue-Weighted Feature Request Ranker

Follow these steps in order. Keep a running log of every SQL statement and its `row_count`.

1. **Inspect Schema & Calendar:** Call `list_schema()` and `get_metric_definitions()`. Resolve the as-of date and the 90-day renewal window from `reporting_calendar`.
2. **Extract & Classify Themes:** Scope = `ticket_type IN ('feature_request', 'complaint')`. Classify `raw_text` into the standard taxonomy below with a single `CASE WHEN regexp_matches(lower(raw_text), ...)` expression, evaluated **in this order** (first match wins):

   | # | Theme | Pattern (lower-case regex) |
   | :- | :--- | :--- |
   | 1 | Log Overage Pricing & Retention Tiering | `overage\|retention\|cold tier\|budget cap\|drop rules\|pre-ingest\|log bill` |
   | 2 | OpenTelemetry Native Ingestion | `opentelemetry\|otel\|otlp` |
   | 3 | Custom Cost Attribution | `cost attribution\|chargeback\|cost center\|cost breakdown\|monitoring bill\|attribute observability spend` |
   | 4 | RBAC / SSO Granularity | `rbac\|sso\|scim\|permission\|roles\|restrict access` |
   | 5 | Compliance Framework Coverage | `compliance\|soc 2\|pci\|hipaa\|cis \|auditor` |
   | 6 | Alert Noise Reduction | `alert\|pager\|noise\|dedup\|threshold` |
   | 7 | Dashboard Performance | `dashboard\|timeout\|load time\|performance` |

   Then read every ticket that fell to `Unclassified` and assign it by judgment (or leave it Unclassified). Report how many tickets were rule-classified vs judgment-classified so the run is auditable. If a new theme appears in >=3 tickets, propose it as a taxonomy addition; do not silently add it.
3. **Calculate Revenue Weighting:** Per theme, compute on a **deduplicated** theme x account set (never `SUM(DISTINCT current_arr)`, which collapses accounts that share an ARR value):
   - **Ticket Count**
   - **Unique Accounts Impacted**
   - **Total Impacted ARR ($)** = sum of `current_arr` over distinct accounts
   - **Near-Term Renewal ARR ($)** = same, restricted to `renewal_window_rule`
   - **At-Risk Accounts** = distinct accounts with `health_status IN ('At Risk', 'Critical')`
   - **Product Area** = most frequent `product_name`; **Top Segment** = `arr_tier` contributing the most impacted ARR
4. **Rank & Synthesize:** Rank by **Total Impacted ARR** descending; break ties by Near-Term Renewal ARR. For the top 3 themes pull one verbatim quote from `raw_text` (prefer the highest-ARR account) and note when ranking by renewal ARR or ticket count would change the order.
5. **Format PM Output:** Save to `outputs/sample_feature_ranking.md`:

```markdown
# PM Feature Prioritization — Revenue-Weighted Customer Demand

## Prioritized Backlog Matrix

| Rank | Theme | Product Area | Tickets | Accounts | Total Impacted ARR | 90-Day Renewal ARR | At-Risk Accts | Top Segment |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |

## Top 3 Revenue Drivers — Voice of Customer
### 1. [Theme Name] ($[X]M Impacted ARR)
- **Customer Pain:** [2-sentence synthesis]
- **Representative Quote:** *"[Verbatim snippet]"* (`ticket_id`, `[arr_tier]`)
- **PM Recommendation:** [Concrete scope suggestion]

## Method & Caveats
[Scope, classification counts, ranking sensitivity]

## Query Appendix
[Numbered list: purpose, row_count, SQL in a fenced block]
```
