import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile

from models.schemas import (
    DocumentInfoResponse,
    DocumentUploadResponse,
    HealthResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from services import embeddings, object_store, pdf_parser, vector_store

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    qdrant_ok = vector_store.is_healthy()
    s3_ok = object_store.is_healthy()
    status = "ok" if (qdrant_ok and s3_ok) else "degraded"
    return HealthResponse(status=status, qdrant=qdrant_ok, s3=s3_ok)


@router.post("/documents", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...)) -> DocumentUploadResponse:
    if file.content_type not in ("application/pdf", "application/octet-stream") and not (
        file.filename or ""
    ).lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    doc_id = str(uuid.uuid4())
    filename = file.filename or f"{doc_id}.pdf"

    try:
        pages = pdf_parser.extract_pages(pdf_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {exc}") from exc

    chunks = pdf_parser.parse_and_chunk(pdf_bytes)

    s3_key = object_store.upload_pdf(doc_id, filename, pdf_bytes)

    if chunks:
        texts = [c.text for c in chunks]
        vectors = embeddings.embed_texts(texts)
        chunk_dicts = [
            {"page": c.page, "chunk_index": c.chunk_index, "text": c.text} for c in chunks
        ]
        vector_store.upsert_chunks(doc_id, filename, chunk_dicts, vectors)

    return DocumentUploadResponse(
        doc_id=doc_id,
        filename=filename,
        num_pages=len(pages),
        num_chunks=len(chunks),
        s3_key=s3_key,
    )


@router.get("/documents/{doc_id}", response_model=DocumentInfoResponse)
def get_document(doc_id: str) -> DocumentInfoResponse:
    num_chunks = vector_store.count_chunks_for_doc(doc_id)
    if num_chunks == 0:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentInfoResponse(doc_id=doc_id, filename="", num_chunks=num_chunks)


@router.post("/search", response_model=SearchResponse)
def search(request: SearchRequest) -> SearchResponse:
    vector = embeddings.embed_text(request.query)
    raw_results = vector_store.search(vector, top_k=request.top_k)
    results = [SearchResultItem(**r) for r in raw_results]
    return SearchResponse(query=request.query, results=results)
