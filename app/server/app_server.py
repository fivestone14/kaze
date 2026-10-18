# The Kaze web server the phone talks to: it serves the three screens in app/web, the live feed file fresh on every
# request and today's 1-minute closes from the store, all behind one Basic-auth token, on 127.0.0.1 for a tunnel.
import hmac
import json
import sqlite3
import sys
import threading
import time
from base64 import b64decode
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from engine import job_log, paths, session_clock, store

JOB = "app"
WEB_FOLDER = (paths.REPO_ROOT / "app" / "web").resolve()
FEED_ROUTE, BARS_ROUTE = "/latest.json", "/bars.json"
PORT_KEY, TOKEN_KEY, DEFAULT_PORT = "KAZE_APP_PORT", "KAZE_APP_TOKEN", 8791
AUTH_USER = "kaze"
BARS_CACHE_SECONDS = 20
HANDLER_TIMEOUT_SECONDS = 15
LOGGED_ROUTE_CHARS = 120
NO_STORE = "no-store"
PAGE_POLICY = "default-src 'self'"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".json": "application/json", ".woff2": "font/woff2",
                 ".txt": "text/plain; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
bars_cache = {"trade_date": None, "built_at": 0.0, "body": b""}
bars_lock = threading.Lock()


def read_settings():
    """The port and the token from .env; refuses to start without a token (the phone would be open to anyone)."""
    token = paths.ENV.get(TOKEN_KEY)
    if not token:
        raise SystemExit(f"kaze app: {TOKEN_KEY} is not set in {paths.ENV_FILE}; see .env.example")
    return int(paths.ENV.get(PORT_KEY) or DEFAULT_PORT), token


PORT, TOKEN = read_settings()
EXPECTED_AUTH = f"{AUTH_USER}:{TOKEN}".encode()


def authorized(header):
    """True when the Authorization header carries the Basic pair kaze:<token>, compared in constant time."""
    if not header or not header.startswith("Basic "):
        return False
    try:
        given = b64decode(header[6:], validate=True)
    except ValueError:
        return False
    return hmac.compare_digest(given, EXPECTED_AUTH)


def web_file(route):
    """The static file a route names inside app/web, or None when it is not a plain file there (no listings)."""
    name = "index.html" if route == "/" else route.lstrip("/")
    file = (WEB_FOLDER / name).resolve()
    if WEB_FOLDER not in file.parents or not file.is_file() or file.suffix not in CONTENT_TYPES:
        return None
    return file


def feed_document():
    """The live feed file's bytes, read fresh, or None when the engine has not written one yet."""
    file = paths.live_feed_file()
    return file.read_bytes() if file.exists() else None


def feed_trade_date():
    """The session the live feed describes (the date of its cut), or today's New York date when there is none or
    the file is half-written."""
    document = feed_document()
    try:
        cut_at = json.loads(document).get("cut_at") if document else None
    except ValueError:
        cut_at = None
    if cut_at:
        return session_clock.parse_iso(cut_at).date().isoformat()
    return session_clock.to_eastern(session_clock.now_utc()).date().isoformat()


class StoreMissing(Exception):
    """The store file is not there: /bars.json answers 503 and the log says so by name."""


