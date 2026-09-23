"""
screen.py — «чистый чат»: у каждого пользователя в чате ровно один актуальный экран бота.

Как это работает:
- ID текущего экрана (последнего сообщения бота с меню) хранится в storage (data/screens.json).
- Нажатие кнопки: экран редактируется на месте. Если нажали кнопку на старом сообщении,
  редактируется оно, а прежний экран удаляется.
- Команда (/start, /help …): старый экран удаляется, новый отправляется вниз чата.
- Текст пользователя (ответ на вопрос, промокод, отзыв…) удаляется после обработки,
  а экран обновляется.
- Если экран нельзя отредактировать (документ, счёт на оплату, слишком старое сообщение),
  отправляется новое сообщение, а старое удаляется.

Исключения, которые НЕ удаляются автоматически:
- Уведомления (ответ поддержки, новые отзывы и обращения для админа, рассылки) — у них всегда
  есть кнопка «✖️ Скрыть», и нажатие кнопок в уведомлении не превращает его в экран.
- Итоговый документ .docx — это результат, который пользователь сохраняет себе.
"""
import html
import logging
import re
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import storage

logger = logging.getLogger(__name__)

TEXT_LIMIT = 4000
DISMISS_CALLBACK = "dismiss"


# =========================================================
# ТЕКСТ
# =========================================================

def esc(value) -> str:
    """Экранирует пользовательский текст для HTML-разметки Telegram."""
    return html.escape(str(value if value is not None else ""), quote=False)


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def truncate_plain(text: str, limit: int) -> str:
    """Обрезает уже экранированный текст без тегов, не разрывая HTML-сущности (&lt; и т.п.)."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    cut = re.sub(r"&[a-zA-Z#0-9]*$", "", cut)
    space = cut.rfind(" ")
    if space > limit * 0.7:
        cut = cut[:space]
    return cut.rstrip() + "…"


def fit_text(text: str, limit: int = TEXT_LIMIT) -> str:
    """Страховка от лимита Telegram (4096 символов)."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


# =========================================================
# ФИЛЬТРЫ
# =========================================================

def not_command(message: Message) -> bool:
    """Фильтр для обработчиков ввода: команды (/help, /cancel…) проходят дальше к своим обработчикам."""
    return not (message.text or message.caption or "").startswith("/")


def _is_notification(message: Message) -> bool:
    markup = message.reply_markup
    if not markup or not getattr(markup, "inline_keyboard", None):
        return False
    return any(
        getattr(btn, "callback_data", None) == DISMISS_CALLBACK
        for row in markup.inline_keyboard
        for btn in row
    )


# =========================================================
# ОТПРАВКА / УДАЛЕНИЕ
# =========================================================

async def delete_message(bot: Bot, chat_id: int, message_id: Optional[int]) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def delete_user_message(message: Message) -> None:
    """Удаляет сообщение пользователя (команду, ответ, ввод), чтобы не засорять чат."""
    try:
        await message.delete()
    except Exception:
        pass


async def _try_edit(bot: Bot, chat_id: int, message_id: int, text: str, markup) -> bool:
    try:
        await bot.edit_message_text(
            text=text, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="HTML"
        )
        return True
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "not modified" in err:
            return True
        if "can't parse entities" in err:
            try:
                await bot.edit_message_text(
                    text=fit_text(strip_html(text)), chat_id=chat_id, message_id=message_id,
                    reply_markup=markup, parse_mode=None,
                )
                return True
            except TelegramBadRequest as e2:
                return "not modified" in str(e2).lower()
        return False


async def _send(bot: Bot, chat_id: int, text: str, markup) -> Message:
    try:
        return await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "can't parse entities" not in str(e).lower():
            raise
        logger.warning("HTML не разобран (%s), отправляю без разметки", e)
        return await bot.send_message(chat_id=chat_id, text=fit_text(strip_html(text)), reply_markup=markup, parse_mode=None)


async def show_screen(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    *,
    source: Optional[Message] = None,
    force_new: bool = False,
) -> int:
    """
    Показывает экран. source — сообщение с нажатой кнопкой (callback.message).
    force_new=True — отправить экран новым сообщением внизу чата (для команд и после документов).
    """
    text = fit_text(text)
    current_id = await storage.get_screen_id(chat_id)

    if source is not None and _is_notification(source):
        # Кнопка нажата в уведомлении: его не трогаем, экран показываем отдельно
        source = None
        force_new = True

    target_id = None
    if not force_new:
        target_id = source.message_id if source is not None else current_id

    if target_id and await _try_edit(bot, chat_id, target_id, text, reply_markup):
        if current_id and current_id != target_id:
            await delete_message(bot, chat_id, current_id)
        await storage.set_screen_id(chat_id, target_id)
        return target_id

    sent = await _send(bot, chat_id, text, reply_markup)
    stale = {current_id, target_id, source.message_id if source is not None else None}
    for old_id in stale - {None, sent.message_id}:
        await delete_message(bot, chat_id, old_id)
    await storage.set_screen_id(chat_id, sent.message_id)
    return sent.message_id


async def replace_screen(bot: Bot, chat_id: int, new_message_id: int) -> None:
    """Делает экраном уже отправленное сообщение (например, счёт на оплату Stars) и удаляет старый экран."""
    current_id = await storage.get_screen_id(chat_id)
    if current_id and current_id != new_message_id:
        await delete_message(bot, chat_id, current_id)
    await storage.set_screen_id(chat_id, new_message_id)


# =========================================================
# УВЕДОМЛЕНИЯ (не экран: остаются в чате до нажатия «Скрыть»)
# =========================================================

def dismiss_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="✖️ Скрыть", callback_data=DISMISS_CALLBACK)]


async def notify(
    bot: Bot,
    chat_id: int,
    text: str,
    extra_rows: Optional[list[list[InlineKeyboardButton]]] = None,
) -> bool:
    """Отправляет уведомление с кнопкой «Скрыть». Возвращает True, если доставлено."""
    rows = list(extra_rows or []) + [dismiss_row()]
    try:
        await _send(bot, chat_id, fit_text(text), InlineKeyboardMarkup(inline_keyboard=rows))
        return True
    except Exception as e:
        logger.warning("Не удалось доставить уведомление в чат %s: %s", chat_id, e)
        return False
