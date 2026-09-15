from typing import List

from pydantic import BaseModel, Field


class ChunkMetadata(BaseModel):
    doc_id: str
    filename: str
    page: int
    chunk_index: int
    text: str


class DocumentUploadResponse(BaseModel):
    doc_id: str
    filename: str
    num_pages: int
    num_chunks: int
    s3_key: str


class DocumentInfoResponse(BaseModel):
    doc_id: str
    filename: str
    num_chunks: int


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


class HealthResponse(BaseModel):
    status: str
    qdrant: bool
    s3: bool
