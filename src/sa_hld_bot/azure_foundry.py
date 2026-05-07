from __future__ import annotations

from dataclasses import dataclass

from openai import AzureOpenAI, BadRequestError

from .config import Settings


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    url: str
    title: str
    content: str
    score: float


class AzureFoundryClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = AzureOpenAI(
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_endpoint=settings.azure_openai_endpoint,
        )

    def _create_chat_completion(self, model: str, messages: list[dict], temperature: float | None) -> object:
        kwargs = {
            "model": model,
            "messages": messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            return self.client.chat.completions.create(**kwargs)
        except BadRequestError as exc:
            # Some Azure deployments lock temperature to default and reject explicit overrides.
            message = str(exc).lower()
            if temperature is not None and "temperature" in message and "supported" in message:
                kwargs.pop("temperature", None)
                return self.client.chat.completions.create(**kwargs)
            raise

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
