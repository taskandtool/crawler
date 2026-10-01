import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ttcrawl import audit, net  # noqa: E402

SITE = {
    "https://acme.test/": (200, """<html><head><title>Acme</title><meta name="description" content="Roofs."><link rel="canonical" href="https://acme.test/"></head>
        <body><header><nav><a href="/about">About</a><a href="/gone">Gone</a></nav></header><main><h1>Acme</h1>
        <a href="https://partner.test/ok">Partner</a><a href="https://partner.test/dead">Dead</a>
        <img src="/img/hero.jpg" alt="crew"><img src="/img/missing.png"><img src="/img/huge.jpg" alt="big"></main></body></html>"""),
    "https://acme.test/about": (200, """<html><head><title>Acme</title></head><body><h1>One</h1><h1>Two</h1>
        <a href="/old-page">Old</a><script type="application/ld+json">{not json</script></body></html>"""),
    "https://acme.test/gone": (404, ""),
    "https://acme.test/img/hero.jpg": (200, ""),
    "https://acme.test/img/huge.jpg": (200, "big"),
    "https://acme.test/img/missing.png": (404, ""),
    "https://acme.test/old-page": (301, ""),
    "https://acme.test/new-page": (200, "<html><head><title>New</title><meta name=description content=x></head><body><h1>N</h1></body></html>"),
    "https://partner.test/ok": (200, ""),
    "https://partner.test/dead": (404, ""),
    "https://acme.test/sitemap.xml": (200, "<urlset><url><loc>https://acme.test/</loc></url><url><loc>https://acme.test/ghost</loc></url></urlset>"),
    "https://acme.test/ghost": (404, ""),
}


def partners_or_public(url):
    """The test partner's .test host stands in for a public one; anything else is judged for real."""
    return url.startswith("https://partner.test/") or net.public_http_url(url)


def fake_fetch(url, cap=0, timeout=0, method="GET"):
    key = audit.norm(url)
    if key == "https://acme.test/old-page":
        return {"status": 200, "final_url": "https://acme.test/new-page", "chain": [(301, "https://acme.test/old-page")],
                "headers": {"content-type": "text/html"}, "body": SITE["https://acme.test/new-page"][1].encode()}
    status, body = SITE.get(key, (404, ""))
    ctype = "text/html" if body.startswith("<html") else ("application/xml" if "urlset" in body else "image/jpeg")
    headers = {"content-type": ctype}
    if key.endswith("huge.jpg"):
        headers["content-length"] = "900000"
    return {"status": status, "final_url": url, "chain": [], "headers": headers, "body": body.encode()}


class AuditTests(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(audit.norm("https://a.test/x/#top"), "https://a.test/x")
        self.assertEqual(audit.norm("https://a.test/"), "https://a.test/")

    def test_audit_finds_the_planted_issues(self):
        r = audit.audit("https://acme.test/", max_pages=20, fetch=fake_fetch, public=partners_or_public)
        kinds = {(i["kind"], i["subject"]) for i in r["issues"]}
        self.assertIn(("broken page", "https://acme.test/gone"), kinds)
        self.assertIn(("broken internal link", "https://acme.test/img/missing.png"), kinds)
        self.assertIn(("broken external link", "https://partner.test/dead"), kinds)
        self.assertIn(("internal link redirects", "https://acme.test/old-page"), kinds)
        self.assertIn(("sitemap", "https://acme.test/ghost"), kinds)
        # the sitemap seeds the crawl, so an unlinked page is still visited
        self.assertIn("https://acme.test/ghost", {i["subject"] for i in r["issues"] if i["kind"] == "broken page"})
        details = {i["detail"] for i in r["issues"] if i["subject"] == "https://acme.test/about"}
        self.assertIn("no meta description", details)
        self.assertIn("2 h1 headings", details)
        self.assertIn("duplicate title", details)
        self.assertIn("JSON-LD that does not parse", details)
        home = {i["detail"] for i in r["issues"] if i["subject"] == "https://acme.test/"}
        self.assertIn("1 image(s) without alt text", home)
        self.assertNotIn("no title", home)
        self.assertFalse(any(i["subject"] == "https://partner.test/ok" for i in r["issues"]))
        self.assertIn(("heavy image", "https://acme.test/img/huge.jpg"), kinds)
        about = {i["detail"] for i in r["issues"] if i["subject"] == "https://acme.test/about"}
        self.assertIn("no lang attribute on <html>", about)
        self.assertIn("## Issues", audit.markdown(r))

    def test_clean_site_has_no_issues_section(self):
        clean = {"site": "x", "pages": 1, "unread": 0, "limit_reached": False, "external_checked": 0, "sitemap_urls": 1,
                 "issues": [], "old_urls": [], "checked_at": "now"}
        md = audit.markdown(clean)
        self.assertNotIn("## Issues", md)
        self.assertIn("No issues found", md)

    def test_a_link_to_a_private_address_is_never_requested(self):
        asked = []
        page = ("<html><head><title>T</title></head><body><h1>T</h1><a href='http://169.254.169.254/latest/meta-data'>m</a>"
                "<a href='http://10.0.0.5/admin'>a</a><a href='https://partner.test/ok'>p</a>"
                "<a href='sms:+15550100'>t</a><a href='ftp://files.acme.test/x'>f</a></body></html>")

        def fetch(url, cap=0, timeout=0, method="GET"):
            asked.append(url)
            body = page if audit.norm(url) == "https://acme.test/" else ""
            return {"status": 200 if body or "partner" in url else 404, "final_url": url, "chain": [],
                    "headers": {"content-type": "text/html"}, "body": body.encode()}

        r = audit.audit("https://acme.test/", max_pages=5, fetch=fetch, public=partners_or_public)
        self.assertFalse([u for u in asked if "169.254" in u or "10.0.0.5" in u])
        self.assertEqual(sorted(r["external_skipped"]), ["http://10.0.0.5/admin", "http://169.254.169.254/latest/meta-data"])
        self.assertIn("https://partner.test/ok", asked)
        self.assertFalse([u for u in asked if u.startswith(("sms:", "ftp:"))])      # not http: nothing to request
        self.assertFalse([u for u in r["external_skipped"] if not u.startswith("http")])
        self.assertIn("Not requested", audit.markdown(r))

    def test_no_external_skips_partners(self):
        r = audit.audit("https://acme.test/", max_pages=20, fetch=fake_fetch, check_external=False)
        self.assertFalse(any(i["kind"] == "broken external link" for i in r["issues"]))


if __name__ == "__main__":
    unittest.main()
