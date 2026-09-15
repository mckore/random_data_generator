# PDF RAG Service (PoC)

A proof-of-concept service that ingests PDFs, extracts and chunks their text,
embeds the chunks locally, and indexes them in Qdrant for semantic search.
Raw PDFs are stored in a real AWS S3 bucket.

## Stack

- **API**: FastAPI (Uvicorn)
- **PDF extraction**: PyMuPDF (digital-native PDFs only; no OCR)
- **Embeddings**: local `sentence-transformers` model `all-MiniLM-L6-v2` (CPU, no API key)
- **Vector store**: Qdrant, collection `pdf_chunks`, cosine distance
- **Object storage**: AWS S3 bucket `jm-dap-dev-landing-o-use1` (region `us-east-1`)

## Prerequisites

- Docker and Docker Compose
- AWS credentials available locally via one of: `~/.aws/credentials` (with a
  profile), `AWS_PROFILE`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` env
  vars, or an IAM role. The app container mounts `~/.aws` read-only and uses
  boto3's default credential chain — no AWS secrets are stored in this repo.
- IAM permissions on the target bucket: `s3:PutObject`, `s3:HeadBucket` (and
  `s3:GetObject` if you plan to extend the service to read PDFs back).

## Running

```bash
cp .env.example .env   # adjust S3_BUCKET_NAME / AWS_REGION / AWS_PROFILE if needed
docker compose up --build
```

The first build downloads `torch` and the embedding model, so it can take a
while and produce a multi-GB image. The embedding model is cached in a named
volume (`model_cache`) so subsequent restarts are fast.

The API is available at `http://localhost:8000`.

## Endpoints

- `GET /health` — checks Qdrant and S3 connectivity
- `POST /documents` — multipart PDF upload; parses, chunks, embeds, and indexes it
- `GET /documents/{doc_id}` — returns the chunk count for a document
- `POST /search` — `{"query": "...", "top_k": 5}`; returns ranked matching chunks

### Example usage

```bash
# Upload a PDF
curl -F "file=@/path/to/document.pdf" http://localhost:8000/documents

# Search
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "termination clause", "top_k": 3}'
```

## Caveats

- PyMuPDF only extracts text from digital-native PDFs; scanned/image-only
  PDFs will yield empty text (OCR is out of scope for this PoC).
- This PoC writes to a real S3 bucket, not a local emulator — uploaded test
  files persist in `jm-dap-dev-landing-o-use1` under `pdfs/<doc_id>/...`.
  Clean up test objects after experimenting.
- Chunking is a simple fixed-size character splitter with overlap; it is not
  layout- or sentence-aware.
