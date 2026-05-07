from __future__ import annotations

import json
import re
import subprocess
import time
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import chromadb
import requests
from bs4 import BeautifulSoup
from PIL import Image

from .azure_foundry import AzureFoundryClient, RetrievedChunk
from .catalog import TECHZONE_DOMAIN, TECHZONE_SITEMAP_URL
from .config import Settings
from .embeddings import HuggingFaceEmbeddingService
from .guardrails import is_techzone_source
from .logging_utils import get_logger


REQUEST_TIMEOUT = 20
CHUNK_SIZE = 1400
CHUNK_OVERLAP = 220
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": f"https://{TECHZONE_DOMAIN}/",
    "DNT": "1",
}
ICON_HINTS = (
    "icon",
    "logo",
    "avatar",
    "author",
    "profile",
    "favicon",
    "sprite",
    "badge",
)
ARCHITECTURE_HINTS = (
    "architecture",
    "reference-architecture",
    "diagram",
    "topology",
    "deployment",
    "design",
)
CAPTION_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class CrawlPage:
    url: str
    title: str
    text: str
    image_urls: list[str]


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_text(soup: BeautifulSoup) -> str:
    candidates = []
    for node in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        text = _normalize_whitespace(node.get_text(" ", strip=True))
        if len(text) >= 25:
            candidates.append(text)
    return "\n".join(candidates)


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


