"""Eval harness: run structured image selection over canonical flows and check
the selected diagrams satisfy each flow's dimension expectations.

Usage:
    python scripts/eval_image_selection.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from sa_hld_bot.azure_foundry import AzureFoundryClient  # noqa: E402
from sa_hld_bot.config import load_settings  # noqa: E402
from sa_hld_bot.image_select import select_hld_images  # noqa: E402
from sa_hld_bot.rag import TechZoneRagStore  # noqa: E402

# Reference URLs mirror app.derive_solution_references for a horizon_8 deck.
H8_REFS = [
    "https://techzone.omnissa.com/resource/horizon-8-architecture",
    "https://techzone.omnissa.com/resource/network-ports-horizon-8",
    "https://techzone.omnissa.com/resource/understand-and-troubleshoot-horizon-connections",
    "https://techzone.omnissa.com/resource/environment-infrastructure-design",
    "https://techzone.omnissa.com/resource/reference-architecture-vm-specifications",
    "https://techzone.omnissa.com/resource/unified-access-gateway-architecture",
]

MULTI_SITE_TERMS = (
    "multi-site", "multisite", "multi site", "active-active", "active/active",
    "active-passive", "active/passive", "stretched cluster", "stretched vsan",
    "preferred site", "secondary site", "witness site", "data site to data site",
)

def row_text(row: dict) -> str:
    return " ".join(str(row.get(k, "")) for k in (
        "title", "caption", "figure_caption", "section_heading", "embed_text", "page_url"
    )).lower()

FLOWS = [
    {
        "name": "internal / single / on-prem",
        "answers": {"access_type": "Internal users only", "site_topology": "Single site",
                    "hosting_strategy": "On-premises", "horizon_protocol_scope": "Blast Extreme only"},
        "forbid": lambda r: r.get("uag_present") or r.get("dmz_design") in ("single", "double")
                            or r.get("access_scope") in ("external", "both") or r.get("cloud_platform")
                            or r.get("site_topology") in ("multisite", "multisite_active_active", "multisite_active_passive", "cloud_pod")
                            or any(term in row_text(r) for term in MULTI_SITE_TERMS)
                            or any(k in (r.get("figure_caption", "") or "").lower()
                                   for k in ("unsupported", "design approach", "design methodology",
                                             "app volumes", "gpu-accelerated", "service definition")),
        "want_any": lambda r: r.get("access_scope") in ("internal", ""),
    },
    {
        "name": "external / single / on-prem",
        "answers": {"access_type": "External users only", "site_topology": "Single site",
                    "hosting_strategy": "On-premises", "horizon_protocol_scope": "Blast Extreme only"},
        "forbid": lambda r: r.get("cloud_platform") or r.get("access_scope") == "internal",
        "want_any": lambda r: r.get("uag_present") or r.get("access_scope") in ("external", "both")
                              or r.get("dmz_design") in ("single", "double"),
    },
    {
        "name": "both / N+1 / on-prem",
        "answers": {"access_type": "Both internal and external", "site_topology": "N+1 within region",
                    "hosting_strategy": "On-premises", "horizon_protocol_scope": "Blast + PCoIP"},
        "forbid": lambda r: r.get("cloud_platform"),
        "want_any": lambda r: r.get("uag_present"),
    },
    {
        "name": "internal / single / cloud-AVS",
        "answers": {"access_type": "Internal users only", "site_topology": "Single site",
                    "hosting_strategy": "Cloud", "horizon_8_arch_track": "Horizon 8 on Azure VMware Solution"},
        "forbid": lambda r: r.get("cloud_platform") in ("vmc_aws", "gcve", "ocvs", "acvs"),
        "want_any": lambda r: r.get("cloud_platform") == "avs",
    },
    # Add this flow to FLOWS in scripts/eval_image_selection.py
    {
        "name": "external / single / on-prem / single-DMZ",
        "answers": {"access_type": "External users only", "site_topology": "Single site",
                    "hosting_strategy": "On-premises", "horizon_dmz_design": "Single DMZ",
                    "horizon_protocol_scope": "Blast Extreme only"},
        "forbid": lambda r: r.get("dmz_design") == "double",
        "want_any": lambda r: r.get("dmz_design") == "single",
    },
        {
        "name": "external / multi-site active-active / on-prem",
        "answers": {
            "access_type": "External users only",
            "site_topology": "Multi-site",
            "availability_requirements": "Multi-site active/active",
            "hosting_strategy": "On-premises",
            "horizon_protocol_scope": "Blast Extreme only",
        },
        "forbid": lambda r: r.get("site_topology") == "single_site" or (
            "single-site" in (r.get("figure_caption", "") or "").lower()
            and "multi" not in (r.get("figure_caption", "") or "").lower()
        ),
        "want_any": lambda r: r.get("uag_present") or r.get("access_scope") in ("external", "both"),
    },
]

def main() -> None:
    settings = load_settings(ROOT)
    store = TechZoneRagStore(settings, AzureFoundryClient(settings))

    overall_ok = True
    for flow in FLOWS:
        rows = select_hld_images(store, ["horizon_8"], flow["answers"], H8_REFS, limit=10)
        violations = [r for r in rows if flow["forbid"](r)]
        satisfied = any(flow["want_any"](r) for r in rows)
        ok = not violations and satisfied and rows
        overall_ok = overall_ok and ok
        print(f"\n=== {flow['name']} ===  {'PASS' if ok else 'FAIL'}")
        print(f"  selected: {len(rows)} | want_any satisfied: {satisfied} | violations: {len(violations)}")
        for r in rows:
            print(f"   - [{r.get('topic',''):18s}] acc={r.get('access_scope',''):8s} "
                  f"uag={int(bool(r.get('uag_present')))} dmz={r.get('dmz_design','none'):6s} "
                  f"cloud={r.get('cloud_platform','') or '-':7s} :: {r.get('figure_caption') or r.get('caption','')[:60]}")
        for v in violations:
            print(f"   !! VIOLATION: {v.get('topic')} acc={v.get('access_scope')} uag={v.get('uag_present')} "
                  f"dmz={v.get('dmz_design')} cloud={v.get('cloud_platform')}")

    print(f"\nOVERALL: {'PASS' if overall_ok else 'FAIL'}")

if __name__ == "__main__":
    main()
