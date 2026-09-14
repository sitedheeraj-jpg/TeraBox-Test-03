from __future__ import annotations

import html
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

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

_HLS_BODY = """<video id="v" controls playsinline></video>
<div class="hint" id="hint"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.5.15/hls.min.js"></script>
<script>
  var video = document.getElementById('v');
  var src = {src!r};
  var hint = document.getElementById('hint');
  function fail(msg) {{ hint.textContent = msg; }}
  if (video.canPlayType('application/vnd.apple.mpegurl')) {{
    // Safari/iOS play HLS natively
    video.src = src;
    video.play().catch(function(){{}});
  }} else if (window.Hls && Hls.isSupported()) {{
    var hls = new Hls();
    hls.on(Hls.Events.ERROR, function(_e, data) {{
      if (data.fatal) fail('Playback error (' + data.type + '). Try Direct instead.');
    }});
    hls.loadSource(src);
    hls.attachMedia(video);
    video.play().catch(function(){{}});
  }} else {{
    fail('This browser cannot play this stream. Try Direct instead.');
  }}
</script>"""


def _not_found(handler: BaseHTTPRequestHandler) -> None:
    body = b"This link has expired. Open the file again in the bot."
    handler.send_response(404)
    handler.send_header("content-type", "text/plain")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _looks_like_hls(url: str, content_type: str) -> bool:
    content_type = content_type.lower()
    return (
        "mpegurl" in content_type
        or "m3u8" in content_type
        or urlsplit(url).path.lower().endswith(".m3u8")
    )


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
        target = resolve_redirect(token, kind) or ""
        content_type = ""
        try:
            with httpx.stream(
                "GET", target, headers=_UPSTREAM_HEADERS, follow_redirects=True, timeout=_TIMEOUT
            ) as probe:
                content_type = probe.headers.get("content-type", "")
        except httpx.HTTPError:
            pass
        if _looks_like_hls(target, content_type):
            body = _HLS_BODY.format(src=media_url)
        else:
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


def _rewrite_manifest(text: str, base_url: str, token: str, kind: str) -> str:
    """Rewrites every URI in an HLS manifest (segments, sub-playlists, key
    files) into our own /media/ proxy so the browser never learns the real
    CDN host, even mid-playback."""
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue
        if stripped.startswith("#"):
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                abs_url = urljoin(base_url, m.group(1))
                proxied = f"/media/{token}?t={kind}&seg=" + quote(abs_url, safe="")
                line = line.replace(m.group(1), proxied)
            out_lines.append(line)
            continue
        abs_url = urljoin(base_url, stripped)
        out_lines.append(f"/media/{token}?t={kind}&seg=" + quote(abs_url, safe=""))
    return "\n".join(out_lines) + "\n"


def _segment_allowed(seg_url: str, token: str, kind: str) -> bool:
    # The token itself is the real access control (secret + expiring). This
    # extra host check just keeps the proxy from being usable to fetch
    # arbitrary unrelated sites even if a token leaks.
    resolved = resolve_redirect(token, kind) or ""
    allowed_host = urlsplit(resolved).hostname or ""
    seg_host = urlsplit(seg_url).hostname or ""
    if not allowed_host or not seg_host:
        return False
    return seg_host == allowed_host or seg_host.endswith("." + allowed_host.split(".", 1)[-1])


def serve_media(handler: BaseHTTPRequestHandler, parsed, head_only: bool = False) -> None:
    """Proxies the actual file bytes for /media/<token>?t=stream|direct. The
    upstream URL is fetched here, server-side, and is never sent to the
    browser in any header or redirect. HLS manifests are additionally
    rewritten so segment/sub-playlist URLs are also proxied."""
    token = parsed.path.removeprefix("/media/").strip("/")
    qs = parse_qs(parsed.query)
    kind = (qs.get("t") or [""])[0]
    seg = (qs.get("seg") or [None])[0]

    if seg:
        target = unquote(seg)
        if not _segment_allowed(target, token, kind):
            handler.send_response(403)
            handler.end_headers()
            return
    else:
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
            content_type = res.headers.get("content-type", "")
            if _looks_like_hls(target, content_type):
                res.read()
                rewritten = _rewrite_manifest(res.text, str(res.url), token, kind)
                body = rewritten.encode()
                handler.send_response(200)
                handler.send_header("content-type", "application/vnd.apple.mpegurl")
                handler.send_header("content-length", str(len(body)))
                handler.end_headers()
                if not head_only:
                    handler.wfile.write(body)
                return

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
