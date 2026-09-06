import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixtures import WP_HTML  # noqa: E402
from ttcrawl import check, wp  # noqa: E402


def _resp(status, body=b"", headers=None, final=None, chain=None):
    return {"status": status, "body": body, "headers": headers or {}, "final_url": final, "chain": chain or [], "truncated": False}


class WpTests(unittest.TestCase):
    def test_detect_in_html(self):
        self.assertEqual(wp.detect_in_html(WP_HTML), "https://blog.example.com/news/wp-json/")
        self.assertIsNone(wp.detect_in_html("<html></html>"))

    def test_api_base_uses_the_page_hint_and_falls_back(self):
        calls = []

        def fetch(url, **kw):
            calls.append(url)
            if url == "https://blog.example.com/news/":
                return _resp(200, WP_HTML.encode())
            if url == "https://blog.example.com/news/wp-json/wp/v2/posts?per_page=1":
                return _resp(200, b'[{"id":1}]')
            return _resp(404)

        self.assertEqual(wp.api_base("https://blog.example.com/news/", fetch=fetch), "https://blog.example.com/news/wp-json")
        self.assertIsNone(wp.api_base("https://plain.example.com/", fetch=lambda u, **k: _resp(404)))

    def test_fetch_all_paginates(self):
        pages = {1: [{"id": 1}], 2: [{"id": 2}]}

        def fetch(url, **kw):
            n = int(url.split("&page=")[1].split("&")[0])
            import json
            return _resp(200, json.dumps(pages.get(n, [])).encode(), {"X-WP-TotalPages": "2"})

        self.assertEqual([i["id"] for i in wp.fetch_all("https://b.com/wp-json", "posts", fetch=fetch)], [1, 2])

    def test_frontmatter(self):
        item = {"title": {"rendered": "Hello &amp; welcome"}, "link": "https://b.com/hello", "date": "2026-01-02T10:00:00",
                "modified": "2026-02-03T00:00:00", "author": 7, "status": "publish", "slug": "hello", "categories": [3]}
        fm = wp.frontmatter(item, {7: "Jane"}, {3: "News"}, "posts")
        self.assertIn('title: "Hello & welcome"', fm)
        self.assertIn("type: wp_post", fm)
        self.assertIn("date: 2026-01-02", fm)
        self.assertIn('author: "Jane"', fm)
        self.assertIn('categories: ["News"]', fm)


class CheckTests(unittest.TestCase):
    def test_map_url(self):
        self.assertEqual(check.map_url("https://old.com/about?x=1", "https://new.com"), "https://new.com/about?x=1")
        self.assertEqual(check.map_url("https://old.com/", "https://new.com"), "https://new.com/")

    def test_verdicts(self):
        self.assertEqual(check.verdict(200, [], False), check.OK)
        self.assertEqual(check.verdict(200, [(301, "x")], False), check.OK)
        self.assertEqual(check.verdict(200, [(301, "x"), (301, "y")], False), check.CHAIN)
        self.assertEqual(check.verdict(200, [], True), check.NOINDEX)
        self.assertEqual(check.verdict(404, [], False), check.MISSING)
        self.assertEqual(check.verdict(410, [], False), check.MISSING)
        self.assertEqual(check.verdict(500, [], False), check.ERROR)
        self.assertEqual(check.verdict(None, [], False), check.ERROR)

    def test_check_url_reads_the_page(self):
        html = b"<html><head><title>New</title><meta name='description' content='d'></head><body><h1>One</h1></body></html>"
        r = check.check_url("https://new.com/a", fetch=lambda u, **k: _resp(200, html, final="https://new.com/a", chain=[(301, "https://new.com/a")]))
        self.assertEqual((r["status"], r["title"], r["h1_count"], r["chain"]), (200, "New", 1, ["https://new.com/a"]))

        def boom(u, **k):
            raise OSError("down")

        self.assertEqual(check.check_url("https://new.com/a", fetch=boom)["status"], None)

    def test_markdown_report(self):
        report = {"new_base": "https://new.com", "rows": [
            {"old_url": "https://old.com/a", "new_url": "https://new.com/a", "verdict": check.OK, "status": 200, "chain": [],
             "final_url": "https://new.com/a", "title": "A", "old_title": "A", "findings": ["no description"], "error": None},
            {"old_url": "https://old.com/b", "new_url": "https://new.com/b", "verdict": check.MISSING, "status": 404, "chain": [],
             "final_url": None, "title": "", "old_title": "B", "findings": [], "error": None}],
            "counts": {check.OK: 1, check.MISSING: 1, check.CHAIN: 0, check.NOINDEX: 0, check.ERROR: 0},
            "sitemap": {"fetched": True, "entries": 1, "missing_from_sitemap": ["/a"], "new_in_sitemap": []},
            "home": {"status": 200, "jsonld_types": ["LocalBusiness"], "title": "New"}}
        md = check.markdown(report)
        self.assertTrue(md.splitlines()[6].startswith("| https://old.com/b | missing"))
        self.assertIn("missing from the sitemap: /a", md)
        self.assertIn("LocalBusiness", md)


if __name__ == "__main__":
    unittest.main()
