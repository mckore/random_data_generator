# PDF Entity Extraction + Enrichment with Built-in PII Protection

> **Status: design only — not yet implemented.** Saved for reference. Builds on the
> baseline service in `pdf_rag_service/` (PR #1). Also tracked as future
> enhancements: an MCP server wrapper over `services/`, and a caching layer
> (Redis) once query/upload load justifies it.

## Problem
Extend the PDF RAG PoC so that, for an uploaded document, the service can extract an
individual's **name** and **address** from the PDF text, then enrich them with
**home value** (Zillow-style) and **occupation** (LinkedIn-style) lookups. Real API
access for both lookups doesn't exist yet, so enrichment must be built behind
pluggable provider interfaces with working mock providers.

The documents are **insurance (P&C-style) records containing PII**, so PII protection
must be built into the pipeline: detection, tokenization/redaction before anything
reaches the searchable store, encrypted storage, authenticated access, auditability,
and erasure.

Specifically: **name, address, and phone are allowed to flow through the system as
reversible tokens with IDs** (e.g. `<PERSON_1>`), rendered back to real values only at
runtime for callers holding a separate reveal key. All other PII types are
irreversibly redacted.

## Current state (baseline, PR #1)
`pdf_rag_service/` has upload → parse → chunk → embed → Qdrant, with raw PDFs in S3.
Relevant constraints:
- Qdrant only holds per-chunk points; there is no document-level record
  (`GET /documents/{doc_id}` currently returns `filename=""` for this reason).
- `object_store.py` can only upload; the token vault design needs get/put/delete of a
  JSON sidecar object per document (`s3:GetObject`, `s3:DeleteObject` added to IAM).
- Zillow's public API is discontinued and LinkedIn has no person-lookup API; both are
  realistically third-party vendors later (Zillow Bridge Interactive, People Data
  Labs, etc.). Providers are therefore abstractions, not concrete integrations.

## Design

### PII protection (Presidio)
- **Engine**: Microsoft Presidio (`presidio-analyzer` + `presidio-anonymizer`)
  configured to use spaCy `en_core_web_sm` (pinned wheel; smaller than the default
  `lg` model, adequate for PoC). One shared `AnalyzerEngine` in `app/services/pii.py`.
- **Entity set** (configurable, default): `PERSON, LOCATION, US_SSN, PHONE_NUMBER,
  EMAIL_ADDRESS, DATE_TIME, CREDIT_CARD, US_DRIVER_LICENSE, US_BANK_NUMBER,
  US_PASSPORT, IP_ADDRESS`. No medical recognizers (P&C scope, not PHI).
- **Two classes of PII, decided per entity type:**
  - **Tokenized (reversible)** — `PII_TOKENIZE_ENTITIES` default
    `PERSON, ADDRESS, PHONE_NUMBER`. Each distinct value is replaced with a stable
    per-document token `<PERSON_1>`, `<ADDRESS_1>`, `<PHONE_1>` … (same normalized
    value → same token within a doc; numbering restarts per doc, so tokens are not
    linkable across documents). Names/phones come from Presidio; `ADDRESS` spans come
    from `pyap` because Presidio `LOCATION` does not reliably capture full street
    addresses.
  - **Redacted (irreversible)** — every other detected type (`US_SSN, CREDIT_CARD,
    DATE_TIME, US_DRIVER_LICENSE, US_BANK_NUMBER, US_PASSPORT, EMAIL_ADDRESS,
    IP_ADDRESS, LOCATION`) is replaced with a bare `<US_SSN>`-style tag. Not
    recoverable from anything the service stores except the original PDF in S3.
- Tokenization/redaction is applied to page text **before chunking, embedding, and
  upsert**, so Qdrant never contains raw name/address/phone/SSN values. It also makes
  token-based coreference visible in search results (`<PERSON_1>` appears
  consistently across a doc's chunks).
- **Token vault**: the `token → {type, value}` mapping is written as an encrypted
  JSON sidecar `pdfs/{doc_id}/pii_vault.json` in S3 (SSE, KMS when configured),
  alongside the PDF. It is the only place token values live. Deleted with the
  document.
- **Runtime rendering**: `include_pii=true` on `GET /documents/{doc_id}`,
  `POST /documents/{doc_id}/enrich`, and `POST /search` loads the relevant vault(s)
  and substitutes real values for tokens in the response (chunk text, entities,
  enrichment subject). Requires the **separate `X-PII-Reveal-Key` header** matching
  `PII_REVEAL_KEY` (split privilege: the normal `X-API-Key` alone cannot reveal).
  Missing/wrong reveal key → `403`; if `PII_REVEAL_KEY` is unset, reveal is disabled
  entirely.
- `PII_MODE` = `tokenize` (default) | `none` (raw everywhere, local debugging only,
  startup warning). A `mask` mode was considered and dropped — tokens already provide
  a safe display form.
- **PII inventory**: at upload, `pii_inventory: {ENTITY_TYPE: {count, pages: [..]}}`
  (all types) plus `token_stats: {token: {type, count, first_page}}` are stored on
  the document record. Types/counts/pages/tokens only — never values.
- **S3 encryption**: every put sets `ServerSideEncryption` — `AES256` by default, or
  `aws:kms` + `SSEKMSKeyId` when `S3_KMS_KEY_ID` is set (`S3_SSE_MODE=AES256|aws:kms`).
  The bucket should also enforce default encryption + `BucketKeyEnabled` and deny
  unencrypted puts via bucket policy (documented, not managed by the app).
- **API key auth**: FastAPI dependency requiring `X-API-Key` matching `API_KEY`
  (constant-time compare). Applied to every route except `GET /health` (needed for
  container healthchecks). App refuses to start if `API_KEY` is empty. A second
  dependency checks `X-PII-Reveal-Key` against `PII_REVEAL_KEY` only when
  `include_pii=true`. Both keys come from `.env` (never committed).
- **Audit log**: `audit` logger emitting one structured JSON line per PII-relevant
  event — `document.upload`, `document.read`, `document.search`, `document.enrich`,
  `document.reveal`, `document.delete` — with `ts, event, doc_id(s), actor` (sha256
  prefix of the API key, not the key), `reveal_actor` (sha256 prefix of the reveal
  key when used), `pii_mode`, `include_pii`, `tokens_revealed` (count),
  `entity_type_counts`, `provider sources`, `status`. Application logging never
  emits chunk text, token values, or enrichment values.
- **Erasure**: `DELETE /documents/{doc_id}` removes chunk points (filter on
  `doc_id`), the document point, the PDF, and the vault sidecar, audited; `404` if
  unknown.

### Entity extraction (no API keys, no PDF re-read)
- Because tokenization already identified names/addresses at upload, enrichment works
  from the document record's `token_stats` plus the vault — the PDF is not
  downloaded again.
- **Name**: most frequent `PERSON_*` token (ties → lowest `first_page`, then lowest
  token number); all `PERSON_*` tokens returned as candidates.
- **Address**: `ADDRESS_1` (first-seen address) as primary; all `ADDRESS_*` tokens as
  candidates.
- Providers are called with the real values resolved from the vault in-process; the
  values are not persisted anywhere new and are not returned unless the reveal key
  is present.
- Module: `app/services/entity_extractor.py` with
  `select_subject(token_stats) -> Subject{name_token, name_candidates, address_token, address_candidates}`;
  Presidio/pyap span detection itself lives in `services/pii.py`. Heuristic and
  PoC-grade; swapping in LLM extraction later is a one-module change.

### Enrichment providers (pluggable)
- `app/services/enrichment/base.py`:
  `HomeValueProvider.lookup(address) -> HomeValueResult{estimated_value, currency, source, raw}`
  and
  `OccupationProvider.lookup(name, address) -> OccupationResult{occupation, employer, source, raw}`
  as `Protocol`s.
- `app/services/enrichment/mock.py`: deterministic mock implementations (seeded from a
  hash of the input so repeated calls agree), `source="mock"` so results are never
  mistaken for real data.
- `app/services/enrichment/__init__.py`: factory reading `HOME_VALUE_PROVIDER` /
  `OCCUPATION_PROVIDER` env vars (default `mock`); unknown value raises at startup.
  Adding e.g. a `bridge` or `pdl` provider = implement the protocol and register it.

### Document-level record in Qdrant
- New collection `pdf_documents` (same 384-dim cosine config), one point per
  `doc_id`, vector = mean of the doc's chunk embeddings, payload =
  `{doc_id, filename, num_pages, num_chunks, s3_key, vault_key, pii_mode, pii_inventory, token_stats, subject?, enrichment?}`.
  `subject` holds token references only; `enrichment` holds provider outputs (home
  value, occupation/employer, sources) keyed to those tokens.
- Created during `POST /documents`; updated (payload only, via `set_payload`) by the
  enrich endpoint. `GET /documents/{doc_id}` switches to read this record, fixing
  the empty filename.

### API
All routes require `X-API-Key` except `/health`; `include_pii=true` additionally
requires `X-PII-Reveal-Key`.
- `POST /documents` — unchanged contract; runs Presidio + pyap per page,
  tokenizes/redacts, builds `pii_inventory` + `token_stats`, uploads PDF and vault
  sidecar to S3 with SSE, embeds tokenized chunks, writes the document-level point,
  audits.
- `POST /documents/{doc_id}/enrich?include_pii=false` — selects subject tokens from
  `token_stats`, resolves values from the vault in-process, calls both providers,
  stores `subject` + `enrichment` on the document record, returns them with tokens
  (or rendered values with reveal key). `404` if doc unknown; 200 with
  `enrichment: null` and a `reason` if no `PERSON_*`/`ADDRESS_*` tokens exist.
- `GET /documents/{doc_id}?include_pii=false` — full document record incl.
  `pii_inventory`, `token_stats`, prior `subject`/`enrichment`; tokens rendered to
  values only with reveal key.
- `POST /search` — gains optional `include_pii`; with reveal key, tokens in returned
  chunk text are rendered from each result's document vault (vaults loaded per
  distinct `doc_id` in the result set).
- `DELETE /documents/{doc_id}` — erasure across Qdrant + S3 (PDF + vault).

### Config additions
`API_KEY` (required), `PII_REVEAL_KEY` (optional; reveal disabled if unset),
`PII_MODE=tokenize`, `PII_TOKENIZE_ENTITIES=PERSON,ADDRESS,PHONE_NUMBER`,
`PII_REDACT_ENTITIES=<comma list>`, `S3_SSE_MODE=AES256`, `S3_KMS_KEY_ID=` (optional),
`HOME_VALUE_PROVIDER=mock`, `OCCUPATION_PROVIDER=mock`,
`QDRANT_DOCUMENTS_COLLECTION=pdf_documents`, `SPACY_MODEL=en_core_web_sm` — in
`config.py`, `docker-compose.yml`, `.env.example`.

### Dependencies
`presidio-analyzer`, `presidio-anonymizer`, `spacy` (3.7.x) + pinned `en_core_web_sm`
wheel URL, `pyap` added to `requirements.txt` (wheel URL is pip-installable directly,
so tests and Docker share one list).

### Files touched
- **New**: `services/pii.py` (Presidio analyzer + pyap span detection,
  tokenize/redact, inventory, `render(text, vault)`), `services/vault.py`
  (build/serialize/load vault; S3 sidecar put/get/delete via `object_store`),
  `services/entity_extractor.py` (`select_subject`),
  `services/enrichment/{__init__,base,mock}.py`, `api/auth.py` (API-key +
  reveal-key dependencies), `services/audit.py` (JSON audit logger),
  `tests/test_pii.py`, `tests/test_vault.py`, `tests/test_auth.py`,
  `tests/test_entity_extractor.py`, `tests/test_enrichment.py`
- **Modified**: `services/object_store.py` (generic `put_object/get_object/delete_object`
  with SSE, plus `upload_pdf`/`delete_document_objects`), `services/vector_store.py`
  (documents collection: `upsert_document`, `get_document`, `set_document_payload`,
  `delete_document`), `api/routes.py`, `models/schemas.py`, `config.py`, `main.py`
  (startup validation, logging config), `requirements.txt`, `Dockerfile`,
  `docker-compose.yml`, `.env.example`, `README.md` (two keys, tokenization model,
  IAM `PutObject/GetObject/DeleteObject/HeadBucket` + KMS if used, bucket encryption
  policy guidance, provider extension guide, caveats), `tests/test_api.py`,
  `tests/test_object_store.py`

## Testing
- `test_pii.py`: real Presidio + pyap on synthetic text — name/phone/address become
  `<PERSON_1>`/`<PHONE_1>`/`<ADDRESS_1>`; repeated value reuses its token; SSN/credit
  card become bare `<US_SSN>`/`<CREDIT_CARD>` and are absent from the vault;
  `render()` restores tokenized values but not redacted ones; inventory/token_stats
  contain no values.
- `test_vault.py` (moto): vault round-trips through S3 with `ServerSideEncryption`
  set; deleted with the document; missing vault handled.
- `test_auth.py`: missing/wrong `X-API-Key` → 401; `/health` open;
  `include_pii=true` without/with wrong `X-PII-Reveal-Key` → 403; reveal disabled
  when `PII_REVEAL_KEY` unset; app startup fails without `API_KEY`.
- `test_entity_extractor.py`: most frequent `PERSON_*` wins with tie-break rules;
  `ADDRESS_1` primary; empty `token_stats` → no subject.
- `test_enrichment.py`: mock providers deterministic and tagged `source="mock"`;
  factory defaults to mocks and rejects unknown names.
- `test_object_store.py` (moto): put includes `ServerSideEncryption` (AES256 and KMS
  variants), get round-trips, delete removes PDF + vault.
- `test_api.py`: upload stores tokenized chunk text, inventory, token_stats and
  writes a vault; enrich happy path returns tokens by default and rendered values
  with reveal key; search renders tokens only with reveal key; 404 unknown doc;
  graceful no-subject; `DELETE` purges PDF + vault + points and audits; audit events
  asserted via `caplog` and verified to contain no PII values.
- Full suite runs locally in a venv (no Docker/Qdrant/S3 required; spaCy sm model
  ~12MB).

## Branch / PR
New branch `pdf-entity-enrichment` off `pdf-rag-service`, opened as a stacked PR
targeting `pdf-rag-service` so the diff is reviewable on its own; retarget to `main`
once PR #1 merges.

## Caveats
- Presidio with `en_core_web_sm` will miss some names and over-flag some dates;
  recall is not 100%, so tokenization reduces but does not eliminate raw PII reaching
  Qdrant. A missed name stays in the chunk text as plain text. Upgrading to
  `en_core_web_lg` or adding custom recognizers (policy numbers, VINs, claim IDs) is
  the tuning path.
- Semantic search by a person's actual name/phone won't match (Qdrant holds tokens);
  searching by `<PERSON_1>`-style token within a doc does work.
- The vault sits in the same S3 bucket as the PDF. Acceptable for a PoC (same trust
  boundary as the source document), but production should put vaults behind a
  distinct KMS key/bucket with narrower IAM than the PDF store.
- Two shared keys is still PoC-grade auth; real deployment should use an identity
  provider with a distinct reveal role. TLS termination is assumed in front of the
  app.
- spaCy/pyap extraction will misfire on some layouts (letterheads, multiple parties,
  non-US addresses); results include all candidate tokens for that reason.
- Mock providers return fabricated numbers/titles; they are for pipeline plumbing
  only.
- Enrichment couples PII (name+address) to financial/employment data — insurance
  underwriting use likely falls under FCRA/GLBA plus state privacy law; confirm
  permissible purpose before wiring real vendors.
