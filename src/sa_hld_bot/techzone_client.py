from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .catalog import TECHZONE_DOMAIN, is_allowed_techzone_url


DEFAULT_TIMEOUT = 20
USER_AGENT = "SA-HLD-Bot/1.0"


@dataclass
class PageMedia:
    page_url: str
    image_url: str | None = None
    caption: str = ""


class TechZoneClient:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"https://{TECHZONE_DOMAIN}/",
            }
        )

    def fetch_html(self, url: str) -> str:
        self._validate_url(url)
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.text

    def extract_first_allowed_image(self, page_url: str) -> PageMedia:
        html = self.fetch_html(page_url)
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup.find_all("img"):
            candidate = tag.get("src") or tag.get("data-src") or tag.get("data-lazy-src")
            if not candidate:
                continue
            image_url = urljoin(page_url, candidate)
            if not is_allowed_techzone_url(image_url):
                continue
            alt_text = (tag.get("alt") or "").strip()
            return PageMedia(page_url=page_url, image_url=image_url, caption=alt_text)
        return PageMedia(page_url=page_url)

    def download_image_bytes(self, image_url: str) -> BytesIO:
        self._validate_url(image_url)
        response = self.session.get(image_url, timeout=self.timeout)
        response.raise_for_status()
        return BytesIO(response.content)

    def extract_page_bullets(self, page_url: str, limit: int = 5) -> list[str]:
        html = self.fetch_html(page_url)
        soup = BeautifulSoup(html, "html.parser")
        bullets: list[str] = []

        for tag in soup.find_all(["h2", "h3", "li", "p"]):
            text = " ".join(tag.get_text(" ", strip=True).split())
            if len(text) < 30:
                continue
            bullets.append(text)
            if len(bullets) >= limit:
                break
        return bullets

    def batch_extract_media(self, urls: Iterable[str]) -> list[PageMedia]:
        media: list[PageMedia] = []
        for url in urls:
            try:
                media.append(self.extract_first_allowed_image(url))
            except Exception:
                media.append(PageMedia(page_url=url))
        return media

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.netloc != TECHZONE_DOMAIN:
            raise ValueError(f"Only {TECHZONE_DOMAIN} URLs are allowed, received: {url}")
