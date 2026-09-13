from __future__ import annotations

import time


def format_bytes(num: float) -> str:
    if num <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    value = float(num)
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    if i == 0:
        return f"{int(value)} B"
    return f"{value:.1f} {units[i]}"


def format_speed(bps: float) -> str:
    return f"{format_bytes(bps)}/s"


def format_duration(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, r = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{r:02d}"
    return f"{m:02d}:{r:02d}"


def bar(pct: float, width: int = 16) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = round((pct / 100) * width)
    return "▰" * filled + "▱" * (width - filled)


def render(kind: str, filename: str, done: int, total: int, speed: float, elapsed: float) -> str:
    pct = (done / total * 100) if total else 0.0
    eta = ((total - done) / speed) if speed > 0 and total > done else 0.0
    title = "ᴜᴘʟᴏᴀᴅɪɴɢ ғɪʟᴇ" if kind == "upload" else "ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ ғɪʟᴇ"
    return (
        f"<b>{title}</b>\n<blockquote>{filename}</blockquote>\n"
        f"{bar(pct)}  {pct:.1f}%\n\n"
        f"<b>speed</b>    {format_speed(speed)}\n"
        f"<b>done</b>     {format_bytes(done)} / {format_bytes(total)}\n"
        f"<b>time</b>     {format_duration(elapsed)} elapsed · {format_duration(eta)} left"
    )


class Meter:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.last_t = self.started
        self.last_n = 0
        self.ema = 0.0

    def reset(self) -> None:
        self.started = time.monotonic()
        self.last_t = self.started
        self.last_n = 0
        self.ema = 0.0

    def update(self, done: int) -> tuple[float, float]:
        now = time.monotonic()
        dt = max(0.001, now - self.last_t)
        inst = (done - self.last_n) / dt
        self.ema = inst if self.ema == 0 else self.ema * 0.72 + inst * 0.28
        self.last_t = now
        self.last_n = done
        return self.ema, now - self.started
