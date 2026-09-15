import pytest

import config
from services import enrichment
from services.enrichment.mock import MockHomeValueProvider, MockOccupationProvider


def test_mock_home_value_is_deterministic_and_tagged():
    p = MockHomeValueProvider()
    a = p.lookup("123 Main Street, Springfield, IL 62704")
    b = p.lookup("123  main street, springfield, il 62704")  # whitespace/case-insensitive
    c = p.lookup("9 Other Road, Denver, CO 80202")

    assert a.source == "mock"
    assert a.currency == "USD"
    assert 150_000 <= a.estimated_value <= 950_000
    assert a.estimated_value == b.estimated_value
    assert a.estimated_value != c.estimated_value


def test_mock_occupation_is_deterministic_and_tagged():
    p = MockOccupationProvider()
    a = p.lookup("Jane Doe", "123 Main Street")
    b = p.lookup("jane doe")

    assert a.source == "mock"
    assert a.occupation and a.employer
    assert (a.occupation, a.employer) == (b.occupation, b.employer)


def test_mock_raw_payload_does_not_echo_input():
    home = MockHomeValueProvider().lookup("123 Main Street, Springfield, IL 62704").as_dict()
    occ = MockOccupationProvider().lookup("Jane Doe", "123 Main Street").as_dict()
    for blob in (repr(home["raw"]), repr(occ["raw"])):
        assert "Main Street" not in blob
        assert "Jane" not in blob


def test_factory_defaults_to_mock():
    assert isinstance(enrichment.get_home_value_provider(), MockHomeValueProvider)
    assert isinstance(enrichment.get_occupation_provider(), MockOccupationProvider)


def test_factory_reads_settings(monkeypatch):
    monkeypatch.setattr(config.settings, "home_value_provider", "mock")
    assert enrichment.get_home_value_provider().name == "mock"


def test_factory_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(config.settings, "occupation_provider", "linkedin")
    with pytest.raises(enrichment.UnknownProviderError, match="linkedin"):
        enrichment.get_occupation_provider()
    with pytest.raises(enrichment.UnknownProviderError):
        enrichment.validate_providers()


def test_validate_providers_passes_with_defaults():
    enrichment.validate_providers()
