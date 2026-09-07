import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ttcrawl import a11y  # noqa: E402

GOOD = """<html lang="en"><head><meta name="viewport" content="width=device-width"><title>Acme</title></head>
<body><h1>Acme</h1><h2>Services</h2><h3>Roofs</h3><a href="/x">Our roofing services</a>
<a href="/y"><img src="l.png" alt="Acme logo"></a><a href="/z" aria-label="Call us"></a>
<form><label for="e">Email</label><input id="e" type="email"><label>Name <input type="text"></label>
<input type="hidden" name="h"><textarea aria-label="Message"></textarea><button>Send</button></form>
<div id="a"></div><div id="b"></div></body></html>"""

BAD = """<html><head><title>A very long title that goes on and on and on and on past sixty characters</title></head>
<body><h1>Acme</h1><h3>Skipped</h3><a href="/x"></a><a href="/y">Click here</a><a href="/w">read more</a>
<button></button><form><input type="text" name="q"><select name="s"></select></form>
<div id="dup"></div><span id="dup"></span></body></html>"""


class A11yTests(unittest.TestCase):
    def test_a_good_page_has_no_findings(self):
        self.assertEqual(a11y.findings(GOOD, "Acme", "A real description of the page that is long enough to read as a snippet in search."), [])

    def test_a_bad_page_lists_each_failure(self):
        f = a11y.findings(BAD, "A very long title that goes on and on and on and on past sixty characters", "Short.")
        self.assertIn("no lang attribute on <html>", f)
        self.assertIn("no viewport meta tag", f)
        self.assertIn("heading order skips from h1 to h3", f)
        self.assertIn("1 link(s) with no text", f)
        self.assertIn('generic link text: "click here", "read more"', f)
        self.assertIn("1 button(s) with no text", f)
        self.assertIn("2 form field(s) without a label", f)
        self.assertIn("duplicate id(s): dup", f)
        self.assertTrue(any(x.startswith("title is 7") for x in f))
        self.assertTrue(any(x.startswith("meta description is 6 characters") for x in f))

    def test_unparseable_html_is_one_finding(self):
        self.assertEqual(a11y.findings(None), ["HTML could not be parsed"])


if __name__ == "__main__":
    unittest.main()
