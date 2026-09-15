"""Qdrant access.

Two collections:

* ``pdf_chunks`` - one point per text chunk (vector = chunk embedding).
  Payload: doc_id, filename, page, chunk_index, text (tokenized/redacted).
* ``pdf_documents`` - one point per document, id == doc_id (vector = mean of
  the chunk embeddings). Payload: document metadata, PII inventory, token
  stats, and enrichment results. Never token values.
"""

import uuid
from functools import lru_cache
from typing import Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from config import settings


@lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def _ensure(collection_name: str) -> None:
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    if collection_name not in existing:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=qmodels.VectorParams(
                size=settings.embedding_dim,
                distance=qmodels.Distance.COSINE,
            ),
        )


def ensure_collections() -> None:
    _ensure(settings.qdrant_collection)
    _ensure(settings.qdrant_documents_collection)


def _doc_filter(doc_id: str) -> qmodels.Filter:
    return qmodels.Filter(
        must=[qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id))]
    )


# --------------------------------------------------------------------------- #
# Chunks
# --------------------------------------------------------------------------- #


def upsert_chunks(
    doc_id: str,
    filename: str,
    chunks: List[dict],
    vectors: List[List[float]],
) -> None:
    """chunks: list of {page, chunk_index, text} dicts, aligned with vectors."""
    ensure_collections()
    points = [
        qmodels.PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "doc_id": doc_id,
                "filename": filename,
                "page": chunk["page"],
                "chunk_index": chunk["chunk_index"],
                "text": chunk["text"],
            },
        )
        for chunk, vector in zip(chunks, vectors)
    ]
    if points:
        get_client().upsert(collection_name=settings.qdrant_collection, points=points)


def search(vector: List[float], top_k: int = 5) -> List[dict]:
    ensure_collections()
    results = get_client().search(
        collection_name=settings.qdrant_collection,
        query_vector=vector,
        limit=top_k,
    )
    return [
        {
            "score": r.score,
            "doc_id": r.payload.get("doc_id"),
            "filename": r.payload.get("filename"),
            "page": r.payload.get("page"),
            "chunk_index": r.payload.get("chunk_index"),
            "text": r.payload.get("text"),
        }
        for r in results
    ]


def count_chunks_for_doc(doc_id: str) -> int:
    ensure_collections()
    result = get_client().count(
        collection_name=settings.qdrant_collection,
        count_filter=_doc_filter(doc_id),
    )
    return result.count


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #


def upsert_document(doc_id: str, vector: List[float], payload: Dict) -> None:
    ensure_collections()
    get_client().upsert(
        collection_name=settings.qdrant_documents_collection,
        points=[qmodels.PointStruct(id=doc_id, vector=vector, payload=payload)],
    )


def get_document(doc_id: str) -> Optional[Dict]:
    ensure_collections()
    points = get_client().retrieve(
        collection_name=settings.qdrant_documents_collection,
        ids=[doc_id],
        with_payload=True,
        with_vectors=False,
    )
    return dict(points[0].payload) if points else None


def set_document_payload(doc_id: str, payload: Dict) -> None:
    """Merge ``payload`` keys into the document record."""
    ensure_collections()
    get_client().set_payload(
        collection_name=settings.qdrant_documents_collection,
        payload=payload,
        points=[doc_id],
    )


def delete_document(doc_id: str) -> None:
    """Remove all chunk points and the document point for ``doc_id``."""
    ensure_collections()
    client = get_client()
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qmodels.FilterSelector(filter=_doc_filter(doc_id)),
    )
    client.delete(
        collection_name=settings.qdrant_documents_collection,
        points_selector=qmodels.PointIdsList(points=[doc_id]),
    )


def is_healthy() -> bool:
    try:
        get_client().get_collections()
        return True
    except Exception:
        return False
