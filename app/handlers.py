from __future__ import annotations

import asyncio
import html
import re
import secrets
import time
from pathlib import Path
from typing import Awaitable, Callable

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from app import storage, texts
from app.domains import detect_links
from app.progress import Meter, format_bytes, render
from app.resolver import FileInfo, resolve
from app.settings import settings


JOBS = asyncio.Semaphore(max(1, min(settings.max_concurrent, 32)))
CACHE_TTL_SECONDS = 30 * 60


class CachedFile:
    def __init__(self, file: FileInfo, owner_id: int) -> None:
        self.file = file
        self.owner_id = owner_id
        self.expires_at = time.monotonic() + CACHE_TTL_SECONDS


CACHE: dict[str, CachedFile] = {}


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


async def _safe_edit(message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except Exception:
        # Telegram returns an error if an edit repeats the current content or
        # if a progress update races with a finished upload. The job itself
        # should not fail because a cosmetic update was rejected.
        pass


def _allowed(user_id: int) -> tuple[bool, str | None]:
    if storage.kv_get("maintenance") == "on" or settings.maintenance:
        if not settings.is_owner(user_id):
            return False, texts.MAINTENANCE
    if storage.is_banned(user_id):
        return False, texts.BANNED
    if settings.bot_public or settings.is_owner(user_id) or storage.is_authorized(user_id):
        return True, None
    return False, texts.PRIVATE


def _upload_limit_mb() -> int:
    raw = storage.kv_get("limit")
    try:
        configured = int(raw) if raw else settings.max_file_mb
    except ValueError:
        configured = settings.max_file_mb
    return max(1, min(configured, settings.transport_max_file_mb))


def _can_upload(file: FileInfo) -> bool:
    return not file.size or file.size <= _upload_limit_mb() * 1024 * 1024


def resolve_redirect(token: str, kind: str) -> str | None:
    """Look up the real TeraBox URL behind a public /go/ link. Used by the
    tiny redirect webpage in app.main so buttons never show the raw
    TeraBox/CDN host to the user."""
    item = CACHE.get(token)
    if not item or item.expires_at <= time.monotonic():
        return None
    file = item.file
    if kind == "stream":
        return file.stream_hd_url or file.stream_url
    if kind == "direct":
        return file.direct_link
    return None


def _public_link(token: str, kind: str) -> str | None:
    base = settings.public_base_url
    if not base:
        return None
    return f"{base}/go/{token}?t={kind}"


def _file_keyboard(file: FileInfo, token: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    top_row: list[InlineKeyboardButton] = []
    stream_target = file.stream_hd_url or file.stream_url
    if stream_target:
        hidden = _public_link(token, "stream")
        top_row.append(InlineKeyboardButton("▶ stream", url=hidden or stream_target))
    if file.direct_link:
        hidden = _public_link(token, "direct")
        top_row.append(InlineKeyboardButton("↗ direct", url=hidden or file.direct_link))
    if top_row:
        rows.append(top_row)

    if _can_upload(file):
        rows.append(
            [InlineKeyboardButton(f"↓ send file · {file.formatted_size}", callback_data=f"dl:{token}")]
        )
    return InlineKeyboardMarkup(rows)


def _caption(file: FileInfo) -> str:
    tpl = storage.kv_get("caption") or settings.caption_template
    return (
        tpl.replace("{filename}", html.escape(file.file_name))
        .replace("{size}", html.escape(file.formatted_size))
        .replace("{url}", html.escape(file.direct_link or "", quote=True))
        .replace("{host}", "")
    )[:1024]


def _cache(file: FileInfo, owner_id: int) -> str:
    token = secrets.token_urlsafe(9)
    CACHE[token] = CachedFile(file, owner_id)
    if len(CACHE) > 800:
        now = time.monotonic()
        for key, item in list(CACHE.items()):
            if item.expires_at <= now:
                CACHE.pop(key, None)
    return token


def _cached(token: str, user_id: int) -> FileInfo | None:
    item = CACHE.get(token)
    if not item:
        return None
    if item.expires_at <= time.monotonic():
        CACHE.pop(token, None)
        return None
    if item.owner_id != user_id and not settings.is_owner(user_id):
        return None
    return item.file


def _admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 stats", callback_data="admin:stats"),
                InlineKeyboardButton("👥 users", callback_data="admin:users"),
            ],
            [
                InlineKeyboardButton("🧾 logs", callback_data="admin:logs"),
                InlineKeyboardButton("⚙ settings", callback_data="admin:settings"),
            ],
            [
                InlineKeyboardButton("⏸ toggle maintenance", callback_data="admin:maintenance"),
                InlineKeyboardButton("↻ refresh", callback_data="admin:home"),
            ],
        ]
    )


