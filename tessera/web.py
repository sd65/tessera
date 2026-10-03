"""A read-only HTTP view of the corpus, for the browser UI.

Wiring only, like cli.py. Every query lives in store.py and every constant in
config.py — this module decides what a URL means and what bytes come back, and
nothing else.

Two things are worth knowing before reading the code.

Read-only is a property of the connection, not of the routing. There are no
`POST`/`PUT`/`DELETE` handlers, and the opener in store.connect_readonly() asks
SQLite for `mode=ro` on top of that, so a handler that tried to write would fail
at the engine rather than merely being unexpected.

Static files are served from a fixed table of URL paths. Nothing from a request
is ever concatenated into a filesystem path, so there is no traversal to defend
against — an unknown path is a 404 before any filesystem call happens.
"""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import config, store

UI_DIR = Path(__file__).parent / "ui"

# What makes "nothing leaves the machine" checkable rather than aspirational:
# with this header the page cannot reach the network at all except the nocookie
# embed. It also forbids inline <script> and inline style attributes, which is
# why every byte of CSS lives in ui/styles.js and every component lives in its
# own module.
CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "frame-src https://www.youtube-nocookie.com; "
    "object-src 'none'; "
    "base-uri 'none'"
)

# The embed is the one outbound request this page makes, and this is the only
# header standing between it and YouTube's origin.
#
# This was `no-referrer`, on the reasoning that a localhost URL is nobody else's
# business. It cost the player. YouTube refuses to configure its embed when the
# request carries no Referer, and says so on the video: "Video player
# configuration error — Error 153". Confirmed here rather than believed: driving
# Chrome over CDP, attaching to the embed's out-of-process target and reading its
# DOM gave exactly that error text under `no-referrer`, and the video title under
# `origin-when-cross-origin`.
#
# So an origin is sent, and the most that can be sent here is an origin: no path,
# no query, never the `?v=` deep link. `strict-origin-when-cross-origin` also
# works and was tested, but it sends the full URL on same-origin requests, which
# buys nothing and leaks more if this page ever grows a link out. This is the
# least a working embed accepts.
REFERRER_POLICY = "origin-when-cross-origin"

# Validated before any query is built. Also validated client-side before it is
# interpolated into the embed URL, because the deep link `?v=` is user input.
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

# URL path -> (file under UI_DIR, content type). Exact matches only, and the
# filenames never come from the request.
STATIC: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.js": ("styles.js", "text/javascript; charset=utf-8"),
    "/format.js": ("format.js", "text/javascript; charset=utf-8"),
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/components/app.js": ("components/app.js", "text/javascript; charset=utf-8"),
    "/components/video-list.js": ("components/video-list.js", "text/javascript; charset=utf-8"),
    "/components/video-player.js": ("components/video-player.js", "text/javascript; charset=utf-8"),
    "/components/transcript.js": ("components/transcript.js", "text/javascript; charset=utf-8"),
    "/components/el.js": ("components/el.js", "text/javascript; charset=utf-8"),
}

_JSON = "application/json; charset=utf-8"


class BadRequest(Exception):
    """A query parameter the client got wrong. Becomes a 400, not a 500."""


def _one(query: dict[str, list[str]], name: str) -> str | None:
    """A single query value, or None if absent or blank.

    `?channel=` is treated as `?channel` — an empty filter is no filter. Only
    the first value is read; a repeated parameter is a client bug, not something
    worth a 400.
    """
    values = query.get(name) or []
    return (values[0].strip() if values else "") or None


