from __future__ import annotations

import logging
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from app import storage
from app.handlers import (
    auth_cmd,
    ban_cmd,
    broadcast_cmd,
    help_cmd,
    logs_cmd,
    maintenance_cmd,
    on_callback,
    on_text,
    owner_cmd,
    setcaption_cmd,
    setlimit_cmd,
    setwelcome_cmd,
    start,
    stats_cmd,
    unauth_cmd,
    unban_cmd,
    users_cmd,
)
from app.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("teradrop")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b'{"ok":true,"service":"teradrop"}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def start_health() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", settings.health_port), HealthHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    log.info("health on %s", settings.health_port)


def validate_bot_api_url() -> None:
    """Fail early with a useful message when a private API hostname is unreachable."""
    raw = (settings.bot_api_url or "").strip()
    if not raw:
        return
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SystemExit(
            "BOT_API_URL is invalid. Use an HTTP URL such as "
            "http://telegram-bot-api:8081 or leave it empty for Telegram's hosted API."
        )
    try:
        socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise SystemExit(
            f"BOT_API_URL host '{parsed.hostname}' cannot be resolved from this deployment. "
            "Leave BOT_API_URL empty for the hosted Telegram API, or use the reachable "
            "private/public URL of a separately running local Bot API server."
        ) from exc


def build_app() -> Application:
    settings.ensure_dirs()
    storage.init()
    saved = storage.kv_get("welcome")
    if saved:
        settings.welcome_text = saved
    builder = (
        Application.builder()
        .token(settings.bot_token)
        .concurrent_updates(max(16, settings.max_concurrent * 4))
        .connection_pool_size(max(16, settings.max_concurrent * 4))
        .pool_timeout(30)
    )
    if settings.bot_api_url:
        builder = builder.base_url(settings.telegram_api_root + "/bot").base_file_url(
            settings.telegram_api_root + "/file/bot"
        )
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("owner", owner_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("logs", logs_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CommandHandler("auth", auth_cmd))
    app.add_handler(CommandHandler("unauth", unauth_cmd))
    app.add_handler(CommandHandler("maintenance", maintenance_cmd))
    app.add_handler(CommandHandler("setwelcome", setwelcome_cmd))
    app.add_handler(CommandHandler("setcaption", setcaption_cmd))
    app.add_handler(CommandHandler("setlimit", setlimit_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CallbackQueryHandler(on_callback, block=False))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text, block=False))
    return app


def main() -> None:
    if not settings.bot_token:
        raise SystemExit("Set BOT_TOKEN in .env before starting TeraDrop.")
    validate_bot_api_url()
    start_health()
    log.info(
        "starting %s; large uploads=%s; effective limit=%s MB",
        settings.bot_name,
        settings.bot_api_enabled,
        settings.effective_max_file_mb,
    )
    application = build_app()
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
