from __future__ import annotations

import hashlib
import inspect
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Lightweight imports only — keep the initial page render fast.
# Heavy modules (chromadb, rag, agents) are imported lazily inside initialize_agents().
from sa_hld_bot.catalog import FAMILY_CHOICES, PRODUCTS, Question, compile_reference_resources, effective_answers, essential_question_keys, filtered_question_options, normalize_answer, question_source, visible_questions
from sa_hld_bot.config import load_settings
from sa_hld_bot.image_followup import apply_command, looks_like_image_command, parse_command
from sa_hld_bot.sessions import delete_session, list_sessions, load_session, save_session


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
    st.session_state.setdefault("rag_narrative", {})
    st.session_state.setdefault("current_session_id", "")
    st.session_state.setdefault("optional_questions_consent", "")
    st.session_state.setdefault("optional_questions_done", False)
    st.session_state.setdefault("figure_candidates", [])
    st.session_state.setdefault("figure_excluded", [])
    st.session_state.setdefault("use_sample_template", True)
    st.session_state.setdefault("sample_template_path", "/Users/prasanththangaraj/Downloads/test-for AI.pptx")
    st.session_state.setdefault("_visible_questions_cache", {"signature": "", "questions": []})


def chat_add(role: str, content: str) -> None:
    st.session_state.messages.append({"role": role, "content": content})


def first_question():
    pending = pending_questions()
    if pending:
        return pending[0]
    return None


def pending_questions():
    missing = []
    essential_only = None
    if st.session_state.get("optional_questions_done"):
        essential_only = essential_question_keys(st.session_state.selected_products, st.session_state.answers)
    for question in current_visible_questions():
        if essential_only is not None and question.key not in essential_only:
            continue
        if not normalize_answer(st.session_state.answers.get(question.key)):
            missing.append(question)
    return missing


def unanswered_optional_questions() -> list[Question]:
    essential = essential_question_keys(st.session_state.selected_products, st.session_state.answers)
    return [
        question
        for question in visible_questions(st.session_state.selected_products, st.session_state.answers, include_optional=True)
        if question.key not in essential and not normalize_answer(st.session_state.answers.get(question.key))
    ]


def accept_optional_questions() -> None:
    st.session_state.optional_questions_consent = "yes"
    st.session_state.optional_questions_done = False
    st.session_state.active_question_key = ""
    clear_question_cache()


def decline_optional_questions() -> None:
    st.session_state.optional_questions_consent = "no"
    st.session_state.optional_questions_done = True
    st.session_state.active_question_key = ""
    clear_question_cache()


def finish_optional_questions() -> None:
    st.session_state.optional_questions_done = True
    st.session_state.active_question_key = ""
    clear_question_cache()


def reset_optional_questions_state() -> None:
    """Reset per-interview state: optional-question consent and figure curation."""
    st.session_state.optional_questions_consent = ""
    st.session_state.optional_questions_done = False
    st.session_state.figure_candidates = []
    st.session_state.figure_excluded = []


def compute_figure_candidates() -> list[dict]:
    """Run answer-aware diagram selection (no LLM calls) for the review panel."""
    _settings, _rag_store, _orchestrator = get_agents()
    if not _rag_store:
        return []
    answers = effective_answers(st.session_state.answers)
    product_keys = st.session_state.selected_products
    resources = compile_reference_resources(product_keys)
    refs = [resource.url for resource in resources] + derive_solution_references(product_keys, answers)
    from sa_hld_bot.image_select import select_hld_images

    return select_hld_images(
        _rag_store,
        selected_products=product_keys,
        answers=answers,
        reference_urls=list(dict.fromkeys(refs)),
        limit=10,
    )


def clear_question_cache() -> None:
    st.session_state._visible_questions_cache = {"signature": "", "questions": []}


