from __future__ import annotations
"""Document helpers: loaders, concatenators, and adapters for FastAPI files."""

from pathlib import Path
from typing import Iterable, List

from fastapi import UploadFile
from langchain.schema import Document
from langchain_community.document_loaders import (
    PyPDFLoader,
    Docx2txtLoader,
    TextLoader,
)

from src.utils.logger import GLOBAL_LOGGER as log
from src.utils.exception.custom_exception import DocumentPortalException
from src.utils.config_loader import get_supported_extensions

SUPPORTED_EXTENSIONS = get_supported_extensions()


def load_documents(paths: Iterable[Path]) -> List[Document]:
    """Load documents from filesystem using appropriate loaders.

    Args:
        paths: Iterable of file paths to load.

    Returns:
        A list of LangChain Document objects.
    """
    docs: List[Document] = []
    try:
        for p in paths:
            ext = p.suffix.lower()
            if ext == ".pdf":
                loader = PyPDFLoader(str(p))
            elif ext == ".docx":
                loader = Docx2txtLoader(str(p))
            elif ext == ".txt":
                loader = TextLoader(str(p), encoding="utf-8")
            else:
                log.warning("Unsupported extension skipped", path=str(p))
                continue
            docs.extend(loader.load())
        log.info("Documents loaded", count=len(docs))
        return docs
    except Exception as e:
        log.error("Failed loading documents", error=str(e))
        raise DocumentPortalException("Error loading documents", e) from e


def concat_for_analysis(docs: List[Document]) -> str:
    """Join documents with section markers containing their source.

    Args:
        docs: List of documents to concatenate.

    Returns:
        Single string containing all page contents.
    """
    parts: List[str] = []
    for d in docs:
        src = d.metadata.get("source") or d.metadata.get("file_path") or "unknown"
        parts.append(f"\n--- SOURCE: {src} ---\n{d.page_content}")
    return "\n".join(parts)


def concat_for_comparison(ref_docs: List[Document], act_docs: List[Document]) -> str:
    """Create a side-by-side like text representation for comparison prompts."""
    left = concat_for_analysis(ref_docs)
    right = concat_for_analysis(act_docs)
    return f"<<REFERENCE_DOCUMENTS>>\n{left}\n\n<<ACTUAL_DOCUMENTS>>\n{right}"


# ---------- Helpers ----------
class FastAPIFileAdapter:
    """Adapt FastAPI UploadFile to a minimal interface used by our I/O helpers.

    Provides a ``name`` attribute and a ``getbuffer()`` method to read bytes.
    """

    def __init__(self, uf: UploadFile):
        self._uf = uf
        self.name = uf.filename

    def getbuffer(self) -> bytes:
        self._uf.file.seek(0)
        return self._uf.file.read()


def read_pdf_via_handler(handler, path: str) -> str:
    """Read PDF using a handler that may expose different method names.

    Supports either ``read_pdf(path)`` or a generic ``read_(path)``.
    """
    if hasattr(handler, "read_pdf"):
        return handler.read_pdf(path)  # type: ignore[attr-defined]
    if hasattr(handler, "read_"):
        return handler.read_(path)  # type: ignore[attr-defined]
    raise RuntimeError("DocHandler has neither read_pdf nor read_ method.")