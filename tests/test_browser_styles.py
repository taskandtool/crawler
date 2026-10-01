import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ttcrawl import browser, styles  # noqa: E402


class BrowserTests(unittest.TestCase):
    def test_find_obscura_order(self):
        self.assertEqual(browser.find_obscura(env={"OBSCURA_BIN": "/x/obscura"}, which=lambda _: None,
                                              exists=lambda p: p == "/x/obscura"), "/x/obscura")
        self.assertIsNone(browser.find_obscura(env={"OBSCURA_BIN": "/gone"}, which=lambda _: "/usr/bin/obscura",
                                               exists=lambda p: p != "/gone"))
        self.assertEqual(browser.find_obscura(env={}, which=lambda _: "/usr/bin/obscura", exists=lambda p: False), "/usr/bin/obscura")
        self.assertEqual(browser.find_obscura(env={}, which=lambda _: None, exists=lambda p: p == "/usr/local/bin/obscura"),
                         "/usr/local/bin/obscura")
        self.assertIsNone(browser.find_obscura(env={}, which=lambda _: None, exists=lambda p: False))

    def test_parse_eval(self):
        self.assertEqual(browser.parse_eval('{"title":"t"}'), {"title": "t"})
        self.assertEqual(browser.parse_eval('"{\\"title\\":\\"t\\"}"'), {"title": "t"})
        self.assertIsNone(browser.parse_eval("nope"))


class StyleTests(unittest.TestCase):
    def test_normalize_color(self):
        self.assertEqual(styles.normalize_color("rgb(47, 91, 234)"), "#2f5bea")
        self.assertEqual(styles.normalize_color("rgba(47, 91, 234, 0.5)"), "#2f5bea@0.5")
        self.assertEqual(styles.normalize_color("#ABC"), "#aabbcc")
        self.assertIsNone(styles.normalize_color("transparent"))

    def test_merge(self):
        a = {"fonts": ['"Inter", sans-serif', "Georgia, serif"], "colors": [["rgb(0, 0, 0)", 10]],
             "body": {"font": "Inter, sans-serif", "size": "18px", "color": "rgb(0, 0, 0)", "background": "rgb(255, 255, 255)"},
             "buttons": [{"text": "Call", "background": "rgb(47, 91, 234)", "color": "#fff", "font": "Inter"}],
             "logos": [{"tag": "img", "src": "/logo.svg", "alt": "Acme", "width": 120, "height": 40}]}
        m = styles.merge_styles([a, None, {"fonts": ["Inter"], "colors": [["rgb(0, 0, 0)", 3]]}])
        self.assertEqual(m["fonts"], ["Inter", "Georgia"])
        self.assertEqual(m["colors"][0], {"color": "#000000", "count": 13})
        self.assertEqual(m["roles"]["body"]["font"], "Inter")
        self.assertEqual(m["buttons"][0]["background"], "#2f5bea")
        self.assertEqual(m["pages_read"], 2)


if __name__ == "__main__":
    unittest.main()
