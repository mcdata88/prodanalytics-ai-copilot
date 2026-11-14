"""Generate the synthetic SaaS analytics warehouse: data/saas_analytics.duckdb.

Deterministic (fixed seed). Builds three tables matching data/metrics.yml:
  accounts              500 B2B accounts across SMB / Mid-Market / Enterprise
  weekly_product_usage  12 weeks x 4 products of usage telemetry
  support_tickets       100 unstructured tickets / feature requests

Planted signal (what the WBR skill should find):
  In the latest complete week, Mid-Market Log Management ingestion drops ~18% WoW.
  A cohort of Mid-Market accounts paused or throttled log shipping after overage
  charges; the matching complaints are in support_tickets over the last 14 days.

Run:  uv run python data/seed_data.py
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

SEED = 42
DATA_DIR = Path(__file__).resolve().parent
DB_PATH = DATA_DIR / "saas_analytics.duckdb"

N_WEEKS = 12
LATEST_WEEK = date(2026, 11, 2)  # Monday of the latest complete week
WEEKS = [LATEST_WEEK - timedelta(weeks=N_WEEKS - 1 - i) for i in range(N_WEEKS)]
AS_OF = LATEST_WEEK + timedelta(days=7)

PRODUCTS = ["Infra Monitoring", "APM", "Log Management", "Cloud Security"]
ADOPTION_MIN_GB = {"Infra Monitoring": 10, "APM": 5, "Log Management": 25, "Cloud Security": 2}

TIERS = {
    # tier: (account count, ARR range, P(provisioned) per product, weekly GB range per product, users range)
    "SMB": (250, (12_000, 70_000),
            {"Infra Monitoring": 0.95, "APM": 0.45, "Log Management": 0.50, "Cloud Security": 0.25},
            {"Infra Monitoring": (15, 90), "APM": (6, 40), "Log Management": (30, 220), "Cloud Security": (2, 12)},
            (2, 25)),
    "Mid-Market": (175, (80_000, 380_000),
                   {"Infra Monitoring": 0.97, "APM": 0.65, "Log Management": 0.70, "Cloud Security": 0.40},
                   {"Infra Monitoring": (120, 600), "APM": (40, 250), "Log Management": (400, 2_200), "Cloud Security": (10, 60)},
                   (10, 120)),
    "Enterprise": (75, (420_000, 2_600_000),
                   {"Infra Monitoring": 1.00, "APM": 0.85, "Log Management": 0.85, "Cloud Security": 0.70},
                   {"Infra Monitoring": (900, 4_500), "APM": (300, 1_600), "Log Management": (3_000, 16_000), "Cloud Security": (60, 400)},
                   (60, 600)),
}

# Weekly growth drift per (product, tier). Cloud Security Enterprise is the planted bright spot.
DRIFT = {
    ("Cloud Security", "Enterprise"): 0.012,
    ("Cloud Security", "Mid-Market"): 0.006,
    ("APM", "Enterprise"): 0.004,
}
DEFAULT_DRIFT = 0.002
CLOUDSEC_ENT_FINAL_WEEK_LIFT = 1.07  # new CSPM framework pack launched in the latest week

NAME_A = ["North", "Blue", "Iron", "Silver", "Quantum", "Bright", "Summit", "Harbor", "Atlas", "Nimbus",
          "Cedar", "Vertex", "Pioneer", "Lumen", "Crescent", "Orbit", "Granite", "Falcon", "Meridian", "Copper"]
NAME_B = ["Logistics", "Health", "Fintech", "Retail", "Labs", "Media", "Robotics", "Energy", "Software",
          "Payments", "Analytics", "Mobility", "Foods", "Security", "Networks", "Biotech", "Travel", "Games"]
NAME_C = ["Inc", "Corp", "Co", "Group", "Systems", "Holdings", "Technologies"]


def build_accounts(rng: random.Random) -> list[dict]:
    accounts, idx, used_names = [], 1, set()
    for tier, (count, (lo, hi), *_rest) in TIERS.items():
        for _ in range(count):
            while True:
                name = f"{rng.choice(NAME_A)} {rng.choice(NAME_B)} {rng.choice(NAME_C)}"
                if name not in used_names:
                    used_names.add(name)
                    break
            current = round(rng.uniform(lo, hi), -2)
            r = rng.random()
            if r < 0.05:
                prior = 0.0  # net-new logo
            elif r < 0.20:
                prior = round(current / rng.uniform(0.80, 0.97), -2)  # contraction
            elif r < 0.55:
                prior = current  # flat
            else:
                prior = round(current / rng.uniform(1.02, 1.30), -2)  # expansion
            accounts.append({
                "account_id": f"ACC-{idx:04d}",
                "account_name": name,
                "arr_tier": tier,
                "current_arr": current,
                "prior_quarter_arr": prior,
                "renewal_date": AS_OF + timedelta(days=rng.randint(5, 365)),
                "health_status": rng.choices(["Healthy", "At Risk", "Critical"], [0.78, 0.17, 0.05])[0],
            })
            idx += 1
    return accounts


def build_usage(rng: random.Random, accounts: list[dict]) -> tuple[list[list], set[str], set[str]]:
    """Return usage rows plus the planted 'paused' and 'throttled' Mid-Market Log Management cohorts."""
    base: dict[tuple[str, str], tuple[float, int, str]] = {}
    for a in accounts:
        _, _, p_prov, gb_rng, u_rng = TIERS[a["arr_tier"]]
        for p in PRODUCTS:
            if rng.random() < p_prov[p]:
                gb = rng.uniform(*gb_rng[p])
                users = rng.randint(*u_rng)
                if rng.random() < 0.08:  # a slice of shelfware accounts that never adopt
                    gb, users = ADOPTION_MIN_GB[p] * rng.uniform(0.2, 0.8), rng.randint(0, 2)
                base[(a["account_id"], p)] = (gb, users, a["arr_tier"])

    # Planted cohort: Mid-Market Log Management accounts that cut log shipping after overage bills.
    mm_log = sorted(acc for (acc, p), (gb, users, t) in base.items()
                    if p == "Log Management" and t == "Mid-Market" and users >= 3 and gb >= 200)
    affected = rng.sample(mm_log, 34)
    paused, throttled = set(affected[:11]), set(affected[11:])

    rows: list[list] = []
    gb_by_week: dict[tuple[str, str], list[float]] = {}
    users_by_week: dict[tuple[str, str], list[int]] = {}
    for (acc, p), (gb0, users0, tier) in base.items():
        drift = DRIFT.get((p, tier), DEFAULT_DRIFT)
        gbs, us = [], []
        for w in range(N_WEEKS):
            gbs.append(gb0 * (1 + drift) ** w * rng.uniform(0.96, 1.04))
            u = max(0, round(users0 * rng.uniform(0.93, 1.07)))
            if rng.random() < 0.015:  # occasional quiet week (holiday, team offsite) -> below adoption bar
                u = min(u, 2)
            us.append(u)
        if p == "Cloud Security" and tier == "Enterprise":
            gbs[-1] *= CLOUDSEC_ENT_FINAL_WEEK_LIFT
        if acc in paused and p == "Log Management":
            gbs[-1] = gbs[-2] * rng.uniform(0.01, 0.04)
            us[-1] = rng.randint(0, 2)
        gb_by_week[(acc, p)] = gbs
        users_by_week[(acc, p)] = us

    # Solve the throttle factor so the Mid-Market Log Management aggregate lands at exactly -18.0% WoW.
    keys = [k for k, v in base.items() if k[1] == "Log Management" and v[2] == "Mid-Market"]
    prior_total = sum(gb_by_week[k][-2] for k in keys)
    fixed_total = sum(gb_by_week[k][-1] for k in keys if k[0] not in throttled)
    throttled_total = sum(gb_by_week[k][-1] for k in keys if k[0] in throttled)
    factor = (0.82 * prior_total - fixed_total) / throttled_total
    for k in keys:
        if k[0] in throttled:
            gb_by_week[k][-1] *= factor * rng.uniform(0.97, 1.03)
    # Re-normalise after the per-account jitter so the headline number is exact.
    rescale = (0.82 * prior_total - fixed_total) / sum(gb_by_week[k][-1] for k in keys if k[0] in throttled)
    for k in keys:
        if k[0] in throttled:
            gb_by_week[k][-1] *= rescale

    for (acc, p), gbs in gb_by_week.items():
        for w, week in enumerate(WEEKS):
            gb = round(gbs[w], 2)
            users = users_by_week[(acc, p)][w]
            rows.append([week, acc, p, users, gb, users >= 3 and gb >= ADOPTION_MIN_GB[p]])
    return rows, paused, throttled


# --------------------------------------------------------------------------- tickets
LOG_OVERAGE_TEXTS = [
    "Our log ingestion overage bill this month was nearly 3x our committed spend. We have paused log shipping from staging and non-prod until pricing is sorted.",
    "We got hit with surprise overage charges on Log Management. Leadership asked us to cut ingestion immediately, so we disabled debug-level logs across all services.",
    "The per-GB overage rate makes Log Management unaffordable at our volume. We need a cheaper archive/cold tier instead of paying hot-storage prices for 30 days of retention.",
    "Overage pricing on logs is pushing us to evaluate alternatives. We turned off log forwarding for two clusters this week to stay under quota.",
    "Can you explain why our log bill jumped after the pricing change? We are sampling logs at 20% now just to stay within the committed tier.",
    "We need retention tiering - 7 days hot, 90 days cold - at a lower price point. Right now overages on 30-day retention are forcing us to drop logs entirely.",
    "Finance flagged Log Management overages again. We have stopped ingesting load balancer and CDN logs until we can renegotiate the contract at renewal.",
    "Ingestion quota alerts came too late; we only found out about the overage when the invoice arrived. Please add budget caps or a hard ingestion limit.",
    "We are throttling the log agent on every host because the new overage pricing doesn't work for a mid-sized team. Need volume-based discounts or exclusion filters before ingest.",
    "Our renewal is coming up and the log overage charges are the number one objection from our VP Eng. We cut log volume roughly in half last week.",
    "Please add pre-ingest filtering/drop rules so we are not billed for noisy health-check logs. Overage costs are the reason we paused shipping from Kubernetes.",
]
THEME_TEXTS = {
    "alert_noise": [
        "Alert fatigue is real: our on-call gets 200+ alerts a night and most are duplicates. Need correlation/grouping of related alerts.",
        "Please add alert deduplication and dynamic baselines. Static thresholds page us on every deploy.",
        "Feature request: suppress child alerts when a parent host is down. Our pager volume is unmanageable.",
        "We muted half of our monitors because of noise. Smarter anomaly-based alerting would bring us back.",
    ],
    "rbac_sso": [
        "We need RBAC scoped per team/service, not just admin vs read-only. Security review is blocking wider rollout.",
        "SCIM provisioning and SSO group-to-role mapping are required by our IT policy before we can add more seats.",
        "Feature request: granular permissions so contractors can see dashboards but not raw logs containing PII.",
        "Audit requires us to restrict access to production data by business unit. Current roles are too coarse.",
    ],
    "otel": [
        "We are standardising on OpenTelemetry. Please support native OTLP ingestion without the proprietary agent.",
        "OTel traces lose resource attributes when they go through your collector. Need first-class OpenTelemetry semantic conventions.",
        "Feature request: accept OTLP metrics directly so we can drop the sidecar agent from our service mesh.",
        "Our platform team won't install another vendor agent. Native OpenTelemetry collector support is a must for APM expansion.",
    ],
    "cost_attribution": [
        "We need to attribute observability spend to teams and cost centers using tags. Finance wants chargeback reports.",
        "Feature request: usage and cost breakdown by service/tag so each team owns its monitoring bill.",
        "Can we get a cost attribution dashboard? We can't tell which team is driving our usage growth.",
    ],
    "compliance": [
        "Please add CIS and SOC 2 benchmark packs to Cloud Security posture checks - auditors keep asking for mapped evidence.",
        "Feature request: exportable compliance reports (PCI, HIPAA) from the CSPM findings view.",
        "We need custom compliance frameworks in Cloud Security; our internal controls don't map to the built-in ones.",
    ],
    "dashboard_perf": [
        "Dashboards with more than 20 widgets take 30+ seconds to load. This is a bug that hurts every incident review.",
        "Query timeouts on 30-day dashboards for high-cardinality metrics. Please improve performance.",
        "Dashboard load times regressed after the last release; our NOC wall screens keep timing out.",
    ],
}
THEME_PRODUCTS = {
    "alert_noise": ["Infra Monitoring", "APM"],
    "rbac_sso": ["Infra Monitoring", "Log Management", "Cloud Security", "APM"],
    "otel": ["APM", "Infra Monitoring"],
    "cost_attribution": ["Infra Monitoring", "Log Management"],
    "compliance": ["Cloud Security"],
    "dashboard_perf": ["Infra Monitoring", "APM"],
}
THEME_TYPES = {
    "alert_noise": ["feature_request", "complaint"],
    "rbac_sso": ["feature_request"],
    "otel": ["feature_request"],
    "cost_attribution": ["feature_request"],
    "compliance": ["feature_request"],
    "dashboard_perf": ["bug", "complaint"],
}
# 22 planted overage tickets + 75 themed background tickets + 3 how-to questions = 100
THEME_COUNTS = {"alert_noise": 18, "rbac_sso": 15, "otel": 14, "cost_attribution": 8, "compliance": 10, "dashboard_perf": 10}
TICKET_CONTEXT = [
    "Please escalate to our account team.",
    "This is blocking our Q1 planning.",
    "Our CFO is asking for a response this week.",
    "We have been customers for three years and this is the first time we've considered leaving.",
    "Happy to jump on a call with product.",
    "Ticket filed on behalf of our SRE lead.",
    "We'd like a written answer before our next QBR.",
    "Several of our engineers have raised the same concern.",
]
GENERIC_QUESTIONS = [
    "How do I rotate the API key used by the agent without downtime?",
    "Where can I find the agent changelog for the latest release?",
    "Is there a Terraform provider for managing monitors as code?",
]


def build_tickets(rng: random.Random, accounts: list[dict], usage_keys: set[tuple[str, str]],
                  paused: set[str], throttled: set[str]) -> list[list]:
    by_id = {a["account_id"]: a for a in accounts}
    tickets: list[list] = []
    latest_end = datetime.combine(LATEST_WEEK + timedelta(days=6), datetime.min.time()).replace(hour=17)

    def ts(days_back_lo: int, days_back_hi: int) -> datetime:
        return latest_end - timedelta(days=rng.randint(days_back_lo, days_back_hi),
                                      hours=rng.randint(0, 9), minutes=rng.randint(0, 59))

    # Planted root cause: 22 overage complaints from the affected Mid-Market cohort, last 14 days.
    cohort = sorted(paused | throttled)
    filers = sorted(paused) + rng.sample(sorted(throttled), 22 - len(paused))
    texts = rng.sample(LOG_OVERAGE_TEXTS, len(LOG_OVERAGE_TEXTS)) * 2
    for acc, text in zip(filers, texts):
        tickets.append([acc, "Log Management", ts(0, 13),
                        rng.choices(["complaint", "feature_request"], [0.75, 0.25])[0],
                        f"{text} {rng.choice(TICKET_CONTEXT)}"])
    # Pull a share of the affected cohort's renewals into the next 90 days (ARR at risk).
    for acc in cohort:
        if rng.random() < 0.55:
            by_id[acc]["renewal_date"] = AS_OF + timedelta(days=rng.randint(10, 85))
        by_id[acc]["health_status"] = rng.choices(["At Risk", "Critical", "Healthy"], [0.55, 0.20, 0.25])[0]

    tier_weights = {"SMB": 1.0, "Mid-Market": 1.6, "Enterprise": 2.4}
    for theme, n in THEME_COUNTS.items():
        for i in range(n):
            product = rng.choice(THEME_PRODUCTS[theme])
            candidates = [a for a in accounts if (a["account_id"], product) in usage_keys]
            weights = [tier_weights[a["arr_tier"]] * (1.8 if theme in ("rbac_sso", "otel") and a["arr_tier"] == "Enterprise" else 1)
                       for a in candidates]
            acc = rng.choices(candidates, weights)[0]["account_id"]
            tickets.append([acc, product, ts(0, 83), rng.choice(THEME_TYPES[theme]), rng.choice(THEME_TEXTS[theme])])
    for text in GENERIC_QUESTIONS:  # how-to noise the ranker must scope out
        acc = rng.choice(accounts)["account_id"]
        tickets.append([acc, "Infra Monitoring", ts(0, 83), "question", text])

    tickets.sort(key=lambda t: t[2])
    return [[f"TKT-{1001 + i}", *t] for i, t in enumerate(tickets)]


def main() -> None:
    rng = random.Random(SEED)
    accounts = build_accounts(rng)
    usage, paused, throttled = build_usage(rng, accounts)
    usage_keys = {(r[1], r[2]) for r in usage}
    tickets = build_tickets(rng, accounts, usage_keys, paused, throttled)

    DB_PATH.unlink(missing_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute("""
        CREATE TABLE accounts (
            account_id VARCHAR PRIMARY KEY, account_name VARCHAR, arr_tier VARCHAR,
            current_arr DOUBLE, prior_quarter_arr DOUBLE, renewal_date DATE, health_status VARCHAR)""")
    con.execute("""
        CREATE TABLE weekly_product_usage (
            week_start DATE, account_id VARCHAR, product_name VARCHAR,
            active_users INTEGER, ingestion_gb DOUBLE, is_adopted BOOLEAN)""")
    con.execute("""
        CREATE TABLE support_tickets (
            ticket_id VARCHAR PRIMARY KEY, account_id VARCHAR, product_name VARCHAR,
            created_at TIMESTAMP, ticket_type VARCHAR, raw_text VARCHAR)""")

    con.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [[a[k] for k in ("account_id", "account_name", "arr_tier", "current_arr",
                                     "prior_quarter_arr", "renewal_date", "health_status")] for a in accounts])
    con.executemany("INSERT INTO weekly_product_usage VALUES (?, ?, ?, ?, ?, ?)", usage)
    con.executemany("INSERT INTO support_tickets VALUES (?, ?, ?, ?, ?, ?)", tickets)
    con.close()

    print(f"Wrote {DB_PATH.relative_to(DATA_DIR.parent)}")
    print(f"  accounts:             {len(accounts):>6,}")
    print(f"  weekly_product_usage: {len(usage):>6,}  ({WEEKS[0]} .. {WEEKS[-1]})")
    print(f"  support_tickets:      {len(tickets):>6,}")


if __name__ == "__main__":
    main()
