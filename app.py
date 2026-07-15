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
    st.session_state.setdefault("custom_sections", [])
    st.session_state.setdefault("excluded_sections", [])
    st.session_state.setdefault("feedback_mode", "")
    st.session_state.setdefault("fragment_chat_pending", False)
    st.session_state.setdefault("fragment_chat_value", None)
    st.session_state.setdefault("fragment_chat_action", {})
    st.session_state.setdefault("add_diagram_match_notice", {})
    st.session_state.setdefault("use_sample_template", True)
    st.session_state.setdefault("sample_template_path", "/Users/prasanththangaraj/Downloads/test-for AI.pptx")
    st.session_state.setdefault("_visible_questions_cache", {"signature": "", "questions": []})


def chat_add(
    role: str,
    content: str,
    details: list[str] | None = None,
    action: dict[str, str] | None = None,
) -> None:
    message: dict[str, object] = {"role": role, "content": content}
    if details:
        message["details"] = list(details)
    if action:
        message["action"] = dict(action)
    st.session_state.messages.append(message)


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
    st.session_state.custom_sections = []
    st.session_state.excluded_sections = []


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
        limit=int(st.session_state.get("figure_limit", 10)),
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


def queue_fragment_question() -> None:
    """Capture the submitted chat value before rerunning only the fragment."""
    value = st.session_state.get("post_question")
    if not value or st.session_state.get("fragment_chat_pending"):
        return
    st.session_state.fragment_chat_value = value
    st.session_state.fragment_chat_pending = True
    text = getattr(value, "text", None)
    if text is None:
        text = str(value or "")
    files = list(getattr(value, "files", []) or [])
    display_text = text or ("(attached a diagram image)" if files else "")
    chat_add("user", display_text)


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


def _record_generation_time(seconds: float) -> None:
    """Persist actual generation durations so future estimates are accurate."""
    path = ROOT / "data" / "generation_times.json"
    try:
        history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        history = []
    history = (list(history) + [round(float(seconds), 1)])[-10:]
    try:
        path.write_text(json.dumps(history), encoding="utf-8")
    except Exception:
        pass


def estimated_generation_seconds() -> int:
    """Estimate from the average of recent actual runs; fall back to the model."""
    path = ROOT / "data" / "generation_times.json"
    try:
        history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        history = []
    recent = [float(v) for v in history[-5:] if isinstance(v, (int, float))]
    if recent:
        return max(30, int(sum(recent) / len(recent)))
    return estimate_ppt_generation_seconds()


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


HLD_INSTRUCTION = (
    "Use Omnissa Tech Zone context from the RAG results only. Write for a formal high-level design document. "
    "Ground each recommendation in the retrieved architecture guidance. If a customer detail is unknown or to be confirmed, "
    "state it as an assumption or open item rather than inventing a value."
)


def _section_prompt(section: str, context_blob: str, product_keys: list[str]) -> str:
    """Grounded generation prompt for a named HLD section (shared by full
    generation and conversational rewrites)."""
    if section == "summary":
        return f"{HLD_INSTRUCTION}\nCreate a customer-facing executive summary based on these inputs:\n{context_blob}"
    if section == "architecture":
        return f"{HLD_INSTRUCTION}\nProvide a high-level architecture description for selected products {', '.join(product_keys)} and customer inputs:\n{context_blob}"
    if section == "security":
        return f"{HLD_INSTRUCTION}\nDescribe security standards, access design, RBAC, certificates, hardening, and logging for these customer constraints:\n{context_blob}"
    if section == "networking":
        return f"{HLD_INSTRUCTION}\nDescribe the networking requirements for this design: network segments/subnets, required ports and firewall rules, load balancing, DNS/DHCP/NTP dependencies, and external access paths, for these customer constraints:\n{context_blob}"
    if section == "operations":
        return f"{HLD_INSTRUCTION}\nDescribe operational model, HA, backup, monitoring, and DR for these customer constraints:\n{context_blob}"
    if section in PRODUCTS:
        return f"{HLD_INSTRUCTION}\nProvide customer-facing HLD detailed design guidance for {PRODUCTS[section].title} using these inputs:\n{context_blob}"
    return f"{HLD_INSTRUCTION}\nProvide customer-facing HLD design guidance about {section.replace('_', ' ')} using these inputs:\n{context_blob}"


def apply_hld_edit(cmd: dict, orchestrator, rag_store) -> str:
    """Execute a conversational HLD edit and rebuild the outputs. Returns a chat message."""
    from sa_hld_bot.hld_followup import section_display_name

    action = cmd.get("action", "qa")
    answers = effective_answers(st.session_state.answers)
    context_blob = "\n".join(f"{k}: {v}" for k, v in answers.items())
    product_keys = st.session_state.selected_products
    titles = {k: PRODUCTS[k].title for k in product_keys if k in PRODUCTS}

    if action == "update_answer":
        key, value = cmd["answer_key"], cmd["answer_value"]
        if not value:
            return "Tell me the new value, for example: 'change the DMZ design to Double DMZ'."
        st.session_state.answers[key] = value
        clear_question_cache()
        from sa_hld_bot.image_select import select_hld_images
        st.session_state.ppt_preview_images = select_hld_images(
            rag_store, product_keys, effective_answers(st.session_state.answers),
            st.session_state.get("last_references", []),
            limit=int(st.session_state.get("figure_limit", 10)),
        )
        rebuild_deck_from_state()
        return (
            f"Updated **{key.replace('_', ' ')}** to '{value}', re-selected the matching Tech Zone diagrams, "
            "and rebuilt the HLD. Ask me to 'rewrite' any section if its narrative should reflect this change too."
        )

    if action == "rewrite_section":
        section = cmd["section"]
        prompt = (
            f"{_section_prompt(section, context_blob, product_keys)}\n\n"
            f"Revision request from the architect: {cmd['instruction']}\n"
            "Rewrite the section accordingly, staying grounded in the Tech Zone context."
        )
        result = orchestrator.answer(prompt)
        if result.blocked:
            return f"Blocked by guardrail: {result.blocked_reason}"
        narrative = dict(st.session_state.get("rag_narrative") or {})
        narrative[section] = clean_markdown_text(result.answer, max_len=2400)
        st.session_state.rag_narrative = narrative
        st.session_state.excluded_sections = [s for s in st.session_state.get("excluded_sections", []) if s != section]
        rebuild_deck_from_state()
        refs = "\n".join(f"- {u}" for u in (result.citations or [])[:5])
        return f"Rewrote the **{section_display_name(section, titles)}** section and rebuilt the HLD.\n\nSources:\n{refs}"

    if action == "add_section":
        prompt = (
            f"{HLD_INSTRUCTION}\nWrite a customer-facing HLD section titled '{cmd['title']}' covering: "
            f"{cmd['instruction']}\nCustomer inputs:\n{context_blob}"
        )
        result = orchestrator.answer(prompt)
        if result.blocked:
            return f"Blocked by guardrail: {result.blocked_reason}"
        sections = [s for s in st.session_state.get("custom_sections", [])
                    if s.get("title", "").lower() != cmd["title"].lower()]
        sections.append({
            "title": cmd["title"],
            "content": clean_markdown_text(result.answer, max_len=2400),
            "keywords": cmd.get("keywords", []),
        })
        st.session_state.custom_sections = sections
        rebuild_deck_from_state()
        refs = "\n".join(f"- {u}" for u in (result.citations or [])[:5])
        return f"Added the section **{cmd['title']}** (grounded in Tech Zone) and rebuilt the HLD.\n\nSources:\n{refs}"

    if action == "remove_section":
        section = cmd["section"]
        custom = st.session_state.get("custom_sections", [])
        remaining = [s for s in custom if _slugify(s.get("title", "")) != section]
        if len(remaining) != len(custom):
            st.session_state.custom_sections = remaining
        else:
            excluded = set(st.session_state.get("excluded_sections", []))
            excluded.add(section)
            st.session_state.excluded_sections = sorted(excluded)
        rebuild_deck_from_state()
        return f"Removed the **{section_display_name(section, titles)}** section and rebuilt the HLD."

    if action == "regenerate_all":
        output_path, docx_path, refs, _preview = generate_hld_outputs(orchestrator, rag_store)
        return f"Regenerated the full HLD: `{output_path}` and `{docx_path}`"

    return ""


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(title).lower()).strip("_")


