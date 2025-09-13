"""Centralized Langfuse observed service functions.

Lightweight wrapper functions decorated with @observe / @observe(as_type="generation").
Uses the new Langfuse client + CallbackHandler pattern (no langfuse_context).
Routers should call these helpers rather than embedding inline tracing logic.
"""

from __future__ import annotations

import os
from typing import Any, Sequence

from langfuse import get_client, observe  # type: ignore
from langfuse.langchain import CallbackHandler  # type: ignore

from src.utils.logger import GLOBAL_LOGGER as log
from src.utils.token_counter import count_tokens


# ---------------------------- Embeddings ------------------------------------
def record_embedding_batch(
    model: str, provider: str, texts: Sequence[str], session_id: str | None = None
) -> int:
    """Record an embedding generation batch.

    We don't perform the embedding here; caller does that. This function just
    updates the observation usage metadata following reference style.
    Returns approximate token count used for embeddings (simple heuristic).
    """
    tokens = 0
    try:
        for t in texts:
            tokens += count_tokens(provider, model, t)
    except Exception:
        # fallback minimal
        total_chars = sum(len(t) for t in texts)
        tokens = max(1, total_chars // 4) if total_chars else 0
    # Note: Usage attachment now handled by provider integration; logging here for visibility
    log.info(
        "Embedding batch usage (approx)",
        extra={
            "provider": provider,
            "model": model,
            "tokens": tokens,
            "batch_size": len(texts),
            "session_id": session_id,
        },
    )
    return tokens


# ---------------------------- Chat / RAG ------------------------------------
def record_chat_generation(
    model: str,
    provider: str,
    prompt: str,
    response_text: str,
    session_id: str | None = None,
):
    """Attach usage metadata for a chat generation already executed."""
    prompt_tokens = max(1, len(prompt) // 4)
    completion_tokens = max(1, len(response_text) // 4)
    log.info(
        "Chat generation usage (approx)",
        extra={
            "provider": provider,
            "model": model,
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "session_id": session_id,
        },
    )
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}


# ---------------------------- Analysis --------------------------------------
def record_analysis(
    model: str,
    provider: str,
    input_snippet: str,
    output_snippet: str,
    session_id: str | None = None,
):
    """Add usage for an analysis style generation (structured parsing)."""
    try:
        in_tokens = count_tokens(provider, model, input_snippet)
        out_tokens = count_tokens(provider, model, output_snippet)
    except Exception:
        in_tokens = max(1, len(input_snippet) // 4)
        out_tokens = max(1, len(output_snippet) // 4)
    log.info(
        "Analysis usage (approx)",
        extra={
            "provider": provider,
            "model": model,
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "session_id": session_id,
        },
    )
    return {"input_tokens": in_tokens, "output_tokens": out_tokens}


# ---------------------------- Comparison ------------------------------------
def record_comparison(
    model: str,
    provider: str,
    left: str,
    right: str,
    result_text: str,
    session_id: str | None = None,
):
    try:
        lt = count_tokens(provider, model, left)
        rt = count_tokens(provider, model, right)
        out = count_tokens(provider, model, result_text)
    except Exception:
        lt = max(1, len(left) // 4)
        rt = max(1, len(right) // 4)
        out = max(1, len(result_text) // 4)
    log.info(
        "Comparison usage (approx)",
        extra={
            "provider": provider,
            "model": model,
            "input_tokens": lt + rt,
            "output_tokens": out,
            "session_id": session_id,
        },
    )
    return {"input_tokens": lt + rt, "output_tokens": out}


# ---------------------------- Chat RAG run ----------------------------------
@observe(as_type="generation")
def run_chat_rag(
    rag: Any, question: str, session_id: str | None = None, k: int | None = None
) -> Any:
    """
    Execute the RAG chain within a Langfuse trace using a bound handler,
    mirroring the reference pattern.
    """
    # Attach Langfuse handler using new API
    client = get_client()  # ensure client initialized
    # Pre-update with input/model to let Langfuse infer costs later
    try:
        model_name = getattr(rag.llm, "_dp_model_name", None) or "unknown-model"
    except Exception:
        model_name = "unknown-model"
    try:
        if client and hasattr(client, "update_current_generation"):
            client.update_current_generation(
                input=question,
                model=model_name,
                metadata={"session_id": session_id, "k": k},
            )
    except Exception:
        log.warning("FAILED TO UPDATE CURRENT GENERATION INPUT/MODEL")
    try:
        handler = CallbackHandler()
    except Exception:
        handler = None
    if not handler:
        log.warning("NO LANGFUSE HANDLER AVAILABLE; RUNNING WITHOUT CALLBACKS")
    result = rag.invoke(
        question,
        chat_history=[],
        callbacks=[handler] if handler else None,
    )
    # Post-update with usage_details so Langfuse shows tokens and infers cost
    try:
        provider = os.getenv("CHAT_PROVIDER", os.getenv("LLM_PROVIDER", "openai"))
        in_toks = count_tokens(provider, model_name, question)
        out_toks = count_tokens(provider, model_name, str(result))
        if client and hasattr(client, "update_current_generation"):
            client.update_current_generation(
                usage_details={
                    "input": in_toks,
                    "output": out_toks,
                }
            )
    except Exception:
        log.warning("FAILED TO UPDATE CURRENT GENERATION USAGE DETAILS")
    return result
