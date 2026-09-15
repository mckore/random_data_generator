import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

import main
from services import audit, embeddings, object_store, vault, vector_store
from tests.conftest import AUTH, REVEAL

RAW_PII = ["Jane Doe", "536-90-4399", "(555) 123-4567", "123 Main Street", "Robert Miles"]


# --------------------------------------------------------------------------- #
# In-memory doubles
# --------------------------------------------------------------------------- #


class FakeVectorStore:
    def __init__(self):
        self.docs = {}
        self.chunks = []

    def upsert_chunks(self, doc_id, filename, chunks, vectors):
        assert len(chunks) == len(vectors)
        self.chunks.extend({"doc_id": doc_id, "filename": filename, **c} for c in chunks)

    def upsert_document(self, doc_id, vector, payload):
        assert len(vector) == 3
        self.docs[doc_id] = json.loads(json.dumps(payload))  # force JSON-serialisable

    def get_document(self, doc_id):
        return dict(self.docs[doc_id]) if doc_id in self.docs else None

    def set_document_payload(self, doc_id, payload):
        self.docs[doc_id].update(json.loads(json.dumps(payload)))

    def delete_document(self, doc_id):
        self.docs.pop(doc_id, None)
        self.chunks = [c for c in self.chunks if c["doc_id"] != doc_id]

    def search(self, vector, top_k=5):
        return [{"score": 0.9, **c} for c in self.chunks[:top_k]]


class FakeVault:
    def __init__(self):
        self.store = {}

    def save(self, doc_id, tokens):
        self.store[doc_id] = json.loads(json.dumps(tokens))
        return f"pdfs/{doc_id}/pii_vault.json"

    def load(self, doc_id):
        return dict(self.store[doc_id]) if doc_id in self.store else None


@pytest.fixture
def fake_vs(monkeypatch):
    fake = FakeVectorStore()
    for name in ("upsert_chunks", "upsert_document", "get_document", "set_document_payload",
                 "delete_document", "search"):
        monkeypatch.setattr(vector_store, name, getattr(fake, name))
    monkeypatch.setattr(vector_store, "is_healthy", lambda: True)
    return fake


@pytest.fixture
def fake_vault(monkeypatch):
    fake = FakeVault()
    monkeypatch.setattr(vault, "save_vault", fake.save)
    monkeypatch.setattr(vault, "load_vault", fake.load)
    return fake


@pytest.fixture
def deleted_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(object_store, "upload_pdf", lambda d, f, b: f"pdfs/{d}/{f}")
    monkeypatch.setattr(object_store, "delete_document_objects", lambda *keys: calls.append(keys))
    monkeypatch.setattr(object_store, "is_healthy", lambda: True)
    return calls


@pytest.fixture(autouse=True)
def _embeddings(monkeypatch):
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [[0.1, 0.2, 0.3] for _ in texts])
    monkeypatch.setattr(embeddings, "embed_text", lambda text: [0.1, 0.2, 0.3])


@pytest.fixture
def client(fake_vs, fake_vault, deleted_keys):
    return TestClient(main.app)


@pytest.fixture
def audit_log(caplog):
    caplog.set_level(logging.INFO, logger=audit.AUDIT_LOGGER_NAME)
    return caplog