def _app_log():
    from sa_hld_bot.logging_utils import get_logger

    return get_logger("sa_hld_bot.app", ROOT / "data" / "logs")


def _caption_rows_local() -> list[dict]:
    """Caption store rows read directly from disk (no RAG store required)."""
    path = ROOT / "data" / "image_captions.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


_WHY_STOPWORDS = {"diagram", "image", "figure", "picture", "shown", "included", "selected", "output", "why", "wasnt", "isnt", "this", "that", "the", "not", "was"}


def _find_row_by_keywords(text: str) -> dict | None:
    """Best caption-store match for a free-text diagram description."""
    normalized = str(text).lower()
    words = list(dict.fromkeys(
        w for w in re.findall(r"[a-z0-9-]+", normalized)
        if len(w) > 3 and w not in _WHY_STOPWORDS
    ))
    if not words:
        return None
    desired_ha = (
        "active_active" if re.search(r"active[\s/-]+active", normalized)
        else "active_passive" if re.search(r"active[\s/-]+passive", normalized)
        else ""
    )
    best, best_hits = None, 0
    for row in _caption_rows_local():
        haystack = " ".join([
            str(row.get("caption", "")), str(row.get("topic", "")),
            str(row.get("section_heading", "")), str(row.get("page_url", "")),
        ]).lower()
        hits = sum(1 for w in words if w in haystack)
        if desired_ha:
            from sa_hld_bot.image_select import diagram_profile
            if diagram_profile(row).get("ha_model") == desired_ha:
                hits += 3
        if hits > best_hits:
            best, best_hits = row, hits
    return best if best_hits >= 2 else None


def _direct_design_conflict_explanation(text: str, answers: dict[str, str]) -> str:
    """Answer clear availability-model conflicts from verified interview inputs."""
    normalized = str(text).lower()
    requested = (
        "active_active" if re.search(r"active[\s/-]+active", normalized)
        else "active_passive" if re.search(r"active[\s/-]+passive", normalized)
        else ""
    )
    availability = normalize_answer(answers.get("availability_requirements"))
    selected_text = availability.lower()
    selected = (
        "active_active" if re.search(r"active[\s/-]+active", selected_text)
        else "active_passive" if re.search(r"active[\s/-]+passive", selected_text)
        else ""
    )
    if not requested or not selected or requested == selected:
        return ""

    requested_label = "active-active" if requested == "active_active" else "active-passive"
    selected_label = "active-active" if selected == "active_active" else "active-passive"
    site = normalize_answer(answers.get("site_topology"))
    site_clause = f" and the site topology is **{site}**" if site else ""
    return (
        f"The **{requested_label}** diagram was not selected because your design input is "
        f"**{availability or selected_label}**{site_clause}. The selector excludes diagrams that show the "
        f"opposite availability model, so it correctly favored {selected_label} multi-site views. "
        f"If the intended architecture is {requested_label}, change the availability requirement to "
        f"**Multi-site {requested_label.replace('-', '/')}** and regenerate the diagram set."
    )


def _looks_like_why_question(text: str) -> bool:
    from sa_hld_bot.diagram_qa import looks_like_diagram_selection_question

    return looks_like_diagram_selection_question(text)


def enable_clipboard_paste_bridge() -> None:
    """Let users paste images from the clipboard (Ctrl/Cmd+V) anywhere on the page.

    The pasted image is forwarded as a synthetic drop event to the nearest
    visible dropzone: a file uploader if one is open (e.g. the Add-a-diagram
    popover), otherwise the chat input's attachment zone.
    """
    js = """
    <script>
    (function() {
      const P = window.parent;
      if (P.__hldPasteBridgeV1) return;
      P.__hldPasteBridgeV1 = true;
      P.document.addEventListener('paste', function(ev) {
        try {
          const items = (ev.clipboardData || {}).items || [];
          let file = null;
          for (const it of items) {
            if (it.kind === 'file' && it.type && it.type.indexOf('image/') === 0) {
              file = it.getAsFile();
              break;
            }
          }
          if (!file) return;
          const zones = Array.from(P.document.querySelectorAll(
            '[data-testid="stFileUploaderDropzone"], [data-testid="stChatInputFileUploadButton"]'
          )).filter(el => el.offsetParent !== null);
          if (!zones.length) return;
          const uploader = zones.find(el => el.getAttribute('data-testid') === 'stFileUploaderDropzone');
          const target = uploader || zones[0];
          const named = new File([file], 'pasted-' + Date.now() + '.png', {type: file.type || 'image/png'});
          const dt = new DataTransfer();
          dt.items.add(named);
          const event = new Event('drop', {bubbles: true, cancelable: true});
          event.dataTransfer = dt;
          target.dispatchEvent(event);
          ev.preventDefault();
        } catch (e) {}
      }, true);
    })();
    </script>
    """
    try:
        # st.html strips <script> tags unless JavaScript is explicitly allowed.
        st.html(js, unsafe_allow_javascript=True)
    except TypeError:
        # Older Streamlit: fall back to an iframe component, which executes JS.
        import streamlit.components.v1 as components

        components.html(js, height=0)


def record_figure_feedback(vote: str, row: dict, reason: str = "") -> None:
    """Persist per-diagram thumbs feedback from the curation panel."""
    from sa_hld_bot.feedback import figure_keys_for_rows, record_feedback
    from sa_hld_bot.image_select import requirement_profile

    req = requirement_profile(effective_answers(st.session_state.answers), st.session_state.selected_products)
    record_feedback(
        ROOT / "data", vote, req, st.session_state.selected_products,
        reason=reason, figure_keys=figure_keys_for_rows([row]), kind="figure",
        session_id=st.session_state.get("current_session_id", ""),
    )


