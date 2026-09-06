import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ttcrawl import browser, styles  # noqa: E402


class _Proc:
    def __init__(self, returncode=0, stdout=b""):
        self.returncode = returncode
        self.stdout = stdout


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

    def test_render_html(self):
        seen = {}

        def runner(cmd, **kw):
            seen["cmd"] = cmd
            return _Proc(0, b"<html><body><p>hello from js</p></body></html>")

        out = browser.render_html("https://x.com/a", "/bin/obscura", runner=runner)
        self.assertIn("hello from js", out)
        self.assertEqual(seen["cmd"][:3], ["/bin/obscura", "fetch", "https://x.com/a"])
        # a dump and a screenshot never share a call: Obscura prints nothing then
        self.assertNotIn("--screenshot", seen["cmd"])
        self.assertNotIn("--allow-private-network", seen["cmd"])
        self.assertTrue(browser.screenshot("https://x.com/a", "/bin/obscura", "/tmp/a.png", runner=runner))
        self.assertIn("--screenshot", seen["cmd"])
        self.assertNotIn("--dump", seen["cmd"])
        self.assertFalse(browser.screenshot("https://x.com/a", "/bin/o", "/tmp/a.png", runner=lambda c, **k: _Proc(1, b"")))
        self.assertIsNone(browser.render_html("https://x.com", "/bin/o", runner=lambda c, **k: _Proc(1, b"")))
        self.assertIsNone(browser.render_html("https://x.com", "/bin/o", runner=lambda c, **k: _Proc(0, b"  \n")))

        def timeout(c, **k):
            raise subprocess.TimeoutExpired(c, 1)

        self.assertIsNone(browser.render_html("https://x.com", "/bin/o", runner=timeout))

    def test_read_styles_and_parse_eval(self):
        self.assertEqual(browser.parse_eval('{"title":"t"}'), {"title": "t"})
        self.assertEqual(browser.parse_eval('"{\\"title\\":\\"t\\"}"'), {"title": "t"})
        self.assertIsNone(browser.parse_eval("nope"))
        out = browser.read_styles("https://x.com", "/bin/o", runner=lambda c, **k: _Proc(0, b'{"fonts":["Inter"]}'))
        self.assertEqual(out, {"fonts": ["Inter"]})


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
