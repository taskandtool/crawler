"""`tt-crawl sheet`: the page it draws numbers each picture, in order, inlined."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ttcrawl import sheet, site  # noqa: E402
from ttcrawl.say import count  # noqa: E402


class SheetTest(unittest.TestCase):
    def test_each_picture_is_numbered_in_order_and_inlined(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for name in ("a.png", "b.jpg"):
                p = os.path.join(d, name)
                with open(p, "wb") as f:
                    f.write(b"\x89PNG fake")
                paths.append(p)
            html = sheet.page(paths)
        self.assertLess(html.index("<b>1</b>"), html.index("<b>2</b>"))
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("data:image/jpeg;base64,", html)
        self.assertIn("a.png", html)

    def test_from_a_crawl_the_cards_photographs_in_order_or_its_marks(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "_index"))
            os.makedirs(os.path.join(d, "images"))
            media = [{"kind": "photo", "file": "a.jpg", "width": 900}, {"kind": "photo", "file": "b.jpg", "width": 2000},
                     {"kind": "photo", "file": "small.jpg", "width": 400}, {"kind": "mark", "file": "m.png", "width": 90}]
            for it in media:
                open(os.path.join(d, "images", it["file"]), "wb").close()
            with open(os.path.join(d, "_index", "media.json"), "w") as f:
                json.dump(media, f)
            self.assertEqual(sheet.from_crawl(d, marks=False), [os.path.join(d, "images", n) for n in ("b.jpg", "a.jpg", "small.jpg")])
            self.assertEqual(sheet.from_crawl(d, marks=True), [os.path.join(d, "images", "m.png")])


class FactWordsTest(unittest.TestCase):
    def test_the_crawl_summary_counts_each_kind_of_fact_in_words(self):
        self.assertEqual([count(2, *site.FACT_WORDS[k]) for k in ("address", "hours")], ["2 addresses", "2 hours lines"])