def record_hld_feedback(vote: str, reason: str = "", uploaded: bytes | None = None) -> str:
    """Persist a thumbs up/down and return an extra note for the UI message."""
    from sa_hld_bot.feedback import figure_keys_for_rows, match_uploaded_image, record_feedback
    from sa_hld_bot.image_select import requirement_profile

    req = requirement_profile(effective_answers(st.session_state.answers), st.session_state.selected_products)
    shown_rows = st.session_state.get("ppt_preview_images", [])
    figure_keys: list[dict] = []
    kind = "hld"
    extra = ""
    if vote == "up":
        figure_keys = figure_keys_for_rows(shown_rows)
    elif uploaded:
        match = match_uploaded_image(_caption_rows_local(), uploaded)
        if match is not None:
            figure_keys = figure_keys_for_rows([match])
            kind = "figure"
            extra = f" Identified diagram: {match.get('caption', '')}."
        else:
            extra = " (The uploaded image didn't match a library diagram; the written reason was still recorded.)"
    record_feedback(
        ROOT / "data", vote, req, st.session_state.selected_products,
        reason=reason, figure_keys=figure_keys, kind=kind,
        session_id=st.session_state.get("current_session_id", ""),
    )
    return extra


def generate_hld_outputs(orchestrator, rag_store, progress=None):
    from sa_hld_bot.ppt_builder import HldPptBuilder
    from sa_hld_bot.docx_builder import HldDocxBuilder

    def report(message: str) -> None:
        if progress:
            try:
                progress(message)
            except Exception:
                pass

    answers = effective_answers(st.session_state.answers)
    product_keys = st.session_state.selected_products
    product_objs = [PRODUCTS[key] for key in product_keys if key in PRODUCTS]
    report("Preparing the Tech Zone reference set")
    resources = compile_reference_resources(product_keys)

    context_blob = "\n".join(f"{k}: {v}" for k, v in answers.items())
    prompts = {key: _section_prompt(key, context_blob, product_keys) for key in
               ["summary", "architecture", "security", "networking", "operations", *product_keys]}

    # Replay reviewer feedback (thumbs-down reasons from similar designs) into
    # the generation prompts so past complaints are addressed automatically.
    try:
        from sa_hld_bot.feedback import narrative_guidance
        from sa_hld_bot.image_select import requirement_profile
        guidance = narrative_guidance(ROOT / "data", requirement_profile(answers, product_keys))
    except Exception:
        guidance = []
    if guidance:
        report(f"Applying {len(guidance)} learned reviewer feedback note(s)")
        guidance_text = "\nPrevious reviewer feedback on similar designs — address it in this document:\n" + "\n".join(f"- {g}" for g in guidance)
        prompts = {key: value + guidance_text for key, value in prompts.items()}

    narrative: dict[str, str] = {}
    all_citations: list[str] = []
    section_names = {"summary": "Executive summary", "architecture": "Solution overview", "security": "Security standards", "networking": "Networking requirements", "operations": "Operations and recovery"}
    for section, prompt in prompts.items():
        display = section_names.get(section, PRODUCTS[section].title if section in PRODUCTS else section)
        report(f"Writing section: {display}")
        result = orchestrator.answer(prompt)
        if result.blocked:
            narrative[section] = f"RAG guardrail block: {result.blocked_reason}"
        else:
            narrative[section] = clean_markdown_text(result.answer, max_len=1500)
        all_citations.extend(result.citations)

    report("Compiling document references")
    citation_refs = [resources_url for resources_url in all_citations if resources_url]
    derived_refs = derive_solution_references(product_keys, answers)
    base_refs = [resource.url for resource in resources]
    candidate_references = filter_solution_references(product_keys, answers, list(dict.fromkeys(base_refs + derived_refs + citation_refs)))
    report("Selecting architecture diagrams matched to your answers")
    from sa_hld_bot.image_select import select_hld_images as select_hld_images_v3
    image_rows = select_hld_images_v3(
        rag_store,
        selected_products=product_keys,
        answers=answers,
        reference_urls=candidate_references,
        limit=int(st.session_state.get("figure_limit", 10)),
    )
    # Respect any exclusions made in the diagram review panel.
    excluded_figures = set(st.session_state.get("figure_excluded", []))
    if excluded_figures:
        image_rows = [row for row in image_rows if str(row.get("local_path", "")) not in excluded_figures]
    report(f"Matched {len(image_rows)} diagrams: " + "; ".join((r.get("caption") or "")[:60] for r in image_rows[:6]) + ("..." if len(image_rows) > 6 else ""))
    references = filter_solution_references(
        product_keys,
        answers,
        list(dict.fromkeys(citation_refs + image_source_references(image_rows))),
    )

    output_dir = ROOT / "output"
    base_name = f"{answers.get('customer_name', 'Customer').replace(' ', '_')}_HLD_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    output_path = output_dir / f"{base_name}.pptx"
    docx_path = output_dir / f"{base_name}.docx"

    report("Building the PowerPoint deck")
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
    report("Building the Word document (cover, TOC, numbered sections, figures)")
    docx_builder = HldDocxBuilder()
    docx_builder.build(
        output_path=docx_path,
        customer_name=answers.get("customer_name", "Customer"),
        selected_products=product_objs,
        questionnaire=answers,
        rag_narrative=narrative,
        references=references,
        image_rows=image_rows,
        custom_sections=st.session_state.get("custom_sections", []),
        excluded_sections=set(st.session_state.get("excluded_sections", [])),
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
    report("Saving the session")
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
        "custom_sections": st.session_state.get("custom_sections", []),
        "excluded_sections": st.session_state.get("excluded_sections", []),
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
    st.session_state.custom_sections = payload.get("custom_sections", []) or []
    st.session_state.excluded_sections = payload.get("excluded_sections", []) or []
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
        custom_sections=st.session_state.get("custom_sections", []),
        excluded_sections=set(st.session_state.get("excluded_sections", [])),
    )
    st.session_state.ppt_path = str(output_path)
    st.session_state.docx_path = str(docx_path)
    persist_current_session()


