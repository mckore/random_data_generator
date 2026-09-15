# PDF RAG Service (PoC) — with built-in PII protection

A proof-of-concept service for insurance (P&C-style) PDFs. It extracts and
chunks text, **tokenizes/redacts PII before anything is embedded**, indexes
chunks in Qdrant for semantic search, stores the source PDF (encrypted) in S3,
and can enrich the document's subject with home value / occupation lookups via
pluggable providers.

Design rationale lives in `docs/pii-enrichment-design.md`.

## Stack

- **API**: FastAPI (Uvicorn)
- **PDF extraction**: PyMuPDF (digital-native PDFs only; no OCR)
- **PII detection**: Microsoft Presidio (spaCy `en_core_web_sm`) + `pyap` for US street addresses
- **Embeddings**: local `sentence-transformers` `all-MiniLM-L6-v2` (CPU, no API key)
- **Vector store**: Qdrant — `pdf_chunks` (per chunk) and `pdf_documents` (per document)
- **Object storage**: AWS S3 bucket `jm-dap-dev-landing-o-use1` (`us-east-1`), server-side encrypted
- **Enrichment**: provider interfaces with deterministic `mock` implementations

## How PII is handled

Every page of text passes through the tokenizer **before** chunking and embedding:

| Class | Entity types (default) | What is stored | Reversible? |
|---|---|---|---|
| Tokenized | `PERSON`, `ADDRESS`, `PHONE_NUMBER` | `<PERSON_1>`, `<ADDRESS_1>`, `<PHONE_1>` … | Yes, via the vault + reveal key |
| Redacted | `US_SSN`, `CREDIT_CARD`, `DATE_TIME`, `US_DRIVER_LICENSE`, `US_BANK_NUMBER`, `US_PASSPORT`, `EMAIL_ADDRESS`, `IP_ADDRESS`, `LOCATION` | `<US_SSN>`, `<CREDIT_CARD>` … | No |

- Tokens are **per document**: the same name reuses the same token within a
  document, and numbering restarts for the next document, so tokens cannot be
  joined across documents.
- The token → value mapping (the **vault**) is written as
  `pdfs/{doc_id}/pii_vault.json` next to the PDF in S3, server-side encrypted.
  It is the only place tokenized values exist, and it is deleted with the document.
- Qdrant holds only tokenized/redacted text, a per-document **PII inventory**
  (entity types, counts, pages) and **token stats** (token, type, count, first page).
  Never values.
- **Rendering**: pass `?include_pii=true` **and** the `X-PII-Reveal-Key` header to
  get real values back in a response. The normal API key alone can never reveal
  (split privilege). Every reveal is audited.
- **Audit log**: one JSON line per upload / read / search / enrich / reveal / delete
  on the `audit` logger, containing IDs, counts and key fingerprints — never text
  or values.
- **Erasure**: `DELETE /documents/{doc_id}` purges chunk points, the document
  record, the PDF and the vault.

`PII_MODE=none` disables text rewriting (detection, inventory and vault still
run). It exists for local debugging only and logs a warning at startup.

## Prerequisites

- Docker and Docker Compose
- AWS credentials available locally via `~/.aws/credentials` / `AWS_PROFILE` /
  env vars / IAM role. The container mounts `~/.aws` read-only; no AWS secrets
  live in this repo.
- IAM on the bucket: `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`,
  `s3:HeadBucket` (`s3:ListBucket` on the bucket ARN for HeadBucket). Add
  `kms:GenerateDataKey` / `kms:Decrypt` on the key if you use `S3_SSE_MODE=aws:kms`.
- Recommended bucket hardening (outside this app): default encryption with
  `BucketKeyEnabled`, a bucket policy denying unencrypted puts, versioning off
  or lifecycle rules that honour deletes, and public access blocked.

## Running

```bash
cp .env.example .env
# set API_KEY (and optionally PII_REVEAL_KEY):  openssl rand -hex 32
docker compose up --build
```

The first build downloads `torch`, the embedding model and the spaCy model;
expect a multi-GB image. Subsequent starts are fast (models are cached in the
`model_cache` volume / baked into the image).

API: `http://localhost:8000` (OpenAPI docs at `/docs`).

## Endpoints

