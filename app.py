from __future__ import annotations

import hashlib
import inspect
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import chromadb
import streamlit as st

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sa_hld_bot.agents import AgenticRagOrchestrator, GuardrailAgent, RetrievalAgent, SolutionAgent
from sa_hld_bot.azure_foundry import AzureFoundryClient
from sa_hld_bot.catalog import FAMILY_CHOICES, PRODUCTS, compile_reference_resources, normalize_answer, required_questions, visible_questions
from sa_hld_bot.config import load_settings
from sa_hld_bot.docx_builder import HldDocxBuilder
from sa_hld_bot.ppt_builder import HldPptBuilder
from sa_hld_bot.rag import TechZoneRagStore


st.set_page_config(
    page_title="Solution Architect CoPilot",
    page_icon="AI",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def bootstrap_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("selected_families", [])
    st.session_state.setdefault("selected_products", [])
    st.session_state.setdefault("answers", {})
    st.session_state.setdefault("ppt_path", "")
    st.session_state.setdefault("docx_path", "")
    st.session_state.setdefault("rag_ready", False)
    st.session_state.setdefault("rag_stats", {"chunks": 0, "images": 0})
    st.session_state.setdefault("active_question_key", "")
    st.session_state.setdefault("question_prompted", False)
    st.session_state.setdefault("generated_signature", "")
    st.session_state.setdefault("ppt_preview", [])
    st.session_state.setdefault("ppt_preview_images", [])
    st.session_state.setdefault("last_references", [])
    st.session_state.setdefault("use_sample_template", True)
    st.session_state.setdefault("sample_template_path", "/Users/prasanththangaraj/Downloads/test-for AI.pptx")


def chat_add(role: str, content: str) -> None:
    st.session_state.messages.append({"role": role, "content": content})


def first_question():
    pending = pending_questions()
    if pending:
        return pending[0]
    return None


def pending_questions():
    missing = []
    for question in visible_questions(st.session_state.selected_products, st.session_state.answers):
        if not normalize_answer(st.session_state.answers.get(question.key)):
            missing.append(question)
    return missing


def ask_next_question_if_needed() -> None:
    next_q = first_question()
    if not next_q:
        return
    if st.session_state.active_question_key == next_q.key:
        return
    all_visible = visible_questions(st.session_state.selected_products, st.session_state.answers)
    q_index = 1
    for idx, question in enumerate(all_visible, start=1):
        if question.key == next_q.key:
            q_index = idx
            break
    hint = f"\n\nHint: {next_q.help_text}" if next_q.help_text else ""
    chat_add("assistant", f"Question {q_index} of {len(all_visible)}: {next_q.prompt}{hint}")
    st.session_state.active_question_key = next_q.key


def focus_chat_input() -> None:
    js = """
    <script>
    const tryFocus = () => {
      const root = window.parent.document;
      const el = root.querySelector('textarea[aria-label="Answer input"]')
            || root.querySelector('textarea[data-testid="stChatInputTextArea"]');
      if (el) { el.focus(); }
    };
    setTimeout(tryFocus, 100);
    setTimeout(tryFocus, 400);
    </script>
    """
    if hasattr(st, "html"):
        st.html(js)


def initialize_agents():
    started = time.perf_counter()
    settings = load_settings(ROOT)
    if not settings.configured:
        st.session_state["startup_init_seconds"] = round(time.perf_counter() - started, 2)
        return settings, None, None
    foundry, rag, orchestrator = _initialize_agents_cached(
        settings.azure_openai_endpoint,
        settings.azure_openai_api_key,
        settings.azure_openai_api_version,
        settings.azure_chat_deployment,
        settings.azure_vision_deployment,
        settings.hf_embedding_model,
        str(settings.chroma_dir),
        str(settings.data_dir),
        str(settings.logs_dir),
        str(settings.images_dir),
        str(settings.image_captions_file),
        settings.collection_name,
        settings.image_collection_name,
        settings.max_images_per_page,
        settings.sitemap_resource_only,
    )
    st.session_state["startup_init_seconds"] = round(time.perf_counter() - started, 2)
    return settings, rag, orchestrator


@st.cache_resource(show_spinner=False)
def _initialize_agents_cached(
    endpoint: str,
    api_key: str,
    api_version: str,
    chat_deployment: str,
    vision_deployment: str,
    hf_embedding_model: str,
    chroma_dir: str,
    data_dir: str,
    logs_dir: str,
    images_dir: str,
    image_captions_file: str,
    collection_name: str,
    image_collection_name: str,
    max_images_per_page: int,
    sitemap_resource_only: bool,
):
    from sa_hld_bot.config import Settings

    settings = Settings(
        azure_openai_endpoint=endpoint,
        azure_openai_api_key=api_key,
        azure_openai_api_version=api_version,
        azure_chat_deployment=chat_deployment,
        azure_vision_deployment=vision_deployment,
        hf_embedding_model=hf_embedding_model,
        chroma_dir=Path(chroma_dir),
        data_dir=Path(data_dir),
        logs_dir=Path(logs_dir),
        images_dir=Path(images_dir),
        image_captions_file=Path(image_captions_file),
        collection_name=collection_name,
        image_collection_name=image_collection_name,
        max_images_per_page=max_images_per_page,
        sitemap_resource_only=sitemap_resource_only,
    )
    foundry = AzureFoundryClient(settings)
    rag = TechZoneRagStore(settings, foundry)
    orchestrator = AgenticRagOrchestrator(
        retrieval=RetrievalAgent(rag),
        solution=SolutionAgent(foundry),
        guardrail=GuardrailAgent(),
    )
    return foundry, rag, orchestrator


def verify_ingestion_coverage(settings) -> dict[str, object]:
    from sa_hld_bot.rag import TechZoneCrawler

    crawler = TechZoneCrawler(settings)
    sitemap_urls = crawler.sitemap_urls()

    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    collection = client.get_or_create_collection(settings.collection_name)
    data = collection.get(include=["metadatas"])
    indexed_urls = {
        meta["url"]
        for meta in data.get("metadatas", [])
        if isinstance(meta, dict) and meta.get("url")
    }
    missing = [url for url in sitemap_urls if url not in indexed_urls]
    return {
        "sitemap_resources": len(sitemap_urls),
        "indexed_unique_pages": len(indexed_urls),
        "chunks": collection.count(),
        "missing_count": len(missing),
        "missing_sample": missing[:10],
    }


def ensure_seed_message() -> None:
    if st.session_state.messages:
        return
    chat_add(
        "assistant",
        (
            "This bot runs as an agentic workflow: Questionnaire Agent + Retrieval Agent + Solution Agent + Guardrail Agent. "
            "Answers are allowed only from Tech Zone RAG results."
        ),
    )


def set_products_from_families(families: list[str]) -> None:
    valid = []
    for family in families:
        valid.extend(FAMILY_CHOICES.get(family, ()))
    st.session_state.selected_products = [item for item in st.session_state.selected_products if item in valid]


def sync_selection_state() -> None:
    previous_products = list(st.session_state.selected_products)
    set_products_from_families(st.session_state.selected_families)
    if previous_products != st.session_state.selected_products:
        st.session_state.answers = {}
        st.session_state.messages = st.session_state.messages[:1]
        st.session_state.active_question_key = ""
        st.session_state.generated_signature = ""
        st.session_state.last_references = []


def answer_question(value: str) -> None:
    question = first_question()
    if not question:
        return
    normalized = normalize_answer(value)
    if not normalized:
        return
    st.session_state.answers[question.key] = normalized
    st.session_state.generated_signature = ""
    chat_add("user", normalized)
    st.session_state.active_question_key = ""
    ask_next_question_if_needed()


def _generation_signature() -> str:
    payload = {
        "products": st.session_state.selected_products,
        "answers": st.session_state.answers,
        "use_sample_template": bool(st.session_state.get("use_sample_template", False)),
        "sample_template_path": str(st.session_state.get("sample_template_path", "")),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def clean_markdown_text(text: str, max_len: int = 1200) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"(^|\s)[#*_`>-]+", " ", raw)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len]


