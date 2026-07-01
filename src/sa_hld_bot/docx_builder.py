from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Iterable

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .catalog import Product


BLUE = RGBColor(0x2E, 0x74, 0xB5)
DARK_BLUE = RGBColor(0x1F, 0x4D, 0x78)
INK = RGBColor(0x0B, 0x25, 0x45)
MUTED = RGBColor(0x5B, 0x66, 0x73)
TABLE_HEADER = "E8EEF5"
LIGHT_FILL = "F4F6F9"
WHITE = "FFFFFF"
CONTENT_WIDTH_DXA = 9360


def _clean_text(text: object, max_len: int = 1200) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"(^|\s)[#*_`>-]+", " ", raw)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len]


def _is_unknown(value: object) -> bool:
    text = _clean_text(value).lower()
    return not text or "unknown" in text or "to be confirmed" in text or text == "tbd"


def _answer(answers: dict[str, str], key: str, default: str = "To be confirmed") -> str:
    value = _clean_text(answers.get(key, ""), 800)
    return default if _is_unknown(value) else value


def _diagram_header(row: dict[str, str], idx: int, max_len: int = 180) -> str:
    for key in ("figure_caption", "caption", "slide_title", "title"):
        value = _clean_text(row.get(key, ""), max_len)
        if value:
            return value
    return f"Architecture Diagram {idx}"


