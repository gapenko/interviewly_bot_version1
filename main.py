"""
main.py — точка входа приложения.
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeDefault, CallbackQuery, ErrorEvent, Message, TelegramObject

import storage
from config import BOT_TOKEN
from handlers import main_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


class BlockedUserMiddleware(BaseMiddleware):
    """Пользователи, заблокированные администратором, не могут пользоваться ботом."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user and await storage.is_user_blocked(user.id) and not await storage.is_admin(user.id):
            if isinstance(event, CallbackQuery):
                await event.answer("Доступ к боту ограничен администратором.", show_alert=True)
            elif isinstance(event, Message):
                try:
                    await event.delete()
                except Exception:
                    pass
            return None
        return await handler(event, data)


async def on_error(event: ErrorEvent) -> bool:
    """Любая необработанная ошибка логируется, а пользователь получает понятный ответ вместо «вечной загрузки»."""
    logger.exception("Необработанная ошибка при обработке апдейта: %s", event.exception, exc_info=event.exception)
    callback = event.update.callback_query
    if callback:
        try:
            await callback.answer("Произошла ошибка. Попробуйте ещё раз или откройте /start.", show_alert=True)
        except Exception:
            pass
    return True


async def setup_bot_commands(bot: Bot) -> None:
    """Постоянный список команд в меню Telegram слева снизу."""
    commands = [
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="continue", description="▶️ Продолжить собеседование"),
        BotCommand(command="results", description="📊 Мои результаты и резюме"),
        BotCommand(command="pay", description="💳 Оплата и промокоды"),
        BotCommand(command="ref", description="🎁 Бонусы и приглашения"),
        BotCommand(command="reviews", description="⭐️ Отзывы"),
        BotCommand(command="reset", description="🔄 Начать собеседование заново"),
        BotCommand(command="support", description="💬 Поддержка"),
        BotCommand(command="help", description="ℹ️ Помощь"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())


async def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан в .env")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dp = Dispatcher()

    # Бот рассчитан на личные чаты: в группах он не может удалять сообщения и «чистить» экран
    dp.message.filter(F.chat.type == "private")
    dp.callback_query.filter(F.message.chat.type == "private")

    dp.message.outer_middleware(BlockedUserMiddleware())
    dp.callback_query.outer_middleware(BlockedUserMiddleware())
    dp.errors.register(on_error)

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