def add_uploaded_diagram_from_rag(
    uploaded_bytes: bytes,
    mime_type: str,
    progress=None,
) -> dict[str, object]:
    """Resolve a pasted image through the Tech Zone image RAG and add it."""
    report = progress or (lambda _message: None)
    report("Loading the Tech Zone diagram index")
    _settings, rag_store_obj, _orchestrator = initialize_agents()
    if not rag_store_obj:
        return {"status": "unavailable", "message": "The Tech Zone RAG store is unavailable."}

    from sa_hld_bot.feedback import caption_key, match_uploaded_image_in_rag

    report("Extracting visible labels and topology from the pasted diagram")
    match, evidence = match_uploaded_image_in_rag(
        rag_store_obj,
        uploaded_bytes,
        mime_type=mime_type,
        rows=rag_store_obj._load_caption_rows(),
    )
    if match is None:
        reason = str(evidence.get("reason") or "No confident matching Tech Zone diagram was found.")
        return {
            "status": "not_found",
            "message": reason,
            "evidence": evidence,
        }

    report("Verifying the matched diagram against the current HLD")
    shown_rows = list(st.session_state.get("ppt_preview_images", []))
    existing = {caption_key(row) for row in shown_rows}
    caption = str(match.get("caption") or match.get("title") or "Tech Zone diagram")
    if caption_key(match) in existing:
        return {
            "status": "existing",
            "message": f"'{caption}' is already in the HLD.",
            "match": match,
            "evidence": evidence,
        }

    report("Adding the verified Tech Zone diagram and rebuilding the HLD")
    new_row = dict(match)
    new_row["slide_title"] = new_row.get("caption") or new_row.get("title")
    st.session_state.ppt_preview_images = shown_rows + [new_row]
    rebuild_deck_from_state()
    details = [
        f"Match method: {evidence.get('method', 'RAG image search')}",
        f"Match confidence: {evidence.get('confidence', 'unknown')}",
        f"Tech Zone source: {new_row.get('page_url', '')}",
    ]
    if evidence.get("reason"):
        details.append(f"Verification: {evidence['reason']}")
    chat_add(
        "assistant",
        f"Matched the pasted image to **{caption}**, added the authoritative Tech Zone diagram, and rebuilt the HLD.",
        details,
    )
    return {
        "status": "added",
        "message": f"Added: {caption}",
        "match": new_row,
        "evidence": evidence,
    }


def process_hld_chat_request(chat_value, progress) -> bool:
    """Process one queued HLD chat request; return whether the whole app changed."""
    user_q = getattr(chat_value, "text", None)
    if user_q is None:
        user_q = str(chat_value or "")
    attached = list(getattr(chat_value, "files", []) or [])
    log = _app_log()
    started = time.time()
    trace: list[str] = []

    def note(message: str) -> None:
        trace.append(message)
        progress(message)
        log.info("Chat step %d: %s", len(trace), message)

    log.info("Chat request: %r | attachments=%d", (user_q or "")[:200], len(attached))
    note("Understanding your question")
    settings_obj, rag_store_obj, orchestrator_obj = initialize_agents()
    if not rag_store_obj or not orchestrator_obj:
        chat_add("assistant", "Azure AI Foundry and the Tech Zone design store are required.", trace)
        return False

    foundry = rag_store_obj.foundry
    changed_hld = False
    if attached:
        from sa_hld_bot.feedback import caption_key, match_uploaded_image_in_rag

        library = rag_store_obj._load_caption_rows()
        note(f"Matching the attached image against {len(library)} indexed Tech Zone diagrams")
        upload = attached[0]
        match, match_evidence = match_uploaded_image_in_rag(
            rag_store_obj,
            upload.getvalue() if hasattr(upload, "getvalue") else upload.read(),
            mime_type=str(getattr(upload, "type", "") or "image/png"),
            rows=library,
        )
        text_lower = (user_q or "").lower()
        wants_rationale = any(
            word in text_lower for word in ("why", "rationale", "reason", "explain")
        )
        wants_add = not text_lower or any(
            word in text_lower for word in ("add", "include", "insert")
        )
        if match is None:
            reason = str(match_evidence.get("reason") or "No confident match was found.")
            chat_add(
                "assistant",
                f"I couldn't confidently match that image to an indexed Tech Zone diagram. {reason}",
                trace,
            )
        elif wants_add and not wants_rationale:
            note("Adding the verified RAG match and rebuilding the HLD")
            row = dict(match)
            row["slide_title"] = row.get("caption") or row.get("title")
            existing = {caption_key(item) for item in st.session_state.ppt_preview_images}
            if caption_key(row) in existing:
                chat_add("assistant", f"'{match.get('caption', '')}' is already in the HLD.", trace)
            else:
                st.session_state.ppt_preview_images = list(st.session_state.ppt_preview_images) + [row]
                rebuild_deck_from_state()
                trace.extend([
                    f"Match method: {match_evidence.get('method', 'RAG image search')}",
                    f"Match confidence: {match_evidence.get('confidence', 'unknown')}",
                    f"Tech Zone source: {match.get('page_url', '')}",
                ])
                chat_add(
                    "assistant",
                    f"Matched the pasted image to **{match.get('caption', '')}**, added the authoritative Tech Zone diagram, and rebuilt the HLD.",
                    trace,
                )
                changed_hld = True
        elif not wants_rationale and any(word in text_lower for word in ("remove", "drop", "delete", "exclude")):
            note("Removing the matched diagram and rebuilding the HLD")
            from sa_hld_bot.image_select import image_content_keys

            target = {
                key for key in (*image_content_keys(str(match.get("local_path", ""))), caption_key(match)) if key
            }
            kept = []
            for row in st.session_state.ppt_preview_images:
                keys = {
                    key for key in (*image_content_keys(str(row.get("local_path", ""))), caption_key(row)) if key
                }
                if keys & target:
                    continue
                kept.append(row)
            removed = len(st.session_state.ppt_preview_images) - len(kept)
            st.session_state.ppt_preview_images = kept
            if removed:
                rebuild_deck_from_state()
                chat_add("assistant", f"Removed '{match.get('caption', '')}' and rebuilt the HLD.", trace)
                changed_hld = True
            else:
                chat_add("assistant", "That diagram isn't currently in the HLD.", trace)
        else:
            note("Reading the relevant questionnaire decisions")
            note("Replaying the diagram-selection rules")
            from sa_hld_bot.hld_followup import conversational_rationale
            from sa_hld_bot.image_select import explain_figure_selection

            facts = explain_figure_selection(
                match,
                effective_answers(st.session_state.answers),
                st.session_state.selected_products,
                st.session_state.get("last_references", []),
                st.session_state.get("ppt_preview_images", []),
                data_dir=ROOT / "data",
            )
            note("Validating the explanation against the evidence")
            chat_add("assistant", conversational_rationale(foundry, user_q, facts), trace)
    elif _looks_like_why_question(user_q):
        from sa_hld_bot.diagram_qa import answer_diagram_question

        result = answer_diagram_question(
            foundry=foundry,
            question=user_q,
            answers=effective_answers(st.session_state.answers),
            selected_products=st.session_state.selected_products,
            reference_urls=st.session_state.get("last_references", []),
            shown_rows=st.session_state.get("ppt_preview_images", []),
            candidate_rows=_caption_rows_local(),
            progress=note,
            understanding_reported=True,
        )
        chat_add("assistant", result.answer, result.evidence_steps, result.suggested_action)
    else:
        note("Interpreting whether this is a question or an HLD change")
        command = parse_command(foundry, user_q) if looks_like_image_command(user_q) else {"action": "none"}
        if command.get("action", "none") != "none":
            note("Applying the diagram change and rebuilding the HLD")
            new_rows, message = apply_command(
                rag_store_obj,
                command,
                effective_answers(st.session_state.answers),
                st.session_state.selected_products,
                st.session_state.last_references,
                st.session_state.ppt_preview_images,
                limit=int(st.session_state.get("figure_limit", 10)),
            )
            st.session_state.ppt_preview_images = new_rows
            rebuild_deck_from_state()
            chat_add("assistant", message or "Updated the diagrams.", trace)
            changed_hld = True
        else:
            from sa_hld_bot.hld_followup import looks_like_edit_command, parse_hld_command

            hld_command = {"action": "qa"}
            if looks_like_edit_command(user_q):
                custom_slugs = [
                    _slugify(section.get("title", ""))
                    for section in st.session_state.get("custom_sections", [])
                ]
                hld_command = parse_hld_command(
                    foundry,
                    user_q,
                    answer_keys=list(st.session_state.answers.keys()),
                    product_keys=st.session_state.selected_products + custom_slugs,
                )
            if hld_command.get("action", "qa") != "qa":
                note("Applying the requested change and rebuilding the HLD")
                message = apply_hld_edit(hld_command, orchestrator_obj, rag_store_obj)
                chat_add("assistant", message or "Done — HLD updated.", trace)
                changed_hld = True
            else:
                note("Searching Tech Zone and writing a grounded answer")
                answer = orchestrator_obj.answer(user_q)
                if answer.blocked:
                    chat_add("assistant", f"Blocked by guardrail: {answer.blocked_reason}", trace)
                else:
                    refs = "\n".join(f"- {source}" for source in answer.citations[:8])
                    chat_add("assistant", f"{answer.answer}\n\nSources:\n{refs}", trace)

    log.info("Chat request completed in %.1fs", time.time() - started)
    return changed_hld


