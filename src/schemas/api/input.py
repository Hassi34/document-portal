from __future__ import annotations

"""Pydantic input models for API endpoints."""


from fastapi import Form
from pydantic import BaseModel, Field, model_validator

from src.utils.config_loader import load_config

_cfg = load_config()
_retriever = _cfg.get("ai", {}).get("retriever", {})
_DEF_TOP_K = int(_retriever.get("top_k", 10))
_DEF_CHUNK_SIZE = int(_retriever.get("chunk_size", 1000))
_DEF_CHUNK_OVERLAP = int(_retriever.get("chunk_overlap", 200))


class AnalyzeParams(BaseModel):
    """No additional parameters; endpoint expects a single file upload."""

    pass


class CompareParams(BaseModel):
    """No additional parameters; endpoint expects two file uploads."""

    pass


class ChatIndexParams(BaseModel):
    session_id: str | None = Field(
        default=None,
        description="Session identifier when using per-session directories",
    )
    use_session_dirs: bool = Field(
        default=True, description="Whether to create/use per-session subdirectories"
    )
    chunk_size: int = Field(
        default=_DEF_CHUNK_SIZE, ge=1, description="Text splitter chunk size"
    )
    chunk_overlap: int = Field(
        default=_DEF_CHUNK_OVERLAP, ge=0, description="Text splitter chunk overlap"
    )
    k: int = Field(default=_DEF_TOP_K, ge=1, description="Retriever top-k")

    @model_validator(mode="after")
    def _validate_chunks(self) -> "ChatIndexParams":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        return self

    @classmethod
    def as_form(
        cls,
        session_id: str | None = Form(None),
        use_session_dirs: bool = Form(True),
        chunk_size: int = Form(_DEF_CHUNK_SIZE),
        chunk_overlap: int = Form(_DEF_CHUNK_OVERLAP),
        k: int = Form(_DEF_TOP_K),
    ) -> "ChatIndexParams":
        return cls(
            session_id=session_id,
            use_session_dirs=use_session_dirs,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            k=k,
        )


class ChatQueryParams(BaseModel):
    question: str = Field(description="User question to ask the RAG system")
    session_id: str | None = Field(
        default=None,
        description="Session identifier when using per-session directories",
    )
    use_session_dirs: bool = Field(
        default=True, description="Whether the index is session-scoped"
    )
    k: int = Field(default=_DEF_TOP_K, ge=1, description="Retriever top-k")

    @classmethod
    def as_form(
        cls,
        question: str = Form(...),
        session_id: str | None = Form(None),
        use_session_dirs: bool = Form(True),
        k: int = Form(_DEF_TOP_K),
    ) -> "ChatQueryParams":
        return cls(
            question=question,
            session_id=session_id,
            use_session_dirs=use_session_dirs,
            k=k,
        )
