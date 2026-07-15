"""Grounded, agentic answers for architecture-diagram selection questions.

The model interprets the question and phrases verified facts. Selection rules
and questionnaire answers remain the authority for every explanation.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Callable, Literal

from pydantic import BaseModel, Field

from .catalog import normalize_answer
from .image_select import (
    _canon,
    _relevant,
    conflicting_rules,
    diagram_profile,
    image_content_keys,
    requirement_profile,
)


ProgressCallback = Callable[[str], None]


class RequestedDiagramAttributes(BaseModel):
    ha_model: Literal["active_active", "active_passive", ""] = ""
    sites: Literal["single", "multi", ""] = ""
    dmz: Literal["single", "double", "none", ""] = ""
    access: Literal["internal", "external", "both", ""] = ""
    platform: Literal["horizon_8", "horizon_cloud", ""] = ""


class DiagramQuestionIntent(BaseModel):
    intent: Literal["explain_selection", "modify_selection", "general"] = "general"
    requested: RequestedDiagramAttributes = Field(default_factory=RequestedDiagramAttributes)
    terms: list[str] = Field(default_factory=list)
    ambiguous: bool = False
    clarification: str = ""


class DiagramSelectionEvidence(BaseModel):
    outcome: Literal[
        "excluded_conflict", "included", "eligible_outranked", "not_considered", "unknown"
    ] = "unknown"
    requested: RequestedDiagramAttributes
    design_profile: dict[str, str] = Field(default_factory=dict)
    questionnaire_values: dict[str, str] = Field(default_factory=dict)
    violated_rules: list[str] = Field(default_factory=list)
    candidate_caption: str = ""
    candidate_source: str = ""
    selected_alternative: str = ""


class DiagramQAResult(BaseModel):
    answer: str
    evidence_steps: list[str] = Field(default_factory=list)
    clarification: str = ""
    suggested_action: dict[str, str] = Field(default_factory=dict)
    requires_full_rerun: bool = False


_INTENT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "diagram_question_intent",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["explain_selection", "modify_selection", "general"],
                },
                "requested": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "ha_model": {"type": "string", "enum": ["active_active", "active_passive", ""]},
                        "sites": {"type": "string", "enum": ["single", "multi", ""]},
                        "dmz": {"type": "string", "enum": ["single", "double", "none", ""]},
                        "access": {"type": "string", "enum": ["internal", "external", "both", ""]},
                        "platform": {"type": "string", "enum": ["horizon_8", "horizon_cloud", ""]},
                    },
                    "required": ["ha_model", "sites", "dmz", "access", "platform"],
                },
                "terms": {"type": "array", "items": {"type": "string"}},
                "ambiguous": {"type": "boolean"},
                "clarification": {"type": "string"},
            },
            "required": ["intent", "requested", "terms", "ambiguous", "clarification"],
        },
    },
}


_STOPWORDS = {
    "diagram", "image", "figure", "picture", "shown", "included", "selected",
    "picked", "output", "why", "wasnt", "wasn", "isnt", "this", "that", "the", "not",
    "was", "multi", "site", "active", "passive",
}


def _similar(left: str, right: str) -> bool:
    return SequenceMatcher(None, left, right).ratio() >= 0.72


def looks_like_diagram_selection_question(text: str) -> bool:
    """Recognize natural and imperfect requests for a diagram-selection reason."""
    normalized = str(text or "").lower()
    asks_for_reason = any(
        word in normalized for word in ("why", "rationale", "reason", "explain")
    )
    asks_about_missing_selection = bool(
        re.search(r"\b(?:what|how come)\b.*\b(?:wasn'?t|was not|didn'?t|not)\b", normalized)
        and re.search(r"\b(?:selected|picked|shown|included|chosen)\b", normalized)
    )
    diagram_language = any(
        word in normalized
        for word in (
            "diagram", "image", "figure", "picture", "architecture",
            "dmz", "active", "passive", "uag", "multi-site", "multisite",
        )
    )
    return diagram_language and (asks_for_reason or asks_about_missing_selection)


def _lexical_intent(question: str) -> DiagramQuestionIntent:
    """Offline typo-tolerant fallback for common architecture attributes."""
    text = str(question or "").lower()
    words = re.findall(r"[a-z0-9]+", text)
    ha_model = ""
    for idx, word in enumerate(words):
        if not _similar(word, "active"):
            continue
        following = words[idx + 1] if idx + 1 < len(words) else ""
        if _similar(following, "passive"):
            ha_model = "active_passive"
            break
        if _similar(following, "active"):
            ha_model = "active_active"
            break

    sites = "multi" if re.search(r"multi[\s/-]*site", text) else "single" if "single site" in text else ""
    dmz = "double" if "double dmz" in text else "single" if "single dmz" in text else ""
    platform = "horizon_cloud" if "horizon cloud" in text else "horizon_8" if "horizon 8" in text else ""
    terms = list(dict.fromkeys(
        word for word in words if len(word) > 3 and word not in _STOPWORDS
    ))[:8]
    explanation = looks_like_diagram_selection_question(text)
    requested = RequestedDiagramAttributes(
        ha_model=ha_model,
        sites=sites,
        dmz=dmz,
        platform=platform,
    )
    has_attributes = any(requested.model_dump().values())
    ambiguous = explanation and not has_attributes and not terms
    return DiagramQuestionIntent(
        intent="explain_selection" if explanation else "general",
        requested=requested,
        terms=terms,
        ambiguous=ambiguous,
        clarification=(
            "Which diagram or architecture attribute do you mean—for example active-active, active-passive, or a DMZ layout?"
            if ambiguous else ""
        ),
    )


def extract_diagram_intent(foundry, question: str) -> DiagramQuestionIntent:
    """Use schema-validated AI extraction, with a deterministic offline fallback."""
    fallback = _lexical_intent(question)
    if foundry is None:
        return fallback
    try:
        model = foundry.settings.azure_chat_deployment
        response = foundry._create_chat_completion(
            model=model,
            temperature=0.0,
            response_format=_INTENT_RESPONSE_FORMAT,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Interpret only the user's architecture-diagram question. Correct obvious typos. "
                        "Do not infer anything from customer design answers because none are provided. "
                        "Return ONLY JSON matching: "
                        '{"intent":"explain_selection|modify_selection|general",'
                        '"requested":{"ha_model":"active_active|active_passive|",'
                        '"sites":"single|multi|","dmz":"single|double|none|",'
                        '"access":"internal|external|both|",'
                        '"platform":"horizon_8|horizon_cloud|"},'
                        '"terms":["short","diagram","terms"],'
                        '"ambiguous":false,"clarification":""}. '
                        "For misspellings such as 'active ative', infer active_active when the meaning is clear."
                    ),
                },
                {"role": "user", "content": question},
            ],
        )
        raw = (response.choices[0].message.content or "").replace("```json", "").replace("```", "").strip()
        parsed = DiagramQuestionIntent.model_validate(json.loads(raw))
        # The lexical layer is authoritative when it identifies an attribute
        # that the model accidentally omitted.
        merged = parsed.requested.model_dump()
        for key, value in fallback.requested.model_dump().items():
            if value and not merged.get(key):
                merged[key] = value
        parsed.requested = RequestedDiagramAttributes(**merged)
        parsed.terms = list(dict.fromkeys(parsed.terms + fallback.terms))[:8]
        return parsed
    except Exception:
        return fallback


def _row_is_shown(row: dict, shown_rows: list[dict]) -> bool:
    path = str(row.get("local_path", ""))
    md5, ahash = image_content_keys(path)
    keys = {key for key in (path, md5, ahash) if key}
    for shown in shown_rows or []:
        shown_path = str(shown.get("local_path", ""))
        shown_md5, shown_ahash = image_content_keys(shown_path)
        if keys & {key for key in (shown_path, shown_md5, shown_ahash) if key}:
            return True
    return False


def _candidate_score(row: dict, intent: DiagramQuestionIntent) -> int:
    profile = diagram_profile(row)
    requested = intent.requested.model_dump()
    score = 0
    for key, value in requested.items():
        if value and profile.get(key) == value:
            score += 20
        elif value and profile.get(key):
            return -1
    haystack = " ".join(str(row.get(key, "")) for key in (
        "caption", "figure_caption", "title", "topic", "section_heading", "context_text"
    )).lower()
    score += sum(2 for term in intent.terms if term in haystack)
    return score


def build_selection_evidence(
    intent: DiagramQuestionIntent,
    answers: dict[str, str],
    selected_products: list[str],
    reference_urls: list[str],
    shown_rows: list[dict],
    candidate_rows: list[dict],
) -> DiagramSelectionEvidence:
    req = requirement_profile(answers, selected_products)
    requested = intent.requested
    questionnaire = {
        "availability_requirements": normalize_answer(answers.get("availability_requirements")),
        "site_topology": normalize_answer(answers.get("site_topology")),
        "horizon_dmz_design": normalize_answer(answers.get("horizon_dmz_design")),
        "access_type": normalize_answer(answers.get("access_type")),
    }
    design_profile = {
        key: str(req.get(key, ""))
        for key in ("ha_model", "sites", "dmz", "access", "cloud")
        if req.get(key, "")
    }
    violated: list[str] = []
    if requested.ha_model and req.get("ha_model") and requested.ha_model != req.get("ha_model"):
        violated.append("availability_model")
    if requested.sites and req.get("sites") and requested.sites != req.get("sites"):
        violated.append("sites")
    if requested.dmz and req.get("dmz") and requested.dmz != req.get("dmz"):
        violated.append("dmz")
    if requested.access and req.get("access") and requested.access != req.get("access"):
        violated.append("access")

    ranked = sorted(
        ((_candidate_score(row, intent), row) for row in candidate_rows or []),
        key=lambda item: item[0], reverse=True,
    )
    candidate = next((row for score, row in ranked if score >= 0 and score > 0), None)
    caption = ""
    source = ""
    if candidate:
        caption = str(candidate.get("caption") or candidate.get("figure_caption") or candidate.get("title") or "")
        source = str(candidate.get("page_url", ""))

    selected_alternative = ""
    requested_ha = requested.ha_model
    opposite = "active_passive" if requested_ha == "active_active" else "active_active"
    for shown in shown_rows or []:
        shown_profile = diagram_profile(shown)
        is_selected_alternative = (
            requested_ha and shown_profile.get("ha_model") == opposite
        ) or (
            requested.dmz
            and req.get("dmz")
            and shown_profile.get("dmz") == req.get("dmz")
            and shown_profile.get("dmz") != requested.dmz
        )
        if is_selected_alternative:
            selected_alternative = str(shown.get("caption") or shown.get("figure_caption") or shown.get("title") or "")
            break

    if violated:
        outcome = "excluded_conflict"
    elif candidate and _row_is_shown(candidate, shown_rows):
        outcome = "included"
    elif candidate:
        candidate_violations = conflicting_rules(candidate, answers, selected_products)
        if candidate_violations:
            violated.extend(name for name in candidate_violations if name not in violated)
            outcome = "excluded_conflict"
        elif not _relevant(candidate, {_canon(url) for url in reference_urls or []}):
            outcome = "not_considered"
        else:
            outcome = "eligible_outranked"
    else:
        outcome = "unknown"

    return DiagramSelectionEvidence(
        outcome=outcome,
        requested=requested,
        design_profile=design_profile,
        questionnaire_values={key: value for key, value in questionnaire.items() if value},
        violated_rules=violated,
        candidate_caption=caption,
        candidate_source=source,
        selected_alternative=selected_alternative,
    )


def _ha_label(value: str) -> str:
    return "active-active" if value == "active_active" else "active-passive" if value == "active_passive" else value


def _dmz_label(value: str) -> str:
    return "single DMZ" if value == "single" else "double DMZ" if value == "double" else "no DMZ" if value == "none" else value


def _evidence_summary(evidence: DiagramSelectionEvidence) -> list[str]:
    requested = []
    if evidence.requested.ha_model:
        requested.append(f"availability model={_ha_label(evidence.requested.ha_model)}")
    if evidence.requested.sites:
        requested.append(f"site topology={evidence.requested.sites}-site")
    if evidence.requested.dmz:
        requested.append(f"DMZ layout={_dmz_label(evidence.requested.dmz)}")
    if evidence.requested.access:
        requested.append(f"access={evidence.requested.access}")
    details = [f"Requested diagram attributes: {', '.join(requested)}"] if requested else []
    labels = {
        "availability_requirements": "Availability Requirements",
        "site_topology": "Site Topology",
        "horizon_dmz_design": "Horizon DMZ Design",
        "access_type": "Access Type",
    }
    for key, value in evidence.questionnaire_values.items():
        if value and key in labels:
            details.append(f"Questionnaire decision: {labels[key]} = {value}")
    details.append(f"Selection outcome: {evidence.outcome.replace('_', ' ')}")
    if evidence.violated_rules:
        friendly_rules = {
            "availability_model": "availability-model conflict",
            "sites": "site-topology conflict",
            "dmz": "DMZ-layout conflict",
            "access": "access-model conflict",
        }
        details.append(
            "Selection rules: "
            + ", ".join(friendly_rules.get(rule, rule.replace("_", " ")) for rule in evidence.violated_rules)
        )
    if evidence.candidate_caption:
        details.append(f"Candidate checked: {evidence.candidate_caption}")
    if evidence.selected_alternative:
        details.append(f"Selected alternative: {evidence.selected_alternative}")
    return details


def deterministic_answer(evidence: DiagramSelectionEvidence) -> tuple[str, dict[str, str]]:
    requested_ha = evidence.requested.ha_model
    selected_ha = evidence.design_profile.get("ha_model", "")
    availability = evidence.questionnaire_values.get("availability_requirements", "")
    action: dict[str, str] = {}
    if "availability_model" in evidence.violated_rules and requested_ha and selected_ha:
        requested_label = _ha_label(requested_ha)
        selected_label = _ha_label(selected_ha)
        answer = (
            f"The multi-site **{requested_label}** diagram was not selected because the questionnaire's "
            f"**Availability Requirements** value is **{availability or selected_label}**. "
            "The availability-model rule excludes diagrams showing the opposite model, so an "
            f"**{selected_label}** diagram was selected instead."
        )
        action = {
            "type": "update_answer",
            "answer_key": "availability_requirements",
            "answer_value": f"Multi-site {requested_label.replace('-', '/')}",
            "label": f"Switch to multi-site {requested_label} and regenerate",
        }
        return answer, action
    requested_dmz = evidence.requested.dmz
    selected_dmz = evidence.design_profile.get("dmz", "")
    dmz_answer = evidence.questionnaire_values.get("horizon_dmz_design", "")
    if "dmz" in evidence.violated_rules and requested_dmz and selected_dmz:
        requested_label = _dmz_label(requested_dmz)
        selected_label = _dmz_label(selected_dmz)
        outcome_sentence = (
            f"The DMZ-layout rule excludes diagrams showing a conflicting DMZ pattern, so a "
            f"**{selected_label}** diagram was selected instead."
            if evidence.selected_alternative
            else f"The DMZ-layout rule therefore excluded the **{requested_label}** candidate from the HLD's selected diagram set."
        )
        answer = (
            f"The **{requested_label}** diagram was not selected because the questionnaire's "
            f"**Horizon DMZ Design** value is **{dmz_answer or selected_label}**. "
            f"{outcome_sentence}"
        )
        answer_value = {
            "single": "Single DMZ",
            "double": "Double DMZ",
            "none": "No DMZ / internal only",
        }[requested_dmz]
        action = {
            "type": "update_answer",
            "answer_key": "horizon_dmz_design",
            "answer_value": answer_value,
            "label": f"Switch to {requested_label} and regenerate",
        }
        return answer, action
    if evidence.outcome == "included":
        return f"**{evidence.candidate_caption or 'That diagram'}** is included in the current HLD.", action
    if evidence.outcome == "eligible_outranked":
        return (
            f"**{evidence.candidate_caption or 'That diagram'}** matched the design rules but was outranked "
            "by higher-scoring diagrams within the configured diagram limit."
        ), action
    if evidence.outcome == "not_considered":
        return (
            f"**{evidence.candidate_caption or 'That diagram'}** was not considered because its source was "
            "outside the selected product reference set."
        ), action
    if evidence.violated_rules:
        return (
            f"The requested diagram was excluded by these verified selection rules: "
            f"{', '.join(evidence.violated_rules)}."
        ), action
    return (
        "I could not identify the exact diagram or architecture attribute confidently. "
        "Please name the diagram or specify an attribute such as active-active, active-passive, or the DMZ layout."
    ), action


def _answer_is_valid(answer: str, evidence: DiagramSelectionEvidence) -> bool:
    text = str(answer or "").lower()
    if not text:
        return False
    if "availability_model" in evidence.violated_rules:
        requested = _ha_label(evidence.requested.ha_model)
        selected = evidence.questionnaire_values.get("availability_requirements", "").lower()
        return (
            requested in text
            and selected in text
            and ("not selected" in text or "excluded" in text)
            and "opposite" in text
        )
    if "dmz" in evidence.violated_rules:
        requested = _dmz_label(evidence.requested.dmz).lower()
        selected = evidence.questionnaire_values.get("horizon_dmz_design", "").lower()
        return (
            requested in text
            and selected in text
            and ("not selected" in text or "excluded" in text)
            and ("dmz-layout" in text or "dmz layout" in text)
        )
    if evidence.outcome == "included":
        return "included" in text
    return True


def _phrase_with_ai(foundry, question: str, evidence: DiagramSelectionEvidence) -> str:
    if foundry is None:
        return ""
    try:
        response = foundry._create_chat_completion(
            model=foundry.settings.azure_chat_deployment,
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Answer the architecture-diagram question in 2-4 direct sentences using ONLY the "
                        "verified JSON evidence. State the requested diagram, its actual selection outcome, "
                        "the exact questionnaire value, and the violated rule when present. Never substitute "
                        "a selected alternative for the diagram the user asked about. Return plain prose."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Question: {question}\nVerified evidence: {evidence.model_dump_json()}",
                },
            ],
        )
        return (response.choices[0].message.content or "").strip()
    except Exception:
        return ""


def answer_diagram_question(
    foundry,
    question: str,
    answers: dict[str, str],
    selected_products: list[str],
    reference_urls: list[str],
    shown_rows: list[dict],
    candidate_rows: list[dict],
    progress: ProgressCallback | None = None,
    understanding_reported: bool = False,
) -> DiagramQAResult:
    report = progress or (lambda _message: None)
    steps: list[str] = []

    def note(message: str) -> None:
        steps.append(message)
        report(message)

    if not understanding_reported:
        note("Understanding your question")
    cleaned_question = str(question or "").strip()
    note("Reading the relevant questionnaire decisions")
    verified_answers = dict(answers)
    note("Identifying the requested diagram attributes")
    intent = extract_diagram_intent(foundry, cleaned_question)
    if intent.ambiguous:
        clarification = intent.clarification or "Which diagram or architecture attribute do you mean?"
        return DiagramQAResult(answer=clarification, clarification=clarification)

    note("Replaying the diagram-selection rules")
    evidence = build_selection_evidence(
        intent, verified_answers, selected_products, reference_urls, shown_rows, candidate_rows
    )
    fallback, action = deterministic_answer(evidence)
    note("Validating the explanation against the evidence")
    generated = _phrase_with_ai(foundry, cleaned_question, evidence)
    answer = generated if _answer_is_valid(generated, evidence) else fallback
    return DiagramQAResult(
        answer=answer,
        evidence_steps=_evidence_summary(evidence),
        suggested_action=action,
    )