def read_only_store():
    """A connection that only reads an existing store: opened read-only when the WAL sidecars exist (SQLite refuses a
    read-only open of a WAL store without them, as after a restore from backup), else opened without create and
    only ever queried."""
    file = paths.store_file()
    if not file.is_file():
        raise StoreMissing(f"no store at {file.name}")
    mode = "ro" if file.with_name(file.name + "-wal").exists() else "rw"
    conn = sqlite3.connect(f"file:{file}?mode={mode}", uri=True, timeout=store.BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    return conn


def bars_document():
    """The session's 1-minute closes as JSON bytes, from a read-only store connection, rebuilt at most every 20 s."""
    with bars_lock:
        trade_date = feed_trade_date()
        fresh = time.monotonic() - bars_cache["built_at"] < BARS_CACHE_SECONDS
        if fresh and bars_cache["trade_date"] == trade_date:
            return bars_cache["body"]
        conn = read_only_store()
        try:
            bars = [{"at": session_clock.iso_eastern(session_clock.parse_iso(bar["priced_at"])), "close": bar["close"]}
                    for bar in store.session_bars(conn, trade_date)]
        finally:
            conn.close()
        body = json.dumps({"trade_date": trade_date, "bars": bars}).encode()
        bars_cache.update(trade_date=trade_date, built_at=time.monotonic(), body=body)
        return body


class AppHandler(BaseHTTPRequestHandler):
    """One request: the token first, then one of the five things this server serves; a crash inside is a 503 and
    one log line, a client gone mid-reply is one log line, never a traceback."""
    timeout = HANDLER_TIMEOUT_SECONDS
    status_sent = None

    def do_GET(self):
        started = time.monotonic()
        route = self.path.split("?", 1)[0]
        logged = route[:LOGGED_ROUTE_CHARS]
        try:
            self.answer(route)
        except OSError as failure:
            job_log.write_line(JOB, f"client gone {logged} {type(failure).__name__}")
        except Exception as failure:
            job_log.write_line(JOB, f"error {logged} {type(failure).__name__}: {failure}")
            if self.status_sent is None:
                self.reply_if_possible(HTTPStatus.SERVICE_UNAVAILABLE, "Kaze: not available right now")
        job_log.write_line(JOB, f"{self.command} {logged} {self.status_sent} "
                                f"{round((time.monotonic() - started) * 1000)} ms")

    do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_GET

    def reply_if_possible(self, status, text):
        """A short reply that gives up quietly when the client has already gone."""
        try:
            self.send_plain(status, text)
        except OSError:
            pass

    def answer(self, route):
        """Route one request: the token gate first, then the method (only GET and HEAD read anything)."""
        if not authorized(self.headers.get("Authorization")):
            self.send_plain(HTTPStatus.UNAUTHORIZED, "Kaze: sign in", {"WWW-Authenticate": 'Basic realm="Kaze"'})
        elif self.command not in ("GET", "HEAD"):
            self.send_plain(HTTPStatus.METHOD_NOT_ALLOWED, "Kaze: GET only", {"Allow": "GET, HEAD"})
        elif route == FEED_ROUTE:
            document = feed_document()
            if document is None:
                self.send_plain(HTTPStatus.NOT_FOUND, "no live feed yet")
            else:
                self.send_bytes(HTTPStatus.OK, document, CONTENT_TYPES[".json"], NO_STORE)
        elif route == BARS_ROUTE:
            self.send_bytes(HTTPStatus.OK, bars_document(), CONTENT_TYPES[".json"], NO_STORE)
        else:
            file = web_file(route)
            if file is None:
                self.send_plain(HTTPStatus.NOT_FOUND, "not here")
            else:
                self.send_bytes(HTTPStatus.OK, file.read_bytes(), CONTENT_TYPES[file.suffix], "no-cache",
                                PAGE_POLICY if file.suffix == ".html" else None)

    def send_bytes(self, status, body, content_type, cache_control, policy=None):
        """Send a response with its content type, cache rule, length and, for the page, its content security policy
        (HEAD sends the headers only)."""
        self.status_sent = status.value
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        if policy:
            self.send_header("Content-Security-Policy", policy)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_plain(self, status, text, extra_headers=None):
        """A short plain-text answer (401, 404), with any extra headers first."""
        self.status_sent = status.value
        self.send_response(status)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        body = text.encode()
        self.send_header("Content-Type", CONTENT_TYPES[".txt"])
        self.send_header("Cache-Control", NO_STORE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def version_string(self):
        """The Server header: the name, no Python version."""
        return "Kaze"

    def log_message(self, format, *args):
        """Silence the default stderr line: the job log holds the one line per request, without headers or bodies."""


class QuietServer(ThreadingHTTPServer):
    """The threaded server with its traceback printer replaced by one log line, so launchd's capture file stays
    empty whatever a client does."""

    def handle_error(self, request, client_address):
        failure = sys.exc_info()[1]
        job_log.write_line(JOB, f"request dropped {type(failure).__name__}: {failure}")


def main():
    """Serve until stopped; launchd keeps it alive."""
    try:
        server = QuietServer(("127.0.0.1", PORT), AppHandler)
    except OSError as failure:
        raise SystemExit(f"kaze app: cannot listen on 127.0.0.1:{PORT} ({failure.strerror}); is another copy running?")
    server.daemon_threads = True
    job_log.write_line(JOB, f"serving {WEB_FOLDER} and {paths.live_feed_file()} on 127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        job_log.write_line(JOB, "stopped")
        sys.exit(0)


if __name__ == "__main__":
    main()