def _paging(query: dict[str, list[str]], name: str, default: int, ceiling: int | None = None) -> int:
    """A non-negative integer, clamped rather than refused.

    The clamp is on `limit` only, and deliberately: a client asking for more
    than it should gets a bounded answer instead of an error, so a bug in the
    page size cannot turn into an unbounded query. A value that is not a
    non-negative integer at all is a 400 — silently substituting the default
    would hide a broken client, which is worse than refusing it.
    """
    raw = _one(query, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BadRequest(f"{name} must be an integer, got {raw!r}") from None
    if value < 0:
        raise BadRequest(f"{name} must not be negative, got {value}")
    return value if ceiling is None else min(value, ceiling)


class Handler(BaseHTTPRequestHandler):
    server_version = "tessera"
    sys_version = ""  # do not advertise the interpreter version
    protocol_version = "HTTP/1.1"  # so the browser can keep the connection

    def do_GET(self) -> None:
        self._dispatch(send_body=True)

    def do_HEAD(self) -> None:
        self._dispatch(send_body=False)

    def _method_not_allowed(self) -> None:
        self._json(
            {"error": "this server is read-only"},
            send_body=True,
            status=HTTPStatus.METHOD_NOT_ALLOWED,
            extra={"Allow": "GET, HEAD"},
        )

    # Naming the common verbs turns the default 501 "Unsupported method" into a
    # 405 that states what is allowed. The corpus is protected by the database
    # regardless; this is about the error being honest.
    do_POST = do_PUT = do_PATCH = do_DELETE = _method_not_allowed

    def _dispatch(self, send_body: bool) -> None:
        parts = urlsplit(self.path)
        if parts.path.startswith("/api/"):
            self._api(parts.path, parse_qs(parts.query, keep_blank_values=True), send_body)
        else:
            self._static(parts.path, send_body)

    def _api(self, route: str, query: dict[str, list[str]], send_body: bool) -> None:
        if route == "/api/videos":
            try:
                payload = _list_videos(query)
            except BadRequest as exc:
                self._json({"error": str(exc)}, send_body, HTTPStatus.BAD_REQUEST)
                return
            self._json(payload, send_body)
            return

        if route.startswith("/api/videos/"):
            video_id = route[len("/api/videos/") :]
            if not video_id:
                self._json({"error": "no video id"}, send_body, HTTPStatus.NOT_FOUND)
                return
            if not VIDEO_ID.fullmatch(video_id):
                self._json({"error": f"not a video id: {video_id!r}"}, send_body, HTTPStatus.BAD_REQUEST)
                return

            conn = store.connect_readonly()
            try:
                row = store.get_video(conn, video_id)
            finally:
                conn.close()

            if row is None:
                self._json({"error": f"no video {video_id} in the corpus"}, send_body, HTTPStatus.NOT_FOUND)
                return
            self._json(row, send_body)
            return

        self._json({"error": f"no such route: {route}"}, send_body, HTTPStatus.NOT_FOUND)

    def _static(self, route: str, send_body: bool) -> None:
        entry = STATIC.get(route)
        if entry is None:
            self._json({"error": f"no such file: {route}"}, send_body, HTTPStatus.NOT_FOUND)
            return

        name, content_type = entry
        try:
            body = (UI_DIR / name).read_bytes()
        except OSError as exc:  # a packaging fault, not a client one
            self._json({"error": f"could not read {name}: {exc}"}, send_body, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self._send(HTTPStatus.OK, body, content_type, send_body)

    def _json(
        self,
        payload: object,
        send_body: bool,
        status: HTTPStatus = HTTPStatus.OK,
        extra: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, _JSON, send_body, {"Cache-Control": "no-store", **(extra or {})})

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        send_body: bool,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        # Always sent, including on HEAD and on errors: with HTTP/1.1 the length
        # is what tells the client where the response ends.
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Referrer-Policy", REFERRER_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if send_body:
            self.wfile.write(body)


def _list_videos(query: dict[str, list[str]]) -> dict:
    conn = store.connect_readonly()
    try:
        return store.list_videos(
            conn,
            channel=_one(query, "channel"),
            status=_one(query, "status"),
            limit=_paging(query, "limit", config.UI_DEFAULT_LIMIT, config.UI_MAX_LIMIT),
            offset=_paging(query, "offset", 0),
        )
    finally:
        conn.close()


def build_server() -> ThreadingHTTPServer:
    """Bind the loopback socket. Raises OSError if the port is taken.

    Every static path is checked here rather than lazily, so a UI file missing
    from the install is a startup failure with a clear message instead of a
    blank pane in the browser.
    """
    missing = sorted({name for name, _ in STATIC.values() if not (UI_DIR / name).is_file()})
    if missing:
        raise FileNotFoundError(f"UI files missing from {UI_DIR}: {', '.join(missing)}")

    server = ThreadingHTTPServer((config.UI_HOST, config.UI_PORT), Handler)
    server.daemon_threads = True  # so ctrl-c is not held up by a request
    return server
