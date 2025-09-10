"""Langfuse tracing + cost tracking helpers.

Soft-fails if env/config disabled or keys missing.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Dict, Optional

from langfuse import get_client  # type: ignore
from langfuse.langchain import CallbackHandler  # type: ignore

from src.utils.config_loader import load_config
from src.utils.logger import GLOBAL_LOGGER as log


def init_langfuse() -> None:
    """
    Initialize Langfuse client and log connection status at startup.
    Follows sample code: uses get_client(), logs success/failure.
    """
    try:
        client = get_client()
        if client is None:
            log.error("Langfuse client not available (import or env issue)")
            return
        if hasattr(client, "auth_check") and client.auth_check():
            log.info("Langfuse client is authenticated and ready!")
        else:
            log.error(
                "Langfuse authentication failed. Please check credentials and host."
            )
    except Exception as e:
        log.error(f"Langfuse initialization error: {e}")


def flush_langfuse_events() -> None:
    """
    Flushes Langfuse events and logs if successful.
    """
    try:
        client = get_client()
        if client and hasattr(client, "flush"):
            client.flush()
            log.info("Langfuse events flushed successfully!")
        else:
            log.warning("Langfuse flush not available or client not initialized.")
    except Exception as e:
        log.error(f"Langfuse flush error: {e}")


def get_langchain_callback_handler():
    """
    Returns a new Langfuse CallbackHandler for use in LangChain config callbacks.
    """
    try:
        return CallbackHandler()
    except Exception as e:
        log.error(f"Failed to create Langfuse CallbackHandler: {e}")
        return None


__all__ = [
    "init_langfuse",
    "get_client",
    "CallbackHandler",
    "get_langchain_callback_handler",
    "flush_langfuse_events",
]
