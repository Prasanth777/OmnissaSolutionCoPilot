from __future__ import annotations

from dataclasses import dataclass

from .azure_foundry import AzureFoundryClient, RetrievedChunk
from .guardrails import ensure_grounded_answer, is_safe_user_input
from .optimization import compress_context_with_llmlingua, optimize_prompt_with_dspy
from .rag import TechZoneRagStore


@dataclass(frozen=True)
class AgentAnswer:
    answer: str
    citations: list[str]
    used_chunks: list[RetrievedChunk]
    blocked: bool = False
    blocked_reason: str = ""


class RetrievalAgent:
    def __init__(self, rag: TechZoneRagStore) -> None:
        self.rag = rag

    def run(self, query: str, top_k: int = 8) -> list[RetrievedChunk]:
        return self.rag.search(query, top_k=top_k)


class SolutionAgent:
    def __init__(self, foundry: AzureFoundryClient) -> None:
        self.foundry = foundry

    def run(self, query: str, chunks: list[RetrievedChunk]) -> str:
        context_lines = []
        for item in chunks:
            context_lines.append(f"[SOURCE] {item.url}\n[TITLE] {item.title}\n[CONTENT] {item.content}")
        context = "\n\n".join(context_lines)
        compressed_context = compress_context_with_llmlingua(context)

        system_prompt = (
            "You are a solution architecture assistant for Omnissa. "
            "Use only provided context from techzone.omnissa.com. "
            "If context is insufficient, state that clearly and ask for a narrower query. "
            "Return plain text only (no markdown headings, bullets, or special formatting). "
            "Keep the response concise and architecture-focused."
        )
        user_prompt = (
            f"Question:\n{query}\n\n"
            f"Context from Tech Zone RAG:\n{compressed_context}\n\n"
            "Answer for customer-facing solution architects. Keep it practical, implementation-aware, and plain text."
        )
        optimized = optimize_prompt_with_dspy(system_prompt, user_prompt)
        return self.foundry.chat(optimized.system_prompt, optimized.user_prompt, temperature=0.1)


class GuardrailAgent:
    def run(self, user_text: str, answer: str, citations: list[str]) -> tuple[bool, str]:
        input_check = is_safe_user_input(user_text)
        if not input_check.allowed:
            return False, input_check.reason
        output_check = ensure_grounded_answer(answer, citations)
        if not output_check.allowed:
            return False, output_check.reason
        return True, ""


class AgenticRagOrchestrator:
    def __init__(self, retrieval: RetrievalAgent, solution: SolutionAgent, guardrail: GuardrailAgent) -> None:
        self.retrieval = retrieval
        self.solution = solution
        self.guardrail = guardrail

    def answer(self, query: str) -> AgentAnswer:
        chunks = self.retrieval.run(query=query, top_k=8)
        citations = list(dict.fromkeys(item.url for item in chunks if item.url))
        if not chunks:
            return AgentAnswer(
                answer="I could not find relevant content in the Tech Zone RAG index for this request.",
                citations=[],
                used_chunks=[],
                blocked=True,
                blocked_reason="No RAG matches.",
            )

        answer = self.solution.run(query=query, chunks=chunks)
        allowed, reason = self.guardrail.run(user_text=query, answer=answer, citations=citations)
        if not allowed:
            return AgentAnswer(answer="", citations=citations, used_chunks=chunks, blocked=True, blocked_reason=reason)
        return AgentAnswer(answer=answer, citations=citations, used_chunks=chunks)
