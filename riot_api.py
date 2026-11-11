"""Неблокирующий клиент только к официальному Riot API."""

import asyncio
import logging
from urllib.parse import quote

import aiohttp

LOG = logging.getLogger(__name__)


class RiotApiError(Exception):
    """Ответ сервера Riot не позволяет продолжить запрос."""


class RiotClient:
    def __init__(self, api_key: str, region: str = "eu"):
        if region not in {"eu", "na", "ap", "kr", "latam", "br"}:
            raise ValueError("Неизвестный регион VALORANT")
        self.key = api_key
        self.region = region
        self.session: aiohttp.ClientSession | None = None
        self.content_cache: dict = {}

    async def __aenter__(self):
        # Таймаут не позволит сети навсегда заблокировать цикл.
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=25),
            headers={"X-Riot-Token": self.key},
        )
        return self

    async def __aexit__(self, *_):
        if self.session:
            await self.session.close()

    async def get(self, routing: str, path: str) -> dict:
        if self.session is None:
            raise RuntimeError("Клиент Riot не открыт")
        url = f"https://{routing}.api.riotgames.com{path}"
        for attempt in range(3):
            try:
                async with self.session.get(url) as response:
                    if response.status == 429 or response.status >= 500:
                        # Уважаем заголовок Retry-After при лимитах API.
                        if attempt < 2:
                            try:
                                delay = min(float(response.headers.get("Retry-After", "2")), 30)
                            except ValueError:
                                delay = 2
                            await asyncio.sleep(max(1, delay))
                            continue
                    if response.status != 200:
                        message = (await response.text())[:180]
                        raise RiotApiError(f"Riot HTTP {response.status}: {message}")
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == 2:
                    raise RiotApiError("Riot недоступен по сети") from exc
                LOG.warning("Ошибка соединения с Riot, повтор #%s", attempt + 1)
                await asyncio.sleep(2 ** attempt)
        raise RiotApiError("Не удалось получить ответ Riot")

    async def puuid(self, name: str, tag: str) -> str:
        # Для VALORANT EU аккаунт запрашиваем через региональный кластер Europe.
        region = "europe" if self.region == "eu" else (
            "americas" if self.region in {"na", "latam", "br"} else "asia"
        )
        data = await self.get(
            region,
            f"/riot/account/v1/accounts/by-riot-id/{quote(name, safe='')}/{quote(tag, safe='')}",
        )
        return data["puuid"]

    async def matchlist(self, puuid: str) -> list[dict]:
        data = await self.get(
            self.region,
            f"/val/match/v1/matchlists/by-puuid/{quote(puuid, safe='')}",
        )
        return data.get("history", [])

    async def match(self, match_id: str) -> dict:
        return await self.get(self.region, f"/val/match/v1/matches/{quote(match_id, safe='')}")

    async def content(self) -> dict:
        # Справочник героев и карт. При сетевой ошибке используем старый кэш.
        if self.content_cache:
            return self.content_cache
        self.content_cache = await self.get(self.region, "/val/content/v1/contents?locale=en-US")
        return self.content_cache
