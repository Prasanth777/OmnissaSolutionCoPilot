from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.shared import Inches

from .catalog import Product


def _clean_text(text: str, max_len: int = 1200) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"(^|\s)[#*_`>-]+", " ", raw)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len]


class HldDocxBuilder:
    def build(
        self,
        output_path: Path,
        customer_name: str,
        selected_products: list[Product],
        questionnaire: dict[str, str],
        rag_narrative: dict[str, str],
        references: list[str],
        image_rows: list[dict[str, str]],
    ) -> Path:
        image_rows = [row for row in image_rows if row.get("image_type", "architecture_diagram") == "architecture_diagram"]
        doc = Document()
        doc.add_heading(f"{customer_name or 'Customer'} Architecture Approach", level=1)
        doc.add_paragraph("Generated from Omnissa Tech Zone architecture references.")

        doc.add_heading("Scope", level=2)
        for key in (
            "industry",
            "project_scope",
            "users_personas",
            "hosting_strategy",
            "identity_source",
            "security_requirements",
            "availability_requirements",
        ):
            doc.add_paragraph(f"{key.replace('_', ' ').title()}: {_clean_text(questionnaire.get(key, 'TBD'), 220)}")

        doc.add_heading("Bottom-Up Architecture Layers", level=2)
        layers = [
            ("Infrastructure & Deployment", rag_narrative.get("architecture", "")),
            ("Network & Security", rag_narrative.get("security", "")),
            ("Identity & Access", rag_narrative.get("security", "")),
            ("Operations & Resilience", rag_narrative.get("operations", "")),
        ]
        for label, text in layers:
            doc.add_heading(label, level=3)
            doc.add_paragraph(_clean_text(text, 800) or "Refer to architecture diagrams below.")

        doc.add_heading("Architecture Diagrams", level=2)
        for idx, row in enumerate(image_rows, start=1):
            title = _clean_text(row.get("slide_title") or row.get("title", f"Diagram {idx}"), 180)
            caption = _clean_text(row.get("caption", ""), 280)
            source = _clean_text(row.get("page_url", ""), 280)
            local_path = Path(str(row.get("local_path", "")))
            doc.add_heading(f"{idx}. {title}", level=3)
            if local_path.exists():
                doc.add_picture(str(local_path), width=Inches(6.8))
            if caption:
                doc.add_paragraph(caption)
            if source:
                doc.add_paragraph(f"Source: {source}")

        doc.add_heading("References", level=2)
        for ref in references[:60]:
            doc.add_paragraph(_clean_text(ref, 220))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(output_path)
        return output_path
