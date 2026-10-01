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

    def test_extension_follows_the_content_type(self):
        from ttcrawl.media import extension
        self.assertEqual(extension("", "https://x.com/a.png"), "png")
        self.assertEqual(extension("image/png", "https://x.com/a"), "png")
        self.assertEqual(extension("image/png", "https://x.com/a.jpg"), "png")      # what it is, not what it is called
        self.assertEqual(extension("image/svg+xml; charset=utf-8", "https://x.com/a"), "svg")
        self.assertEqual(extension("", "https://x.com/a"), "img")

    def test_one_key_across_an_images_sizes(self):
        from ttcrawl.media import variant_of
        k = variant_of("https://x.com/wp-content/uploads/team.jpg")[0]
        for v in ("https://x.com/wp-content/uploads/team-300x200.jpg?w=300", "https://X.com/wp-content/uploads/team-scaled.jpg",
                  "https://x.com/wp-content/uploads/team@2x.jpg"):
            self.assertEqual(variant_of(v)[0], k)
        self.assertNotEqual(variant_of("https://x.com/wp-content/uploads/team2.jpg")[0], k)

    def test_rewrite_images_touches_only_mapped_image_tokens(self):
        md = "![a](https://x.com/a.jpg) and ![b](https://x.com/a.jpg?w=800) see [also](https://x.com/a.jpg)"
        out = text.rewrite_images(md, {"https://x.com/a.jpg": "images/h1.jpg"})
        self.assertEqual(out, "![a](images/h1.jpg) and ![b](https://x.com/a.jpg?w=800) see [also](https://x.com/a.jpg)")

    def test_near_duplicate(self):
        a = " ".join("word%d" % i for i in range(300))
        b = a.replace("word150", "changed")                   # one word in 300
        c = " ".join("other%d" % i for i in range(300))
        seen = text.NearDuplicates()
        self.assertFalse(seen.check(text.minhash(a)))
        seen.keep(text.minhash(a))
        self.assertTrue(seen.check(text.minhash(b)))
        self.assertFalse(seen.check(text.minhash(c)))
        self.assertEqual(text.minhash(a), text.minhash(a))    # stable, so a resumed crawl agrees

    def page(self, body):
        return f"{self.NAV}\n\n{body}\n\n{self.FOOTER}\n"

    def test_repeated_lines_are_furniture_on_most_pages_only(self):
        from ttcrawl.site import repeated_lines

        def blocks(*texts):
            return [{"tag": "p", "text": t, "chrome": False} for t in texts]
        bar = "24/7 emergency line 555-0100"
        pages = [blocks(bar, f"Unique content number {i} about heat pumps.") for i in range(6)]
        self.assertEqual(repeated_lines(pages), {bar})
        shared = "We are licensed and insured."             # on 2 of 6 pages: content
        pages = [blocks(bar, shared if i < 2 else "Other text %d" % i) for i in range(6)]
        self.assertNotIn(text.norm_line(shared), repeated_lines(pages))
        self.assertEqual(repeated_lines([blocks(bar)] * 2), set())   # too few pages to tell

    def test_word_count(self):
        self.assertEqual(text.word_count("one two  three"), 3)


if __name__ == "__main__":
    unittest.main()
