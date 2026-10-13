"""Page selection, page separators, and hidden-text recovery."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "pdf-to-markdown"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from converter import cleanup, convert, document  # noqa: E402
from converter.models import ConversionContext  # noqa: E402

SEPARATOR_RE = re.compile(r"(?m)^\{(\d+)\}-{48}$")
VISIBLE_WORDS = " ".join(f"visible{i}" for i in range(30))
HIDDEN_WORDS = " ".join(f"hidden{i}" for i in range(30))


def _write_fixture(path: Path) -> None:
    """Page 1: normal text. Page 2: a scan image over a 2 pt invisible OCR layer.

    Page 3: image only. Scanner software writes OCR layers in tiny invisible
    fonts; PyMuPDF4LLM drops text that small, which is the failure this guards.
    """
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 260), False)
    pix.clear_with(255)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 300), VISIBLE_WORDS[:60], fontsize=11)
    page.insert_text((72, 320), VISIBLE_WORDS[60:120], fontsize=11)
    page.insert_text((72, 340), VISIBLE_WORDS[120:180], fontsize=11)
    page.insert_text((72, 360), VISIBLE_WORDS[180:], fontsize=11)
    page = doc.new_page()
    page.insert_image(page.rect, pixmap=pix)
    for index in range(0, len(HIDDEN_WORDS), 60):
        page.insert_text(
            (72, 300 + index / 3), HIDDEN_WORDS[index : index + 60], fontsize=2, render_mode=3
        )
    page = doc.new_page()
    page.insert_image(page.rect, pixmap=pix)
    doc.save(path)
    doc.close()


class PageSpecTests(unittest.TestCase):
    def test_parse_page_spec_accepts_ranges_and_lists(self):
        self.assertEqual(convert.parse_page_spec("1-3,7,9-10", 12), [0, 1, 2, 6, 8, 9])
        self.assertEqual(convert.parse_page_spec("4", 4), [3])
        self.assertEqual(convert.parse_page_spec(" 2 , 2-3 ", 3), [1, 2])

    def test_parse_page_spec_rejects_out_of_range_pages(self):
        with self.assertRaises(ValueError):
            convert.parse_page_spec("1-5", 4)
        with self.assertRaises(ValueError):
            convert.parse_page_spec("0", 4)
        with self.assertRaises(ValueError):
            convert.parse_page_spec(",", 4)

    def test_describe_pages_collapses_runs(self):
        self.assertEqual(convert.describe_pages(None), "all")
        self.assertEqual(convert.describe_pages([0, 1, 2, 6, 8, 9]), "1-3, 7, 9-10")
        self.assertEqual(convert.describe_pages([3]), "4")


class PageSeparatorTests(unittest.TestCase):
    def test_separators_mark_every_page_including_empty_ones(self):
        text = convert.join_page_texts([(1, "one"), (2, ""), (3, "three")], page_separators=True)

        self.assertEqual(SEPARATOR_RE.findall(text), ["1", "2", "3"])
        self.assertEqual(
            text,
            "{1}" + "-" * 48 + "\n\none\n\n{2}" + "-" * 48 + "\n\n{3}" + "-" * 48 + "\n\nthree\n\n",
        )

    def test_without_separators_empty_pages_are_dropped(self):
        self.assertEqual(
            convert.join_page_texts([(1, "one"), (2, ""), (3, "three")], page_separators=False),
            "one\n\nthree\n",
        )

    def test_separators_survive_the_cleanup_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "fixture.pdf"
            _write_fixture(pdf)
            context = ConversionContext(pdf_path=pdf, page_numbers=None, with_text=2, without_text=1)
            raw = convert.join_page_texts(
                [(1, "# Title\n\nBody line one."), (2, ""), (3, "Body on page three.")],
                page_separators=True,
            )
            cleaned = cleanup.cleanup_markdown(
                raw, context, Path(tmp) / "fixture_images", Path(tmp) / "fixture.md"
            )

        self.assertEqual(SEPARATOR_RE.findall(cleaned), ["1", "2", "3"])


class HiddenTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pdf = Path(self.tmp.name) / "fixture.pdf"
        _write_fixture(self.pdf)

    def _extract(self, **kwargs):
        context = ConversionContext(pdf_path=self.pdf, page_numbers=None, with_text=2, without_text=1)
        text, _ = convert.extract_markdown(
            context,
            force_ocr=False,
            backend=None,
            langs=["en"],
            images_dir=Path(self.tmp.name) / "fixture_images",
            write_images=False,
            page_separators=True,
            **kwargs,
        )
        return context, text

    def test_tiny_invisible_text_layer_is_detected(self):
        import pymupdf4llm

        chunks = pymupdf4llm.to_markdown(str(self.pdf), page_chunks=True, show_progress=False)
        by_page = {int(c["metadata"]["page_number"]): c["text"] for c in chunks}

        self.assertNotIn("hidden0", by_page[2])
        self.assertEqual(document.hidden_text_pages(self.pdf, by_page), [2])

    def test_hidden_text_mode_text_keeps_the_pdf_text_layer(self):
        context, text = self._extract(hidden_text="text")

        self.assertEqual(context.hidden_text_pages, [2])
        self.assertIn("visible0", text)
        self.assertIn("hidden29", text)
        pages = {int(m.group(1)) for m in SEPARATOR_RE.finditer(text)}
        self.assertEqual(pages, {1, 2, 3})
        page_two = text.split("{2}" + "-" * 48)[1].split("{3}" + "-" * 48)[0]
        self.assertIn("hidden0", page_two)
        self.assertNotIn("visible0", page_two)

    def test_hidden_text_mode_off_leaves_the_page_empty(self):
        context, text = self._extract(hidden_text="off")

        self.assertEqual(context.hidden_text_pages, [2])
        self.assertNotIn("hidden0", text)

    def test_no_images_leaves_no_images_dir_behind(self):
        self._extract(hidden_text="text")

        self.assertFalse((Path(self.tmp.name) / "fixture_images").exists())


if __name__ == "__main__":
    unittest.main()
