from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Shared
# --------------------------------------------------------------------------- #


class HealthResponse(BaseModel):
    status: str
    qdrant: bool
    s3: bool


class InventoryEntry(BaseModel):
    count: int
    pages: List[int]


class TokenStat(BaseModel):
    type: str
    count: int
    first_page: int


class Subject(BaseModel):
    """Who the document is about, expressed as tokens.

    ``name`` / ``address`` are only populated when the caller supplied a valid
    reveal key (``include_pii=true``).
    """

    name_token: Optional[str] = None
    address_token: Optional[str] = None
    name_candidates: List[str] = Field(default_factory=list)
    address_candidates: List[str] = Field(default_factory=list)
    name: Optional[str] = None
    address: Optional[str] = None


class HomeValue(BaseModel):
    estimated_value: Optional[float]
    currency: str
    source: str
    raw: Dict[str, Any] = Field(default_factory=dict)


class Occupation(BaseModel):
    occupation: Optional[str]
    employer: Optional[str]
    source: str
    raw: Dict[str, Any] = Field(default_factory=dict)


class Enrichment(BaseModel):
    home_value: Optional[HomeValue] = None
    occupation: Optional[Occupation] = None
    enriched_at: str


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #


class DocumentUploadResponse(BaseModel):
    doc_id: str
    filename: str
    num_pages: int
    num_chunks: int
    s3_key: str
    vault_key: Optional[str]
    pii_mode: str
    pii_inventory: Dict[str, InventoryEntry]


class DocumentRecordResponse(BaseModel):
    doc_id: str
    filename: str
    num_pages: int
    num_chunks: int
    s3_key: str
    vault_key: Optional[str]
    pii_mode: str
    pii_inventory: Dict[str, InventoryEntry]
    token_stats: Dict[str, TokenStat]
    subject: Optional[Subject] = None
    enrichment: Optional[Enrichment] = None
    pii_revealed: bool = False


class EnrichResponse(BaseModel):
    doc_id: str
    subject: Optional[Subject]
    enrichment: Optional[Enrichment]
    reason: Optional[str] = None
    pii_revealed: bool = False


class DeleteResponse(BaseModel):
    doc_id: str
    deleted: bool


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


class SearchResultItem(BaseModel):
    score: float
    doc_id: str
    filename: str
    page: int
    chunk_index: int
    text: str


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResultItem]
    pii_revealed: bool = False
