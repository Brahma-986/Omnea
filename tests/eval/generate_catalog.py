"""Catalog snapshot and benchmark dataset generator with nearest-neighbor mining.

Deterministic generation of a frozen enterprise vendor catalog (260+ entities)
and labeled evaluation queries with:
- Zero family split leakage (catalog & queries strictly disjoint between Dev and Held-Out).
- Real nearest-neighbor adversarial negatives mined over catalog (>=30 with JW >= 0.85).
- max_jw and max_trigram recorded for each negative.
- catalog_variant support for per-case overrides (case_dev_029, case_held_037).
- expected_best_target recorded on >=30 review cases.
- Wilson score interval calculation across all cohorts.
"""

from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Any

from rapidfuzz.distance import JaroWinkler

RANDOM_SEED = 42
OUTPUT_DIR = Path(__file__).parent
CATALOG_FILE = OUTPUT_DIR / "catalog_snapshot.jsonl"
BENCHMARK_FILE = OUTPUT_DIR / "catalog_benchmark.jsonl"
ALIASES_SEED_FILE = OUTPUT_DIR / "vendor_aliases_seed.jsonl"


def deterministic_uuid(namespace: str, name: str) -> str:
    """Generate a consistent UUID based on namespace and identifier name."""
    ns = uuid.uuid5(uuid.NAMESPACE_DNS, f"omnea.context.{namespace}")
    return str(uuid.uuid5(ns, name))


def trigram_similarity(s1: str, s2: str) -> float:
    """Calculate character trigram Jaccard similarity."""
    a, b = s1.lower(), s2.lower()
    t1 = {a[i : i + 3] for i in range(len(a) - 2)}
    t2 = {b[i : i + 3] for i in range(len(b) - 2)}
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / len(t1 | t2)


def wilson_upper_bound(errors: int, total: int, z: float = 1.95996) -> float:
    """Calculate the Wilson score interval upper bound for error rate."""
    if total == 0:
        return 0.0
    p = errors / total
    z2 = z * z
    denom = 1.0 + z2 / total
    center = p + z2 / (2.0 * total)
    spread = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * total)) / total)
    return min(1.0, (center + spread) / denom)


# =============================================================================
# 1. Base Canonical Catalog Entities
# =============================================================================
DEV_CATALOG_BASE = [
    {"name": "Datadog, Inc.", "family": "Datadog", "aliases": ["Datadog"]},
    {"name": "SAP SE", "family": "SAP", "aliases": ["SAP"]},
    {"name": "Cisco Systems, Inc.", "family": "Cisco", "aliases": ["Cisco"]},
    {
        "name": "Sysco Corporation",
        "family": "Sysco",
        "aliases": ["Sysco"],
    },  # Unaliased for Sysco Foods test
    {"name": "Amazon Web Services, Inc.", "family": "Amazon", "aliases": ["AWS", "Amazon AWS"]},
    {"name": "Google LLC", "family": "Google", "aliases": ["Google", "GCP", "Google Cloud"]},
    {"name": "Box, Inc.", "family": "Box", "aliases": ["Box"]},
    {"name": "Fox Corporation", "family": "Fox", "aliases": ["Fox"]},
    {"name": "Studio 2", "family": "Studio", "aliases": []},
    {"name": "HubSpot, Inc.", "family": "HubSpot", "aliases": ["Hubspot"]},
    {"name": "Twilio Inc.", "family": "Twilio", "aliases": ["Twilio"]},
    {"name": "Figma, LLC", "family": "Figma", "aliases": ["Figma"]},
    {"name": "Snowflake, Inc.", "family": "Snowflake", "aliases": ["Snowflake"]},
    {"name": "Atlassian Pty Ltd", "family": "Atlassian", "aliases": ["Atlassian"]},
    {
        "name": "Zoom Video Communications, Inc.",
        "family": "Zoom",
        "aliases": ["Zoom", "Zoom Video"],
    },
    {"name": "ZoomInfo Technologies LLC", "family": "ZoomInfo", "aliases": ["ZoomInfo"]},
    {"name": "Zenith Technologies Ltd", "family": "Zenith", "aliases": []},
    {"name": "SaaS Labs Inc", "family": "SaaS Labs", "aliases": ["SaaS Labs"]},
    {"name": "Fastly, Inc.", "family": "Fastly", "aliases": ["Fastly"]},
    {"name": "Dynatrace LLC", "family": "Dynatrace", "aliases": ["Dynatrace"]},
]

HELD_OUT_CATALOG_BASE = [
    {"name": "Microsoft Corporation", "family": "Microsoft", "aliases": ["Microsoft", "MSFT"]},
    {
        "name": "Salesforce, Inc.",
        "family": "Salesforce",
        "aliases": ["Salesforce", "Salesforce.com"],
    },
    {
        "name": "CrowdStrike, Inc.",
        "family": "CrowdStrike",
        "aliases": ["CrowdStrike", "Crowdstrike"],
    },
    {"name": "Apple Inc.", "family": "Apple", "aliases": ["Apple"]},
    {
        "name": "Applied Materials, Inc.",
        "family": "Applied Materials",
        "aliases": ["Applied Materials"],
    },
    {"name": "US Foods, Inc.", "family": "US Foods", "aliases": ["US Foods"]},
    {"name": "GitHub, Inc.", "family": "GitHub", "aliases": ["GitHub"]},
    {"name": "GitLab, Inc.", "family": "GitLab", "aliases": ["GitLab"]},
    {"name": "Square, Inc.", "family": "Square", "aliases": ["Square", "Block Inc"]},
    {"name": "Squarespace, Inc.", "family": "Squarespace", "aliases": ["Squarespace"]},
    {"name": "Notion Labs, Inc.", "family": "Notion", "aliases": ["Notion"]},
    {"name": "Motion Corp", "family": "Motion", "aliases": ["Motion"]},
    {"name": "Sentry.io", "family": "Sentry", "aliases": ["Sentry", "Functional Software"]},
    {"name": "Centric Software Inc", "family": "Centric Software", "aliases": ["Centric"]},
    {"name": "MongoDB, Inc.", "family": "MongoDB", "aliases": ["MongoDB"]},
    {"name": "ServiceNow, Inc.", "family": "ServiceNow", "aliases": ["ServiceNow"]},
    {"name": "PagerDuty, Inc.", "family": "PagerDuty", "aliases": ["PagerDuty"]},
    {"name": "Cloudflare, Inc.", "family": "Cloudflare", "aliases": ["Cloudflare"]},
    {"name": "Docker, Inc.", "family": "Docker", "aliases": ["Docker"]},
    {"name": "Palantir Technologies Inc.", "family": "Palantir", "aliases": ["Palantir"]},
    {"name": "Slack Technologies, LLC", "family": "Slack", "aliases": ["Slack"]},
    {"name": "Asana, Inc.", "family": "Asana", "aliases": ["Asana"]},
    {"name": "Intercom, Inc.", "family": "Intercom", "aliases": ["Intercom"]},
    {"name": "Telecom Italia S.p.A.", "family": "Telecom Italia", "aliases": ["TIM"]},
    {"name": "Monday.com Ltd", "family": "Monday.com", "aliases": ["Monday.com", "Monday"]},
    {"name": "Sunday Security Inc", "family": "Sunday Security", "aliases": ["Sunday Security"]},
    {"name": "Confluent, Inc.", "family": "Confluent", "aliases": ["Confluent"]},
    {"name": "Level 4 Labs", "family": "Level", "aliases": []},
    {
        "name": "Johnson & Johnson",
        "family": "Johnson & Johnson",
        "aliases": ["J&J", "Johnson and Johnson"],
    },
    {
        "name": "Bank of America Corporation",
        "family": "Bank of America",
        "aliases": ["BofA", "Bank of America"],
    },
    {
        "name": "UK Power Networks Ltd",
        "family": "UK Power Networks",
        "aliases": ["UK Power Networks"],
    },
    {"name": "Apex Group Ltd", "family": "Apex", "aliases": []},
    {
        "name": "Web3 Technologies Inc",
        "family": "Web3",
        "aliases": ["Web3", "Web 3"],
    },
]

