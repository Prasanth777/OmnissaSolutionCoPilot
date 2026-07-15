from __future__ import annotations

import json
from types import SimpleNamespace

from sa_hld_bot.diagram_qa import (
    DiagramQuestionIntent,
    RequestedDiagramAttributes,
    answer_diagram_question,
    build_selection_evidence,
    extract_diagram_intent,
    looks_like_diagram_selection_question,
)


ACTIVE_PASSIVE_ANSWERS = {
    "availability_requirements": "Multi-site active/passive",
    "site_topology": "Multi-site",
}


class FakeFoundry:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.settings = SimpleNamespace(azure_chat_deployment="test-model")

    def _create_chat_completion(self, **_kwargs):
        content = self.responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def test_typo_active_ative_is_understood_offline():
    intent = extract_diagram_intent(None, "why wasnt multi site active ative diagram picked?")

    assert intent.intent == "explain_selection"
    assert intent.requested.ha_model == "active_active"
    assert intent.requested.sites == "multi"


def test_what_wasnt_single_dmz_selected_is_a_selection_question():
    assert looks_like_diagram_selection_question("what wasnt single dmz selected?")
    intent = extract_diagram_intent(None, "what wasnt single dmz selected?")

    assert intent.intent == "explain_selection"
    assert intent.requested.dmz == "single"


def test_single_dmz_conflict_leads_with_verified_selection_reason():
    result = answer_diagram_question(
        None,
        "what wasnt single dmz selected?",
        {
            "horizon_dmz_design": "Double DMZ",
            "access_type": "Both internal and external",
        },
        ["horizon_8"],
        [],
        [],
        [],
    )

    assert "**single DMZ** diagram was not selected" in result.answer
    assert "**Horizon DMZ Design** value is **Double DMZ**" in result.answer
    assert "DMZ-layout rule" in result.answer
    assert result.suggested_action["answer_value"] == "Single DMZ"
    assert any("DMZ-layout conflict" in detail for detail in result.evidence_steps)


def test_active_active_conflict_uses_questionnaire_answer():
    result = answer_diagram_question(
        None,
        "why wasnt multi site active active diagram picked?",
        ACTIVE_PASSIVE_ANSWERS,
        ["horizon_8"],
        [],
        [],
        [],
    )

    assert "multi-site **active-active** diagram was not selected" in result.answer.lower()
    assert "**Multi-site active/passive**" in result.answer
    assert "availability-model rule" in result.answer
    assert result.suggested_action["answer_value"] == "Multi-site active/active"


def test_bad_ai_wording_is_rejected_for_deterministic_fallback():
    intent_json = json.dumps({
        "intent": "explain_selection",
        "requested": {
            "ha_model": "active_active",
            "sites": "multi",
            "dmz": "",
            "access": "",
            "platform": "horizon_8",
        },
        "terms": [],
        "ambiguous": False,
        "clarification": "",
    })
    foundry = FakeFoundry([intent_json, "The active-passive figure was included."])

    result = answer_diagram_question(
        foundry,
        "why wasnt the active active diagram picked?",
        ACTIVE_PASSIVE_ANSWERS,
        ["horizon_8"],
        [],
        [],
        [],
    )

    assert "active-active** diagram was not selected" in result.answer.lower()
    assert "Multi-site active/passive" in result.answer


def test_ambiguous_diagram_question_requests_clarification():
    result = answer_diagram_question(
        None,
        "why wasn't the diagram picked?",
        ACTIVE_PASSIVE_ANSWERS,
        ["horizon_8"],
        [],
        [],
        [],
    )

    assert result.clarification
    assert "active-active" in result.answer


def test_matching_selected_active_active_candidate_is_reported_included():
    row = {
        "caption": "Figure 33: Active-active architecture",
        "page_url": "https://techzone.omnissa.com/resource/horizon-8-architecture",
        "local_path": "/tmp/active-active-test.png",
        "site_topology": "multisite_active_active",
        "image_type": "architecture_diagram",
    }
    intent = DiagramQuestionIntent(
        intent="explain_selection",
        requested=RequestedDiagramAttributes(ha_model="active_active", sites="multi"),
    )
    evidence = build_selection_evidence(
        intent,
        {
            "availability_requirements": "Multi-site active/active",
            "site_topology": "Multi-site",
        },
        ["horizon_8"],
        [row["page_url"]],
        [row],
        [row],
    )

    assert evidence.outcome == "included"
    assert evidence.violated_rules == []
