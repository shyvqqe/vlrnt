"""Личный Telegram-бот: автоматически уведомляет только о поражениях VALORANT."""

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv
from aiohttp import web

from db import Database
from parser import loss_message, parse_match
from riot_api import RiotClient, RiotApiError

# При локальном запуске .env лежит рядом с main.py; Render передаст переменные сам.
load_dotenv(Path(__file__).with_name(".env"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("valorant-bot")

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
RIOT_KEY = os.getenv("RIOT_API_KEY", "").strip()
OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "8287059539"))
GAME_NAME = os.getenv("RIOT_GAME_NAME", "zev1x")
TAG_LINE = os.getenv("RIOT_TAG_LINE", "436")
REGION = os.getenv("VAL_REGION", "eu").lower()
INTERVAL = max(30, int(os.getenv("CHECK_INTERVAL_SECONDS", "180")))
DB_PATH = os.getenv("DB_PATH", "./data/valorant.sqlite3")

router = Router()


class Monitor:
    def __init__(self, bot: Bot, db: Database, riot: RiotClient):
        self.bot = bot
        self.db = db
        self.riot = riot
        self.lock = asyncio.Lock()
        self.wakeup = asyncio.Event()
        self.cached_puuid: str | None = None

    async def check_once(self):
        if self.db.get("owner_id") != OWNER_ID:
            return  # Пока владелец не нажал /start, никуда не пишем.
        async with self.lock:
            puuid = self.cached_puuid
            if not puuid:
                puuid = await self.riot.puuid(GAME_NAME, TAG_LINE)
                self.cached_puuid = puuid
            history = await self.riot.matchlist(puuid)
            history = [h for h in history if h.get("matchId")]
            history.sort(key=lambda h: (int(h.get("gameStartTimeMillis") or 0), h["matchId"]))

            # Первый запуск: фиксируем уже существующий матч, не отправляя историю.
            if self.db.get("last_match_id") is None:
                if history:
                    last = history[-1]
                    self.db.put("last_match_id", last["matchId"])
                    self.db.put("last_match_time", int(last.get("gameStartTimeMillis") or 0))
                else:
                    # Список пуст, но инициализация завершена: первую будущую игру не пропустим.
                    self.db.put("last_match_id", "__empty_history__")
                    self.db.put("last_match_time", 0)
                self.db.put("loss_streak", 0)
                LOG.info("Первичный курсор установлен. Старые матчи не отправляем.")
                await self.deliver_pending()
                return

            last_time = int(self.db.get("last_match_time", 0))
            last_id = self.db.get("last_match_id")
            unseen = [
                h for h in history
                if (int(h.get("gameStartTimeMillis") or 0) > last_time or
                    (int(h.get("gameStartTimeMillis") or 0) == last_time and h["matchId"] != last_id))
                and not self.db.exists(h["matchId"])
            ]

            # Детали обрабатываем хронологически, иначе серия проигрышей будет неверной.
            content = {}
            if unseen:
                try:
                    content = await self.riot.content()
                except RiotApiError as exc:
                    LOG.warning("Справочник Riot недоступен: %s", exc)
            for h in unseen:
                match_id = h["matchId"]
                details = await self.riot.match(match_id)
                result = parse_match(details, puuid, content)
                streak = int(self.db.get("loss_streak", 0))
                if result.outcome == "loss":
                    streak += 1
                elif result.outcome in {"win", "draw"}:
                    streak = 0
                # В режимах без явного победителя сохраняем предыдущую серию.
                message = loss_message(result, streak) if result.outcome == "loss" else None
                started = int(h.get("gameStartTimeMillis") or details.get("matchInfo", {}).get("gameStartMillis") or 0)
                self.db.record(match_id, started, result, message, streak)
                LOG.info("Матч %s обработан (%s)", match_id, result.outcome)
            await self.deliver_pending()

    async def deliver_pending(self):
        # При временной ошибке Telegram сообщения сохраняются в SQLite до следующей попытки.
        for row in self.db.pending():
            try:
                await self.bot.send_message(chat_id=OWNER_ID, text=row["message"])
            except Exception:
                LOG.exception("Не удалось отправить уведомление. Повторим позже.")
                break
            else:
                self.db.mark_sent(row["match_id"])

    async def run_forever(self):
        while True:
            try:
                await self.check_once()
            except (RiotApiError, ValueError, KeyError):
                LOG.exception("Ошибка обработки Riot API, повтор через %s сек.", INTERVAL)
            except Exception:
                LOG.exception("Непредвиденная ошибка, цикл продолжится")
            try:
                await asyncio.wait_for(self.wakeup.wait(), timeout=INTERVAL)
            except asyncio.TimeoutError:
                pass
            self.wakeup.clear()


