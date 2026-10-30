from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError


class PDFProcessingError(RuntimeError):
    """A PDF could not be read as usable text."""


MAX_PDF_PAGES = 250
MAX_EXTRACTED_TEXT_CHARACTERS = 500_000


def extract_pdf_pages(
    pdf_bytes: bytes,
    max_pages: int = MAX_PDF_PAGES,
    max_text_characters: int = MAX_EXTRACTED_TEXT_CHARACTERS,
) -> list[tuple[int, str]]:
    if not pdf_bytes:
        raise PDFProcessingError("The uploaded PDF is empty.")
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        if reader.is_encrypted:
            raise PDFProcessingError("This PDF is password-protected. Upload an unlocked copy.")
        if len(reader.pages) > max_pages:
            raise PDFProcessingError(
                f"This PDF exceeds the {max_pages}-page limit. Split it into smaller files and try again."
            )
        pages = []
        extracted_characters = 0
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                extracted_characters += len(text)
                if extracted_characters > max_text_characters:
                    raise PDFProcessingError(
                        "This PDF contains too much selectable text to index safely. Split it into smaller files and try again."
                    )
                pages.append((page_number, text))
    except PDFProcessingError:
        raise
    except (PdfReadError, PdfStreamError, ValueError, OSError) as exc:
        raise PDFProcessingError("Could not read this PDF. Check that it is a valid, unencrypted file.") from exc
    if not pages:
        raise PDFProcessingError(
            "No selectable text was found in the PDF. Scanned documents need OCR; paste extracted text instead."
        )
    return pages
