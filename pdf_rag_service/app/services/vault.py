"""Per-document token vault.

The vault is the only place tokenized PII values live. It is stored as a JSON
sidecar next to the source PDF in S3 (server-side encrypted, see
``object_store``) and deleted together with the document.

Format::

    {
      "version": 1,
      "doc_id": "...",
      "created_at": "2026-01-01T00:00:00+00:00",
      "tokens": {"<PERSON_1>": {"type": "PERSON", "value": "Jane Doe"}, ...}
    }
"""

import json
from datetime import datetime, timezone
from typing import Dict, Optional

from services import object_store

VAULT_VERSION = 1
TokenMap = Dict[str, Dict[str, str]]


def build_vault(doc_id: str, tokens: TokenMap) -> dict:
    return {
        "version": VAULT_VERSION,
        "doc_id": doc_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tokens": tokens,
    }


def save_vault(doc_id: str, tokens: TokenMap) -> str:
    """Persist the vault sidecar and return its S3 key."""
    body = json.dumps(build_vault(doc_id, tokens), ensure_ascii=False).encode("utf-8")
    return object_store.put_object(object_store.vault_key(doc_id), body, "application/json")


def load_vault(doc_id: str) -> Optional[TokenMap]:
    """Return the token map for ``doc_id`` or ``None`` if no vault exists."""
    raw = object_store.get_object(object_store.vault_key(doc_id))
    if raw is None:
        return None
    data = json.loads(raw.decode("utf-8"))
    return data.get("tokens", {})


def delete_vault(doc_id: str) -> None:
    object_store.delete_object(object_store.vault_key(doc_id))
