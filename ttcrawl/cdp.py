"""A small Chrome DevTools Protocol client over a standard-library WebSocket,
enough to drive Chrome or `obscura serve`: read a page as the browser built
it, and take it whole as screenshots.

One CDP capture is refused past 33,554,432 pixels (about 23,300px tall at
1440 wide), so a page is captured as strips of a fixed height instead: every
strip is under the limit whatever the page's length, and a strip is also the
size a model reads well.
"""
import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.request

# What a model sees at full resolution (Claude 4.7 and later): a long edge of
# at most 2576px and at most 4784 visual tokens, one per 28x28 patch; a larger
# image is scaled down first and its small text lost.
MODEL_EDGE, MODEL_TOKENS, PATCH = 2576, 4784, 28
MAX_STRIPS = 40            # a longer page is noted as truncated
PAGE_MAX = 16384           # Chrome's largest capture; a longer whole page is scaled to fit
VIEWPORT = (1440, 1000)


class CDPError(Exception):
    """The browser or its connection failed: a fresh browser may do better."""


class PageError(CDPError):
    """This page failed in a working browser (refused, empty, too slow): a
    fresh browser would fail it the same way."""


class WebSocket:
    """Text frames only, client side (masked), as CDP needs."""

    def __init__(self, url, timeout=60):
        rest = url.split("://", 1)[1]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("websocket handshake closed")
            head += chunk
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status + b" ":
            raise CDPError("websocket handshake refused: %s" % status.decode("latin-1"))
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _frame(self):
        """One complete frame off the front of the buffer, or None when it has
        not all arrived: nothing is consumed until it has, so a read that times
        out mid-frame leaves the stream where it was."""
        buf = self.buf
        if len(buf) < 2:
            return None
        b1, b2 = buf[0], buf[1]
        n, at = b2 & 0x7F, 2
        if n == 126:
            if len(buf) < 4:
                return None
            n, at = struct.unpack(">H", buf[2:4])[0], 4
        elif n == 127:
            if len(buf) < 10:
                return None
            n, at = struct.unpack(">Q", buf[2:10])[0], 10
        mask = None
        if b2 & 0x80:
            if len(buf) < at + 4:
                return None
            mask, at = buf[at:at + 4], at + 4
        if len(buf) < at + n:
            return None
        payload, self.buf = buf[at:at + n], buf[at + n:]
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return b1, payload

    def send(self, text):
        data = text.encode()
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        self.sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        parts = []
        while True:
            frame = self._frame()
            if frame is None:
                chunk = self.sock.recv(1 << 16)
                if not chunk:
                    raise CDPError("websocket closed")
                self.buf += chunk
                continue
            b1, payload = frame
            op = b1 & 0x0F
            if op == 0x8:
                raise CDPError("websocket closed by the browser")
            if op == 0x9:                      # ping: nothing to keep
                continue
            parts.append(payload)
            if b1 & 0x80:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Session:
    """One page on a browser's CDP endpoint (flattened target session).
    `on_event(session, event)` sees every event as it arrives (and may answer
    it with `send`, as the request guard does); the rest wait in `events`."""

    def __init__(self, ws_url, timeout=60, on_event=None):
        self.ws = WebSocket(ws_url, timeout=timeout)
        self.next_id = 0
        self.events = []
        self.on_event = on_event
        self.target = None
        try:
            self.target = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
            self.session = self.call("Target.attachToTarget", {"targetId": self.target, "flatten": True})["sessionId"]
        except BaseException:
            self.close()
            raise

    def send(self, method, params=None, session=True):
        """A command whose answer nobody waits for; returns its id."""
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if session and getattr(self, "session", None):
            msg["sessionId"] = self.session
        self.ws.send(json.dumps(msg))
        return self.next_id

    def _incoming(self, msg):
        if "method" in msg:
            if self.on_event and self.on_event(self, msg):
                return
            self.events.append(msg)

    def call(self, method, params=None, session=True, timeout=60):
        want = self.send(method, params, session)
        self.ws.sock.settimeout(timeout)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                reply = json.loads(self.ws.recv())
            except socket.timeout:
                break
            if reply.get("id") == want:
                if "error" in reply:
                    raise PageError("%s: %s" % (method, reply["error"].get("message")))
                return reply.get("result", {})
            self._incoming(reply)
        raise PageError("%s timed out" % method)

    def wait_event(self, name, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for i, e in enumerate(self.events):
                if e["method"] == name:
                    return self.events.pop(i)
            self.ws.sock.settimeout(max(0.1, deadline - time.monotonic()))
            try:
                self._incoming(json.loads(self.ws.recv()))
            except socket.timeout:
                break
        return None

    def evaluate(self, expression, await_promise=False, timeout=60):
        r = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                           "awaitPromise": await_promise}, timeout=timeout)
        return (r.get("result") or {}).get("value")

    def close(self):
        if self.target:
            try:
                self.call("Target.closeTarget", {"targetId": self.target}, session=False, timeout=10)
            except Exception:
                pass
        self.ws.close()