def estimate_ppt_generation_seconds() -> int:
    # Include retrieval, selective image planning, and file generation overhead.
    section_calls = 4 + len(st.session_state.selected_products)
    image_planning = 20 if "horizon_8" in st.session_state.selected_products else 10
    return max(60, section_calls * 9 + image_planning)


def derive_solution_references(product_keys: list[str], answers: dict[str, str]) -> list[str]:
    refs: list[str] = []
    h8_track = answers.get("horizon_8_arch_track", "").lower()
    if "vmware cloud on aws" in h8_track:
        refs.append("https://techzone.omnissa.com/resource/horizon-8-vmware-cloud-aws-architecture")
    elif "azure vmware solution" in h8_track:
        refs.append("https://techzone.omnissa.com/resource/horizon-8-azure-vmware-solution-architecture")
    elif "google cloud vmware engine" in h8_track:
        refs.append("https://techzone.omnissa.com/resource/horizon-8-google-cloud-vmware-engine-architecture")
    elif "oracle cloud vmware solution" in h8_track:
        refs.append("https://techzone.omnissa.com/resource/horizon-8-oracle-cloud-vmware-solution-architecture")
    elif "alibaba cloud vmware service" in h8_track:
        refs.append("https://techzone.omnissa.com/resource/horizon-8-alibaba-cloud-vmware-service-architecture")
    elif "horizon 8 core architecture" in h8_track:
        refs.append("https://techzone.omnissa.com/resource/horizon-8-architecture")
    if "horizon_8" in product_keys:
        refs.append("https://techzone.omnissa.com/resource/horizon-8-architecture")
        refs.append("https://techzone.omnissa.com/resource/horizon-8-configuration")
        refs.append("https://techzone.omnissa.com/resource/reference-architecture-vm-specifications")
        refs.append("https://techzone.omnissa.com/resource/network-ports-horizon-8")
        refs.append("https://techzone.omnissa.com/resource/understand-and-troubleshoot-horizon-connections")
        refs.append("https://techzone.omnissa.com/resource/environment-infrastructure-design")

    if "load balancer" in answers.get("horizon_access_topology", "").lower():
        refs.append("https://techzone.omnissa.com/resource/load-balancing-unified-access-gateway-horizon")
    if "dmz" in answers.get("horizon_dmz_design", "").lower():
        refs.append("https://techzone.omnissa.com/resource/unified-access-gateway-architecture")

    hc_track = answers.get("horizon_cloud_arch_track", "").lower()
    if "next-gen architecture" in hc_track:
        refs.append("https://techzone.omnissa.com/resource/horizon-cloud-service-next-gen-architecture")
    if "security" in hc_track:
        refs.append("https://techzone.omnissa.com/resource/horizon-cloud-service-security-overview")
    if "connectivity" in hc_track:
        refs.append("https://techzone.omnissa.com/resource/horizon-cloud-service-next-gen-configuration")
    if "horizon_cloud" in product_keys:
        refs.append("https://techzone.omnissa.com/resource/horizon-cloud-service-next-gen-architecture")

    av_track = answers.get("app_volumes_arch_track", "").lower()
    if "architecture baseline" in av_track:
        refs.append("https://techzone.omnissa.com/resource/app-volumes-architecture")
    if "packaging" in av_track or "lifecycle" in av_track:
        refs.append("https://techzone.omnissa.com/resource/app-volumes-configuration")
    if "published apps on-demand" in av_track:
        refs.append("https://techzone.omnissa.com/resource/app-volumes-configuration")
    if "app_volumes" in product_keys:
        refs.append("https://techzone.omnissa.com/resource/app-volumes-architecture")

    dem_track = answers.get("dem_arch_track", "").lower()
    if "architecture baseline" in dem_track:
        refs.append("https://techzone.omnissa.com/resource/dynamic-environment-manager-architecture")
    if "configuration" in dem_track or "policy operations" in dem_track:
        refs.append("https://techzone.omnissa.com/resource/dynamic-environment-manager-configuration")
    if "dynamic_environment_manager" in product_keys:
        refs.append("https://techzone.omnissa.com/resource/dynamic-environment-manager-architecture")

    uem_track = answers.get("uem_arch_track", "").lower()
    if "architecture baseline" in uem_track:
        refs.append("https://techzone.omnissa.com/resource/workspace-one-uem-architecture")
    if "saas architecture" in uem_track:
        refs.append("https://techzone.omnissa.com/resource/administration-changes-workspace-one-uem-modern-saas-architecture")
    if "configuration-first" in uem_track:
        refs.append("https://techzone.omnissa.com/resource/workspace-one-uem-configuration")
    if "workspace_one_uem" in product_keys:
        refs.append("https://techzone.omnissa.com/resource/workspace-one-uem-architecture")

    access_track = answers.get("access_arch_track", "").lower()
    if "architecture baseline" in access_track:
        refs.append("https://techzone.omnissa.com/resource/workspace-one-access-architecture")
    if "configuration-first" in access_track:
        refs.append("https://techzone.omnissa.com/resource/workspace-one-access-configuration")
    if "zero trust" in access_track:
        refs.append("https://techzone.omnissa.com/resource/zero-trust-regulation-and-framework")
    if "omnissa_access" in product_keys:
        refs.append("https://techzone.omnissa.com/resource/workspace-one-access-architecture")

    uag_track = answers.get("uag_arch_track", "").lower()
    if "architecture baseline" in uag_track:
        refs.append("https://techzone.omnissa.com/resource/unified-access-gateway-architecture")
    if "high-availability" in uag_track:
        refs.append("https://techzone.omnissa.com/resource/configuring-high-availability-unified-access-gateway")
    if "load-balanced" in uag_track or "global edge" in uag_track:
        refs.append("https://techzone.omnissa.com/resource/load-balancing-unified-access-gateway-horizon")
    if "unified_access_gateway" in product_keys:
        refs.append("https://techzone.omnissa.com/resource/unified-access-gateway-architecture")

    refs = list(dict.fromkeys(refs))
    return refs


