"""Authentication dependencies.

* ``require_api_key`` - every route except ``/health``. Compares the
  ``X-API-Key`` header against ``API_KEY`` in constant time.
* ``reveal_context`` - evaluated on routes that accept ``include_pii``. When
  the caller asks for PII, a *separate* ``X-PII-Reveal-Key`` must match
  ``PII_REVEAL_KEY`` (split privilege: the normal API key alone can never
  reveal). If ``PII_REVEAL_KEY`` is not configured, reveal is disabled.
"""

import hmac
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Query, Security, status
from fastapi.security import APIKeyHeader

import config
from services import audit

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_reveal_key_header = APIKeyHeader(name="X-PII-Reveal-Key", auto_error=False)


def _matches(provided: Optional[str], expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


@dataclass
class Actor:
    """Identity of the caller as recorded in audit logs (fingerprints only)."""

    api_key_fp: str


@dataclass
class RevealContext:
    include_pii: bool
    reveal_key_fp: Optional[str] = None

    @property
    def revealed(self) -> bool:
        return self.include_pii


def require_api_key(api_key: Optional[str] = Security(_api_key_header)) -> Actor:
    if not _matches(api_key, config.settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return Actor(api_key_fp=audit.fingerprint(api_key))


def reveal_context(
    include_pii: bool = Query(
        False,
        description="Render tokenized PII to real values. Requires X-PII-Reveal-Key.",
    ),
    reveal_key: Optional[str] = Security(_reveal_key_header),
    _: Actor = Depends(require_api_key),
) -> RevealContext:
    if not include_pii:
        return RevealContext(include_pii=False)

    settings = config.settings
    if not settings.reveal_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PII reveal is disabled on this deployment",
        )
    if not _matches(reveal_key, settings.pii_reveal_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid PII reveal key",
        )
    return RevealContext(include_pii=True, reveal_key_fp=audit.fingerprint(reveal_key))
