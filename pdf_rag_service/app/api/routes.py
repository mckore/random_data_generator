import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

import config
from api.auth import Actor, RevealContext, require_api_key, reveal_context
from models.schemas import (
    DeleteResponse,
    DocumentRecordResponse,
    DocumentUploadResponse,
    EnrichResponse,
    HealthResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from services import (
    audit,
    embeddings,
    entity_extractor,
    enrichment,
    object_store,
    pdf_parser,
    pii,
    vault,
    vector_store,
)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _mean(vectors: List[List[float]]) -> List[float]:
    dims = len(vectors[0])
    totals = [0.0] * dims
    for vec in vectors:
        for i, v in enumerate(vec):
            totals[i] += v
    n = float(len(vectors))
    return [t / n for t in totals]


def _is_pdf(file: UploadFile) -> bool:
    if (file.filename or "").lower().endswith(".pdf"):
        return True
    return file.content_type in ("application/pdf", "application/octet-stream")


def _get_record_or_404(doc_id: str) -> Dict:
    record = vector_store.get_document(doc_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return record


def _load_vault_for_reveal(doc_id: str, reveal: RevealContext) -> Dict[str, Dict[str, str]]:
    if not reveal.revealed:
        return {}
    return vault.load_vault(doc_id) or {}


def _render_subject(subject: Optional[Dict], token_map: Dict) -> Optional[Dict]:
    if subject is None:
        return None
    rendered = dict(subject)
    if token_map:
        rendered["name"] = pii.render_value(subject.get("name_token"), token_map)
        rendered["address"] = pii.render_value(subject.get("address_token"), token_map)
    return rendered


def _audit_reveal(doc_ids: List[str], reveal: RevealContext, actor: Actor, count: int) -> None:
    if reveal.revealed:
        audit.log(
            audit.REVEAL,
            doc_ids=doc_ids,
            actor=actor.api_key_fp,
            reveal_actor=reveal.reveal_key_fp,
            tokens_revealed=count,
        )


def _entity_type_counts(inventory: Dict) -> Dict[str, int]:
    return {etype: entry.get("count", 0) for etype, entry in inventory.items()}


# --------------------------------------------------------------------------- #
# Health (unauthenticated - used by container healthchecks)
# --------------------------------------------------------------------------- #


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    qdrant_ok = vector_store.is_healthy()
    s3_ok = object_store.is_healthy()
    status = "ok" if (qdrant_ok and s3_ok) else "degraded"
    return HealthResponse(status=status, qdrant=qdrant_ok, s3=s3_ok)


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #


@router.post("/documents", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    actor: Actor = Depends(require_api_key),
) -> DocumentUploadResponse:
    if not _is_pdf(file):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    doc_id = str(uuid.uuid4())
    filename = file.filename or f"{doc_id}.pdf"
    settings = config.settings

    try:
        pages = pdf_parser.extract_pages(pdf_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {exc}") from exc

    # --- PII detection / tokenization (before anything is embedded or stored) ---
    tokenizer = pii.DocumentTokenizer()
    chunks: List[pdf_parser.Chunk] = []
    for page in pages:
        processed = tokenizer.process_page(page.text, page.page)
        for idx, text in enumerate(pdf_parser.chunk_text(processed.text)):
            chunks.append(pdf_parser.Chunk(page=page.page, chunk_index=idx, text=text))

    # --- Object storage (source PDF + token vault), always encrypted ---
    s3_key = object_store.upload_pdf(doc_id, filename, pdf_bytes)
    vault_key = vault.save_vault(doc_id, tokenizer.vault) if tokenizer.vault else None

    # --- Vector storage ---
    if chunks:
        vectors = embeddings.embed_texts([c.text for c in chunks])
        vector_store.upsert_chunks(
            doc_id,
            filename,
            [{"page": c.page, "chunk_index": c.chunk_index, "text": c.text} for c in chunks],
            vectors,
        )
        doc_vector = _mean(vectors)
    else:
        doc_vector = embeddings.embed_text(filename)

    record = {
        "doc_id": doc_id,
        "filename": filename,
        "num_pages": len(pages),
        "num_chunks": len(chunks),
        "s3_key": s3_key,
        "vault_key": vault_key,
        "pii_mode": settings.pii_mode,
        "pii_inventory": tokenizer.inventory,
        "token_stats": tokenizer.token_stats,
        "subject": None,
        "enrichment": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    vector_store.upsert_document(doc_id, doc_vector, record)

    audit.log(
        audit.UPLOAD,
        doc_id=doc_id,
        actor=actor.api_key_fp,
        pii_mode=settings.pii_mode,
        num_pages=len(pages),
        num_chunks=len(chunks),
        entity_type_counts=_entity_type_counts(tokenizer.inventory),
        tokens=len(tokenizer.token_stats),
        status="ok",
    )

    return DocumentUploadResponse(
        doc_id=doc_id,
        filename=filename,
        num_pages=len(pages),
        num_chunks=len(chunks),
        s3_key=s3_key,
        vault_key=vault_key,
        pii_mode=settings.pii_mode,
        pii_inventory=tokenizer.inventory,
    )


@router.get("/documents/{doc_id}", response_model=DocumentRecordResponse)
def get_document(
    doc_id: str,
    actor: Actor = Depends(require_api_key),
    reveal: RevealContext = Depends(reveal_context),
) -> DocumentRecordResponse:
    record = _get_record_or_404(doc_id)
    token_map = _load_vault_for_reveal(doc_id, reveal)

    subject = _render_subject(record.get("subject"), token_map)
    revealed_count = sum(1 for k in ("name", "address") if subject and subject.get(k))

    audit.log(
        audit.READ,
        doc_id=doc_id,
        actor=actor.api_key_fp,
        include_pii=reveal.revealed,
        status="ok",
    )
    _audit_reveal([doc_id], reveal, actor, revealed_count)

    return DocumentRecordResponse(
        **{k: v for k, v in record.items() if k not in ("subject", "created_at")},
        subject=subject,
        pii_revealed=reveal.revealed,
    )


@router.post("/documents/{doc_id}/enrich", response_model=EnrichResponse)
def enrich_document(
    doc_id: str,
    actor: Actor = Depends(require_api_key),
    reveal: RevealContext = Depends(reveal_context),
) -> EnrichResponse:
    record = _get_record_or_404(doc_id)

    subject = entity_extractor.select_subject(record.get("token_stats") or {})
    if subject.is_empty:
        audit.log(
            audit.ENRICH,
            doc_id=doc_id,
            actor=actor.api_key_fp,
            status="no_subject",
        )
        return EnrichResponse(
            doc_id=doc_id,
            subject=None,
            enrichment=None,
            reason="No PERSON or ADDRESS tokens were detected in this document",
        )

    token_map = vault.load_vault(doc_id)
    if token_map is None:
        raise HTTPException(status_code=409, detail="Token vault is missing for this document")

    # Real values exist only in this scope. They are handed to providers and
    # discarded; nothing below persists them.
    name_value = pii.render_value(subject.name_token, token_map)
    address_value = pii.render_value(subject.address_token, token_map)

    home_value = None
    occupation = None
    sources = {}
    if address_value:
        provider = enrichment.get_home_value_provider()
        home_value = provider.lookup(address_value).as_dict()
        sources["home_value"] = provider.name
    if name_value:
        provider = enrichment.get_occupation_provider()
        occupation = provider.lookup(name_value, address_value).as_dict()
        sources["occupation"] = provider.name

    enrichment_record = {
        "home_value": home_value,
        "occupation": occupation,
        "enriched_at": datetime.now(timezone.utc).isoformat(),
    }
    vector_store.set_document_payload(
        doc_id, {"subject": subject.as_dict(), "enrichment": enrichment_record}
    )

    audit.log(
        audit.ENRICH,
        doc_id=doc_id,
        actor=actor.api_key_fp,
        include_pii=reveal.revealed,
        providers=sources,
        has_name=name_value is not None,
        has_address=address_value is not None,
        status="ok",
    )

    subject_out = subject.as_dict()
    revealed_count = 0
    if reveal.revealed:
        subject_out["name"] = name_value
        subject_out["address"] = address_value
        revealed_count = sum(1 for v in (name_value, address_value) if v)
    _audit_reveal([doc_id], reveal, actor, revealed_count)

    return EnrichResponse(
        doc_id=doc_id,
        subject=subject_out,
        enrichment=enrichment_record,
        pii_revealed=reveal.revealed,
    )


@router.delete("/documents/{doc_id}", response_model=DeleteResponse)
def delete_document(
    doc_id: str,
    actor: Actor = Depends(require_api_key),
) -> DeleteResponse:
    record = _get_record_or_404(doc_id)

    vector_store.delete_document(doc_id)
    object_store.delete_document_objects(record.get("s3_key"), record.get("vault_key"))

    audit.log(audit.DELETE, doc_id=doc_id, actor=actor.api_key_fp, status="ok")
    return DeleteResponse(doc_id=doc_id, deleted=True)


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


@router.post("/search", response_model=SearchResponse)
def search(
    request: SearchRequest,
    actor: Actor = Depends(require_api_key),
    reveal: RevealContext = Depends(reveal_context),
) -> SearchResponse:
    query_vector = embeddings.embed_text(request.query)
    raw_results = vector_store.search(query_vector, top_k=request.top_k)

    revealed_count = 0
    if reveal.revealed and raw_results:
        vaults: Dict[str, Dict] = {}
        for result in raw_results:
            doc_id = result["doc_id"]
            if doc_id not in vaults:
                vaults[doc_id] = vault.load_vault(doc_id) or {}
            result["text"], n = pii.render(result["text"], vaults[doc_id])
            revealed_count += n

    doc_ids = sorted({r["doc_id"] for r in raw_results})
    audit.log(
        audit.SEARCH,
        doc_ids=doc_ids,
        actor=actor.api_key_fp,
        include_pii=reveal.revealed,
        top_k=request.top_k,
        results=len(raw_results),
        status="ok",
    )
    _audit_reveal(doc_ids, reveal, actor, revealed_count)

    return SearchResponse(
        query=request.query,
        results=[SearchResultItem(**r) for r in raw_results],
        pii_revealed=reveal.revealed,
    )
