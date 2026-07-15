from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from openai import AzureOpenAI, BadRequestError

from .config import Settings
from .logging_utils import get_logger


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    url: str
    title: str
    content: str
    score: float
    section_title: str = ""
    section_url: str = ""


class AzureFoundryClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = AzureOpenAI(
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_endpoint=settings.azure_openai_endpoint,
        )
        self.usage_logger = get_logger("sa_hld_bot.llm_usage", settings.logs_dir)
        # $/1K tokens for the chat deployment (e.g. gpt-5.2-chat); set via env
        # so pricing changes don't require a code change.
        self._price_in = float(os.getenv("AZURE_OPENAI_INPUT_PRICE_PER_1K", "0") or 0)
        self._price_out = float(os.getenv("AZURE_OPENAI_OUTPUT_PRICE_PER_1K", "0") or 0)
        self._session_prompt_tokens = 0
        self._session_completion_tokens = 0
        self._session_cost = 0.0

    def _record_usage(self, model: str, response: object, elapsed: float) -> None:
        try:
            usage = getattr(response, "usage", None)
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        except Exception:
            return
        cost = (prompt_tokens / 1000.0) * self._price_in + (completion_tokens / 1000.0) * self._price_out
        self._session_prompt_tokens += prompt_tokens
        self._session_completion_tokens += completion_tokens
        self._session_cost += cost
        self.usage_logger.info(
            "LLM call: model=%s prompt_tokens=%d completion_tokens=%d cost=$%.4f "
            "elapsed=%.1fs | session: tokens=%d/%d cost=$%.4f",
            model, prompt_tokens, completion_tokens, cost, elapsed,
            self._session_prompt_tokens, self._session_completion_tokens, self._session_cost,
        )
        try:
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": round(cost, 6),
                "elapsed_s": round(elapsed, 1),
            }
            path = self.settings.data_dir / "llm_usage.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except Exception:
            pass

    def _create_chat_completion(
        self,
        model: str,
        messages: list[dict],
        temperature: float | None,
        response_format: dict | None = None,
    ) -> object:
        kwargs = {
            "model": model,
            "messages": messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if response_format is not None:
            kwargs["response_format"] = response_format
        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(**kwargs)
        except BadRequestError as exc:
            # Some Azure deployments lock temperature to default and reject explicit overrides.
            message = str(exc).lower()
            if temperature is not None and "temperature" in message and "supported" in message:
                kwargs.pop("temperature", None)
                response = self.client.chat.completions.create(**kwargs)
            elif response_format is not None and (
                "response_format" in message or "json_schema" in message
            ):
                # Retain the prompt-level JSON contract for older Azure API
                # versions that do not yet accept structured-output options.
                kwargs.pop("response_format", None)
                response = self.client.chat.completions.create(**kwargs)
            else:
                raise
        self._record_usage(model, response, time.perf_counter() - started)
        return response

    def chat(self, system_prompt: str, user_prompt: str, temperature: float | None = 0.1) -> str:
        response = self._create_chat_completion(
            model=self.settings.azure_chat_deployment,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""

    def caption_image_from_url(self, image_url: str, page_url: str = "", page_title: str = "") -> str:
        vision_model = self.settings.azure_vision_deployment or self.settings.azure_chat_deployment
        response = self._create_chat_completion(
            model=vision_model,
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Write a concise architecture caption (max 18 words). "
                        "The caption MUST be consistent with the page context and MUST NOT introduce other cloud/platform names."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Caption this architecture image for customer-facing solution slides.\n"
                                f"Page title: {page_title}\n"
                                f"Page URL: {page_url}\n"
                                "Use the page context as the source of truth. "
                                "Do not mention a different cloud/provider/platform than the page context."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        )
        return (response.choices[0].message.content or "").strip()

    def classify_architecture_diagram_from_url(self, image_url: str) -> bool:
        vision_model = self.settings.azure_vision_deployment or self.settings.azure_chat_deployment
        response = self._create_chat_completion(
            model=vision_model,
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict image classifier for enterprise architecture slides. "
                        "Return only one token: ARCHITECTURE_DIAGRAM or NOT_ARCHITECTURE_DIAGRAM."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Classify this image. Architecture diagrams include layered blocks, components, "
                                "network/security zones, flows, or deployment topologies. "
                                "Icons, logos, avatars, decorations, and screenshots are NOT architecture diagrams."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        )
        verdict = (response.choices[0].message.content or "").strip().upper()
        return "ARCHITECTURE_DIAGRAM" in verdict and "NOT_ARCHITECTURE_DIAGRAM" not in verdict
