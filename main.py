
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
        runner = await start_health_server()
        dp = Dispatcher()
        dp.include_router(router)
        async with Bot(token=TOKEN) as bot, RiotClient(RIOT_KEY, REGION) as riot:
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
