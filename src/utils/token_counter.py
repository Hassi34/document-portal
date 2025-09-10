"""Token counting utilities with provider-aware selection.

Strategy:
- Prefer provider-reported usage (handled elsewhere by Langfuse handler).
- When manual counting needed:
  - OpenAI/Azure OpenAI: use tiktoken if available (model-aware encoding).
  - Otherwise: fallback to len(text)//4 heuristic.

Dependencies are optional; this module soft-fails to heuristic when missing.
"""

from __future__ import annotations

from typing import Callable


def _openai_encoding_for_model(model: str) -> str:
    name = (model or "").lower()
    # o-series and 4o generally use o200k_base
    if "gpt-4o" in name or "gpt-4.1" in name or "gpt-4.1" in name or "o1" in name:
        return "o200k_base"
    # embeddings (text-embedding-3-*) use cl100k_base
    if "text-embedding-3" in name or "text-embedding-ada" in name:
        return "cl100k_base"
    # default GPT-3.5/4 tokenizer
    return "cl100k_base"


def _count_tokens_tiktoken(model: str, text: str) -> int | None:
    try:
        import tiktoken  # type: ignore

        encoding_name = _openai_encoding_for_model(model)
        enc = tiktoken.get_encoding(encoding_name)
        return len(enc.encode(text or ""))
    except Exception:
        return None


def count_tokens(provider: str, model: str, text: str) -> int:
    """Count tokens for a given provider/model/text.

    Currently robust for OpenAI/Azure via tiktoken; falls back to heuristic otherwise.
    """
    prov = (provider or "").lower()
    # Treat azure-openai like openai for tokenization purposes
    if prov in {"openai", "azure-openai", "azure"}:
        tok = _count_tokens_tiktoken(model, text)
        if tok is not None:
            return tok
    # TODO: Optionally add transformers-based tokenizers for Llama-family if needed.
    # Fallback heuristic
    s = text or ""
    return max(1, len(s) // 4) if s else 0
