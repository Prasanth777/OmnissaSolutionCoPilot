"""Conversational HLD editing: route a free-text request into a structured edit
operation over the generated HLD.

Supported intents (beyond the image commands in image_followup.py):
- update_answer   : change a design input ("switch to double DMZ", "make it multi-site")
- rewrite_section : revise a section's narrative with an instruction
                    ("expand the networking section with port details")
- add_section     : add a new TechZone-grounded section ("add a section on printing")
- remove_section  : drop a section ("remove the business continuity section")
- regenerate_all  : rebuild the whole HLD from scratch
- qa              : not an edit — answer as a grounded question

All narrative content is produced by the caller through the TechZone-only RAG
orchestrator, so every edit stays grounded in techzone.omnissa.com sources.
"""
from __future__ import annotations

import json
import re

# Core document sections that can be rewritten or removed. Product detailed-design
# sections are addressed by their product key (e.g. horizon_8).
CORE_SECTIONS = {
    "summary": "Overview / executive summary",
    "architecture": "Solution Overview",
    "networking": "Networking Requirements",
    "security": "Security Standards",
    "operations": "Business Continuity and Recovery",
}
REMOVABLE_STRUCTURAL = {
    "key_contacts": "Key Contacts",
    "requirements": "Requirements and Considerations",
    "additional_views": "Additional Architecture Views",
    "review_acceptance": "Review and Acceptance",
}

_EDIT_HINTS = (
    "section", "rewrite", "expand", "shorten", "elaborate", "add ", "remove", "delete",
    "drop", "change", "update", "modify", "switch", "make it", "instead", "regenerate",
    "rebuild", "revise", "improve", "detail", "summarize", "shorter", "longer",
)


def looks_like_edit_command(text: str) -> bool:
    t = (text or "").lower()
    return any(h in t for h in _EDIT_HINTS)


def parse_hld_command(foundry, text: str, answer_keys: list[str], product_keys: list[str]) -> dict:
    """LLM-extract a structured HLD edit op. Falls back to {'action': 'qa'}."""
    base: dict = {
        "action": "qa", "answer_key": "", "answer_value": "",
        "section": "", "instruction": "", "title": "", "keywords": [],
    }
    sections = list(CORE_SECTIONS) + list(REMOVABLE_STRUCTURAL) + list(product_keys)
    try:
        model = foundry.settings.azure_chat_deployment
        resp = foundry._create_chat_completion(
            model=model,
            temperature=0.0,
            messages=[
                {"role": "system", "content": (
                    "You convert a user's request about editing a generated High-Level Design "
                    "document into JSON. Respond ONLY with compact JSON:\n"
                    '{"action":"update_answer|rewrite_section|add_section|remove_section|regenerate_all|qa",'
                    '"answer_key":"","answer_value":"","section":"","instruction":"","title":"","keywords":[]}\n'
                    "update_answer = the user changes a design input/decision (value goes in answer_value; "
                    f"answer_key must be one of: {', '.join(sorted(answer_keys)[:60])}).\n"
                    f"rewrite_section/remove_section: section must be one of: {', '.join(sections)}.\n"
                    "rewrite_section = revise/expand/shorten an existing section per the instruction.\n"
                    "add_section = add a new topic section; put the heading in title, the request in "
                    "instruction, and 2-4 lowercase diagram search words in keywords.\n"
                    "regenerate_all = rebuild the entire document.\n"
                    "qa = a question, not an edit."
                )},
                {"role": "user", "content": text},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
    except Exception:
        return base

    out = dict(base)
    action = str(data.get("action", "")).lower()
    if action in ("update_answer", "rewrite_section", "add_section", "remove_section", "regenerate_all", "qa"):
        out["action"] = action
    out["answer_key"] = re.sub(r"[^a-z0-9_]", "", str(data.get("answer_key", "")).lower())
    out["answer_value"] = str(data.get("answer_value", "")).strip()
    out["section"] = re.sub(r"[^a-z0-9_]", "", str(data.get("section", "")).lower())
    out["instruction"] = str(data.get("instruction", "")).strip() or text
    out["title"] = str(data.get("title", "")).strip()
    out["keywords"] = [str(k).lower().strip() for k in (data.get("keywords") or []) if str(k).strip()][:6]

    # Validate references; degrade to qa rather than acting on a bad target.
    if out["action"] == "update_answer" and out["answer_key"] not in set(answer_keys):
        out["action"] = "qa"
    if out["action"] in ("rewrite_section", "remove_section") and out["section"] not in set(sections):
        out["action"] = "qa"
    if out["action"] == "add_section" and not out["title"]:
        out["title"] = out["instruction"][:60].title()
    return out


def conversational_rationale(foundry, question: str, facts: str) -> str:
    """Rationale agent: turn the selection engine's verified facts into a direct,
    conversational answer to the user's actual question.

    The engine's deterministic analysis stays the single source of truth — the
    model only rephrases it; on any failure the raw facts are returned.
    """
    try:
        model = foundry.settings.azure_chat_deployment
        resp = foundry._create_chat_completion(
            model=model,
            temperature=0.3,
            messages=[
                {"role": "system", "content": (
                    "You are the Solution Architect CoPilot, a helpful chatbot for an HLD generator. "
                    "Answer the user's question directly and conversationally in 3-6 sentences, using ONLY "
                    "the verified facts provided — never invent rules, answers, or diagram details. "
                    "Name the diagram, state plainly whether it is in the current HLD, and explain the reason "
                    "in terms of the customer's design answers (e.g. 'because you selected a load balancer in "
                    "front of UAG, the Connection Server load-balancing view conflicts'). "
                    "If the facts include a way to change the outcome, end with that as a short suggestion. "
                    "Plain prose, no headings; you may keep the source URL."
                )},
                {"role": "user", "content": (
                    f"User question: {question or 'Why was this diagram not selected?'}\n\n"
                    f"Verified facts from the selection engine:\n{facts}"
                )},
            ],
        )
        answer = (resp.choices[0].message.content or "").strip()
        return answer or facts
    except Exception:
        return facts


def section_display_name(section: str, product_titles: dict[str, str]) -> str:
    if section in CORE_SECTIONS:
        return CORE_SECTIONS[section]
    if section in REMOVABLE_STRUCTURAL:
        return REMOVABLE_STRUCTURAL[section]
    return product_titles.get(section, section.replace("_", " ").title())