# Known existing subsidiaries in the catalog
DEV_SUBSIDIARIES = [
    {"name": "Datadog EMEA", "family": "Datadog", "parent_name": "Datadog, Inc."},
    {"name": "Google UK", "family": "Google", "parent_name": "Google LLC"},
    {"name": "Google APAC", "family": "Google", "parent_name": "Google LLC"},
    {"name": "Cisco International Ltd", "family": "Cisco", "parent_name": "Cisco Systems, Inc."},
]

HELD_OUT_SUBSIDIARIES = [
    {"name": "Acme Germany", "family": "Acme", "parent_name": None},
    {"name": "Acme UK", "family": "Acme", "parent_name": None},
    {"name": "CrowdStrike UK Ltd", "family": "CrowdStrike", "parent_name": "CrowdStrike, Inc."},
    {
        "name": "Microsoft Ireland Operations Ltd",
        "family": "Microsoft",
        "parent_name": "Microsoft Corporation",
    },
]

# Real B2B Distractors (100 Dev, 100 Held-Out, completely disjoint, zero cross-family leaks)
DEV_DISTRACTORS = [
    "Anaplan Inc",
    "Splunk Services LLC",
    "Zscaler Technologies",
    "Netskope Inc",
    "Mimecast North America",
    "Proofpoint Systems",
    "Qualys Security Inc",
    "Tenable Network Security",
    "Rapid7 LLC",
    "Varonis Systems",
    "CyberArk Software",
    "SailPoint Technologies",
    "Ping Identity Corp",
    "ForgeRock Inc",
    "OneLogin LLC",
    "Auth0 Inc",
    "Duo Security",
    "Lookout Mobile",
    "SentinelOne Inc",
    "Cybereason Inc",
    "Deep Instinct",
    "Bitdefender LLC",
    "Sophos Group",
    "Kaspersky Labs",
    "Trend Micro Inc",
    "F-Secure Corp",
    "Check Point Software",
    "Palo Alto Networks",
    "Fortinet Technologies",
    "SonicWall Inc",
    "WatchGuard Technologies",
    "Barracuda Networks",
    "F5 Networks",
    "Citrix Systems",
    "VMware LLC",
    "Nutanix Inc",
    "Pure Storage",
    "NetApp Inc",
    "Cohesity Inc",
    "Rubrik Inc",
    "Veeam Software",
    "Commvault Systems",
    "Veritas Technologies",
    "Datto Inc",
    "Acronis International",
    "Carbonite Inc",
    "Backblaze LLC",
    "Druva Inc",
    "Clumio Inc",
    "HYCU Inc",
    "OwnBackup Inc",
    "Spanning Cloud",
    "Kaseya Limited",
    "ConnectWise LLC",
    "SolarWinds Worldwide",
    "NinjaRMM LLC",
    "Atera Networks",
    "Syncro MSP",
    "Datadog Partner Dev 1",
    "Datadog Partner Dev 2",
    "New Relic Inc",
    "AppDynamics LLC",
    "Dynatrace Systems",
    "Datadog Partner Dev 3",
    "Sumo Logic",
    "Loggly Inc",
    "Papertrail LLC",
    "LogDNA Inc",
    "Coralogix Ltd",
    "Humio ApS",
    "Graylog Inc",
    "Logz.io Inc",
    "Scalyr Inc",
    "Wavefront by VMware",
    "SignalFx Inc",
    "Lightstep Inc",
    "Honeycomb.io",
    "Chronosphere Inc",
    "Grafana Labs",
    "InfluxData Inc",
    "Prometheus Services",
    "Cortex Metrics",
    "Thanos Metrics",
    "VictoriaMetrics",
    "Timescale Inc",
    "Quest Software",
    "Idera Inc",
    "Redgate Software",
    "SolarWinds Database",
    "Idera Database Tools",
    "DbVisualizer AB",
    "Navicat Software",
    "DataGrip JetBrains",
    "DBeaver Corp",
    "Tableau Software",
    "QlikTech Inc",
    "Looker Data",
    "Sisense Inc",
    "ThoughtSpot Inc",
    "Domo Inc",
]

HELD_OUT_DISTRACTORS = [
    "Coupa Software Inc",
    "Basware Corporation",
    "Kyriba Financial",
    "Tipalti Payments",
    "Airbase Solutions",
    "Teampay Expense",
    "Ramp Financial Inc",
    "Brex Corporate",
    "Infor Global",
    "Epicor Software",
    "Sage Group",
    "Unit4 NV",
    "IFS AB",
    "Deltek Inc",
    "Acumatica Inc",
    "NetSuite Inc",
    "Intuit Inc",
    "Xero Limited",
    "FreshBooks Inc",
    "Wave Financial",
    "Zoho Corporation",
    "SugarCRM Inc",
    "Zendesk Inc",
    "Freshworks Inc",
    "Help Scout",
    "Kustomer Inc",
    "Gorgias Inc",
    "Front App Inc",
    "Groove Networks",
    "LiveChat Software",
    "Olark Inc",
    "Tidio LLC",
    "Crisp IM",
    "Drift.com Inc",
    "Qualified.com",
    "Conversica Inc",
    "Exceed.ai",
    "Salesloft Inc",
    "Outreach.io",
    "Apollo.io",
    "Cognism Ltd",
    "Lusha Systems",
    "LeadIQ Inc",
    "Seamless.ai",
    "Uplead LLC",
    "Clearbit Inc",
    "FullContact Inc",
    "PeopleDataLabs",
    "Pipl Inc",
    "Dun & Bradstreet",
    "Experian Business",
    "Equifax Commercial",
    "TransUnion B2B",
    "LexisNexis Risk",
    "Thomson Reuters",
    "Bloomberg LP",
    "FactSet Research",
    "S&P Global",
    "Moody's Analytics",
    "Morningstar Inc",
    "PitchBook Data",
    "Crunchbase Inc",
    "CB Insights",
    "Gartner Inc",
    "Forrester Research",
    "IDC Research",
    "451 Research",
    "G2.com Inc",
    "TrustRadius Inc",
    "Capterra Inc",
    "Software Advice",
    "GetApp Inc",
    "PeerSpot Inc",
    "Marketo Inc",
    "Pardot LLC",
    "Eloqua Corp",
    "ActiveCampaign",
    "Mailchimp Inc",
    "Klaviyo Inc",
    "Sendgrid Inc",
    "Braze Inc",
    "Iterable Inc",
    "Customer.io",
    "Omnisend Inc",
    "Campaign Monitor",
    "Constant Contact",
    "AWeber Systems",
    "GetResponse Inc",
    "ConvertKit LLC",
    "Drip Inc",
    "MailerLite Ltd",
    "Moosend Inc",
    "Brevo SAS",
    "Postmark App",
    "SparkPost Inc",
    "Mailgun Technologies",
    "MessageBird BV",
    "Sinch AB",
    "FloQast Inc",
    "BlackLine Systems",
]


