"""Second pass: classify extracted image records as architecture diagram vs screenshot
using the project's Azure vision client, and add image_type/keep flags.

Reads  data/sitemap_image_inspection.jsonl
Writes data/sitemap_image_classified.jsonl   (one enriched record per line)

Resumable: image_urls already in the output file are skipped.

Usage:
    python scripts/classify_image_records.py --limit 60     # sample first 60 (cheap check)
    python scripts/classify_image_records.py                # classify everything
    python scripts/classify_image_records.py --no-vision    # text heuristic only (no API calls)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from sa_hld_bot.azure_foundry import AzureFoundryClient  # noqa: E402
from sa_hld_bot.config import load_settings  # noqa: E402

CATEGORIES = {"ARCHITECTURE_DIAGRAM", "FLOW_DIAGRAM", "CONSOLE_SCREENSHOT", "UI_SCREENSHOT", "OTHER"}
KEEP_TYPES = {"architecture_diagram", "flow_diagram"}

ARCH_KW = ("architecture", "topology", "logical", "components", "deployment", "reference architecture",
           "high-level", "data flow", "network port", "load balancing", "dmz", "pod and block", "overview")
SCREENSHOT_ALT = ("screenshot", "screen shot", "graphical user interface", "blue screen",
                  "black screen", "dialog", "command prompt", "console", "wizard")
FIGURE_RE = re.compile(r"^\s*figure\s*\d+", re.IGNORECASE)

def heuristic_type(rec: dict) -> str:
    alt = (rec.get("alt") or "").lower()
    cap = (rec.get("figure_caption") or "").lower()
    heading = (rec.get("section_heading") or "").lower()
    arch_signal = any(k in cap or k in heading for k in ARCH_KW)
    if FIGURE_RE.match(cap) and arch_signal:
        return "architecture_diagram"
    if any(s in alt for s in SCREENSHOT_ALT):
        return "ui_screenshot"
    return "other"

def vision_category(foundry: AzureFoundryClient, rec: dict) -> str:
    vision_model = foundry.settings.azure_vision_deployment or foundry.settings.azure_chat_deployment
    context = " | ".join(filter(None, [
        rec.get("page_title", ""), rec.get("section_heading", ""), rec.get("figure_caption", ""),
        (rec.get("context_text", "") or "")[:300],
    ]))
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
    for cat in CATEGORIES:
        if cat in token:
            return cat.lower()
    return "other"

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=str(ROOT / "data" / "sitemap_image_inspection.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "data" / "sitemap_image_classified.jsonl"))
    ap.add_argument("--limit", type=int, default=0, help="classify at most N records (0 = all)")
    ap.add_argument("--no-vision", action="store_true", help="use text heuristic only, no API calls")
    args = ap.parse_args()

    inp, out = Path(args.inp), Path(args.out)
    if not inp.exists():
        sys.exit(f"input not found: {inp}")

    done: set[str] = set()
    if out.exists():
        for ln in out.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    done.add(json.loads(ln).get("image_url", ""))
                except Exception:
                    pass

    settings = load_settings(ROOT)
    foundry = None if args.no_vision else AzureFoundryClient(settings)

    rows = [json.loads(ln) for ln in inp.read_text(encoding="utf-8").splitlines() if ln.strip()]
    counts: dict[str, int] = {}
    kept_by_page: dict[str, int] = {}
    processed = 0

    with out.open("a", encoding="utf-8") as out_f:
        for rec in rows:
            url = rec.get("image_url", "")
            if url in done:
                continue
            if args.limit and processed >= args.limit:
                break
            source = "heuristic"
            itype = heuristic_type(rec)
            if foundry is not None:
                try:
                    itype = vision_category(foundry, rec)
                    source = "vision"
                except Exception as exc:
                    source = "heuristic_fallback"
                    print(f"  vision failed ({exc}); using heuristic for {url.split('/')[-1]}", file=sys.stderr)
            rec["image_type"] = itype
            rec["keep"] = itype in KEEP_TYPES
            rec["classifier_source"] = source
            out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out_f.flush()
            counts[itype] = counts.get(itype, 0) + 1
            if rec["keep"]:
                kept_by_page[rec.get("page_url", "")] = kept_by_page.get(rec.get("page_url", ""), 0) + 1
            processed += 1
            if processed % 10 == 0:
                print(f"...{processed} classified")

    print("\n--- classification summary ---")
    print(f"records classified this run: {processed}")
    for t, c in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {t:22s} {c}")
    kept = sum(1 for t in KEEP_TYPES for _ in range(counts.get(t, 0)))
    print(f"kept (diagram/flow): {sum(counts.get(t,0) for t in KEEP_TYPES)}")
    print("\nkept diagrams per page:")
    for page, n in sorted(kept_by_page.items(), key=lambda x: -x[1]):
        print(f"  {n:2d}  {page}")
    print(f"\noutput: {out}")

if __name__ == "__main__":
    main()
