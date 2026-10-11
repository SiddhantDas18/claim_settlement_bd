"""Getting text out of uploaded documents.

Digital PDF -> pdfplumber. Scanned PDF (little or no text layer) -> render pages with pypdfium2, then OCR.
Images -> RapidOCR. Never raises: a failure is returned as method "failed" with the error message.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MIN_CHARS_PER_PAGE = 30  # less than this in the text layer = treat the PDF as a scan
RENDER_SCALE = 2.0  # 72 dpi * 2 = 144 dpi, enough for printed text
MAX_OCR_PAGES = 10


@dataclass
class ExtractedText:
    text: str
    method: str  # "pdf_text" | "pdf_ocr" | "image_ocr" | "failed"
    ocr_confidence: float | None = None  # mean RapidOCR score, 0-1
    error: str | None = None


_engine = None


def _ocr_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR

        _engine = RapidOCR()
    return _engine


def _rows(boxes, texts) -> list[str]:
    """Join OCR boxes that sit on the same line, so 'Total:' and '16,450' end up on one line."""
    items = []
    for box, text in zip(boxes, texts):
        ys = [point[1] for point in box]
        items.append((min(ys), max(ys), min(point[0] for point in box), text))
    items.sort()
    rows: list[list[tuple]] = []
    for top, bottom, left, text in items:
        middle = (top + bottom) / 2
        if rows:
            last_top, last_bottom = rows[-1][0][0], rows[-1][0][1]
            if abs(middle - (last_top + last_bottom) / 2) < (last_bottom - last_top) / 2:
                rows[-1].append((top, bottom, left, text))
                continue
        rows.append([(top, bottom, left, text)])
    return [" ".join(text for *_, text in sorted(row, key=lambda item: item[2])) for row in rows]


def ocr_image(image) -> tuple[str, list[float]]:
    """OCR one PIL image. Returns (text, per-box confidence scores)."""
    import numpy as np

    result = _ocr_engine()(np.asarray(image.convert("RGB")))
    if not result.txts:
        return "", []
    return "\n".join(_rows(result.boxes, result.txts)), [float(score) for score in result.scores]


def _mean(scores: list[float]) -> float | None:
    return round(sum(scores) / len(scores), 4) if scores else None


def _from_pdf(path: Path) -> ExtractedText:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        pages = len(pdf.pages)
        text = "\n\n".join((page.extract_text() or "") for page in pdf.pages)
    if len(text.strip()) >= MIN_CHARS_PER_PAGE * max(pages, 1):
        return ExtractedText(text.strip(), "pdf_text")

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(path)
    page_texts, scores = [], []
    try:
        for index in range(min(len(document), MAX_OCR_PAGES)):
            image = document[index].render(scale=RENDER_SCALE).to_pil()
            page_text, page_scores = ocr_image(image)
            page_texts.append(page_text)
            scores += page_scores
    finally:
        document.close()
    text = "\n\n".join(page_texts).strip()
    return ExtractedText(text, "pdf_ocr", _mean(scores), None if text else "No text found in the scanned PDF.")


def _from_image(path: Path) -> ExtractedText:
    from PIL import Image

    with Image.open(path) as image:
        text, scores = ocr_image(image)
    return ExtractedText(text.strip(), "image_ocr", _mean(scores), None if text.strip() else "No text found in the image.")


def extract_text(path: Path, content_type: str) -> ExtractedText:
    try:
        if content_type == "application/pdf":
            return _from_pdf(path)
        return _from_image(path)
    except Exception as exc:  # a broken file must never break the upload
        log.exception("Text extraction failed for %s", path)
        return ExtractedText("", "failed", None, f"{type(exc).__name__}: {exc}"[:500])
