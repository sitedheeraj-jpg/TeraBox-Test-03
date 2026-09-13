from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from app.settings import settings

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


@dataclass
class Clearance:
    cookies: dict[str, str] = field(default_factory=dict)
    user_agent: str = DEFAULT_UA
    token: str | None = None


async def solve_challenge(siteurl: str, attempts: int = 3) -> Clearance:
    url = settings.solver_url.rstrip("/") + "/solve-challenge"
    last_error = "solver failed"
    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=110) as client:
                res = await client.post(url, json={"siteurl": siteurl, "timeout": 70})
                data = res.json()
            if res.status_code >= 400 or data.get("error"):
                code = str(data.get("error_code") or "")
                last_error = str(data.get("error") or f"Solver HTTP {res.status_code}")
                if code == "browser_error" and attempt < attempts:
                    await asyncio.sleep(1.5 * attempt)
                    continue
                raise RuntimeError(last_error)
            cookies: dict[str, str] = {}
            for item in data.get("cookies") or []:
                name = item.get("name")
                value = item.get("value")
                if name and value is not None:
                    cookies[str(name)] = str(value)
            return Clearance(
                cookies=cookies,
                user_agent=data.get("user_agent") or DEFAULT_UA,
            )
        except RuntimeError:
            raise
        except Exception as exc:
            last_error = str(exc)
            if attempt < attempts:
                await asyncio.sleep(1.5 * attempt)
                continue
            raise RuntimeError(last_error) from exc
    raise RuntimeError(last_error)


async def solve_turnstile(siteurl: str) -> str | None:
    url = settings.solver_url.rstrip("/") + "/solve"
    payload = {
        "sitekey": settings.turnstile_sitekey,
        "siteurl": siteurl,
        "timeout": 45,
    }
    try:
        async with httpx.AsyncClient(timeout=70) as client:
            res = await client.post(url, json=payload)
            data = res.json()
        return data.get("token")
    except Exception:
        return None