def build_catalog() -> list[dict[str, Any]]:
    """Build the frozen catalog snapshot."""
    catalog: list[dict[str, Any]] = []

    # Dev base
    for item in DEV_CATALOG_BASE:
        vid = deterministic_uuid("catalog", item["name"])
        catalog.append(
            {
                "id": vid,
                "name": item["name"],
                "family": item["family"],
                "split": "dev",
                "aliases": item["aliases"],
                "parent_id": None,
                "status": "active",
            }
        )

    # Dev subsidiaries
    for item in DEV_SUBSIDIARIES:
        vid = deterministic_uuid("catalog", item["name"])
        pid = deterministic_uuid("catalog", item["parent_name"]) if item["parent_name"] else None
        catalog.append(
            {
                "id": vid,
                "name": item["name"],
                "family": item["family"],
                "split": "dev",
                "aliases": [],
                "parent_id": pid,
                "status": "active",
            }
        )

    # Dev distractors
    for name in DEV_DISTRACTORS:
        vid = deterministic_uuid("catalog", name)
        catalog.append(
            {
                "id": vid,
                "name": name,
                "family": f"Distractor_{name}",
                "split": "dev",
                "aliases": [],
                "parent_id": None,
                "status": "active",
            }
        )

    # Held-out base
    for item in HELD_OUT_CATALOG_BASE:
        vid = deterministic_uuid("catalog", item["name"])
        catalog.append(
            {
                "id": vid,
                "name": item["name"],
                "family": item["family"],
                "split": "held_out",
                "aliases": item["aliases"],
                "parent_id": None,
                "status": "active",
            }
        )

    # Held-out subsidiaries
    for item in HELD_OUT_SUBSIDIARIES:
        vid = deterministic_uuid("catalog", item["name"])
        pid = deterministic_uuid("catalog", item["parent_name"]) if item["parent_name"] else None
        catalog.append(
            {
                "id": vid,
                "name": item["name"],
                "family": item["family"],
                "split": "held_out",
                "aliases": [],
                "parent_id": pid,
                "status": "active",
            }
        )

    # Held-out distractors
    for name in HELD_OUT_DISTRACTORS:
        vid = deterministic_uuid("catalog", name)
        catalog.append(
            {
                "id": vid,
                "name": name,
                "family": f"Distractor_{name}",
                "split": "held_out",
                "aliases": [],
                "parent_id": None,
                "status": "active",
            }
        )

    # Special held-out entities for scenario testing (merged target and pending review target)
    sentry_id = deterministic_uuid("catalog", "Sentry.io")
    merged_sentry_id = deterministic_uuid("catalog", "Merged Sentry Inc")
    catalog.append(
        {
            "id": merged_sentry_id,
            "name": "Merged Sentry Inc",
            "family": "MergedSentry",
            "split": "held_out",
            "aliases": [],
            "parent_id": None,
            "status": "merged",
            "previous_status": "active",
            "merged_into_id": sentry_id,
        }
    )

    pending_partner_id = deterministic_uuid("catalog", "Pending Review Partner")
    catalog.append(
        {
            "id": pending_partner_id,
            "name": "Pending Review Partner",
            "family": "PendingPartner",
            "split": "held_out",
            "aliases": [],
            "parent_id": None,
            "status": "pending_review",
        }
    )

    return catalog


# 37 curated adversarial nearest-neighbor queries against held-out catalog
MINED_ADVERSARIAL_SPEC: list[tuple[str, str]] = [
    ("CloudStrike Inc", "CloudStrike"),
    ("CrowdStryke Inc", "CrowdStryke"),
    ("MicroStrategy Inc", "MicroStrategy"),
    ("Salesloft Systems Inc", "Salesloft"),
    ("Applied Medical Inc", "Applied Medical"),
    ("SquareTrade Inc", "SquareTrade"),
    ("MotionPoint Corp", "MotionPoint"),
    ("Centric Consulting Inc", "Centric Consulting"),
    ("MongoLab Inc", "MongoLab"),
    ("ServiceDesk Inc", "ServiceDesk"),
    ("PageDuty Inc", "PageDuty"),
    ("CloudFront Inc", "CloudFront"),
    ("Dockers Inc", "Dockers"),
    ("Palantir Solutions Inc", "Palantir Solutions"),
    ("Intermedia Inc", "Intermedia"),
    ("Telecom Austria S.p.A.", "Telecom Austria"),
    ("Sunday Sky Inc", "Sunday Sky"),
    ("Confluence Inc", "Confluence"),
    ("Johnson Controls Inc", "Johnson Controls"),
    ("Bank of Montreal", "Bank of Montreal"),
    ("Web2 Technologies Inc", "Web2"),
    ("FreshBook Cloud Inc", "FreshBooks"),
    ("SugarCRM Solutions", "SugarCRM Solutions"),
    ("Zendesk Support Inc", "Zendesk Support"),
    ("Front App Systems", "Front App Systems"),
    ("Crisp Messaging", "Crisp Messaging"),
    ("Drift Communications", "Drift Communications"),
    ("Clearbit Data Inc", "Clearbit Data"),
    ("Crunchbase Data Inc", "Crunchbase Data"),
    ("Gartner Consulting", "Gartner Consulting"),
    ("Sendgrid Mail Inc", "Sendgrid Mail"),
    ("Mailchimp Systems", "Mailchimp Systems"),
    ("Brevo Messaging", "Brevo Messaging"),
    ("Klaviyo Marketing Inc", "Klaviyo Marketing"),
    ("Moosend Mail", "Moosend Mail"),
    ("SparkPost Relay", "SparkPost Relay"),
    ("MessageBird Voice", "MessageBird Voice"),
]


def mine_adversarial_negatives(
    catalog: list[dict[str, Any]],
    split: str = "held_out",
) -> list[dict[str, Any]]:
    """Mine nearest-neighbor adversarial negatives over catalog with JW >= 0.85."""
    mined: list[dict[str, Any]] = []
    candidates = [c for c in catalog if c["split"] == split]

    for query, family in MINED_ADVERSARIAL_SPEC:
        best_jw = 0.0
        best_tri = 0.0
        best_cand: dict[str, Any] | None = None

        for cand in candidates:
            # Query must not be identical to an existing catalog entity
            if query.lower() == cand["name"].lower():
                continue
            jw = JaroWinkler.similarity(query.lower(), cand["name"].lower())
            tri = trigram_similarity(query, cand["name"])
            if jw > best_jw:
                best_jw = jw
                best_tri = tri
                best_cand = cand

        assert best_cand is not None
        assert best_jw >= 0.85, (
            f"Query '{query}' nearest neighbor '{best_cand['name']}' has JW={best_jw:.4f} < 0.85"
        )

        mined.append(
            {
                "query_name": query,
                "vendor_family": f"Mined_{family}",
                "best_target_id": best_cand["id"],
                "best_target_name": best_cand["name"],
                "max_jw": round(best_jw, 4),
                "max_trigram": round(best_tri, 4),
            }
        )

    return mined


