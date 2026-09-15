import logging
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

PII_MODES = ("tokenize", "none")
S3_SSE_MODES = ("AES256", "aws:kms")


def _csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    # --- Auth ---
    # Required. Every route except /health requires X-API-Key to match.
    api_key: str = ""
    # Optional. Required in X-PII-Reveal-Key for include_pii=true. If empty,
    # PII reveal is disabled entirely.
    pii_reveal_key: str = ""

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "pdf_chunks"
    qdrant_documents_collection: str = "pdf_documents"

    # --- S3 ---
    s3_bucket_name: str = "jm-dap-dev-landing-o-use1"
    aws_region: str = "us-east-1"
    s3_sse_mode: str = "AES256"  # AES256 | aws:kms
    s3_kms_key_id: str = ""

    # --- Embeddings / chunking ---
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dim: int = 384
    chunk_size: int = 1000
    chunk_overlap: int = 150

    # --- PII ---
    # tokenize: replace tokenizable PII with <TYPE_n> tokens and redact the rest
    #           before chunk text is embedded/stored.
    # none:     leave text untouched (local debugging only). Detection still
    #           runs so the inventory/vault/enrichment continue to work.
    pii_mode: str = "tokenize"
    spacy_model: str = "en_core_web_sm"
    pii_score_threshold: float = 0.4
    # Reversible (token + vault). ADDRESS is produced by pyap, not Presidio.
    pii_tokenize_entities: str = "PERSON,ADDRESS,PHONE_NUMBER"
    # Irreversible (bare <TYPE> tag, never stored anywhere but the source PDF).
    pii_redact_entities: str = (
        "US_SSN,CREDIT_CARD,DATE_TIME,US_DRIVER_LICENSE,US_BANK_NUMBER,"
        "US_PASSPORT,EMAIL_ADDRESS,IP_ADDRESS,LOCATION"
    )

    # --- Enrichment providers ---
    home_value_provider: str = "mock"
    occupation_provider: str = "mock"

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    @property
    def tokenize_entities(self) -> List[str]:
        return _csv(self.pii_tokenize_entities)

    @property
    def redact_entities(self) -> List[str]:
        return _csv(self.pii_redact_entities)

    @property
    def presidio_entities(self) -> List[str]:
        """Entity types requested from Presidio (ADDRESS is handled by pyap)."""
        return [e for e in self.tokenize_entities + self.redact_entities if e != "ADDRESS"]

    @property
    def reveal_enabled(self) -> bool:
        return bool(self.pii_reveal_key)


def validate_settings(s: Settings) -> None:
    """Fail fast on unsafe or inconsistent configuration."""
    if not s.api_key:
        raise RuntimeError("API_KEY must be set; refusing to start without authentication")
    if s.pii_mode not in PII_MODES:
        raise RuntimeError(f"PII_MODE must be one of {PII_MODES}, got {s.pii_mode!r}")
    if s.s3_sse_mode not in S3_SSE_MODES:
        raise RuntimeError(f"S3_SSE_MODE must be one of {S3_SSE_MODES}, got {s.s3_sse_mode!r}")
    if s.s3_sse_mode == "aws:kms" and not s.s3_kms_key_id:
        raise RuntimeError("S3_KMS_KEY_ID is required when S3_SSE_MODE=aws:kms")
    if s.chunk_overlap >= s.chunk_size:
        raise RuntimeError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")

    overlap = set(s.tokenize_entities) & set(s.redact_entities)
    if overlap:
        raise RuntimeError(f"Entity types cannot be both tokenized and redacted: {sorted(overlap)}")

    if s.pii_mode == "none":
        logger.warning(
            "PII_MODE=none: chunk text is stored and returned UNREDACTED. "
            "Use only for local debugging."
        )
    if not s.reveal_enabled:
        logger.info("PII_REVEAL_KEY not set: include_pii=true is disabled")


settings = Settings()
