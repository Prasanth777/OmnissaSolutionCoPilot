from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .catalog import TECHZONE_DOMAIN


SUSPICIOUS_PATTERNS = (
    r"ignore (all|previous) instructions",
    r"system prompt",
    r"developer message",
    r"exfiltrate",
    r"bypass",
    r"jailbreak",
)


@dataclass(frozen=True)
class GuardrailResult:
    allowed: bool
    reason: str = ""


def is_safe_user_input(text: str) -> GuardrailResult:
    lowered = text.lower()
    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, lowered):
            return GuardrailResult(False, "Potential prompt-injection pattern detected.")
    return GuardrailResult(True)


def is_techzone_source(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.netloc == TECHZONE_DOMAIN


def all_sources_techzone(urls: list[str]) -> GuardrailResult:
    bad = [url for url in urls if not is_techzone_source(url)]
    if bad:
        return GuardrailResult(False, f"Non-Tech Zone source blocked: {bad[0]}")
    return GuardrailResult(True)


def ensure_grounded_answer(answer: str, citations: list[str]) -> GuardrailResult:
    if not citations:
        return GuardrailResult(False, "No citations found. Answer must come from RAG.")
    if not answer.strip():
        return GuardrailResult(False, "Empty answer.")
    source_check = all_sources_techzone(citations)
    if not source_check.allowed:
        return source_check
    return GuardrailResult(True)