def current_visible_questions() -> list[Question]:
    include_optional = st.session_state.get("optional_questions_consent", "") == "yes"
    signature = hashlib.sha1(
        json.dumps(
            {
                "products": st.session_state.selected_products,
                "answers": st.session_state.answers,
                "include_optional": include_optional,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    cache = st.session_state.get("_visible_questions_cache", {})
    if cache.get("signature") == signature:
        return list(cache.get("questions", []))
    questions = visible_questions(st.session_state.selected_products, st.session_state.answers, include_optional=include_optional)
    st.session_state._visible_questions_cache = {"signature": signature, "questions": questions}
    return questions


def prune_hidden_answers() -> None:
    visible_keys = {question.key for question in current_visible_questions()}
    stale_keys = [key for key in st.session_state.answers if key not in visible_keys]
    for key in stale_keys:
        st.session_state.answers.pop(key, None)


def question_source_markdown(question: Question) -> str:
    source_hint = question_source(question, st.session_state.selected_products)
    if not source_hint:
        return ""
    return f"[Source: {source_hint.title}]({source_hint.url})"


def ask_next_question_if_needed() -> None:
    next_q = first_question()
    if not next_q:
        return
    if st.session_state.active_question_key == next_q.key:
        return
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
    # Heavy imports done here so module-level startup is fast.
    import chromadb  # noqa: F401 (side-effect: initialises chroma logging)
    from sa_hld_bot.agents import AgenticRagOrchestrator, GuardrailAgent, RetrievalAgent, SolutionAgent
    from sa_hld_bot.azure_foundry import AzureFoundryClient
    from sa_hld_bot.rag import TechZoneRagStore
    from sa_hld_bot.config import Settings as _Settings

    settings = _Settings(
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
    import chromadb

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
        st.session_state.current_session_id = ""
        reset_optional_questions_state()
        clear_question_cache()


def _answer_value(value: str | list[str] | tuple[str, ...]) -> str:
    if isinstance(value, (list, tuple)):
        parts = [normalize_answer(item) for item in value if normalize_answer(item)]
        if len(parts) > 1:
            parts = [item for item in parts if item != "Unknown / to be confirmed"]
        return "; ".join(dict.fromkeys(parts))
    return normalize_answer(value)


def answer_question(value: str | list[str] | tuple[str, ...]) -> None:
    question = first_question()
    if not question:
        return
    normalized = _answer_value(value)
    if not normalized:
        return
    st.session_state.answers[question.key] = normalized
    clear_question_cache()
    prune_hidden_answers()
    clear_question_cache()
    st.session_state.generated_signature = ""
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
        refs.append("https://techzone.omnissa.com/resource/omnissa-horizon-blast-extreme-display-protocol")
        refs.append("https://techzone.omnissa.com/resource/network-ports-horizon-8")
        refs.append("https://techzone.omnissa.com/resource/understand-and-troubleshoot-horizon-connections")
        refs.append("https://techzone.omnissa.com/resource/environment-infrastructure-design")

    load_balancer_signal = " ".join(
        normalize_answer(answers.get(key)).lower()
        for key in ("load_balancer", "load_balancer_placement", "horizon_external_access", "horizon_access_topology")
    )
    if "load balancer" in load_balancer_signal or "load balancing" in load_balancer_signal:
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


def _horizon_reference_allowed(product_keys: list[str], answers: dict[str, str], url: str) -> bool:
    lower_url = url.lower().strip().rstrip("/")
    if not lower_url or "techzone.omnissa.com" not in lower_url:
        return False

    broad_reject_tokens = (
        "business-drivers-use-cases-and-service-definitions",
        "workspace-one-and-horizon-reference-architecture-overview",
        "evaluation-guide",
        "best-practices-managing-microsoft-bitlocker",
        "microsoft-teams-optimization-horizon",
        "using-apple-automated-device-enrollment",
        "blocking-unwanted-apps-managed-ios-devices",
        "managing-updates-macos",
        "using-workspace-one-manage-operating-system-updates-macos-devices",
        "configuring-tunnel-edge-service-workspace-one",
        "getting-started-workspace-one-experience-management",
        "introduction-horizon-citrix-practitioners",
        "cmmc-compliance",
        "deploying-omnissa-horizon-amazon-ec2-and-amazon-workspaces",
        "horizon-cloud-on-microsoft-azure-first-gen",
        "compliance-14-ncsc-cloud-security-principles",
        "alignment-dora-requirements",
        "alignment-nis-2-directive",
        "alignment-nist",
        "cloud-computing-compliance-criteria-catalogue",
    )
    if any(token in lower_url for token in broad_reject_tokens):
        return False

    selected_product_set = set(product_keys)
    if "horizon_cloud" in selected_product_set and "horizon_8" not in selected_product_set:
        horizon_cloud_tokens = {
            "what-omnissa-horizon",
            "horizon-cloud-service-next-gen-architecture",
            "horizon-cloud-service-next-gen-configuration",
            "horizon-cloud-service-next-gen-security-overview",
            "omnissa-horizon-blast-extreme-display-protocol",
            "understand-and-troubleshoot-horizon-connections",
            "network-ports-horizon-8",
        }
        external_tokens = {
            "unified-access-gateway-architecture",
            "deploying-unified-access-gateway",
            "load-balancing-unified-access-gateway-horizon",
            "configuring-high-availability-unified-access-gateway",
        }
        if any(token in lower_url for token in horizon_cloud_tokens):
            return True
        if answers.get("access_type", "").lower() != "internal users only" and any(token in lower_url for token in external_tokens):
            return True
        return False

    if "horizon_8" not in product_keys:
        return True

    hosting = answers.get("hosting_strategy", "").lower()
    access_type = answers.get("access_type", "").lower()
    track = answers.get("horizon_8_arch_track", "").lower()

    cloud_ref_tokens = {
        "vmware cloud on aws": "horizon-8-vmware-cloud-aws-architecture",
        "azure vmware solution": "horizon-8-azure-vmware-solution-architecture",
        "google cloud vmware engine": "horizon-8-google-cloud-vmware-engine-architecture",
        "oracle cloud vmware solution": "horizon-8-oracle-cloud-vmware-solution-architecture",
        "alibaba cloud vmware service": "horizon-8-alibaba-cloud-vmware-service-architecture",
    }
    selected_cloud_token = ""
    for answer_token, url_token in cloud_ref_tokens.items():
        if answer_token in track:
            selected_cloud_token = url_token
            break

    h8_core_tokens = {
        "horizon-8-architecture",
        "horizon-8-configuration",
        "reference-architecture-vm-specifications",
        "network-ports-horizon-8",
        "understand-and-troubleshoot-horizon-connections",
        "environment-infrastructure-design",
        "omnissa-horizon-blast-extreme-display-protocol",
        "what-omnissa-horizon",
    }
    external_tokens = {
        "unified-access-gateway-architecture",
        "deploying-unified-access-gateway",
        "load-balancing-unified-access-gateway-horizon",
        "configuring-high-availability-unified-access-gateway",
    }
    product_tokens: set[str] = set()
    if "app_volumes" in selected_product_set:
        product_tokens.add("app-volumes")
    if "dynamic_environment_manager" in selected_product_set:
        product_tokens.add("dynamic-environment-manager")
    if "omnissa_access" in selected_product_set:
        product_tokens.update({"workspace-one-access", "zero-trust"})
    if "workspace_one_uem" in selected_product_set:
        product_tokens.add("workspace-one-uem")

    is_h8_cloud_ref = any(token in lower_url for token in cloud_ref_tokens.values())
    if hosting == "on-premises" and is_h8_cloud_ref:
        return False
    if selected_cloud_token and is_h8_cloud_ref and selected_cloud_token not in lower_url:
        return False
    if any(token in lower_url for token in h8_core_tokens):
        return True
    if access_type != "internal users only" and any(token in lower_url for token in external_tokens):
        return True
    if any(token in lower_url for token in product_tokens):
        return True
    if selected_cloud_token and selected_cloud_token in lower_url:
        return True
    return False


def filter_solution_references(product_keys: list[str], answers: dict[str, str], refs: list[str]) -> list[str]:
    filtered: list[str] = []
    for url in refs:
        if _horizon_reference_allowed(product_keys, answers, url):
            filtered.append(url.strip().rstrip("/"))
    return list(dict.fromkeys(filtered))


def image_source_references(image_rows: list[dict]) -> list[str]:
    refs: list[str] = []
    for row in image_rows or []:
        page_url = str(row.get("page_url") or "").strip()
        if page_url:
            refs.append(page_url)
    return list(dict.fromkeys(refs))


def session_question_log() -> list[dict[str, str]]:
    return [
        {
            "key": question.key,
            "prompt": question.prompt,
            "answer": normalize_answer(st.session_state.answers.get(question.key)),
        }
        for question in current_visible_questions()
    ]


def selected_image_identifiers(image_rows: list[dict]) -> list[dict[str, str]]:
    identifiers: list[dict[str, str]] = []
    for idx, row in enumerate(image_rows or [], start=1):
        identifiers.append({
            "index": str(idx),
            "topic": normalize_answer(row.get("topic")),
            "caption": normalize_answer(row.get("figure_caption") or row.get("caption") or row.get("title")),
            "page_url": normalize_answer(row.get("page_url")),
            "image_url": normalize_answer(row.get("image_url")),
            "local_path": normalize_answer(row.get("local_path")),
            "dmz_design": normalize_answer(row.get("dmz_design")),
            "site_topology": normalize_answer(row.get("site_topology")),
        })
    return identifiers


def generate_hld_outputs(orchestrator, rag_store):
    from sa_hld_bot.ppt_builder import HldPptBuilder
    from sa_hld_bot.docx_builder import HldDocxBuilder
    answers = effective_answers(st.session_state.answers)
    product_keys = st.session_state.selected_products
    product_objs = [PRODUCTS[key] for key in product_keys if key in PRODUCTS]
    resources = compile_reference_resources(product_keys)

    context_blob = "\n".join(f"{k}: {v}" for k, v in answers.items())
    hld_instruction = (
        "Use Omnissa Tech Zone context from the RAG results only. Write for a formal high-level design document. "
        "Ground each recommendation in the retrieved architecture guidance. If a customer detail is unknown or to be confirmed, "
        "state it as an assumption or open item rather than inventing a value."
    )
    prompts = {
        "summary": f"{hld_instruction}\nCreate a customer-facing executive summary based on these inputs:\n{context_blob}",
        "architecture": f"{hld_instruction}\nProvide a high-level architecture description for selected products {', '.join(product_keys)} and customer inputs:\n{context_blob}",
        "security": f"{hld_instruction}\nDescribe security standards, access design, RBAC, certificates, hardening, and logging for these customer constraints:\n{context_blob}",
        "networking": f"{hld_instruction}\nDescribe the networking requirements for this design: network segments/subnets, required ports and firewall rules, load balancing, DNS/DHCP/NTP dependencies, and external access paths, for these customer constraints:\n{context_blob}",
        "operations": f"{hld_instruction}\nDescribe operational model, HA, backup, monitoring, and DR for these customer constraints:\n{context_blob}",
    }
    for key in product_keys:
        prompts[key] = f"{hld_instruction}\nProvide customer-facing HLD detailed design guidance for {PRODUCTS[key].title} using these inputs:\n{context_blob}"

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
    candidate_references = filter_solution_references(product_keys, answers, list(dict.fromkeys(base_refs + derived_refs + citation_refs)))
    from sa_hld_bot.image_select import select_hld_images as select_hld_images_v3
    image_rows = select_hld_images_v3(
        rag_store,
        selected_products=product_keys,
        answers=answers,
        reference_urls=candidate_references,
        limit=10,
    )
    # Respect any exclusions made in the diagram review panel.
    excluded_figures = set(st.session_state.get("figure_excluded", []))
    if excluded_figures:
        image_rows = [row for row in image_rows if str(row.get("local_path", "")) not in excluded_figures]
    references = filter_solution_references(
        product_keys,
        answers,
        list(dict.fromkeys(citation_refs + image_source_references(image_rows))),
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
    st.session_state.rag_narrative = narrative
    persist_current_session()
    return output_path, docx_path, references, preview_sections


def _session_title() -> str:
    customer = st.session_state.answers.get("customer_name", "Customer") or "Customer"
    names = ", ".join(PRODUCTS[k].title for k in st.session_state.selected_products if k in PRODUCTS)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"{customer} - {names or 'No products'} - {stamp}"


def persist_current_session() -> str:
    payload = {
        "id": st.session_state.get("current_session_id") or None,
        "title": _session_title(),
        "selected_families": list(st.session_state.selected_families),
        "selected_products": list(st.session_state.selected_products),
        "answers": dict(st.session_state.answers),
        "references": list(st.session_state.get("last_references", [])),
        "preview_sections": st.session_state.get("ppt_preview", []),
        "image_rows": st.session_state.get("ppt_preview_images", []),
        "questionnaire_log": session_question_log(),
        "selected_image_identifiers": selected_image_identifiers(st.session_state.get("ppt_preview_images", [])),
        "rag_narrative": st.session_state.get("rag_narrative", {}),
        "messages": st.session_state.get("messages", []),
        "ppt_path": st.session_state.get("ppt_path", ""),
        "docx_path": st.session_state.get("docx_path", ""),
        "optional_questions_consent": st.session_state.get("optional_questions_consent", ""),
        "optional_questions_done": bool(st.session_state.get("optional_questions_done", False)),
    }
    sid = save_session(ROOT / "data", payload, session_id=payload["id"])
    st.session_state.current_session_id = sid
    return sid


def load_session_into_state(payload: dict) -> None:
    products = list(payload.get("selected_products", []))
    families = sorted({PRODUCTS[k].family for k in products if k in PRODUCTS})
    st.session_state.selected_products = products
    st.session_state.selected_families = families
    st.session_state.answers = dict(payload.get("answers", {}))
    st.session_state.ppt_preview = payload.get("preview_sections", [])
    st.session_state.ppt_preview_images = payload.get("image_rows", [])
    st.session_state.last_references = payload.get("references", [])
    st.session_state.rag_narrative = payload.get("rag_narrative", {})
    st.session_state.ppt_path = payload.get("ppt_path", "")
    st.session_state.docx_path = payload.get("docx_path", "")
    st.session_state.messages = payload.get("messages", []) or []
    st.session_state.active_question_key = ""
    st.session_state.current_session_id = payload.get("id", "")
    st.session_state.optional_questions_consent = payload.get("optional_questions_consent", "")
    st.session_state.optional_questions_done = bool(payload.get("optional_questions_done", False))
    clear_question_cache()
    st.session_state.generated_signature = _generation_signature()


def rebuild_deck_from_state() -> None:
    from sa_hld_bot.ppt_builder import HldPptBuilder
    from sa_hld_bot.docx_builder import HldDocxBuilder
    """Rebuild PPT/DOCX from stored narrative + current image set (no LLM calls)."""
    narrative = st.session_state.get("rag_narrative") or {}
    if not narrative:
        return
    product_keys = st.session_state.selected_products
    product_objs = [PRODUCTS[k] for k in product_keys if k in PRODUCTS]
    answers = effective_answers(st.session_state.answers)
    references = st.session_state.get("last_references", [])
    image_rows = st.session_state.get("ppt_preview_images", [])
    output_dir = ROOT / "output"
    base_name = f"{answers.get('customer_name', 'Customer').replace(' ', '_')}_HLD_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    output_path = output_dir / f"{base_name}.pptx"
    docx_path = output_dir / f"{base_name}.docx"
    builder = HldPptBuilder()
    sample_path_raw = str(st.session_state.get("sample_template_path", "")).strip()
    build_kwargs = dict(
        output_path=output_path, customer_name=answers.get("customer_name", "Customer"),
        selected_products=product_objs, questionnaire=answers, rag_narrative=narrative,
        references=references, image_rows=image_rows,
    )
    params = inspect.signature(builder.build).parameters
    if "sample_ppt_path" in params:
        build_kwargs["sample_ppt_path"] = Path(sample_path_raw) if sample_path_raw else None
    if "use_sample_style" in params:
        build_kwargs["use_sample_style"] = bool(st.session_state.get("use_sample_template", False))
    builder.build(**build_kwargs)
    HldDocxBuilder().build(
        output_path=docx_path, customer_name=answers.get("customer_name", "Customer"),
        selected_products=product_objs, questionnaire=answers, rag_narrative=narrative,
        references=references, image_rows=image_rows,
    )
    st.session_state.ppt_path = str(output_path)
    st.session_state.docx_path = str(docx_path)
    persist_current_session()


bootstrap_state()
sync_selection_state()

settings = load_settings(ROOT)
rag_store = None
orchestrator = None


def get_agents():
    """Initialize heavy AI/RAG dependencies only when a workflow needs them."""
    with st.spinner("Loading AI agents and vector store..."):
        return initialize_agents()

ensure_seed_message()

st.title("Solution Architect CoPilot")
st.caption("Azure AI Foundry + Tech Zone only RAG + customer-ready PowerPoint generation.")

with st.sidebar:
    st.subheader("Saved Sessions")
    if st.button("➕ New session", key="new_session_btn"):
        st.session_state.current_session_id = ""
        st.session_state.selected_families = []
        st.session_state.selected_products = []
        st.session_state.answers = {}
        st.session_state.messages = st.session_state.messages[:1]
        st.session_state.active_question_key = ""
        st.session_state.generated_signature = ""
        st.session_state.ppt_preview = []
        st.session_state.ppt_preview_images = []
        st.session_state.last_references = []
        st.session_state.rag_narrative = {}
        st.session_state.ppt_path = ""
        st.session_state.docx_path = ""
        reset_optional_questions_state()
        st.rerun()
    _saved = list_sessions(ROOT / "data")
    if not _saved:
        st.caption("No saved sessions yet. Generate an HLD to save one.")
    for _s in _saved[:25]:
        _row = st.columns([5, 1])
        active = _s["id"] == st.session_state.get("current_session_id")
        label = ("● " if active else "") + _s.get("title", _s["id"])
        if _row[0].button(label, key=f"load_{_s['id']}", help="Open this session"):
            _payload = load_session(ROOT / "data", _s["id"])
            if _payload:
                load_session_into_state(_payload)
                st.rerun()
        if _row[1].button("🗑", key=f"del_{_s['id']}", help="Delete"):
            delete_session(ROOT / "data", _s["id"])
            if active:
                st.session_state.current_session_id = ""
            st.rerun()

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
    live = st.session_state.get("rag_stats", {"chunks": 0, "images": 0})
    st.caption(f"Last known index: {live.get('chunks', 0)} chunks, {live.get('images', 0)} images")
    rebuild = st.button("Build / Rebuild Tech Zone RAG")
    if rebuild:
        _settings, _rag_store, _orchestrator = get_agents()
        if not _settings.configured or not _rag_store:
            st.error("Azure configuration missing. Cannot build RAG.")
        else:
            try:
                with st.spinner("Crawling Tech Zone sitemap and building Chroma index..."):
                    stats = _rag_store.rebuild_from_sitemap(
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
        _settings, _rag_store, _orchestrator = get_agents()
        if not _rag_store:
            st.error("RAG store is not initialized.")
        else:
            with st.spinner("Checking sitemap resources vs indexed pages..."):
                report = verify_ingestion_coverage(_settings)
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
        if st.button("Load latest logs", key="load_logs_btn"):
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
        else:
            st.caption("Click to load logs only when troubleshooting.")

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

total_questions = len(current_visible_questions())
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
            st.session_state.selected_families = list(families)
            set_products_from_families(families)
            st.session_state.answers = {}
            st.session_state.messages = st.session_state.messages[:1]
            st.session_state.active_question_key = ""
            st.session_state.generated_signature = ""
            st.session_state.last_references = []
            st.session_state.current_session_id = ""
            reset_optional_questions_state()

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
    ADDON_PRODUCTS = {"app_volumes", "dynamic_environment_manager"}
    HORIZON_BROKERS = {"horizon_8", "horizon_cloud"}
    if available:
        product_cols = st.columns(2)
        new_selected_products: list[str] = []
        for idx, key in enumerate(available):
            with product_cols[idx % 2]:
                selected = key in st.session_state.selected_products
                toggle = st.checkbox(PRODUCTS[key].title, value=selected, key=f"product_{key}")
                if toggle:
                    new_selected_products.append(key)
        # Enforce broker dependency: App Volumes and DEM require Horizon 8 or Horizon Cloud.
        has_broker = any(k in new_selected_products for k in HORIZON_BROKERS)
        addons_checked = [k for k in new_selected_products if k in ADDON_PRODUCTS]
        if addons_checked and not has_broker:
            new_selected_products = [k for k in new_selected_products if k not in ADDON_PRODUCTS]
            st.warning(
                f"{', '.join(PRODUCTS[k].title for k in addons_checked)} "
                "requires Horizon 8 or Horizon Cloud as a broker. "
                "Please select a Horizon product first."
            )
        if new_selected_products != st.session_state.selected_products:
            st.session_state.selected_products = new_selected_products
            st.session_state.answers = {}
            st.session_state.messages = st.session_state.messages[:1]
            st.session_state.active_question_key = ""
            st.session_state.generated_signature = ""
            st.session_state.last_references = []
            st.session_state.current_session_id = ""
            reset_optional_questions_state()
    else:
        st.info("Select at least one family to reveal the available product tracks.")

if not st.session_state.selected_products:
    st.info("Choose at least one product track to start the guided questionnaire.")
else:
    ask_next_question_if_needed()
    
if st.session_state.selected_products:
    st.markdown("### 3. Guided Design Interview")
    if st.session_state.answers:
        with st.expander("Answered so far", expanded=False):
            recent_items = list(st.session_state.answers.items())[-8:]
            for key, value in recent_items:
                st.caption(f"{key.replace('_', ' ').title()}: {value}")

    next_q = first_question()
    if next_q:
        all_visible = current_visible_questions()
        q_index = next((idx for idx, question in enumerate(all_visible, start=1) if question.key == next_q.key), 1)
        _essential_keys = essential_question_keys(st.session_state.selected_products, st.session_state.answers)
        _is_optional_q = next_q.key not in _essential_keys
        optional_tag = " (optional)" if _is_optional_q else ""
        st.markdown(f"**Question {q_index} of {len(all_visible)}{optional_tag}: {next_q.prompt}**")
        if _is_optional_q:
            st.button(
                "Skip remaining optional questions and finish",
                key="skip_optional_questions",
                on_click=finish_optional_questions,
            )
        if next_q.help_text:
            st.caption(f"Hint: {next_q.help_text}")
        source = question_source_markdown(next_q)
        if source:
            st.caption(source)
        options = filtered_question_options(next_q, st.session_state.selected_products, st.session_state.answers)
        if options:
            if next_q.multi_select:
                selected_options = st.multiselect(
                    "Suggested answers",
                    options,
                    key=f"multi_answer_{next_q.key}",
                    placeholder="Choose one or more",
                )
                st.button(
                    "Use selected answers",
                    key=f"submit_multi_{next_q.key}",
                    on_click=answer_question,
                    args=(selected_options,),
                    disabled=not selected_options,
                    use_container_width=True,
                )
            else:
                st.markdown("**Suggested answers**")
                cols = st.columns(min(3, max(1, len(options))))
                for idx, option in enumerate(options):
                    with cols[idx % len(cols)]:
                        st.button(
                            option,
                            key=f"answer_{next_q.key}_{idx}",
                            on_click=answer_question,
                            args=(option,),
                            use_container_width=True,
                        )

        custom = st.chat_input("Answer input")
        if custom:
            answer_question(custom)
            st.rerun()
    elif (
        st.session_state.get("optional_questions_consent", "") == ""
        and not st.session_state.get("optional_questions_done")
        and unanswered_optional_questions()
    ):
        optional_count = len(unanswered_optional_questions())
        st.success("All essential design questions are complete.")
        st.markdown(
            f"**Would you like to answer additional optional questions to further refine the HLD?** "
            f"There are up to {optional_count} optional questions covering areas like business drivers, "
            "scope, backup, monitoring, and operational details. You can skip them at any point."
        )
        consent_cols = st.columns(2)
        with consent_cols[0]:
            st.button(
                "Yes, ask the optional questions",
                key="accept_optional_questions",
                type="primary",
                use_container_width=True,
                on_click=accept_optional_questions,
            )
        with consent_cols[1]:
            st.button(
                "No, continue with essentials only",
                key="decline_optional_questions",
                use_container_width=True,
                on_click=decline_optional_questions,
            )
    else:
        st.success("Questionnaire complete.")
        est_seconds = estimate_ppt_generation_seconds()
        st.info(f"Estimated PPT generation time: ~{est_seconds // 60}m {est_seconds % 60}s")
        generation_sig = _generation_signature()

        with st.expander("Review architecture diagrams (optional)", expanded=False):
            st.caption(
                "Preview the diagrams selected for this design based on your answers. "
                "Untick any diagram you don't want in the generated HLD."
            )
            if st.button("Preview diagram selection", key="preview_figures_btn"):
                st.session_state.figure_candidates = compute_figure_candidates()
                valid_paths = {str(row.get("local_path", "")) for row in st.session_state.figure_candidates}
                st.session_state.figure_excluded = [p for p in st.session_state.get("figure_excluded", []) if p in valid_paths]
                if not st.session_state.figure_candidates:
                    st.warning("No eligible diagrams found. Build the Tech Zone RAG store first (sidebar).")
            _candidates = st.session_state.get("figure_candidates", [])
            if _candidates:
                from sa_hld_bot.image_select import figure_attribute_tags
                _excluded = set(st.session_state.get("figure_excluded", []))
                for _idx, _row in enumerate(_candidates):
                    _lp = str(_row.get("local_path", ""))
                    fig_cols = st.columns([2, 3])
                    with fig_cols[0]:
                        try:
                            st.image(_lp, use_container_width=True)
                        except Exception:
                            st.caption("(image preview unavailable)")
                    with fig_cols[1]:
                        st.markdown(f"**{_row.get('slide_title') or _row.get('caption') or 'Diagram'}**")
                        if _row.get("caption"):
                            st.caption(str(_row.get("caption")))
                        _tags = figure_attribute_tags(_row)
                        if _tags:
                            st.caption("Attributes: " + " · ".join(_tags))
                        if _row.get("page_url"):
                            st.caption(f"Source: {_row.get('page_url')}")
                        _keep = st.checkbox(
                            "Include in HLD",
                            value=_lp not in _excluded,
                            key=f"figure_keep_{_idx}",
                        )
                        if _keep:
                            _excluded.discard(_lp)
                        else:
                            _excluded.add(_lp)
                    st.divider()
                st.session_state.figure_excluded = sorted(_excluded)
                _kept = len(_candidates) - len([p for p in _excluded if p in {str(r.get('local_path','')) for r in _candidates}])
                st.caption(f"{_kept} of {len(_candidates)} diagrams will be included.")

        st.caption("You can ask follow-up design questions or edit the diagrams.")
        user_q = st.chat_input(
            "Ask about the architecture, or edit diagrams (e.g. 'remove the DMZ image', "
            "'add a True SSO diagram', 'use double DMZ', 'regenerate')",
            key="post_question",
        )
        if user_q:
            chat_add("user", user_q)
            _settings, _rag_store, _orchestrator = get_agents()
            if not _orchestrator or not _rag_store:
                chat_add("assistant", "Azure AI Foundry and RAG store are required.")
            else:
                cmd = parse_command(_rag_store.foundry, user_q) if looks_like_image_command(user_q) else {"action": "none"}
                if cmd.get("action", "none") != "none":
                    new_rows, msg = apply_command(
                        _rag_store, cmd, effective_answers(st.session_state.answers),
                        st.session_state.selected_products, st.session_state.last_references,
                        st.session_state.ppt_preview_images, limit=10,
                    )
                    st.session_state.ppt_preview_images = new_rows
                    rebuild_deck_from_state()
                    chat_add("assistant", msg or "Updated the diagrams.")
                else:
                    result = _orchestrator.answer(user_q)
                    if result.blocked:
                        chat_add("assistant", f"Blocked by guardrail: {result.blocked_reason}")
                    else:
                        refs = "\n".join(f"- {src}" for src in result.citations[:8])
                        chat_add("assistant", f"{result.answer}\n\nSources:\n{refs}")
            st.rerun()

        if st.button("Generate Customer HLD PPT", type="primary"):
            _settings, _rag_store, _orchestrator = get_agents()
            if not _orchestrator or not _rag_store:
                st.error("Azure AI Foundry and RAG store are required.")
            else:
                with st.spinner(f"Generating RAG-grounded customer PowerPoint (estimated ~{est_seconds // 60}m {est_seconds % 60}s)..."):
                    output_path, docx_path, refs, _preview = generate_hld_outputs(_orchestrator, _rag_store)
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
