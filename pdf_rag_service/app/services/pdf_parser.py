from dataclasses import dataclass
from typing import List

import fitz  # PyMuPDF

from config import settings


@dataclass
class PageText:
    page: int
    text: str


@dataclass
class Chunk:
    page: int
    chunk_index: int
    text: str


def extract_pages(pdf_bytes: bytes) -> List[PageText]:
    """Extract raw text per page from a PDF file's bytes."""
    pages: List[PageText] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page_number, page in enumerate(doc, start=1):
            text = page.get_text("text")
            pages.append(PageText(page=page_number, text=text))
    return pages


def chunk_text(
    text: str,
    chunk_size: int = None,
    chunk_overlap: int = None,
) -> List[str]:
    """Split text into overlapping fixed-size character chunks."""
    chunk_size = chunk_size or settings.chunk_size
    chunk_overlap = chunk_overlap or settings.chunk_overlap

    text = text.strip()
    if not text:
        return []

    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    chunks = []
    start = 0
    text_len = len(text)
    step = chunk_size - chunk_overlap

    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == text_len:
            break
        start += step

    return chunks


def parse_and_chunk(pdf_bytes: bytes) -> List[Chunk]:
    """Extract text per page and split into chunks, preserving page numbers."""
    pages = extract_pages(pdf_bytes)
    chunks: List[Chunk] = []
    for page in pages:
        page_chunks = chunk_text(page.text)
        for idx, chunk in enumerate(page_chunks):
            chunks.append(Chunk(page=page.page, chunk_index=idx, text=chunk))
    return chunks
