import sys
from pathlib import Path

# The app modules use flat top-level imports (e.g. `from config import
# settings`), matching how the Docker image runs `uvicorn main:app` from
# /app. Insert the app/ directory onto sys.path so tests can import them
# the same way, regardless of the directory pytest is invoked from.
APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import fitz  # noqa: E402
import pytest  # noqa: E402


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
