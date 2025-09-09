"""Environment bootstrap: load .env (local) and expand API_KEYS JSON bundle.

This ensures required AWS variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION)
are present in os.environ early at process start.
"""

from __future__ import annotations

import json
import os
from typing import Iterable

from src.utils.logger import GLOBAL_LOGGER as log


def _set_if_missing(key: str, value: str | None) -> None:
    if value and key not in os.environ:
        os.environ[key] = value


def bootstrap_env(
    required: Iterable[str] | None = None, api_keys_env: str = "API_KEYS"
) -> None:
    """Load local .env (if not production) and expand JSON secret bundle.

    required: list of env var names we expect (will log if still missing).
    api_keys_env: name of env holding JSON object of key/value pairs.
    """
    env = os.getenv("ENV", "local").lower()

    # Local .env for developer convenience
    if env != "production":
        try:
            from dotenv import load_dotenv  # type: ignore

            if load_dotenv():
                log.info("Loaded .env file for local environment")
        except Exception:
            pass

    # Expand JSON bundle (if present). This supports storing AWS vars in Secrets Manager
    raw = os.getenv(api_keys_env)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, (str | int | float)):
                        _set_if_missing(k, str(v))
                log.info("Expanded API_KEYS JSON bundle", count=len(data))
        except Exception as e:  # noqa: BLE001
            log.warning("Failed parsing API_KEYS JSON", error=str(e))

    # Ensure required list present
    missing = []
    if required:
        for k in required:
            if not os.getenv(k):
                missing.append(k)
    if missing:
        log.warning("Missing expected environment variables", missing=missing)
    else:
        if required:
            log.info("All required environment variables present", keys=list(required))
