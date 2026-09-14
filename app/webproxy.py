from __future__ import annotations

import html
import select
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs

import httpx

from app.handlers import resolve_cached_file, resolve_redirect

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_REFERER = "https://www.teraboxdl.site/"
_UPSTREAM_HEADERS = {"user-agent": _UA, "referer": _REFERER}
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
  .err {{ color:#ff6b6b; font-size:13px; margin-top:14px; display:none; }}
</style>
</head>
<body>
<h1>{title}</h1>
{body}
</body>
</html>"""

# Plain native <video>. No third-party player skin: the stream is a
# fragmented MP4 with an empty moov (no known duration/seek table, by
# design, so playback can start before the whole file is fetched). A
# custom player that tries to draw a duration/seek bar will show NaN and
# look "corrupted" — the native element just plays it as a live stream.
_STREAM_BODY = """<video id="player" controls playsinline autoplay preload="auto"
       onerror="document.getElementById('err').style.display='block'">
  <source src="{src}" type="video/mp4">
</video>
<div class="hint">Playback starts as soon as the first part arrives — no need to wait for a full download.</div>
<div class="err" id="err">Playback failed. Try the Direct Download link from the bot instead.</div>"""


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
        # Whatever the source actually is (HLS manifest or a direct file),
        # our own /media/ endpoint always hands back plain, standard MP4 —
        # see serve_media below — so the page never needs to guess a format
        # or parse a manifest itself.
        body = _STREAM_BODY.format(src=media_url)
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


def _drain_stderr(proc: subprocess.Popen, sink: bytearray) -> None:
    try:
        while True:
            chunk = proc.stderr.read(4096)
            if not chunk:
                return
            sink.extend(chunk)
            del sink[:-4000]  # keep only the tail, ffmpeg's real error is usually last
    except Exception:
        return


def _redact(text: str, secret_url: str) -> str:
    if secret_url and secret_url in text:
        text = text.replace(secret_url, "[hidden]")
    return text


def _transmux_stream(handler: BaseHTTPRequestHandler, source_url: str, head_only: bool) -> None:
    """Runs the source (HLS manifest or direct video, doesn't matter which)
    through ffmpeg and pipes out plain fragmented MP4. This is what actually
    fixes playback: browsers don't have to parse HLS at all, and it works
    the same way regardless of what format TeraBox happens to hand back.
    Container-only (-c copy): ffmpeg re-muxes, it does not re-encode, so
    this is cheap on CPU.

    Crucially, we don't send any HTTP response headers until we've actually
    confirmed ffmpeg produced real output — otherwise a silent ffmpeg
    failure looks like a successful-but-empty video to the browser, which
    is indistinguishable from the broken player you'd otherwise see."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        handler.send_response(503)
        body = b"Streaming is unavailable on this deployment (ffmpeg not installed)."
        handler.send_header("content-type", "text/plain")
        handler.send_header("content-length", str(len(body)))
        handler.end_headers()
        if not head_only:
            handler.wfile.write(body)
        return

    cmd = [
        ffmpeg,
        "-loglevel", "error",
        "-user_agent", _UA,
        "-headers", f"Referer: {_REFERER}\r\n",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", source_url,
        "-c", "copy",
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stderr_tail = bytearray()
    threading.Thread(target=_drain_stderr, args=(proc, stderr_tail), daemon=True).start()

    # Wait for the first chunk of real MP4 output before committing to a
    # 200 response, with a generous timeout for the initial connect+probe.
    first_chunk = b""
    try:
        readable, _, _ = select.select([proc.stdout], [], [], 20.0)
        if readable:
            first_chunk = proc.stdout.read(256 * 1024) or b""
    except Exception:
        first_chunk = b""

    if not first_chunk:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        err = _redact(bytes(stderr_tail).decode("utf-8", "replace").strip(), source_url)
        body = (
            "Could not start the stream.\n"
            + (err[-500:] if err else "The source did not return any playable data.")
        ).encode()
        handler.send_response(502)
        handler.send_header("content-type", "text/plain; charset=utf-8")
        handler.send_header("content-length", str(len(body)))
        handler.end_headers()
        if not head_only:
            handler.wfile.write(body)
        return

    handler.send_response(200)
    handler.send_header("content-type", "video/mp4")
    handler.send_header("cache-control", "no-store")
    # Tell WebKit (Safari / Telegram's in-app browser, which is WKWebView
    # on iOS) explicitly not to issue Range requests. Without this header,
    # WebKit sends one anyway, gets back a non-206 response for it (we
    # can't honor byte ranges on a live transmux), and renders the video
    # element as broken/"corrupted" instead of falling back to a plain
    # sequential stream. This single header is the actual fix for that.
    handler.send_header("accept-ranges", "none")
    handler.end_headers()
    if head_only:
        proc.kill()
        return
    try:
        handler.wfile.write(first_chunk)
        while True:
            chunk = proc.stdout.read(256 * 1024)
            if not chunk:
                break
            handler.wfile.write(chunk)
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _proxy_raw(handler: BaseHTTPRequestHandler, target: str, head_only: bool) -> None:
    """Plain byte passthrough for the Direct-download button — the actual
    original file, untouched, with Range support so downloads can resume."""
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


def serve_media(handler: BaseHTTPRequestHandler, parsed, head_only: bool = False) -> None:
    """Serves /media/<token>?t=stream|direct. The upstream URL is only ever
    used server-side and is never sent to the browser in any header,
    redirect, or manifest."""
    token = parsed.path.removeprefix("/media/").strip("/")
    kind = (parse_qs(parsed.query).get("t") or [""])[0]
    target = resolve_redirect(token, kind) if token else None
    if not target:
        _not_found(handler)
        return
    if kind == "stream":
        _transmux_stream(handler, target, head_only)
    else:
        _proxy_raw(handler, target, head_only)
