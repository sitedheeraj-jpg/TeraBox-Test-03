from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

import httpx

from app.handlers import resolve_cached_file, resolve_redirect

_UPSTREAM_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "referer": "https://www.teraboxdl.site/",
}
_TIMEOUT = httpx.Timeout(None, connect=20.0)

_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ background:#0b0d12; color:#eaeaea; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
         display:flex; flex-direction:column; align-items:center; padding:32px 16px; }}
  h1 {{ font-size:16px; font-weight:600; max-width:720px; text-align:center; word-break:break-word; }}
  video {{ width:100%; max-width:960px; border-radius:12px; margin-top:16px; background:#000; }}
  a.button {{ margin-top:24px; display:inline-block; padding:14px 28px; border-radius:10px;
              background:#5b8cff; color:#fff; text-decoration:none; font-weight:600; font-size:15px; }}
  .hint {{ color:#8a8f98; font-size:13px; margin-top:14px; }}
</style>
</head>
<body>
<h1>{title}</h1>
{body}
</body>
</html>"""


def _not_found(handler: BaseHTTPRequestHandler) -> None:
    body = b"This link has expired. Open the file again in the bot."
    handler.send_response(404)
    handler.send_header("content-type", "text/plain")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def serve_page(handler: BaseHTTPRequestHandler, parsed) -> None:
    """Renders the stream/download HTML page for /go/<token>?t=stream|direct.
    The page only ever talks to our own /media/ endpoint, never to the real
    TeraBox/CDN URL."""
    token = parsed.path.removeprefix("/go/").strip("/")
    kind = (parse_qs(parsed.query).get("t") or [""])[0]
    file = resolve_cached_file(token) if token else None
    if not file or kind not in ("stream", "direct"):
        _not_found(handler)
        return
    title = html.escape(file.file_name)
    media_url = f"/media/{token}?t={kind}"
    if kind == "stream":
        body = f'<video controls autoplay playsinline src="{media_url}"></video>'
    else:
        body = (
            f'<a class="button" href="{media_url}" download="{title}">⬇ Download {html.escape(file.formatted_size)}</a>'
            '<div class="hint">Your browser will download the file directly from this page.</div>'
        )
    page = _PAGE.format(title=title, body=body).encode()
    handler.send_response(200)
    handler.send_header("content-type", "text/html; charset=utf-8")
    handler.send_header("content-length", str(len(page)))
    handler.end_headers()
    handler.wfile.write(page)


def serve_media(handler: BaseHTTPRequestHandler, parsed, head_only: bool = False) -> None:
    """Proxies the actual file bytes for /media/<token>?t=stream|direct. The
    upstream URL is fetched here, server-side, and is never sent to the
    browser in any header or redirect."""
    token = parsed.path.removeprefix("/media/").strip("/")
    kind = (parse_qs(parsed.query).get("t") or [""])[0]
    target = resolve_redirect(token, kind) if token else None
    if not target:
        _not_found(handler)
        return

    req_headers = dict(_UPSTREAM_HEADERS)
    range_header = handler.headers.get("Range")
    if range_header:
        req_headers["range"] = range_header

    try:
        with httpx.stream(
            "GET", target, headers=req_headers, follow_redirects=True, timeout=_TIMEOUT
        ) as res:
            handler.send_response(res.status_code if res.status_code in (200, 206) else 502)
            for name in ("content-type", "content-length", "content-range"):
                if name in res.headers:
                    handler.send_header(name, res.headers[name])
            handler.send_header("accept-ranges", "bytes")
            handler.end_headers()
            if head_only:
                return
            for chunk in res.iter_bytes(256 * 1024):
                try:
                    handler.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
    except httpx.HTTPError:
        try:
            handler.send_response(502)
            handler.end_headers()
        except Exception:
            pass
