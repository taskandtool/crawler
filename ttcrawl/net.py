"""Fetching, with the safety rails every subcommand shares.

The AI drives these fetches from inside our infrastructure, so every host is
checked against private, loopback, link-local, and reserved ranges before a
request, and again on every redirect hop. A 429 (or a 503 with Retry-After)
is waited out and retried, never hammered.
"""
import gzip
import ipaddress
import random
import socket
import time
import zlib
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request

USER_AGENT = "TaskAndTool-Crawler/0.1 (+https://taskandtool.app)"
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref")
SKIP_EXTENSIONS = (".pdf", ".zip", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp4",
                   ".mp3", ".css", ".js", ".ico", ".xml", ".json", ".doc", ".docx", ".xls", ".xlsx",
                   ".ppt", ".pptx")
DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx")
IMAGE_CONTENT_TYPES = ("image/",)


def is_public_host(host):
    """SSRF guard: refuse anything that resolves to a private, loopback,
    link-local, or reserved address, and anything that does not resolve."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return False
    return True


def public_http_url(url):
    """True for an http(s) URL on a public host."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".internal")):
        return False
    return is_public_host(host)


def same_site(url, root_host):
    """Same host as the crawl's root. A leading `www.` does not make a
    different site."""
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.") == (root_host or "").lower().removeprefix("www.")


def normalize_url(url):
    """One canonical form per page: no fragment, no tracking params, no
    trailing slash on paths, so the same page is not crawled twice."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith(TRACKING_PARAMS)]
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((parts.scheme, (parts.hostname or "").lower(), path, urlencode(query), ""))


def crawlable(url):
    path = urlsplit(url).path.lower()
    return not path.endswith(SKIP_EXTENSIONS)


def is_document(url):
    return urlsplit(url).path.lower().endswith(DOCUMENT_EXTENSIONS)


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-run the SSRF check on every redirect hop, and remember the chain."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urlsplit(newurl).hostname
        if not host or not is_public_host(host):
            raise urllib.error.URLError("redirect to non-public host blocked")
        chain = getattr(req, "_chain", [])
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new._chain = chain + [(code, newurl)]
        return new


_opener = urllib.request.build_opener(_GuardedRedirect())


def throttled(status, headers):
    """A 429 always; a 503 only when it says when to come back (Retry-After).
    A bare 503 is usually a site that is down, which `check` and `audit` must
    report, not wait out (pure)."""
    return status == 429 or (status == 503 and retry_after(headers.get("retry-after")) is not None)
MAX_RETRIES = 4
BACKOFF_CAP_S = 120

# Called with (url, status, wait_s) each time a site throttles us, so a crawl
# can slow every later request too, not only retry this one.
on_throttle = None


def retry_after(value, now=None):
    """Retry-After as seconds: delta-seconds or an HTTP date; None when absent
    or unreadable (pure)."""
    if value is None or not str(value).strip():
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, IndexError):
        return None
    return max(0.0, when - (time.time() if now is None else now))


def backoff(attempt, header=None, jitter=0.0):
    """Seconds before the nth retry: what the site asks, never less than the
    doubling floor (5, 10, 20, 40), capped. A site that sends `Retry-After: 0`
    with its 429s would otherwise get four retries inside a second (pure)."""
    floor = 5 * 2 ** (attempt - 1)
    return min(BACKOFF_CAP_S, max(retry_after(header) or 0, floor) + jitter)


def fetch(url, cap=2_000_000, timeout=30, method="GET", sleep=time.sleep):
    """`fetch_once`, retrying a throttled answer after the wait the site asks for
    (or the doubling floor), up to four times; the last answer is returned
    as it came."""
    attempt = 1
    while True:
        r = fetch_once(url, cap=cap, timeout=timeout, method=method)
        if not throttled(r["status"], r["headers"]) or attempt > MAX_RETRIES:
            return r
        wait = backoff(attempt, r["headers"].get("retry-after"), jitter=random.random())
        if on_throttle:
            on_throttle(url, r["status"], wait)
        sleep(wait)
        attempt += 1


def fetch_once(url, cap=2_000_000, timeout=30, method="GET"):
    """Fetch a URL with the guard. Returns a dict: status, final_url, chain
    (the redirect hops as (status, url)), headers, body (bytes, capped).
    Raises urllib errors for network failures; an HTTP error status is a
    normal result (status set, body may be empty)."""
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}, method=method)
    req._chain = []
    try:
        with _opener.open(req, timeout=timeout) as resp:
            body = b"" if method == "HEAD" else resp.read(cap + 1)
            headers = {k.lower(): v for k, v in resp.headers.items()}
            body = decompress(body, headers.get("content-encoding", ""))
            return {"status": resp.status, "final_url": resp.geturl(), "chain": _chain_of(resp, req),
                    "headers": headers, "body": body[:cap], "truncated": len(body) > cap}
    except urllib.error.HTTPError as e:
        body = b"" if method == "HEAD" else (e.read(cap) if hasattr(e, "read") else b"")
        headers = {k.lower(): v for k, v in (e.headers or {}).items()}
        return {"status": e.code, "final_url": e.geturl() if hasattr(e, "geturl") else url,
                "chain": [], "headers": headers, "body": decompress(body, headers.get("content-encoding", "")),
                "truncated": False}


def decompress(body, encoding=""):
    """Some servers gzip even when asked not to; the magic bytes decide."""
    if body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except (OSError, EOFError):
            return body
    if "deflate" in (encoding or ""):
        try:
            return zlib.decompress(body)
        except zlib.error:
            try:
                return zlib.decompress(body, -zlib.MAX_WBITS)
            except zlib.error:
                return body
    return body


def _chain_of(resp, req):
    # urllib replaces the request on each hop; the final one carries the chain
    chain = getattr(resp, "_chain", None)
    if chain is None:
        chain = getattr(req, "_chain", [])
    return list(chain)


def fetch_text(url, cap=2_000_000, timeout=30):
    r = fetch(url, cap=cap, timeout=timeout)
    return r["body"].decode("utf-8", "replace")


def fetch_bytes(url, cap, content_types=IMAGE_CONTENT_TYPES, sleep=time.sleep):
    """Bytes of a URL when its Content-Type starts with one of
    `content_types` and it is under `cap`; else (None, content_type).
    Throttled answers are retried the way `fetch` retries them."""
    attempt = 1
    while True:
        try:
            return _fetch_bytes_once(url, cap, content_types)
        except urllib.error.HTTPError as e:
            headers = {k.lower(): v for k, v in (e.headers or {}).items()}
            if not throttled(e.code, headers) or attempt > MAX_RETRIES:
                raise
            wait = backoff(attempt, headers.get("retry-after"), jitter=random.random())
            if on_throttle:
                on_throttle(url, e.code, wait)
            sleep(wait)
            attempt += 1


def _fetch_bytes_once(url, cap, content_types):
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    with _opener.open(req, timeout=30) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if content_types and not ctype.startswith(content_types):
            return None, ctype
        data = resp.read(cap + 1)
        if len(data) > cap:
            return None, ctype
        return decompress(data, resp.headers.get("Content-Encoding", "")), ctype
