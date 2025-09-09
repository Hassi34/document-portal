from __future__ import annotations

import json
import os
from typing import Iterable

from .logging import configure_logging, get_logger

log = get_logger("env")


def _set_missing(k: str, v: str | None) -> None:
    if v and k not in os.environ:
        os.environ[k] = v


def load_env(
    required: Iterable[str] | None = None, bundle_env: str = "API_KEYS"
) -> None:
    if os.getenv("ENV", "local").lower() != "production":
        dotenv_loaded = False
        try:  # pragma: no cover
            from dotenv import load_dotenv  # type: ignore

            load_dotenv()
            dotenv_loaded = True
        except Exception:
            pass
        # Fallback minimal parser if python-dotenv not working
        if not dotenv_loaded:
            env_path = os.getenv("BACKUP_DOTENV_PATH", ".env")
            if os.path.exists(env_path):
                try:
                    with open(env_path) as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#") or "=" not in line:
                                continue
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            if k and k not in os.environ:
                                os.environ[k] = v
                    log.info("dotenv_fallback_loaded", extra={"path": env_path})
                except Exception as e:  # noqa: BLE001
                    log.warning("dotenv_fallback_failed", extra={"error": str(e)})
    raw = os.getenv(bundle_env)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, (str | int | float)):
                        _set_missing(k, str(v))
                log.info("api_keys_expanded", extra={"count": len(data)})
        except Exception as e:  # noqa: BLE001
            log.warning("api_keys_parse_failed", extra={"error": str(e)})
    missing = []
    if required:
        for k in required:
            if not os.getenv(k):
                missing.append(k)
    if missing:
        log.warning("env_missing", extra={"missing": missing})
    elif required:
        log.info("env_all_present", extra={"keys": list(required)})
    # Refresh logging level after env (potential LOG_LEVEL) loaded
    configure_logging(refresh=False)
