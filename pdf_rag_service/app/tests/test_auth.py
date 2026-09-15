import pytest
from fastapi.testclient import TestClient

import config
import main
from config import Settings, validate_settings
from services import object_store, vector_store
from tests.conftest import AUTH, REVEAL, TEST_API_KEY


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(vector_store, "is_healthy", lambda: True)
    monkeypatch.setattr(object_store, "is_healthy", lambda: True)
    monkeypatch.setattr(vector_store, "get_document", lambda doc_id: None)
    return TestClient(main.app)


def test_health_is_open(client):
    assert client.get("/health").status_code == 200


def test_protected_route_rejects_missing_key(client):
    resp = client.get("/documents/anything")
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "ApiKey"


def test_protected_route_rejects_wrong_key(client):
    assert client.get("/documents/anything", headers={"X-API-Key": "nope"}).status_code == 401


def test_protected_route_accepts_correct_key(client):
    # 404 (not 401) proves auth passed and the handler ran.
    assert client.get("/documents/anything", headers=AUTH).status_code == 404


@pytest.mark.parametrize("path", ["/documents", "/search"])
def test_all_mutating_routes_require_key(client, path):
    assert client.post(path).status_code == 401
    assert client.delete("/documents/x").status_code == 401


def test_include_pii_without_reveal_key_is_forbidden(client):
    resp = client.get("/documents/anything?include_pii=true", headers=AUTH)
    assert resp.status_code == 403


def test_include_pii_with_wrong_reveal_key_is_forbidden(client):
    headers = {"X-API-Key": TEST_API_KEY, "X-PII-Reveal-Key": "wrong"}
    assert client.get("/documents/anything?include_pii=true", headers=headers).status_code == 403


def test_include_pii_with_valid_reveal_key_passes_auth(client):
    # 404 proves both auth layers passed.
    assert client.get("/documents/anything?include_pii=true", headers=REVEAL).status_code == 404


def test_reveal_key_alone_does_not_grant_api_access(client):
    headers = {"X-PII-Reveal-Key": REVEAL["X-PII-Reveal-Key"]}
    assert client.get("/documents/anything?include_pii=true", headers=headers).status_code == 401


def test_reveal_disabled_when_key_unset(client, monkeypatch):
    monkeypatch.setattr(config.settings, "pii_reveal_key", "")
    resp = client.get("/documents/anything?include_pii=true", headers=REVEAL)
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"]


def test_include_pii_false_ignores_reveal_key_entirely(client):
    assert client.get("/documents/anything?include_pii=false", headers=AUTH).status_code == 404


# --- startup validation -------------------------------------------------- #


def test_startup_fails_without_api_key():
    with pytest.raises(RuntimeError, match="API_KEY"):
        validate_settings(Settings(api_key=""))


def test_startup_rejects_bad_pii_mode():
    with pytest.raises(RuntimeError, match="PII_MODE"):
        validate_settings(Settings(api_key="k", pii_mode="mask"))


def test_startup_requires_kms_key_for_kms_mode():
    with pytest.raises(RuntimeError, match="S3_KMS_KEY_ID"):
        validate_settings(Settings(api_key="k", s3_sse_mode="aws:kms", s3_kms_key_id=""))


def test_startup_rejects_entity_in_both_lists():
    with pytest.raises(RuntimeError, match="both"):
        validate_settings(
            Settings(api_key="k", pii_tokenize_entities="PERSON", pii_redact_entities="PERSON,US_SSN")
        )


def test_valid_settings_pass():
    validate_settings(Settings(api_key="k"))
