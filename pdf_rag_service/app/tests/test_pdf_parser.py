import pytest

from services import pdf_parser


def test_chunk_text_basic_splitting():
    text = "a" * 2500
    chunks = pdf_parser.chunk_text(text, chunk_size=1000, chunk_overlap=100)

    assert len(chunks) == 3
    assert all(len(c) <= 1000 for c in chunks)


def test_chunk_text_overlap_content_matches():
    text = "a" * 2500
    chunks = pdf_parser.chunk_text(text, chunk_size=1000, chunk_overlap=100)

    assert chunks[0][-100:] == chunks[1][:100]


def test_chunk_text_returns_empty_list_for_blank_text():
    assert pdf_parser.chunk_text("   \n  ") == []


def test_chunk_text_rejects_overlap_greater_or_equal_to_size():
    with pytest.raises(ValueError):
        pdf_parser.chunk_text("hello world", chunk_size=10, chunk_overlap=10)


def test_chunk_text_short_text_returns_single_chunk():
    chunks = pdf_parser.chunk_text("short text", chunk_size=1000, chunk_overlap=100)
    assert chunks == ["short text"]


def test_extract_pages_returns_one_entry_per_page(sample_pdf_bytes):
    pages = pdf_parser.extract_pages(sample_pdf_bytes)

    assert len(pages) == 2
    assert "Hello world" in pages[0].text
    assert "apples" in pages[1].text


def test_parse_and_chunk_preserves_page_numbers(sample_pdf_bytes):
    chunks = pdf_parser.parse_and_chunk(sample_pdf_bytes)

    assert chunks
    assert {c.page for c in chunks} == {1, 2}
    # chunk_index should restart at 0 for each page
    first_page_indices = [c.chunk_index for c in chunks if c.page == 1]
    assert first_page_indices[0] == 0


def test_parse_and_chunk_handles_blank_pdf(blank_pdf_bytes):
    assert pdf_parser.parse_and_chunk(blank_pdf_bytes) == []
