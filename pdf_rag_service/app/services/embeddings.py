from functools import lru_cache
from typing import TYPE_CHECKING, List

from config import settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


@lru_cache(maxsize=1)
def get_model() -> "SentenceTransformer":
    """Lazily load and cache the embedding model (loaded once per process).

    The import is deferred so that modules/tests importing this file don't
    need the heavy `sentence-transformers`/`torch` stack installed unless a
    model is actually requested.
    """
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.embedding_model)


def embed_texts(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    model = get_model()
    vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return vectors.tolist()


def embed_text(text: str) -> List[float]:
    return embed_texts([text])[0]
