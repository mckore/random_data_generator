import logging

from fastapi import FastAPI

import config
from api.routes import router
from services import enrichment


def configure_logging() -> None:
    # Audit events are JSON strings emitted on the "audit" logger; they
    # propagate to the root handler so they appear alongside app logs and are
    # capturable in tests. Application logs never include document text or
    # PII values (see services/audit.py).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


configure_logging()
config.validate_settings(config.settings)
enrichment.validate_providers()

app = FastAPI(
    title="PDF RAG Service",
    description=(
        "PoC service for parsing insurance PDFs, tokenizing PII, embedding chunks, "
        "searching via Qdrant, and enriching the document subject via pluggable providers."
    ),
    version="0.2.0",
)

app.include_router(router)
