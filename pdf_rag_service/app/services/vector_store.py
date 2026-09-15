import uuid
from functools import lru_cache
from typing import List

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from config import settings


@lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def ensure_collection() -> None:
    client = get_client()
    collections = {c.name for c in client.get_collections().collections}
    if settings.qdrant_collection not in collections:
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=qmodels.VectorParams(
                size=settings.embedding_dim,
                distance=qmodels.Distance.COSINE,
            ),
        )


def upsert_chunks(
    doc_id: str,
    filename: str,
    chunks: List[dict],
    vectors: List[List[float]],
) -> None:
    """chunks: list of {page, chunk_index, text} dicts, aligned with vectors."""
    ensure_collection()
    client = get_client()

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
        client.upsert(collection_name=settings.qdrant_collection, points=points)


def search(vector: List[float], top_k: int = 5) -> List[dict]:
    ensure_collection()
    client = get_client()
    results = client.search(
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
    ensure_collection()
    client = get_client()
    result = client.count(
        collection_name=settings.qdrant_collection,
        count_filter=qmodels.Filter(
            must=[qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id))]
        ),
    )
    return result.count


def is_healthy() -> bool:
    try:
        get_client().get_collections()
        return True
    except Exception:
        return False
