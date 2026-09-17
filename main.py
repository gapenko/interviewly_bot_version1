"""
main.py — точка входа приложения.
Регистрирует нативное командное меню слева снизу и запускает polling.
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeDefault

from config import BOT_TOKEN
from handlers import main_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def setup_bot_commands(bot: Bot) -> None:
    """Устанавливает постоянный список команд с описанием в меню Telegram слева снизу."""
    commands = [
        BotCommand(command="start", description="🏠 Главное меню / Начать"),
        BotCommand(command="continue", description="▶️ Продолжить собеседование"),
        BotCommand(command="ref", description="🎁 Рефералы и бонусы"),
        BotCommand(command="reviews", description="⭐️ Отзывы участников"),
        BotCommand(command="pay", description="💳 Оплата и промокоды"),
        BotCommand(command="reset", description="🔄 Начать интервью заново"),
        BotCommand(command="support", description="💬 Служба поддержки"),
        BotCommand(command="help", description="ℹ️ Инструкция и регламент"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())


async def main() -> None:
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(main_router)

    logger.info("Настройка командного меню бота...")
    await setup_bot_commands(bot)

    logger.info("Бот запускается...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен.")