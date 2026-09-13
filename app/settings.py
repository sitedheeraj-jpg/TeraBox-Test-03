from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _ids(raw: str) -> set[int]:
    out: set[int] = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    bot_token: str = Field(default="", alias="BOT_TOKEN")
    owner_ids_raw: str = Field(default="", alias="OWNER_IDS")
    bot_public: bool = Field(default=True, alias="BOT_PUBLIC")
    authorized_ids_raw: str = Field(default="", alias="AUTHORIZED_IDS")
    force_sub_channel: str = Field(default="", alias="FORCE_SUB_CHANNEL")
    log_channel: str = Field(default="", alias="LOG_CHANNEL")
    dump_channel: str = Field(default="", alias="DUMP_CHANNEL")
    maintenance: bool = Field(default=False, alias="MAINTENANCE")
    # The local Bot API server supports uploads up to 2 GB.  Without it,
    # Telegram's hosted Bot API remains limited to roughly 50 MB.
    max_file_mb: int = Field(default=2000, alias="MAX_FILE_MB")
    max_concurrent: int = Field(default=3, alias="MAX_CONCURRENT")
    bot_name: str = Field(default="TeraDrop", alias="BOT_NAME")
    bot_username: str = Field(default="TeraDropBot", alias="BOT_USERNAME")
    welcome_text: str = Field(default="", alias="WELCOME_TEXT")
    caption_template: str = Field(default="{filename}\n{size}", alias="CAPTION_TEMPLATE")
    teraboxdl_url: str = Field(default="https://www.teraboxdl.site", alias="TERABOXDL_URL")
    solver_url: str = Field(default="http://caboose.proxy.rlwy.net:42271", alias="SOLVER_URL")
    turnstile_sitekey: str = Field(default="0x4AAAAAAC3x1HiBz5IFyj7s", alias="TURNSTILE_SITEKEY")
    bot_api_url: str = Field(default="", alias="BOT_API_URL")
    download_dir: Path = Field(default=Path("/app/data/tmp"), alias="DOWNLOAD_DIR")
    data_dir: Path = Field(default=Path("/app/data"), alias="DATA_DIR")
    health_port: int = Field(default=8080, alias="HEALTH_PORT")

    @property
    def owner_ids(self) -> set[int]:
        return _ids(self.owner_ids_raw)

    @property
    def authorized_ids(self) -> set[int]:
        return _ids(self.authorized_ids_raw)

    @property
    def max_file_bytes(self) -> int:
        return max(1, self.max_file_mb) * 1024 * 1024

    @property
    def telegram_api_root(self) -> str:
        raw = (self.bot_api_url or "").strip().rstrip("/")
        if not raw:
            return "https://api.telegram.org"
        if raw.endswith("/file/bot"):
            raw = raw[: -len("/file/bot")]
        if raw.endswith("/bot"):
            raw = raw[: -len("/bot")]
        return raw.rstrip("/")

    @property
    def bot_api_enabled(self) -> bool:
        return bool((self.bot_api_url or "").strip())

    @property
    def transport_max_file_mb(self) -> int:
        return 2000 if self.bot_api_enabled else 49

    @property
    def effective_max_file_mb(self) -> int:
        return max(1, min(max(1, self.max_file_mb), self.transport_max_file_mb))

    @property
    def effective_max_file_bytes(self) -> int:
        return self.effective_max_file_mb * 1024 * 1024

    def telegram_method_url(self, method: str) -> str:
        return f"{self.telegram_api_root}/bot{self.bot_token}/{method}"

    @property
    def resolver_hosts(self) -> list[str]:
        primary = (self.teraboxdl_url or "https://www.teraboxdl.site").rstrip("/")
        hosts = [primary, "https://www.teraboxdl.site", "https://teraboxdl.site"]
        out: list[str] = []
        for host in hosts:
            if host not in out:
                out.append(host)
        return out

    def is_owner(self, user_id: int) -> bool:
        return user_id in self.owner_ids

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
