"""One-shot: render a Tech Zone page via the project crawler and dump per-image context.

Usage:
    python scripts/inspect_techzone_page.py [url]
Writes data/page_inspection.json and prints a summary.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from bs4 import BeautifulSoup  # noqa: E402
from sa_hld_bot.config import load_settings  # noqa: E402
from sa_hld_bot.rag import TechZoneCrawler, _normalize_whitespace  # noqa: E402

DEFAULT_URL = "https://techzone.omnissa.com/resource/horizon-8-architecture"

def nearest_heading(node):
    """Walk backwards through the document to the closest preceding h1-h4."""
    for prev in node.find_all_previous(["h1", "h2", "h3", "h4"]):
        text = _normalize_whitespace(prev.get_text(" ", strip=True))
        if text:
            return {"level": prev.name, "text": text, "id": prev.get("id", "")}
    return {"level": "", "text": "", "id": ""}

def surrounding_paragraphs(node, limit=2):
    before, after = [], []
    for prev in node.find_all_previous(["p", "li"]):
        t = _normalize_whitespace(prev.get_text(" ", strip=True))
        if len(t) >= 25:
            before.append(t)
        if len(before) >= limit:
            break
    for nxt in node.find_all_next(["p", "li"]):
        t = _normalize_whitespace(nxt.get_text(" ", strip=True))
        if len(t) >= 25:
            after.append(t)
        if len(after) >= limit:
            break
    return list(reversed(before)), after

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    settings = load_settings(ROOT)
    crawler = TechZoneCrawler(settings)

    html = crawler._fetch_html_text(url)  # curl -> requests -> Playwright fallback
    soup = BeautifulSoup(html, "html.parser")

    title = _normalize_whitespace(soup.title.get_text(strip=True) if soup.title else url)
    outline = [
        {"level": h.name, "text": _normalize_whitespace(h.get_text(" ", strip=True)), "id": h.get("id", "")}
        for h in soup.find_all(["h1", "h2", "h3", "h4"])
        if _normalize_whitespace(h.get_text(" ", strip=True))
    ]

    images = []
    for img in soup.find_all("img"):
        figure = img.find_parent("figure")
        figcaption = ""
        if figure:
            cap = figure.find("figcaption")
            if cap:
                figcaption = _normalize_whitespace(cap.get_text(" ", strip=True))
        before, after = surrounding_paragraphs(img)
        images.append({
            "src_candidates": crawler._extract_image_candidates(img),
            "alt": _normalize_whitespace(img.get("alt", "")),
            "title_attr": _normalize_whitespace(img.get("title", "")),
            "figcaption": figcaption,
            "nearest_heading": nearest_heading(img),
            "text_before": before,
            "text_after": after,
            "width_attr": img.get("width", ""),
            "height_attr": img.get("height", ""),
        })

    report = {
        "url": url,
        "title": title,
        "html_length": len(html),
        "heading_count": len(outline),
        "image_count": len(images),
        "outline": outline,
        "images": images,
    }
    out = ROOT / "data" / "page_inspection.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}")
    print(f"Title: {title}")
    print(f"Headings: {len(outline)} | Images: {len(images)} | HTML chars: {len(html)}")
    for i, im in enumerate(images, 1):
        src = (im["src_candidates"] or [""])[-1]
        print(f"\n[{i}] {src.split('/')[-1]}")
        print(f"    heading : {im['nearest_heading']['text']}")
        print(f"    alt     : {im['alt']}")
        print(f"    caption : {im['figcaption']}")
        if im["text_before"]:
            print(f"    before  : {im['text_before'][-1][:160]}")

if __name__ == "__main__":
    main()
