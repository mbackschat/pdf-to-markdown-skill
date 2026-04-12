import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / ".claude"
    / "skills"
    / "pdf-to-markdown"
    / "pdf_to_markdown.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("pdf_to_markdown", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pdf_to_markdown = load_module()
ocr = sys.modules["converter.ocr"]


class OcrResolutionTests(unittest.TestCase):
    def test_ocr_disabled_without_flags(self):
        with mock.patch.object(ocr, "pick_ocr_backend", return_value="rapidocr"):
            result = pdf_to_markdown.resolve_ocr_resolution(
                force_ocr_requested=False,
                auto_ocr_requested=False,
                image_only_pages=3,
                engine="auto",
            )
        self.assertFalse(result.enabled)
        self.assertFalse(result.force_ocr)
        self.assertFalse(result.auto_ocr)
        self.assertIsNone(result.backend)

    def test_auto_ocr_requires_flag_and_image_only_pages(self):
        with mock.patch.object(ocr, "pick_ocr_backend", return_value="rapidocr"):
            result = pdf_to_markdown.resolve_ocr_resolution(
                force_ocr_requested=False,
                auto_ocr_requested=True,
                image_only_pages=2,
                engine="auto",
            )
        self.assertTrue(result.enabled)
        self.assertFalse(result.force_ocr)
        self.assertTrue(result.auto_ocr)
        self.assertEqual(result.backend, "rapidocr")

    def test_force_ocr_overrides_page_mix(self):
        with mock.patch.object(ocr, "pick_ocr_backend", return_value="mac"):
            result = pdf_to_markdown.resolve_ocr_resolution(
                force_ocr_requested=True,
                auto_ocr_requested=False,
                image_only_pages=0,
                engine="auto",
            )
        self.assertTrue(result.enabled)
        self.assertTrue(result.force_ocr)
        self.assertFalse(result.auto_ocr)
        self.assertEqual(result.backend, "mac")


class HeadingCleanupTests(unittest.TestCase):
    def test_remove_running_headers_keeps_real_heading_depth(self):
        source = "\n".join(
            [
                "## Manual Title",
                "",
                "### Real Section",
                "Body",
                "## Manual Title",
                "",
                "### Another Section",
                "Body",
                "## Manual Title",
                "",
                "### Final Section",
                "Body",
            ]
        )
        cleaned = pdf_to_markdown.remove_running_headers(source)
        self.assertNotIn("## Manual Title", cleaned)
        self.assertIn("### Real Section", cleaned)
        self.assertIn("### Another Section", cleaned)
        self.assertIn("### Final Section", cleaned)


class TocFallbackTests(unittest.TestCase):
    def test_title_only_toc_line_is_conservative(self):
        self.assertTrue(pdf_to_markdown.looks_like_toc_title_only_line("Flag Summary"))
        self.assertFalse(
            pdf_to_markdown.looks_like_toc_title_only_line(
                "This paragraph is too long to be a reliable title-like TOC entry because it reads like prose."
            )
        )
        self.assertFalse(pdf_to_markdown.looks_like_toc_title_only_line("Ends like prose."))


if __name__ == "__main__":
    unittest.main()