def generate_hld_outputs(orchestrator: AgenticRagOrchestrator, rag: TechZoneRagStore) -> tuple[Path, Path, list[str], list[dict[str, str]]]:
    answers = st.session_state.answers
    product_keys = st.session_state.selected_products
    product_objs = [PRODUCTS[key] for key in product_keys if key in PRODUCTS]
    resources = compile_reference_resources(product_keys)

    context_blob = "\n".join(f"{k}: {v}" for k, v in answers.items())
    prompts = {
        "summary": f"Create a customer-facing executive summary based on these inputs:\n{context_blob}",
        "architecture": f"Provide a high-level architecture description for selected products {', '.join(product_keys)} and customer inputs:\n{context_blob}",
        "security": f"Describe security and access design for these customer constraints:\n{context_blob}",
        "operations": f"Describe operational model, HA, and DR for these customer constraints:\n{context_blob}",
    }
    for key in product_keys:
        prompts[key] = f"Provide customer-facing HLD design guidance for {PRODUCTS[key].title} using these inputs:\n{context_blob}"

    narrative: dict[str, str] = {}
    all_citations: list[str] = []
    for section, prompt in prompts.items():
        result = orchestrator.answer(prompt)
        if result.blocked:
            narrative[section] = f"RAG guardrail block: {result.blocked_reason}"
        else:
            narrative[section] = clean_markdown_text(result.answer, max_len=1500)
        all_citations.extend(result.citations)

    citation_refs = [resources_url for resources_url in all_citations if resources_url]
    derived_refs = derive_solution_references(product_keys, answers)
    base_refs = [resource.url for resource in resources]
    references = list(dict.fromkeys(base_refs + derived_refs + citation_refs))
    image_rows = rag.select_hld_images(
        selected_products=product_keys,
        answers=answers,
        reference_urls=references,
        limit=10,
    )

    output_dir = ROOT / "output"
    base_name = f"{answers.get('customer_name', 'Customer').replace(' ', '_')}_HLD_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    output_path = output_dir / f"{base_name}.pptx"
    docx_path = output_dir / f"{base_name}.docx"

    builder = HldPptBuilder()
    sample_path_raw = str(st.session_state.get("sample_template_path", "")).strip()
    sample_path = Path(sample_path_raw) if sample_path_raw else None
    build_kwargs = dict(
        output_path=output_path,
        customer_name=answers.get("customer_name", "Customer"),
        selected_products=product_objs,
        questionnaire=answers,
        rag_narrative=narrative,
        references=references,
        image_rows=image_rows,
    )
    params = inspect.signature(builder.build).parameters
    if "sample_ppt_path" in params:
        build_kwargs["sample_ppt_path"] = sample_path
    if "use_sample_style" in params:
        build_kwargs["use_sample_style"] = bool(st.session_state.get("use_sample_template", False))
    builder.build(**build_kwargs)
    docx_builder = HldDocxBuilder()
    docx_builder.build(
        output_path=docx_path,
        customer_name=answers.get("customer_name", "Customer"),
        selected_products=product_objs,
        questionnaire=answers,
        rag_narrative=narrative,
        references=references,
        image_rows=image_rows,
    )
    preview_sections = [
        {"title": "Customer Context", "content": "\n".join(builder._context_bullets(answers))},
        {"title": "Proposed Solution Summary", "content": clean_markdown_text(narrative.get("summary", ""), 600)},
        {"title": "Architecture Approach", "content": clean_markdown_text(narrative.get("architecture", ""), 600)},
        {"title": "Security and Access", "content": clean_markdown_text(narrative.get("security", ""), 600)},
        {"title": "Operational Model", "content": clean_markdown_text(narrative.get("operations", ""), 600)},
    ]
    for key in product_keys:
        preview_sections.append({"title": PRODUCTS[key].title, "content": clean_markdown_text(narrative.get(key, ""), 600)})

    st.session_state.ppt_preview = preview_sections
    st.session_state.ppt_preview_images = image_rows
    st.session_state.last_references = references
    st.session_state.ppt_path = str(output_path)
    st.session_state.docx_path = str(docx_path)
    return output_path, docx_path, references, preview_sections


