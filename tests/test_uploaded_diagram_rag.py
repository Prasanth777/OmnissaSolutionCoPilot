from __future__ import annotations

import json
from types import SimpleNamespace

from sa_hld_bot import feedback


class FakeFoundry:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.settings = SimpleNamespace(
            azure_chat_deployment="chat-model",
            azure_vision_deployment="vision-model",
        )

    def _create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        content = self.responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class FakeEmbeddingService:
    def __init__(self):
        self.queries: list[str] = []

    def embed_text(self, text: str):
        self.queries.append(text)
        return [0.1, 0.2]


class FakeImageCollection:
    def __init__(self, row: dict):
        self.row = row

    def count(self):
        return 1

    def query(self, **_kwargs):
        return {
            "documents": [["Figure 32 active-passive multi-site architecture"]],
            "metadatas": [[{
                "local_path": self.row["local_path"],
                "caption": self.row["caption"],
                "page_url": self.row["page_url"],
            }]],
            "distances": [[0.12]],
        }


class FakeStore:
    def __init__(self, row: dict, responses: list[str]):
        self.row = row
        self.foundry = FakeFoundry(responses)
        self.embedding_service = FakeEmbeddingService()
        self.image_collection = FakeImageCollection(row)

    def _load_caption_rows(self):
        return [self.row]

    def _ensure_image_collection_populated(self, _rows):
        return None


def test_direct_visual_match_is_preferred(monkeypatch):
    row = {"caption": "Exact diagram", "local_path": "/tmp/exact.png"}
    monkeypatch.setattr(feedback, "match_uploaded_image", lambda _rows, _data: row)

    match, evidence = feedback.match_uploaded_image_in_rag(
        SimpleNamespace(_load_caption_rows=lambda: [row]), b"image"
    )

    assert match == row
    assert evidence["method"] == "visual fingerprint"


def test_cropped_image_uses_vision_description_and_rag_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback, "match_uploaded_image", lambda _rows, _data: None)
    row = {
        "caption": "Figure 32: Active-passive architecture",
        "title": "Horizon 8 Architecture",
        "page_url": "https://techzone.omnissa.com/resource/horizon-8-architecture",
        "local_path": str(tmp_path / "figure-32.png"),
        "image_type": "architecture_diagram",
    }
    store = FakeStore(row, [
        "Two sites, Pod 1 active and Pod 2 standby, Cloud Pod Architecture, Figure 32.",
        json.dumps({"candidate_index": 0, "confidence": "high", "reason": "Distinctive labels agree."}),
    ])

    match, evidence = feedback.match_uploaded_image_in_rag(
        store, b"pasted-image", mime_type="image/png", rows=[row]
    )

    assert match["caption"] == row["caption"]
    assert evidence["method"] == "vision + RAG image search"
    assert evidence["confidence"] == "high"
    assert "Pod 1 active" in store.embedding_service.queries[0]
    assert store.foundry.calls[1]["response_format"]["type"] == "json_schema"


def test_low_confidence_rag_candidate_is_not_added(monkeypatch, tmp_path):
    monkeypatch.setattr(feedback, "match_uploaded_image", lambda _rows, _data: None)
    row = {
        "caption": "Unrelated architecture",
        "page_url": "https://techzone.omnissa.com/resource/example",
        "local_path": str(tmp_path / "unrelated.png"),
        "image_type": "architecture_diagram",
    }
    store = FakeStore(row, [
        "A diagram with unreadable labels.",
        json.dumps({"candidate_index": -1, "confidence": "low", "reason": "Evidence does not agree."}),
    ])

    match, evidence = feedback.match_uploaded_image_in_rag(store, b"pasted-image", rows=[row])

    assert match is None
    assert evidence["confidence"] == "low"
