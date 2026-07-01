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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

HORIZON_PAGE_TOKENS = (
    "horizon-8", "unified-access-gateway", "network-ports-horizon",
    "understand-and-troubleshoot-horizon", "environment-infrastructure-design",
    "reference-architecture-vm-specifications", "load-balancing-unified-access-gateway",
)

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
_MULTI_SITE_TOPOS = {"multisite", "multisite_active_active", "multisite_active_passive", "cloud_pod"}

# Pages that are methodology/overview content, not deliverable solution architecture.
NON_SOLUTION_PAGE_TOKENS = (
    "business-drivers-use-cases-and-service-definitions",
    "workspace-one-and-horizon-reference-architecture-overview",
)
# Caption/heading phrases that mark a non-deliverable diagram (process, methodology,
# or an explicitly unsupported configuration). Excluded for every flow.
NON_SOLUTION_KEYWORDS = (
    "unsupported", "design approach", "design methodology",
    "reference architecture design methodology", "service definition",
    "business drivers", "outcome validation", "scenario definition", "scenario integration",
    "methodology", "persona", "use case definition",
)
# App Volumes / GPU app-delivery content — only relevant if App Volumes is in scope.
APP_VOLUMES_KEYWORDS = ("app volumes", "gpu-accelerated", "published apps on demand", "app attach")
DEM_KEYWORDS = ("dynamic environment manager", "dem ")
FSLOGIX_KEYWORDS = ("fslogix", "profile container", "office container")
PROCESS_OR_SCREENSHOT_KEYWORDS = ("dashboard", "launch flow", "process flow", "on-ramp")

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
    ]).lower()

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
        else "single" if "single" in dmz_raw
        else "none" if ("no dmz" in dmz_raw or "internal only" in dmz_raw)
        else ""
    )

    hosting = a.get("hosting_strategy", "")
    on_prem = ("on-prem" in hosting or "premises" in hosting)
    track = a.get("horizon_8_arch_track", "")
    cloud = ""
    for token, tag in CLOUD_MAP.items():
        if token in track:
            cloud = tag
            break

    scope = a.get("horizon_protocol_scope", "blast extreme only")
    if "rdp" in scope:
        protocols_allowed = {"blast", "pcoip", "rdp"}
    elif "pcoip" in scope:
        protocols_allowed = {"blast", "pcoip"}
    else:
        protocols_allowed = {"blast"}

    mfa_provider = a.get("mfa_provider", "")
    ws1_in_scope = bool(products & WS1_PRODUCTS) or ("workspace one" in mfa_provider) or ("ws1" in mfa_provider)
    profile_strategy = a.get("dem_profile_strategy", "")
    operations_detail = bool(a.get("monitoring_logging", "").replace("unknown / to be confirmed", "").strip())

    return {
        "access": access, "sites": sites, "dmz": dmz, "on_prem": on_prem,
        "cloud": cloud, "protocols_allowed": protocols_allowed, "ws1_in_scope": ws1_in_scope,
        "av_in_scope": "app_volumes" in products,
        "dem_in_scope": "dynamic_environment_manager" in products,
        "fslogix_in_scope": "fslogix" in profile_strategy or "dem + fslogix" in profile_strategy,
        "operations_detail": operations_detail,
    }

def diagram_profile(row: dict) -> dict:
    site_topo = (row.get("site_topology", "") or "")
    if site_topo == "single_site":
        sites = "single"
    elif site_topo in _MULTI_SITE_TOPOS:
        sites = "multi"
    else:
        sites = ""

    protocols = set(row.get("protocols_visible") or row.get("protocols") or [])
    if "all display protocol" in _narrow_text(row):
        protocols |= {"blast", "pcoip", "rdp"}

    return {
        "access": row.get("access_scope", "") or "",
        "sites": sites,
        "dmz": row.get("dmz_design", "none") or "none",
        "cloud": row.get("cloud_platform", "") or "",
        "protocols": protocols,
        "uag": (row.get("has_uag") is True) or bool(row.get("uag_present")),
        "external_clients": row.get("has_external_clients") is True,
        "workspace_one": row.get("has_workspace_one") is True,
        "text": _narrow_text(row),
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
        "cloud",
        conflict=lambda req, dia: (
            (req["on_prem"] and bool(dia["cloud"]))
            or (bool(req["cloud"]) and bool(dia["cloud"]) and dia["cloud"] != req["cloud"])
        ),
        bonus=lambda req, dia: 30 if req.get("cloud") and dia["cloud"] == req.get("cloud") else 0,
    ),
    Rule(
        "protocols",
        conflict=lambda req, dia: bool(dia["protocols"] - req["protocols_allowed"]),
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

def _relevant(row: dict, ref_set: set) -> bool:
    pu = _canon(row.get("page_url", ""))
    if any(tok in pu for tok in NON_SOLUTION_PAGE_TOKENS):
        return False
    return pu in ref_set or any(tok in pu for tok in HORIZON_PAGE_TOKENS)

def _score(row: dict, dia: dict, ref_set: set, req: dict) -> int:
    s = 0
    if _canon(row.get("page_url", "")) in ref_set:
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
    for rule in RULES:
        s += rule.bonus(req, dia)
    return s

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
    rows = _load_arch_rows(store)
    ref_set = {_canon(u) for u in (reference_urls or [])}

    if "horizon_8" not in selected_products:
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
    for r in rows:
        if not _relevant(r, ref_set):
            continue
        dia = diagram_profile(r)
        if not _eligible(req, dia):
            continue
        scored.append((_score(r, dia, ref_set, req), r, dia))

    selected, used = [], set()
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

    for topic in topic_order:
        if len(selected) >= limit:
            break
        for _s, r in by_topic.get(topic, []):
            lp = str(r.get("local_path", ""))
            if lp in used:
                continue
            chosen = dict(r)
            chosen["slide_title"] = TOPIC_TITLE.get(topic, r.get("caption") or r.get("title"))
            selected.append(chosen)
            used.add(lp)
            break

    if len(selected) < limit:
        leftovers = sorted((t for t in scored if str(t[1].get("local_path", "")) not in used),
                           key=lambda x: x[0], reverse=True)
        for _s, r, _dia in leftovers:
            chosen = dict(r)
            chosen["slide_title"] = TOPIC_TITLE.get(r.get("topic", ""), r.get("caption") or r.get("title"))
            selected.append(chosen)
            used.add(str(r.get("local_path", "")))
            if len(selected) >= limit:
                break

    return selected[:limit]
