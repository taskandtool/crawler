"""`tt-crawl sheet`: the page it draws numbers each picture, in order, inlined."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ttcrawl import sheet  # noqa: E402


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
