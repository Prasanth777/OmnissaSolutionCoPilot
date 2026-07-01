"""Batch: render every Tech Zone sitemap page via the project crawler and dump
structured per-image records to data/sitemap_image_inspection.jsonl. Resumable.

Usage:
    python scripts/inspect_techzone_sitemap.py            # full sitemap
    python scripts/inspect_techzone_sitemap.py --limit 20 # first 20 pages
    python scripts/inspect_techzone_sitemap.py --no-resume
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from bs4 import BeautifulSoup  # noqa: E402
from sa_hld_bot.config import load_settings  # noqa: E402
from sa_hld_bot.image_context import extract_image_records, _norm  # noqa: E402
from sa_hld_bot.rag import TechZoneCrawler  # noqa: E402

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max pages (0 = all)")
    ap.add_argument("--start", type=int, default=0, help="skip first N sitemap urls")
    ap.add_argument("--sleep", type=float, default=0.5, help="delay between pages (s)")
    ap.add_argument("--out", default=str(ROOT / "data" / "sitemap_image_inspection.jsonl"))
    ap.add_argument("--processed", default=str(ROOT / "data" / "sitemap_inspection_processed.txt"))
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()

    out_path = Path(args.out)
    processed_path = Path(args.processed)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    settings = load_settings(ROOT)
    crawler = TechZoneCrawler(settings)

    urls = crawler.sitemap_urls()
    if args.start:
        urls = urls[args.start:]
    if args.limit:
        urls = urls[: args.limit]

    done: set[str] = set()
    if not args.no_resume and processed_path.exists():
        done = {ln.strip() for ln in processed_path.read_text(encoding="utf-8").splitlines() if ln.strip()}

    mode = "w" if args.no_resume else "a"
    total_pages = len(urls)
    kept_images = failed = processed_now = 0

    with out_path.open(mode, encoding="utf-8") as out_f, processed_path.open(mode, encoding="utf-8") as proc_f:
        for idx, url in enumerate(urls, 1):
            if url in done:
                continue
            try:
                html = crawler._fetch_html_text(url)
                soup = BeautifulSoup(html, "html.parser")
                title = _norm(soup.title.get_text(strip=True) if soup.title else url)
                records = extract_image_records(soup, url, title)
                for rec in records:
                    out_f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
                out_f.flush()
                kept_images += len(records)
                processed_now += 1
                print(f"[{idx}/{total_pages}] {len(records):2d} imgs  {url}")
            except Exception as exc:
                failed += 1
                print(f"[{idx}/{total_pages}] FAILED  {url}  ({exc})", file=sys.stderr)
            finally:
                proc_f.write(url + "\n")
                proc_f.flush()
            if args.sleep:
                time.sleep(args.sleep)

    print(f"\n--- done ---\npages: {processed_now}  images: {kept_images}  failed: {failed}\nout: {out_path}")

if __name__ == "__main__":
    main()
