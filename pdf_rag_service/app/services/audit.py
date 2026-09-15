"""Structured audit logging for PII-relevant events.

Every event is one JSON object on the ``audit`` logger. Events carry
identifiers, counts, and key fingerprints only - never document text, token
values, or enrichment values.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

AUDIT_LOGGER_NAME = "audit"
_logger = logging.getLogger(AUDIT_LOGGER_NAME)

UPLOAD = "document.upload"
READ = "document.read"
SEARCH = "document.search"
ENRICH = "document.enrich"
REVEAL = "document.reveal"
DELETE = "document.delete"


def fingerprint(secret: Optional[str]) -> Optional[str]:
    """Short, non-reversible identifier for an API key (safe to log)."""
    if not secret:
        return None
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


def log(event: str, **fields: Any) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
    record.update({k: v for k, v in fields.items() if v is not None})
    _logger.info(json.dumps(record, default=str, sort_keys=True))
