import os
from typing import Any, List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from llm_observability.src.tracing import (
    record_chat_generation,
    record_embedding_batch,
    run_chat_rag,
)
from src.ai.document_chat.retrieval import ConversationalRAG
from src.ai.document_ingestion.data_ingestion import ChatIngestor
from src.schemas.api.input import ChatIndexParams, ChatQueryParams
from src.schemas.api.ouput import ChatIndexResponse, ChatQueryResponse
from src.utils.config_loader import load_config
from src.utils.document_ops import FastAPIFileAdapter
from src.utils.logger import GLOBAL_LOGGER as log

# Note: Langfuse callbacks are handled inside services.tracing; no direct context use here.

_cfg = load_config()
FAISS_BASE = (
    _cfg.get("ai", {})
    .get("vector_db", {})
    .get("faiss", {})
    .get("index_path", "data/faiss_index")
)
UPLOAD_BASE = (
    _cfg.get("data", {}).get("storage", {}).get("document_chat", "data/document_chat")
)
FAISS_INDEX_NAME = (
    _cfg.get("ai", {}).get("vector_db", {}).get("faiss", {}).get("index_name", "index")
)
RETRIEVER_TOP_K = _cfg.get("ai", {}).get("retriever", {}).get("top_k", 10)
RETRIEVER_SEARCH_TYPE = (
    _cfg.get("ai", {}).get("retriever", {}).get("search_type", "similarity")
)
RETRIEVER_CHUNK_SIZE = _cfg.get("ai", {}).get("retriever", {}).get("chunk_size", 1000)
RETRIEVER_CHUNK_OVERLAP = (
    _cfg.get("ai", {}).get("retriever", {}).get("chunk_overlap", 200)
)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/index", response_model=ChatIndexResponse)
async def chat_build_index(
    files: List[UploadFile] = File(...),
    params: ChatIndexParams = Depends(ChatIndexParams.as_form),
) -> Any:
    try:
        log.info(
            f"Indexing chat session. Session ID: {params.session_id}, Files: {[f.filename for f in files]}"
        )
        wrapped = [FastAPIFileAdapter(f) for f in files]
        ci = ChatIngestor(
            temp_base=UPLOAD_BASE,
            faiss_base=FAISS_BASE,
            use_session_dirs=params.use_session_dirs,
            session_id=params.session_id or None,
        )
        ci.built_retriver(
            wrapped,
            chunk_size=params.chunk_size,
            chunk_overlap=params.chunk_overlap,
            k=params.k,
        )
        # Embedding usage recording via observed service helper
        try:
            provider = os.getenv("EMBEDDING_PROVIDER", "openai")
            if provider == "azure":
                provider = "azure-openai"
            emb_cfg = _cfg.get("ai", {}).get("embedding_model", {}).get(provider, {})
            emb_model = emb_cfg.get("model_name", "embedding-model")
            texts: list[str] = []
            for f in wrapped:
                try:
                    texts.append(f.read_text())
                except Exception:
                    continue
            record_embedding_batch(emb_model, provider, texts, session_id=ci.session_id)
        except Exception:  # pragma: no cover
            pass
        log.info(f"Index created successfully for session: {ci.session_id}")
        return {
            "session_id": ci.session_id,
            "k": params.k,
            "use_session_dirs": params.use_session_dirs,
        }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Chat index building failed")
        raise HTTPException(status_code=500, detail=f"Indexing failed: {e}")


@router.post("/query", response_model=ChatQueryResponse)
async def chat_query(
    params: ChatQueryParams = Depends(ChatQueryParams.as_form),
) -> Any:
    try:
        log.info(
            f"Received chat query: '{params.question}' | session: {params.session_id}"
        )
        if params.use_session_dirs and not params.session_id:
            raise HTTPException(
                status_code=400,
                detail="session_id is required when use_session_dirs=True",
            )

        index_dir = (
            os.path.join(FAISS_BASE, params.session_id)
            if params.use_session_dirs
            else FAISS_BASE
        )  # type: ignore
        if not os.path.isdir(index_dir):
            raise HTTPException(
                status_code=404, detail=f"FAISS index not found at: {index_dir}"
            )

        rag = ConversationalRAG(session_id=params.session_id)
        rag.load_retriever_from_faiss(
            index_dir,
            k=params.k,
            index_name=FAISS_INDEX_NAME,
            search_type=RETRIEVER_SEARCH_TYPE,
        )
        # Run under an observed helper which attaches the Langfuse handler
        response = run_chat_rag(
            rag, params.question, session_id=params.session_id, k=params.k
        )
        # Record usage via observed helper (no deprecated langfuse_context)
        try:
            provider = os.getenv("CHAT_PROVIDER", os.getenv("LLM_PROVIDER", "openai"))
            model_name = getattr(rag.llm, "_dp_model_name", None) or "unknown-model"
            record_chat_generation(
                model=model_name,
                provider=provider,
                prompt=params.question,
                response_text=str(response),
                session_id=params.session_id,
            )
        except Exception:
            pass
        log.info("Chat query handled successfully.")

        return {
            "answer": response,
            "session_id": params.session_id,
            "k": params.k,
            "engine": "LCEL-RAG",
        }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Chat query failed")
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")
