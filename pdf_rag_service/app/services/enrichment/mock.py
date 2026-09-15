"""Deterministic mock providers.

Outputs are derived from a hash of the input so repeated calls agree, and
every result is tagged ``source="mock"`` so it can never be mistaken for
vendor data. The ``raw`` payload intentionally does not echo the input.
"""

import hashlib
import re
from typing import Optional

from services.enrichment.base import HomeValueResult, OccupationResult

_OCCUPATIONS = [
    ("Registered Nurse", "Regional Medical Center"),
    ("Software Engineer", "Acme Technologies"),
    ("Electrician", "Bright Spark Electrical LLC"),
    ("High School Teacher", "Unified School District"),
    ("Accountant", "Ledger & Co. CPAs"),
    ("Truck Driver", "Interstate Freight Lines"),
    ("Retail Manager", "Northside Home Goods"),
    ("Paralegal", "Hart & Wells LLP"),
]


def _seed(*parts: Optional[str]) -> int:
    normalized = "|".join(re.sub(r"\s+", " ", (p or "").strip().casefold()) for p in parts)
    return int(hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12], 16)


class MockHomeValueProvider:
    name = "mock"

    def lookup(self, address: str) -> HomeValueResult:
        seed = _seed(address)
        # $150k - $950k in $500 steps
        value = 150_000 + (seed % 1_600) * 500
        return HomeValueResult(
            estimated_value=float(value),
            currency="USD",
            source=self.name,
            raw={"note": "synthetic value; not a real valuation", "confidence": "n/a"},
        )


class MockOccupationProvider:
    name = "mock"

    def lookup(self, name: str, address: Optional[str] = None) -> OccupationResult:
        occupation, employer = _OCCUPATIONS[_seed(name) % len(_OCCUPATIONS)]
        return OccupationResult(
            occupation=occupation,
            employer=employer,
            source=self.name,
            raw={"note": "synthetic record; not a real person lookup", "confidence": "n/a"},
        )