monitor: Monitor | None = None
storage: Database | None = None


def is_owner(message: Message) -> bool:
    return (
        message.from_user is not None and message.from_user.id == OWNER_ID
        and message.chat.type == "private"
    )


@router.message(Command("start"))
async def start(message: Message):
    if not is_owner(message):
        # Чужие чаты игнорируем молча: бот предназначен только для владельца.
        return
    assert storage is not None and monitor is not None
    storage.put("owner_id", OWNER_ID)
    monitor.wakeup.set()
    await message.answer(
        f"Мониторинг {GAME_NAME}#{TAG_LINE} включён. "
        f"Проверяю матчи каждые {INTERVAL} секунд. "
        "Пишу сам только о новых поражениях. Старые матчи не отправляю."
    )


@router.message(Command("last"))
async def last(message: Message):
    if not is_owner(message):
        return
    assert storage is not None
    item = storage.last_loss()
    await message.answer(item["message"] if item else "Новых проигрышей после запуска ещё нет.")


@router.message(Command("stats"))
async def stats(message: Message):
    if not is_owner(message):
        return
    assert storage is not None
    row = storage.stats()
    if not row["total"]:
        await message.answer("Статистика появится после новых матчей. Старая история не учитывается.")
        return
    await message.answer(
        "Матчи после начала мониторинга:\n"
        f"Всего: {row['total']}\n"
        f"Побед: {row['wins']}\n"
        f"Поражений: {row['losses']}\n"
        f"K/D/A: {row['kills']}/{row['deaths']}/{row['assists']}"
    )


@router.message(Command("streak"))
async def streak(message: Message):
    if not is_owner(message):
        return
    assert storage is not None
    await message.answer(f"Текущая серия поражений: {storage.get('loss_streak', 0)}")


async def start_health_server() -> web.AppRunner:
    """Открываем HTTP-порт для Render Web Service, не заменяя Telegram polling."""
    async def health(_request: web.Request) -> web.Response:
        # Этот адрес сообщает о работе процесса, но не о доступности Riot API.
        return web.Response(text="OK\n", content_type="text/plain")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        # Render ожидает 0.0.0.0 и порт из переменной PORT.
        port = int(os.getenv("PORT", "10000"))
        site = web.TCPSite(runner, host="0.0.0.0", port=port)
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    LOG.info("HTTP-проверка Render запущена на 0.0.0.0:%s", port)
    return runner


async def main():
    global monitor, storage
    if not TOKEN or not RIOT_KEY or TOKEN.startswith("ВСТАВЬ") or RIOT_KEY.startswith("ВСТАВЬ"):
        raise RuntimeError("Укажи TELEGRAM_TOKEN и одобренный RIOT_API_KEY в .env / Render Environment")
    storage = Database(DB_PATH)
    runner = None
    try:
        # HTTP-порт нужен Render Web Service; Telegram по-прежнему работает через polling.
        runner = await start_health_server()
        dp = Dispatcher()
        dp.include_router(router)
        async with Bot(token=TOKEN) as bot, RiotClient(RIOT_KEY, REGION) as riot:
            # Фоновый цикл запускается независимо от команд пользователя.
            monitor = Monitor(bot, storage, riot)
            task = asyncio.create_task(monitor.run_forever())
            try:
                await dp.start_polling(bot, allowed_updates=["message"])
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
    finally:
        if runner is not None:
            await runner.cleanup()
        storage.close()


if __name__ == "__main__":
    asyncio.run(main())