def _canon_url(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def _row_text(row: dict[str, str]) -> str:
    return " ".join(
        _clean_text(row.get(key, ""), 300)
        for key in ("topic", "slide_title", "title", "caption", "figure_caption", "section_heading", "page_url")
    ).lower()


class HldDocxBuilder:
    """Build a formal HLD DOCX in the Peninsula-style design-document shape."""

    def __init__(self) -> None:
        self._used_image_paths: set[str] = set()
        self._figure_no = 0
        self._table_no = 0
        self._decision_no = 0

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
        self._used_image_paths = set()
        self._figure_no = 0
        self._table_no = 0
        self._decision_no = 0

        image_rows = [
            row for row in image_rows
            if row.get("image_type", "architecture_diagram") == "architecture_diagram"
            and Path(str(row.get("local_path", ""))).exists()
        ]
        used_refs = self._used_references(references, image_rows)

        doc = Document()
        self._configure_document(doc, customer_name, questionnaire)
        self._cover(doc, customer_name, selected_products, questionnaire)
        doc.add_section(WD_SECTION.NEW_PAGE)
        self._set_running_header_footer(doc.sections[-1], customer_name, questionnaire)

        self._key_contacts(doc, questionnaire)
        self._overview(doc, customer_name, selected_products, questionnaire, rag_narrative)
        self._requirements(doc, questionnaire)
        self._solution_overview(doc, questionnaire, rag_narrative, image_rows)
        self._detailed_design(doc, selected_products, questionnaire, rag_narrative, image_rows)
        self._networking(doc, questionnaire, rag_narrative, image_rows)
        self._security(doc, questionnaire, rag_narrative, image_rows)
        self._business_continuity(doc, questionnaire, rag_narrative, image_rows)
        self._references(doc, used_refs)
        self._review_acceptance(doc, questionnaire)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(output_path)
        return output_path

    def _configure_document(self, doc: Document, customer_name: str, answers: dict[str, str]) -> None:
        section = doc.sections[0]
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)
        section.header_distance = Inches(0.492)
        section.footer_distance = Inches(0.492)
        self._set_running_header_footer(section, customer_name, answers)

        styles = doc.styles
        normal = styles["Normal"]
        normal.font.name = "Calibri"
        normal._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
        normal._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
        normal.font.size = Pt(11)
        normal.paragraph_format.space_after = Pt(6)
        normal.paragraph_format.line_spacing = 1.10

        for name, size, color, before, after in (
            ("Heading 1", 16, BLUE, 16, 8),
            ("Heading 2", 13, BLUE, 12, 6),
            ("Heading 3", 12, DARK_BLUE, 8, 4),
            ("Heading 4", 11, DARK_BLUE, 6, 3),
            ("Heading 5", 10.5, DARK_BLUE, 4, 2),
        ):
            style = styles[name]
            style.font.name = "Calibri"
            style._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
            style._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
            style.font.size = Pt(size)
            style.font.color.rgb = color
            style.font.bold = True
            style.paragraph_format.space_before = Pt(before)
            style.paragraph_format.space_after = Pt(after)

        self._ensure_paragraph_style(doc, "Figure Caption", size=9.5, color=MUTED, italic=True, after=8)
        self._ensure_paragraph_style(doc, "Design Decision", size=10.5, color=INK, bold=True, before=5, after=5)
        self._ensure_paragraph_style(doc, "Reference Link", size=9.5, color=DARK_BLUE, after=3)

    def _set_running_header_footer(self, section, customer_name: str, answers: dict[str, str]) -> None:
        project = _answer(answers, "project_name", "Architecture Design")
        header = section.header.paragraphs[0]
        header.text = f"{customer_name or 'Customer'} | {project}"
        header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        if header.runs:
            header.runs[0].font.size = Pt(9)
            header.runs[0].font.color.rgb = MUTED

        footer = section.footer.paragraphs[0]
        footer.text = "High-Level Design"
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if footer.runs:
            footer.runs[0].font.size = Pt(9)
            footer.runs[0].font.color.rgb = MUTED

    def _cover(self, doc: Document, customer_name: str, products: list[Product], answers: dict[str, str]) -> None:
        doc.add_paragraph()
        title = doc.add_paragraph()
        title.paragraph_format.space_after = Pt(4)
        run = title.add_run(f"{customer_name or 'Customer'} Omnissa Architecture Design")
        run.font.name = "Calibri"
        run.font.size = Pt(24)
        run.font.bold = True
        run.font.color.rgb = INK

        subtitle = doc.add_paragraph()
        subtitle.paragraph_format.space_after = Pt(16)
        sub = subtitle.add_run("High-Level Design")
        sub.font.size = Pt(15)
        sub.font.color.rgb = MUTED

        rows = [
            ("Project / Service", _answer(answers, "project_name", "Omnissa EUC Architecture")),
            ("Products", ", ".join(product.title for product in products) or "To be confirmed"),
            ("Prepared By", _answer(answers, "prepared_by")),
            ("Version", _answer(answers, "document_version", "0.1 - Draft")),
            ("Date", date.today().strftime("%B %d, %Y")),
        ]
        self._simple_table(doc, rows, widths=(2500, 6200), header=None)
        self._rule(doc)

        p = doc.add_paragraph()
        p.add_run("Document purpose: ").bold = True
        p.add_run(
            "This HLD summarizes the proposed Omnissa architecture, key requirements, design decisions, "
            "network/security considerations, and recovery assumptions using customer inputs and Omnissa Tech Zone guidance."
        )

    def _key_contacts(self, doc: Document, answers: dict[str, str]) -> None:
        doc.add_heading("Key Contacts", level=1)
        rows = [
            ("Prepared by", _answer(answers, "prepared_by")),
            ("Customer contacts", _answer(answers, "customer_contacts")),
            ("Reviewers / approvers", _answer(answers, "reviewers")),
            ("Operations owner", _answer(answers, "operations_owner")),
        ]
        self._numbered_table(doc, "Key Contacts", ("Role", "Name / Responsibility"), rows)

    def _overview(self, doc: Document, customer_name: str, products: list[Product], answers: dict[str, str], narrative: dict[str, str]) -> None:
        doc.add_heading("Overview", level=1)
        self._paragraph(doc, narrative.get("summary") or f"This document describes the proposed Omnissa architecture for {customer_name or 'the customer'}.")
        self._paragraph(
            doc,
            "The design is structured to capture requirements, assumptions, constraints, solution architecture, detailed component design, "
            "networking, security standards, and recovery considerations."
        )

        doc.add_heading("Audience", level=2)
        for item in (
            "Project executive sponsor",
            "Desktop and EUC operations leads",
            "Application operations leads",
            "Cloud, infrastructure, network, and security architects",
            "Implementation engineers responsible for detailed design and deployment",
        ):
            self._bullet(doc, item)

        doc.add_heading("Document Reference", level=2)
        rows = [
            ("Customer", customer_name or "Customer"),
            ("Industry", _answer(answers, "industry")),
            ("Products", ", ".join(product.title for product in products) or "To be confirmed"),
            ("Primary objective", _answer(answers, "project_scope")),
            ("Document status", _answer(answers, "document_version", "0.1 - Draft")),
        ]
        self._numbered_table(doc, "Document Reference", ("Field", "Value"), rows)

    def _requirements(self, doc: Document, answers: dict[str, str]) -> None:
        doc.add_heading("Requirements and Considerations", level=1)
        self._paragraph(doc, "This section summarizes the business requirements, technical requirements, constraints, and risks that shape the HLD.")

        doc.add_heading("Business Requirements", level=2)
        self._numbered_table(
            doc,
            "Business Requirements",
            ("Requirement", "Design Input"),
            [
                ("Business drivers", _answer(answers, "business_drivers")),
                ("Primary objective", _answer(answers, "project_scope")),
                ("User personas", _answer(answers, "users_personas")),
                ("Success criteria", _answer(answers, "success_criteria")),
                ("In scope", _answer(answers, "in_scope")),
                ("Out of scope", _answer(answers, "out_of_scope")),
            ],
        )

        doc.add_heading("Technical Requirements", level=2)
        self._numbered_table(
            doc,
            "Technical Requirements",
            ("Requirement", "Design Input"),
            [
                ("Workloads / delivery model", _answer(answers, "horizon_use_cases", _answer(answers, "project_scope"))),
                ("Expected scale / concurrency", _answer(answers, "workload_concurrency")),
                ("Hosting strategy", _answer(answers, "hosting_strategy")),
                ("Site topology", _answer(answers, "site_topology")),
                ("Identity source", _answer(answers, "identity_source")),
                ("MFA", f"{_answer(answers, 'mfa_required')} - {_answer(answers, 'mfa_provider')}"),
                ("External access", _answer(answers, "access_type")),
                ("Load balancer", _answer(answers, "load_balancer")),
                ("FQDN / DNS", _answer(answers, "fqdn_strategy")),
                ("Certificate", _answer(answers, "cert_type")),
            ],
        )

        doc.add_heading("Constraints", level=2)
        self._numbered_table(
            doc,
            "Constraints",
            ("Constraint", "Impact"),
            [
                ("Project constraints", _answer(answers, "constraints")),
                ("Network services", _answer(answers, "dns_dhcp_ntp")),
                ("Network segments", _answer(answers, "network_segments")),
                ("Firewall and ports", _answer(answers, "firewall_ports")),
                ("Open items", _answer(answers, "open_items")),
            ],
        )

        doc.add_heading("Risks", level=2)
        self._numbered_table(
            doc,
            "Risks",
            ("Risk", "Mitigation / Note"),
            [
                ("Known risks", _answer(answers, "risks")),
                ("Assumptions", _answer(answers, "assumptions")),
                ("Certificate ownership", _answer(answers, "certificate_owner")),
                ("Operational ownership", _answer(answers, "operations_owner")),
            ],
        )

    def _solution_overview(self, doc: Document, answers: dict[str, str], narrative: dict[str, str], images: list[dict[str, str]]) -> None:
        doc.add_heading("Solution Overview", level=1)
        self._paragraph(doc, narrative.get("architecture") or "The solution architecture will be finalized from the confirmed requirements and Tech Zone design guidance.")
        self._decision(doc, "Hosting and topology", f"{_answer(answers, 'hosting_strategy')} deployment with {_answer(answers, 'site_topology')} topology.")
        self._decision(doc, "Access model", f"{_answer(answers, 'access_type')} with {_answer(answers, 'load_balancer')} load balancing posture.")
        self._add_figures(doc, images, ("overall", "high level", "logical", "architecture"), limit=2)

    def _detailed_design(self, doc: Document, products: list[Product], answers: dict[str, str], narrative: dict[str, str], images: list[dict[str, str]]) -> None:
        for product in products:
            doc.add_heading(f"{product.title} Detailed Design", level=1)
            self._paragraph(doc, narrative.get(product.key) or product.summary)
            if product.key == "horizon_8":
                self._horizon_design(doc, answers, images)
            elif product.key == "app_volumes":
                self._app_volumes_design(doc, answers, images)
            elif product.key == "dynamic_environment_manager":
                self._dem_design(doc, answers, images)
            elif product.key == "unified_access_gateway":
                self._uag_design(doc, answers, images)
            else:
                self._product_design_table(doc, product, answers)
                self._add_figures(doc, images, (product.title.lower(), product.key.replace("_", " ")), limit=1)

    def _horizon_design(self, doc: Document, answers: dict[str, str], images: list[dict[str, str]]) -> None:
        doc.add_heading("Horizon 8 Architecture", level=2)
        self._numbered_table(
            doc,
            "Horizon Component Design",
            ("Component", "Design Input"),
            [
                ("Pod and block model", _answer(answers, "horizon_pod_block_model")),
                ("Desktop / RDSH model", _answer(answers, "horizon_pool_model")),
                ("Connection Servers", _answer(answers, "horizon_connection_server_count")),
                ("External access", _answer(answers, "horizon_external_access", _answer(answers, "access_type"))),
                ("Access topology", _answer(answers, "horizon_access_topology")),
                ("DMZ design", _answer(answers, "horizon_dmz_design")),
                ("Display protocols", _answer(answers, "horizon_protocol_scope", "Blast Extreme only")),
                ("Event database", _answer(answers, "horizon_database_events")),
                ("Golden image strategy", _answer(answers, "horizon_golden_image")),
            ],
        )
        self._decision(doc, "Horizon access topology", f"{_answer(answers, 'horizon_access_topology')} using {_answer(answers, 'horizon_dmz_design')} edge placement.")
        self._add_figures(doc, images, ("pod", "block", "connection server", "horizon logical", "cloud pod", "single-site", "multi-site"), limit=3)

    def _app_volumes_design(self, doc: Document, answers: dict[str, str], images: list[dict[str, str]]) -> None:
        self._numbered_table(
            doc,
            "App Volumes Design",
            ("Design Area", "Design Input"),
            [
                ("Use case", _answer(answers, "app_volumes_scope")),
                ("Architecture track", _answer(answers, "app_volumes_arch_track")),
                ("Design focus", _answer(answers, "app_volumes_design_focus")),
                ("Storage", _answer(answers, "app_volumes_storage")),
                ("Database", _answer(answers, "app_volumes_database")),
            ],
        )
        self._add_figures(doc, images, ("app volumes", "storage group", "apps on demand", "package", "database"), limit=2)

    def _dem_design(self, doc: Document, answers: dict[str, str], images: list[dict[str, str]]) -> None:
        self._numbered_table(
            doc,
            "Dynamic Environment Manager Design",
            ("Design Area", "Design Input"),
            [
                ("Management scope", _answer(answers, "dem_scope")),
                ("Architecture track", _answer(answers, "dem_arch_track")),
                ("Design focus", _answer(answers, "dem_design_focus")),
                ("File shares", _answer(answers, "dem_file_shares")),
                ("Profile strategy", _answer(answers, "dem_profile_strategy")),
            ],
        )
        self._add_figures(doc, images, ("dynamic environment manager", "dem", "profile", "configuration share", "fslogix"), limit=2)

    def _uag_design(self, doc: Document, answers: dict[str, str], images: list[dict[str, str]]) -> None:
        self._numbered_table(
            doc,
            "Unified Access Gateway Design",
            ("Design Area", "Design Input"),
            [
                ("NIC configuration", _answer(answers, "uag_nic_config")),
                ("Published services", _answer(answers, "uag_services")),
                ("Edge pattern", _answer(answers, "uag_edge_pattern")),
                ("Architecture track", _answer(answers, "uag_arch_track")),
                ("Design focus", _answer(answers, "uag_design_focus")),
            ],
        )
        self._add_figures(doc, images, ("unified access gateway", "uag", "dmz", "pass-through", "load balancing"), limit=2)

    def _product_design_table(self, doc: Document, product: Product, answers: dict[str, str]) -> None:
        prefix = product.key
        rows = [
            ("Architecture track", _answer(answers, f"{prefix}_arch_track")),
            ("Design focus", _answer(answers, f"{prefix}_design_focus")),
        ]
        self._numbered_table(doc, f"{product.title} Design Inputs", ("Design Area", "Design Input"), rows)

    def _networking(self, doc: Document, answers: dict[str, str], narrative: dict[str, str], images: list[dict[str, str]]) -> None:
        doc.add_heading("Networking Requirements", level=1)
        self._paragraph(doc, narrative.get("security") or "Network requirements must be validated against the customer firewall, DNS, DHCP, NTP, and load-balancing standards.")
        self._numbered_table(
            doc,
            "Network Requirements",
            ("Area", "Requirement / Assumption"),
            [
                ("Primary site", _answer(answers, "primary_site")),
                ("Secondary sites", _answer(answers, "secondary_sites", "Not applicable")),
                ("Access type", _answer(answers, "access_type")),
                ("Network segments", _answer(answers, "network_segments")),
                ("DNS / DHCP / NTP", _answer(answers, "dns_dhcp_ntp")),
                ("Firewall / ports", _answer(answers, "firewall_ports")),
                ("Load balancing", _answer(answers, "load_balancer")),
                ("FQDN strategy", _answer(answers, "fqdn_strategy")),
            ],
        )
        self._add_figures(doc, images, ("load balancing", "network", "dmz", "uag", "connection server"), limit=3)

    def _security(self, doc: Document, answers: dict[str, str], narrative: dict[str, str], images: list[dict[str, str]]) -> None:
        doc.add_heading("Security Standards", level=1)
        self._paragraph(doc, narrative.get("security") or "Security standards should align to the customer identity, MFA, certificate, RBAC, logging, and hardening requirements.")
        self._numbered_table(
            doc,
            "Security Standards",
            ("Security Area", "Design Input"),
            [
                ("Security baseline", _answer(answers, "security_requirements")),
                ("Identity source", _answer(answers, "identity_source")),
                ("MFA", f"{_answer(answers, 'mfa_required')} - {_answer(answers, 'mfa_provider')}"),
                ("Certificate type", _answer(answers, "cert_type")),
                ("Certificate owner", _answer(answers, "certificate_owner")),
                ("RBAC", _answer(answers, "rbac_model")),
                ("Antivirus / hardening", _answer(answers, "antivirus_hardening")),
                ("Monitoring / logging", _answer(answers, "monitoring_logging")),
            ],
        )
        self._decision(doc, "Security posture", f"{_answer(answers, 'security_requirements')} with {_answer(answers, 'mfa_required')} MFA requirement.")
        self._add_figures(doc, images, ("authentication", "true sso", "access", "pass-through", "security"), limit=2)

    def _business_continuity(self, doc: Document, answers: dict[str, str], narrative: dict[str, str], images: list[dict[str, str]]) -> None:
        doc.add_heading("Business Continuity and Recovery", level=1)
        self._paragraph(doc, narrative.get("operations") or "Availability and recovery design should be validated through detailed design and operational readiness workshops.")
        self._numbered_table(
            doc,
            "Disaster Recovery Scenarios",
            ("Scenario", "Design Response"),
            [
                ("Availability target", _answer(answers, "availability_requirements")),
                ("Backup expectations", _answer(answers, "backup_requirements")),
                ("DR scenarios", _answer(answers, "dr_scenarios")),
                ("Operations owner", _answer(answers, "operations_owner")),
                ("Monitoring and logging", _answer(answers, "monitoring_logging")),
                ("Open recovery items", _answer(answers, "open_items")),
            ],
        )
        self._add_figures(doc, images, ("active-active", "active-passive", "multi-site", "cloud pod", "operations dashboard"), limit=2)

    def _references(self, doc: Document, references: list[str]) -> None:
        doc.add_heading("References", level=1)
        if not references:
            self._paragraph(doc, "No external source links were used in the generated content.")
            return
        for idx, ref in enumerate(references, start=1):
            p = doc.add_paragraph(style="Reference Link")
            p.add_run(f"{idx}. ")
            self._add_hyperlink(p, ref, ref)

    def _review_acceptance(self, doc: Document, answers: dict[str, str]) -> None:
        doc.add_heading("Review and Acceptance", level=1)
        self._paragraph(doc, "The following sign-off table is provided for review tracking and acceptance of the high-level design.")
        self._numbered_table(
            doc,
            "Review and Acceptance",
            ("Reviewer", "Role / Status"),
            [
                (_answer(answers, "reviewers"), "Review / approval to be completed"),
                (_answer(answers, "operations_owner"), "Operational acceptance to be confirmed"),
                (_answer(answers, "customer_contacts"), "Customer stakeholder acknowledgement"),
            ],
        )

    def _add_figures(self, doc: Document, images: list[dict[str, str]], keywords: Iterable[str], limit: int = 1) -> None:
        matched: list[dict[str, str]] = []
        kws = tuple(k.lower() for k in keywords)
        for row in images:
            local_path = str(row.get("local_path", ""))
            if not local_path or local_path in self._used_image_paths:
                continue
            text = _row_text(row)
            if any(keyword in text for keyword in kws):
                matched.append(row)
            if len(matched) >= limit:
                break
        if not matched:
            for row in images:
                local_path = str(row.get("local_path", ""))
                if local_path and local_path not in self._used_image_paths:
                    matched.append(row)
                    break

        for row in matched[:limit]:
            self._add_figure(doc, row)

    def _add_figure(self, doc: Document, row: dict[str, str]) -> None:
        image_path = Path(str(row.get("local_path", "")))
        if not image_path.exists():
            return
        self._used_image_paths.add(str(image_path))
        self._figure_no += 1
        title = _diagram_header(row, self._figure_no)
        doc.add_paragraph()
        try:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.add_run().add_picture(str(image_path), width=Inches(6.4))
        except Exception:
            return
        caption = doc.add_paragraph(style="Figure Caption")
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        caption.add_run(f"Figure {self._figure_no} {title}")
        source = _clean_text(row.get("page_url", ""), 300)
        if source:
            src = doc.add_paragraph(style="Figure Caption")
            src.alignment = WD_ALIGN_PARAGRAPH.CENTER
            src.add_run("Source: ")
            self._add_hyperlink(src, source, source)

    def _decision(self, doc: Document, title: str, text: str) -> None:
        self._decision_no += 1
        p = doc.add_paragraph(style="Design Decision")
        p.paragraph_format.left_indent = Inches(0.12)
        p.paragraph_format.right_indent = Inches(0.12)
        self._paragraph_shading(p, LIGHT_FILL)
        p.add_run(f"Design Decision {self._decision_no} {title}: ").bold = True
        p.add_run(_clean_text(text, 500))

    def _numbered_table(self, doc: Document, title: str, headers: tuple[str, str], rows: list[tuple[str, str]]) -> None:
        self._table_no += 1
        cap = doc.add_paragraph(style="Figure Caption")
        cap.add_run(f"Table {self._table_no} {title}").bold = True
        self._simple_table(doc, rows, widths=(3000, 6200), header=headers)

    def _simple_table(
        self,
        doc: Document,
        rows: list[tuple[str, str]],
        widths: tuple[int, int],
        header: tuple[str, str] | None,
    ) -> None:
        table = doc.add_table(rows=1 if header else 0, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.LEFT
        table.autofit = False
        self._set_table_width(table, widths)
        if header:
            hdr = table.rows[0].cells
            for idx, text in enumerate(header):
                self._set_cell(hdr[idx], text, bold=True, fill=TABLE_HEADER)
        for label, value in rows:
            cells = table.add_row().cells
            self._set_cell(cells[0], label, bold=True, fill=WHITE)
            self._set_cell(cells[1], value or "To be confirmed", fill=WHITE)
        doc.add_paragraph()

    def _set_table_width(self, table, widths: tuple[int, int]) -> None:
        tbl = table._tbl
        tbl_pr = tbl.tblPr
        tbl_w = tbl_pr.find(qn("w:tblW"))
        if tbl_w is None:
            tbl_w = OxmlElement("w:tblW")
            tbl_pr.append(tbl_w)
        tbl_w.set(qn("w:w"), str(sum(widths)))
        tbl_w.set(qn("w:type"), "dxa")
        for row in table.rows:
            for idx, cell in enumerate(row.cells):
                cell.width = Pt(widths[idx] / 20)
                tc_pr = cell._tc.get_or_add_tcPr()
                tc_w = tc_pr.find(qn("w:tcW"))
                if tc_w is None:
                    tc_w = OxmlElement("w:tcW")
                    tc_pr.append(tc_w)
                tc_w.set(qn("w:w"), str(widths[idx]))
                tc_w.set(qn("w:type"), "dxa")

    def _set_cell(self, cell, text: str, bold: bool = False, fill: str | None = None) -> None:
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        if fill:
            self._cell_shading(cell, fill)
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(2)
        run = p.add_run(_clean_text(text, 900))
        run.bold = bold
        run.font.size = Pt(10)

    def _paragraph(self, doc: Document, text: str) -> None:
        doc.add_paragraph(_clean_text(text, 1600) or "To be confirmed.")

    def _bullet(self, doc: Document, text: str) -> None:
        p = doc.add_paragraph(style="List Bullet")
        p.add_run(_clean_text(text, 400))

    def _rule(self, doc: Document) -> None:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(12)
        p_pr = p._p.get_or_add_pPr()
        p_bdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "8")
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), "2E74B5")
        p_bdr.append(bottom)
        p_pr.append(p_bdr)

    def _ensure_paragraph_style(
        self,
        doc: Document,
        name: str,
        size: float,
        color: RGBColor,
        bold: bool = False,
        italic: bool = False,
        before: float = 0,
        after: float = 0,
    ) -> None:
        styles = doc.styles
        try:
            style = styles[name]
        except KeyError:
            style = styles.add_style(name, 1)
        style.font.name = "Calibri"
        style._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
        style._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
        style.font.size = Pt(size)
        style.font.color.rgb = color
        style.font.bold = bold
        style.font.italic = italic
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)

    def _cell_shading(self, cell, fill: str) -> None:
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = tc_pr.find(qn("w:shd"))
        if shd is None:
            shd = OxmlElement("w:shd")
            tc_pr.append(shd)
        shd.set(qn("w:fill"), fill)

    def _paragraph_shading(self, paragraph, fill: str) -> None:
        p_pr = paragraph._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:fill"), fill)
        p_pr.append(shd)

    def _add_hyperlink(self, paragraph, text: str, url: str) -> None:
        part = paragraph.part
        r_id = part.relate_to(
            url,
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
            is_external=True,
        )
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), r_id)
        run = OxmlElement("w:r")
        r_pr = OxmlElement("w:rPr")
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "1F4D78")
        r_pr.append(color)
        underline = OxmlElement("w:u")
        underline.set(qn("w:val"), "single")
        r_pr.append(underline)
        run.append(r_pr)
        text_element = OxmlElement("w:t")
        text_element.text = text
        run.append(text_element)
        hyperlink.append(run)
        paragraph._p.append(hyperlink)

    def _used_references(self, references: list[str], image_rows: list[dict[str, str]]) -> list[str]:
        refs: list[str] = []
        for ref in references or []:
            if ref:
                refs.append(_canon_url(ref))
        for row in image_rows:
            ref = _canon_url(str(row.get("page_url", "")))
            if ref:
                refs.append(ref)
        return list(dict.fromkeys(refs))