bootstrap_state()
sync_selection_state()
settings, rag_store, orchestrator = initialize_agents()
ensure_seed_message()

st.title("Solution Architect CoPilot")
st.caption("Azure AI Foundry + Tech Zone only RAG + customer-ready PowerPoint generation.")

with st.sidebar:
    st.subheader("System")
    if settings.configured:
        st.success("Azure AI Foundry configuration detected")
    else:
        st.error("Set Azure environment variables to enable agents and RAG")
    init_seconds = float(st.session_state.get("startup_init_seconds", 0.0))
    if init_seconds > 0:
        st.caption(f"Startup init time: {init_seconds:.2f}s")

    st.subheader("RAG Control")
    page_limit = st.number_input("Max sitemap pages to index", min_value=50, max_value=5000, value=100, step=50)
    force_full_rebuild = st.checkbox("Force full rebuild (ignore delta)", value=False)
    if rag_store:
        live = rag_store.index_stats()
        st.caption(f"Current index: {live['chunks']} chunks, {live['images']} images")
        st.session_state.rag_stats = live
    rebuild = st.button("Build / Rebuild Tech Zone RAG")
    if rebuild:
        if not settings.configured or not rag_store:
            st.error("Azure configuration missing. Cannot build RAG.")
        else:
            try:
                with st.spinner("Crawling Tech Zone sitemap and building Chroma index..."):
                    stats = rag_store.rebuild_from_sitemap(
                        max_pages=int(page_limit),
                        force_full_rebuild=bool(force_full_rebuild),
                    )
                st.session_state.rag_ready = True
                st.session_state.rag_stats = {"chunks": stats["chunks"], "images": stats["images_total"]}
                st.success(
                    "RAG sync complete. "
                    f"Scanned: {stats['pages_scanned']}, Upserted: {stats['pages_upserted']}, "
                    f"Unchanged: {stats['pages_unchanged']}, Failed: {stats['pages_failed']}, "
                    f"Chunks updated: {stats['chunks']}, Images total: {stats['images_total']} "
                    f"(+{stats['images_added']}/-{stats['images_deleted']})."
                )
            except Exception as exc:
                st.session_state.rag_ready = False
                st.error(f"RAG build failed: {exc}")

    verify = st.button("Verify Ingestion Coverage")
    if verify:
        if not rag_store:
            st.error("RAG store is not initialized.")
        else:
            with st.spinner("Checking sitemap resources vs indexed pages..."):
                report = verify_ingestion_coverage(settings)
            st.info(
                f"Sitemap resources: {report['sitemap_resources']} | Indexed unique pages: {report['indexed_unique_pages']} | "
                f"Chunks: {report['chunks']} | Missing: {report['missing_count']}"
            )
            if report["missing_sample"]:
                st.caption("Missing sample:")
                for item in report["missing_sample"]:
                    st.write(f"- {item}")

    with st.expander("Logs & Audit"):
        app_log = settings.logs_dir / "app.log"
        audit_log = settings.logs_dir / "ingestion_audit.jsonl"
        st.caption(f"App log: `{app_log}`")
        st.caption(f"Ingestion audit: `{audit_log}`")

        if app_log.exists():
            lines = app_log.read_text(encoding="utf-8").splitlines()[-50:]
            st.text("\n".join(lines) if lines else "No app logs yet.")
        else:
            st.info("No app log file yet.")

        if audit_log.exists():
            rows = [json.loads(line) for line in audit_log.read_text(encoding="utf-8").splitlines() if line.strip()]
            st.caption(f"Audit rows: {len(rows)}")
            recent = rows[-10:]
            for row in recent:
                status = row.get("status", "")
                url = row.get("url", "")
                image_url = row.get("image_url", "")
                st.write(f"- {status} | {url or image_url}")
        else:
            st.info("No ingestion audit file yet.")

    st.subheader("PPT Style")
    use_template = st.checkbox(
        "Learn layout/style from sample PPT",
        value=bool(st.session_state.get("use_sample_template", True)),
    )
    sample_template_path = st.text_input(
        "Sample PPT path",
        value=str(st.session_state.get("sample_template_path", "/Users/prasanththangaraj/Downloads/test-for AI.pptx")),
    )
    st.session_state.use_sample_template = bool(use_template)
    st.session_state.sample_template_path = sample_template_path.strip()