class TechZoneCrawler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger("sa_hld_bot.rag.crawler", settings.logs_dir)
        self.session = requests.Session()
        self.session.headers.update(BROWSER_HEADERS)
        self._prime_session()

    def _prime_session(self) -> None:
        # Warm up cookies/session similarly to a browser landing on the site first.
        try:
            self.session.get(f"https://{TECHZONE_DOMAIN}/", timeout=REQUEST_TIMEOUT)
        except Exception as exc:
            self.logger.debug("Session prime failed: %s", exc)

    def _fetch_with_playwright(self, url: str, accept_header: str) -> str:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            self.logger.debug("Playwright import unavailable for %s: %s", url, exc)
            return ""

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=BROWSER_UA,
                    locale="en-US",
                    extra_http_headers={
                        "Accept": accept_header,
                        "Accept-Language": "en-US,en;q=0.9",
                        "Cache-Control": "no-cache",
                        "Pragma": "no-cache",
                        "Referer": f"https://{TECHZONE_DOMAIN}/",
                    },
                )

                # Prime cookies similarly to a real browsing session.
                page = context.new_page()
                page.goto(f"https://{TECHZONE_DOMAIN}/", wait_until="domcontentloaded", timeout=(REQUEST_TIMEOUT + 10) * 1000)
                response = page.goto(url, wait_until="networkidle", timeout=(REQUEST_TIMEOUT + 20) * 1000)
                if response and response.ok:
                    # For XML/plain text endpoints use response text.
                    content_type = (response.header_value("content-type") or "").lower()
                    if "xml" in content_type or "text/plain" in content_type:
                        text = response.text() or ""
                    else:
                        text = page.content() or ""
                    browser.close()
                    if text.strip():
                        self.logger.info("Fetched via playwright fallback: %s", url)
                        return text
                browser.close()
        except PlaywrightTimeoutError as exc:
            self.logger.debug("Playwright timeout for %s: %s", url, exc)
        except Exception as exc:
            self.logger.debug("Playwright fetch failed for %s: %s", url, exc)
        return ""

    @staticmethod
    def _curl_command(url: str, accept_header: str) -> list[str]:
        return [
            "curl",
            "-sS",
            "-L",
            "--http2",
            "--compressed",
            "-H",
            f"User-Agent: {BROWSER_UA}",
            "-H",
            f"Accept: {accept_header}",
            "-H",
            "Accept-Language: en-US,en;q=0.9",
            "-H",
            "Cache-Control: no-cache",
            "-H",
            "Pragma: no-cache",
            "-H",
            f"Referer: https://{TECHZONE_DOMAIN}/",
            "-H",
            "DNT: 1",
            url,
        ]

    def _fetch_xml_text(self, url: str) -> str:
        cache_file = self.settings.data_dir / "sitemap_cache.xml"
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)

        # Browser-like fetch first because Tech Zone may block default requests clients with 403.
        for _ in range(3):
            cmd = self._curl_command(url, "application/xml,text/xml,*/*;q=0.9")
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=REQUEST_TIMEOUT + 15)
            text = (result.stdout or "").strip()
            if result.returncode == 0 and text:
                cache_file.write_text(text, encoding="utf-8")
                self.logger.debug("Fetched sitemap via curl: %s", url)
                return text
            time.sleep(1.5)

        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            text = response.text.strip()
            if text:
                cache_file.write_text(text, encoding="utf-8")
                self.logger.debug("Fetched sitemap via requests fallback: %s", url)
                return text
        except Exception as exc:
            self.logger.debug("Requests sitemap fallback failed for %s: %s", url, exc)

        playwright_text = self._fetch_with_playwright(url, "application/xml,text/xml,*/*;q=0.9").strip()
        if playwright_text:
            cache_file.write_text(playwright_text, encoding="utf-8")
            return playwright_text

        if cache_file.exists():
            cached = cache_file.read_text(encoding="utf-8").strip()
            if cached:
                self.logger.warning("Using cached sitemap after fetch failures: %s", url)
                return cached
        raise RuntimeError(f"Failed to fetch sitemap via requests and curl: {url}")

    def _fetch_html_text(self, url: str) -> str:
        # Browser-like fetch first because Tech Zone may block default requests clients with 403.
        cmd = self._curl_command(url, "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=REQUEST_TIMEOUT + 15)
        text = (result.stdout or "").strip()
        if result.returncode == 0 and text:
            self.logger.debug("Fetched page via curl: %s", url)
            return text

        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            text = response.text.strip()
            if "Access Denied" not in text and text:
                self.logger.debug("Fetched page via requests fallback: %s", url)
                return text
        except Exception as exc:
            self.logger.debug("Requests page fallback failed for %s: %s", url, exc)

        playwright_html = self._fetch_with_playwright(url, "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        playwright_html = playwright_html.strip()
        if playwright_html and "Access Denied" not in playwright_html:
            return playwright_html

        raise RuntimeError(f"Failed to fetch page via curl and requests: {url}")

    def _fetch_binary(self, url: str) -> bytes:
        # Browser-like fetch first for better parity with user browser behavior.
        cmd = self._curl_command(url, "*/*")
        result = subprocess.run(cmd, capture_output=True, check=False, timeout=REQUEST_TIMEOUT + 15)
        if result.returncode == 0 and result.stdout:
            return bytes(result.stdout)

        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            if response.content:
                return response.content
        except Exception as exc:
            self.logger.debug("Requests binary fallback failed for %s: %s", url, exc)

        raise RuntimeError(f"Failed to fetch binary payload via curl and requests: {url}")

    @staticmethod
    def _extract_srcset_best(srcset: str) -> str:
        parts = [part.strip() for part in srcset.split(",") if part.strip()]
        if not parts:
            return ""
        return parts[-1].split(" ")[0].strip()

    def _extract_image_candidates(self, image) -> list[str]:
        candidates: list[str] = []
        # Prioritize lazy/source attributes over src because src is often a placeholder.
        for key in ("data-srcset", "srcset", "data-src", "data-lazy-src", "src"):
            value = (image.get(key) or "").strip()
            if not value:
                continue
            if key.endswith("srcset"):
                picked = self._extract_srcset_best(value)
                if picked:
                    candidates.append(picked)
            else:
                candidates.append(value)
        return candidates

    def _fetch_sitemap_urls(self, sitemap_url: str) -> list[str]:
        xml_text = self._fetch_xml_text(sitemap_url)
        root = ElementTree.fromstring(xml_text)

        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        child_sitemaps = root.findall(".//sm:sitemap/sm:loc", ns)
        if child_sitemaps:
            urls: list[str] = []
            for sm in child_sitemaps:
                child_url = (sm.text or "").strip()
                if child_url:
                    urls.extend(self._fetch_sitemap_urls(child_url))
            return urls

        page_urls = []
        for node in root.findall(".//sm:url/sm:loc", ns):
            url = (node.text or "").strip()
            if not url or not is_techzone_source(url):
                continue
            if self.settings.sitemap_resource_only:
                path = urlparse(url).path
                if not path.startswith("/resource/"):
                    continue
            page_urls.append(url)
        return page_urls

    def sitemap_urls(self) -> list[str]:
        return self._fetch_sitemap_urls(TECHZONE_SITEMAP_URL)

    def crawl_page(self, url: str) -> CrawlPage | None:
        if not is_techzone_source(url):
            return None
        try:
            html = self._fetch_html_text(url)
        except Exception:
            return None
        soup = BeautifulSoup(html, "html.parser")

        title = _normalize_whitespace(soup.title.get_text(strip=True) if soup.title else url)
        text = _extract_text(soup)
        image_urls: list[str] = []
        for image in soup.find_all("img"):
            for candidate in self._extract_image_candidates(image):
                resolved = urljoin(url, candidate)
                if is_techzone_source(resolved):
                    image_urls.append(resolved)
        return CrawlPage(url=url, title=title, text=text, image_urls=list(dict.fromkeys(image_urls)))

    def download_image(self, image_url: str, output_dir: Path) -> Path | None:
        if not is_techzone_source(image_url):
            return None
        output_dir.mkdir(parents=True, exist_ok=True)
        parsed = urlparse(image_url)
        name = Path(parsed.path).name or "image"
        if "." not in name:
            name = f"{name}.jpg"
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
        digest = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:10]
        safe_name = f"{digest}_{safe_name}"
        target = output_dir / safe_name
        if target.exists():
            return target
        try:
            content = self._fetch_binary(image_url)
        except Exception:
            return None
        target.write_bytes(content)
        return target


class TechZoneRagStore:
    def __init__(self, settings: Settings, foundry: AzureFoundryClient) -> None:
        self.settings = settings
        self.foundry = foundry
        self.logger = get_logger("sa_hld_bot.rag.store", settings.logs_dir)
        self._embedding_service: HuggingFaceEmbeddingService | None = None
        self.client = chromadb.PersistentClient(path=str(settings.chroma_dir))
        self.collection = self.client.get_or_create_collection(settings.collection_name)

    @property
    def embedding_service(self) -> HuggingFaceEmbeddingService:
        # Lazy load heavy embedding model to reduce first app startup latency.
        if self._embedding_service is None:
            started = time.perf_counter()
            self._embedding_service = HuggingFaceEmbeddingService(self.settings.hf_embedding_model)
            elapsed = time.perf_counter() - started
            self.logger.info("Embedding model loaded: %s in %.2fs", self.settings.hf_embedding_model, elapsed)
        return self._embedding_service

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _get_page_chunk_ids(self, page_url: str) -> tuple[list[str], str]:
        existing = self.collection.get(where={"url": page_url}, include=["metadatas"])
        ids = existing.get("ids", []) or []
        metadatas = existing.get("metadatas", []) or []
        page_hash = ""
        if metadatas and isinstance(metadatas[0], dict):
            page_hash = str(metadatas[0].get("page_hash", "") or "")
        return list(ids), page_hash

    def _load_caption_rows(self) -> list[dict[str, str]]:
        file = self.settings.image_captions_file
        if not file.exists():
            return []
        rows: list[dict[str, str]] = []
        changed = False
        for line in file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    local_path = str(row.get("local_path", "")).strip()
                    if local_path and not Path(local_path).exists():
                        changed = True
                        continue
                    rows.append(row)
            except Exception:
                continue
        if changed:
            self._save_caption_rows(rows)
        return rows

    def _save_caption_rows(self, rows: list[dict[str, str]]) -> None:
        file = self.settings.image_captions_file
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    def _backfill_missing_captions(self, rows: list[dict[str, str]], max_updates: int = 20) -> list[dict[str, str]]:
        updated = 0
        changed = False
        for row in rows:
            if updated >= max_updates:
                break
            if row.get("image_type") != "architecture_diagram":
                continue
            caption = str(row.get("caption", "")).strip()
            local_path = str(row.get("local_path", "")).strip()
            image_url = str(row.get("image_url", "")).strip()
            if caption or not image_url or not local_path or not Path(local_path).exists():
                continue
            page_url = str(row.get("page_url", "")).strip()
            page_title = str(row.get("title", "")).strip()
            try:
                generated = self.foundry.caption_image_from_url(
                    image_url=image_url,
                    page_url=page_url,
                    page_title=page_title,
                )
            except Exception:
                generated = ""
            generated = self._align_caption_with_page(generated, page_title, page_url)
            if generated:
                row["caption"] = generated
                row["caption_version"] = CAPTION_SCHEMA_VERSION
                changed = True
                updated += 1
        if changed:
            self._save_caption_rows(rows)
        return rows

    def _rows_from_audit(self, limit: int = 5000) -> list[dict[str, str]]:
        audit_file = self.settings.logs_dir / "ingestion_audit.jsonl"
        if not audit_file.exists():
            return []
        rows: list[dict[str, str]] = []
        seen_paths: set[str] = set()
        lines = audit_file.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if payload.get("status") != "image_accepted":
                continue
            local_path = str(payload.get("local_path", "")).strip()
            if not local_path or not Path(local_path).exists() or local_path in seen_paths:
                continue
            seen_paths.add(local_path)
            rows.append(
                {
                    "page_url": str(payload.get("url", "")),
                    "image_url": str(payload.get("image_url", "")),
                    "local_path": local_path,
                    "caption": "",
                    "title": str(payload.get("url", "")),
                    "image_type": "architecture_diagram",
                    # Mark as stale so next rebuild regenerates caption/title context.
                    "caption_version": 0,
                }
            )
            if len(rows) >= limit:
                break
        rows.reverse()
        return rows

    @staticmethod
    def _align_caption_with_page(caption: str, page_title: str, page_url: str) -> str:
        raw = (caption or "").strip()
        if not raw:
            return raw
        context = f"{page_title} {page_url}".lower()
        fixed = raw
        platform_map = [
            ("azure vmware solution", "Azure VMware Solution (AVS)"),
            ("vmware cloud on aws", "VMware Cloud on AWS"),
            ("google cloud vmware engine", "Google Cloud VMware Engine"),
            ("oracle cloud vmware solution", "Oracle Cloud VMware Solution"),
            ("alibaba cloud vmware service", "Alibaba Cloud VMware Service"),
        ]
        for key, canonical in platform_map:
            if key in context and canonical.lower() not in fixed.lower():
                fixed = f"{canonical}: {fixed}"
                break
        return fixed

    @staticmethod
    def _delete_local_file(path_str: str) -> None:
        try:
            path = Path(path_str)
            if path.exists() and path.is_file():
                path.unlink()
        except Exception:
            pass

    def rebuild_from_sitemap(self, max_pages: int | None = None, force_full_rebuild: bool = False) -> dict[str, int]:
        crawler = TechZoneCrawler(self.settings)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.logger.info("RAG rebuild start run_id=%s max_pages=%s force_full_rebuild=%s", run_id, max_pages, force_full_rebuild)
        urls = crawler.sitemap_urls()
        if max_pages:
            urls = urls[:max_pages]

        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings.images_dir.mkdir(parents=True, exist_ok=True)
        caption_rows = self._load_caption_rows()

        if force_full_rebuild:
            for path in self.settings.images_dir.glob("*"):
                if path.is_file():
                    try:
                        path.unlink()
                    except Exception as exc:
                        self.logger.warning("Failed to remove stale image file %s: %s", path, exc)
            if self.collection.count() > 0:
                ids = self.collection.get(include=[])["ids"]
                if ids:
                    self.collection.delete(ids=ids)
            caption_rows = []

        indexed_chunks = 0
        upserted_pages = 0
        unchanged_pages = 0
        failed_pages = 0
        indexed_images = 0
        deleted_images = 0
        audit_file = self.settings.logs_dir / "ingestion_audit.jsonl"

        for page_idx, url in enumerate(urls):
            page_started = time.perf_counter()
            page = crawler.crawl_page(url)
            if not page or not page.text:
                failed_pages += 1
                self._append_audit(
                    audit_file,
                    {
                        "run_id": run_id,
                        "url": url,
                        "status": "skipped",
                        "reason": "empty_or_unreachable_page",
                    },
                )
                continue

            chunks = _chunk_text(page.text)
            if not chunks:
                failed_pages += 1
                self._append_audit(
                    audit_file,
                    {
                        "run_id": run_id,
                        "url": url,
                        "status": "skipped",
                        "reason": "no_chunks",
                    },
                )
                continue

            page_hash = self._hash_text(page.text)
            existing_ids, existing_page_hash = self._get_page_chunk_ids(page.url)
            page_rows = [row for row in caption_rows if row.get("page_url") == page.url]
            captions_stale = (
                force_full_rebuild
                or not page_rows
                or any(int(row.get("caption_version", 0) or 0) != CAPTION_SCHEMA_VERSION for row in page_rows)
                or any(not str(row.get("caption", "")).strip() for row in page_rows)
            )
            page_changed = force_full_rebuild or not existing_ids or existing_page_hash != page_hash

            if not page_changed and not captions_stale:
                unchanged_pages += 1
                self._append_audit(
                    audit_file,
                    {
                        "run_id": run_id,
                        "url": page.url,
                        "status": "page_unchanged_skipped",
                        "chunks": len(chunks),
                    },
                )
                continue

            if page_changed:
                if existing_ids:
                    self.collection.delete(ids=existing_ids)

                embeddings = self.embedding_service.embed_texts(chunks)
                chunk_ids = [f"{self._hash_text(page.url)[:12]}_{page_idx}_{chunk_idx}" for chunk_idx in range(len(chunks))]
                metadatas = [
                    {
                        "url": page.url,
                        "title": page.title,
                        "chunk_index": i,
                        "page_hash": page_hash,
                        "chunk_hash": self._hash_text(chunks[i]),
                    }
                    for i in range(len(chunks))
                ]

                self.collection.upsert(ids=chunk_ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
                indexed_chunks += len(chunks)
                upserted_pages += 1

            # Remove previous image rows/files for this page before re-ingesting.
            prior_rows = [row for row in caption_rows if row.get("page_url") == page.url]
            caption_rows = [row for row in caption_rows if row.get("page_url") != page.url]
            for prior in prior_rows:
                local_path = str(prior.get("local_path", "")).strip()
                if local_path and not any(str(r.get("local_path", "")) == local_path for r in caption_rows):
                    self._delete_local_file(local_path)
                    deleted_images += 1

            accepted_images = 0
            for image_url in page.image_urls:
                local_path = crawler.download_image(image_url, self.settings.images_dir)
                if not local_path:
                    self._append_audit(
                        audit_file,
                        {
                            "run_id": run_id,
                            "url": page.url,
                            "image_url": image_url,
                            "status": "image_download_failed",
                        },
                    )
                    continue
                if not self._is_architecture_diagram(local_path=local_path, image_url=image_url, page_title=page.title):
                    self._append_audit(
                        audit_file,
                        {
                            "run_id": run_id,
                            "url": page.url,
                            "image_url": image_url,
                            "status": "image_rejected_non_architecture",
                        },
                    )
                    continue
                caption = ""
                try:
                    caption = self.foundry.caption_image_from_url(
                        image_url=image_url,
                        page_url=page.url,
                        page_title=page.title,
                    )
                except Exception:
                    caption = ""
                caption = self._align_caption_with_page(caption, page.title, page.url)
                row = {
                    "page_url": page.url,
                    "image_url": image_url,
                    "local_path": str(local_path),
                    "caption": caption,
                    "title": page.title,
                    "image_type": "architecture_diagram",
                    "caption_version": CAPTION_SCHEMA_VERSION,
                }
                caption_rows.append(row)
                indexed_images += 1
                accepted_images += 1
                self._append_audit(
                    audit_file,
                    {
                        "run_id": run_id,
                        "url": page.url,
                        "image_url": image_url,
                        "local_path": str(local_path),
                        "status": "image_accepted",
                    },
                )
                if accepted_images >= self.settings.max_images_per_page:
                    break

            page_elapsed = time.perf_counter() - page_started
            self._append_audit(
                audit_file,
                {
                    "run_id": run_id,
                    "url": page.url,
                    "status": "page_indexed",
                    "chunks": len(chunks),
                    "images_found": len(page.image_urls),
                    "images_accepted": accepted_images,
                    "seconds": round(page_elapsed, 2),
                },
            )
            # Persist incrementally so interruptions do not lose all image metadata.
            self._save_caption_rows(caption_rows)

        self._save_caption_rows(caption_rows)

        stats = {
            "pages_scanned": len(urls),
            "pages_upserted": upserted_pages,
            "pages_unchanged": unchanged_pages,
            "pages_failed": failed_pages,
            "chunks": indexed_chunks,
            "images_added": indexed_images,
            "images_deleted": deleted_images,
            "images_total": len(caption_rows),
            "urls_discovered": len(urls),
        }
        self.logger.info("RAG rebuild complete run_id=%s stats=%s", run_id, stats)
        return stats

    def _is_architecture_diagram(self, local_path: Path, image_url: str, page_title: str) -> bool:
        path_lower = image_url.lower()
        if any(token in path_lower for token in ICON_HINTS):
            return False
        if path_lower.endswith(".svg"):
            return False

        try:
            with Image.open(local_path) as image:
                width, height = image.size
        except Exception:
            return False

        if width < 420 or height < 260:
            return False
        area = width * height
        if area < 240_000:
            return False
        ratio = width / max(1, height)
        if ratio < 0.9 or ratio > 4.8:
            return False

        if not any(hint in path_lower or hint in page_title.lower() for hint in ARCHITECTURE_HINTS):
            if width < 720 or height < 360:
                return False

        # Vision-assisted classification to distinguish architecture diagrams from decorative images.
        try:
            if self.foundry.classify_architecture_diagram_from_url(image_url):
                return True
        except Exception:
            pass

        # Conservative fallback when vision is unavailable: keep only very likely architecture frames.
        return width >= 900 and height >= 500 and ratio <= 3.8

    def index_stats(self) -> dict[str, int]:
        chunks = self.collection.count()
        images = 0
        if self.settings.image_captions_file.exists():
            images = sum(1 for line in self.settings.image_captions_file.read_text(encoding="utf-8").splitlines() if line.strip())
        return {"chunks": int(chunks), "images": int(images)}

    def search(self, query: str, top_k: int = 6) -> list[RetrievedChunk]:
        vector = self.embedding_service.embed_text(query)
        result = self.collection.query(
            query_embeddings=[vector],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        docs = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        ids = result.get("ids", [[]])[0]

        chunks: list[RetrievedChunk] = []
        for chunk_id, doc, meta, distance in zip(ids, docs, metadatas, distances):
            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    url=str(meta.get("url", "")),
                    title=str(meta.get("title", "")),
                    content=str(doc),
                    score=float(distance),
                )
            )
        return chunks

    def best_images_for_urls(self, urls: list[str], limit: int = 6) -> list[dict[str, str]]:
        rows = self._load_caption_rows()
        if not rows:
            rows = self._rows_from_audit()
            if rows:
                self._save_caption_rows(rows)
        if rows:
            rows = self._backfill_missing_captions(rows, max_updates=min(max(limit, 10), 40))
        if not rows:
            return []
        wanted = {self._canonical_url(url) for url in urls}
        matches: list[dict[str, str]] = []
        for row in rows:
            local_path = str(row.get("local_path", "")).strip()
            if local_path and not Path(local_path).exists():
                continue
            if (
                self._canonical_url(str(row.get("page_url", ""))) in wanted
                and row.get("image_type") == "architecture_diagram"
            ):
                matches.append(row)
            if len(matches) >= limit:
                break
        if matches:
            return matches

        # Fallback: return recent architecture rows with valid files if direct URL mapping is sparse.
        for row in reversed(rows):
            if row.get("image_type") != "architecture_diagram":
                continue
            local_path = str(row.get("local_path", "")).strip()
            if not local_path or not Path(local_path).exists():
                continue
            matches.append(row)
            if len(matches) >= limit:
                break
        matches.reverse()
        return matches

    def select_hld_images(
        self,
        selected_products: list[str],
        answers: dict[str, str],
        reference_urls: list[str],
        limit: int = 10,
    ) -> list[dict[str, str]]:
        rows = self._load_caption_rows()
        if not rows:
            rows = self._rows_from_audit()
            if rows:
                self._save_caption_rows(rows)
        if rows:
            rows = self._backfill_missing_captions(rows, max_updates=min(max(limit * 2, 12), 60))
        rows = [
            row
            for row in rows
            if row.get("image_type") == "architecture_diagram"
            and Path(str(row.get("local_path", ""))).exists()
        ]
        if not rows:
            return []

        if "horizon_8" in selected_products:
            return self._select_horizon_8_hld_images(rows, answers, reference_urls, limit=limit)
        return self.best_images_for_urls(reference_urls, limit=limit)

    @staticmethod
    def _row_search_text(row: dict[str, str]) -> str:
        return " ".join(
            [
                str(row.get("title", "")),
                str(row.get("caption", "")),
                str(row.get("page_url", "")),
                str(row.get("image_url", "")),
            ]
        ).lower()

    @staticmethod
    def _canonical_url_set(urls: list[str]) -> set[str]:
        wanted: set[str] = set()
        for url in urls:
            try:
                parsed = urlparse(url)
                wanted.add(f"{parsed.scheme}://{parsed.netloc}{parsed.path}")
            except Exception:
                wanted.add(url)
        return wanted

    def _score_section_row(
        self,
        row: dict[str, str],
        section_keywords: tuple[str, ...],
        preferred_urls: tuple[str, ...],
        answer_keywords: tuple[str, ...] = (),
    ) -> int:
        text = self._row_search_text(row)
        score = 0
        for keyword in section_keywords:
            if keyword in text:
                score += 8
        for keyword in answer_keywords:
            if keyword and keyword in text:
                score += 5
        page_url = self._canonical_url(str(row.get("page_url", "")))
        if page_url in preferred_urls:
            score += 20
        return score

    def _pick_best_section_image(
        self,
        rows: list[dict[str, str]],
        used_paths: set[str],
        slide_title: str,
        section_keywords: tuple[str, ...],
        preferred_urls: tuple[str, ...],
        answer_keywords: tuple[str, ...] = (),
        min_score: int = 8,
    ) -> dict[str, str] | None:
        ranked: list[tuple[int, dict[str, str]]] = []
        for row in rows:
            local_path = str(row.get("local_path", ""))
            if not local_path or local_path in used_paths:
                continue
            score = self._score_section_row(row, section_keywords, preferred_urls, answer_keywords)
            if score >= min_score:
                ranked.append((score, row))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0], reverse=True)
        chosen = dict(ranked[0][1])
        chosen["slide_title"] = slide_title
        used_paths.add(str(chosen.get("local_path", "")))
        return chosen

    def _select_horizon_8_hld_images(
        self,
        rows: list[dict[str, str]],
        answers: dict[str, str],
        reference_urls: list[str],
        limit: int,
    ) -> list[dict[str, str]]:
        canonical_refs = self._canonical_url_set(reference_urls)
        track = str(answers.get("horizon_8_arch_track", "")).lower()
        access = str(answers.get("horizon_access_topology", "")).lower()
        dmz = str(answers.get("horizon_dmz_design", "")).lower()
        protocols = str(answers.get("horizon_protocol_scope", "")).lower()

        platform_refs = tuple(
            url
            for url in canonical_refs
            if any(token in url for token in ("horizon-8-", "unified-access-gateway", "network-ports-horizon-8", "environment-infrastructure-design"))
        )

        plans: list[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...], int]] = [
            (
                "Core Components - Logical View",
                ("logical", "core components", "architecture overview", "connection server"),
                tuple(url for url in platform_refs if "horizon-8-architecture" in url),
                (),
                10,
            ),
            (
                "Core Components - Block Design",
                ("block design", "pod", "block", "management resource pool", "user resource pool"),
                tuple(url for url in platform_refs if "horizon-8-architecture" in url or "environment-infrastructure-design" in url),
                (),
                10,
            ),
            (
                "Sizing Best Practices - Configurations",
                ("configuration", "sizing", "vm specifications", "capacity", "resource pool"),
                tuple(
                    url
                    for url in platform_refs
                    if any(token in url for token in ("configuration", "vm-specifications", "reference-architecture-vm-specifications"))
                ),
                (),
                8,
            ),
            (
                "Access Architecture",
                ("external access", "uag", "access architecture", "edge", "remote access"),
                tuple(url for url in platform_refs if "unified-access-gateway" in url or "horizon-8-architecture" in url),
                tuple(keyword for keyword in (access, dmz) if keyword),
                10,
            ),
            (
                "Networking - Network Flows and Ports",
                ("network ports", "network flows", "ports", "traffic flows"),
                tuple(url for url in platform_refs if "network-ports-horizon-8" in url),
                (),
                12,
            ),
            (
                "Networking - Internal Connections",
                ("internal", "blast", "pcoip", "rdp"),
                tuple(url for url in platform_refs if "network-ports-horizon-8" in url or "understand-and-troubleshoot-horizon-connections" in url),
                tuple(keyword for keyword in (protocols,) if keyword),
                10,
            ),
            (
                "Networking - External Connections",
                ("external", "blast", "pcoip", "rdp", "gateway"),
                tuple(url for url in platform_refs if "network-ports-horizon-8" in url or "understand-and-troubleshoot-horizon-connections" in url),
                tuple(keyword for keyword in (protocols, access) if keyword),
                10,
            ),
            (
                "Overall Design",
                ("reference architecture", "overall", "deployment architecture", "environment infrastructure", "dmz"),
                tuple(url for url in platform_refs if any(token in url for token in ("environment-infrastructure-design", "architecture", "reference-architecture-overview"))),
                tuple(keyword for keyword in (track,) if keyword),
                10,
            ),
        ]

        selected: list[dict[str, str]] = []
        used_paths: set[str] = set()
        for slide_title, section_keywords, preferred_urls, answer_keywords, min_score in plans:
            if len(selected) >= limit:
                break
            row = self._pick_best_section_image(
                rows=rows,
                used_paths=used_paths,
                slide_title=slide_title,
                section_keywords=section_keywords,
                preferred_urls=preferred_urls,
                answer_keywords=answer_keywords,
                min_score=min_score,
            )
            if row:
                selected.append(row)

        if selected:
            return selected[:limit]

        fallback = self.best_images_for_urls(reference_urls, limit=limit)
        for idx, row in enumerate(fallback, start=1):
            row["slide_title"] = row.get("title", f"Architecture Diagram {idx}")
        return fallback

    @staticmethod
    def _canonical_url(url: str) -> str:
        try:
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        except Exception:
            return url

    def list_ingested_images(self, limit: int = 100) -> list[dict[str, str]]:
        source_rows = self._load_caption_rows()
        if not source_rows:
            source_rows = self._rows_from_audit(limit=max(limit * 5, 200))
            if source_rows:
                self._save_caption_rows(source_rows)
        if source_rows:
            source_rows = self._backfill_missing_captions(source_rows, max_updates=min(max(limit, 10), 60))
        if not source_rows:
            return []
        rows: list[dict[str, str]] = []
        changed = False
        for row in source_rows:
            local_path = str(row.get("local_path", "")).strip()
            if local_path and not Path(local_path).exists():
                changed = True
                continue
            if row.get("image_type") == "architecture_diagram":
                rows.append(row)
            if len(rows) >= limit:
                break
        if changed:
            all_rows = self._load_caption_rows()
            self._save_caption_rows(all_rows)
        return rows

    @staticmethod
    def _append_audit(audit_file: Path, row: dict[str, object]) -> None:
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ts": datetime.now(timezone.utc).isoformat(), **row}
        with audit_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
