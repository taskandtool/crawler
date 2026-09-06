import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ttcrawl import net, text  # noqa: E402


class NetTests(unittest.TestCase):
    def test_private_and_loopback_refused(self):
        for host in ("127.0.0.1", "192.168.1.1", "10.0.0.5", "localhost", "169.254.169.254", "nonexistent.invalid"):
            self.assertFalse(net.is_public_host(host), host)

    def test_public_allowed(self):
        self.assertTrue(net.is_public_host("8.8.8.8"))

    def test_public_http_url(self):
        self.assertTrue(net.public_http_url("https://1.1.1.1/"))
        for bad in ("file:///etc/passwd", "ftp://x.com", "http://localhost/", "http://foo.local/", "http://[::1]/", "nope"):
            self.assertFalse(net.public_http_url(bad), bad)

    def test_same_site_ignores_www(self):
        self.assertTrue(net.same_site("https://www.x.com/a", "x.com"))
        self.assertTrue(net.same_site("https://x.com/a", "www.x.com"))
        self.assertFalse(net.same_site("https://evil.com/a", "x.com"))
        self.assertFalse(net.same_site("https://sub.x.com/a", "x.com"))

    def test_normalize_url(self):
        self.assertEqual(net.normalize_url("https://X.com/About/?utm_source=x&b=2#top"), "https://x.com/About?b=2")
        self.assertEqual(net.normalize_url("https://x.com"), "https://x.com/")
        self.assertIsNone(net.normalize_url("mailto:a@x.com"))
        self.assertIsNone(net.normalize_url("javascript:void(0)"))

    def test_crawlable_and_documents(self):
        self.assertTrue(net.crawlable("https://x.com/services"))
        self.assertFalse(net.crawlable("https://x.com/brochure.PDF"))
        self.assertFalse(net.crawlable("https://x.com/a.jpg"))
        self.assertTrue(net.is_document("https://x.com/price-list.pdf"))
        self.assertTrue(net.is_document("https://x.com/menu.docx"))
        self.assertFalse(net.is_document("https://x.com/menu"))


class TextTests(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(text.slugify("https://x.com/services/"), "services")
        self.assertEqual(text.slugify("https://x.com/"), "index")
        self.assertEqual(text.slugify("https://x.com/a/b/c/"), "a-b-c")
        self.assertLessEqual(len(text.slugify("https://x.com/" + "z" * 200)), 80)

    def test_ext_for(self):
        self.assertEqual(text.ext_for("https://x.com/a.png", ""), "png")
        self.assertEqual(text.ext_for("https://x.com/a", "image/png"), "png")
        self.assertEqual(text.ext_for("https://x.com/a.JPEG", ""), "jpg")
        self.assertEqual(text.ext_for("https://x.com/a", "image/svg+xml"), "img")

    def test_image_key_collapses_variants(self):
        k = text.image_key("https://x.com/wp-content/uploads/team.jpg")
        for v in ("https://x.com/wp-content/uploads/team-300x200.jpg?v=3", "https://X.com/wp-content/uploads/team-scaled.jpg",
                  "https://x.com/wp-content/uploads/team@2x.jpg"):
            self.assertEqual(text.image_key(v), k)
        self.assertNotEqual(text.image_key("https://x.com/wp-content/uploads/team2.jpg"), k)

    def test_rewrite_images_touches_only_mapped_image_tokens(self):
        md = "![a](https://x.com/a.jpg) and ![b](https://x.com/a.jpg?w=800) see [also](https://x.com/a.jpg)"
        out = text.rewrite_images(md, {"https://x.com/a.jpg": "images/h1.jpg"})
        self.assertEqual(out, "![a](images/h1.jpg) and ![b](https://x.com/a.jpg?w=800) see [also](https://x.com/a.jpg)")

    def test_near_duplicate(self):
        a = "the quick brown fox jumps over the lazy dog again and again " * 5
        dup, sset = text.near_duplicate(a, [])
        self.assertFalse(dup)
        self.assertTrue(text.near_duplicate(a, [sset])[0])
        b = "we bake sourdough bread and pastries fresh every morning downtown " * 3
        self.assertFalse(text.near_duplicate(b, [sset])[0])

    NAV = "[Home](/) [Services](/services) [Contact](/contact)"
    FOOTER = "© 2026 Acme Hydraulics · 12 Main St · 555-0100"

    def page(self, body):
        return f"{self.NAV}\n\n{body}\n\n{self.FOOTER}\n"

    def test_repetition_fallback_strips_and_keeps_once(self):
        pages = [self.page(f"# Page {i}\n\nUnique content number {i} about heat pumps.") for i in range(6)]
        cleaned, common = text.strip_common_lines(pages)
        for i, md in enumerate(cleaned):
            self.assertIn(f"Unique content number {i}", md)
            self.assertNotIn("555-0100", md)
        self.assertEqual(common, [self.NAV, self.FOOTER])
        shared = "We are licensed and insured."
        pages = [self.page(f"# P{i}\n\n{shared if i < 2 else 'Other text ' + str(i)}") for i in range(6)]
        self.assertIn(shared, text.strip_common_lines(pages)[0][0])
        self.assertEqual(text.strip_common_lines([self.page('b')] * 3)[1], [])

    def test_threshold(self):
        self.assertEqual(text.boilerplate_threshold(4), 3)
        self.assertEqual(text.boilerplate_threshold(30), 10)

    def test_strip_lines_and_html_to_text(self):
        self.assertEqual(text.strip_lines("keep\nDrop Me\nkeep2\n", {"drop me"}), "keep\nkeep2\n")
        t = text.html_to_text("<p>Hi</p><script>evil()</script><style>x{}</style><div>There &amp; back</div>")
        self.assertIn("Hi", t)
        self.assertIn("There & back", t)
        self.assertNotIn("evil", t)
        self.assertEqual(text.word_count("one two  three"), 3)


if __name__ == "__main__":
    unittest.main()
