"""Enrichment provider interfaces.

Real vendors (Zillow Bridge Interactive, ATTOM, People Data Labs, ...) are
wired in later by implementing these protocols and registering them in
``services/enrichment/__init__.py``. Providers receive real values resolved
from the vault in-process and must not persist them.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Protocol


@dataclass
class HomeValueResult:
    estimated_value: Optional[float]
    currency: str
    source: str
    raw: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class OccupationResult:
    occupation: Optional[str]
    employer: Optional[str]
    source: str
    raw: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


class HomeValueProvider(Protocol):
    name: str

    def lookup(self, address: str) -> HomeValueResult: ...


class OccupationProvider(Protocol):
    name: str

    def lookup(self, name: str, address: Optional[str] = None) -> OccupationResult: ...
