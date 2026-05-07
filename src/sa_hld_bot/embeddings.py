from __future__ import annotations

import os

class HuggingFaceEmbeddingService:
    _model_cache: dict[str, object] = {}

    def __init__(self, model_name: str) -> None:
        # Keep startup logs quiet in Streamlit (warnings can be very noisy but non-fatal).
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        try:
            from transformers.utils import logging as hf_logging

            hf_logging.set_verbosity_error()
        except Exception:
            pass
        try:
            from huggingface_hub.utils import disable_progress_bars

            disable_progress_bars()
        except Exception:
            pass

        self.model_name = model_name
        if model_name in self._model_cache:
            self.model = self._model_cache[model_name]
            return
        from sentence_transformers import SentenceTransformer

        try:
            model = SentenceTransformer(model_name)
        except Exception:
            # If HF Hub has transient network/DNS issues, use local cache when available.
            model = SentenceTransformer(model_name, local_files_only=True)
        self._model_cache[model_name] = model
        self.model = model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return vectors.tolist()

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]
