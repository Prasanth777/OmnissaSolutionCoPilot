"""Reusable v3 image-pipeline logic shared by the standalone scripts and the
in-app ingestion (rebuild_from_sitemap).

Responsibilities:
- classify an image as architecture/flow diagram vs screenshot/ui/other (vision),
  reusing cached verdicts keyed by image_url to avoid repeat vision calls;
- build a v3 caption row (authoritative figure caption + structured dimensions).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CAPTION_SCHEMA_VERSION = 3
KEEP_TYPES = {"architecture_diagram", "flow_diagram"}
CATEGORIES = {"ARCHITECTURE_DIAGRAM", "FLOW_DIAGRAM", "CONSOLE_SCREENSHOT", "UI_SCREENSHOT", "OTHER"}

CLOUD_PLATFORM = [
    ("horizon-8-vmware-cloud-aws", "vmc_aws"),
    ("horizon-8-azure-vmware-solution", "avs"),
    ("horizon-8-google-cloud-vmware-engine", "gcve"),
    ("horizon-8-oracle-cloud-vmware-solution", "ocvs"),
    ("horizon-8-alibaba-cloud-vmware-service", "acvs"),
    ("horizon-cloud", "horizon_cloud"),
]

TOPIC_BY_ANCHOR = {
    "architectural-overview": "logical_view", "components": "logical_view",
    "connection-server": "connection_server", "load-balancing-connection-servers": "cs_load_balancing",
    "pod-and-block": "block_design", "block": "block_design", "pod": "block_design",
    "cloud-pod-architecture": "cloud_pod",
    "external-access": "access_external", "external-access-architecture": "access_external",
    "single-dmz": "dmz_single", "double-dmz": "dmz_double",
    "unified-access-gateway-scaling": "access_external",
    "load-balancing-unified-access-gateway": "access_external",
    "unified-access-gateway-high-availability": "access_external",
    "display-protocol": "networking", "internal-connections": "networking_internal",
    "external-connections": "networking_external", "micro-segmentation": "networking",
    "authentication": "authentication", "true-sso": "true_sso", "true-sso-scalability": "true_sso",
    "active-directory-domains": "authentication",
    "scaled-single-site-architecture": "single_site_design",
    "multi-site-architecture": "multisite", "active-passive": "multisite", "active-active": "multisite",
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def cloud_platform_for(page_url: str) -> str:
    low = (page_url or "").lower()
    for token, tag in CLOUD_PLATFORM:
        if token in low:
            return tag
    return ""


def topic_for(anchor: str, heading: str) -> str:
    if anchor and anchor in TOPIC_BY_ANCHOR:
        return TOPIC_BY_ANCHOR[anchor]
    slug = _slug(heading)
    return TOPIC_BY_ANCHOR.get(slug, slug or "overview")


def clean_title(page_title: str) -> str:
    return re.sub(r"\s*\|\s*Omnissa\s*$", "", page_title or "").strip()


def load_classification_cache(path: Path) -> dict:
    """image_url -> image_type, from a prior classified JSONL (avoids repeat vision)."""
    out: dict[str, str] = {}
    if not path or not path.exists():
        return out
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        iu = r.get("image_url", "")
        it = r.get("image_type", "")
        if not iu or not it:
            continue
        # A v3/keep row implies architecture; a classified row carries image_type + keep.
        if it == "architecture_diagram" and r.get("keep") is False:
            it = "ui_screenshot"
        out[iu] = it
    return out


def classify_image_type(foundry, rec: dict) -> str:
    """Vision classification into one of CATEGORIES (lowercased). Falls back to 'other'."""
    vision_model = foundry.settings.azure_vision_deployment or foundry.settings.azure_chat_deployment
    context = " | ".join(filter(None, [
        rec.get("page_title", ""), rec.get("section_heading", ""),
        rec.get("figure_caption", ""), (rec.get("context_text", "") or "")[:300],
    ]))
    try:
        resp = foundry._create_chat_completion(
            model=vision_model,
            temperature=0.0,
            messages=[
                {"role": "system", "content": (
                    "You classify images from technical docs for use in architecture slide decks. "
                    "Reply with exactly ONE token: ARCHITECTURE_DIAGRAM, FLOW_DIAGRAM, "
                    "CONSOLE_SCREENSHOT, UI_SCREENSHOT, or OTHER. "
                    "ARCHITECTURE_DIAGRAM = boxes/components/zones/topology line art. "
                    "FLOW_DIAGRAM = sequence/process/auth flow line art. "
                    "CONSOLE_SCREENSHOT/UI_SCREENSHOT = captured product UI, admin consoles, wizards, terminals. "
                    "Judge from the IMAGE; the text context is only a hint."
                )},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Context: {context}\nClassify the image."},
                    {"type": "image_url", "image_url": {"url": rec.get("image_url", "")}},
                ]},
            ],
        )
        token = (resp.choices[0].message.content or "").strip().upper()
    except Exception:
        return "other"
    for cat in CATEGORIES:
        if cat in token:
            return cat.lower()
    return "other"

def build_v3_caption_row(rec: dict, image_type: str, local_path: str, flags: dict | None = None) -> dict:
    """Compose a v3 caption row from an ImageRecord dict + classification + local file.
    `flags` carries the per-image visual flags (has_uag/has_dmz/... + protocols_visible)."""
    hints = rec.get("hints", {}) or {}
    f = flags or {}
    page_url = rec.get("page_url", "")
    heading = rec.get("section_heading", "")
    anchor = rec.get("section_anchor", "")
    caption = (rec.get("figure_caption") or "").strip() or heading or clean_title(rec.get("page_title", ""))
    components = hints.get("components_shown", []) or []
    load_balancer = ("load_balancer" in components) or ("load balanc" in (rec.get("embed_text", "") or "").lower())
    return {
        "page_url": page_url,
        "image_url": rec.get("image_url", ""),
        "local_path": local_path,
        "title": clean_title(rec.get("page_title", "")),
        "caption": caption,
        "image_type": "architecture_diagram",
        "caption_version": CAPTION_SCHEMA_VERSION,
        "diagram_kind": image_type,
        "embed_text": rec.get("embed_text", ""),
        "section_heading": heading,
        "section_anchor": anchor,
        "figure_caption": rec.get("figure_caption", ""),
        "topic": topic_for(anchor, heading),
        "access_scope": hints.get("access_scope", ""),
        "uag_present": bool(hints.get("uag_present", False)),
        "load_balancer": bool(load_balancer),
        "dmz_design": hints.get("dmz_design", "none"),
        "protocols": hints.get("protocols", []),
        "site_topology": hints.get("site_topology", ""),
        "cloud_platform": cloud_platform_for(page_url),
        "components_shown": components,
        "keep": True,
        # visual flags (durable; reused on rebuild)
        "has_uag": bool(f.get("has_uag", False)),
        "has_dmz": bool(f.get("has_dmz", False)),
        "has_external_clients": bool(f.get("has_external_clients", False)),
        "has_workspace_one": bool(f.get("has_workspace_one", False)),
        "has_horizon_edge": bool(f.get("has_horizon_edge", False)),
        "protocols_visible": list(f.get("protocols_visible", []) or []),
        "flags_version": 1 if flags else 0,
    }

import json as _json

def classify_visual_flags(foundry, rec: dict) -> dict:
    """Vision pass: report which components are VISIBLE in the diagram (reliable,
    unlike caption text). Returns flags + visible display protocols."""
    vision_model = foundry.settings.azure_vision_deployment or foundry.settings.azure_chat_deployment
    context = " | ".join(filter(None, [
        rec.get("title", "") or rec.get("page_title", ""),
        rec.get("section_heading", ""), rec.get("figure_caption", ""),
    ]))
    empty = {"has_uag": False, "has_dmz": False, "has_external_clients": False,
             "has_workspace_one": False, "has_horizon_edge": False, "protocols_visible": []}
    try:
        resp = foundry._create_chat_completion(
            model=vision_model,
            temperature=0.0,
            messages=[
                {"role": "system", "content": (
                    "You inspect an architecture diagram and report which components are VISIBLE. "
                    "Respond ONLY with compact JSON, no prose: "
                    '{"has_uag":bool,"has_dmz":bool,"has_external_clients":bool,'
                    '"has_workspace_one":bool,"has_horizon_edge":bool,'
                    '"protocols_visible":["blast"|"pcoip"|"rdp"]}. '
                    "has_uag = a Unified Access Gateway box is drawn. "
                    "has_dmz = a DMZ zone/band is drawn. "
                    "has_external_clients = external/internet clients outside the datacenter are drawn. "
                    "has_workspace_one = a Workspace ONE / WS1 Access node is drawn. "
                    "has_horizon_edge = a Horizon Edge Gateway is drawn. "
                    "protocols_visible = only display protocols explicitly labeled in the image."
                )},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Context: {context}\nReturn the JSON."},
                    {"type": "image_url", "image_url": {"url": rec.get("image_url", "")}},
                ]},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = _json.loads(raw)
    except Exception:
        return dict(empty)
    out = dict(empty)
    for k in ("has_uag", "has_dmz", "has_external_clients", "has_workspace_one", "has_horizon_edge"):
        out[k] = bool(data.get(k, False))
    pv = data.get("protocols_visible", []) or []
    out["protocols_visible"] = [p for p in ("blast", "pcoip", "rdp") if p in [str(x).lower() for x in pv]]
    return out


def classify_image(foundry, rec: dict) -> dict:
    """One vision call returning BOTH the category and the visual flags, so a rebuild
    spends a single call per new image. Returns a flat dict:
    {image_type, has_uag, has_dmz, has_external_clients, has_workspace_one,
     has_horizon_edge, protocols_visible[]}."""
    vision_model = foundry.settings.azure_vision_deployment or foundry.settings.azure_chat_deployment
    context = " | ".join(filter(None, [
        rec.get("title", "") or rec.get("page_title", ""),
        rec.get("section_heading", ""), rec.get("figure_caption", ""),
        (rec.get("context_text", "") or "")[:300],
    ]))
    base = {"image_type": "other", "has_uag": False, "has_dmz": False,
            "has_external_clients": False, "has_workspace_one": False,
            "has_horizon_edge": False, "protocols_visible": []}
    try:
        resp = foundry._create_chat_completion(
            model=vision_model,
            temperature=0.0,
            messages=[
                {"role": "system", "content": (
                    "You classify an image from technical docs and report visible components. "
                    "Respond ONLY with compact JSON, no prose: "
                    '{"image_type":"ARCHITECTURE_DIAGRAM|FLOW_DIAGRAM|CONSOLE_SCREENSHOT|UI_SCREENSHOT|OTHER",'
                    '"has_uag":bool,"has_dmz":bool,"has_external_clients":bool,'
                    '"has_workspace_one":bool,"has_horizon_edge":bool,'
                    '"protocols_visible":["blast"|"pcoip"|"rdp"]}. '
                    "ARCHITECTURE_DIAGRAM=boxes/components/zones/topology line art; "
                    "FLOW_DIAGRAM=sequence/process/auth flow line art; "
                    "CONSOLE_SCREENSHOT/UI_SCREENSHOT=captured product UI/admin console/wizard/terminal. "
                    "has_uag=Unified Access Gateway box drawn; has_dmz=DMZ zone drawn; "
                    "has_external_clients=external/internet clients outside the datacenter drawn; "
                    "has_workspace_one=Workspace ONE / WS1 Access node drawn; "
                    "has_horizon_edge=Horizon Edge Gateway drawn; "
                    "protocols_visible=display protocols explicitly labeled. Judge from the IMAGE."
                )},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Context: {context}\nReturn the JSON."},
                    {"type": "image_url", "image_url": {"url": rec.get("image_url", "")}},
                ]},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip().replace("```json", "").replace("```", "").strip()
        data = _json.loads(raw) if "_json" in globals() else __import__("json").loads(raw)
    except Exception:
        return dict(base)
    out = dict(base)
    token = str(data.get("image_type", "")).strip().upper()
    out["image_type"] = next((c.lower() for c in CATEGORIES if c in token), "other")
    for k in ("has_uag", "has_dmz", "has_external_clients", "has_workspace_one", "has_horizon_edge"):
        out[k] = bool(data.get(k, False))
    pv = [str(x).lower() for x in (data.get("protocols_visible", []) or [])]
    out["protocols_visible"] = [p for p in ("blast", "pcoip", "rdp") if p in pv]
    return out


def load_enriched_cache(image_captions_path) -> dict:
    """image_url -> {diagram_kind, flags{...}} from an existing v3 captions file,
    so a rebuild reuses prior classification + flags without new vision calls."""
    from pathlib import Path as _Path
    import json as _j
    out: dict[str, dict] = {}
    p = _Path(image_captions_path)
    if not p.exists():
        return out
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            r = _j.loads(ln)
        except Exception:
            continue
        iu = r.get("image_url", "")
        if not iu or int(r.get("flags_version", 0) or 0) < 1:
            continue
        out[iu] = {
            "diagram_kind": r.get("diagram_kind") or "architecture_diagram",
            "flags": {
                "has_uag": bool(r.get("has_uag", False)),
                "has_dmz": bool(r.get("has_dmz", False)),
                "has_external_clients": bool(r.get("has_external_clients", False)),
                "has_workspace_one": bool(r.get("has_workspace_one", False)),
                "has_horizon_edge": bool(r.get("has_horizon_edge", False)),
                "protocols_visible": list(r.get("protocols_visible", []) or []),
            },
        }
    return out
