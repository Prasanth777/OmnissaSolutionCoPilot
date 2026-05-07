from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class PromptBundle:
    system_prompt: str
    user_prompt: str


def compress_context_with_llmlingua(context: str, target_tokens: int = 1800) -> str:
    if os.getenv("ENABLE_LLM_LINGUA", "0") != "1":
        return context
    try:
        from llmlingua import PromptCompressor
    except Exception:
        return context

    try:
        compressor = PromptCompressor()
        compressed = compressor.compress_prompt(
            context,
            target_token=target_tokens,
            force_context_ids=[0],
        )
        if isinstance(compressed, dict):
            return str(compressed.get("compressed_prompt", context))
        return str(compressed)
    except Exception:
        return context


def optimize_prompt_with_dspy(system_prompt: str, user_prompt: str) -> PromptBundle:
    if os.getenv("ENABLE_DSPY_OPTIMIZATION", "0") != "1":
        return PromptBundle(system_prompt=system_prompt, user_prompt=user_prompt)
    try:
        import dspy
    except Exception:
        return PromptBundle(system_prompt=system_prompt, user_prompt=user_prompt)

    # Lightweight usage of DSPy as a prompt-shaping layer without requiring compile-time datasets.
    class PromptPolisher(dspy.Signature):
        """Rewrite prompts to reduce verbosity while preserving constraints."""

        system_prompt = dspy.InputField()
        user_prompt = dspy.InputField()
        polished_system = dspy.OutputField()
        polished_user = dspy.OutputField()

    try:
        polisher = dspy.Predict(PromptPolisher)
        output = polisher(system_prompt=system_prompt, user_prompt=user_prompt)
        polished_system = str(getattr(output, "polished_system", "")).strip() or system_prompt
        polished_user = str(getattr(output, "polished_user", "")).strip() or user_prompt
        return PromptBundle(system_prompt=polished_system, user_prompt=polished_user)
    except Exception:
        return PromptBundle(system_prompt=system_prompt, user_prompt=user_prompt)