All routes except `GET /health` require `X-API-Key`. Routes marked † accept
`?include_pii=true`, which additionally requires `X-PII-Reveal-Key`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Qdrant + S3 connectivity |
| POST | `/documents` | Upload a PDF: tokenize/redact, store PDF + vault (SSE), embed, index; returns `pii_inventory` |
| GET † | `/documents/{doc_id}` | Document record: inventory, token stats, prior subject/enrichment |
| POST † | `/documents/{doc_id}/enrich` | Pick the subject (most frequent person, first address), call providers, store + return results |
| POST † | `/search` | `{"query": "...", "top_k": 5}`; tokenized chunk text unless revealed |
| DELETE | `/documents/{doc_id}` | Erase from Qdrant and S3 (PDF + vault) |

### Examples

```bash
export API_KEY=...            # from .env
export PII_REVEAL_KEY=...     # optional, from .env

# Upload
curl -H "X-API-Key: $API_KEY" -F "file=@policy.pdf" http://localhost:8000/documents
# -> {"doc_id": "...", "pii_inventory": {"PERSON": {"count": 4, "pages": [1,2]}, "US_SSN": {...}}, ...}

# Enrich (tokens only)
curl -X POST -H "X-API-Key: $API_KEY" http://localhost:8000/documents/$DOC/enrich
# -> {"subject": {"name_token": "<PERSON_1>", "address_token": "<ADDRESS_1>", ...},
#     "enrichment": {"home_value": {"estimated_value": 412500.0, "source": "mock", ...},
#                    "occupation": {"occupation": "Electrician", "source": "mock", ...}}}

# Same, rendered (requires the reveal key)
curl -X POST -H "X-API-Key: $API_KEY" -H "X-PII-Reveal-Key: $PII_REVEAL_KEY" \
  "http://localhost:8000/documents/$DOC/enrich?include_pii=true"

# Search
curl -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"query": "water damage claim", "top_k": 3}' http://localhost:8000/search

# Erase
curl -X DELETE -H "X-API-Key: $API_KEY" http://localhost:8000/documents/$DOC
```

## Enrichment providers

`HOME_VALUE_PROVIDER` and `OCCUPATION_PROVIDER` select implementations from the
registries in `app/services/enrichment/__init__.py`. Only `mock` exists today;
its results are deterministic per input and tagged `"source": "mock"`.

To add a vendor (e.g. Zillow Bridge Interactive, ATTOM, People Data Labs):

1. Create `app/services/enrichment/<vendor>.py` implementing
   `HomeValueProvider` and/or `OccupationProvider` from `base.py`.
2. Register it: `HOME_VALUE_PROVIDERS["<vendor>"] = VendorHomeValueProvider`.
3. Set the env var. Provider credentials should come from env/secret manager,
   not from `.env` in the repo.

Providers receive real values resolved from the vault in-process and must not
persist them. Note that Zillow's public API is discontinued and LinkedIn offers
no person-lookup API; both integrations realistically mean a third-party data
vendor, and insurance underwriting use may fall under FCRA/GLBA — confirm
permissible purpose before wiring one in.

## Testing

Tests need no Docker, Qdrant, S3, or embedding model. Presidio + spaCy run for
real on synthetic text; S3 is mocked with `moto`; the vector store, embeddings
and providers are monkeypatched at the API layer.

```bash
cd app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # installs torch too; slow the first time
pytest
```

## Caveats

- Detection recall is not 100%. Presidio with `en_core_web_sm` misses some
  names and over-flags some dates; a missed name stays in Qdrant as plain text.
  Tune with `en_core_web_lg`, `PII_SCORE_THRESHOLD`, or custom recognizers
  (policy numbers, VINs, claim IDs).
- Semantic search by a person's real name or phone won't match — Qdrant holds
  tokens. Searching `<PERSON_1>` within a document does work.
- Subject selection is heuristic (most frequent `PERSON`, first `ADDRESS`); all
  candidate tokens are returned so a caller can override.
- The vault shares a bucket with the PDF. Fine for a PoC; production should use
  a separate bucket/KMS key with narrower IAM.
- Two shared keys are PoC-grade auth. Put an identity provider and TLS in front
  before real use.
- PyMuPDF only extracts text from digital-native PDFs (no OCR).
- Uploads go to a real S3 bucket; clean up test objects (`DELETE` handles both
  PDF and vault).
