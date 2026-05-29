from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_api_version: str
    azure_chat_deployment: str
    azure_vision_deployment: str
    hf_embedding_model: str
    chroma_dir: Path
    data_dir: Path
    logs_dir: Path
    images_dir: Path
    image_captions_file: Path
    collection_name: str
    image_collection_name: str
    max_images_per_page: int
    sitemap_resource_only: bool

    @property
    def configured(self) -> bool:
        required = (
            self.azure_openai_endpoint,
            self.azure_openai_api_key,
            self.azure_chat_deployment,
        )
        return all(item.strip() for item in required)


def load_settings(root_dir: Path) -> Settings:
    # Load .env variables at app startup (dotenv does not override shell-exported envs).
    load_dotenv(root_dir / ".env", override=False)
    load_dotenv(root_dir / ".env.local", override=False)

    chat_deployment = (
        os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "").strip()
        or os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
    )

    data_dir = root_dir / "data"
    logs_dir = data_dir / "logs"
    images_dir = data_dir / "images"
    return Settings(
        azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", "").strip(),
        azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY", "").strip(),
        azure_openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        azure_chat_deployment=chat_deployment,
        azure_vision_deployment=os.getenv("AZURE_OPENAI_VISION_DEPLOYMENT", "").strip(),
        hf_embedding_model=os.getenv("HF_EMBEDDING_MODEL", "BAAI/bge-large-en-v1.5").strip(),
        chroma_dir=Path(os.getenv("CHROMA_DIR", str(data_dir / "chroma"))),
        data_dir=data_dir,
        logs_dir=logs_dir,
        images_dir=images_dir,
        image_captions_file=data_dir / "image_captions.jsonl",
        collection_name=os.getenv("CHROMA_COLLECTION_NAME", "omnissa_techzone"),
        image_collection_name=os.getenv("CHROMA_IMAGE_COLLECTION_NAME", "omnissa_techzone_images"),
        max_images_per_page=max(0, int(os.getenv("MAX_IMAGES_PER_PAGE", "6"))),
        sitemap_resource_only=os.getenv("SITEMAP_RESOURCE_ONLY", "1").strip() != "0",
    )
