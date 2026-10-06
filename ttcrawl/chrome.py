"""The browsers tt-crawl drives, choosing one, and installing them.

One browser reads a crawl's pages and takes its screenshots (`--browser`).
Chrome, the default, renders and paints as the browsers people use do; it
is the heavier of the two and loads anything, so every request it makes goes
through cdp.RequestGuard. Obscura is small and fast and refuses private
addresses itself, but paints some things differently (a circle's curve, a
box sized only by its aspect ratio).

`tt-crawl setup` fetches Google's chrome-headless-shell from
the Chrome for Testing channel into the user's own folders, adds the system
libraries and basic fonts it needs through apt when they are missing, and
links it into ~/.local/bin; it installs Obscura's release build the same
way and puts a `tt-crawl` launcher on the PATH. Nothing here needs a key or reaches a provider.
"""
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

from . import browser, cdp, net, say

CFT_JSON = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
OBSCURA_REPO = "https://github.com/h4ckf0r0day/obscura"
OBSCURA_DEFAULT = "v0.2.2"
HOME = os.path.expanduser("~")
LOCAL_BIN = os.path.join(HOME, ".local", "bin")
SHARE = os.path.join(HOME, ".local", "share", "tt-crawl")
CHROME_CANDIDATES = (
    os.path.join(LOCAL_BIN, "chrome-headless-shell"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
)
# The shared libraries chrome-headless-shell links against, by the apt
# package that ships each (Ubuntu 24.04 on renamed several to ...t64).
LIB_PACKAGES = {
    "libnss3.so": ["libnss3"], "libnssutil3.so": ["libnss3"], "libsmime3.so": ["libnss3"],
    "libnspr4.so": ["libnspr4"], "libatk-1.0.so.0": ["libatk1.0-0t64", "libatk1.0-0"],
    "libatk-bridge-2.0.so.0": ["libatk-bridge2.0-0t64", "libatk-bridge2.0-0"],
    "libatspi.so.0": ["libatspi2.0-0t64", "libatspi2.0-0"], "libcups.so.2": ["libcups2t64", "libcups2"],
    "libdrm.so.2": ["libdrm2"], "libxkbcommon.so.0": ["libxkbcommon0"], "libXcomposite.so.1": ["libxcomposite1"],
    "libXdamage.so.1": ["libxdamage1"], "libXfixes.so.3": ["libxfixes3"], "libXrandr.so.2": ["libxrandr2"],
    "libgbm.so.1": ["libgbm1"], "libasound.so.2": ["libasound2t64", "libasound2"],
    "libpango-1.0.so.0": ["libpango-1.0-0"], "libcairo.so.2": ["libcairo2"], "libdbus-1.so.3": ["libdbus-1-3"],
    "libexpat.so.1": ["libexpat1"], "libxcb.so.1": ["libxcb1"], "libX11.so.6": ["libx11-6"], "libXext.so.6": ["libxext6"],
    "libglib-2.0.so.0": ["libglib2.0-0t64", "libglib2.0-0"], "libgio-2.0.so.0": ["libglib2.0-0t64", "libglib2.0-0"],
    "libgobject-2.0.so.0": ["libglib2.0-0t64", "libglib2.0-0"], "libudev.so.1": ["libudev1"],
}
FONT_PACKAGES = ["fontconfig", "fonts-liberation", "fonts-dejavu-core", "fonts-noto-color-emoji"]


def find_chrome(env=os.environ, exists=os.path.isfile, which=shutil.which):
    """A Chrome to drive, or None: $CHROME_BIN, then
    chrome-headless-shell on the PATH, then the usual places."""
    explicit = env.get("CHROME_BIN")
    if explicit:
        return explicit if exists(explicit) else None
    found = which("chrome-headless-shell")
    if found:
        return found
    return next((c for c in CHROME_CANDIDATES if exists(c)), None)


def can_install_chrome():
    """Chrome for Testing publishes Linux builds for x86-64 only."""
    return sys.platform.startswith("linux") and platform.machine().lower() in ("x86_64", "amd64")


def missing_libraries(binary, run=subprocess.run):
    """The shared libraries `binary` needs that the system lacks (pure given ldd's output)."""
    try:
        out = run(["ldd", binary], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return sorted({m.group(1) for m in re.finditer(r"^\s*(\S+) => not found", out, re.M)})


def packages_for(libs):
    """The apt package choices for missing libraries (pure): [[first choice, fallback], ...]."""
    out, unknown = [], []
    for lib in libs:
        choices = LIB_PACKAGES.get(lib)
        if choices and choices not in out:
            out.append(choices)
        elif not choices:
            unknown.append(lib)
    return out, unknown


def apt_install(choices, run=subprocess.run, log=print):
    """Install each package (the first of its choices that apt knows) with
    sudo, quietly; returns the ones that could not be installed."""
    if not shutil.which("apt-get"):
        return [c[0] for c in choices]
    sudo = [] if os.geteuid() == 0 else ["sudo", "-n"]
    run(sudo + ["apt-get", "update", "-qq"], capture_output=True, timeout=600)
    failed = []
    for options in choices:
        for pkg in options:
            r = run(sudo + ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "-qq",
                            "--no-install-recommends", pkg], capture_output=True, timeout=900)
            if r.returncode == 0:
                log("  installed %s" % pkg)
                break
        else:
            failed.append(options[0])
    return failed


def _download(url, timeout=600):
    req = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def install_chrome(log=print):
    """chrome-headless-shell, current stable, into ~/.local/share/tt-crawl,
    linked as ~/.local/bin/chrome-headless-shell; its libraries and fonts
    through apt when missing. Returns the binary's path."""
    if not can_install_chrome():
        raise RuntimeError("Chrome for Testing has Linux builds for x86-64 only; this is %s %s"
                           % (sys.platform, platform.machine()))
    channels = json.loads(_download(CFT_JSON, timeout=60))["channels"]["Stable"]
    version = channels["version"]
    url = next(d["url"] for d in channels["downloads"]["chrome-headless-shell"] if d["platform"] == "linux64")
    home = os.path.join(SHARE, "chrome", version)
    binary = os.path.join(home, "chrome-headless-shell-linux64", "chrome-headless-shell")
    if not os.path.isfile(binary):
        log("chrome-headless-shell %s: downloading" % version)
        data = _download(url)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(home)
        for dirpath, _, files in os.walk(home):          # zip loses the executable bits
            for f in files:
                os.chmod(os.path.join(dirpath, f), 0o755)
    choices, unknown = packages_for(missing_libraries(binary))
    if unknown:
        log("  libraries with no known package: %s" % ", ".join(unknown))
    if not shutil.which("fc-list") or not subprocess.run(["fc-list"], capture_output=True, text=True).stdout.strip():
        choices += [[p] for p in FONT_PACKAGES]
    failed = apt_install(choices, log=log) if choices else []
    if failed:
        log("  could not install: %s" % ", ".join(failed))
    still = missing_libraries(binary)
    if still:
        raise RuntimeError("chrome-headless-shell still lacks %s" % ", ".join(still))
    # linked onto the PATH only once it can run, so find_chrome never finds a half install
    os.makedirs(LOCAL_BIN, exist_ok=True)
    link = os.path.join(LOCAL_BIN, "chrome-headless-shell")
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(binary, link)
    return link


def _bin_dir():
    """Where a browser binary goes: /usr/local/bin when this user can write
    there (directly or with sudo), else ~/.local/bin, the order find_obscura
    looks in, so an upgrade replaces the copy on the PATH."""
    if os.access("/usr/local/bin", os.W_OK):
        return "/usr/local/bin", []
    if shutil.which("sudo") and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
        return "/usr/local/bin", ["sudo", "-n"]
    os.makedirs(LOCAL_BIN, exist_ok=True)
    return LOCAL_BIN, []


def install_obscura(version=OBSCURA_DEFAULT, log=print):
    """Obscura's release build for this machine (with its worker, which the
    parallel `scrape` command spawns), unless that version is already there.
    Returns the binary."""
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(platform.machine().lower())
    system = "linux" if sys.platform.startswith("linux") else "macos" if sys.platform == "darwin" else None
    if not arch or not system:
        raise RuntimeError("no Obscura build for %s %s" % (sys.platform, platform.machine()))
    dest, sudo = _bin_dir()
    binary, stamp = os.path.join(dest, "obscura"), os.path.join(dest, ".obscura-version")
    try:
        with open(stamp) as f:
            if os.path.isfile(binary) and f.read().strip() == version:
                return binary
    except OSError:
        pass
    log("obscura %s: downloading into %s" % (version, dest))
    data = _download("%s/releases/download/%s/obscura-%s-%s.tar.gz" % (OBSCURA_REPO, version, arch, system))
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            t.extractall(tmp)
        for name in ("obscura", "obscura-worker"):
            found = next((os.path.join(d, name) for d, _, fs in os.walk(tmp) if name in fs), None)
            if found:
                subprocess.run(sudo + ["install", "-m", "755", found, os.path.join(dest, name)], check=True)
            elif name == "obscura":
                raise RuntimeError("no obscura binary in the %s release" % version)
        with open(os.path.join(tmp, "stamp"), "w") as f:
            f.write(version)
        subprocess.run(sudo + ["install", "-m", "644", os.path.join(tmp, "stamp"), stamp], check=True)
    return binary


class Chrome:
    """Chrome on a loopback CDP port for the life of a crawl. Chrome loads
    anything it is pointed at, so each page's requests go through
    cdp.RequestGuard (needs_guard)."""
    engine, needs_guard = "chrome", True

    def __init__(self, binary, popen=subprocess.Popen):
        self.port = cdp.free_port()
        self.profile = tempfile.mkdtemp(prefix="tt-crawl-chrome-")
        self.proc = popen([binary, "--headless", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                           "--hide-scrollbars", "--font-render-hinting=none", "--no-first-run", "--mute-audio",
                           "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=%d" % self.port,
                           "--user-data-dir=%s" % self.profile, "about:blank"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.ws_url = cdp.wait_for_endpoint(self.port, tries=80)
        except BaseException:          # interrupted while waiting: leave no Chrome behind
            self.stop()
            raise
        if not self.ws_url:
            self.stop()
            raise cdp.CDPError("chrome never opened its debugging port")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


def driver(choice, install=True, log=None):
    """The browser a crawl reads pages and takes screenshots with, as
    (cdp.Driver or None, a note or None). chrome: Chrome when it is on the
    machine or can be installed, else Obscura, with a note saying so; a
    Chrome that will not start hands over to Obscura too (Driver.note).
    obscura: Obscura. None when neither is there."""
    log = log or (lambda m: sys.stderr.write(m + "\n"))
    note = None
    if choice == "chrome":
        binary = find_chrome()
        if not binary and install and can_install_chrome():
            try:
                binary = install_chrome(log=log)
            except Exception as e:          # an install that fails costs fidelity, never the crawl
                note = "chrome could not be installed (%s)" % str(e).split("\n")[0][:160]
        obscura = browser.find_obscura()
        if binary:
            return cdp.Driver(binary, browser=Chrome, fallback=(obscura, cdp.Obscura) if obscura else None), None
        note = note or "chrome is not on this machine and cannot be installed here"
    obscura = browser.find_obscura()
    if obscura:
        return cdp.Driver(obscura, browser=cdp.Obscura), note and note + "; obscura used instead"
    return None, (note + "; " if note else "") + "obscura is not installed: no browser"


def install_launcher(which=shutil.which, log=print):
    """A `tt-crawl` command on the PATH, whatever pip did with its console
    script (a user install lands in ~/.local/bin, which a service shell may
    not have). Returns its path."""
    found = which("tt-crawl")
    if found:
        return found
    dest, sudo = _bin_dir()
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "tt-crawl")
        with open(script, "w") as f:
            f.write('#!/bin/sh\nexec python3 -m ttcrawl "$@"\n')
        subprocess.run(sudo + ["install", "-m", "755", script, os.path.join(dest, "tt-crawl")], check=True)
    log("tt-crawl launcher -> %s" % dest)
    return os.path.join(dest, "tt-crawl")


def setup(log=print, steps=None):
    """Everything a machine needs after `pip install`: the launcher, Chrome
    and Obscura. Each step that fails is reported and the rest still run.
    Returns {name: path or None} and {name: error}."""
    steps = steps or (("tt-crawl", install_launcher), ("chrome", install_chrome), ("obscura", install_obscura))
    done, errors = {}, {}
    for name, step in steps:
        try:
            done[name] = step(log=log)
        except Exception as e:          # one missing browser leaves the other usable
            done[name] = None
            errors[name] = str(e).split("\n")[0][:300]
    return done, errors


def run_setup(args):
    log = lambda m: sys.stderr.write(m + "\n")
    done, errors = setup(log=log)
    browsers = [b for b in ("chrome", "obscura") if done.get(b)]
    ok = bool(browsers) and bool(done.get("tt-crawl"))
    lines = ["%s: %s" % (name, path or "failed: " + errors.get(name, "not installed")) for name, path in done.items()]
    if not ok:
        return say.fail(args, 1, "no browser installed" if done.get("tt-crawl") else "tt-crawl is not on the PATH",
                        "tt-crawl setup again once that is fixed (it is safe to re-run)", *lines)
    say.done(args, {"ok": ok, **done, "errors": errors}, "ready: %s" % ", ".join(n for n, p in done.items() if p), lines,
             "tt-crawl playbook, for the steps of each job")
    return 0


def add_parser(sub):
    p = say.command(sub, "setup", "after pip install: tt-crawl on the PATH, then Chrome and Obscura (safe to re-run)",
                    "Prints where each of tt-crawl, chrome and obscura is, or why it failed; one failed step does not "
                    "stop the others. Exit 1 when tt-crawl is not on the PATH or no browser could be installed.")
    p.set_defaults(func=run_setup)
