from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict

_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        data: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        if record.exc_info:
            data["exc_info"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k not in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
            }:
                data[k] = v
        return json.dumps(data, separators=(",", ":"))


def configure_logging(refresh: bool = False) -> None:
    """Configure or refresh logging.

    If already configured, only the log level is updated unless refresh=True.
    """
    global _configured
    root = logging.getLogger()
    if not _configured or refresh:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(JsonFormatter())
        if not _configured:
            root.handlers[:] = [handler]
        else:
            # Replace existing stream handlers' formatter
            for h in root.handlers:
                if isinstance(h, logging.StreamHandler):
                    h.setFormatter(JsonFormatter())
        _configured = True
    # Always refresh level from env
    root.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def get_logger(name: str = "backup") -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
