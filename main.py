"""Telegram-бот принимает результаты Overwolf по HTTPS и пишет ТОЛЬКО владельцу."""

import asyncio
import contextlib
import hmac
import logging
import os
import time
from pathlib import Path

from aiohttp import web
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

from db import Database
from match_data import loss_message, validate_match

load_dotenv(Path(__file__).with_name(".env"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("overwolf-bot")

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "8287059539"))
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "./data/valorant.sqlite3")
PORT = int(os.getenv("PORT", "10000"))

router = Router()
store: Database | None = None
telegram: Bot | None = None
send_lock = asyncio.Lock()


def is_owner(message: Message) -> bool:
    return (
        message.from_user is not None and message.from_user.id == OWNER_ID
        and message.chat.type == "private"
    )


async def deliver_pending():
    """При временном сбое Telegram оставляем сообщения в SQLite для повтора."""
    assert store is not None and telegram is not None
    if store.get("owner_id") != OWNER_ID:
        return
    async with send_lock:
        for row in store.pending():
            try:
                await telegram.send_message(chat_id=OWNER_ID, text=row["message"])
            except Exception:
                LOG.exception("Telegram недоступен, повтор отправки позднее")
                break
            store.mark_sent(row["match_id"])


async def pending_loop():
    """Повтор доставки по таймеру; не опрашиваем Riot API."""
    while True:
        try:
            await deliver_pending()
        except Exception:
            LOG.exception("Сбой обработки очереди Telegram")
        await asyncio.sleep(30)


@router.message(Command("start"))
async def start(message: Message):
    if not is_owner(message):
        return
    assert store is not None
    if store.get("owner_started_at") is None:
        store.put("owner_started_at", int(time.time() * 1000))
    store.put("owner_id", OWNER_ID)
    await message.answer(
        "Мониторинг Valorant через Overwolf включён. "
        "Запусти локальное приложение на ПК. "
        "Только новые поражения будут приходить сюда; канал не используется."
    )


@router.message(Command("last"))
async def last(message: Message):
    if not is_owner(message):
        return
    assert store is not None
    row = store.last_loss()
    await message.answer(row["message"] if row else "Проигрышей пока нет.")


@router.message(Command("stats"))
async def stats(message: Message):
    if not is_owner(message):
        return
    assert store is not None
    s = store.stats()
    await message.answer(
        "Матчи, переданные Overwolf после /start:\n"
        f"Всего: {s['total']}\nПобед: {s['wins']}\nПоражений: {s['losses']}\n"
        f"K/D/A (из доступных данных): {s['kills']}/{s['deaths']}/{s['assists']}"
    )


@router.message(Command("streak"))
async def streak(message: Message):
    if not is_owner(message):
        return
    assert store is not None
    await message.answer(f"Текущая серия поражений: {store.get('loss_streak', 0)}")


def cors_headers() -> dict:
    # Для локального Overwolf расширения. Доступ ограничивается BRIDGE_TOKEN,
    # а не CORS (CORS сам по себе не является авторизацией).
    return {"Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Bridge-Token",
            "Cache-Control": "no-store"}


def authorized(request: web.Request) -> bool:
    supplied = request.headers.get("X-Bridge-Token", "")
    return bool(supplied and hmac.compare_digest(supplied, BRIDGE_TOKEN))


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="OK\n")


async def options(_request: web.Request) -> web.Response:
    return web.Response(status=204, headers=cors_headers())


async def ping(request: web.Request) -> web.Response:
    if not authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401, headers=cors_headers())
    return web.json_response({"status": "ready"}, headers=cors_headers())


async def accept_match(request: web.Request) -> web.Response:
    """Секретный вход для Overwolf: принимает результат, не публикует в канал."""
    if not authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401, headers=cors_headers())
    # Не принимаем произвольно большие тела и незнакомые типы данных.
    if request.content_length is not None and request.content_length > 8192:
        return web.json_response({"error": "too_large"}, status=413, headers=cors_headers())
    try:
        data = validate_match(await request.json())
    except (ValueError, TypeError, UnicodeError):
        return web.json_response({"error": "invalid_match"}, status=400, headers=cors_headers())

    assert store is not None
    if store.get("owner_id") != OWNER_ID:
        # До /start НЕ накапливаем старые матчи для будущей рассылки.
        return web.json_response({"status": "ignored_until_start"}, headers=cors_headers())
    # При задержке сети не отправляем матчи, сыгранные ещё до первого /start.
    if data["finished_at"] < int(store.get("owner_started_at", 0)):
        return web.json_response({"status": "ignored_before_start"}, headers=cors_headers())
    if store.exists(data["match_id"]):
        return web.json_response({"status": "duplicate"}, headers=cors_headers())
    next_streak = int(store.get("loss_streak", 0)) + 1 if data["outcome"] == "loss" else 0
    text = loss_message(data, next_streak) if data["outcome"] == "loss" else None
    inserted, streak = store.record(data, text)
    LOG.info("Матч %s: %s, серия: %d, новый: %s", data["match_id"],
             data["outcome"], streak, inserted)
    # Сначала отвечаем быстро; доставка пройдет через цикл каждые <=30 сек.
    return web.json_response({"status": "saved" if inserted else "duplicate"}, headers=cors_headers())


async def main():
    global store, telegram
    if not TOKEN or TOKEN.startswith("ВСТАВЬ"):
        raise RuntimeError("Укажи TELEGRAM_TOKEN в Render Environment")
    if len(BRIDGE_TOKEN) < 32 or BRIDGE_TOKEN.startswith("ВСТАВЬ"):
        raise RuntimeError("Задай случайный BRIDGE_TOKEN длиной от 32 символов")
    store = Database(DB_PATH)
    app = web.Application(client_max_size=8192)
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/api/ping", ping)
    app.router.add_post("/api/match", accept_match)
    app.router.add_route("OPTIONS", "/api/match", options)
    app.router.add_route("OPTIONS", "/api/ping", options)
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        LOG.info("HTTP-порт %d активен, Overwolf endpoint /api/match", PORT)
        async with Bot(token=TOKEN) as bot:
            telegram = bot
            dp = Dispatcher()
            dp.include_router(router)
            task = asyncio.create_task(pending_loop())
            try:
                await dp.start_polling(bot, allowed_updates=["message"])
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
    finally:
        await runner.cleanup()
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
