"""Fetching, with the safety rails every subcommand shares.

Whoever asks for a URL may not be whoever runs the machine, so a host is
checked against private, loopback, link-local and reserved ranges before a
command requests it, and again on every redirect hop. The one exception is
the site running on this machine (http://localhost or 127.0.0.1), which
`check` and `audit` exist to test. A 429 (or a 503 with Retry-After) is
waited out and retried, never hammered. With $TTCRAWL_SIGN_IN set, a site
that asks for a sign-in is opened through the link that command gives
(`signed_in`), here and in the browser.
"""
import gzip
import http.cookiejar
import ipaddress
import json
import os
import random
import shlex
import socket
import subprocess
import time
import zlib
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request

from . import __version__

USER_AGENT = "tt-crawl/%s (+https://github.com/taskandtool/crawler)" % __version__
LOCAL_HOSTS = ("localhost", "127.0.0.1")
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


def local_http_url(url):
    """True for http://localhost[:port] or http://127.0.0.1[:port] (pure):
    the site running on this machine."""
    parts = urlsplit(url)
    return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS


def local_or_public_http_url(url):
    """What `check` and `audit` may test: a public http(s) URL, or the site
    running on this machine."""
    return local_http_url(url) or public_http_url(url)


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
    """Re-run the SSRF check on every redirect hop, and remember the chain. A
    local site may redirect within itself, never anywhere else local."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urlsplit(newurl).hostname
        within_local = local_http_url(newurl) and urlsplit(newurl).netloc == urlsplit(req.full_url).netloc
        if not host or not (within_local or is_public_host(host)):
            raise urllib.error.URLError("redirect to non-public host blocked: %s" % newurl)
        # one list for the first request and every hop, so fetch_once,
        # which holds only the first, reads every hop from it
        chain = getattr(req, "_chain", None)
        if chain is None:
            chain = req._chain = []
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            chain.append((code, newurl))
            new._chain = chain
        return new


# A sign-in's cookie, kept for the rest of the run.
_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(_GuardedRedirect(), urllib.request.HTTPCookieProcessor(_jar))

# Hosts the sign-in command said need no sign-in anywhere (or that it could
# not answer for), not asked again this run; the signed links it gave, by
# address; and the hosts this opener holds a sign-in's cookie for. Each kept
# well inside the few minutes a signed link lasts.
_open_as_is, _signed, _cookie_hosts = set(), {}, {}
SIGNED_FOR = 600


def signed_in(url, env=os.environ, run=subprocess.run, now=time.monotonic):
    """The address to open for `url`. $TTCRAWL_SIGN_IN, when set, names a
    command that gets the address and prints JSON whose `url` is what to
    open: a signed-in link for a page of the caller's own that asks for a
    sign-in, else the address unchanged, with `"site": true` when that holds
    for the whole host. Anything else it does, or a failure, opens `url` as
    it is and the host is not asked about again."""
    command, host = env.get("TTCRAWL_SIGN_IN"), urlsplit(url).hostname
    if not command or not host or host in _open_as_is:
        return url
    link, at = _signed.get(url, (None, 0))
    if link and now() - at < SIGNED_FOR:
        return link
    answer = {}
    try:
        out = run([os.path.expanduser(w) for w in shlex.split(command)] + [url],
                  capture_output=True, text=True, timeout=30)
        answer = json.loads(out.stdout or "{}")
        got = answer.get("url")
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        got = None
    if isinstance(got, str) and got != url and urlsplit(got).hostname == host:
        _signed[url] = (got, now())
        return got
    if got != url or answer.get("site") is True:
        _open_as_is.add(host)
    return url


def _open(req, timeout, now=time.monotonic):
    """`_opener.open`, signed in where the site asks. One sign-in per host
    serves the run's later requests through its cookie; the site's redirect
    takes the token out, so `final_url` never carries it, and that hop is
    left out of the chain."""
    url, host = req.full_url, urlsplit(req.full_url).hostname
    target = url if now() - _cookie_hosts.get(host, -SIGNED_FOR) < SIGNED_FOR else signed_in(url)
    if target == url:
        return _opener.open(req, timeout=timeout)
    req.full_url = target
    resp = _opener.open(req, timeout=timeout)
    # signed in only if the site kept the visitor (a refused link sends them
    # to sign in elsewhere): then its cookie serves the next requests
    if urlsplit(resp.geturl()).hostname == host:
        _cookie_hosts[host] = now()
    # the sign-in's own hop back to the address is not the site redirecting
    chain = getattr(req, "_chain", None)
    if chain and urlsplit(chain[0][1]).hostname == host:
        del chain[0]
    return resp


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
        with _open(req, timeout) as resp:
            body = b"" if method == "HEAD" else resp.read(cap + 1)
            headers = {k.lower(): v for k, v in resp.headers.items()}
            body = decompress(body, headers.get("content-encoding", ""))
            return {"status": resp.status, "final_url": resp.geturl(), "chain": list(req._chain),
                    "headers": headers, "body": body[:cap], "truncated": len(body) > cap}
    except urllib.error.HTTPError as e:
        body = b"" if method == "HEAD" else (e.read(cap) if hasattr(e, "read") else b"")
        headers = {k.lower(): v for k, v in (e.headers or {}).items()}
        return {"status": e.code, "final_url": e.geturl() if hasattr(e, "geturl") else url,
                "chain": list(getattr(req, "_chain", [])), "headers": headers,
                "body": decompress(body, headers.get("content-encoding", "")),
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
    with _open(req, 30) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if content_types and not ctype.startswith(content_types):
            return None, ctype
        data = resp.read(cap + 1)
        if len(data) > cap:
            return None, ctype
        return decompress(data, resp.headers.get("Content-Encoding", "")), ctype