def _upload(client, pdf_bytes, name="policy.pdf"):
    resp = client.post(
        "/documents",
        headers=AUTH,
        files={"file": (name, io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _audit_events(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == audit.AUDIT_LOGGER_NAME]


def _assert_no_pii(blob: str):
    for value in RAW_PII:
        assert value not in blob, f"PII leaked: {value}"


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #


def test_health_ok(client):
    assert client.get("/health").json() == {"status": "ok", "qdrant": True, "s3": True}


def test_health_degraded(client, monkeypatch):
    monkeypatch.setattr(vector_store, "is_healthy", lambda: False)
    body = client.get("/health").json()
    assert body["status"] == "degraded" and body["qdrant"] is False


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #


def test_upload_rejects_non_pdf(client):
    resp = client.post("/documents", headers=AUTH,
                       files={"file": ("n.txt", io.BytesIO(b"x"), "text/plain")})
    assert resp.status_code == 400


def test_upload_rejects_empty(client):
    resp = client.post("/documents", headers=AUTH,
                       files={"file": ("a.pdf", io.BytesIO(b""), "application/pdf")})
    assert resp.status_code == 400


def test_upload_tokenizes_before_storage(client, fake_vs, fake_vault, pii_pdf_bytes, audit_log):
    body = _upload(client, pii_pdf_bytes)
    doc_id = body["doc_id"]

    # Response: inventory only, no values
    assert body["pii_mode"] == "tokenize"
    assert body["pii_inventory"]["PERSON"]["count"] >= 2
    assert body["vault_key"] == f"pdfs/{doc_id}/pii_vault.json"
    _assert_no_pii(json.dumps(body))

    # Chunks in the vector store are tokenized
    stored_text = " ".join(c["text"] for c in fake_vs.chunks if c["doc_id"] == doc_id)
    assert "<PERSON_1>" in stored_text
    assert "<US_SSN>" in stored_text
    _assert_no_pii(stored_text)

    # Document record holds stats, not values
    record = fake_vs.docs[doc_id]
    assert record["filename"] == "policy.pdf"
    assert record["token_stats"]["<PERSON_1>"]["type"] == "PERSON"
    assert record["subject"] is None and record["enrichment"] is None
    _assert_no_pii(json.dumps(record))

    # Vault holds the values, and only tokenizable types
    tokens = fake_vault.store[doc_id]
    assert tokens["<PERSON_1>"]["value"] == "Jane Doe"
    assert {t["type"] for t in tokens.values()} <= {"PERSON", "ADDRESS", "PHONE_NUMBER"}

    # Audit
    events = _audit_events(audit_log)
    assert events[-1]["event"] == audit.UPLOAD
    assert events[-1]["doc_id"] == doc_id
    assert events[-1]["entity_type_counts"]["PERSON"] >= 2
    _assert_no_pii(json.dumps(events))


def test_upload_without_pii_has_no_vault(client, fake_vault, sample_pdf_bytes):
    body = _upload(client, sample_pdf_bytes, name="plain.pdf")
    assert body["vault_key"] is None
    assert body["doc_id"] not in fake_vault.store
    assert body["num_pages"] == 2 and body["num_chunks"] > 0


def test_upload_blank_pdf_still_creates_record(client, fake_vs, blank_pdf_bytes):
    body = _upload(client, blank_pdf_bytes, name="blank.pdf")
    assert body["num_chunks"] == 0
    assert body["doc_id"] in fake_vs.docs


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #


def test_get_document_404(client):
    assert client.get("/documents/nope", headers=AUTH).status_code == 404


def test_get_document_returns_record_without_values(client, pii_pdf_bytes):
    doc_id = _upload(client, pii_pdf_bytes)["doc_id"]
    body = client.get(f"/documents/{doc_id}", headers=AUTH).json()

    assert body["filename"] == "policy.pdf"
    assert "<PERSON_1>" in body["token_stats"]
    assert body["pii_revealed"] is False
    assert body["subject"] is None
    _assert_no_pii(json.dumps(body))


# --------------------------------------------------------------------------- #
# Enrich
# --------------------------------------------------------------------------- #


def test_enrich_404(client):
    assert client.post("/documents/nope/enrich", headers=AUTH).status_code == 404


def test_enrich_without_subject_returns_reason(client, sample_pdf_bytes, audit_log):
    doc_id = _upload(client, sample_pdf_bytes, name="plain.pdf")["doc_id"]
    body = client.post(f"/documents/{doc_id}/enrich", headers=AUTH).json()

    assert body["subject"] is None and body["enrichment"] is None
    assert "No PERSON or ADDRESS" in body["reason"]
    assert _audit_events(audit_log)[-1]["status"] == "no_subject"


def test_enrich_returns_tokens_by_default(client, fake_vs, pii_pdf_bytes, audit_log):
    doc_id = _upload(client, pii_pdf_bytes)["doc_id"]
    resp = client.post(f"/documents/{doc_id}/enrich", headers=AUTH)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["pii_revealed"] is False
    assert body["subject"]["name_token"] == "<PERSON_1>"
    assert body["subject"]["address_token"] == "<ADDRESS_1>"
    assert body["subject"]["name"] is None and body["subject"]["address"] is None
    assert body["enrichment"]["home_value"]["source"] == "mock"
    assert body["enrichment"]["occupation"]["source"] == "mock"
    assert body["enrichment"]["home_value"]["estimated_value"] > 0
    _assert_no_pii(json.dumps(body))

    # Persisted on the record, still token-only
    record = fake_vs.docs[doc_id]
    assert record["subject"]["name_token"] == "<PERSON_1>"
    assert record["enrichment"]["occupation"]["occupation"]
    _assert_no_pii(json.dumps(record))

    events = _audit_events(audit_log)
    enrich = [e for e in events if e["event"] == audit.ENRICH][-1]
    assert enrich["providers"] == {"home_value": "mock", "occupation": "mock"}
    assert enrich["has_name"] and enrich["has_address"]
    assert not any(e["event"] == audit.REVEAL for e in events)
    _assert_no_pii(json.dumps(events))


def test_enrich_reveals_with_reveal_key(client, pii_pdf_bytes, audit_log):
    doc_id = _upload(client, pii_pdf_bytes)["doc_id"]
    body = client.post(f"/documents/{doc_id}/enrich?include_pii=true", headers=REVEAL).json()

    assert body["pii_revealed"] is True
    assert body["subject"]["name"] == "Jane Doe"
    assert body["subject"]["address"].startswith("123 Main Street")

    reveal = [e for e in _audit_events(audit_log) if e["event"] == audit.REVEAL][-1]
    assert reveal["tokens_revealed"] == 2
    assert reveal["reveal_actor"] and reveal["actor"]
    _assert_no_pii(json.dumps(_audit_events(audit_log)))


def test_enrich_requires_reveal_key_for_include_pii(client, pii_pdf_bytes):
    doc_id = _upload(client, pii_pdf_bytes)["doc_id"]
    assert client.post(f"/documents/{doc_id}/enrich?include_pii=true", headers=AUTH).status_code == 403


def test_enrich_409_when_vault_missing(client, fake_vault, pii_pdf_bytes):
    doc_id = _upload(client, pii_pdf_bytes)["doc_id"]
    fake_vault.store.pop(doc_id)
    assert client.post(f"/documents/{doc_id}/enrich", headers=AUTH).status_code == 409


def test_get_document_after_enrich_renders_only_with_reveal(client, pii_pdf_bytes):
    doc_id = _upload(client, pii_pdf_bytes)["doc_id"]
    client.post(f"/documents/{doc_id}/enrich", headers=AUTH)

    plain = client.get(f"/documents/{doc_id}", headers=AUTH).json()
    assert plain["subject"]["name_token"] == "<PERSON_1>"
    assert plain["subject"]["name"] is None
    assert plain["enrichment"]["home_value"]["source"] == "mock"
    _assert_no_pii(json.dumps(plain))

    revealed = client.get(f"/documents/{doc_id}?include_pii=true", headers=REVEAL).json()
    assert revealed["pii_revealed"] is True
    assert revealed["subject"]["name"] == "Jane Doe"


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


def test_search_requires_nonempty_query(client):
    assert client.post("/search", headers=AUTH, json={"query": "", "top_k": 1}).status_code == 422


def test_search_returns_tokenized_text_by_default(client, pii_pdf_bytes, audit_log):
    _upload(client, pii_pdf_bytes)
    body = client.post("/search", headers=AUTH, json={"query": "insured", "top_k": 5}).json()

    assert body["results"]
    assert body["pii_revealed"] is False
    text = " ".join(r["text"] for r in body["results"])
    assert "<PERSON_1>" in text
    _assert_no_pii(text)

    event = [e for e in _audit_events(audit_log) if e["event"] == audit.SEARCH][-1]
    assert event["results"] == len(body["results"]) and event["include_pii"] is False


def test_search_renders_with_reveal_key(client, pii_pdf_bytes, audit_log):
    _upload(client, pii_pdf_bytes)
    body = client.post("/search?include_pii=true", headers=REVEAL, json={"query": "insured"}).json()

    text = " ".join(r["text"] for r in body["results"])
    assert body["pii_revealed"] is True
    assert "Jane Doe" in text
    assert "<US_SSN>" in text  # redacted types never come back

    reveal = [e for e in _audit_events(audit_log) if e["event"] == audit.REVEAL][-1]
    assert reveal["tokens_revealed"] >= 1
    _assert_no_pii(json.dumps(_audit_events(audit_log)))


def test_search_include_pii_without_reveal_key_forbidden(client):
    assert client.post("/search?include_pii=true", headers=AUTH, json={"query": "x"}).status_code == 403


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #


def test_delete_404(client):
    assert client.delete("/documents/nope", headers=AUTH).status_code == 404


def test_delete_purges_everything(client, fake_vs, deleted_keys, pii_pdf_bytes, audit_log):
    body = _upload(client, pii_pdf_bytes)
    doc_id = body["doc_id"]

    resp = client.delete(f"/documents/{doc_id}", headers=AUTH)
    assert resp.json() == {"doc_id": doc_id, "deleted": True}

    assert doc_id not in fake_vs.docs
    assert not any(c["doc_id"] == doc_id for c in fake_vs.chunks)
    assert deleted_keys[-1] == (body["s3_key"], body["vault_key"])
    assert client.get(f"/documents/{doc_id}", headers=AUTH).status_code == 404

    assert _audit_events(audit_log)[-1] == {
        **_audit_events(audit_log)[-1],
        "event": audit.DELETE,
        "doc_id": doc_id,
        "status": "ok",
    }