def _admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("← admin home", callback_data="admin:home")]])


async def _admin_home(message) -> None:
    summary = storage.stats()
    await message.reply_text(
        texts.ADMIN_PANEL.format(
            users=summary["users"],
            downloads=summary["downloads"],
            bans=summary["bans"],
            maintenance="on" if storage.kv_get("maintenance") == "on" else "off",
            limit=_upload_limit_mb(),
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=_admin_keyboard(),
    )


async def _show_admin_detail(query, section: str) -> None:
    if section == "stats":
        s = storage.stats()
        text = (
            "<b>📊 ᴜsᴀɢᴇ sᴛᴀᴛs</b>\n\n"
            f"<blockquote>users: {s['users']}\n"
            f"completed downloads: {s['downloads']}\n"
            f"banned accounts: {s['bans']}</blockquote>"
        )
    elif section == "users":
        rows = storage.recent_users(18)
        text = "<b>👥 ʀᴇᴄᴇɴᴛ ᴜsᴇʀs</b>\n\n" + (_escape("\n".join(rows)) if rows else "no users yet.")
    elif section == "logs":
        rows = storage.recent_logs(16)
        text = "<b>🧾 ʀᴇᴄᴇɴᴛ ʟᴏɢs</b>\n\n" + (_escape("\n".join(rows)) if rows else "no errors recorded.")
    else:
        api_state = "enabled · up to 2 gb" if settings.bot_api_enabled else "cloud api · 49 mb"
        text = (
            "<b>⚙ ʀᴜɴᴛɪᴍᴇ sᴇᴛᴛɪɴɢs</b>\n\n"
            f"<blockquote>upload transport: {api_state}\n"
            f"effective upload limit: {_upload_limit_mb()} mb\n"
            f"download workers: {settings.max_concurrent}\n"
            f"health port: {settings.health_port}</blockquote>\n\n"
            "use /setlimit &lt;mb&gt; to change the configured ceiling."
        )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=_admin_back_keyboard())


