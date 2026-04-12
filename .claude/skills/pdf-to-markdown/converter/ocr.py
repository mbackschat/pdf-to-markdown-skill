"""OCR policy and backend helpers for the PDF-to-Markdown converter."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import OcrResolution


def map_lang_codes(langs: list[str], target: str) -> str:
    """Map short language codes to backend-specific OCR language labels."""
    tess_lang_map = {
        "en": "eng",
        "de": "deu",
        "fr": "fra",
        "es": "spa",
        "it": "ita",
        "pt": "por",
        "nl": "nld",
        "ja": "jpn",
        "zh": "chi_sim",
    }
    mac_lang_map = {
        "en": "en-US",
        "de": "de-DE",
        "fr": "fr-FR",
        "es": "es-ES",
        "it": "it-IT",
        "pt": "pt-BR",
        "nl": "nl-NL",
        "ja": "ja-JP",
        "zh": "zh-Hans",
    }

    if target == "mac":
        return ",".join(mac_lang_map.get(lang, lang) for lang in langs)
    if target == "tesseract":
        return "+".join(tess_lang_map.get(lang, lang) for lang in langs)
    return ",".join(langs)


def pick_ocr_backend(engine: str) -> str | None:
    """Choose an installed OCR backend."""
    if engine == "mac":
        if sys.platform == "darwin":
            try:
                import ocrmac  # noqa: F401
            except ImportError:
                return None
            return "mac"
        return None
    if engine == "rapidocr":
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except ImportError:
            return None
        return "rapidocr"
    if engine == "tesseract":
        try:
            import pymupdf

            if pymupdf.get_tessdata():
                return "tesseract"
        except Exception:
            return None
        return None
    if engine == "auto":
        if sys.platform == "darwin":
            backend = pick_ocr_backend("mac")
            if backend:
                return backend
        backend = pick_ocr_backend("rapidocr")
        if backend:
            return backend
        backend = pick_ocr_backend("tesseract")
        if backend:
            return backend
    return None


def resolve_ocr_resolution(
    *,
    force_ocr_requested: bool,
    auto_ocr_requested: bool,
    image_only_pages: int,
    engine: str,
):
    """Resolve explicit OCR policy for this conversion run."""
    from .models import OcrResolution

    auto_ocr = auto_ocr_requested and image_only_pages > 0
    enabled = force_ocr_requested or auto_ocr
    backend = pick_ocr_backend(engine) if enabled else None
    return OcrResolution(
        enabled=enabled,
        force_ocr=force_ocr_requested,
        auto_ocr=auto_ocr,
        backend=backend,
    )


def get_ocr_function(backend: str | None, langs: list[str]):
    """Return an OCR callback compatible with PyMuPDF4LLM."""
    if backend == "mac":
        return build_ocrmac_function(langs)
    if backend == "rapidocr":
        from pymupdf4llm.ocr import rapidocr_api

        return rapidocr_api.exec_ocr
    if backend == "tesseract":
        from pymupdf4llm.ocr import tesseract_api

        return tesseract_api.exec_ocr
    return None


def build_ocrmac_function(langs: list[str]):
    """Build a PyMuPDF4LLM OCR adapter backed by Apple Vision."""
    import pymupdf
    from PIL import Image
    from ocrmac.ocrmac import text_from_image

    font = pymupdf.Font("helv")
    fontname = "F0"

    def adjust_width(text: str, fontsize: float, rect: pymupdf.Rect) -> pymupdf.Matrix:
        width = font.text_length(text, fontsize)
        return pymupdf.Matrix(rect.width / width, 1) if width > 0 else pymupdf.Matrix(1, 1)

    def exec_ocr(page, dpi=300, pixmap=None, language="eng", keep_ocr_text=False):
        if pixmap is None:
            pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)

        image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
        preferred_languages = [lang for lang in map_lang_codes(langs, "mac").split(",") if lang]
        results = text_from_image(
            image,
            recognition_level="accurate",
            language_preference=preferred_languages or None,
            confidence_threshold=0.15,
            detail=True,
        )

        if not results:
            return

        page.insert_font(fontname=fontname, fontbuffer=font.buffer)
        matrix = pymupdf.Rect(pixmap.irect).torect(page.rect)

        for text, _confidence, bbox in results:
            if not text or not text.strip():
                continue

            x, y, width, height = bbox
            rect = pymupdf.Rect(
                x * pixmap.width,
                (1.0 - (y + height)) * pixmap.height,
                (x + width) * pixmap.width,
                (1.0 - y) * pixmap.height,
            ) * matrix

            fontsize = max(rect.height, 6)
            mat = adjust_width(text, fontsize, rect)
            page.insert_text(
                rect.bl + (0, -0.15 * fontsize),
                text,
                fontsize=fontsize,
                fontname=fontname,
                # Keep OCR searchable without visibly repainting scanned pages.
                render_mode=3,
                morph=(rect.bl, mat),
            )

    return exec_ocr