total_questions = len(visible_questions(st.session_state.selected_products, st.session_state.answers))
complete = total_questions - len(pending_questions())

with st.container(border=True):
    st.markdown("### Guided HLD Flow")
    st.caption("Follow the steps below: choose the solution family, narrow to products in scope, answer the guided questions, then generate the customer-ready HLD.")

    top_left, top_right = st.columns([1.4, 1])
    with top_left:
        families = st.multiselect(
            "1. Choose solution families",
            options=list(FAMILY_CHOICES.keys()),
            default=st.session_state.selected_families,
            placeholder="Choose one or more families",
        )
        if families != st.session_state.selected_families:
            st.session_state.selected_families = families
            set_products_from_families(families)
            st.session_state.answers = {}
            st.session_state.messages = st.session_state.messages[:1]
            st.session_state.active_question_key = ""
            st.session_state.generated_signature = ""
            st.session_state.last_references = []
            st.rerun()

    with top_right:
        st.markdown("**Progress**")
        st.progress(0.0 if total_questions == 0 else complete / total_questions)
        st.write(f"{complete} / {total_questions} questions complete")
        if total_questions == 0:
            st.caption("Start by selecting a family and product.")
        elif complete < total_questions:
            st.caption("Keep answering the guided prompts to unlock the final HLD output.")
        else:
            st.caption("All required design questions are complete. You can generate the output now.")

    st.markdown("**2. Choose product tracks in scope**")
    available = []
    for fam in st.session_state.selected_families:
        available.extend(FAMILY_CHOICES.get(fam, ()))
    if available:
        product_cols = st.columns(2)
        new_selected_products: list[str] = []
        for idx, key in enumerate(available):
            with product_cols[idx % 2]:
                selected = key in st.session_state.selected_products
                toggle = st.checkbox(PRODUCTS[key].title, value=selected, key=f"product_{key}")
                if toggle:
                    new_selected_products.append(key)
        if new_selected_products != st.session_state.selected_products:
            st.session_state.selected_products = new_selected_products
            st.session_state.answers = {}
            st.session_state.messages = st.session_state.messages[:1]
            st.session_state.active_question_key = ""
            st.session_state.generated_signature = ""
            st.session_state.last_references = []
            st.rerun()
    else:
        st.info("Select at least one family to reveal the available product tracks.")

