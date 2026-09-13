from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.domains import detect_links, extract_surl
from app.settings import settings
from app.solver import DEFAULT_UA, Clearance, solve_challenge, solve_turnstile

_sessions: dict[str, Clearance] = {}


@dataclass
class FileInfo:
    file_name: str
    size: int
    formatted_size: str
    fs_id: str
    direct_link: str | None = None
    stream_url: str | None = None
    stream_hd_url: str | None = None
    thumb: str | None = None
    path: str | None = None
    is_dir: bool = False


@dataclass
class ResolveResult:
    ok: bool
    message: str = ""
    title: str = ""
    host: str = ""
    surl: str | None = None
    source_url: str = ""
    files: list[FileInfo] = field(default_factory=list)
    used_solver: bool = False
    via: str = ""
    elapsed_ms: int = 0


def _num(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _fmt(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    n = float(size)
    i = 0
    while n >= 1024 and i < 4:
        n /= 1024
        i += 1
    return f"{int(n)} {units[i]}" if i == 0 else f"{n:.1f} {units[i]}"


def _is_challenge(text: str, status: int) -> bool:
    if status in (403, 503, 429):
        return True
    head = (text or "")[:1200].lower()
    return "just a moment" in head or "cf-turnstile" in head or "challenge-platform" in head


def _map_file(raw: dict[str, Any]) -> FileInfo:
    is_dir = str(raw.get("isdir") or raw.get("is_dir") or "0") == "1"
    path = str(raw.get("path") or "")
    name = str(
        raw.get("server_filename")
        or raw.get("file_name")
        or raw.get("filename")
        or path.split("/")[-1]
        or "file"
    )
    size = _num(raw.get("size") or raw.get("sizebytes"))
    thumbs = raw.get("thumbs") if isinstance(raw.get("thumbs"), dict) else {}
    stream = str(raw.get("stream_url") or "") or None
    hd = str(raw.get("stream_download_url") or raw.get("hd_url") or "") or None
    direct = str(raw.get("direct_link") or raw.get("dlink") or raw.get("download_url") or "") or None
    return FileInfo(
        file_name=name,
        size=size,
        formatted_size=str(raw.get("formatted_size") or "") or _fmt(size),
        fs_id=str(raw.get("fs_id") or ""),
        direct_link=direct,
        stream_url=stream,
        stream_hd_url=hd or stream,
        thumb=str(thumbs.get("url1") or thumbs.get("icon") or raw.get("thumb") or "") or None,
        path=path or None,
        is_dir=is_dir,
    )


async def _post(
    client: httpx.AsyncClient,
    host: str,
    url: str,
    clearance: Clearance | None,
    extra: dict | None = None,
) -> httpx.Response:
    headers = {
        "content-type": "application/json",
        "accept": "application/json,text/plain,*/*",
        "origin": host,
        "referer": host + "/",
        "user-agent": (clearance.user_agent if clearance else DEFAULT_UA),
    }
    body: dict[str, Any] = {"url": url}
    if extra:
        body.update(extra)
    cookies = clearance.cookies if clearance else None
    return await client.post(f"{host}/api/proxy", json=body, headers=headers, cookies=cookies)


def _parse(payload: dict[str, Any], meta: dict[str, Any]) -> ResolveResult:
    if isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("list"), list):
        payload = payload["data"]
    errno = _num(payload.get("errno"))
    errmsg = str(payload.get("errmsg") or payload.get("error") or payload.get("message") or "")
    if errno:
        return ResolveResult(
            ok=False,
            message=errmsg or f"TeraBox said this share is unavailable (code {errno}).",
            **meta,
        )
    raw_list = payload.get("list")
    if not isinstance(raw_list, list) or not raw_list:
        return ResolveResult(ok=False, message=errmsg or "No files were returned for this share.", **meta)
    files = [_map_file(item) for item in raw_list if isinstance(item, dict)]
    files_only = [f for f in files if not f.is_dir] or files
    return ResolveResult(
        ok=True,
        title=str(payload.get("title") or files_only[0].file_name),
        files=files_only,
        **meta,
    )


async def resolve(share_url: str) -> ResolveResult:
    t0 = time.monotonic()
    detected = detect_links(share_url)
    source = detected[0]["url"] if detected else share_url.strip()
    if not source.startswith("http") or not detected:
        return ResolveResult(ok=False, message="I could not find a TeraBox link.")
    host_label = str(detected[0]["host"] or "")
    surl = detected[0]["surl"] if detected else extract_surl(source)
    used_solver = False
    last_message = "Could not reach the downloader."
    timeout = httpx.Timeout(45.0, connect=15.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for host in settings.resolver_hosts:
            meta = {
                "used_solver": used_solver,
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
                "host": host_label,
                "surl": surl,
                "source_url": source,
                "via": host,
            }
            try:
                res = await _post(client, host, source, _sessions.get(host))
                text = res.text
                if _is_challenge(text, res.status_code):
                    used_solver = True
                    clearance = await solve_challenge(host + "/")
                    _sessions[host] = clearance
                    res = await _post(client, host, source, clearance)
                    text = res.text
                    if _is_challenge(text, res.status_code):
                        token = await solve_turnstile(host)
                        extra = {"token": token} if token else None
                        res = await _post(client, host, source, clearance, extra)
                        text = res.text
                meta["used_solver"] = used_solver
                meta["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
                try:
                    payload = res.json()
                except Exception:
                    last_message = (
                        "Cloudflare is still blocking the downloader. Try again in a moment."
                        if _is_challenge(text, res.status_code)
                        else "The downloader returned an unexpected page instead of file data."
                    )
                    continue
                parsed = _parse(payload if isinstance(payload, dict) else {}, meta)
                if parsed.ok:
                    return parsed
                last_message = parsed.message
                if not _is_challenge(text, res.status_code):
                    return parsed
            except Exception as exc:
                last_message = str(exc)
                continue

    return ResolveResult(
        ok=False,
        message=last_message,
        used_solver=used_solver,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
        host=host_label,
        surl=surl,
        source_url=source,
    )
