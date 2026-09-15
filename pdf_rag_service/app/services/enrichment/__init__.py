"""Provider factory.

Select implementations with ``HOME_VALUE_PROVIDER`` / ``OCCUPATION_PROVIDER``.
To add a vendor: implement the protocol in ``services/enrichment/<vendor>.py``
and register a zero-arg constructor in the registries below.
"""

from typing import Callable, Dict

import config
from services.enrichment.base import HomeValueProvider, OccupationProvider
from services.enrichment.mock import MockHomeValueProvider, MockOccupationProvider

HOME_VALUE_PROVIDERS: Dict[str, Callable[[], HomeValueProvider]] = {
    "mock": MockHomeValueProvider,
}

OCCUPATION_PROVIDERS: Dict[str, Callable[[], OccupationProvider]] = {
    "mock": MockOccupationProvider,
}


class UnknownProviderError(RuntimeError):
    pass


def get_home_value_provider(name: str | None = None) -> HomeValueProvider:
    key = name or config.settings.home_value_provider
    try:
        return HOME_VALUE_PROVIDERS[key]()
    except KeyError:
        raise UnknownProviderError(
            f"Unknown HOME_VALUE_PROVIDER {key!r}; known: {sorted(HOME_VALUE_PROVIDERS)}"
        ) from None


def get_occupation_provider(name: str | None = None) -> OccupationProvider:
    key = name or config.settings.occupation_provider
    try:
        return OCCUPATION_PROVIDERS[key]()
    except KeyError:
        raise UnknownProviderError(
            f"Unknown OCCUPATION_PROVIDER {key!r}; known: {sorted(OCCUPATION_PROVIDERS)}"
        ) from None


def validate_providers() -> None:
    """Called at startup so a typo in provider config fails fast."""
    get_home_value_provider()
    get_occupation_provider()
