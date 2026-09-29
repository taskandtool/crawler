"""A small Chrome DevTools Protocol client over a standard-library WebSocket,
enough to drive `obscura serve` for full-page screenshots.

`obscura fetch --screenshot` captures only the first screen, and one CDP
capture is refused past 33,554,432 pixels (about 23,300px tall at 1440 wide),
so a page is captured as strips of a fixed height instead: every strip is
under the limit whatever the page's length, and a strip is also the size a
model reads well.
"""
import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.request

STRIP_HEIGHT = 1600
MAX_STRIPS = 40            # 64,000px; a longer page is noted as truncated
VIEWPORT = (1440, 1000)


class CDPError(Exception):
    pass


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
        target = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.session = self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.target = target

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
            reply = json.loads(self.ws.recv())
            if reply.get("id") == want:
                if "error" in reply:
                    raise CDPError("%s: %s" % (method, reply["error"].get("message")))
                return reply.get("result", {})
            self._incoming(reply)
        raise CDPError("%s timed out" % method)

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

    def __init__(self, is_public=None):
        from . import net
        self.is_public = is_public or net.is_public_host
        self.seen, self.refused = {}, []

    def allowed(self, url):
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        if parts.scheme in ("data", "blob", "about"):
            return True
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        host = parts.hostname.lower()
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


class Obscura:
    """`obscura serve` on a loopback port for the life of a crawl. Private
    networks stay blocked (Obscura's own default), so the SSRF rail holds for
    the page and every subresource it loads."""
    engine, needs_guard = "obscura", False

    def __init__(self, binary, popen=subprocess.Popen):
        self.port = free_port()
        self.proc = popen([binary, "serve", "--port", str(self.port)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws_url = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/version", timeout=2) as r:
                    self.ws_url = json.load(r)["webSocketDebuggerUrl"]
                    break
            except Exception:
                time.sleep(0.25)
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


class Shooter:
    """Full-page screenshots across a crawl: one `obscura serve`, started on
    the first shot, replaced every `PAGES_PER_BROWSER` pages (it degrades on a
    long run) and once more when a page kills it. A shot never costs the page:
    `shoot` returns the meta, or {"error": ...}."""
    PAGES_PER_BROWSER = 15

    def __init__(self, binary, browser=Obscura, capture=None):
        self.binary, self.browser_cls = binary, browser
        self.capture = capture or screenshot_strips
        self.browser, self.shots = None, 0

    def _fresh(self):
        self.stop()
        self.browser, self.shots = self.browser_cls(self.binary), 0

    def shoot(self, url, out_dir):
        for attempt in (1, 2):
            try:
                if self.browser is None or self.shots >= self.PAGES_PER_BROWSER or attempt == 2:
                    self._fresh()
                self.shots += 1
                return self.capture(self.browser, url, out_dir)
            except (CDPError, OSError, ValueError, KeyError) as e:
                err = str(e).split("\n")[0][:200]
        return {"error": err}

    def stop(self):
        if self.browser:
            self.browser.stop()
            self.browser = None


def strip_plan(height, strip=STRIP_HEIGHT, max_strips=MAX_STRIPS):
    """(y, h) for each strip of a page `height` tall, and whether it was cut (pure)."""
    height = max(1, int(height))
    out, y = [], 0
    while y < height and len(out) < max_strips:
        out.append((y, min(strip, height - y)))
        y += strip
    return out, y < height


SETTLE_JS = "document.body ? document.body.innerText.length : 0"
HEIGHT_JS = "Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)"


def screenshot_strips(browser, url, out_dir, width=VIEWPORT[0], strip=STRIP_HEIGHT,
                      max_strips=MAX_STRIPS, timeout=45):
    """The whole page as PNG strips `01.png`, `02.png`… under `out_dir`, plus
    `meta.json`. Returns the meta dict, or raises CDPError."""
    guard = RequestGuard() if getattr(browser, "needs_guard", False) else None
    s = Session(browser.ws_url, timeout=timeout, on_event=guard)
    try:
        if guard:
            guard.enable(s)
        s.call("Page.enable")
        s.call("Emulation.setDeviceMetricsOverride",
               {"width": width, "height": VIEWPORT[1], "deviceScaleFactor": 1, "mobile": False})
        s.call("Page.navigate", {"url": url}, timeout=timeout)
        s.wait_event("Page.loadEventFired", timeout=timeout)
        # Lazy images decode as they come into view: walk down the page once,
        # then wait until the text stops growing.
        s.evaluate("(async () => { for (let y = 0; y < %s; y += 800) { scrollTo(0, y); "
                   "await new Promise(r => setTimeout(r, 60)); } scrollTo(0, 0); })()" % HEIGHT_JS,
                   await_promise=True, timeout=timeout)
        last = -1
        for _ in range(10):
            n = s.evaluate(SETTLE_JS) or 0
            if n and n == last:
                break
            last = n
            time.sleep(0.5)
        height = s.evaluate(HEIGHT_JS) or VIEWPORT[1]
        plan, truncated = strip_plan(height, strip, max_strips)
        os.makedirs(out_dir, exist_ok=True)
        for old in os.listdir(out_dir):     # an earlier run's strips and meta
            if old.endswith(".png") or old == "meta.json":
                os.remove(os.path.join(out_dir, old))
        files = []
        for i, (y, h) in enumerate(plan, 1):
            shot = s.call("Page.captureScreenshot", {
                "format": "png", "captureBeyondViewport": True,
                "clip": {"x": 0, "y": y, "width": width, "height": h, "scale": 1}}, timeout=timeout)
            name = "%02d.png" % i
            with open(os.path.join(out_dir, name), "wb") as f:
                f.write(base64.b64decode(shot["data"]))
            files.append(name)
        meta = {"url": url, "width": width, "height": int(height), "strip_height": strip,
                "strips": files, "truncated": truncated, "engine": getattr(browser, "engine", "obscura")}
        with open(os.path.join(out_dir, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2)
        return meta
    finally:
        s.close()
