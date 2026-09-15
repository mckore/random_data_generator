"""Pick the individual a document is about, using only tokens.

Tokenization (``services/pii.py``) already found every person and address in
the document at upload time. Enrichment therefore never re-reads the PDF; it
selects subject tokens from ``token_stats`` and resolves the values from the
vault only at the moment a provider is called.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_TOKEN_NUM = re.compile(r"_(\d+)>$")


@dataclass
class Subject:
    name_token: Optional[str] = None
    address_token: Optional[str] = None
    name_candidates: List[str] = field(default_factory=list)
    address_candidates: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.name_token is None and self.address_token is None

    def as_dict(self) -> dict:
        return {
            "name_token": self.name_token,
            "address_token": self.address_token,
            "name_candidates": self.name_candidates,
            "address_candidates": self.address_candidates,
        }


def _token_number(token: str) -> int:
    match = _TOKEN_NUM.search(token)
    return int(match.group(1)) if match else 0


def _tokens_of_type(token_stats: Dict[str, dict], entity_type: str) -> List[str]:
    return [t for t, s in token_stats.items() if s.get("type") == entity_type]


def select_subject(token_stats: Dict[str, dict]) -> Subject:
    """Choose primary name/address tokens.

    * Name: most frequent ``PERSON`` token; ties broken by earliest
      ``first_page``, then lowest token number (first seen).
    * Address: the first-seen ``ADDRESS`` token (lowest number). Insurance
      documents usually lead with the insured property/mailing address.
    """
    persons = _tokens_of_type(token_stats, "PERSON")
    addresses = _tokens_of_type(token_stats, "ADDRESS")

    persons.sort(
        key=lambda t: (
            -int(token_stats[t].get("count", 0)),
            int(token_stats[t].get("first_page", 0)),
            _token_number(t),
        )
    )
    addresses.sort(key=_token_number)

    return Subject(
        name_token=persons[0] if persons else None,
        address_token=addresses[0] if addresses else None,
        name_candidates=persons,
        address_candidates=addresses,
    )
