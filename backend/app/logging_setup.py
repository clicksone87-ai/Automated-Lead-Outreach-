"""Structured (JSON lines) logging for all services; secrets are never logged."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

from app.config import get_settings

_SECRET_RE = re.compile(r"(api[_-]?key|token|password|secret|authorization)=([^&\s]+)", re.I)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = _SECRET_RE.sub(r"\1=***", record.getMessage())
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": message,
        }
        if record.exc_info:
            payload["exc"] = _SECRET_RE.sub(r"\1=***", self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(get_settings().log_level.upper())
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine", "arq.jobs"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