async def _handle_admin_callback(query, action: str) -> None:
    if action == "home":
        summary = storage.stats()
        await query.edit_message_text(
            texts.ADMIN_PANEL.format(
                users=summary["users"],
                downloads=summary["downloads"],
                bans=summary["bans"],
                maintenance="on" if storage.kv_get("maintenance") == "on" else "off",
                limit=_upload_limit_mb(),
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=_admin_keyboard(),
        )
    elif action == "maintenance":
        current = storage.kv_get("maintenance") == "on"
        storage.kv_set("maintenance", "off" if current else "on")
        await _handle_admin_callback(query, "home")
    else:
        await _show_admin_detail(query, action)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user:
        storage.touch_user(user.id, user.username, user.first_name)
    ok, reason = _allowed(user.id if user else 0)
    if not ok and reason:
        await update.message.reply_text(reason, parse_mode=ParseMode.HTML)
        return
    await update.message.reply_text(texts.welcome(), parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(texts.HELP, parse_mode=ParseMode.HTML)


async def owner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_owner(update):
        await update.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
        return
    await update.message.reply_text(
        texts.OWNER_HELP,
        parse_mode=ParseMode.HTML,
        reply_markup=_admin_keyboard(),
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return
    user = update.effective_user
    storage.touch_user(user.id, user.username, user.first_name)
    ok, reason = _allowed(user.id)
    if not ok:
        await message.reply_text(reason or texts.PRIVATE, parse_mode=ParseMode.HTML)
        return

    found = detect_links(message.text)
    if not found:
        if message.chat.type == "private":
            await message.reply_text(texts.NO_LINK, parse_mode=ParseMode.HTML)
        return
    await process_link(message, found[0]["url"], found[0], user.id)


async def process_link(message, url: str, meta: dict, owner_id: int) -> None:
    status = await message.reply_text(texts.DETECTING, parse_mode=ParseMode.HTML)
    await _safe_edit(
        status,
        texts.ANALYSING.format(
            domain=_escape(meta.get("host") or "terabox"),
            surl=_escape(meta.get("surl") or "unknown"),
        ),
    )
    await _safe_edit(status, texts.RETRIEVING)
    try:
        result = await resolve(url)
    except Exception as exc:
        storage.log("error", str(exc))
        await _safe_edit(status, texts.FAILED.format(reason=_escape(exc)))
        return
    if not result.ok:
        storage.log("warn", result.message)
        await _safe_edit(status, texts.FAILED.format(reason=_escape(result.message)))
        return
    await _safe_edit(status, texts.RESOLVING)
    file = result.files[0]
    kind = (
        "ғᴏʟᴅᴇʀ"
        if len(result.files) > 1
        else ("ᴠɪᴅᴇᴏ" if file.file_name.lower().endswith((".mp4", ".mkv", ".mov", ".webm")) else "ғɪʟᴇ")
    )
    if len(result.files) > 1:
        buttons: list[list[InlineKeyboardButton]] = []
        for item in result.files[:40]:
            token = _cache(item, owner_id)
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"{item.file_name[:38]} · {item.formatted_size}",
                        callback_data=f"pick:{token}",
                    )
                ]
            )
        body = texts.FOLDER.format(
            title=_escape(result.title),
            count=len(result.files),
            size=_escape(format_bytes(sum(item.size for item in result.files))),
        )
        await _safe_edit(status, body, InlineKeyboardMarkup(buttons))
        return

    token = _cache(file, owner_id)
    body = texts.READY.format(
        filename=_escape(file.file_name),
        size=_escape(file.formatted_size),
        kind=kind,
    )
    if not _can_upload(file):
        if settings.bot_api_enabled:
            body += "\n\n<i>send file is above the configured owner limit.</i>"
        else:
            body += "\n\n" + texts.LARGE_UPLOAD_UNAVAILABLE
    await _safe_edit(status, body, _file_keyboard(file, token))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    user = update.effective_user
    if query.data.startswith("admin:"):
        if not _require_owner(update):
            await query.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
            return
        await _handle_admin_callback(query, query.data.split(":", 1)[1])
        return

    ok, reason = _allowed(user.id)
    if not ok:
        await query.message.reply_text(reason or texts.PRIVATE, parse_mode=ParseMode.HTML)
        return
    data = query.data
    if data.startswith("pick:"):
        file = _cached(data.split(":", 1)[1], user.id)
        if not file:
            await query.message.reply_text(
                "that file selection expired. send the link again.",
                parse_mode=ParseMode.HTML,
            )
            return
        await query.message.reply_text(
            texts.READY.format(
                filename=_escape(file.file_name),
                size=_escape(file.formatted_size),
                kind="ғɪʟᴇ",
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=_file_keyboard(file, _cache(file, user.id)),
        )
        return
    if data.startswith("dl:"):
        file = _cached(data.split(":", 1)[1], user.id)
        if not file:
            await query.message.reply_text(
                "that download expired. send the link again.",
                parse_mode=ParseMode.HTML,
            )
            return
        await download_and_send(query.message, file, user.id)


async def download_and_send(message, file: FileInfo, user_id: int) -> None:
    if not file.direct_link and not file.stream_url:
        await message.reply_text("no download URL was returned for this file.")
        return
    limit = _upload_limit_mb()
    limit_bytes = limit * 1024 * 1024
    if file.size and file.size > limit_bytes:
        await message.reply_text(
            _too_large_text(file, limit),
            parse_mode=ParseMode.HTML,
            reply_markup=_file_keyboard(file, _cache(file, user_id)),
        )
        return

    url = file.direct_link or file.stream_url
    async with JOBS:
        status = await message.reply_text(
            render("download", _escape(file.file_name), 0, file.size, 0, 0),
            parse_mode=ParseMode.HTML,
        )
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", file.file_name).strip("._")[:80] or "download"
        dest = settings.download_dir / f"{secrets.token_hex(5)}_{safe_name}"
        settings.download_dir.mkdir(parents=True, exist_ok=True)
        try:
            await _download_file(status, url or "", dest, file)
            size = dest.stat().st_size if dest.exists() else file.size
            if size > limit_bytes:
                await _safe_edit(
                    status,
                    _too_large_text(file, limit, actual_size=size),
                    _file_keyboard(file, _cache(file, user_id)),
                )
                return
            await message.chat.send_action(
                ChatAction.UPLOAD_VIDEO
                if file.file_name.lower().endswith((".mp4", ".mov", ".webm"))
                else ChatAction.UPLOAD_DOCUMENT
            )
            await _safe_edit(status, render("upload", _escape(file.file_name), 0, size, 0, 0))
            await _upload_to_telegram(status, message.chat.id, dest, file, size)
            storage.bump_download(user_id)
            # The file has now arrived as its own message in the chat, so the
            # "file ready" message (with the stream/direct/send buttons) and
            # the progress status message are no longer needed — clean both up
            # instead of leaving a stale "finished" text behind.
            for stale in (status, message):
                try:
                    await stale.delete()
                except Exception:
                    pass
        except Exception as exc:
            storage.log("error", str(exc))
            await _safe_edit(status, texts.FAILED.format(reason=_escape(exc)))
        finally:
            try:
                Path(dest).unlink(missing_ok=True)
            except Exception:
                pass


def _too_large_text(file: FileInfo, limit: int, actual_size: int | None = None) -> str:
    size = format_bytes(actual_size) if actual_size else file.formatted_size
    if settings.bot_api_enabled:
        hint = "lower the owner limit with /setlimit or use stream/direct."
    else:
        hint = (
            "stream or direct are ready below. to send larger files in chat, "
            "run a local telegram bot api server and set <code>BOT_API_URL</code>."
        )
    return texts.TOO_LARGE.format(size=_escape(size), limit=limit, hint=hint)


_DL_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "referer": "https://www.teraboxdl.site/",
}
PARALLEL_SEGMENTS = 6
PARALLEL_MIN_BYTES = 12 * 1024 * 1024  # below this, one connection is plenty


