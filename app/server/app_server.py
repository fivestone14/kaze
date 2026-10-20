# The Kaze web server the phone talks to: the three screens in app/web, the live feed file fresh on every request,
# today's 1-minute closes from Mirai's bar file and SPX's live price as a stream of server-sent events. Everything sits
# behind one Basic-auth token on 127.0.0.1, for a tunnel to reach.
import hmac
import json
import select
import socket
import sys
import threading
import time
from base64 import b64decode
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine import paths, session_clock
from engine.collect import mirai_files
from engine.jobs import job_log

JOB = "app"
WEB_FOLDER = (paths.REPO_ROOT / "app" / "web").resolve()
FEED_ROUTE, BARS_ROUTE, PRICE_ROUTE = "/latest.json", "/bars.json", "/price.stream"
PORT_KEY, TOKEN_KEY, DEFAULT_PORT = "KAZE_APP_PORT", "KAZE_APP_TOKEN", 8791
AUTH_USER = "kaze"
BASIC_PREFIX = "Basic "
HANDLER_TIMEOUT_SECONDS = 15
# The price stream: the file polled four times a second, a comment line every 15 s so the tunnel (which drops a
# connection quiet for 100 s) keeps it, at most four open at once, each closed after 30 minutes for the page to reopen.
PRICE_POLL_SECONDS = 0.25
PRICE_HEARTBEAT_SECONDS = 15
PRICE_STREAMS_MAX = 4
PRICE_STREAM_SECONDS = 30 * 60
EVENT_STREAM = "text/event-stream"
LOGGED_ROUTE_CHARS = 120
NO_STORE = "no-store"
PAGE_POLICY = "default-src 'self'"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".json": "application/json", ".woff2": "font/woff2",
                 ".txt": "text/plain; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
bars_cache = {"key": None, "body": b""}
bars_lock = threading.Lock()
price_streams = {"open": 0}
price_streams_lock = threading.Lock()


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
    if not header or not header.startswith(BASIC_PREFIX):
        return False
    try:
        given = b64decode(header[len(BASIC_PREFIX):], validate=True)
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
    """The session the live feed describes (the date of its cut), else today's New York date."""
    # Today when there is no feed or the file is half-written.
    document = feed_document()
    try:
        cut_at = json.loads(document).get("cut_at") if document else None
    except ValueError:
        cut_at = None
    if cut_at:
        return session_clock.parse_iso(cut_at).date().isoformat()
    return session_clock.today_eastern().isoformat()


def bars_document():
    """The session's 1-minute closes from Mirai's bar file as JSON bytes, rebuilt only when that file has grown."""
    with bars_lock:
        trade_date = feed_trade_date()
        day = date.fromisoformat(trade_date)
        key = (trade_date, mirai_files.file_bytes(mirai_files.FILES["spx_bars"], day))
        if bars_cache["key"] == key:
            return bars_cache["body"]
        bars = [{"at": session_clock.iso_eastern(bar["at"]), "close": bar["close"]}
                for bar in mirai_files.read_spx_bars(day, session_clock.now_utc()) if bar.get("close") is not None]
        body = json.dumps({"trade_date": trade_date, "bars": bars}).encode()
        bars_cache.update(key=key, body=body)
        return body


def price_tick():
    """The live price as the stream sends it ({price, at} on the New York clock), or None while Mirai has none."""
    live = mirai_files.read_live_price()
    return None if live is None else {"price": live["price"], "at": session_clock.iso_eastern(live["at"])}


def take_price_stream():
    """Count one more open price stream; False when four are already open."""
    with price_streams_lock:
        if price_streams["open"] >= PRICE_STREAMS_MAX:
            return False
        price_streams["open"] += 1
        return True


def release_price_stream():
    """Count one price stream closed."""
    with price_streams_lock:
        price_streams["open"] -= 1


class AppHandler(BaseHTTPRequestHandler):
    """One request: the token first, then one of the six things this server serves, never a traceback."""
    # A crash inside is a 503 and one log line; a client gone mid-reply is one log line. The 15 s timeout bounds each
    # read and write; a price stream ends when the phone closes its end (checked each poll) or after 30 minutes.
    timeout = HANDLER_TIMEOUT_SECONDS
    status_sent = None

    def do_GET(self):
        """Answer one request and write its one log line (route, status, milliseconds)."""
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
                self.send(HTTPStatus.OK, document, CONTENT_TYPES[".json"], NO_STORE)
        elif route == BARS_ROUTE:
            self.send(HTTPStatus.OK, bars_document(), CONTENT_TYPES[".json"], NO_STORE)
        elif route == PRICE_ROUTE:
            self.stream_price()
        else:
            file = web_file(route)
            if file is None:
                self.send_plain(HTTPStatus.NOT_FOUND, "not here")
            else:
                headers = {"Content-Security-Policy": PAGE_POLICY} if file.suffix == ".html" else None
                self.send(HTTPStatus.OK, file.read_bytes(), CONTENT_TYPES[file.suffix], "no-cache", headers)

    def stream_price(self):
        """Server-sent events: the live price each time it changes, a comment every 15 s, closed after 30 minutes."""
        if not take_price_stream():
            self.send_plain(HTTPStatus.SERVICE_UNAVAILABLE, "Kaze: too many price streams", {"Retry-After": "30"})
            return
        try:
            self.status_sent = HTTPStatus.OK.value
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", EVENT_STREAM)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.send_price_events()
        finally:
            release_price_stream()

    def send_price_events(self):
        """Poll the price file four times a second and write each change as one event, or a heartbeat when quiet."""
        started = last_write = time.monotonic()
        sent = None
        while time.monotonic() - started < PRICE_STREAM_SECONDS:
            tick = price_tick()
            if tick is not None and tick != sent:
                self.wfile.write(f"data: {json.dumps(tick)}\n\n".encode())
                sent, last_write = tick, time.monotonic()
            elif time.monotonic() - last_write >= PRICE_HEARTBEAT_SECONDS:
                self.wfile.write(b": still here\n\n")
                last_write = time.monotonic()
            if self.client_gone():
                return
            time.sleep(PRICE_POLL_SECONDS)

    def client_gone(self):
        """True once the phone has closed its end: a socket that is readable but holds nothing is a closed one."""
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            return bool(readable) and not self.connection.recv(1, socket.MSG_PEEK)
        except OSError:
            return True

    def send(self, status, body, content_type, cache_control, headers=None):
        """Send one response: status, any extra headers, content type, cache rule, length; HEAD sends headers only."""
        self.status_sent = status.value
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_plain(self, status, text, headers=None):
        """A short plain-text answer (401, 404, 405), never cached."""
        self.send(status, text.encode(), CONTENT_TYPES[".txt"], NO_STORE, headers)

    def version_string(self):
        """The Server header: the name, no Python version."""
        return "Kaze"

    def log_message(self, format, *args):
        """Silence the default stderr line: the job log holds the one line per request, without headers or bodies."""


class QuietServer(ThreadingHTTPServer):
    """The threaded server with its traceback printer replaced by one log line, whatever a client does."""

    def handle_error(self, request, client_address):
        """One log line for a request the server could not finish, so launchd's capture file stays empty."""
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
