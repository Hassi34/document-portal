from __future__ import annotations

"""File I/O helpers for saving uploads and generating session IDs."""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List
from zoneinfo import ZoneInfo

from src.utils.config_loader import get_supported_extensions
from src.utils.exception.custom_exception import DocumentPortalException
from src.utils.logger import GLOBAL_LOGGER as log

SUPPORTED_EXTENSIONS = get_supported_extensions()


# ----------------------------- #
# Helpers (file I/O + loading)  #
# ----------------------------- #
def generate_session_id(prefix: str = "session") -> str:
    """Generate a sortable, timezone-aware session ID.

    Args:
        prefix: A string prefix for the session ID.

    Returns:
        A unique session identifier string.
    """
    tz = ZoneInfo("America/Los_Angeles")
    return (
        f"{prefix}_{datetime.now(tz).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    )


def save_uploaded_files(uploaded_files: Iterable[Any], target_dir: Path) -> List[Path]:
    """Persist uploaded files to disk and return their local paths.

    This accepts objects that expose either a ``read()`` method (like FastAPI's
    UploadFile) or a ``getbuffer()`` method (as in our adapters).

    Args:
        uploaded_files: An iterable of uploaded file-like objects.
        target_dir: Directory to save files into. Created if missing.

    Returns:
        A list of saved file paths.
    """
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        saved: List[Path] = []
        for uf in uploaded_files:
            name = getattr(uf, "name", "file")
            ext = Path(name).suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                log.warning("Unsupported file skipped", filename=name)
                continue
            # Save using a random name to avoid collisions while keeping extension
            fname = f"{uuid.uuid4().hex[:8]}{ext}"
            out = target_dir / fname
            with open(out, "wb") as f:
                if hasattr(uf, "read"):
                    f.write(uf.read())
                else:
                    f.write(uf.getbuffer())  # fallback
            saved.append(out)
            log.info("File saved for ingestion", uploaded=name, saved_as=str(out))
        return saved
    except Exception as e:
        log.error("Failed to save uploaded files", error=str(e), dir=str(target_dir))
        raise DocumentPortalException("Failed to save uploaded files", e) from e
