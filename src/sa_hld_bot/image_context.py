"""Structured per-image context extraction for Tech Zone pages.

DOM traits confirmed via scripts/inspect_techzone_page.py:
- No <figcaption>; the human caption is the paragraph right after the <img>,
  prefixed "Figure N:".
- alt text is auto-generated junk (often mislabels diagrams as "screenshot"),
  so it is NOT trusted for classification.
- Real diagrams live under /sites/default/files/...; chrome lives under /dist/img/.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

ICON_HINTS = ("icon", "logo", "avatar", "author", "profile", "favicon", "sprite", "badge")
FIGURE_RE = re.compile(r"^\s*figure\s*\d+\s*[:.\-]", re.IGNORECASE)
MIN_PARA_LEN = 25

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def _is_data_uri(url: str) -> bool:
    return url.strip().lower().startswith("data:")

def _best_src(img) -> str:
    """Pick the real image URL, preferring lazy attrs and skipping data: placeholders."""
    for key in ("data-srcset", "srcset", "data-src", "data-lazy-src", "src"):
        value = (img.get(key) or "").strip()
        if not value:
            continue
        if key.endswith("srcset"):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            if parts:
                value = parts[-1].split(" ")[0].strip()
        if value and not _is_data_uri(value):
            return value
    return ""

def _is_chrome(path: str, src: str) -> bool:
    low = (path or "").lower()
    if not src or _is_data_uri(src):
        return True
    if low.startswith("/dist/") or "/themes/" in low or "/core/" in low:
        return True
    if low.endswith(".svg"):
        return True
    if any(tok in low for tok in ICON_HINTS):
        return True
    return False

def _nearest_heading(img):
    for prev in img.find_all_previous(["h1", "h2", "h3", "h4"]):
        text = _norm(prev.get_text(" ", strip=True))
        if text:
            return text, str(prev.get("id") or "")
    return "", ""

def _figure_caption(img) -> str:
    """figcaption if present, else the first following paragraph matching 'Figure N:'."""
    figure = img.find_parent("figure")
    if figure:
        cap = figure.find("figcaption")
        if cap:
            text = _norm(cap.get_text(" ", strip=True))
            if text:
                return text
    for nxt in img.find_all_next(["p", "li", "h1", "h2", "h3", "h4"]):
        if nxt.name in {"h1", "h2", "h3", "h4"}:
            break  # don't cross into the next section
        text = _norm(nxt.get_text(" ", strip=True))
        if not text:
            continue
        if FIGURE_RE.match(text):
            return text
        break  # the immediately-adjacent non-figure paragraph is context, not caption
    return ""

def _context_before(img) -> str:
    """Closest preceding explanatory paragraph that is not itself a Figure caption."""
    for prev in img.find_all_previous(["p", "li"]):
        text = _norm(prev.get_text(" ", strip=True))
        if len(text) >= MIN_PARA_LEN and not FIGURE_RE.match(text):
            return text
    return ""

def derive_dimension_hints(text: str) -> dict:
    t = f" {text.lower()} "
    has_uag = ("unified access gateway" in t) or (" uag " in t)
    has_external = has_uag or ("external" in t) or ("dmz" in t) or ("internet" in t)
    has_internal = "internal" in t
    if has_internal and has_external:
        access = "both"
    elif has_external:
        access = "external"
    elif has_internal:
        access = "internal"
    else:
        access = ""

    if "double dmz" in t:
        dmz = "double"
    elif "single dmz" in t or "dmz" in t:
        dmz = "single"
    else:
        dmz = "none"

    protocols = [p for p, kw in (("blast", "blast"), ("pcoip", "pcoip"), ("rdp", "rdp")) if kw in t]

    if "cloud pod" in t or "cpa" in t:
        site = "cloud_pod"
    elif "active-active" in t or "active/active" in t:
        site = "multisite_active_active"
    elif "active-passive" in t or "active/passive" in t:
        site = "multisite_active_passive"
    elif "multi-site" in t or "multisite" in t:
        site = "multisite"
    elif "single-site" in t or "single site" in t:
        site = "single_site"
    else:
        site = ""

    components = [
        name for name, kw in (
            ("connection_server", "connection server"),
            ("uag", "unified access gateway"),
            ("horizon_agent", "horizon agent"),
            ("active_directory", "active directory"),
            ("load_balancer", "load balanc"),
            ("true_sso", "true sso"),
            ("enrollment_server", "enrollment server"),
            ("horizon_edge", "horizon edge"),
        ) if kw in t
    ]
    return {
        "access_scope": access,
        "uag_present": has_uag,
        "dmz_design": dmz,
        "protocols": protocols,
        "site_topology": site,
        "components_shown": components,
    }

@dataclass
class ImageRecord:
    page_url: str
    page_title: str
    image_url: str
    section_heading: str
    section_anchor: str
    figure_caption: str
    context_text: str
    alt: str
    width_attr: str
    height_attr: str
    under_files: bool
    embed_text: str
    hints: dict

    def to_dict(self) -> dict:
        return asdict(self)

def extract_image_records(soup: BeautifulSoup, page_url: str, page_title: str) -> list[ImageRecord]:
    records: list[ImageRecord] = []
    for img in soup.find_all("img"):
        src = _best_src(img)
        resolved = urljoin(page_url, src) if src else ""
        path = urlparse(resolved).path if resolved else ""
        if _is_chrome(path, resolved):
            continue

        heading, anchor = _nearest_heading(img)
        caption = _figure_caption(img)
        context = _context_before(img)
        alt = _norm(img.get("alt", ""))

        under_files = "/sites/default/files/" in path
        if not under_files and not caption and not heading:
            continue

        combined = " ".join(filter(None, [page_title, heading, caption, context]))
        record = ImageRecord(
            page_url=page_url,
            page_title=page_title,
            image_url=resolved,
            section_heading=heading,
            section_anchor=anchor,
            figure_caption=caption,
            context_text=context,
            alt=alt,
            width_attr=str(img.get("width", "") or ""),
            height_attr=str(img.get("height", "") or ""),
            under_files=under_files,
            embed_text=_norm(combined),
            hints=derive_dimension_hints(f"{heading} {caption} {context}"),
        )
        records.append(record)
    return records