class RequestGuard:
    """For a browser that loads anything (Chrome): every request the page
    makes is paused, and one to a private, loopback, link-local or reserved
    address is refused, so the SSRF rail holds for the page, each redirect
    and every subresource. Obscura refuses those itself."""

    def __init__(self, is_public=None, allow_hosts=()):
        from . import net
        self.is_public = is_public or net.is_public_host
        # hosts let through although private: `tt-crawl shoot` on this
        # machine's own dev server (localhost), and nothing else
        self.allow_hosts = {h.lower() for h in allow_hosts}
        self.seen, self.refused = {}, []

    def allowed(self, url):
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        if parts.scheme in ("data", "blob", "about"):
            return True
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        host = parts.hostname.lower()
        if host in self.allow_hosts:
            return True
        if host not in self.seen:
            self.seen[host] = host != "localhost" and not host.endswith((".local", ".internal")) and self.is_public(host)
        return self.seen[host]

    def enable(self, session):
        session.call("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})

    def __call__(self, session, event):
        if event["method"] != "Fetch.requestPaused":
            return False
        params = event["params"]
        if self.allowed(params["request"]["url"]):
            session.send("Fetch.continueRequest", {"requestId": params["requestId"]})
        else:
            self.refused.append(params["request"]["url"])
            session.send("Fetch.failRequest", {"requestId": params["requestId"], "errorReason": "AccessDenied"})
        return True


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for_endpoint(port, tries):
    """The browser's websocket URL once its debugging port answers, or None."""
    for _ in range(tries):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
                return json.load(r)["webSocketDebuggerUrl"]
        except Exception:
            time.sleep(0.25)
    return None


class Obscura:
    """`obscura serve` on a loopback port for the life of a crawl. Private
    networks stay blocked (Obscura's own default), so the SSRF rail holds for
    the page and every subresource it loads."""
    engine, needs_guard = "obscura", False

    def __init__(self, binary, popen=subprocess.Popen):
        self.port = free_port()
        self.proc = popen([binary, "serve", "--port", str(self.port)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.ws_url = wait_for_endpoint(self.port, tries=60)
        except BaseException:
            self.stop()
            raise
        if not self.ws_url:
            self.stop()
            raise CDPError("obscura serve never came up")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class Driver:
    """One browser across a crawl, for reading pages and for screenshots:
    started on first use, replaced every USES_PER_BROWSER uses (a long run
    wears it down) and once more when it dies under a page. A browser that
    will not start hands over to `fallback` ((binary, browser class)), else
    the driver is broken and every page is read without it; `note` says
    which. Neither `render` nor `shoot` ever raises."""
    USES_PER_BROWSER = 30

    def __init__(self, binary, browser=Obscura, capture=None, renderer=None, fallback=None):
        self.binary, self.browser_cls, self.fallback = binary, browser, fallback
        self.capture = capture or screenshot_strips
        self.renderer = renderer or render
        self.browser, self.uses, self.broken, self.note = None, 0, None, None

    @property
    def engine(self):
        return None if self.broken else self.browser_cls.engine

    def _fresh(self):
        self.stop()
        while True:
            try:
                self.browser, self.uses = self.browser_cls(self.binary), 0
                return
            except (CDPError, OSError) as e:
                why = "%s would not start (%s)" % (self.browser_cls.engine, str(e).split("\n")[0][:160])
                if not self.fallback:
                    self.broken = why
                    self.note = why + "; pages read without a browser"
                    raise CDPError(why)
                (self.binary, self.browser_cls), self.fallback = self.fallback, None
                self.note = why + "; %s used instead" % self.browser_cls.engine

    def _use(self, fn, *args):
        err = self.broken
        for attempt in (1, 2):
            if self.broken:
                break
            try:
                if self.browser is None or self.uses >= self.USES_PER_BROWSER or attempt == 2:
                    self._fresh()
                self.uses += 1
                return fn(self.browser, *args)
            except PageError:
                raise
            except (CDPError, OSError, ValueError, KeyError) as e:
                err = str(e).split("\n")[0][:200]
        raise CDPError(err or "the browser failed")

    def render(self, url, styles=False):
        """{"html": ..., "styles": ...} as the browser built the page, or None."""
        try:
            return self._use(self.renderer, url, styles)
        except CDPError:
            return None

    def shoot(self, url, out_dir):
        """The screenshot meta, or {"error": ...}."""
        try:
            return self._use(self.capture, url, out_dir)
        except CDPError as e:
            return {"error": str(e)}

    def stop(self):
        if self.browser:
            self.browser.stop()
            self.browser = None


def strip_height(width):
    """The tallest strip a model reads unscaled at `width` (pure): 2576px up
    to 1456px wide, shorter beyond so the patches stay within budget."""
    cols = -(-int(width) // PATCH)
    return min(MODEL_EDGE // PATCH, MODEL_TOKENS // cols) * PATCH


def overview_scale(width, height):
    """The scale that fits a whole `width` x `height` page into one image a
    model reads unscaled (pure); 1 when it already fits."""
    by_edge = MODEL_EDGE / max(width, height)
    by_tokens = PATCH * (MODEL_TOKENS / (width * height)) ** 0.5
    return min(1.0, by_edge, by_tokens * 0.98)


def strip_plan(height, strip=None, max_strips=MAX_STRIPS):
    """(y, h) for each strip of a page `height` tall, and whether it was cut (pure)."""
    height = max(1, int(height))
    strip = strip or strip_height(1440)
    out, y = [], 0
    while y < height and len(out) < max_strips:
        out.append((y, min(strip, height - y)))
        y += strip
    return out, y < height


SETTLE_JS = "document.body ? document.body.innerText.length : 0"
# Sections an entrance animation keeps invisible until it plays (Elementor,
# AOS, WOW, Animate.css, GSAP-style reveals), shown as they end up: a
# screenshot of the page should hold everything a visitor scrolls to.
REVEAL_JS = """(() => { const css = `.elementor-invisible, [data-aos], .wow, .animate__animated,
  [class*="reveal"], [style*="opacity: 0"], [style*="opacity:0"] {
  opacity: 1 !important; visibility: visible !important; transform: none !important;
  animation: none !important; transition: none !important; }`;
  const el = document.createElement("style"); el.textContent = css; document.head.appendChild(el); })()"""
HEIGHT_JS = "Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)"


def open_page(browser, url, width=VIEWPORT[0], timeout=45, height=VIEWPORT[1], mobile=False, allow_hosts=()):
    """A session on `url`, loaded at `width` (desktop by default); Chrome's
    requests go through a RequestGuard. Raises CDPError when the page does
    not load."""
    guard = RequestGuard(allow_hosts=allow_hosts) if browser.needs_guard else None
    s = Session(browser.ws_url, timeout=timeout, on_event=guard)
    try:
        if guard:
            guard.enable(s)
        s.call("Page.enable")
        s.call("Emulation.setDeviceMetricsOverride",
               {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": mobile})
        nav = s.call("Page.navigate", {"url": url}, timeout=timeout)
        if nav.get("errorText"):
            raise PageError("%s: %s" % (url, nav["errorText"]))
        s.wait_event("Page.loadEventFired", timeout=timeout)
    except BaseException:
        s.close()
        raise
    return s


def settle(s, rounds=10, pause=0.5):
    """Wait until the page's text stops growing (a script still filling it in)."""
    last = -1
    for _ in range(rounds):
        n = s.evaluate(SETTLE_JS) or 0
        if n and n == last:
            break
        last = n
        time.sleep(pause)


def render(browser, url, styles=False, timeout=45):
    """The page as the browser built it: {"html": ..., "styles": ...} from
    browser.EXTRACT_JS. Raises CDPError when it does not render."""
    from .browser import extract_js, parse_eval
    s = open_page(browser, url, timeout=timeout)
    try:
        settle(s)
        got = parse_eval(s.evaluate(extract_js(styles), timeout=timeout))
    finally:
        s.close()
    if not got or not (got.get("html") or "").strip():
        raise PageError("%s rendered empty" % url)
    return got


def screenshot_strips(browser, url, out_dir, width=VIEWPORT[0], strip=None,
                      max_strips=MAX_STRIPS, timeout=45, height=VIEWPORT[1], mobile=False,
                      first_screen=False, allow_hosts=()):
    """The whole page under `out_dir`: `01.png`, `02.png`… strips a model
    reads at full resolution, `overview.png` (the whole page scaled to one
    image a model reads, when there is more than one strip), `page.png` (the
    whole page at full size, for people) and `meta.json`. Only the first
    `height` pixels with `first_screen`. Returns the meta dict, or raises
    CDPError."""
    strip = strip or strip_height(width)
    s = open_page(browser, url, width=width, timeout=timeout, height=height, mobile=mobile,
                  allow_hosts=allow_hosts)
    try:
        # Lazy images decode as they come into view: walk down the page once,
        # slowly enough for scroll-triggered sections, then show whatever an
        # entrance animation still hides and wait until the text stops growing.
        s.evaluate("(async () => { for (let y = 0; y < %s; y += 600) { scrollTo(0, y); "
                   "await new Promise(r => setTimeout(r, 150)); } scrollTo(0, 0); })()" % HEIGHT_JS,
                   await_promise=True, timeout=timeout)
        s.evaluate(REVEAL_JS)
        settle(s)
        page_height = s.evaluate(HEIGHT_JS) or height
        plan, truncated = ([(0, min(height, page_height))], False) if first_screen else strip_plan(page_height, strip, max_strips)
        os.makedirs(out_dir, exist_ok=True)
        for old in os.listdir(out_dir):     # an earlier run's strips and meta
            if old.endswith(".png") or old == "meta.json":
                os.remove(os.path.join(out_dir, old))

        def capture(name, y, h, scale=1):
            shot = s.call("Page.captureScreenshot", {
                "format": "png", "captureBeyondViewport": True,
                "clip": {"x": 0, "y": y, "width": width, "height": h, "scale": scale}}, timeout=timeout)
            with open(os.path.join(out_dir, name), "wb") as f:
                f.write(base64.b64decode(shot["data"]))
            return name

        files = [capture("%02d.png" % i, y, h) for i, (y, h) in enumerate(plan, 1)]
        whole = int(min(page_height, sum(h for _, h in plan)))
        page = capture("page.png", 0, whole, min(1.0, PAGE_MAX / whole)) if len(files) > 1 else files[0]
        overview = capture("overview.png", 0, whole, overview_scale(width, whole)) if len(files) > 1 else None
        meta = {"url": url, "width": width, "height": int(page_height), "strip_height": strip,
                "strips": files, "page": page, "overview": overview, "truncated": truncated,
                "engine": browser.engine}
        with open(os.path.join(out_dir, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2)
        return meta
    finally:
        s.close()
