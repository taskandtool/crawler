"""Firecrawl as a fetcher: `--fetcher firecrawl` asks Firecrawl for each
page's HTML instead of fetching and rendering it here, and asks its map for
every URL it knows of the site. Everything after the fetch (the blocks, the
facts, the pictures, the ledger) is the same crawl.

Worth it for a site that refuses our requests (a challenge page, a block on
cloud addresses) or one too big to discover a link at a time. Every call
spends the owner's Firecrawl credits, on their own key: FIRECRAWL_API_KEY,
which reaches a Task & Tool machine when the owner grants a Firecrawl
connection to the app (delivery "machine"). FIRECRAWL_API_URL points it at a
self-hosted Firecrawl.
"""
import json
import os
import random
import time
import urllib.error
import urllib.request

from . import net

DEFAULT_URL = "https://api.firecrawl.dev"
MAP_LIMIT = 5000


class FirecrawlError(Exception):
    """`fatal` when no later call can succeed (no key, a bad key, no credits):
    the crawl stops rather than spending the rest of the run on refusals."""

    def __init__(self, message, fatal=False):
        super().__init__(message)
        self.fatal = fatal


class Client:
    def __init__(self, key, base=DEFAULT_URL, opener=None, sleep=time.sleep):
        self.key, self.base = key, base.rstrip("/")
        self.opener = opener or urllib.request.build_opener()
        self.sleep = sleep
        self.calls = {"scrape": 0, "map": 0}

    @classmethod
    def from_env(cls, env=os.environ):
        key = env.get("FIRECRAWL_API_KEY")
        if not key:
            raise FirecrawlError("no FIRECRAWL_API_KEY: grant a Firecrawl connection to this app "
                                 "(request_connection(\"firecrawl\", why, auth=\"api_key\", delivery=\"machine\"))", fatal=True)
        return cls(key, env.get("FIRECRAWL_API_URL") or DEFAULT_URL)

    def _post(self, path, body, timeout=120):
        data = json.dumps(body).encode()
        for attempt in range(1, net.MAX_RETRIES + 2):
            req = urllib.request.Request(self.base + path, data=data, method="POST", headers={
                "Authorization": "Bearer " + self.key, "Content-Type": "application/json", "User-Agent": net.USER_AGENT})
            try:
                with self.opener.open(req, timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise FirecrawlError("Firecrawl refused the key (%d)" % e.code, fatal=True)
                if e.code == 402:
                    raise FirecrawlError("Firecrawl says the account is out of credits (402)", fatal=True)
                if e.code == 429 and attempt <= net.MAX_RETRIES:
                    self.sleep(net.backoff(attempt, (e.headers or {}).get("Retry-After"), jitter=random.random()))
                    continue
                raise FirecrawlError("Firecrawl answered %d for %s" % (e.code, body.get("url")))
            except (urllib.error.URLError, OSError, ValueError) as e:
                raise FirecrawlError("Firecrawl could not be reached: %s" % e)
        raise FirecrawlError("Firecrawl kept throttling")

    def scrape(self, url):
        """{status, final_url, html}: the page as Firecrawl's browser rendered it."""
        self.calls["scrape"] += 1
        out = self._post("/v2/scrape", {"url": url, "formats": ["rawHtml"], "onlyMainContent": False,
                                        "blockAds": True, "removeBase64Images": True})
        if not out.get("success", True):
            raise FirecrawlError("Firecrawl could not scrape %s: %s" % (url, out.get("error")))
        data = out.get("data") or {}
        meta = data.get("metadata") or {}
        return {"status": meta.get("statusCode") or 200, "final_url": meta.get("url") or meta.get("sourceURL") or url,
                "html": data.get("rawHtml") or data.get("html") or ""}

    def map(self, url, limit=MAP_LIMIT):
        """Every URL Firecrawl knows for the site (its sitemap, search results
        and earlier crawls), in one call."""
        self.calls["map"] += 1
        out = self._post("/v2/map", {"url": url, "limit": limit, "sitemap": "include"})
        links = out.get("links") or (out.get("data") or {}).get("links") or []
        return [l["url"] if isinstance(l, dict) else l for l in links if l]