async def _probe_range_support(client: httpx.AsyncClient, url: str) -> int:
    """Returns the file size if the server supports byte-range requests
    (needed to split the download across several connections), else 0."""
    try:
        headers = {**_DL_HEADERS, "range": "bytes=0-0"}
        async with client.stream("GET", url, headers=headers) as res:
            if res.status_code != 206:
                return 0
            content_range = res.headers.get("content-range", "")
            total = int(content_range.split("/")[-1]) if "/" in content_range else 0
            return total
    except Exception:
        return 0


async def _download_file(status, url: str, dest: Path, file: FileInfo) -> None:
    meter = Meter()
    last_edit = [0.0]
    lock = asyncio.Lock()
    done_ref = [0]

    async def report(total: int) -> None:
        now = time.monotonic()
        if now - last_edit[0] < 0.8 and done_ref[0] < total:
            return
        last_edit[0] = now
        speed, elapsed = meter.update(done_ref[0])
        await _safe_edit(
            status,
            render("download", _escape(file.file_name), done_ref[0], total or done_ref[0], speed, elapsed),
        )

    timeout = httpx.Timeout(None, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        total = await _probe_range_support(client, url)
        if total >= PARALLEL_MIN_BYTES:
            try:
                await _download_parallel(client, url, dest, total, lock, done_ref, report)
                return
            except Exception:
                # Fall through to a plain single-connection download if the
                # segmented attempt failed partway through (server hiccup,
                # a segment dropping, etc). Reset progress and retry clean.
                done_ref[0] = 0
                meter.reset()

        async with client.stream("GET", url, headers=_DL_HEADERS) as res:
            res.raise_for_status()
            total = int(res.headers.get("content-length") or file.size or 0)
            with dest.open("wb") as fh:
                async for chunk in res.aiter_bytes(256 * 1024):
                    fh.write(chunk)
                    done_ref[0] += len(chunk)
                    await report(total)


async def _download_parallel(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    total: int,
    lock: asyncio.Lock,
    done_ref: list[int],
    report: Callable[[int], Awaitable[None]],
) -> None:
    """Downloads several byte-ranges of the same file concurrently. This
    helps when the bottleneck is a per-connection throttle on the source
    CDN rather than the actual pipe — opening N connections can multiply
    effective throughput even though each one is still capped."""
    with dest.open("wb") as fh:
        fh.truncate(total)

    segment_size = -(-total // PARALLEL_SEGMENTS)
    ranges = []
    start = 0
    while start < total:
        end = min(start + segment_size, total) - 1
        ranges.append((start, end))
        start = end + 1

    async def fetch(rng: tuple[int, int]) -> None:
        seg_start, seg_end = rng
        headers = {**_DL_HEADERS, "range": f"bytes={seg_start}-{seg_end}"}
        async with client.stream("GET", url, headers=headers) as res:
            res.raise_for_status()
            with dest.open("r+b") as fh:
                fh.seek(seg_start)
                async for chunk in res.aiter_bytes(256 * 1024):
                    fh.write(chunk)
                    async with lock:
                        done_ref[0] += len(chunk)
                    await report(total)

    await asyncio.gather(*(fetch(r) for r in ranges))


class _MultipartUpload(httpx.AsyncByteStream):
    def __init__(
        self,
        fields: dict[str, str],
        file_field: str,
        file_path: Path,
        filename: str,
        content_type: str,
        on_progress: Callable[[int], Awaitable[None]],
        thumbnail: bytes | None = None,
    ) -> None:
        self.boundary = f"----teradrop-{secrets.token_hex(12)}"
        self.file_path = file_path
        self.on_progress = on_progress
        self.sent = 0
        self.file_size = file_path.stat().st_size
        prefix = b"".join(self._field_part(name, value) for name, value in fields.items())
        if thumbnail:
            prefix += self._file_prefix("thumbnail", "thumb.jpg", "image/jpeg") + thumbnail + b"\r\n"
        self.prefix = prefix + self._file_prefix(file_field, filename, content_type)
        self.suffix = f"\r\n--{self.boundary}--\r\n".encode()
        self.content_length = len(self.prefix) + self.file_size + len(self.suffix)

    def _field_part(self, name: str, value: str) -> bytes:
        return (
            f"--{self.boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()

    def _file_prefix(self, field: str, filename: str, content_type: str) -> bytes:
        clean_name = filename.replace("\\", "_").replace('"', "'")
        return (
            f"--{self.boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; filename="{clean_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()

    async def __aiter__(self):
        yield self.prefix
        with self.file_path.open("rb") as handle:
            while True:
                chunk = await asyncio.to_thread(handle.read, 256 * 1024)
                if not chunk:
                    break
                self.sent += len(chunk)
                yield chunk
                await self.on_progress(self.sent)
        yield self.suffix

    async def aclose(self) -> None:
        return


async def _fetch_thumbnail(url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=8.0)) as client:
            res = await client.get(url)
            res.raise_for_status()
            data = res.content
            # Telegram thumbnails must be under 200 KB and a jpeg/png.
            return data[: 200 * 1024] if data else None
    except Exception:
        return None


async def _upload_to_telegram(status, chat_id: int, path: Path, file: FileInfo, size: int) -> None:
    is_video = file.file_name.lower().endswith((".mp4", ".mov", ".webm"))
    method = "sendVideo" if is_video else "sendDocument"
    field = "video" if is_video else "document"
    content_type = "video/mp4" if is_video else "application/octet-stream"
    thumbnail = await _fetch_thumbnail(file.thumb)
    meter = Meter()
    last_edit = 0.0

    async def progress(done: int) -> None:
        nonlocal last_edit
        now = time.monotonic()
        if now - last_edit < 0.8 and done < size:
            return
        last_edit = now
        speed, elapsed = meter.update(done)
        await _safe_edit(status, render("upload", _escape(file.file_name), done, size, speed, elapsed))

    fields = {
        "chat_id": str(chat_id),
        "caption": _caption(file),
        "parse_mode": "HTML",
    }
    if thumbnail:
        # Per Telegram Bot API: attach the thumbnail as its own multipart
        # field, then reference it by name via attach://<field_name>.
        fields["thumbnail"] = "attach://thumbnail"
    stream = _MultipartUpload(fields, field, path, file.file_name, content_type, progress, thumbnail=thumbnail)
    headers = {
        "content-type": f"multipart/form-data; boundary={stream.boundary}",
        "content-length": str(stream.content_length),
    }
    timeout = httpx.Timeout(None, connect=30.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            settings.telegram_method_url(method),
            headers=headers,
            content=stream,
        )
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if response.status_code >= 400 or not payload.get("ok"):
        description = payload.get("description") or response.text[:500] or "telegram rejected the upload"
        raise RuntimeError(f"telegram upload failed: {description}")
    await progress(size)


def _require_owner(update: Update) -> bool:
    return bool(update.effective_user and settings.is_owner(update.effective_user.id))


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_owner(update):
        await update.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
        return
    await _admin_home(update.message)


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_owner(update):
        await update.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
        return
    rows = storage.recent_users()
    await update.message.reply_text(
        "<b>👥 ʀᴇᴄᴇɴᴛ ᴜsᴇʀs</b>\n\n" + (_escape("\n".join(rows)) if rows else "no users yet."),
        parse_mode=ParseMode.HTML,
    )


async def logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_owner(update):
        await update.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
        return
    rows = storage.recent_logs()
    await update.message.reply_text(
        "<b>🧾 ʀᴇᴄᴇɴᴛ ʟᴏɢs</b>\n\n" + (_escape("\n".join(rows)) if rows else "no errors recorded."),
        parse_mode=ParseMode.HTML,
    )


async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_owner(update):
        await update.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
        return
    if not context.args:
        await update.message.reply_text("usage: /ban &lt;user_id&gt;", parse_mode=ParseMode.HTML)
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user id must be numeric.")
        return
    storage.ban(uid)
    await update.message.reply_text(f"banned <code>{uid}</code>.", parse_mode=ParseMode.HTML)


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_owner(update):
        await update.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
        return
    if not context.args:
        await update.message.reply_text("usage: /unban &lt;user_id&gt;", parse_mode=ParseMode.HTML)
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user id must be numeric.")
        return
    storage.unban(uid)
    await update.message.reply_text(f"unbanned <code>{uid}</code>.", parse_mode=ParseMode.HTML)


async def auth_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_owner(update):
        await update.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
        return
    if not context.args:
        await update.message.reply_text("usage: /auth &lt;user_id&gt;", parse_mode=ParseMode.HTML)
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user id must be numeric.")
        return
    storage.authorize(uid)
    await update.message.reply_text(f"authorised <code>{uid}</code>.", parse_mode=ParseMode.HTML)


async def unauth_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_owner(update):
        await update.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
        return
    if not context.args:
        await update.message.reply_text("usage: /unauth &lt;user_id&gt;", parse_mode=ParseMode.HTML)
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user id must be numeric.")
        return
    storage.unauthorize(uid)
    await update.message.reply_text(f"removed <code>{uid}</code> from the allow-list.", parse_mode=ParseMode.HTML)


async def maintenance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_owner(update):
        await update.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
        return
    flag = (context.args[0] if context.args else "on").lower()
    storage.kv_set("maintenance", "on" if flag in {"on", "1", "true"} else "off")
    await update.message.reply_text(
        f"maintenance is now <b>{storage.kv_get('maintenance')}</b>.",
        parse_mode=ParseMode.HTML,
    )


async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_owner(update):
        await update.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
        return
    text = " ".join(context.args).strip()
    if update.message.reply_to_message and update.message.reply_to_message.text:
        text = update.message.reply_to_message.text
    if not text:
        await update.message.reply_text("reply to a message or pass the new welcome text.")
        return
    storage.kv_set("welcome", text)
    settings.welcome_text = text
    await update.message.reply_text("welcome text updated.")


async def setcaption_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_owner(update):
        await update.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
        return
    text = " ".join(context.args).strip() or "{filename}\\n{size}"
    storage.kv_set("caption", text.replace("\\n", "\n"))
    await update.message.reply_text("caption template updated.")


async def setlimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_owner(update):
        await update.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
        return
    if not context.args:
        await update.message.reply_text("usage: /setlimit &lt;mb&gt;", parse_mode=ParseMode.HTML)
        return
    try:
        requested = int(context.args[0])
    except ValueError:
        await update.message.reply_text("the limit must be a whole number of megabytes.")
        return
    if requested < 1 or requested > 2000:
        await update.message.reply_text("choose a limit between 1 and 2000 mb.")
        return
    storage.kv_set("limit", str(requested))
    await update.message.reply_text(
        f"configured upload limit: <b>{_upload_limit_mb()} mb</b>.",
        parse_mode=ParseMode.HTML,
    )


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _require_owner(update):
        await update.message.reply_text(texts.NOT_OWNER, parse_mode=ParseMode.HTML)
        return
    text = " ".join(context.args).strip()
    if update.message.reply_to_message and update.message.reply_to_message.text:
        text = update.message.reply_to_message.text
    if not text:
        await update.message.reply_text("usage: /broadcast &lt;text&gt;", parse_mode=ParseMode.HTML)
        return
    sent = 0
    for uid in storage.user_ids():
        try:
            await context.bot.send_message(uid, text, parse_mode=ParseMode.HTML)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            continue
    await update.message.reply_text(f"broadcast sent to <b>{sent}</b> users.", parse_mode=ParseMode.HTML)
