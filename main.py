"""
main.py — точка входа: инициализация бота, диспетчера и запуск polling.
"""
import asyncio
import logging
import socket

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from handlers import main_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # 1. Принудительно отключаем IPv6 (AF_INET = только чистый IPv4)
    # Это устраняет таймауты aiohappyeyeballs в облачных контейнерах
    connector = aiohttp.TCPConnector(
        family=socket.AF_INET,
        ssl=True,
    )
    
    # 2. Увеличиваем таймаут на опрос Telegram
    timeout = aiohttp.ClientTimeout(total=45, connect=15)
    session = AiohttpSession(connector=connector, timeout=timeout)

    bot = Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(main_router)

    logger.info("Бот запускается...")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.warning("Пропуск удаления вебхука: %s", e)

    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен.")
