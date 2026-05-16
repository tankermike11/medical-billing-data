import ssl
import time
import hashlib
import shutil
from pathlib import Path
from urllib.robotparser import RobotFileParser
from urllib.parse import urlparse

import httpx
import truststore

# Use the OS certificate store (Windows/macOS/Linux) instead of Python's bundled certifi.
# This resolves SSL errors on machines with corporate CA certificates or SSL inspection.
_ssl_ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

UA = "MASA-AdvocacyIngestor/0.1-pilot (+https://github.com/masa-health/billing-data)"
RATE_LIMIT_DELAY = 1.0  # seconds between requests to same host
_last_request: dict[str, float] = {}


def _polite_delay(host: str) -> None:
    now = time.monotonic()
    last = _last_request.get(host, 0.0)
    gap = RATE_LIMIT_DELAY - (now - last)
    if gap > 0:
        time.sleep(gap)
    _last_request[host] = time.monotonic()


def robots_allowed(url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser(robots_url)
    try:
        rp.read()
        return rp.can_fetch(UA, url)
    except Exception:
        return True


def download(url: str, dest: Path, *, force: bool = False) -> tuple[Path, str]:
    """Download url to dest. Returns (path, sha256). Skips if unchanged hash."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    host = urlparse(url).netloc

    if dest.exists() and not force:
        existing_hash = _sha256(dest)
        return dest, existing_hash

    _polite_delay(host)
    with httpx.stream("GET", url, headers={"User-Agent": UA}, follow_redirects=True, timeout=120, verify=_ssl_ctx) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=65536):
                f.write(chunk)
    shutil.move(tmp, dest)
    return dest, _sha256(dest)


def fetch_text(url: str) -> str:
    host = urlparse(url).netloc
    _polite_delay(host)
    r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=60, verify=_ssl_ctx)
    r.raise_for_status()
    return r.text


def fetch_json(url: str) -> dict | list:
    host = urlparse(url).netloc
    _polite_delay(host)
    r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=60, verify=_ssl_ctx)
    r.raise_for_status()
    return r.json()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
