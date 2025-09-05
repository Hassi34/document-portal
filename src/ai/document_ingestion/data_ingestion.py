from __future__ import annotations

"""Ingestion utilities for analysis, comparison, and chat indexing.

Contains:
- FaissManager: idempotent add/load/save logic for FAISS index using a config-driven index name.
- ChatIngestor: builds retrievers from uploaded files, with sessionized directories.
- DocHandler: save/read PDFs for analysis.
- DocumentComparator: utilities for saving, reading, and combining PDFs for comparison.
"""

import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import fitz  # PyMuPDF
from langchain.schema import Document
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.utils.config_loader import get_supported_extensions, load_config
from src.utils.document_ops import (
    concat_for_analysis,
    concat_for_comparison,
    load_documents,
)
from src.utils.exception.custom_exception import DocumentPortalException
from src.utils.file_io import generate_session_id, save_uploaded_files
from src.utils.logger import GLOBAL_LOGGER as log
from src.utils.model_loader import ModelLoader

SUPPORTED_EXTENSIONS = get_supported_extensions()


# FAISS Manager (load-or-create)
class FaissManager:
    """A tiny manager around FAISS vectorstore with idempotent adds.

    Ensures consistent index naming (from config) and tracks ingested rows to
    avoid duplicate additions across runs.
    """

    def __init__(self, index_dir: Path, model_loader: Optional[ModelLoader] = None):
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)

        self.meta_path = self.index_dir / "ingested_meta.json"
        self._meta: Dict[str, Any] = {"rows": {}}  ## this is dict of rows

        if self.meta_path.exists():
            try:
                self._meta = json.loads(self.meta_path.read_text(encoding="utf-8")) or {
                    "rows": {}
                }  # load it if alrady there
            except Exception:
                self._meta = {"rows": {}}  # init the empty one if dones not exists

        self.model_loader = model_loader or ModelLoader()
        # Load index_name from config for consistent save/load
        cfg = load_config()
        self.index_name = (
            cfg.get("ai", {})
            .get("vector_db", {})
            .get("faiss", {})
            .get("index_name", "index")
        )
        self.emb = self.model_loader.load_embeddings()
        self.vs: Optional[FAISS] = None

    def _exists(self) -> bool:
        # Presence check uses configured index_name (default 'index')
        return (self.index_dir / f"{self.index_name}.faiss").exists() and (
            self.index_dir / f"{self.index_name}.pkl"
        ).exists()

    @staticmethod
    def _fingerprint(text: str, md: Dict[str, Any]) -> str:
        src = md.get("source") or md.get("file_path")
        rid = md.get("row_id")
        if src is not None:
            return f"{src}::{'' if rid is None else rid}"
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _save_meta(self) -> None:
        self.meta_path.write_text(
            json.dumps(self._meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add_documents(self, docs: List[Document]) -> int:
        """Add new documents to the FAISS store, skipping those already seen.

        Returns the number of documents actually added.
        """
        if self.vs is None:
            raise RuntimeError(
                "Call load_or_create() before add_documents_idempotent()."
            )

        new_docs: List[Document] = []

        for d in docs:
            key = self._fingerprint(d.page_content, d.metadata or {})
            if key in self._meta["rows"]:
                continue
            self._meta["rows"][key] = True
            new_docs.append(d)

        if new_docs:
            self.vs.add_documents(new_docs)
            self.vs.save_local(str(self.index_dir), index_name=self.index_name)
            self._save_meta()
        return len(new_docs)

    def load_or_create(
        self, texts: Optional[List[str]] = None, metadatas: Optional[List[dict]] = None
    ) -> FAISS:
        ## if we running first time then it will not go in this block
        if self._exists():
            self.vs = FAISS.load_local(
                str(self.index_dir),
                embeddings=self.emb,
                allow_dangerous_deserialization=True,
                index_name=self.index_name,
            )
            # Guard against dimension mismatch when switching embedding models/providers
            try:
                index_dim = getattr(self.vs.index, "d", None)  # type: ignore[attr-defined]
                probe = self.emb.embed_query("dimension probe")
                emb_dim = len(probe) if probe is not None else None
                if (
                    isinstance(index_dim, int)
                    and isinstance(emb_dim, int)
                    and index_dim != emb_dim
                ):
                    log.warning(
                        "Embedding dimension mismatch detected; resetting FAISS index",
                        index_dim=index_dim,
                        embedding_dim=emb_dim,
                        index_path=str(self.index_dir),
                        index_name=self.index_name,
                    )
                    # Remove old incompatible index files and meta so we can rebuild
                    try:
                        (self.index_dir / f"{self.index_name}.faiss").unlink(
                            missing_ok=True
                        )  # type: ignore[arg-type]
                        (self.index_dir / f"{self.index_name}.pkl").unlink(
                            missing_ok=True
                        )  # type: ignore[arg-type]
                    except Exception:
                        # Best-effort cleanup; continue to rebuild
                        pass
                    # Reset meta and fall through to create branch
                    self._meta = {"rows": {}}
                    self.vs = None  # type: ignore[assignment]
                else:
                    return self.vs
            except Exception:
                # If we cannot determine dims, proceed with loaded index
                return self.vs

        if not texts:
            raise DocumentPortalException(
                "No existing FAISS index and no data to create one", sys
            )
        self.vs = FAISS.from_texts(
            texts=texts, embedding=self.emb, metadatas=metadatas or []
        )
        self.vs.save_local(str(self.index_dir), index_name=self.index_name)
        return self.vs


class ChatIngestor:
    def __init__(
        self,
        temp_base: Optional[str] = None,
        faiss_base: Optional[str] = None,
        use_session_dirs: bool = True,
        session_id: Optional[str] = None,
    ):
        try:
            self.model_loader = ModelLoader()

            self.use_session = use_session_dirs
            self.session_id = session_id or generate_session_id()

            # Load defaults from config when not provided
            cfg = load_config()
            default_temp = (
                cfg.get("data", {})
                .get("storage", {})
                .get("document_chat", "data/document_chat")
            )
            default_faiss = (
                cfg.get("ai", {})
                .get("vector_db", {})
                .get("faiss", {})
                .get("index_path", "data/faiss_index")
            )
            resolved_temp = temp_base or default_temp
            resolved_faiss = faiss_base or default_faiss

            self.temp_base = Path(resolved_temp)
            self.temp_base.mkdir(parents=True, exist_ok=True)
            self.faiss_base = Path(resolved_faiss)
            self.faiss_base.mkdir(parents=True, exist_ok=True)

            self.temp_dir = self._resolve_dir(self.temp_base)
            self.faiss_dir = self._resolve_dir(self.faiss_base)

            log.info(
                "ChatIngestor initialized",
                session_id=self.session_id,
                temp_dir=str(self.temp_dir),
                faiss_dir=str(self.faiss_dir),
                sessionized=self.use_session,
            )
        except Exception as e:
            log.error("Failed to initialize ChatIngestor", error=str(e))
            raise DocumentPortalException(
                "Initialization error in ChatIngestor", e
            ) from e

    def _resolve_dir(self, base: Path) -> Path:
        if self.use_session:
            d = base / self.session_id  # e.g. "faiss_index/abc123"
            d.mkdir(parents=True, exist_ok=True)  # creates dir if not exists
            return d
        return base  # fallback: "faiss_index/"

    def _split(
        self, docs: List[Document], chunk_size: int = 1000, chunk_overlap: int = 200
    ) -> List[Document]:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        chunks = splitter.split_documents(docs)
        log.info(
            "Documents split",
            chunks=len(chunks),
            chunk_size=chunk_size,
            overlap=chunk_overlap,
        )
        return chunks

    def built_retriver(
        self,
        uploaded_files: Iterable,
        *,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        k: int = 5,
    ):
        try:
            # Resolve defaults from config if caller used default values
            cfg = load_config()
            if chunk_size == 1000:
                chunk_size = (
                    cfg.get("ai", {}).get("retriever", {}).get("chunk_size", 1000)
                )
            if chunk_overlap == 200:
                chunk_overlap = (
                    cfg.get("ai", {}).get("retriever", {}).get("chunk_overlap", 200)
                )
            if k == 5:
                k = cfg.get("ai", {}).get("retriever", {}).get("top_k", 10)
            paths = save_uploaded_files(uploaded_files, self.temp_dir)
            docs = load_documents(paths)
            if not docs:
                raise ValueError("No valid documents loaded")

            chunks = self._split(
                docs, chunk_size=chunk_size, chunk_overlap=chunk_overlap
            )

            ## FAISS manager very very important class for the docchat
            fm = FaissManager(self.faiss_dir, self.model_loader)

            texts = [c.page_content for c in chunks]
            metas = [c.metadata for c in chunks]

            try:
                vs = fm.load_or_create(texts=texts, metadatas=metas)
            except Exception:
                vs = fm.load_or_create(texts=texts, metadatas=metas)

            added = fm.add_documents(chunks)
            log.info("FAISS index updated", added=added, index=str(self.faiss_dir))

            return vs.as_retriever(search_type="similarity", search_kwargs={"k": k})

        except Exception as e:
            log.exception("Failed to build retriever")
            raise DocumentPortalException("Failed to build retriever", e) from e


class DocHandler:
    """PDF save + read (page-wise) for analysis."""

    def __init__(
        self, data_dir: Optional[str] = None, session_id: Optional[str] = None
    ):
        config = load_config()
        default_data_dir = (
            config.get("data", {})
            .get("storage", {})
            .get("document_analysis", "data/document_analysis")
        )
        self.data_dir = data_dir or os.path.join(os.getcwd(), default_data_dir)
        self.session_id = session_id or generate_session_id("session")
        self.session_path = os.path.join(self.data_dir, self.session_id)
        os.makedirs(self.session_path, exist_ok=True)
        log.info(
            "DocHandler initialized",
            session_id=self.session_id,
            session_path=self.session_path,
        )

    def save_pdf(self, uploaded_file) -> str:
        try:
            filename = os.path.basename(uploaded_file.name)
            if not filename.lower().endswith(".pdf"):
                raise ValueError("Invalid file type. Only PDFs are allowed.")
            save_path = os.path.join(self.session_path, filename)
            with open(save_path, "wb") as f:
                if hasattr(uploaded_file, "read"):
                    f.write(uploaded_file.read())
                else:
                    f.write(uploaded_file.getbuffer())
            log.info(
                "PDF saved successfully",
                file=filename,
                save_path=save_path,
                session_id=self.session_id,
            )
            return save_path
        except Exception as e:
            log.error("Failed to save PDF", error=str(e), session_id=self.session_id)
            raise DocumentPortalException(f"Failed to save PDF: {str(e)}", e) from e

    def read_pdf(self, pdf_path: str) -> str:
        try:
            text_chunks = []
            with fitz.open(pdf_path) as doc:
                for page_num in range(doc.page_count):
                    page = doc.load_page(page_num)
                    text_chunks.append(
                        f"\n--- Page {page_num + 1} ---\n{page.get_text()}"
                    )  # type: ignore
            text = "\n".join(text_chunks)
            log.info(
                "PDF read successfully",
                pdf_path=pdf_path,
                session_id=self.session_id,
                pages=len(text_chunks),
            )
            return text
        except Exception as e:
            log.error(
                "Failed to read PDF",
                error=str(e),
                pdf_path=pdf_path,
                session_id=self.session_id,
            )
            raise DocumentPortalException(
                f"Could not process PDF: {pdf_path}", e
            ) from e


class DocumentComparator:
    """
    Save, read & combine PDFs for comparison with session-based versioning.
    """

    def __init__(
        self, base_dir: Optional[str] = None, session_id: Optional[str] = None
    ):
        config = load_config()
        default_base_dir = (
            config.get("data", {})
            .get("storage", {})
            .get("document_compare", "data/document_compare")
        )
        resolved_base = base_dir or os.path.join(os.getcwd(), default_base_dir)
        self.base_dir = Path(resolved_base)
        self.session_id = session_id or generate_session_id()
        self.session_path = self.base_dir / self.session_id
        self.session_path.mkdir(parents=True, exist_ok=True)
        log.info("DocumentComparator initialized", session_path=str(self.session_path))

    def save_uploaded_files(self, reference_file, actual_file):
        try:
            ref_path = self.session_path / reference_file.name
            act_path = self.session_path / actual_file.name
            for fobj, out in ((reference_file, ref_path), (actual_file, act_path)):
                if not fobj.name.lower().endswith(".pdf"):
                    raise ValueError("Only PDF files are allowed.")
                with open(out, "wb") as f:
                    if hasattr(fobj, "read"):
                        f.write(fobj.read())
                    else:
                        f.write(fobj.getbuffer())
            log.info(
                "Files saved",
                reference=str(ref_path),
                actual=str(act_path),
                session=self.session_id,
            )
            return ref_path, act_path
        except Exception as e:
            log.error("Error saving PDF files", error=str(e), session=self.session_id)
            raise DocumentPortalException("Error saving files", e) from e

    def read_pdf(self, pdf_path: Path) -> str:
        try:
            with fitz.open(pdf_path) as doc:
                if doc.is_encrypted:
                    raise ValueError(f"PDF is encrypted: {pdf_path.name}")
                parts = []
                for page_num in range(doc.page_count):
                    page = doc.load_page(page_num)
                    text = page.get_text()  # type: ignore
                    if text.strip():
                        parts.append(f"\n --- Page {page_num + 1} --- \n{text}")
            log.info("PDF read successfully", file=str(pdf_path), pages=len(parts))
            return "\n".join(parts)
        except Exception as e:
            log.error("Error reading PDF", file=str(pdf_path), error=str(e))
            raise DocumentPortalException("Error reading PDF", e) from e

    def combine_documents(self) -> str:
        try:
            doc_parts = []
            for file in sorted(self.session_path.iterdir()):
                if file.is_file() and file.suffix.lower() == ".pdf":
                    content = self.read_pdf(file)
                    doc_parts.append(f"Document: {file.name}\n{content}")
            combined_text = "\n\n".join(doc_parts)
            log.info(
                "Documents combined", count=len(doc_parts), session=self.session_id
            )
            return combined_text
        except Exception as e:
            log.error(
                "Error combining documents", error=str(e), session=self.session_id
            )
            raise DocumentPortalException("Error combining documents", e) from e

    def clean_old_sessions(self, keep_latest: int = 3):
        try:
            sessions = sorted(
                [f for f in self.base_dir.iterdir() if f.is_dir()], reverse=True
            )
            for folder in sessions[keep_latest:]:
                shutil.rmtree(folder, ignore_errors=True)
                log.info("Old session folder deleted", path=str(folder))
        except Exception as e:
            log.error("Error cleaning old sessions", error=str(e))
            raise DocumentPortalException("Error cleaning old sessions", e) from e
