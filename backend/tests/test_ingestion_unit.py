"""Unit tests for extraction, cleaning, scanned detection, and chunking."""

from __future__ import annotations

import pytest
from app.schemas.document import FileType
from app.services.ingestion import (
    CorruptFileError,
    EmptyDocumentError,
    PageText,
    ScannedPDFError,
    build_summary_excerpts,
    chunk_pages,
    clean_text,
    ensure_has_text,
    extract_docx_pages,
    extract_pdf_pages,
)

from tests.conftest import make_docx, make_pdf


def test_clean_text_normalizes_whitespace_and_hyphenation() -> None:
    raw = "Depen-\ndency   injection\t is\n\n\n\nuseful.\x00  "
    assert clean_text(raw) == "Dependency injection is\n\nuseful."


def test_extract_pdf_pages_keeps_page_numbers() -> None:
    data = make_pdf(["Alpha content on the first page.", "Beta content on the second page."])
    pages = extract_pdf_pages(data)
    assert [p.page_number for p in pages] == [1, 2]
    assert "Alpha" in pages[0].text
    assert "Beta" in pages[1].text


def test_corrupt_pdf_raises() -> None:
    with pytest.raises(CorruptFileError):
        extract_pdf_pages(b"%PDF-1.7 this is not really a pdf")


def test_scanned_pdf_is_rejected() -> None:
    pages = extract_pdf_pages(make_pdf(["", "", ""]))
    with pytest.raises(ScannedPDFError, match="OCR"):
        ensure_has_text(pages, FileType.PDF, min_avg_chars=50)


def test_pdf_with_text_passes() -> None:
    pages = [PageText(1, "x" * 200), PageText(2, "")]  # one blank page is fine
    ensure_has_text(pages, FileType.PDF, min_avg_chars=50)


def test_empty_docx_is_rejected() -> None:
    with pytest.raises(EmptyDocumentError):
        ensure_has_text([], FileType.DOCX, min_avg_chars=50)


def test_extract_docx_splits_into_sections() -> None:
    paragraphs = [f"Paragraph {i} " + "word " * 40 for i in range(20)]  # ~4.3k chars
    pages = extract_docx_pages(make_docx(paragraphs), chars_per_page=1000)
    assert len(pages) > 1
    assert [p.page_number for p in pages] == list(range(1, len(pages) + 1))
    assert all(len(p.text) <= 1000 for p in pages)
    assert "Paragraph 0" in pages[0].text
    assert "Paragraph 19" in pages[-1].text


def test_corrupt_docx_raises() -> None:
    with pytest.raises(CorruptFileError):
        extract_docx_pages(b"PK\x03\x04garbage", chars_per_page=1000)


def test_chunks_never_cross_pages_and_respect_size() -> None:
    pages = [PageText(1, "a " * 900), PageText(2, "b " * 900), PageText(3, "")]
    chunks = chunk_pages(pages, chunk_size=500, chunk_overlap=50)

    assert all(len(c.text) <= 500 for c in chunks)
    assert {c.page_number for c in chunks} == {1, 2}
    assert all(set(c.text.split()) == {"a"} for c in chunks if c.page_number == 1)
    assert all(set(c.text.split()) == {"b"} for c in chunks if c.page_number == 2)
    # chunk_index is a global 0..n-1 sequence
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_chunk_overlap_is_applied() -> None:
    words = " ".join(f"w{i}" for i in range(300))
    chunks = chunk_pages([PageText(1, words)], chunk_size=200, chunk_overlap=60)
    assert len(chunks) > 2
    first_tail = chunks[0].text.split()[-1]
    assert first_tail in chunks[1].text.split()


def test_summary_excerpts_spread_and_bounded() -> None:
    pages = [PageText(i, f"page {i} " + "text " * 150) for i in range(1, 21)]
    chunks = chunk_pages(pages, chunk_size=800, chunk_overlap=0)
    excerpt = build_summary_excerpts(chunks, max_chars=3000)
    assert len(excerpt) <= 3000 + 10  # joiners
    assert "[page 1]" in excerpt
    assert "[page 1]" != excerpt.split("\n\n")[-1][:8]  # later pages are sampled too