def build_benchmark_cases(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build exactly 250 evaluation cases (125 Dev / 125 Held-Out)."""
    cases: list[dict[str, Any]] = []
    cat_by_name = {c["name"]: c for c in catalog}

    # Dev ID references
    datadog_id = cat_by_name["Datadog, Inc."]["id"]
    datadog_emea_id = cat_by_name["Datadog EMEA"]["id"]
    sap_id = cat_by_name["SAP SE"]["id"]
    cisco_id = cat_by_name["Cisco Systems, Inc."]["id"]
    sysco_id = cat_by_name["Sysco Corporation"]["id"]
    aws_id = cat_by_name["Amazon Web Services, Inc."]["id"]
    google_id = cat_by_name["Google LLC"]["id"]
    google_uk_id = cat_by_name["Google UK"]["id"]
    google_apac_id = cat_by_name["Google APAC"]["id"]
    box_id = cat_by_name["Box, Inc."]["id"]
    fox_id = cat_by_name["Fox Corporation"]["id"]
    studio_2_id = cat_by_name["Studio 2"]["id"]
    hubspot_id = cat_by_name["HubSpot, Inc."]["id"]
    twilio_id = cat_by_name["Twilio Inc."]["id"]
    figma_id = cat_by_name["Figma, LLC"]["id"]
    snowflake_id = cat_by_name["Snowflake, Inc."]["id"]
    atlassian_id = cat_by_name["Atlassian Pty Ltd"]["id"]
    zoom_id = cat_by_name["Zoom Video Communications, Inc."]["id"]
    zoominfo_id = cat_by_name["ZoomInfo Technologies LLC"]["id"]
    saas_labs_id = cat_by_name["SaaS Labs Inc"]["id"]

    # Held-out ID references
    msft_id = cat_by_name["Microsoft Corporation"]["id"]
    msft_ireland_id = cat_by_name["Microsoft Ireland Operations Ltd"]["id"]
    salesforce_id = cat_by_name["Salesforce, Inc."]["id"]
    crowdstrike_id = cat_by_name["CrowdStrike, Inc."]["id"]
    crowdstrike_uk_id = cat_by_name["CrowdStrike UK Ltd"]["id"]
    apple_id = cat_by_name["Apple Inc."]["id"]
    applied_id = cat_by_name["Applied Materials, Inc."]["id"]
    us_foods_id = cat_by_name["US Foods, Inc."]["id"]
    github_id = cat_by_name["GitHub, Inc."]["id"]
    gitlab_id = cat_by_name["GitLab, Inc."]["id"]
    square_id = cat_by_name["Square, Inc."]["id"]
    squarespace_id = cat_by_name["Squarespace, Inc."]["id"]
    notion_id = cat_by_name["Notion Labs, Inc."]["id"]
    motion_id = cat_by_name["Motion Corp"]["id"]
    sentry_id = cat_by_name["Sentry.io"]["id"]
    centric_id = cat_by_name["Centric Software Inc"]["id"]
    mongodb_id = cat_by_name["MongoDB, Inc."]["id"]
    servicenow_id = cat_by_name["ServiceNow, Inc."]["id"]
    pagerduty_id = cat_by_name["PagerDuty, Inc."]["id"]
    cloudflare_id = cat_by_name["Cloudflare, Inc."]["id"]
    docker_id = cat_by_name["Docker, Inc."]["id"]
    palantir_id = cat_by_name["Palantir Technologies Inc."]["id"]
    asana_id = cat_by_name["Asana, Inc."]["id"]
    intercom_id = cat_by_name["Intercom, Inc."]["id"]
    tim_id = cat_by_name["Telecom Italia S.p.A."]["id"]
    monday_id = cat_by_name["Monday.com Ltd"]["id"]
    sunday_id = cat_by_name["Sunday Security Inc"]["id"]
    confluent_id = cat_by_name["Confluent, Inc."]["id"]
    level_4_id = cat_by_name["Level 4 Labs"]["id"]
    jnj_id = cat_by_name["Johnson & Johnson"]["id"]
    boa_id = cat_by_name["Bank of America Corporation"]["id"]
    uk_power_id = cat_by_name["UK Power Networks Ltd"]["id"]
    acme_germany_id = cat_by_name["Acme Germany"]["id"]
    acme_uk_id = cat_by_name["Acme UK"]["id"]
    web3_id = cat_by_name["Web3 Technologies Inc"]["id"]

    # =========================================================================
    # DEV CASES (Total: 125)
    # =========================================================================
    dev_core: list[dict[str, Any]] = [
        {
            "query_name": "Datadog, Inc.",
            "family": "Datadog",
            "allowed_outcomes": [["MATCH", datadog_id]],
            "category": "legal_suffix",
            "rule_path": "canonical_key",
            "notes": "Exact canonical match after suffix stripping.",
        },
        {
            "query_name": "Datadog LLC",
            "family": "Datadog",
            "allowed_outcomes": [["MATCH", datadog_id]],
            "category": "legal_suffix",
            "rule_path": "canonical_key",
            "notes": "LLC suffix variant.",
        },
        {
            "query_name": "Datadog EMEA",
            "family": "Datadog",
            "allowed_outcomes": [["MATCH", datadog_emea_id]],
            "category": "existing_subsidiary",
            "rule_path": "canonical_key",
            "notes": "Matches existing subsidiary node.",
        },
        {
            "query_name": "Datadog APAC",
            "family": "Datadog",
            "allowed_outcomes": [["SUBSIDIARY_OF", datadog_id]],
            "category": "subsidiary_creation",
            "rule_path": "exact_parent_region",
            "notes": "Creates APAC subsidiary under Datadog, Inc.",
        },
        {
            "query_name": "SAP America",
            "family": "SAP",
            "allowed_outcomes": [["SUBSIDIARY_OF", sap_id]],
            "category": "subsidiary_creation",
            "rule_path": "exact_parent_region",
            "notes": "Region strip leaves SAP, exact match to parent.",
        },
        {
            "query_name": "SAP Deutschland SE",
            "family": "SAP",
            "allowed_outcomes": [["SUBSIDIARY_OF", sap_id]],
            "category": "deutschland_synonym",
            "rule_path": "exact_parent_region",
            "notes": "Deutschland synonym mapped to Germany subsidiary under SAP.",
        },
        {
            "query_name": "Amazon AWS",
            "family": "Amazon",
            "allowed_outcomes": [["MATCH", aws_id]],
            "category": "alias",
            "rule_path": "alias",
            "notes": "Alias match.",
        },
        {
            "query_name": "AWS",
            "family": "Amazon",
            "allowed_outcomes": [["MATCH", aws_id]],
            "category": "alias",
            "rule_path": "alias",
            "notes": "Short alias match.",
        },
        {
            "query_name": "Google",
            "family": "Google",
            "allowed_outcomes": [["MATCH", google_id]],
            "category": "alias",
            "rule_path": "alias",
            "notes": "Alias match.",
        },
        {
            "query_name": "Google UK",
            "family": "Google",
            "allowed_outcomes": [["MATCH", google_uk_id]],
            "forbidden_targets": [google_apac_id],
            "category": "existing_subsidiary",
            "rule_path": "canonical_key",
            "notes": "Matches Google UK, never Google APAC.",
        },
        {
            "query_name": "Google APAC",
            "family": "Google",
            "allowed_outcomes": [["MATCH", google_apac_id]],
            "forbidden_targets": [google_uk_id],
            "category": "existing_subsidiary",
            "rule_path": "canonical_key",
            "notes": "Matches Google APAC, never Google UK.",
        },
        {
            "query_name": "Google France",
            "family": "Google",
            "allowed_outcomes": [["SUBSIDIARY_OF", google_id]],
            "forbidden_targets": [google_uk_id, google_apac_id],
            "category": "subsidiary_creation",
            "rule_path": "exact_parent_region",
            "notes": "Creates Google France under parent.",
        },
        {
            "query_name": "Cisco Systems, Inc.",
            "family": "Cisco",
            "allowed_outcomes": [["MATCH", cisco_id]],
            "forbidden_targets": [sysco_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Cisco canonical; never links to Sysco.",
        },
        {
            "query_name": "Sysco Corporation",
            "family": "Sysco",
            "allowed_outcomes": [["MATCH", sysco_id]],
            "forbidden_targets": [cisco_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Sysco canonical; never links to Cisco.",
        },
        {
            "query_name": "Sysco Foods LLC",
            "family": "Sysco",
            "allowed_outcomes": [["PROVISIONAL_REVIEW", sysco_id], ["NEW_ENTITY", None]],
            "expected_best_target": sysco_id,
            "forbidden_targets": [cisco_id],
            "category": "unaliased_descriptor_negative",
            "rule_path": "fuzzy_review",
            "notes": "Unaliased Sysco Foods; 'foods' not in allowlist, strictly caps at review.",
        },
        {
            "query_name": "Box, Inc.",
            "family": "Box",
            "allowed_outcomes": [["MATCH", box_id]],
            "forbidden_targets": [fox_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Box canonical; never links to Fox.",
        },
        {
            "query_name": "Fox Corporation",
            "family": "Fox",
            "allowed_outcomes": [["MATCH", fox_id]],
            "forbidden_targets": [box_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Fox canonical; never links to Box.",
        },
        {
            "query_name": "Hubspot",
            "family": "HubSpot",
            "allowed_outcomes": [["MATCH", hubspot_id]],
            "category": "alias",
            "rule_path": "alias",
            "notes": "Casing normalization.",
        },
        {
            "query_name": "Twilio Inc.",
            "family": "Twilio",
            "allowed_outcomes": [["MATCH", twilio_id]],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Exact match.",
        },
        {
            "query_name": "Figma Inc",
            "family": "Figma",
            "allowed_outcomes": [["MATCH", figma_id]],
            "category": "legal_suffix",
            "rule_path": "canonical_key",
            "notes": "Suffix variant.",
        },
        {
            "query_name": "Snowflake Computing GmbH",
            "family": "Snowflake",
            "allowed_outcomes": [["MATCH", snowflake_id]],
            "category": "loose_descriptor",
            "rule_path": "loose_key",
            "notes": "Allowlisted loose descriptor 'computing' strips cleanly.",
        },
        {
            "query_name": "Atlassian Pty Ltd",
            "family": "Atlassian",
            "allowed_outcomes": [["MATCH", atlassian_id]],
            "category": "legal_suffix",
            "rule_path": "canonical_key",
            "notes": "Australian suffix.",
        },
        {
            "query_name": "Zoom Video",
            "family": "Zoom",
            "allowed_outcomes": [["MATCH", zoom_id]],
            "forbidden_targets": [zoominfo_id],
            "category": "alias",
            "rule_path": "alias",
            "notes": "Zoom Video alias; never links to ZoomInfo.",
        },
        {
            "query_name": "ZoomInfo",
            "family": "ZoomInfo",
            "allowed_outcomes": [["MATCH", zoominfo_id]],
            "forbidden_targets": [zoom_id],
            "category": "alias",
            "rule_path": "alias",
            "notes": "ZoomInfo alias; never links to Zoom Video.",
        },
        {
            "query_name": "SaaS Labs Inc",
            "family": "SaaS Labs",
            "allowed_outcomes": [["MATCH", saas_labs_id]],
            "forbidden_targets": [sap_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "SaaS Labs canonical; <=3 char guard prevents SAP match.",
        },
        # Non-Auto-Link Cases (Review & New Entity)
        {
            "query_name": "Datadgo Inc",
            "family": "Datadog",
            "allowed_outcomes": [["PROVISIONAL_REVIEW", datadog_id], ["NEW_ENTITY", None]],
            "expected_best_target": datadog_id,
            "category": "typo_review",
            "rule_path": "fuzzy_review",
            "notes": "Typo in Datadog; auto-link strictly forbidden.",
        },
        {
            "query_name": "Dtadog Inc",
            "family": "Datadog",
            "allowed_outcomes": [["PROVISIONAL_REVIEW", datadog_id], ["NEW_ENTITY", None]],
            "expected_best_target": datadog_id,
            "category": "typo_review",
            "rule_path": "fuzzy_review",
            "notes": "Typo with dropped vowel Dtadog; auto-link strictly forbidden.",
        },
        {
            "query_name": "Studio 1",
            "family": "Studio",
            "allowed_outcomes": [["NEW_ENTITY", None]],
            "forbidden_targets": [studio_2_id],
            "category": "digit_mismatch",
            "rule_path": "low_confidence_new",
            "notes": "Digit mismatch: {1} != {2} blocks match.",
        },
        {
            "query_name": "SAP",
            "family": "SAP",
            "catalog_variant": {"exclude": ["SAP SE"]},
            "allowed_outcomes": [["NEW_ENTITY", None]],
            "forbidden_targets": [saas_labs_id],
            "category": "short_guard",
            "rule_path": "low_confidence_new",
            "notes": "Query SAP with only SaaS Labs in catalog: short guard blocks fuzzy.",
        },
        {
            "query_name": "Zenith Nordic",
            "family": "Zenith",
            "allowed_outcomes": [["PROVISIONAL_REVIEW", None], ["NEW_ENTITY", None]],
            "category": "unattached_region",
            "rule_path": "fuzzy_review",
            "notes": "Regional name with no parent in catalog; queues review task.",
        },
        {
            "query_name": "Dev Vendor Near Miss",
            "family": "Dev_NearMiss",
            "allowed_outcomes": [["NEW_ENTITY", None], ["PROVISIONAL_REVIEW", None]],
            "category": "review_band",
            "rule_path": "fuzzy_review",
            "notes": "Genuine review-band near miss on Dev.",
        },
    ]

    idx = 1
    for c in dev_core:
        cases.append(
            {
                "query_id": f"case_dev_{idx:03d}",
                "split": "dev",
                "query_name": c["query_name"],
                "vendor_family": c["family"],
                "allowed_outcomes": c["allowed_outcomes"],
                "expected_best_target": c.get("expected_best_target"),
                "forbidden_targets": c.get("forbidden_targets", []),
                "catalog_variant": c.get("catalog_variant"),
                "category": c["category"],
                "rule_path": c.get("rule_path", "canonical_key"),
                "notes": c["notes"],
            }
        )
        idx += 1

    while idx <= 125:
        target_distractor = DEV_DISTRACTORS[(idx - 32) % len(DEV_DISTRACTORS)]
        vid = cat_by_name[target_distractor]["id"]
        if idx % 2 == 0:
            cases.append(
                {
                    "query_id": f"case_dev_{idx:03d}",
                    "split": "dev",
                    "query_name": target_distractor,
                    "vendor_family": f"Distractor_{target_distractor}",
                    "allowed_outcomes": [["MATCH", vid]],
                    "forbidden_targets": [],
                    "category": "distractor_match",
                    "rule_path": "canonical_key",
                    "notes": "Clean distractor query.",
                }
            )
        else:
            cases.append(
                {
                    "query_id": f"case_dev_{idx:03d}",
                    "split": "dev",
                    "query_name": f"Unseen Dev Tech Solutions {idx} Corp",
                    "vendor_family": f"UnseenDev_{idx}",
                    "allowed_outcomes": [["NEW_ENTITY", None]],
                    "forbidden_targets": [vid, datadog_id, sap_id],
                    "category": "novel_entity",
                    "rule_path": "low_confidence_new",
                    "notes": "Novel vendor entity.",
                }
            )
        idx += 1

    # =========================================================================
    # HELD-OUT CASES (Total: 125; exactly 45 auto-links, 80 non-auto-links)
    # With >=30 mined adversarial nearest neighbors (JW >= 0.85)
    # =========================================================================
    held_out_auto: list[dict[str, Any]] = [
        # Deterministic Aliases
        {
            "query_name": "MSFT",
            "family": "Microsoft",
            "allowed_outcomes": [["MATCH", msft_id]],
            "category": "alias",
            "rule_path": "alias",
            "notes": "Held-out alias verified before fuzzy.",
        },
        {
            "query_name": "Salesforce.com",
            "family": "Salesforce",
            "allowed_outcomes": [["MATCH", salesforce_id]],
            "category": "alias",
            "rule_path": "alias",
            "notes": "Alias without suffix.",
        },
        {
            "query_name": "Block Inc",
            "family": "Square",
            "allowed_outcomes": [["MATCH", square_id]],
            "category": "alias",
            "rule_path": "alias",
            "notes": "Corporate rebranding alias.",
        },
        {
            "query_name": "TIM",
            "family": "Telecom Italia",
            "allowed_outcomes": [["MATCH", tim_id]],
            "category": "alias",
            "rule_path": "alias",
            "notes": "Telco acronym alias.",
        },
        {
            "query_name": "Monday",
            "family": "Monday.com",
            "allowed_outcomes": [["MATCH", monday_id]],
            "category": "alias",
            "rule_path": "alias",
            "notes": "Short brand alias.",
        },
        {
            "query_name": "J&J",
            "family": "Johnson & Johnson",
            "allowed_outcomes": [["MATCH", jnj_id]],
            "category": "alias",
            "rule_path": "alias",
            "notes": "Ampersand acronym alias.",
        },
        {
            "query_name": "BofA",
            "family": "Bank of America",
            "allowed_outcomes": [["MATCH", boa_id]],
            "category": "alias",
            "rule_path": "alias",
            "notes": "Financial acronym alias.",
        },
        # Canonical Key (Punctuation, casing, legal suffix)
        {
            "query_name": "Microsoft Corporation",
            "family": "Microsoft",
            "allowed_outcomes": [["MATCH", msft_id]],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Canonical match.",
        },
        {
            "query_name": "Salesforce, Inc.",
            "family": "Salesforce",
            "allowed_outcomes": [["MATCH", salesforce_id]],
            "category": "legal_suffix",
            "rule_path": "canonical_key",
            "notes": "Suffix strip.",
        },
        {
            "query_name": "CrowdStrike, Inc.",
            "family": "CrowdStrike",
            "allowed_outcomes": [["MATCH", crowdstrike_id]],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Canonical match.",
        },
        {
            "query_name": "Apple Inc.",
            "family": "Apple",
            "allowed_outcomes": [["MATCH", apple_id]],
            "forbidden_targets": [applied_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Apple canonical; strictly never links to Applied Materials.",
        },
        {
            "query_name": "Applied Materials, Inc.",
            "family": "Applied Materials",
            "allowed_outcomes": [["MATCH", applied_id]],
            "forbidden_targets": [apple_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Applied Materials canonical; never links to Apple.",
        },
        {
            "query_name": "US Foods, Inc.",
            "family": "US Foods",
            "allowed_outcomes": [["MATCH", us_foods_id]],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Foods is not a loose descriptor; US Foods retained intact.",
        },
        {
            "query_name": "GitHub, Inc.",
            "family": "GitHub",
            "allowed_outcomes": [["MATCH", github_id]],
            "forbidden_targets": [gitlab_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "GitHub canonical; never links to GitLab.",
        },
        {
            "query_name": "GitLab, Inc.",
            "family": "GitLab",
            "allowed_outcomes": [["MATCH", gitlab_id]],
            "forbidden_targets": [github_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "GitLab canonical; never links to GitHub.",
        },
        {
            "query_name": "Square, Inc.",
            "family": "Square",
            "allowed_outcomes": [["MATCH", square_id]],
            "forbidden_targets": [squarespace_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Square canonical; never links to Squarespace.",
        },
        {
            "query_name": "Squarespace, Inc.",
            "family": "Squarespace",
            "allowed_outcomes": [["MATCH", squarespace_id]],
            "forbidden_targets": [square_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Squarespace canonical; never links to Square.",
        },
        {
            "query_name": "Notion Labs, Inc.",
            "family": "Notion",
            "allowed_outcomes": [["MATCH", notion_id]],
            "forbidden_targets": [motion_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Notion canonical; never links to Motion.",
        },
        {
            "query_name": "Motion Corp",
            "family": "Motion",
            "allowed_outcomes": [["MATCH", motion_id]],
            "forbidden_targets": [notion_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Motion canonical; never links to Notion.",
        },
        {
            "query_name": "Sentry.io",
            "family": "Sentry",
            "allowed_outcomes": [["MATCH", sentry_id]],
            "forbidden_targets": [centric_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Sentry canonical; never links to Centric Software.",
        },
        {
            "query_name": "Centric Software Inc",
            "family": "Centric Software",
            "allowed_outcomes": [["MATCH", centric_id]],
            "forbidden_targets": [sentry_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Centric canonical; never links to Sentry.",
        },
        {
            "query_name": "MongoDB, Inc.",
            "family": "MongoDB",
            "allowed_outcomes": [["MATCH", mongodb_id]],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "MongoDB canonical.",
        },
        {
            "query_name": "ServiceNow, Inc.",
            "family": "ServiceNow",
            "allowed_outcomes": [["MATCH", servicenow_id]],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "ServiceNow canonical.",
        },
        {
            "query_name": "PagerDuty, Inc.",
            "family": "PagerDuty",
            "allowed_outcomes": [["MATCH", pagerduty_id]],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "PagerDuty canonical.",
        },
        {
            "query_name": "Cloudflare, Inc.",
            "family": "Cloudflare",
            "allowed_outcomes": [["MATCH", cloudflare_id]],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Cloudflare canonical.",
        },
        {
            "query_name": "Docker, Inc.",
            "family": "Docker",
            "allowed_outcomes": [["MATCH", docker_id]],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Docker canonical.",
        },
        {
            "query_name": "Asana, Inc.",
            "family": "Asana",
            "allowed_outcomes": [["MATCH", asana_id]],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Asana canonical.",
        },
        {
            "query_name": "Intercom, Inc.",
            "family": "Intercom",
            "allowed_outcomes": [["MATCH", intercom_id]],
            "forbidden_targets": [tim_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Intercom canonical; never links to Telecom Italia.",
        },
        {
            "query_name": "Monday.com Ltd",
            "family": "Monday.com",
            "allowed_outcomes": [["MATCH", monday_id]],
            "forbidden_targets": [sunday_id],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Monday.com canonical; never links to Sunday Security.",
        },
        {
            "query_name": "Confluent, Inc.",
            "family": "Confluent",
            "allowed_outcomes": [["MATCH", confluent_id]],
            "category": "canonical",
            "rule_path": "canonical_key",
            "notes": "Confluent canonical.",
        },
        {
            "query_name": "Johnson & Johnson",
            "family": "Johnson & Johnson",
            "allowed_outcomes": [["MATCH", jnj_id]],
            "category": "ampersand_canonical",
            "rule_path": "canonical_key",
            "notes": "Ampersand canonical.",
        },
        {
            "query_name": "Johnson and Johnson",
            "family": "Johnson & Johnson",
            "allowed_outcomes": [["MATCH", jnj_id]],
            "category": "ampersand_variant",
            "rule_path": "canonical_key",
            "notes": "Standardizes 'and' to '&' matching canonical.",
        },
        {
            "query_name": "Bank of America Corporation",
            "family": "Bank of America",
            "allowed_outcomes": [["MATCH", boa_id]],
            "category": "phrase_region",
            "rule_path": "canonical_key",
            "notes": "Region token inside phrase not stripped.",
        },
        {
            "query_name": "UK Power Networks Ltd",
            "family": "UK Power Networks",
            "allowed_outcomes": [["MATCH", uk_power_id]],
            "category": "prefix_region",
            "rule_path": "canonical_key",
            "notes": "Region token at start of name not stripped.",
        },
        {
            "query_name": "Web 3 Technologies Inc",
            "family": "Web3",
            "allowed_outcomes": [["MATCH", web3_id]],
            "category": "digit_spacing",
            "rule_path": "canonical_key",
            "notes": "Digit spacing: Web 3 vs Web3 normalized cleanly.",
        },
        # Existing Subsidiaries
        {
            "query_name": "CrowdStrike UK Ltd",
            "family": "CrowdStrike",
            "allowed_outcomes": [["MATCH", crowdstrike_uk_id]],
            "category": "existing_subsidiary",
            "rule_path": "canonical_key",
            "notes": "Matches existing sibling subsidiary.",
        },
        {
            "query_name": "Microsoft Ireland Operations Ltd",
            "family": "Microsoft",
            "allowed_outcomes": [["MATCH", msft_ireland_id]],
            "category": "existing_subsidiary",
            "rule_path": "canonical_key",
            "notes": "Matches existing Microsoft subsidiary.",
        },
        {
            "query_name": "Acme Germany",
            "family": "Acme",
            "allowed_outcomes": [["MATCH", acme_germany_id]],
            "forbidden_targets": [acme_uk_id],
            "category": "sibling_match",
            "rule_path": "canonical_key",
            "notes": "Resolves to Acme Germany, never Acme UK.",
        },
        {
            "query_name": "Acme UK",
            "family": "Acme",
            "allowed_outcomes": [["MATCH", acme_uk_id]],
            "forbidden_targets": [acme_germany_id],
            "category": "sibling_match",
            "rule_path": "canonical_key",
            "notes": "Resolves to Acme UK, never Acme Germany.",
        },
        # Subsidiary Creation
        {
            "query_name": "Salesforce UK",
            "family": "Salesforce",
            "allowed_outcomes": [["SUBSIDIARY_OF", salesforce_id]],
            "category": "subsidiary_creation",
            "rule_path": "exact_parent_region",
            "notes": "Regional subsidiary creation under Salesforce.",
        },
        {
            "query_name": "MongoDB APAC Pte Ltd",
            "family": "MongoDB",
            "allowed_outcomes": [["SUBSIDIARY_OF", mongodb_id]],
            "category": "subsidiary_creation",
            "rule_path": "exact_parent_region",
            "notes": "APAC regional subsidiary under MongoDB.",
        },
        {
            "query_name": "ServiceNow Deutschland GmbH",
            "family": "ServiceNow",
            "allowed_outcomes": [["SUBSIDIARY_OF", servicenow_id]],
            "category": "deutschland_synonym",
            "rule_path": "exact_parent_region",
            "notes": "Deutschland synonym mapped to Germany subsidiary.",
        },
        {
            "query_name": "Palantir UK Ltd",
            "family": "Palantir",
            "allowed_outcomes": [["SUBSIDIARY_OF", palantir_id]],
            "category": "subsidiary_creation",
            "rule_path": "exact_parent_region",
            "notes": "UK subsidiary under Palantir Technologies.",
        },
        # Allowlisted Loose Descriptors
        {
            "query_name": "CrowdStrike Technologies",
            "family": "CrowdStrike",
            "allowed_outcomes": [["MATCH", crowdstrike_id]],
            "category": "loose_descriptor",
            "rule_path": "loose_key",
            "notes": "Allowlisted descriptor 'technologies' resolves to parent.",
        },
        {
            "query_name": "Merged Sentry Inc",
            "family": "MergedSentry",
            "allowed_outcomes": [["MATCH", sentry_id]],
            "category": "merged_target",
            "rule_path": "canonical_key",
            "notes": "Merged vendor target follows merge chain to active survivor.",
        },
    ]
    assert len(held_out_auto) == 45, (
        f"Expected exactly 45 held_out_auto cases, got {len(held_out_auto)}"
    )

    # Mine 37 real nearest-neighbor adversarial cases (all JW >= 0.85)
    mined_adversarials = mine_adversarial_negatives(catalog, split="held_out")
    assert len(mined_adversarials) >= 30, (
        f"Expected >= 30 mined adversarials, got {len(mined_adversarials)}"
    )

    h_idx = 1
    # 1. Add held-out auto-links (exactly 45)
    for c in held_out_auto:
        cases.append(
            {
                "query_id": f"case_held_{h_idx:03d}",
                "split": "held_out",
                "query_name": c["query_name"],
                "vendor_family": c["family"],
                "allowed_outcomes": c["allowed_outcomes"],
                "forbidden_targets": c.get("forbidden_targets", []),
                "catalog_variant": c.get("catalog_variant"),
                "category": c["category"],
                "rule_path": c.get("rule_path", "canonical_key"),
                "notes": c["notes"],
            }
        )
        h_idx += 1

    # 2. Add mined nearest-neighbor adversarial cases (37 cases with JW >= 0.85)
    for adv in mined_adversarials:
        target_id = adv["best_target_id"]
        cases.append(
            {
                "query_id": f"case_held_{h_idx:03d}",
                "split": "held_out",
                "query_name": adv["query_name"],
                "vendor_family": adv["vendor_family"],
                "allowed_outcomes": [["PROVISIONAL_REVIEW", target_id], ["NEW_ENTITY", None]],
                "expected_best_target": target_id,
                "forbidden_targets": [],
                "max_jw": adv["max_jw"],
                "max_trigram": adv["max_trigram"],
                "category": "mined_adversarial_negative",
                "rule_path": "fuzzy_review",
                "notes": (
                    f"Mined nearest neighbor to {adv['best_target_name']} "
                    f"with max_jw={adv['max_jw']}."
                ),
            }
        )
        h_idx += 1

    # 3. Specific negative edge cases (Level 3, Foods Inc, Apex Nordic, Crowdstrke, Acme tie, etc.)
    specific_negatives = [
        {
            "query_name": "Level 3",
            "family": "Level",
            "allowed_outcomes": [["NEW_ENTITY", None]],
            "forbidden_targets": [level_4_id],
            "category": "digit_mismatch",
            "rule_path": "low_confidence_new",
            "notes": "Digit mismatch: {3} != {4} blocks auto-link.",
        },
        {
            "query_name": "Foods Inc",
            "family": "US Foods",
            "catalog_variant": {"exclude": ["US Foods, Inc."]},
            "allowed_outcomes": [["NEW_ENTITY", None]],
            "forbidden_targets": [us_foods_id],
            "category": "distinct_near_miss",
            "rule_path": "low_confidence_new",
            "notes": "Foods Inc distractor with catalog_variant; never auto-links to US Foods.",
        },
        {
            "query_name": "Apex Nordic",
            "family": "Apex",
            "allowed_outcomes": [["PROVISIONAL_REVIEW", None], ["NEW_ENTITY", None]],
            "category": "unattached_region",
            "rule_path": "fuzzy_review",
            "notes": "Regional name with no parent in catalog; queues review task.",
        },
        {
            "query_name": "Crowdstrke Inc",
            "family": "CrowdStrike",
            "allowed_outcomes": [["PROVISIONAL_REVIEW", crowdstrike_id], ["NEW_ENTITY", None]],
            "expected_best_target": crowdstrike_id,
            "category": "typo_review",
            "rule_path": "fuzzy_review",
            "notes": "Typo in CrowdStrike; auto-link forbidden, expected review target set.",
        },
        {
            "query_name": "Acme Corp",
            "family": "Acme",
            "allowed_outcomes": [["PROVISIONAL_REVIEW", None], ["NEW_ENTITY", None]],
            "forbidden_targets": [acme_germany_id, acme_uk_id],
            "category": "same_base_parent_tie",
            "rule_path": "fuzzy_review",
            "notes": "Acme Germany and Acme UK exist with no parent; query cannot pick sibling.",
        },
        {
            "query_name": "Pending Review Partner",
            "family": "PendingPartner",
            "allowed_outcomes": [["PROVISIONAL_REVIEW", None], ["NEW_ENTITY", None]],
            "category": "pending_review_target",
            "rule_path": "fuzzy_review",
            "notes": "Candidate vendor in pending_review status; auto-link strictly blocked.",
        },
    ]

    for sn in specific_negatives:
        cases.append(
            {
                "query_id": f"case_held_{h_idx:03d}",
                "split": "held_out",
                "query_name": sn["query_name"],
                "vendor_family": sn["family"],
                "allowed_outcomes": sn["allowed_outcomes"],
                "expected_best_target": sn.get("expected_best_target"),
                "forbidden_targets": sn.get("forbidden_targets", []),
                "catalog_variant": sn.get("catalog_variant"),
                "category": sn["category"],
                "rule_path": sn.get("rule_path", "canonical_key"),
                "notes": sn["notes"],
            }
        )
        h_idx += 1

    # 4. Fill remaining held-out cases up to 125 total (80 non-auto-links) with smoke novel entities
    while h_idx <= 125:
        cases.append(
            {
                "query_id": f"case_held_{h_idx:03d}",
                "split": "held_out",
                "query_name": f"Novel HeldOut Partner {h_idx} Corp",
                "vendor_family": f"UnseenHeldOut_{h_idx}",
                "allowed_outcomes": [["NEW_ENTITY", None]],
                "forbidden_targets": [msft_id, apple_id],
                "category": "smoke_novel_entity",
                "rule_path": "low_confidence_new",
                "notes": "Novel vendor entity in held-out split (smoke filler).",
            }
        )
        h_idx += 1

    return cases


def generate_seed_aliases(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate seed vendor_aliases rows from known enterprise canonical catalog entries."""
    aliases: list[dict[str, Any]] = []
    for item in catalog:
        for alias in item.get("aliases", []):
            if alias.lower() != item["name"].lower():
                aliases.append(
                    {
                        "id": deterministic_uuid("alias", f"{item['id']}:{alias}"),
                        "vendor_id": item["id"],
                        "alias": alias,
                        "normalized_alias": alias.strip().lower(),
                        "source": "seed",
                    }
                )
    return aliases


def main() -> None:
    """Generate and write frozen catalog snapshot and benchmark cases."""
    catalog = build_catalog()
    cases = build_benchmark_cases(catalog)
    aliases = generate_seed_aliases(catalog)

    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        for c in catalog:
            f.write(json.dumps(c) + "\n")

    with open(BENCHMARK_FILE, "w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case) + "\n")

    with open(ALIASES_SEED_FILE, "w", encoding="utf-8") as f:
        for al in aliases:
            f.write(json.dumps(al) + "\n")

    dev_cases = [c for c in cases if c["split"] == "dev"]
    held_cases = [c for c in cases if c["split"] == "held_out"]

    dev_fams = {c["vendor_family"] for c in dev_cases}
    held_fams = {c["vendor_family"] for c in held_cases}
    assert dev_fams.isdisjoint(held_fams), f"Split leakage! {dev_fams & held_fams}"

    held_auto_links = [
        c
        for c in held_cases
        if any(
            outcome[0] in ("MATCH", "SUBSIDIARY_OF") and outcome[1] is not None
            for outcome in c["allowed_outcomes"]
        )
        and not any(
            outcome[0] in ("NEW_ENTITY", "PROVISIONAL_REVIEW") for outcome in c["allowed_outcomes"]
        )
    ]
    held_non_auto = [
        c
        for c in held_cases
        if any(
            outcome[0] in ("NEW_ENTITY", "PROVISIONAL_REVIEW") for outcome in c["allowed_outcomes"]
        )
    ]

    mined_high_jw = [c for c in held_cases if c.get("max_jw", 0.0) >= 0.85]
    review_expected = [c for c in held_cases if c.get("expected_best_target") is not None]

    assert len(catalog) >= 240
    assert len(dev_cases) == 125
    assert len(held_cases) == 125
    assert len(held_auto_links) == 45, (
        f"Expected 45 held-out auto links, got {len(held_auto_links)}"
    )
    assert len(held_non_auto) == 80, (
        f"Expected 80 held-out non-auto links, got {len(held_non_auto)}"
    )
    assert len(mined_high_jw) >= 30, (
        f"Expected >= 30 mined negatives with JW>=0.85, got {len(mined_high_jw)}"
    )
    assert len(review_expected) >= 30, (
        f"Expected >= 30 review cases with expected_best_target, got {len(review_expected)}"
    )

    # Compute Wilson score intervals
    w_mined = wilson_upper_bound(0, len(mined_high_jw))
    w_auto = wilson_upper_bound(0, len(held_auto_links))
    w_non_auto = wilson_upper_bound(0, len(held_non_auto))
    w_total = wilson_upper_bound(0, len(held_cases))

    print(f"Catalog entities: {len(catalog)}")
    print(f"Benchmark cases: Dev={len(dev_cases)}, Held-Out={len(held_cases)}")
    print(
        f"Held-Out Auto-Link cases: {len(held_auto_links)} "
        f"(0-err Wilson 95% bound: {w_auto * 100:.2f}%)"
    )
    print(
        f"Held-Out Non-Auto-Link cases: {len(held_non_auto)} "
        f"(0-err Wilson 95% bound: {w_non_auto * 100:.2f}%)"
    )
    print(
        f"Held-Out Mined Negatives (JW >= 0.85): {len(mined_high_jw)} "
        f"(0-err Wilson 95% bound: {w_mined * 100:.2f}%)"
    )
    print(f"Held-Out Review Cases with expected_best_target: {len(review_expected)}")
    print(f"Total Held-Out 0-err Wilson 95% bound: {w_total * 100:.2f}%")
    print(f"Seed Aliases generated: {len(aliases)}")


if __name__ == "__main__":
    main()
