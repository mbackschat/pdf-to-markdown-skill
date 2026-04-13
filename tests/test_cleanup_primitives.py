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

    def test_move_extracted_images_recreates_destination_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            extraction_images_dir = tmp_path / "tmp_images"
            extraction_images_dir.mkdir()
            (extraction_images_dir / "figure.png").write_text("image", encoding="utf-8")

            images_dir = tmp_path / "final_images"
            convert.move_extracted_images(extraction_images_dir, images_dir)

            self.assertTrue(images_dir.exists())
            self.assertTrue((images_dir / "figure.png").exists())
            self.assertEqual((images_dir / "figure.png").read_text(encoding="utf-8"), "image")


class HeadingCleanupTests(unittest.TestCase):
    def test_escape_literal_angle_brackets_preserves_br_and_code_blocks(self):
        source = "\n".join(
            [
                "Press <b> or <F1>.",
                "**----- Start of picture text -----**<br>",
                "Code sample:",
                "```",
                "if key == <b>:",
                "```",
                "Visit <https://example.com> or write <help@example.com>.",
            ]
        )

        cleaned = cleanup.escape_literal_angle_brackets(source)
        self.assertIn("Press &lt;b&gt; or &lt;F1&gt;.", cleaned)
        self.assertIn("**----- Start of picture text -----**<br>", cleaned)
        self.assertIn("if key == <b>:", cleaned)
        self.assertIn("<https://example.com>", cleaned)
        self.assertIn("<help@example.com>", cleaned)

    def test_cleanup_markdown_can_skip_heading_pipeline(self):
        context = ConversionContext(pdf_path=Path("dummy.pdf"), page_numbers=None)
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            output_path = tmp_path / "out.md"
            images_dir = tmp_path / "images"
            images_dir.mkdir()

            with mock.patch.object(cleanup, "apply_heading_pipeline", side_effect=AssertionError("heading pipeline called")), mock.patch.object(
                cleanup, "normalize_reference_entry_headings", side_effect=AssertionError("reference cleanup called")
            ), mock.patch.object(cleanup, "strip_contents_sections", side_effect=AssertionError("contents stripping called")), mock.patch.object(
                cleanup, "apply_text_cleanup_pipeline", return_value="text-cleaned"
            ), mock.patch.object(cleanup, "merge_fenced_block_with_code_bullets", side_effect=lambda text: text), mock.patch.object(
                cleanup, "merge_adjacent_fenced_blocks", side_effect=lambda text: text
            ), mock.patch.object(cleanup, "escape_literal_angle_brackets", side_effect=lambda text: text), mock.patch.object(
                cleanup, "make_image_refs_relative", side_effect=lambda text, *_args, **_kwargs: text
            ):
                cleaned = cleanup.cleanup_markdown(
                    "original",
                    context,
                    images_dir,
                    output_path,
                    skip_heading_pipeline=True,
                )

        self.assertEqual(cleaned, "text-cleaned\n")

    def test_cleanup_markdown_can_skip_text_cleanup(self):
        context = ConversionContext(pdf_path=Path("dummy.pdf"), page_numbers=None)
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            output_path = tmp_path / "out.md"
            images_dir = tmp_path / "images"
            images_dir.mkdir()

            with mock.patch.object(cleanup, "apply_heading_pipeline", return_value="heading-cleaned"), mock.patch.object(
                cleanup, "normalize_reference_entry_headings", side_effect=lambda text, _context: text + "\nref"
            ), mock.patch.object(cleanup, "strip_contents_sections", side_effect=lambda text: text + "\nstripped"), mock.patch.object(
                cleanup, "apply_text_cleanup_pipeline", side_effect=AssertionError("text cleanup called")
            ), mock.patch.object(cleanup, "merge_fenced_block_with_code_bullets", side_effect=lambda text: text), mock.patch.object(
                cleanup, "merge_adjacent_fenced_blocks", side_effect=lambda text: text
            ), mock.patch.object(cleanup, "escape_literal_angle_brackets", side_effect=lambda text: text), mock.patch.object(
                cleanup, "make_image_refs_relative", side_effect=lambda text, *_args, **_kwargs: text
            ):
                cleaned = cleanup.cleanup_markdown(
                    "original",
                    context,
                    images_dir,
                    output_path,
                    skip_text_cleanup=True,
                )

        self.assertEqual(cleaned, "heading-cleaned\nref\nstripped\n")

    def test_cleanup_markdown_can_skip_all_cleanup(self):
        context = ConversionContext(pdf_path=Path("dummy.pdf"), page_numbers=None)
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            output_path = tmp_path / "out.md"
            images_dir = tmp_path / "images"
            images_dir.mkdir()

            with mock.patch.object(cleanup, "apply_heading_pipeline", side_effect=AssertionError("heading pipeline called")), mock.patch.object(
                cleanup, "normalize_reference_entry_headings", side_effect=AssertionError("reference cleanup called")
            ), mock.patch.object(cleanup, "strip_contents_sections", side_effect=AssertionError("contents stripping called")), mock.patch.object(
                cleanup, "apply_text_cleanup_pipeline", side_effect=AssertionError("text cleanup called")
            ), mock.patch.object(cleanup, "merge_fenced_block_with_code_bullets", side_effect=AssertionError("merge code bullets called")), mock.patch.object(
                cleanup, "merge_adjacent_fenced_blocks", side_effect=AssertionError("merge blocks called")
            ), mock.patch.object(cleanup, "escape_literal_angle_brackets", side_effect=AssertionError("angle bracket cleanup called")), mock.patch.object(
                cleanup, "make_image_refs_relative", side_effect=lambda text, *_args, **_kwargs: text
            ):
                cleaned = cleanup.cleanup_markdown(
                    "original",
                    context,
                    images_dir,
                    output_path,
                    skip_all_cleanup=True,
                )

        self.assertEqual(cleaned, "original\n")

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

    def test_remove_running_headers_drops_large_repeated_top_page_titles(self):
        source = "\n".join(
            [
                "## Intro",
                "",
                "#### Pure C English Overview",
                "",
                "Body one.",
                "",
                "#### Pure C English Overview",
                "",
                "Body two.",
                "",
                "#### Pure C English Overview",
                "",
                "Body three.",
            ]
        )
        context = ConversionContext(pdf_path=Path("dummy.pdf"), page_numbers=None)
        fake_headings = [
            MarkdownHeading(0, "Intro", [], "intro", 2),
            MarkdownHeading(2, "Pure C English Overview", [], "pure-c-english-overview-a", 4),
            MarkdownHeading(6, "Pure C English Overview", [], "pure-c-english-overview-b", 4),
            MarkdownHeading(10, "Pure C English Overview", [], "pure-c-english-overview-c", 4),
        ]
        fake_matches = {
            0: {"page_no": 1, "x0": 72.0, "y0": 120.0, "size": 18.0},
            1: {"page_no": 3, "x0": 184.4, "y0": 41.4, "size": 20.0},
            2: {"page_no": 6, "x0": 184.4, "y0": 41.4, "size": 20.0},
            3: {"page_no": 8, "x0": 184.4, "y0": 41.4, "size": 20.0},
        }
        with mock.patch.object(cleanup, "extract_markdown_headings", return_value=fake_headings), mock.patch.object(
            cleanup, "match_headings_to_source_lines", return_value=fake_matches
        ):
            cleaned = cleanup.remove_running_headers(source, context)
        self.assertNotIn("#### Pure C English Overview", cleaned)
        self.assertIn("Body one.", cleaned)
        self.assertIn("Body two.", cleaned)
        self.assertIn("Body three.", cleaned)

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

    def test_captionish_heading_context_is_demoted(self):
        small_source = {"size": 12.0}
        self.assertTrue(
            reference_entries.looks_like_captionish_heading_context(
                "WD 1772 Floppy Disk Controller Specification",
                small_source,
                "|A|B|",
            )
        )
        self.assertTrue(
            reference_entries.looks_like_captionish_heading_context(
                "Where:",
                small_source,
                "- 0 = always 0",
            )
        )
        self.assertFalse(
            reference_entries.looks_like_captionish_heading_context(
                "2.2.1 USER CONTROL NAMES AND OPERATIONS",
                small_source,
                "|A|B|",
            )
        )


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

    def test_extract_contents_entries_from_title_only_page_lines(self):
        page_lines = [
            {"x0": 72.0, "size": 14.0, "text": "CHAPTER 1. INTRODUCTION"},
            {"x0": 72.0, "size": 12.0, "text": "1.1 GENERAL INFORMATION"},
            {"x0": 72.0, "size": 14.0, "text": "CHAPTER 2. USING THE MULTISYNC 3D"},
            {"x0": 72.0, "size": 12.0, "text": "2.1 GETTING ACQUAINTED WITH YOUR MULTISYNC 3D COLOR MONITOR"},
            {"x0": 86.4, "size": 12.0, "text": "2.2.1 USER CONTROL NAMES AND OPERATIONS"},
        ]
        entries = headings.extract_contents_entries_from_page_lines(page_lines)
        self.assertEqual(
            [entry["title"] for entry in entries],
            [
                "CHAPTER 1. INTRODUCTION",
                "1.1 GENERAL INFORMATION",
                "CHAPTER 2. USING THE MULTISYNC 3D",
                "2.1 GETTING ACQUAINTED WITH YOUR MULTISYNC 3D COLOR MONITOR",
                "2.2.1 USER CONTROL NAMES AND OPERATIONS",
            ],
        )

    def test_extract_contents_entries_rejects_prose_page(self):
        page_lines = [
            {"x0": 72.0, "size": 12.0, "text": "General information about the product and how to use it safely."},
            {"x0": 72.0, "size": 12.0, "text": "This paragraph is normal body prose and should not become a TOC."},
            {"x0": 72.0, "size": 12.0, "text": "Another body paragraph with punctuation."},
        ]
        self.assertEqual(headings.extract_contents_entries_from_page_lines(page_lines), [])

    def test_infer_explicit_contents_entry_level_only_uses_real_structure_cues(self):
        self.assertEqual(headings.infer_explicit_contents_entry_level("COMMAND DESCRIPTION"), None)
        self.assertEqual(headings.infer_explicit_contents_entry_level("COMMAND SUMMARY"), None)
        self.assertEqual(headings.infer_explicit_contents_entry_level("Flag Summary"), None)
        self.assertEqual(headings.infer_explicit_contents_entry_level("1.1 GENERAL INFORMATION"), 2)
        self.assertEqual(headings.infer_explicit_contents_entry_level("2.2.1 USER CONTROL NAMES AND OPERATIONS"), 3)

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
