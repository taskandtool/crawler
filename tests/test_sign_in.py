"""Pages behind a sign-in: $TTCRAWL_SIGN_IN, asked before a request opens a
site of the caller's own, in the browser and in net's fetches."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ttcrawl import net  # noqa: E402


class SignedInTest(unittest.TestCase):
    """$TTCRAWL_SIGN_IN: what every request asks first, browser or not, when it is set."""

    def setUp(self):
        net._open_as_is.clear()
        net._signed.clear()
        net._cookie_hosts.clear()
        self.calls = []

    def runner(self, stdout):
        def run(argv, **_kw):
            self.calls.append(argv)
            return type("Done", (), {"stdout": stdout})()
        return run

    def test_unset_opens_as_is_and_runs_nothing(self):
        url = "https://a.example.com/admin"
        self.assertEqual(net.signed_in(url, env={}, run=self.runner("")), url)
        self.assertEqual(self.calls, [])

    def test_opens_the_signed_link_and_passes_the_address(self):
        url = "https://a.example.com/admin?x=1"
        link = url + "&t=abc"
        got = net.signed_in(url, env={"TTCRAWL_SIGN_IN": "~/bin/signer --json"},
                            run=self.runner('{"url": "%s"}' % link))
        self.assertEqual(got, link)
        self.assertEqual(self.calls, [[os.path.expanduser("~/bin/signer"), "--json", url]])

    def test_a_signed_link_is_reused_only_while_it_lasts(self):
        env, url = {"TTCRAWL_SIGN_IN": "signer"}, "https://a.example.com/admin"
        run = self.runner('{"url": "%s?t=1"}' % url)
        for t in (0, 60, net.SIGNED_FOR + 1):
            self.assertEqual(net.signed_in(url, env=env, run=run, now=lambda t=t: t), url + "?t=1")
        self.assertEqual(len(self.calls), 2)

    def test_a_site_answered_as_is_throughout_is_not_asked_again(self):
        env = {"TTCRAWL_SIGN_IN": "signer"}
        url = "https://other.example.org/"
        self.assertEqual(net.signed_in(url, env=env, run=self.runner('{"url": "%s", "site": true}' % url)), url)
        self.assertEqual(net.signed_in(url + "about", env=env, run=self.runner("")), url + "about")
        self.assertEqual(len(self.calls), 1)

    def test_one_public_page_of_a_site_says_nothing_about_its_private_ones(self):
        env, site = {"TTCRAWL_SIGN_IN": "signer"}, "https://a.example.com"
        net.signed_in(site + "/sitemap.xml", env=env, run=self.runner('{"url": "%s/sitemap.xml"}' % site))
        got = net.signed_in(site + "/admin", env=env, run=self.runner('{"url": "%s/admin?t=1"}' % site))
        self.assertEqual(got, site + "/admin?t=1")
        self.assertEqual(len(self.calls), 2)

    def test_a_refusal_or_garbage_opens_as_is(self):
        env = {"TTCRAWL_SIGN_IN": "signer"}
        for out in ("", "not json", "[]", '{"ok": false}'):
            net._open_as_is.clear()
            self.assertEqual(net.signed_in("https://a.example.com/", env=env, run=self.runner(out)),
                             "https://a.example.com/")

    def test_a_link_to_another_host_is_ignored(self):
        got = net.signed_in("https://a.example.com/", env={"TTCRAWL_SIGN_IN": "signer"},
                            run=self.runner('{"url": "https://evil.example.net/"}'))
        self.assertEqual(got, "https://a.example.com/")

    def test_a_command_that_will_not_run_opens_as_is(self):
        def broken(*_a, **_k):
            raise FileNotFoundError("signer")
        self.assertEqual(net.signed_in("https://a.example.com/", env={"TTCRAWL_SIGN_IN": "signer"}, run=broken),
                         "https://a.example.com/")

    def test_one_sign_in_per_host_serves_later_requests_through_its_cookie(self):
        opened = []

        class Opener:
            def open(self, req, timeout):
                opened.append(req.full_url)
                return "resp"

        env = {"TTCRAWL_SIGN_IN": "signer"}
        real_opener, real_signed = net._opener, net.signed_in
        net._opener = Opener()
        net.signed_in = lambda url: real_signed(url, env=env, run=self.runner('{"url": "%s?t=1"}' % url))
        try:
            for path, t in (("/a", 0), ("/b", 60), ("/c", net.SIGNED_FOR + 61)):
                net._open(net.Request("https://a.example.com" + path), 5, now=lambda t=t: t)
        finally:
            net._opener, net.signed_in = real_opener, real_signed
        self.assertEqual(opened, ["https://a.example.com/a?t=1", "https://a.example.com/b",
                                  "https://a.example.com/c?t=1"])
