from functools import lru_cache
from typing import List

from sentence_transformers import SentenceTransformer

from config import settings


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """Lazily load and cache the embedding model (loaded once per process)."""
    return SentenceTransformer(settings.embedding_model)


def embed_texts(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    model = get_model()
    vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return vectors.tolist()


def embed_text(text: str) -> List[float]:
    return embed_texts([text])[0]
