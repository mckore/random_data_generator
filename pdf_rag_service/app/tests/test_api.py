import io

import pytest
from fastapi.testclient import TestClient

import main
from services import embeddings, object_store, vector_store


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def patch_services(monkeypatch):
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [[0.1, 0.2, 0.3] for _ in texts])
    monkeypatch.setattr(embeddings, "embed_text", lambda text: [0.1, 0.2, 0.3])

    monkeypatch.setattr(
        object_store,
        "upload_pdf",
        lambda doc_id, filename, data: f"pdfs/{doc_id}/{filename}",
    )
    monkeypatch.setattr(object_store, "is_healthy", lambda: True)

    monkeypatch.setattr(vector_store, "is_healthy", lambda: True)
    monkeypatch.setattr(vector_store, "upsert_chunks", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        vector_store,
        "search",
        lambda vector, top_k=5: [
            {
                "score": 0.9,
                "doc_id": "doc-1",
                "filename": "a.pdf",
                "page": 1,
                "chunk_index": 0,
                "text": "hello",
            }
        ],
    )
    monkeypatch.setattr(
        vector_store,
        "count_chunks_for_doc",
        lambda doc_id: 3 if doc_id == "known" else 0,
    )


def test_health_ok(client):
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "qdrant": True, "s3": True}


def test_health_degraded_when_dependency_down(client, monkeypatch):
    monkeypatch.setattr(vector_store, "is_healthy", lambda: False)

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["qdrant"] is False


def test_upload_document_rejects_non_pdf(client):
    resp = client.post(
        "/documents",
        files={"file": ("notes.txt", io.BytesIO(b"not a pdf"), "text/plain")},
    )

    assert resp.status_code == 400


def test_upload_document_rejects_empty_file(client):
    resp = client.post(
        "/documents",
        files={"file": ("sample.pdf", io.BytesIO(b""), "application/pdf")},
    )

    assert resp.status_code == 400


def test_upload_document_success(client, sample_pdf_bytes):
    resp = client.post(
        "/documents",
        files={"file": ("sample.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "sample.pdf"
    assert body["num_pages"] == 2
    assert body["num_chunks"] > 0
    assert body["s3_key"].endswith("sample.pdf")


def test_get_document_not_found(client):
    resp = client.get("/documents/unknown")
    assert resp.status_code == 404


def test_get_document_found(client):
    resp = client.get("/documents/known")

    assert resp.status_code == 200
    assert resp.json()["num_chunks"] == 3


def test_search_returns_results(client):
    resp = client.post("/search", json={"query": "hello", "top_k": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "hello"
    assert len(body["results"]) == 1
    assert body["results"][0]["doc_id"] == "doc-1"


def test_search_requires_nonempty_query(client):
    resp = client.post("/search", json={"query": "", "top_k": 1})
    assert resp.status_code == 422
