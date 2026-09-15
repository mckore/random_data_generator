import os
import sys
from pathlib import Path

# The app modules use flat top-level imports (e.g. `from config import
# settings`), matching how the Docker image runs `uvicorn main:app` from
# /app. Insert the app/ directory onto sys.path so tests can import them
# the same way, regardless of the directory pytest is invoked from.
APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Auth keys must exist before `config`/`main` are imported (the app refuses
# to start without API_KEY). Tests reference these constants.
TEST_API_KEY = "test-api-key"
TEST_REVEAL_KEY = "test-reveal-key"
os.environ.setdefault("API_KEY", TEST_API_KEY)
os.environ.setdefault("PII_REVEAL_KEY", TEST_REVEAL_KEY)
os.environ.setdefault("PII_MODE", "tokenize")
# Never let a developer's .env leak real bucket/key settings into tests.
os.environ.setdefault("S3_BUCKET_NAME", "test-bucket")
os.environ.setdefault("S3_SSE_MODE", "AES256")
# Dummy AWS creds so boto3/moto never touch a real profile.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import fitz  # noqa: E402
import pytest  # noqa: E402

AUTH = {"X-API-Key": TEST_API_KEY}
REVEAL = {"X-API-Key": TEST_API_KEY, "X-PII-Reveal-Key": TEST_REVEAL_KEY}

# Synthetic insurance-style text. The SSN is deliberately non-sequential
# because Presidio invalidates obvious samples like 123-45-6789.
SAMPLE_PII_TEXT = (
    "Named Insured: Jane Doe. SSN 536-90-4399. Phone (555) 123-4567.\n"
    "Property Address: 123 Main Street, Springfield, IL 62704.\n"
    "Jane Doe reported the loss on 03/14/2024 by calling (555) 123-4567.\n"
    "Adjuster: Robert Miles. Card ending 4111 1111 1111 1111."
)


def _build_pdf_bytes(pages_text):
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """A two-page PDF with distinct text on each page."""
    return _build_pdf_bytes(
        [
            "Hello world. " * 50,
            "Second page content about apples and oranges. " * 50,
        ]
    )


@pytest.fixture
def blank_pdf_bytes() -> bytes:
    """A single-page PDF with no text (simulates a scanned/image-only page)."""
    return _build_pdf_bytes([""])


@pytest.fixture
def pii_pdf_bytes() -> bytes:
    """A single-page PDF containing synthetic PII."""
    return _build_pdf_bytes([SAMPLE_PII_TEXT])
