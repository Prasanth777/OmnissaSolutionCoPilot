"""Declarative, answer-aware HLD image selection over the v3 caption store.

Selection is driven by a single attribute vocabulary shared by both sides:
    access, sites, dmz, cloud, protocols, uag, external_clients, workspace_one

- requirement_profile(answers, products): questionnaire answers -> requirement.
- diagram_profile(row): a v3 caption row (visual flags + dimension fields) -> the
  same vocabulary.
- RULES: a declarative list; each rule reports a hard conflict and/or a score bonus.
  The matcher iterates RULES, so a new dimension is one rule + one extracted
  attribute, not edits across three functions.

A diagram is eligible when no rule reports a conflict. Conflicting rows are removed
up front, so the slide-topic spread needs no per-dimension pruning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

HORIZON_PAGE_TOKENS = (
    "horizon-8", "unified-access-gateway", "network-ports-horizon",
    "understand-and-troubleshoot-horizon", "environment-infrastructure-design",
    "reference-architecture-vm-specifications", "load-balancing-unified-access-gateway",
    "omnissa-horizon-blast-extreme-display-protocol",
    # Horizon Cloud Service pages (platform rule scopes them to HZC runs).
    "horizon-cloud-service-next-gen", "horizon-cloud-on-microsoft-azure",
    "what-horizon-cloud-service", "deploying-horizon-edge-gateway",
    "horizon-cloud-service-next-generation-network-ports",
)

# Page tokens that pin a diagram to one Horizon platform.
HZC_PAGE_TOKENS = (
    "horizon-cloud", "next-gen", "deploying-horizon-edge-gateway",
)
H8_PAGE_TOKENS = ("horizon-8", "network-ports-horizon-8")


def _infer_platform(text: str, page: str) -> str:
    """Classify a diagram as horizon_8 / horizon_cloud / '' (neutral)."""
    if any(tok in page for tok in HZC_PAGE_TOKENS):
        return "horizon_cloud"
    if any(tok in page for tok in H8_PAGE_TOKENS):
        return "horizon_8"
    has_hzc = "horizon cloud" in text or "horizon edge" in text or "next-gen" in text
    has_h8 = "horizon 8" in text
    if has_hzc and not has_h8:
        return "horizon_cloud"
    if has_h8 and not has_hzc:
        return "horizon_8"
    return ""

PREFERRED_TOPICS = [
    "logical_view", "single_site_design", "multisite", "cloud_pod", "block_design",
    "access_external", "cs_load_balancing", "dmz_single", "dmz_double",
    "networking_external", "networking_internal", "networking",
    "authentication", "true_sso", "connection_server",
    "app_volumes", "dem", "fslogix", "operations",
]

TOPIC_TITLE = {
    "logical_view": "Logical Architecture", "connection_server": "Connection Server",
    "cs_load_balancing": "Connection Server Load Balancing", "block_design": "Pod and Block Design",
    "cloud_pod": "Cloud Pod Architecture", "access_external": "External Access Architecture",
    "dmz_single": "Single DMZ Deployment", "dmz_double": "Double DMZ Deployment",
    "networking": "Networking", "networking_internal": "Internal Network Connections",
    "networking_external": "External Network Connections", "authentication": "Authentication",
    "true_sso": "True SSO", "single_site_design": "Single-Site Architecture",
    "multisite": "Multi-Site Architecture", "app_volumes": "App Volumes Architecture",
    "dem": "Dynamic Environment Manager Architecture", "fslogix": "FSLogix Profile Architecture",
    "operations": "Operations and Monitoring",
}

CLOUD_MAP = {
    "vmware cloud on aws": "vmc_aws", "azure vmware solution": "avs",
    "google cloud vmware engine": "gcve", "oracle cloud vmware solution": "ocvs",
    "alibaba cloud vmware service": "acvs",
}

WS1_PRODUCTS = {"workspace_one_uem", "omnissa_access", "workspace_one_access", "workspace_one"}
# Horizon 8 running on hyperscaler VMware SDDCs — distinct from Horizon Cloud Service.
VMWARE_CLOUD_TAGS = {"vmc_aws", "avs", "gcve", "ocvs", "acvs"}
_MULTI_SITE_TOPOS = {"multisite", "multisite_active_active", "multisite_active_passive", "cloud_pod"}

# Pages that are methodology/overview content, not deliverable solution architecture.
NON_SOLUTION_PAGE_TOKENS = (
    "business-drivers-use-cases-and-service-definitions",
    "workspace-one-and-horizon-reference-architecture-overview",
    "evaluation-guide",
    "compliance-14-ncsc-cloud-security-principles",
    "alignment-dora-requirements",
    "alignment-nis-2-directive",
    "alignment-nist",
    "cloud-computing-compliance-criteria-catalogue",
)
# Caption/heading phrases that mark a non-deliverable diagram (process, methodology,
# or an explicitly unsupported configuration). Excluded for every flow.
NON_SOLUTION_KEYWORDS = (
    "unsupported", "design approach", "design methodology",
    "reference architecture design methodology", "service definition",
    "business drivers", "outcome validation", "scenario definition", "scenario integration",
    "methodology", "persona", "use case definition",
    "sdlc", "security development lifecycle", "secure development",
    "incident response", "incident and response", "incident management",
    "incident response cycle", "incident management plan",
    "employee training", "certification attempts",
    "broad use cases", "range of customer needs",
    "use case 1", "use case 2", "use case 3",
    "forest", "domain tree", "domain trees", "domain a", "domain b", "domain c",
    "domain x", "domain y", "domain z", "domains and trusts", "trust relationship",
)
NON_BLAST_PROTOCOL_KEYWORDS = ("pcoip", " rdp", "rdp ")
GENERIC_CONNECTION_KEYWORDS = ("internal connection", "external connection")
# App Volumes / GPU app-delivery content — only relevant if App Volumes is in scope.
APP_VOLUMES_KEYWORDS = ("app volumes", "gpu-accelerated", "published apps on demand", "app attach")
DEM_KEYWORDS = ("dynamic environment manager", "dem ")
FSLOGIX_KEYWORDS = ("fslogix", "profile container", "office container")
PROCESS_OR_SCREENSHOT_KEYWORDS = ("dashboard", "launch flow", "process flow", "on-ramp")
# Hybrid/cloud-bursting patterns that contradict a strictly on-premises design.
HYBRID_CAPACITY_KEYWORDS = (
    "cloud capacity", "consume cloud capacity", "manage cloud capacity",
    "cloud-hosted capacity", "cloud bursting", "burst to cloud",
)
SINGLE_SITE_EXCLUDED_KEYWORDS = (
    "active-passive", "active passive", "active-active", "active active",
    "stretched cluster", "stretched vsan", "vsan stretched", "multi-datacentre",
    "multi-datacenter", "site failure", "preferred site", "secondary site",
    "witness site", "data site to data site", "site 1", "site 2", "site 3",
)
MULTI_SITE_INFERENCE_TERMS = SINGLE_SITE_EXCLUDED_KEYWORDS + (
    "multi-site", "multisite", "multi site",
)

# Manual metadata corrections for known diagrams whose captured flags are wrong
# or incomplete (e.g. products visible in the image but missing from
# components_shown). Keyed by (page_url token, caption substring), both lowercase.
ATTRIBUTE_OVERRIDES: list[tuple[str, str, dict]] = [
    # The canonical H8 logical diagram visually includes App Volumes, DEM,
    # ThinApps, and Access panels; only offer it when those products are in scope.
    (
        "resource/horizon-8-architecture",
        "horizon 8 logical components",
        {"components_shown": ["connection_server", "uag", "horizon_agent", "app_volumes", "dynamic_environment_manager", "workspace_one_access"]},
    ),
    (
        "resource/what-horizon-8",
        "external connection with blast network ports",
        {"dmz_design": "single"},
    ),
    (
        "resource/horizon-8-architecture",
        "external connection with blast network ports",
        {"dmz_design": "single"},
    ),
]


def apply_attribute_overrides(row: dict) -> dict:
    page = str(row.get("page_url", "")).lower()
    caption = f"{row.get('caption', '')} {row.get('figure_caption', '')}".lower()
    merged = row
    for page_token, caption_token, attrs in ATTRIBUTE_OVERRIDES:
        if page_token in page and caption_token in caption:
            if merged is row:
                merged = dict(row)
            merged.update(attrs)
    return merged


def _canon(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}".lower()
    except Exception:
        return (url or "").lower()

def _narrow_text(row: dict) -> str:
    return " ".join([
        str(row.get("title", "")),
        str(row.get("figure_caption", "")),
        str(row.get("caption", "")),
        str(row.get("section_heading", "")),
        str(row.get("context_text", "")),
        str(row.get("embed_text", "")),
        str(row.get("page_url", "")),
    ]).lower()

def _infer_site_from_text(text: str, existing: str = "") -> str:
    t = f" {text.lower()} "
    if "cloud pod" in t or " cpa " in t:
        return "cloud_pod"
    if "active-active" in t or "active/active" in t or "active active" in t:
        return "multisite_active_active"
    if "active-passive" in t or "active/passive" in t or "active passive" in t:
        return "multisite_active_passive"
    if any(term in t for term in MULTI_SITE_INFERENCE_TERMS):
        return "multisite"
    if "single-site" in t or "single site" in t:
        return "single_site"
    return existing or ""

def requirement_profile(answers: dict, selected_products=None) -> dict:
    a = {k: (str(v) or "").lower() for k, v in answers.items()}
    products = set(selected_products or [])

    access_raw = a.get("access_type", "")
    access = (
        "internal" if "internal users only" in access_raw
        else "external" if "external users only" in access_raw
        else "both" if "both" in access_raw else ""
    )

    site_raw = a.get("site_topology", "")
    avail_raw = a.get("availability_requirements", "")
    is_multi = (
        "multi-site" in site_raw or "multisite" in site_raw
        or "multi-site" in avail_raw or "active/active" in avail_raw or "active/passive" in avail_raw
    )
    is_single = (not is_multi) and (
        "single site" in site_raw or "single site" in avail_raw or "n+1" in avail_raw
    )
    sites = "multi" if is_multi else "single" if is_single else ""

    dmz_raw = a.get("horizon_dmz_design", "")
    dmz = (
        "double" if "double" in dmz_raw
        # A per-site DMZ pair follows the single-DMZ pattern at each site.
        else "single" if ("single" in dmz_raw or "per-site" in dmz_raw or "per site" in dmz_raw)
        else "none" if ("no dmz" in dmz_raw or "internal only" in dmz_raw)
        else ""
    )

    lb_raw = a.get("load_balancer", "")
    lb_place_raw = a.get("load_balancer_placement", "")
    has_dedicated_lb = lb_raw.startswith("yes") or "load balancer" in a.get("horizon_external_access", "")
    if "built-in" in lb_raw or "no load balancer" in lb_raw:
        lb = "builtin"
    elif has_dedicated_lb:
        lb = (
            "both" if "both" in lb_place_raw
            else "uag" if "uag" in lb_place_raw
            else "cs" if "connection server" in lb_place_raw
            else "any"
        )
    else:
        lb = ""

    hosting = a.get("hosting_strategy", "")
    on_prem = ("on-prem" in hosting or "premises" in hosting)
    track = a.get("horizon_8_arch_track", "")
    cloud = ""
    for token, tag in CLOUD_MAP.items():
        if token in track:
            cloud = tag
            break
    if not cloud and "horizon_cloud" in products:
        cloud = "horizon_cloud"

    provider_raw = a.get("horizon_cloud_provider", "")
    hzc_provider = (
        "azure" if "azure" in provider_raw
        else "aws" if ("amazon" in provider_raw or "aws" in provider_raw or "ec2" in provider_raw)
        else ""
    )

    hzc_track = a.get("horizon_cloud_arch_track", "")
    hzc_gen = (
        "next_gen" if "next-gen" in hzc_track or "next gen" in hzc_track
        else "first_gen" if "first-gen" in hzc_track or "first gen" in hzc_track
        else ""
    )

    protocols_allowed = {"blast"}

    mfa_provider = a.get("mfa_provider", "")
    ws1_in_scope = bool(products & WS1_PRODUCTS) or ("workspace one" in mfa_provider) or ("ws1" in mfa_provider)
    profile_strategy = a.get("dem_profile_strategy", "")
    operations_detail = bool(a.get("monitoring_logging", "").replace("unknown / to be confirmed", "").strip())

    return {
        "access": access, "sites": sites, "dmz": dmz, "lb": lb, "on_prem": on_prem,
        "cloud": cloud, "protocols_allowed": protocols_allowed, "ws1_in_scope": ws1_in_scope,
        "av_in_scope": "app_volumes" in products,
        "dem_in_scope": "dynamic_environment_manager" in products,
        "fslogix_in_scope": "fslogix" in profile_strategy or "dem + fslogix" in profile_strategy,
        "operations_detail": operations_detail,
        "horizon_cloud_only": "horizon_cloud" in products and "horizon_8" not in products,
        "horizon_8_only": "horizon_8" in products and "horizon_cloud" not in products,
        "hzc_provider": hzc_provider,
        "hzc_gen": hzc_gen,
    }

def diagram_profile(row: dict) -> dict:
    row = apply_attribute_overrides(row)
    site_topo = (row.get("site_topology", "") or "")
    text = _narrow_text(row)
    site_topo = _infer_site_from_text(text, site_topo)
    if site_topo == "single_site":
        sites = "single"
    elif site_topo in _MULTI_SITE_TOPOS:
        sites = "multi"
    else:
        sites = ""

    protocols = set(row.get("protocols_visible") or row.get("protocols") or [])
    if "all display protocol" in text:
        protocols |= {"blast", "pcoip", "rdp"}
    if "blast" in text:
        protocols.add("blast")
    if "pcoip" in text:
        protocols.add("pcoip")
    if re.search(r"\brdp\b", text):
        protocols.add("rdp")

    uag = (row.get("has_uag") is True) or bool(row.get("uag_present"))

    # Trust the visual DMZ flag over the (often unset) dmz_design field: a diagram
    # that visibly draws a DMZ is at least a single-DMZ layout.
    dmz = row.get("dmz_design", "none") or "none"
    if dmz == "none" and row.get("has_dmz") is True:
        dmz = "double" if ("double dmz" in text or "double-dmz" in text) else "single"

    # Where is the load balancer pointing in this diagram?
    lb_visible = bool(row.get("load_balancer")) or "load balanc" in text or "load-balanc" in text
    lb_target = ""
    if lb_visible:
        mentions_uag = uag or "unified access gateway" in text or re.search(r"\buag\b", text)
        mentions_cs = "connection server" in text or str(row.get("topic", "")) == "cs_load_balancing"
        if mentions_cs and not mentions_uag:
            lb_target = "cs"
        elif mentions_uag and not mentions_cs:
            lb_target = "uag"
        elif mentions_uag and mentions_cs:
            lb_target = "both"

    components = {str(c).lower() for c in (row.get("components_shown") or [])}

    mentions_azure = "azure" in text or "vnet" in text
    mentions_aws = "amazon" in text or re.search(r"\baws\b|\bec2\b|\bvpc\b", text)
    hzc_provider = (
        "azure" if mentions_azure and not mentions_aws
        else "aws" if mentions_aws and not mentions_azure
        else ""
    )

    page = _canon(str(row.get("page_url", "")))
    hzc_gen = (
        "first_gen" if "first-gen" in page
        else "next_gen" if "next-gen" in page or "next-generation" in page
        else ""
    )

    return {
        "access": row.get("access_scope", "") or "",
        "sites": sites,
        "dmz": dmz,
        "platform": _infer_platform(text, page),
        "hzc_gen": hzc_gen,
        "hzc_provider": hzc_provider,
        "cloud": row.get("cloud_platform", "") or "",
        "protocols": protocols,
        "uag": uag,
        "lb_target": lb_target,
        "components": components,
        "external_clients": row.get("has_external_clients") is True,
        "workspace_one": row.get("has_workspace_one") is True,
        "text": text,
    }

@dataclass(frozen=True)
class Rule:
    name: str
    conflict: Callable[[dict, dict], bool]
    bonus: Callable[[dict, dict], int] = field(default=lambda req, dia: 0)

RULES: list[Rule] = [
    Rule(
        "access",
        conflict=lambda req, dia: (
            (req["access"] == "internal" and (
                dia["access"] in ("external", "both") or dia["uag"] or dia["external_clients"]
                or dia["dmz"] in ("single", "double")
            ))
            or (req["access"] == "external" and dia["access"] == "internal")
        ),
        bonus=lambda req, dia: 6 if req.get("access") and dia["access"] == req.get("access") else 0,
    ),
    Rule(
        "sites",
        conflict=lambda req, dia: (
            (req["sites"] == "single" and dia["sites"] == "multi")
            or (req["sites"] == "multi" and dia["sites"] == "single")
            or (req["sites"] == "single" and any(k in dia["text"] for k in SINGLE_SITE_EXCLUDED_KEYWORDS))
        ),
    ),
    Rule(
        "dmz",
        conflict=lambda req, dia: (
            (req["dmz"] == "single" and dia["dmz"] == "double")
            or (req["dmz"] == "double" and dia["dmz"] == "single")
            or (req["dmz"] == "none" and dia["dmz"] in ("single", "double"))
        ),
    ),
    Rule(
        "horizon_platform",
        conflict=lambda req, dia: (
            (req.get("horizon_cloud_only") and dia.get("platform") == "horizon_8")
            or (req.get("horizon_8_only") and dia.get("platform") == "horizon_cloud")
        ),
        bonus=lambda req, dia: (
            10 if (req.get("horizon_cloud_only") and dia.get("platform") == "horizon_cloud")
            or (req.get("horizon_8_only") and dia.get("platform") == "horizon_8")
            else 0
        ),
    ),
    Rule(
        "hzc_generation",
        conflict=lambda req, dia: (
            bool(req.get("hzc_gen")) and bool(dia.get("hzc_gen"))
            and dia["hzc_gen"] != req["hzc_gen"]
        ),
        bonus=lambda req, dia: 8 if req.get("hzc_gen") and dia.get("hzc_gen") == req.get("hzc_gen") else 0,
    ),
    Rule(
        "on_prem_hybrid_capacity",
        conflict=lambda req, dia: req.get("on_prem", False) and any(k in dia["text"] for k in HYBRID_CAPACITY_KEYWORDS),
    ),
    Rule(
        "hzc_provider",
        conflict=lambda req, dia: (
            bool(req.get("hzc_provider")) and bool(dia.get("hzc_provider"))
            and dia.get("platform") == "horizon_cloud"
            and dia["hzc_provider"] != req["hzc_provider"]
        ),
        bonus=lambda req, dia: (
            8 if req.get("hzc_provider") and dia.get("hzc_provider") == req.get("hzc_provider") else 0
        ),
    ),
    Rule(
        "lb_placement",
        conflict=lambda req, dia: (
            # Customer load balancer sits in front of UAG only: exclude
            # Connection Server load-balancing diagrams (and vice versa).
            (req.get("lb") == "uag" and dia.get("lb_target") == "cs")
            or (req.get("lb") == "cs" and dia.get("lb_target") == "uag")
            # No dedicated load balancer: exclude diagrams whose subject is a
            # dedicated load-balancing tier.
            or (req.get("lb") == "builtin" and dia.get("lb_target") in ("cs", "uag", "both"))
        ),
        bonus=lambda req, dia: (
            12 if req.get("lb") in ("uag", "cs", "both") and dia.get("lb_target")
            and (req.get("lb") == "both" or dia.get("lb_target") in (req.get("lb"), "both"))
            else 0
        ),
    ),
    Rule(
        "component_scope",
        conflict=lambda req, dia: (
            ((not req.get("av_in_scope")) and bool(dia.get("components", set()) & {"app_volumes", "writable_volumes"}))
            or ((not req.get("dem_in_scope")) and bool(dia.get("components", set()) & {"dynamic_environment_manager", "dem"}))
            or ((not req.get("ws1_in_scope")) and bool(dia.get("components", set()) & {"workspace_one_access", "workspace_one_uem", "workspace_one"}))
        ),
    ),
    Rule(
        "cloud",
        conflict=lambda req, dia: (
            (req["on_prem"] and bool(dia["cloud"]))
            # Horizon Cloud Service runs must not show Horizon 8 on VMware SDDC diagrams.
            or (req.get("horizon_cloud_only") and dia["cloud"] in VMWARE_CLOUD_TAGS)
            or (bool(req["cloud"]) and bool(dia["cloud"]) and dia["cloud"] != req["cloud"])
        ),
        bonus=lambda req, dia: 30 if req.get("cloud") and dia["cloud"] == req.get("cloud") else 0,
    ),
    Rule(
        "protocols",
        conflict=lambda req, dia: bool(dia["protocols"] - req["protocols_allowed"]),
    ),
    Rule(
        "blast_only",
        conflict=lambda req, dia: any(k in dia["text"] for k in NON_BLAST_PROTOCOL_KEYWORDS),
        bonus=lambda req, dia: 18 if "blast" in dia["text"] else 0,
    ),
    Rule(
        "generic_network_connection",
        conflict=lambda req, dia: any(k in dia["text"] for k in GENERIC_CONNECTION_KEYWORDS) and "blast" not in dia["text"],
    ),
    Rule(
        "workspace_one",
        conflict=lambda req, dia: (not req["ws1_in_scope"]) and dia["workspace_one"],
    ),
    Rule(
        "content_type",
        conflict=lambda req, dia: any(k in dia["text"] for k in NON_SOLUTION_KEYWORDS),
    ),
    Rule(
        "app_volumes_scope",
        conflict=lambda req, dia: (not req.get("av_in_scope")) and any(k in dia["text"] for k in APP_VOLUMES_KEYWORDS),
    ),
    Rule(
        "dem_scope",
        conflict=lambda req, dia: (not req.get("dem_in_scope")) and any(k in dia["text"] for k in DEM_KEYWORDS),
    ),
    Rule(
        "fslogix_scope",
        conflict=lambda req, dia: (not req.get("fslogix_in_scope")) and any(k in dia["text"] for k in FSLOGIX_KEYWORDS),
    ),
    Rule(
        "process_or_screenshot_scope",
        conflict=lambda req, dia: (not req.get("operations_detail")) and any(k in dia["text"] for k in PROCESS_OR_SCREENSHOT_KEYWORDS),
    ),
]

def _eligible(req: dict, dia: dict) -> bool:
    return not any(rule.conflict(req, dia) for rule in RULES)


def figure_conflicts(row: dict, answers: dict, selected_products=None) -> bool:
    """Single source of truth for 'does this diagram contradict the answers?'.

    Used by the PPT/DOCX builders and the UI so every output filters identically.
    """
    req = requirement_profile(answers, selected_products or [])
    dia = diagram_profile(row)
    return not _eligible(req, dia)


def conflicting_rules(row: dict, answers: dict, selected_products=None) -> list[str]:
    """Names of the rules a diagram violates — for UI explanations/debugging."""
    req = requirement_profile(answers, selected_products or [])
    dia = diagram_profile(row)
    return [rule.name for rule in RULES if rule.conflict(req, dia)]


def figure_attribute_tags(row: dict) -> list[str]:
    """Short human-readable attribute tags for the diagram review UI."""
    dia = diagram_profile(row)
    tags: list[str] = []
    if dia.get("platform"):
        tags.append("Horizon Cloud" if dia["platform"] == "horizon_cloud" else "Horizon 8")
    if dia.get("sites"):
        tags.append("Multi-site" if dia["sites"] == "multi" else "Single-site")
    if dia.get("dmz") not in ("", "none"):
        tags.append(f"{dia['dmz'].title()} DMZ")
    if dia.get("uag"):
        tags.append("UAG")
    lb_target = dia.get("lb_target")
    if lb_target:
        tags.append({"cs": "LB: Connection Servers", "uag": "LB: UAG", "both": "LB: UAG + CS"}[lb_target])
    if dia.get("access"):
        tags.append(f"Access: {dia['access']}")
    if dia.get("protocols"):
        tags.append("Protocols: " + ", ".join(sorted(dia["protocols"])))
    comps = dia.get("components") or set()
    scoped = comps & {"app_volumes", "dynamic_environment_manager", "workspace_one_access", "workspace_one_uem"}
    for comp in sorted(scoped):
        tags.append("Shows: " + comp.replace("_", " ").title())
    if dia.get("cloud"):
        tags.append(f"Cloud: {dia['cloud'].upper()}")
    return tags

def _relevant(row: dict, ref_set: set) -> bool:
    pu = _canon(row.get("page_url", ""))
    if any(tok in pu for tok in NON_SOLUTION_PAGE_TOKENS):
        return False
    return pu in ref_set or any(tok in pu for tok in HORIZON_PAGE_TOKENS)

def _score(row: dict, dia: dict, ref_set: set, req: dict) -> int:
    s = 0
    page_url = _canon(row.get("page_url", ""))
    if page_url in ref_set:
        s += 20
    if row.get("figure_caption"):
        s += 5
    if row.get("diagram_kind") == "architecture_diagram":
        s += 3
    text = dia.get("text", "")
    if any(k in text for k in ("overall", "high level", "logical architecture", "logical components")):
        s += 12
    if req.get("access") in ("external", "both") and any(k in text for k in ("unified access gateway", "uag", "dmz", "load balancing")):
        s += 10
    if req.get("sites") == "multi" and any(k in text for k in ("multi-site", "multisite", "active-active", "active-passive", "cloud pod")):
        s += 10
    if req.get("sites") == "single" and any(k in text for k in ("single-site", "single site", "scaled horizon pod")):
        s += 8
    if req.get("av_in_scope") and any(k in text for k in APP_VOLUMES_KEYWORDS):
        s += 9
    if req.get("dem_in_scope") and any(k in text for k in DEM_KEYWORDS):
        s += 9
    if "blast" in text or "blast-extreme" in page_url:
        s += 45
    if "omnissa-horizon-blast-extreme-display-protocol" in page_url:
        s += 55
    if "internal connection" in text and "blast" not in text:
        s -= 25
    if "pod and block" in text or "block design" in text or "scaled horizon pod" in text:
        s += 20
    if req.get("access") in ("external", "both") and ("load balanc" in text or "load-balanc" in page_url):
        s += 24
    if req.get("dmz") and dia.get("dmz") == req.get("dmz"):
        s += 18
    for rule in RULES:
        s += rule.bonus(req, dia)
    return s


_CONTENT_KEY_CACHE: dict[str, tuple[str, str]] = {}


def image_content_keys(local_path: str) -> tuple[str, str]:
    """(md5, perceptual-hash) of an image file, for duplicate detection.

    The md5 catches byte-identical images republished under different URLs;
    the perceptual hash (16x16 grayscale average hash) additionally catches
    the same diagram re-encoded or resized. Returns ('', '') if unreadable.
    """
    cached = _CONTENT_KEY_CACHE.get(local_path)
    if cached is not None:
        return cached
    try:
        import hashlib

        data = Path(local_path).read_bytes()
        md5 = hashlib.md5(data).hexdigest()
    except Exception:
        _CONTENT_KEY_CACHE[local_path] = ("", "")
        return ("", "")
    ahash = ""
    try:
        from PIL import Image

        with Image.open(local_path) as im:
            gray = im.convert("L").resize((16, 16))
            pixels = list(gray.getdata())
            avg = sum(pixels) / len(pixels)
            bits = "".join("1" if p > avg else "0" for p in pixels)
            ahash = f"{int(bits, 2):064x}"
    except Exception:
        pass
    _CONTENT_KEY_CACHE[local_path] = (md5, ahash)
    return (md5, ahash)


def _image_signature(row: dict) -> str:
    text = _narrow_text(row)
    text = re.sub(r"\bfigure\s+\d+\b", " ", text)
    text = re.sub(r"\btable\s+\d+\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    if text:
        return text[:120]
    image_url = str(row.get("image_url") or row.get("local_path") or "")
    return _canon(image_url)

def _load_arch_rows(store) -> list:
    rows = []
    for r in store._load_caption_rows():
        if r.get("image_type") != "architecture_diagram" or r.get("keep") is False:
            continue
        lp = str(r.get("local_path", ""))
        if lp and Path(lp).exists():
            rows.append(r)
    return rows

def select_hld_images(store, selected_products, answers, reference_urls, limit: int = 10) -> list:
    logger = getattr(store, "logger", None)
    rows = _load_arch_rows(store)
    ref_set = {_canon(u) for u in (reference_urls or [])}

    has_horizon = any(key in selected_products for key in ("horizon_8", "horizon_cloud"))
    if not has_horizon:
        pool = [r for r in rows if _canon(r.get("page_url", "")) in ref_set] or rows
        pool = sorted(pool, key=lambda r: _score(r, diagram_profile(r), ref_set, {}), reverse=True)
        out = []
        for r in pool[:limit]:
            r = dict(r)
            r["slide_title"] = r.get("caption") or r.get("title")
            out.append(r)
        return out

    req = requirement_profile(answers, selected_products)

    scored: list[tuple[int, dict, dict]] = []
    excluded_by_rule: dict[str, int] = {}
    for r in rows:
        if not _relevant(r, ref_set):
            continue
        dia = diagram_profile(r)
        violated = [rule.name for rule in RULES if rule.conflict(req, dia)]
        if violated:
            for name in violated:
                excluded_by_rule[name] = excluded_by_rule.get(name, 0) + 1
            continue
        scored.append((_score(r, dia, ref_set, req), r, dia))

    if logger:
        req_summary = {k: v for k, v in req.items() if v not in ("", False, set(), None)}
        req_summary["protocols_allowed"] = sorted(req.get("protocols_allowed", set()))
        logger.info("Figure selection: requirement profile=%s", req_summary)
        logger.info(
            "Figure selection: %d candidates eligible, excluded by rule=%s",
            len(scored), excluded_by_rule or "{}",
        )

    selected, used, used_signatures = [], set(), set()
    used_content_keys: set[str] = set()

    def _is_duplicate_content(local_path: str) -> bool:
        md5, ahash = image_content_keys(local_path)
        keys = {k for k in (md5, ahash) if k}
        if keys & used_content_keys:
            return True
        used_content_keys.update(keys)
        return False

    by_topic: dict[str, list[tuple[int, dict]]] = {}
    for s, r, _dia in scored:
        by_topic.setdefault(r.get("topic", ""), []).append((s, r))
    for cands in by_topic.values():
        cands.sort(key=lambda x: x[0], reverse=True)

    # Order topics for the slide spread. Normally follow the curated PREFERRED order.
    # When a specific cloud is selected, float cloud-matching topics to the front so
    # cloud-specific diagrams aren't crowded out by neutral core topics.
    free_form = [t for t in by_topic if t not in PREFERRED_TOPICS]
    if req.get("cloud"):
        cloud_first = [
            t for t in by_topic
            if any(r.get("cloud_platform") == req["cloud"] for _s, r in by_topic[t])
        ]
        cloud_first.sort(key=lambda t: by_topic[t][0][0], reverse=True)
        rest = [t for t in (PREFERRED_TOPICS + free_form) if t not in cloud_first]
        topic_order = cloud_first + rest
    else:
        topic_order = PREFERRED_TOPICS + free_form
    if req.get("dmz") == "single":
        topic_order = [topic for topic in topic_order if topic != "dmz_double"]
        blocked_topics = {"dmz_double"}
    elif req.get("dmz") == "double":
        topic_order = [topic for topic in topic_order if topic != "dmz_single"]
        blocked_topics = {"dmz_single"}
    else:
        topic_order = [topic for topic in topic_order if topic not in {"dmz_single", "dmz_double"}]
        blocked_topics = {"dmz_single", "dmz_double"}
    # Do not dedicate a slide/figure to Connection Server load balancing when the
    # customer load balancer fronts UAG only (or there is no dedicated LB).
    if req.get("lb") in ("uag", "builtin"):
        topic_order = [topic for topic in topic_order if topic != "cs_load_balancing"]
        blocked_topics.add("cs_load_balancing")

    for topic in topic_order:
        if len(selected) >= limit:
            break
        for _s, r in by_topic.get(topic, []):
            lp = str(r.get("local_path", ""))
            sig = _image_signature(r)
            if lp in used or sig in used_signatures:
                continue
            if _is_duplicate_content(lp):
                continue
            chosen = dict(r)
            chosen["slide_title"] = TOPIC_TITLE.get(topic, r.get("caption") or r.get("title"))
            selected.append(chosen)
            used.add(lp)
            used_signatures.add(sig)
            break

    if len(selected) < limit:
        leftovers = sorted(
            (
                t for t in scored
                if str(t[1].get("local_path", "")) not in used
                and _image_signature(t[1]) not in used_signatures
                and t[1].get("topic", "") not in blocked_topics
            ),
                           key=lambda x: x[0], reverse=True)
        for _s, r, _dia in leftovers:
            if _is_duplicate_content(str(r.get("local_path", ""))):
                continue
            chosen = dict(r)
            chosen["slide_title"] = TOPIC_TITLE.get(r.get("topic", ""), r.get("caption") or r.get("title"))
            selected.append(chosen)
            used.add(str(r.get("local_path", "")))
            used_signatures.add(_image_signature(r))
            if len(selected) >= limit:
                break

    if logger:
        for idx, r in enumerate(selected[:limit], start=1):
            logger.info(
                "Figure selected %d/%d: '%s' | topic=%s | page=%s",
                idx, min(len(selected), limit),
                r.get("caption") or r.get("title"),
                r.get("topic", ""),
                str(r.get("page_url", "")).split("/")[-1],
            )

    return selected[:limit]
