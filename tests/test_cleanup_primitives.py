import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock


SKILL_DIR = (
    Path(__file__).resolve().parents[1]
    / ".claude"
    / "skills"
    / "pdf-to-markdown"
)
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from converter import cleanup, convert, headings, ocr, reference_entries
from converter.models import ConversionContext, MarkdownHeading


class OcrResolutionTests(unittest.TestCase):
    def test_ocr_disabled_without_flags(self):
        with mock.patch.object(ocr, "pick_ocr_backend", return_value="rapidocr"):
            result = ocr.resolve_ocr_resolution(
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
            result = ocr.resolve_ocr_resolution(
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
            result = ocr.resolve_ocr_resolution(
                force_ocr_requested=True,
                auto_ocr_requested=False,
                image_only_pages=0,
                engine="auto",
            )
        self.assertTrue(result.enabled)
        self.assertTrue(result.force_ocr)
        self.assertFalse(result.auto_ocr)
        self.assertEqual(result.backend, "mac")


class ImageOutputTests(unittest.TestCase):
    def test_reset_images_dir_clears_stale_files_and_subdirs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = Path(tmp_dir) / "sample_images"
            nested = images_dir / "old_dir"
            nested.mkdir(parents=True)
            (images_dir / "old.png").write_text("stale", encoding="utf-8")
            (nested / "nested.txt").write_text("stale", encoding="utf-8")

            convert.reset_images_dir(images_dir)

            self.assertTrue(images_dir.exists())
            self.assertEqual(list(images_dir.iterdir()), [])


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
        cleaned = cleanup.remove_running_headers(source)
        self.assertNotIn("## Manual Title", cleaned)
        self.assertIn("### Real Section", cleaned)
        self.assertIn("### Another Section", cleaned)
        self.assertIn("### Final Section", cleaned)

    def test_remove_running_headers_keeps_repeated_section_labels_with_body(self):
        source = "\n".join(
            [
                "## Chapter 1",
                "",
                "### Summary",
                "Body text for chapter one.",
                "",
                "## Chapter 2",
                "",
                "### Summary",
                "Body text for chapter two.",
                "",
                "## Chapter 3",
                "",
                "### Summary",
                "Body text for chapter three.",
            ]
        )
        cleaned = cleanup.remove_running_headers(source)
        self.assertEqual(cleaned.count("### Summary"), 3)
        self.assertIn("Body text for chapter one.", cleaned)
        self.assertIn("Body text for chapter two.", cleaned)
        self.assertIn("Body text for chapter three.", cleaned)

    def test_remove_running_headers_uses_page_band_signal_when_available(self):
        source = "\n".join(
            [
                "## C-MANSHIP COMPLETE – by CLAYTON WALNUT",
                "",
                "Body page one.",
                "",
                "## C-MANSHIP COMPLETE – by CLAYTON WALNUT",
                "",
                "### What about the Disks?",
                "Body page two.",
                "",
                "## C-MANSHIP COMPLETE – by CLAYTON WALNUT",
                "",
                "Body page three.",
            ]
        )
        context = ConversionContext(pdf_path=Path("dummy.pdf"), page_numbers=None)
        fake_headings = [
            MarkdownHeading(0, "C-MANSHIP COMPLETE – by CLAYTON WALNUT", [], "a", 2),
            MarkdownHeading(4, "C-MANSHIP COMPLETE – by CLAYTON WALNUT", [], "b", 2),
            MarkdownHeading(6, "What about the Disks?", [], "c", 3),
            MarkdownHeading(9, "C-MANSHIP COMPLETE – by CLAYTON WALNUT", [], "d", 2),
        ]
        fake_matches = {
            0: {"y0": 41.7, "size": 14.0},
            1: {"y0": 41.7, "size": 14.0},
            2: {"y0": 176.0, "size": 18.0},
            3: {"y0": 41.7, "size": 14.0},
        }
        with mock.patch.object(cleanup, "extract_markdown_headings", return_value=fake_headings), mock.patch.object(
            cleanup, "match_headings_to_source_lines", return_value=fake_matches
        ):
            cleaned = cleanup.remove_running_headers(source, context)
        self.assertNotIn("C-MANSHIP COMPLETE – by CLAYTON WALNUT", cleaned)
        self.assertIn("### What about the Disks?", cleaned)

    def test_reference_entry_signatures_are_demoted_from_headings(self):
        source = "\n".join(
            [
                "## GEMDOS Function Reference",
                "",
                "### Cauxin()",
                "",
                "### WORD Cauxin(VOID)",
                "",
                "### OPCODE",
                "",
                "Body",
                "",
                "### Cauxis()",
                "",
                "### WORD Cauxis(VOID)",
            ]
        )
        context = ConversionContext(pdf_path=Path("dummy.pdf"), page_numbers=None)
        fake_headings = [
            MarkdownHeading(0, "GEMDOS Function Reference", [], "gemdos-function-reference", 2),
            MarkdownHeading(2, "Cauxin()", [], "cauxin", 3),
            MarkdownHeading(4, "WORD Cauxin(VOID)", [], "word-cauxin-void", 3),
            MarkdownHeading(6, "OPCODE", [], "opcode", 3),
            MarkdownHeading(10, "Cauxis()", [], "cauxis", 3),
            MarkdownHeading(12, "WORD Cauxis(VOID)", [], "word-cauxis-void", 3),
        ]
        fake_matches = {
            0: {"page_no": 58, "x0": 50.0, "y0": 40.0, "size": 18.0, "text": "GEMDOS Function Reference"},
            1: {"page_no": 59, "x0": 50.5, "y0": 48.6, "size": 20.0, "text": "Cauxin()"},
            2: {"page_no": 59, "x0": 50.5, "y0": 85.1, "size": 10.0, "text": "WORD Cauxin( VOID )"},
            3: {"page_no": 59, "x0": 50.5, "y0": 157.3, "size": 10.0, "text": "OPCODE"},
            4: {"page_no": 59, "x0": 50.5, "y0": 436.1, "size": 20.0, "text": "Cauxis()"},
            5: {"page_no": 59, "x0": 50.5, "y0": 472.6, "size": 10.0, "text": "WORD Cauxis( VOID )"},
        }
        fake_page_lines = [
            {"x0": 399.5, "y0": 20.8, "size": 10.0, "text": "Cauxin() - 2.39"},
            {"x0": 50.5, "y0": 48.6, "size": 20.0, "text": "Cauxin()"},
            {"x0": 50.5, "y0": 85.1, "size": 10.0, "text": "WORD Cauxin( VOID )"},
            {"x0": 50.5, "y0": 157.3, "size": 10.0, "text": "OPCODE"},
            {"x0": 137.0, "y0": 157.4, "size": 10.0, "text": "3 (0x03)"},
            {"x0": 50.5, "y0": 182.3, "size": 10.0, "text": "AVAILABILITY"},
            {"x0": 137.0, "y0": 182.1, "size": 10.0, "text": "All GEMDOS versions."},
            {"x0": 50.5, "y0": 207.3, "size": 10.0, "text": "BINDING"},
            {"x0": 137.0, "y0": 207.2, "size": 10.0, "text": "move.w #$3,-(sp)"},
            {"x0": 50.5, "y0": 436.1, "size": 20.0, "text": "Cauxis()"},
            {"x0": 50.5, "y0": 472.6, "size": 10.0, "text": "WORD Cauxis( VOID )"},
        ]
        with mock.patch.object(
            reference_entries, "extract_markdown_headings", return_value=fake_headings
        ), mock.patch.object(
            reference_entries, "match_headings_to_source_lines", return_value=fake_matches
        ), mock.patch.object(
            reference_entries, "extract_page_style_lines", return_value=fake_page_lines
        ):
            cleaned = reference_entries.normalize_reference_entry_headings(source, context)
        self.assertIn("### Cauxin()", cleaned)
        self.assertIn("\nWORD Cauxin(VOID)\n", cleaned)
        self.assertIn("\nOPCODE\n", cleaned)
        self.assertIn("### Cauxis()", cleaned)
        self.assertTrue(cleaned.rstrip().endswith("WORD Cauxis(VOID)"))
        self.assertNotIn("### WORD Cauxin(VOID)", cleaned)
        self.assertNotIn("### OPCODE", cleaned)
        self.assertNotIn("### WORD Cauxis(VOID)", cleaned)

    def test_structured_headings_survive_contents_page_match(self):
        source = "\n".join(
            [
                "## CHAPTER 1. INTRODUCTION",
                "",
                "### 1.1 GENERAL INFORMATION",
                "",
                "## CHAPTER 2. USING THE MULTISYNC 3D",
            ]
        )
        context = ConversionContext(pdf_path=Path("dummy.pdf"), page_numbers=None)
        fake_headings = [
            MarkdownHeading(0, "CHAPTER 1. INTRODUCTION", [], "chapter-1-introduction", 2),
            MarkdownHeading(2, "1.1 GENERAL INFORMATION", [], "11-general-information", 3),
            MarkdownHeading(4, "CHAPTER 2. USING THE MULTISYNC 3D", [], "chapter-2-using", 2),
        ]
        fake_matches = {
            0: {"page_no": 100, "x0": 72.0, "y0": 135.8, "size": 14.0, "text": "CHAPTER 1.  INTRODUCTION"},
            1: {"page_no": 100, "x0": 72.0, "y0": 151.7, "size": 12.0, "text": "1.1  GENERAL INFORMATION"},
            2: {"page_no": 100, "x0": 72.0, "y0": 205.1, "size": 14.0, "text": "CHAPTER 2.  USING THE MULTISYNC 3D"},
        }
        fake_page_lines = [
            {"x0": 72.0, "y0": 72.8, "size": 18.0, "text": "NEC Multisync 3D User's Manual"},
            {"x0": 72.0, "y0": 102.9, "size": 14.0, "text": "TABLE OF CONTENTS"},
            {"x0": 72.0, "y0": 135.8, "size": 14.0, "text": "CHAPTER 1.  INTRODUCTION"},
            {"x0": 72.0, "y0": 151.7, "size": 12.0, "text": "1.1  GENERAL INFORMATION"},
            {"x0": 72.0, "y0": 205.1, "size": 14.0, "text": "CHAPTER 2.  USING THE MULTISYNC 3D"},
            {"x0": 72.0, "y0": 221.1, "size": 12.0, "text": "2.1  GETTING ACQUAINTED"},
        ]
        with mock.patch.object(
            reference_entries, "extract_markdown_headings", return_value=fake_headings
        ), mock.patch.object(
            reference_entries, "match_headings_to_source_lines", return_value=fake_matches
        ), mock.patch.object(
            reference_entries, "extract_page_style_lines", return_value=fake_page_lines
        ):
            cleaned = reference_entries.normalize_reference_entry_headings(source, context)
        self.assertIn("## CHAPTER 1. INTRODUCTION", cleaned)
        self.assertIn("### 1.1 GENERAL INFORMATION", cleaned)

    def test_dense_short_labels_are_demoted_from_headings(self):
        source = "\n".join(
            [
                "#### 2.2.1 USER CONTROL NAMES AND OPERATIONS",
                "",
                "## POWER SWITCH",
                "",
                "## BRIGHTNESS CONTROL",
            ]
        )
        context = ConversionContext(pdf_path=Path("dummy.pdf"), page_numbers=None)
        fake_headings = [
            MarkdownHeading(0, "2.2.1 USER CONTROL NAMES AND OPERATIONS", [], "221-user-control", 4),
            MarkdownHeading(2, "POWER SWITCH", [], "power-switch", 2),
            MarkdownHeading(4, "BRIGHTNESS CONTROL", [], "brightness-control", 2),
        ]
        fake_matches = {
            0: {"page_no": 102, "x0": 72.0, "y0": 362.6, "size": 12.0, "text": "2.2.1 USER CONTROL NAMES AND OPERATIONS"},
            1: {"page_no": 102, "x0": 72.0, "y0": 376.3, "size": 10.0, "text": "n POWER SWITCH"},
            2: {"page_no": 102, "x0": 72.0, "y0": 406.2, "size": 10.0, "text": "o BRIGHTNESS CONTROL"},
        }
        fake_page_lines = [
            {"x0": 72.0, "y0": 362.6, "size": 12.0, "text": "2.2.1 USER CONTROL NAMES AND OPERATIONS"},
            {"x0": 72.0, "y0": 376.3, "size": 10.0, "text": "n POWER SWITCH"},
            {"x0": 72.0, "y0": 406.2, "size": 10.0, "text": "o BRIGHTNESS CONTROL"},
            {"x0": 72.0, "y0": 436.1, "size": 10.0, "text": "p CONTRAST CONTROL"},
            {"x0": 72.0, "y0": 466.0, "size": 10.0, "text": "q COLOR SWITCH"},
            {"x0": 72.0, "y0": 568.2, "size": 10.0, "text": "r MODE SWITCH"},
            {"x0": 72.0, "y0": 72.0, "size": 8.0, "text": "Body"},
            {"x0": 158.4, "y0": 495.7, "size": 8.0, "text": "COLOR MODE"},
        ]
        with mock.patch.object(
            reference_entries, "extract_markdown_headings", return_value=fake_headings
        ), mock.patch.object(
            reference_entries, "match_headings_to_source_lines", return_value=fake_matches
        ), mock.patch.object(
            reference_entries, "extract_page_style_lines", return_value=fake_page_lines
        ):
            cleaned = reference_entries.normalize_reference_entry_headings(source, context)
        self.assertIn("#### 2.2.1 USER CONTROL NAMES AND OPERATIONS", cleaned)
        self.assertNotIn("## POWER SWITCH", cleaned)
        self.assertIn("\nPOWER SWITCH\n", cleaned)
        self.assertNotIn("## BRIGHTNESS CONTROL", cleaned)
        self.assertTrue(cleaned.rstrip().endswith("BRIGHTNESS CONTROL"))

    def test_unmatched_symbol_prefixed_label_run_is_demoted(self):
        source = "\n".join(
            [
                "## � POWER SWITCH",
                "",
                "## � BRIGHTNESS CONTROL",
            ]
        )
        cleaned = reference_entries.demote_unmatched_label_heading_runs(source, {})
        self.assertNotIn("## � POWER SWITCH", cleaned)
        self.assertNotIn("## � BRIGHTNESS CONTROL", cleaned)
        self.assertIn("POWER SWITCH", cleaned)
        self.assertIn("BRIGHTNESS CONTROL", cleaned)


class ListingCleanupTests(unittest.TestCase):
    def test_merge_fenced_block_with_code_bullets(self):
        source = "\n".join(
            [
                "#### Program Listing #1",
                "",
                "```",
                "10   'ST CHECK typing validator by Clayton Walnum",
                "```",
                "",
                "- `20   'based on a program by Istvan Mohos and Tom Hudson`",
                "",
                "- `30   if peek(systab)=1 then cl=17 else cl=32`",
            ]
        )
        cleaned = cleanup.merge_fenced_block_with_code_bullets(source)
        self.assertIn("```", cleaned)
        self.assertIn("10   'ST CHECK typing validator by Clayton Walnum", cleaned)
        self.assertIn("20   'based on a program by Istvan Mohos and Tom Hudson", cleaned)
        self.assertIn("30   if peek(systab)=1 then cl=17 else cl=32", cleaned)
        self.assertNotIn("- `20", cleaned)
        self.assertNotIn("- `30", cleaned)


class TocFallbackTests(unittest.TestCase):
    def test_title_only_toc_line_is_conservative(self):
        self.assertTrue(headings.looks_like_toc_title_only_line("Flag Summary"))
        self.assertFalse(
            headings.looks_like_toc_title_only_line(
                "This paragraph is too long to be a reliable title-like TOC entry because it reads like prose."
            )
        )
        self.assertFalse(headings.looks_like_toc_title_only_line("Ends like prose."))

    def test_promote_structured_plaintext_headings_is_conservative(self):
        source = "\n".join(
            [
                "Intro paragraph.",
                "",
                "CHAPTER 1. INTRODUCTION",
                "",
                "1.1 GENERAL INFORMATION",
                "",
                "2.2.1 USER CONTROL NAMES AND OPERATIONS",
                "",
                "1. Make sure the power is off.",
                "",
                "Normal sentence without heading punctuation",
                "",
            ]
        )
        cleaned = headings.promote_structured_plaintext_headings(source)
        self.assertIn("## CHAPTER 1. INTRODUCTION", cleaned)
        self.assertIn("### 1.1 GENERAL INFORMATION", cleaned)
        self.assertIn("#### 2.2.1 USER CONTROL NAMES AND OPERATIONS", cleaned)
        self.assertIn("\n1. Make sure the power is off.\n", cleaned)
        self.assertNotIn("## 1. Make sure the power is off.", cleaned)


if __name__ == "__main__":
    unittest.main()