@st.fragment
def render_hld_chat_fragment() -> None:
    """Render and process HLD chat without rerunning the document-review UI."""
    st.markdown("#### Ask about or refine this HLD")
    conversation = [message for message in st.session_state.messages[1:] if message.get("content")]
    if conversation:
        with st.container(height=min(560, 140 + 100 * min(len(conversation), 5)), border=True):
            for index, message in enumerate(conversation[-14:]):
                with st.chat_message(message.get("role", "assistant")):
                    details = [str(item) for item in (message.get("details") or []) if str(item).strip()]
                    if details and message.get("role") == "assistant":
                        with st.expander("What I checked", expanded=False):
                            for detail in details:
                                st.markdown(f"- {detail}")
                    st.markdown(str(message.get("content", "")))
                    action = message.get("action") or {}
                    if action.get("type") == "update_answer":
                        if st.button(
                            str(action.get("label") or "Apply this design change"),
                            key=f"chat_action_{len(conversation) - 14 + index}_{action.get('answer_key', '')}",
                            type="primary",
                            disabled=bool(
                                st.session_state.get("fragment_chat_pending")
                                or st.session_state.get("fragment_chat_action")
                            ),
                        ):
                            st.session_state.fragment_chat_action = dict(action)
                            st.rerun(scope="fragment")

    status_slot = st.empty()
    input_slot = st.empty()
    is_busy = bool(
        st.session_state.get("fragment_chat_pending")
        or st.session_state.get("fragment_chat_action")
    )
    with input_slot:
        st.chat_input(
            "e.g. 'remove the DMZ image', 'switch to double DMZ', 'expand the networking section', "
            "'why wasn't the active-active diagram shown?' — or attach a diagram image",
            key="post_question",
            accept_file=True,
            file_type=["png", "jpg", "jpeg"],
            disabled=is_busy,
            on_submit=queue_fragment_question,
        )

    if not is_busy:
        return

    with status_slot.container():
        with st.status("Thinking — starting", expanded=True, state="running") as status:
            current = {"slot": None, "message": ""}

            def progress(message: str) -> None:
                if current["slot"] is not None:
                    current["slot"].markdown(f"✅ {current['message']}")
                current["message"] = message
                current["slot"] = st.empty()
                with current["slot"].container():
                    st.spinner(message)
                status.update(label=f"Thinking — {message}", expanded=True, state="running")

            try:
                action = dict(st.session_state.get("fragment_chat_action") or {})
                if action:
                    progress("Updating the questionnaire decision")
                    key = str(action.get("answer_key", ""))
                    value = str(action.get("answer_value", ""))
                    if key not in {"availability_requirements", "horizon_dmz_design"} or not value:
                        raise ValueError("Unsupported or incomplete chat action")
                    st.session_state.answers[key] = value
                    clear_question_cache()
                    progress("Re-selecting diagrams from the updated design inputs")
                    _settings, rag_store_obj, _orchestrator = initialize_agents()
                    if not rag_store_obj:
                        raise RuntimeError("Tech Zone design store is unavailable")
                    from sa_hld_bot.image_select import select_hld_images

                    st.session_state.ppt_preview_images = select_hld_images(
                        rag_store_obj,
                        st.session_state.selected_products,
                        effective_answers(st.session_state.answers),
                        st.session_state.get("last_references", []),
                        limit=int(st.session_state.get("figure_limit", 10)),
                    )
                    progress("Rebuilding the HLD with the confirmed change")
                    rebuild_deck_from_state()
                    st.session_state.generated_signature = _generation_signature()
                    field_label = key.replace("_", " ").title()
                    trace = [
                        f"Updated {field_label} to {value}",
                        "Replayed diagram-selection rules",
                        "Rebuilt the Word and PowerPoint outputs",
                    ]
                    chat_add(
                        "assistant",
                        f"Updated **{key.replace('_', ' ')}** to **{value}** and regenerated the diagram set and HLD.",
                        trace,
                    )
                    st.session_state.fragment_chat_action = {}
                    if current["slot"] is not None:
                        current["slot"].markdown(f"✅ {current['message']}")
                    status.update(label="HLD updated", state="complete", expanded=True)
                    st.rerun(scope="app")
                else:
                    chat_value = st.session_state.get("fragment_chat_value")
                    changed_hld = process_hld_chat_request(chat_value, progress)
                    st.session_state.fragment_chat_pending = False
                    st.session_state.fragment_chat_value = None
                    if current["slot"] is not None:
                        current["slot"].markdown(f"✅ {current['message']}")
                    status.update(label="Answer ready", state="complete", expanded=True)
                    st.rerun(scope="app" if changed_hld else "fragment")
            except Exception as exc:
                st.session_state.fragment_chat_pending = False
                st.session_state.fragment_chat_value = None
                st.session_state.fragment_chat_action = {}
                if current["slot"] is not None:
                    current["slot"].markdown(f"⚠️ {current['message']}")
                chat_add("assistant", f"I couldn't complete that request: {exc}")
                status.update(label="Request could not be completed", state="error", expanded=True)


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
    _usage_path = ROOT / "data" / "llm_usage.jsonl"
    if _usage_path.exists():
        try:
            _today = datetime.now().strftime("%Y-%m-%d")
            _tok, _cost, _calls = 0, 0.0, 0
            for _line in _usage_path.read_text(encoding="utf-8").splitlines():
                try:
                    _u = json.loads(_line)
                except Exception:
                    continue
                if str(_u.get("ts", "")).startswith(_today):
                    _tok += int(_u.get("prompt_tokens", 0)) + int(_u.get("completion_tokens", 0))
                    _cost += float(_u.get("cost_usd", 0.0))
                    _calls += 1
            if _calls:
                st.caption(f"LLM usage today: {_calls} calls · {_tok:,} tokens · ${_cost:,.4f}")
        except Exception:
            pass
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

    st.subheader("Diagrams")
    _fig_limit = st.slider(
        "Max architecture diagrams",
        min_value=5, max_value=20,
        value=int(st.session_state.get("figure_limit", 10)),
        help="Upper limit for answer-matched diagrams selected into the HLD outputs.",
    )
    st.session_state.figure_limit = int(_fig_limit)

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
        with st.expander(f"Answered so far ({len(st.session_state.answers)})", expanded=False):
            from sa_hld_bot.catalog import required_questions
            _prompt_by_key = {
                q.key: q.prompt
                for q in required_questions(st.session_state.selected_products)
            }
            for key, value in st.session_state.answers.items():
                question_text = _prompt_by_key.get(key, key.replace("_", " ").title())
                st.markdown(f"**{question_text}**")
                st.caption(str(value))

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
                    width="stretch",
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
                            width="stretch",
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
                width="stretch",
                on_click=accept_optional_questions,
            )
        with consent_cols[1]:
            st.button(
                "No, continue with essentials only",
                key="decline_optional_questions",
                width="stretch",
                on_click=decline_optional_questions,
            )
    else:
        st.success("Questionnaire complete.")
        generation_sig = _generation_signature()

        st.caption(
            "Chat to refine the HLD: edit diagrams, sections, or design inputs; attach or paste a diagram image "
            "to ask why it was (not) included or to add/remove it. Everything stays grounded in Omnissa Tech Zone."
        )

        # Native clipboard paste: Streamlit's chat input / file uploader accept
        # drag-drop only, so bridge Ctrl+V by forwarding the clipboard image to
        # the nearest visible dropzone as a synthetic drop event.
        enable_clipboard_paste_bridge()

        # Chat submission and processing live in render_hld_chat_fragment().
        # Keeping this legacy branch inert avoids a whole-app chat rerun for
        # sessions created before the fragment workflow was introduced.
        _chat_value = None
        if _chat_value:
            user_q = getattr(_chat_value, "text", None)
            if user_q is None:
                user_q = str(_chat_value)
            _attached = list(getattr(_chat_value, "files", []) or [])
            chat_add("user", user_q or "(attached a diagram image)")
            _log = _app_log()
            _t0 = time.time()
            _chat_trace: list[str] = []
            _log.info("Chat request: %r | attachments=%d", (user_q or "")[:200], len(_attached))
            with st.status("Thinking — reading your question", expanded=True) as _chat_status:
                _chat_steps = {"n": 0}

                def _note(message: str) -> None:
                    _chat_steps["n"] += 1
                    _chat_trace.append(message)
                    _chat_status.write(f"**Step {_chat_steps['n']}** — {message}")
                    _chat_status.update(label=f"Thinking — {message}", expanded=True)
                    _log.info("Chat step %d: %s", _chat_steps["n"], message)

                _direct_answer = (
                    _direct_design_conflict_explanation(
                        user_q, effective_answers(st.session_state.answers)
                    )
                    if not _attached and _looks_like_why_question(user_q)
                    else ""
                )
                if _direct_answer:
                    _note("Comparing the requested diagram with your availability and site selections")
                    _note("Preparing a direct explanation from the verified design inputs")
                    chat_add("assistant", _direct_answer)
                    _settings = _rag_store = _orchestrator = None
                else:
                    _note("Loading the HLD design inputs and Tech Zone context")
                    _settings, _rag_store, _orchestrator = initialize_agents()

                if _direct_answer:
                    pass
                elif not _orchestrator or not _rag_store:
                    chat_add("assistant", "Azure AI Foundry and RAG store are required.")
                    _chat_status.update(label="Azure AI Foundry and RAG store are required", state="error")
                elif _attached:
                    from sa_hld_bot.feedback import caption_key, match_uploaded_image
                    _library = _caption_rows_local()
                    _note(f"Matching the pasted image against {len(_library)} Tech Zone diagrams")
                    _match = match_uploaded_image(_library, _attached[0].read())
                    _log.info("Image match finished in %.1fs -> %s", time.time() - _t0,
                              (_match or {}).get("caption", "NO MATCH"))
                    _tl = (user_q or "").lower()
                    _wants_rationale = (
                        not _tl
                        or "why" in _tl or "rationale" in _tl or "reason" in _tl or "explain" in _tl
                    )
                    if _match is None:
                        chat_add("assistant", "I couldn't match that image to any diagram in the Tech Zone library.")
                    elif not _wants_rationale and any(w in _tl for w in ("add", "include", "insert")):
                        _note(f"Adding '{(_match.get('caption') or '')[:60]}' and rebuilding the HLD")
                        _row = dict(_match)
                        _row["slide_title"] = _row.get("caption") or _row.get("title")
                        st.session_state.ppt_preview_images = list(st.session_state.ppt_preview_images) + [_row]
                        rebuild_deck_from_state()
                        chat_add("assistant", f"Added '{_match.get('caption', '')}' to the HLD and rebuilt the outputs.")
                    elif not _wants_rationale and any(w in _tl for w in ("remove", "drop", "delete", "exclude")):
                        _note(f"Removing '{(_match.get('caption') or '')[:60]}' and rebuilding the HLD")
                        from sa_hld_bot.image_select import image_content_keys
                        _target = {k for k in (*image_content_keys(str(_match.get("local_path", ""))), caption_key(_match)) if k}
                        _kept = []
                        for _r in st.session_state.ppt_preview_images:
                            _keys = {k for k in (*image_content_keys(str(_r.get("local_path", ""))), caption_key(_r)) if k}
                            if _keys & _target:
                                continue
                            _kept.append(_r)
                        _removed = len(st.session_state.ppt_preview_images) - len(_kept)
                        st.session_state.ppt_preview_images = _kept
                        if _removed:
                            rebuild_deck_from_state()
                            chat_add("assistant", f"Removed '{_match.get('caption', '')}' and rebuilt the HLD.")
                        else:
                            chat_add("assistant", "That diagram isn't currently in the HLD.")
                    else:
                        _note("Analyzing the diagram against your design answers")
                        from sa_hld_bot.hld_followup import conversational_rationale
                        from sa_hld_bot.image_select import explain_figure_selection
                        _facts = explain_figure_selection(
                            _match,
                            effective_answers(st.session_state.answers),
                            st.session_state.selected_products,
                            st.session_state.get("last_references", []),
                            st.session_state.get("ppt_preview_images", []),
                            data_dir=ROOT / "data",
                        )
                        _note("Writing the answer")
                        chat_add("assistant", conversational_rationale(_rag_store.foundry, user_q, _facts))
                else:
                    _note("Interpreting your request")
                    cmd = parse_command(_rag_store.foundry, user_q) if looks_like_image_command(user_q) else {"action": "none"}
                    if cmd.get("action", "none") != "none":
                        _note("Updating the diagram set and rebuilding the HLD")
                        new_rows, msg = apply_command(
                            _rag_store, cmd, effective_answers(st.session_state.answers),
                            st.session_state.selected_products, st.session_state.last_references,
                            st.session_state.ppt_preview_images,
                            limit=int(st.session_state.get("figure_limit", 10)),
                        )
                        st.session_state.ppt_preview_images = new_rows
                        rebuild_deck_from_state()
                        chat_add("assistant", msg or "Updated the diagrams.")
                    else:
                        from sa_hld_bot.hld_followup import looks_like_edit_command, parse_hld_command
                        hld_cmd = {"action": "qa"}
                        if looks_like_edit_command(user_q):
                            custom_slugs = [_slugify(s.get("title", "")) for s in st.session_state.get("custom_sections", [])]
                            hld_cmd = parse_hld_command(
                                _rag_store.foundry, user_q,
                                answer_keys=list(st.session_state.answers.keys()),
                                product_keys=st.session_state.selected_products + custom_slugs,
                            )
                        if hld_cmd.get("action", "qa") != "qa":
                            _note("Applying the change (Tech Zone grounded) and rebuilding the HLD")
                            msg = apply_hld_edit(hld_cmd, _orchestrator, _rag_store)
                            chat_add("assistant", msg or "Done — HLD updated.")
                        elif _looks_like_why_question(user_q) and (_why_row := _find_row_by_keywords(user_q)) is not None:
                            _note("Analyzing the diagram against your design answers")
                            from sa_hld_bot.hld_followup import conversational_rationale
                            from sa_hld_bot.image_select import explain_figure_selection
                            _facts = explain_figure_selection(
                                _why_row,
                                effective_answers(st.session_state.answers),
                                st.session_state.selected_products,
                                st.session_state.get("last_references", []),
                                st.session_state.get("ppt_preview_images", []),
                                data_dir=ROOT / "data",
                            )
                            _note("Writing the answer")
                            chat_add("assistant", conversational_rationale(_rag_store.foundry, user_q, _facts))
                        else:
                            _note("Searching Tech Zone and writing a grounded answer")
                            result = _orchestrator.answer(user_q)
                            if result.blocked:
                                chat_add("assistant", f"Blocked by guardrail: {result.blocked_reason}")
                            else:
                                refs = "\n".join(f"- {src}" for src in result.citations[:8])
                                chat_add("assistant", f"{result.answer}\n\nSources:\n{refs}")
                _log.info("Chat request completed in %.1fs", time.time() - _t0)
                for _message in reversed(st.session_state.messages):
                    if _message.get("role") == "assistant":
                        _message["details"] = list(_chat_trace)
                        break
                _chat_status.update(
                    label=f"Answer ready in {time.time() - _t0:.0f}s", state="complete", expanded=True,
                )
            st.rerun()

        if st.button("Generate Customer HLD Document", type="primary"):
            _est = estimated_generation_seconds()
            _start_ts = time.time()
            with st.status(
                f"Generating HLD — estimated ~{_est // 60}m {_est % 60:02d}s",
                expanded=True,
            ) as _gen_status:
                _step_counter = {"n": 0}

                def _progress(message: str) -> None:
                    _step_counter["n"] += 1
                    _gen_status.write(f"**Step {_step_counter['n']}** — {message}")
                    _gen_status.update(label=f"Generating HLD — {message}", expanded=True)

                _progress("Loading AI agents and vector store")
                _settings, _rag_store, _orchestrator = initialize_agents()
                if not _orchestrator or not _rag_store:
                    _gen_status.update(
                        label="Azure AI Foundry and RAG store are required",
                        state="error",
                        expanded=True,
                    )
                else:
                    output_path, docx_path, refs, _preview = generate_hld_outputs(
                        _orchestrator, _rag_store, progress=_progress
                    )
                    _elapsed = int(time.time() - _start_ts)
                    _record_generation_time(_elapsed)
                    _gen_status.update(
                        label=f"HLD generated in {_elapsed // 60}m {_elapsed % 60:02d}s",
                        state="complete",
                        expanded=True,
                    )
                    st.session_state.generated_signature = generation_sig
                    refs_short = "\n".join(f"- {url}" for url in refs[:6])
                    chat_add(
                        "assistant",
                        f"HLD generated in {_elapsed // 60}m {_elapsed % 60:02d}s: `{output_path}` and `{docx_path}`"
                        f"\n\nTop references used:\n{refs_short}",
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

        # ---------------- Architecture diagram curation (designer-style) ----------------
        if st.session_state.ppt_path or st.session_state.docx_path:
            st.markdown(
                """<style>
                div[data-testid="stImage"] img {border-radius: 10px;}
                div[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 14px;}
                </style>""",
                unsafe_allow_html=True,
            )
            st.markdown("### Review Architecture Diagrams")
            st.caption(
                "Curate the diagrams in this HLD: toggle any diagram out, leave per-diagram feedback "
                "(it trains future selection), add new ones, or prompt the chat below — e.g. "
                "'replace the DMZ diagram with the double-DMZ version'."
            )
            from sa_hld_bot.feedback import caption_key as _cap_key
            from sa_hld_bot.image_select import figure_attribute_tags as _fig_tags

            _shown_rows = st.session_state.get("ppt_preview_images", [])
            _add_cols = st.columns([2, 5])
            with _add_cols[0]:
                with st.popover("➕ Add a diagram", width="stretch"):
                    _add_desc = st.text_input(
                        "Describe the diagram to add",
                        placeholder="e.g. True SSO authentication flow",
                        key="add_diag_desc",
                    )
                    _add_upload = st.file_uploader(
                        "Or paste (Ctrl/Cmd+V) or drop the diagram image here",
                        type=["png", "jpg", "jpeg"],
                        key="add_diag_upl",
                    )

                    def _run_pasted_diagram_search() -> bool:
                        if _add_upload is None:
                            return False
                        with st.status("Searching the Tech Zone diagram RAG...", expanded=True) as _match_status:
                            _match_step = {"number": 0}

                            def _match_progress(message: str) -> None:
                                _match_step["number"] += 1
                                _match_status.write(f"**Step {_match_step['number']}** — {message}")
                                _match_status.update(label=message, state="running", expanded=True)

                            _result = add_uploaded_diagram_from_rag(
                                _add_upload.getvalue(),
                                str(getattr(_add_upload, "type", "") or "image/png"),
                                progress=_match_progress,
                            )
                            st.session_state.add_diagram_match_notice = _result
                            if _result.get("status") == "added":
                                _match_status.update(label=str(_result.get("message")), state="complete", expanded=False)
                                st.toast(str(_result.get("message")), icon="✅")
                                return True
                            if _result.get("status") == "existing":
                                _match_status.update(label=str(_result.get("message")), state="complete", expanded=False)
                            else:
                                _match_status.update(label="No confident RAG match found", state="error", expanded=True)
                            return False

                    if _add_upload is not None:
                        import hashlib as _hl2

                        _add_bytes = _add_upload.getvalue()
                        _add_digest = _hl2.md5(_add_bytes).hexdigest()
                        if st.session_state.get("last_pasted_add_digest") != _add_digest:
                            st.session_state.last_pasted_add_digest = _add_digest
                            st.session_state.add_diagram_match_notice = {}
                            if _run_pasted_diagram_search():
                                st.rerun()
                    _add_notice = dict(st.session_state.get("add_diagram_match_notice") or {})
                    if _add_notice.get("status") == "not_found":
                        st.warning(
                            "No confident Tech Zone diagram match was found. "
                            f"{_add_notice.get('message', '')} You can refine the search with a description above."
                        )
                    elif _add_notice.get("status") == "existing":
                        st.info(str(_add_notice.get("message", "That diagram is already in the HLD.")))
                    elif _add_notice.get("status") == "unavailable":
                        st.error(str(_add_notice.get("message", "The Tech Zone RAG store is unavailable.")))
                    if st.button("Search Tech Zone and add", key="add_diag_btn", type="primary"):
                        if _add_upload is not None and not _add_desc.strip():
                            if _run_pasted_diagram_search():
                                st.rerun()
                        else:
                            _settings, _rag_store, _orchestrator = get_agents()
                            if not _rag_store:
                                st.error("RAG store is required.")
                            elif not _add_desc.strip():
                                st.info("Paste a diagram or describe it first.")
                            else:
                                _words = [w for w in re.findall(r"[a-z0-9-]+", _add_desc.lower())
                                          if len(w) > 3 and w not in _WHY_STOPWORDS]
                                _new_rows, _msg = apply_command(
                                    _rag_store,
                                    {"action": "add", "keywords": _words or [_add_desc.lower()], "dmz": "", "indexes": []},
                                    effective_answers(st.session_state.answers),
                                    st.session_state.selected_products,
                                    st.session_state.last_references,
                                    _shown_rows,
                                    limit=int(st.session_state.get("figure_limit", 10)),
                                )
                                if len(_new_rows) != len(_shown_rows):
                                    st.session_state.ppt_preview_images = _new_rows
                                    with st.spinner("Rebuilding the HLD with the new diagram...", show_time=True):
                                        rebuild_deck_from_state()
                                    st.toast(_msg, icon="✅")
                                    st.rerun()
                                else:
                                    st.warning(_msg)

            if not _shown_rows:
                st.info("No diagrams are currently embedded. Use ➕ Add a diagram or the chat below.")
            _pending_exclude: list[tuple[str, dict]] = []
            _grid = st.columns(2)
            for _i, _row in enumerate(_shown_rows):
                _ck = _cap_key(_row) or f"idx{_i}"
                with _grid[_i % 2]:
                    with st.container(border=True):
                        _lp = str(_row.get("local_path", ""))
                        try:
                            st.image(_lp, width="stretch")
                        except Exception:
                            st.caption("(preview unavailable)")
                        st.markdown(f"**{_row.get('caption') or _row.get('slide_title') or 'Diagram'}**")
                        _tags = _fig_tags(_row)
                        if _tags:
                            st.caption(" · ".join(_tags))
                        _ctl = st.columns([1.1, 1])
                        _included = _ctl[0].toggle("Include", value=True, key=f"diag_inc_{_ck}")
                        with _ctl[1].popover("💬 Feedback", width="stretch"):
                            _fb_text = st.text_area(
                                "Issue or comment",
                                key=f"diag_fb_{_ck}",
                                placeholder="e.g. wrong DMZ layout for this design",
                            )
                            _fb_btns = st.columns(2)
                            if _fb_btns[0].button("👍 Right", key=f"diag_up_{_ck}"):
                                record_figure_feedback("up", _row, _fb_text)
                                st.toast("Reinforced for similar designs", icon="👍")
                            if _fb_btns[1].button("👎 Wrong", key=f"diag_down_{_ck}"):
                                record_figure_feedback("down", _row, _fb_text)
                                st.toast("Recorded — the selector will learn from this", icon="👎")
                        if not _included:
                            _pending_exclude.append((_ck, _row))
            if _pending_exclude:
                st.warning(f"{len(_pending_exclude)} diagram(s) marked for removal.")
                if st.button(
                    f"Apply changes — remove {len(_pending_exclude)} diagram(s) and rebuild the HLD",
                    type="primary",
                    key="apply_diag_changes",
                ):
                    with st.status("Updating the HLD...", expanded=True) as _upd_status:
                        _upd_status.write("**Step 1** — Recording feedback for removed diagrams")
                        _drop_keys = set()
                        for _ck, _row in _pending_exclude:
                            _drop_keys.add(_ck)
                            record_figure_feedback(
                                "down", _row,
                                st.session_state.get(f"diag_fb_{_ck}", "") or "Excluded during diagram review",
                            )
                        _upd_status.write("**Step 2** — Removing diagrams from the HLD")
                        st.session_state.ppt_preview_images = [
                            r for r in _shown_rows if (_cap_key(r) or "") not in _drop_keys
                        ]
                        _upd_status.write("**Step 3** — Rebuilding Word and PowerPoint outputs")
                        rebuild_deck_from_state()
                        _upd_status.update(label="HLD updated", state="complete", expanded=False)
                    for _ck, _row in _pending_exclude:
                        st.session_state.pop(f"diag_inc_{_ck}", None)
                    st.rerun()

        if st.session_state.ppt_path or st.session_state.docx_path:
            st.markdown("#### Rate this HLD")
            st.caption(
                "Your rating trains diagram selection: 👍 reinforces this diagram set for similar designs; "
                "👎 records what was wrong so it is avoided next time."
            )
            fb_cols = st.columns([1, 1, 5])
            if fb_cols[0].button("👍 Good output", key="fb_up_btn"):
                record_hld_feedback("up")
                st.session_state.feedback_mode = ""
                st.success("Thanks — this diagram set is now reinforced for similar designs.")
            if fb_cols[1].button("👎 Needs work", key="fb_down_btn"):
                st.session_state.feedback_mode = "down"
            if st.session_state.get("feedback_mode") == "down":
                with st.form("fb_down_form", clear_on_submit=True):
                    fb_reason = st.text_area(
                        "What was unsatisfying, missing, or wrong?",
                        placeholder="e.g. the DMZ diagram shows a single DMZ but we chose double; the networking section lacks port details",
                    )
                    fb_image = st.file_uploader(
                        "Optional: upload the diagram that was wrong or should have been included",
                        type=["png", "jpg", "jpeg"],
                    )
                    fb_submit = st.form_submit_button("Submit feedback")
                if fb_submit:
                    extra = record_hld_feedback(
                        "down",
                        reason=fb_reason,
                        uploaded=fb_image.read() if fb_image else None,
                    )
                    st.session_state.feedback_mode = ""
                    st.success("Recorded — selections and narratives for similar designs will learn from this." + extra)

        # Fragment-local reruns keep the rest of the generated HLD interactive
        # and unchanged while a follow-up question is being processed.
        render_hld_chat_fragment()

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
