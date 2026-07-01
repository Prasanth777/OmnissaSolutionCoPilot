"""Phase 1 migration (robust): promote classified Tech Zone dataset into the v3 image store.

Reuses already-downloaded files (from image_captions.v2.bak.jsonl), retries downloads,
and falls back to Playwright for images the curl/requests path can't fetch. Logs every
failure so the 'not downloadable' count is real.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from sa_hld_bot.azure_foundry import AzureFoundryClient  # noqa: E402
from sa_hld_bot.config import load_settings  # noqa: E402
from sa_hld_bot.rag import BROWSER_UA, TECHZONE_DOMAIN, TechZoneCrawler, TechZoneRagStore  # noqa: E402

CAPTION_SCHEMA_VERSION = 3

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
    low = page_url.lower()
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

def row_id(image_url: str, local_path: str) -> str:
    return hashlib.sha256((local_path or image_url).encode("utf-8")).hexdigest()[:24]

def build_v3_row(rec: dict, local_path: str) -> dict:
    hints = rec.get("hints", {}) or {}
    page_url = rec.get("page_url", "")
    heading = rec.get("section_heading", "")
    anchor = rec.get("section_anchor", "")
    caption = (rec.get("figure_caption") or "").strip() or heading or clean_title(rec.get("page_title", ""))
    components = hints.get("components_shown", []) or []
    load_balancer = ("load_balancer" in components) or ("load balanc" in rec.get("embed_text", "").lower())
    return {
        "page_url": page_url,
        "image_url": rec.get("image_url", ""),
        "local_path": local_path,
        "title": clean_title(rec.get("page_title", "")),
        "caption": caption,
        "image_type": "architecture_diagram",
        "caption_version": CAPTION_SCHEMA_VERSION,
        "diagram_kind": rec.get("image_type", "architecture_diagram"),
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
    }

def load_v2_local_map(path: Path) -> dict:
    """image_url -> existing local_path from the v2 backup (only if the file exists)."""
    out = {}
    if not path.exists():
        return out
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        iu, lp = r.get("image_url", ""), str(r.get("local_path", ""))
        if iu and lp and Path(lp).exists():
            out[iu] = lp
    return out

def _img_via_playwright(url: str) -> bytes | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=BROWSER_UA,
                                      extra_http_headers={"Referer": f"https://{TECHZONE_DOMAIN}/"})
            page = ctx.new_page()
            resp = page.goto(url, wait_until="load", timeout=30000)
            data = resp.body() if (resp and resp.ok) else None
            browser.close()
            return data
    except Exception:
        return None

def resolve_local(crawler: TechZoneCrawler, image_url: str, images_dir: Path, v2map: dict) -> str:
    if image_url in v2map:
        return v2map[image_url]
    for _ in range(3):
        p = crawler.download_image(image_url, images_dir)
        if p:
            return str(p)
        time.sleep(0.8)
    data = _img_via_playwright(image_url)
    if data:
        name = re.sub(r"[^a-zA-Z0-9._-]", "_", Path(image_url.split("?")[0]).name or "image.png")
        digest = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:10]
        target = images_dir / f"{digest}_{name}"
        target.write_bytes(data)
        return str(target)
    return ""

def reindex_image_collection(store: TechZoneRagStore, rows: list[dict]) -> int:
    coll = store.image_collection
    existing = coll.get(include=[]).get("ids", []) or []
    if existing:
        coll.delete(ids=existing)
    ids, docs, metas, seen = [], [], [], set()
    for row in rows:
        lp = str(row.get("local_path", ""))
        if not lp or not Path(lp).exists():
            continue
        rid = row_id(row.get("image_url", ""), lp)
        if rid in seen:
            continue
        seen.add(rid)
        doc = row.get("embed_text") or f"{row.get('title','')}. {row.get('caption','')}".strip(". ")
        ids.append(rid)
        docs.append(doc)
        metas.append({
            "page_url": row.get("page_url", ""), "image_url": row.get("image_url", ""),
            "local_path": lp, "title": row.get("title", ""), "caption": row.get("caption", ""),
            "image_type": "architecture_diagram", "diagram_kind": row.get("diagram_kind", ""),
            "topic": row.get("topic", ""), "access_scope": row.get("access_scope", ""),
            "uag_present": row.get("uag_present", False), "load_balancer": row.get("load_balancer", False),
            "dmz_design": row.get("dmz_design", "none"),
            "protocols": ",".join(row.get("protocols", []) or []),
            "site_topology": row.get("site_topology", ""), "cloud_platform": row.get("cloud_platform", ""),
            "components_shown": ",".join(row.get("components_shown", []) or []),
            "caption_version": CAPTION_SCHEMA_VERSION,
        })
    if ids:
        coll.upsert(ids=ids, embeddings=store.embedding_service.embed_texts(docs), documents=docs, metadatas=metas)
    return len(ids)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=str(ROOT / "data" / "sitemap_image_classified.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "data" / "image_captions.jsonl"))
    ap.add_argument("--no-reindex", action="store_true")
    args = ap.parse_args()

    settings = load_settings(ROOT)
    crawler = TechZoneCrawler(settings)
    settings.images_dir.mkdir(parents=True, exist_ok=True)

    v2map = load_v2_local_map(ROOT / "data" / "image_captions.v2.bak.jsonl")
    print(f"reusable already-downloaded images from v2 backup: {len(v2map)}")

    records = [json.loads(ln) for ln in Path(args.inp).read_text(encoding="utf-8").splitlines() if ln.strip()]
    kept = [r for r in records if r.get("keep") is True]
    print(f"classified: {len(records)} | keep==true: {len(kept)}")

    rows, failed = [], []
    for i, rec in enumerate(kept, 1):
        img = rec.get("image_url", "")
        if not img:
            continue
        lp = resolve_local(crawler, img, settings.images_dir, v2map)
        if not lp:
            failed.append(img)
            continue
        rows.append(build_v3_row(rec, lp))
        if i % 50 == 0:
            print(f"  ...{i}/{len(kept)} processed, {len(rows)} ok, {len(failed)} failed")

    print(f"\nv3 rows built: {len(rows)} | images not downloadable: {len(failed)}")
    if failed:
        print("  first failures:")
        for u in failed[:8]:
            print(f"    {u}")

    out = Path(args.out)
    if out.exists():
        backup = out.with_suffix(".v2.bak.jsonl")
        if not backup.exists():
            shutil.copy2(out, backup)
            print(f"backed up existing captions -> {backup}")
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} v3 rows -> {out}")

    if not args.no_reindex:
        print("reindexing image collection (loads embedding model)...")
        store = TechZoneRagStore(settings, AzureFoundryClient(settings))
        print(f"indexed {reindex_image_collection(store, rows)} images.")
    print("done.")

if __name__ == "__main__":
    main()
