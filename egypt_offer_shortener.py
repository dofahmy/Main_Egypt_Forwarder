"""Railway short-link service + HTTPS client for the local Egypt forwarder."""
from __future__ import annotations

import html
import hmac
import json
import os
import re
import secrets
import sqlite3
import ssl
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from urllib.request import Request, urlopen


_ASIN_PATH = re.compile(r"/(?:dp|gp/product)/[A-Z0-9]{10}(?:[/?]|$)", re.I)
_CODE = re.compile(r"/[A-Za-z0-9_-]{8,32}/?")


def _database_path() -> Path:
    return Path(os.getenv("EGYPT_SHORT_DB_PATH", "egypt_offer_links.db"))


def _base_url() -> str:
    value = os.getenv("EGYPT_SHORT_BASE_URL", "").strip().rstrip("/")
    parsed = urlsplit(value)
    if not value:
        return ""
    if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query:
        raise ValueError("EGYPT_SHORT_BASE_URL must be a root HTTPS URL")
    return value


def _connect():
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS short_links (
        code TEXT PRIMARY KEY,
        target_url TEXT NOT NULL,
        created_at TEXT NOT NULL,
        click_count INTEGER NOT NULL DEFAULT 0,
        last_clicked_at TEXT
    )""")
    return conn


@contextmanager
def _db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _valid_product_url(url: str) -> bool:
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in ("amazon.eg", "www.amazon.eg"):
        return False
    if not _ASIN_PATH.search(p.path):
        return False
    params = dict(parse_qsl(p.query))
    return all(params.get(key) for key in ("tag", "ref", "linkCode", "linkId"))


def shorten_product_url(url: str) -> str:
    """Always issue a fresh code; keep the full Amazon query untouched."""
    base = _base_url()
    decoded = html.unescape(url)
    if not base or not _valid_product_url(decoded):
        return url
    for _ in range(10):
        code = secrets.token_urlsafe(9)
        try:
            with _db() as conn:
                conn.execute(
                    "INSERT INTO short_links(code,target_url,created_at) VALUES(?,?,?)",
                    (code, decoded, datetime.now(timezone.utc).isoformat()),
                )
            return f"{base}/{code}"
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("Could not allocate unique short URL")


def request_short_url(url: str) -> str:
    """Local forwarder sends a signed creation request to the Railway service."""
    base = _base_url()
    key = os.getenv("EGYPT_SHORT_API_KEY", "").strip()
    decoded = html.unescape(url)
    if not base or not key or not _valid_product_url(decoded):
        return url
    payload = json.dumps({"url": decoded}).encode("utf-8")
    request = Request(
        f"{base}/api/links", data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        # Windows curl.exe uses the OS certificate store, while urllib's
        # OpenSSL chain can select an expired intermediate certificate.
        # truststore keeps full TLS verification using the OS trust store.
        context = None
        if sys.platform == "win32":
            try:
                import truststore
                context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            except ImportError:
                print("⚠️ ثبّتي شهادات Windows لـ Python: py -m pip install truststore")
        with urlopen(request, timeout=8, context=context) as response:
            result = json.load(response)
        short = result.get("short_url", "")
        if short.startswith(base + "/"):
            return short
        # The service may still have its Railway domain configured as its
        # public base while the laptop uses a custom domain to the same app.
        # Reuse only the opaque code; the laptop's configured domain is the
        # one that readers should see. A malformed response remains an error.
        parsed_short = urlsplit(short)
        if (parsed_short.scheme == "https" and parsed_short.hostname
                and not parsed_short.query and not parsed_short.fragment
                and _CODE.fullmatch(parsed_short.path)):
            print("⚠️ دومين الاختصار في Railway مختلف عن الكمبيوتر؛ استخدمت دومين الكمبيوتر")
            return base + parsed_short.path
        print(f"⚠️ خدمة الاختصار رجّعت استجابة غير صالحة؛ هرسل لينك أمازون الكامل: {result!r}")
    except Exception as exc:
        print(f"⚠️ خدمة الاختصار غير متاحة؛ هرسل لينك أمازون الكامل: {type(exc).__name__}: {exc}")
    return url


def shorten_amazon_links(text: str, link_pattern) -> str:
    """Replace only canonical Amazon product URLs, including HTML-escaped ones."""
    if not _base_url() or not os.getenv("EGYPT_SHORT_API_KEY", "").strip():
        return text
    return link_pattern.sub(lambda match: request_short_url(match.group(0)), text)


def lookup(code: str, *, count_click: bool = True) -> str | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,32}", code):
        return None
    with _db() as conn:
        row = conn.execute("SELECT target_url FROM short_links WHERE code=?", (code,)).fetchone()
        if not row:
            return None
        if count_click:
            conn.execute(
                """UPDATE short_links SET click_count=click_count+1,
                   last_clicked_at=? WHERE code=?""",
                (datetime.now(timezone.utc).isoformat(), code),
            )
        return row[0]


class RedirectHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if urlsplit(self.path).path != "/api/links":
            self.send_error(404)
            return
        configured_key = os.getenv("EGYPT_SHORT_API_KEY", "").strip()
        supplied = self.headers.get("Authorization", "")
        if not configured_key or not hmac.compare_digest(supplied, f"Bearer {configured_key}"):
            self.send_error(401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not (0 < length <= 8192):
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            url = body.get("url", "")
            if not isinstance(url, str) or not _valid_product_url(url):
                raise ValueError("Not a supported Amazon Egypt product URL")
            short = shorten_product_url(url)
            payload = json.dumps({"short_url": short}).encode("utf-8")
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        except Exception:
            self.send_error(503)
            return
        self.send_response(201)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_HEAD(self):
        route = urlsplit(self.path).path
        if route == "/health":
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        target = lookup(route.strip("/"), count_click=False) if _CODE.fullmatch(route) else None
        if not target:
            self.send_error(404)
            return
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == "/health":
            payload = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if not _CODE.fullmatch(route):
            self.send_error(404)
            return
        target = lookup(route.strip("/"))
        if not target:
            self.send_error(404)
            return
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()


def run_redirect_server() -> None:
    """Run on Railway, independent of the Telegram forwarder on the laptop."""
    base = _base_url()
    if not base or not os.getenv("EGYPT_SHORT_API_KEY", "").strip():
        raise RuntimeError("Set EGYPT_SHORT_BASE_URL and EGYPT_SHORT_API_KEY")
    with _db():
        pass
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), RedirectHandler)
    print(f"✅ Short links: {base} → local port {port}")
    server.serve_forever()


if __name__ == "__main__":
    run_redirect_server()