if not st.session_state.selected_products:
    st.info("Choose at least one product track to start the guided questionnaire.")
else:
    ask_next_question_if_needed()
    
if st.session_state.selected_products:
    st.markdown("### 3. Guided Design Interview")
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    next_q = first_question()
    if next_q:
        if next_q.options:
            st.markdown("**Suggested answers**")
            chosen = None
            if hasattr(st, "pills"):
                chosen = st.pills("Click a suggestion", options=list(next_q.options), key=f"pill_{next_q.key}")
            else:
                chosen = st.radio("Click a suggestion", options=list(next_q.options), key=f"radio_{next_q.key}", horizontal=True)
            if chosen:
                answer_question(chosen)
                st.rerun()

        custom = st.chat_input("Answer input")
        focus_chat_input()
        if custom:
            answer_question(custom)
            st.rerun()
    else:
        st.success("Questionnaire complete.")
        est_seconds = estimate_ppt_generation_seconds()
        st.info(f"Estimated PPT generation time: ~{est_seconds // 60}m {est_seconds % 60}s")
        generation_sig = _generation_signature()
        current_chunks = int(st.session_state.rag_stats.get("chunks", 0))
        if generation_sig != st.session_state.generated_signature and orchestrator and rag_store and current_chunks > 0:
            with st.spinner(f"Generating customer-facing HLD PowerPoint (estimated ~{est_seconds // 60}m {est_seconds % 60}s)..."):
                output_path, docx_path, refs, _preview = generate_hld_outputs(orchestrator, rag_store)
            st.session_state.generated_signature = generation_sig
            refs_short = "\n".join(f"- {url}" for url in refs[:6])
            chat_add(
                "assistant",
                f"HLD outputs generated: `{output_path}` and `{docx_path}`\n\nTop references used:\n{refs_short}",
            )
            st.rerun()

        st.caption("You can ask follow-up design questions or regenerate the deck.")
        user_q = st.chat_input("Ask a solution question from Tech Zone RAG", key="post_question")
        focus_chat_input()
        if user_q:
            chat_add("user", user_q)
            if not orchestrator:
                chat_add("assistant", "Azure AI Foundry is not configured.")
            elif not rag_store:
                chat_add("assistant", "RAG store is not initialized.")
            else:
                result = orchestrator.answer(user_q)
                if result.blocked:
                    chat_add("assistant", f"Blocked by guardrail: {result.blocked_reason}")
                else:
                    refs = "\n".join(f"- {src}" for src in result.citations[:8])
                    chat_add("assistant", f"{result.answer}\n\nSources:\n{refs}")
            st.rerun()

        if st.button("Generate Customer HLD PPT", type="primary"):
            if not orchestrator or not rag_store:
                st.error("Azure AI Foundry and RAG store are required.")
            else:
                with st.spinner(f"Generating RAG-grounded customer PowerPoint (estimated ~{est_seconds // 60}m {est_seconds % 60}s)..."):
                    output_path, docx_path, refs, _preview = generate_hld_outputs(orchestrator, rag_store)
                st.session_state.generated_signature = generation_sig
                refs_short = "\n".join(f"- {url}" for url in refs[:6])
                chat_add(
                    "assistant",
                    f"HLD outputs generated: `{output_path}` and `{docx_path}`\n\nTop references used:\n{refs_short}",
                )
                st.rerun()

        if st.session_state.ppt_path:
            file_path = Path(st.session_state.ppt_path)
            if file_path.exists():
                with file_path.open("rb") as handle:
                    st.download_button(
                        "Download PPT",
                        data=handle.read(),
                        file_name=file_path.name,
                        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    )
        if st.session_state.docx_path:
            docx_file = Path(st.session_state.docx_path)
            if docx_file.exists():
                with docx_file.open("rb") as handle:
                    st.download_button(
                        "Download DOCX (Architecture Approach)",
                        data=handle.read(),
                        file_name=docx_file.name,
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
        if st.session_state.ppt_path:
            st.markdown("### PPT Content Preview")
            st.caption("Live preview of generated architecture narrative and diagrams.")
            for section in st.session_state.ppt_preview:
                with st.expander(section.get("title", "Slide"), expanded=False):
                    st.write(section.get("content", ""))
            if st.session_state.ppt_preview_images:
                st.markdown("### Architecture Diagram Preview")
                cols = st.columns(2)
                for idx, image_row in enumerate(st.session_state.ppt_preview_images[:24]):
                    image_path = image_row.get("local_path", "")
                    if not image_path:
                        continue
                    with cols[idx % 2]:
                        if Path(image_path).exists():
                            st.image(
                                image_path,
                                caption=image_row.get("caption", "Architecture diagram from Omnissa Tech Zone"),
                                width="stretch",
                            )

show_reference_panels = bool(st.session_state.selected_products) and (
    bool(st.session_state.last_references)
    or bool(st.session_state.ppt_path)
)

if show_reference_panels:
    with st.expander("Current product references"):
        if st.session_state.last_references:
            for ref_url in st.session_state.last_references:
                st.markdown(f"- [{ref_url}]({ref_url})")
        else:
            for ref in compile_reference_resources(st.session_state.selected_products):
                st.markdown(f"- [{ref.title}]({ref.url})")

    if rag_store:
        with st.expander("Ingested Architecture Images (RAG Audit)"):
            max_preview = st.slider("Images to preview", min_value=4, max_value=40, value=12, step=4)
            rows = rag_store.list_ingested_images(limit=max_preview)
            st.caption(f"Showing {len(rows)} architecture-diagram images from the RAG image store.")
            if not rows:
                st.info("No architecture images found yet. Rebuild RAG first.")
            cols = st.columns(2)
            for idx, row in enumerate(rows):
                with cols[idx % 2]:
                    path = row.get("local_path", "")
                    if path and Path(path).exists():
                        st.image(path, caption=row.get("caption", "Architecture diagram"), width="stretch")
                    page_url = row.get("page_url", "")
                    if page_url:
                        st.markdown(f"[Source page]({page_url})")
