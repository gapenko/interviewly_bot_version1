"""
handlers/admin.py — Админ-панель, статистика, баланс Stars,
рассылка и ответы техподдержки прямо через бота.
"""

import asyncio
import json
import logging
import os

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import (
    ADMIN_IDS,
    ADMIN_SECRET_KEY,
    PAYMENTS_LOG_FILE,
    USERS_DATA_FILE,
)
from states import AdminStates

logger = logging.getLogger(__name__)

router = Router(name="admin")

AUTHENTICATED_ADMINS: set[int] = set(ADMIN_IDS)


# =========================================================
# ПРОВЕРКА АДМИНИСТРАТОРА
# =========================================================

class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        if not user:
            return False
        return user.id in AUTHENTICATED_ADMINS


# =========================================================
# КЛАВИАТУРА АДМИНКИ
# =========================================================

def get_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Общая статистика",
                    callback_data="admin_stats",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐️ Баланс Telegram Stars",
                    callback_data="admin_stars",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Создать рассылку",
                    callback_data="admin_broadcast",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💾 Выгрузить базы (JSON)",
                    callback_data="admin_export_db",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Закрыть панель",
                    callback_data="admin_close",
                )
            ],
        ]
    )


# =========================================================
# АВТОРИЗАЦИЯ ПО ПАРОЛЮ
# =========================================================

@router.message(Command("auth"))
async def cmd_auth(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return

    parts = (message.text or "").split(maxsplit=1)

    if len(parts) > 1:
        provided_key = parts[1].strip()

        if provided_key == ADMIN_SECRET_KEY:
            AUTHENTICATED_ADMINS.add(message.from_user.id)
            await state.clear()

            await message.answer(
                "✅ <b>Доступ администратора успешно подтверждён!</b>\n\n"
                "Откройте панель управления командой /admin",
                parse_mode="HTML",
            )
            return

        await message.answer("❌ Неверный пароль администратора.")
        return

    await state.set_state(AdminStates.waiting_for_auth_key)
    await message.answer("🔑 Введите пароль администратора:")


@router.message(AdminStates.waiting_for_auth_key)
async def process_auth_key(
    message: Message,
    state: FSMContext,
) -> None:
    if not message.from_user:
        return

    if (message.text or "").strip() == ADMIN_SECRET_KEY:
        AUTHENTICATED_ADMINS.add(message.from_user.id)
        await state.clear()

        await message.answer(
            "✅ <b>Доступ администратора успешно подтверждён!</b>\n\n"
            "Откройте панель управления командой /admin",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "❌ Неверный пароль. "
            "Попробуйте снова или отмените ввод командой /cancel."
        )


# =========================================================
# ГЛАВНОЕ МЕНЮ АДМИНКИ
# =========================================================

@router.message(Command("admin"), IsAdmin())
async def cmd_admin(message: Message) -> None:
    await message.answer(
        "🛠 <b>Панель управления администратора</b>\n\n"
        "Выберите действие:",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_menu", IsAdmin())
async def cb_admin_menu(callback: CallbackQuery) -> None:
    if not callback.message:
        return

    await callback.message.edit_text(
        "🛠 <b>Панель управления администратора</b>\n\n"
        "Выберите действие:",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_close", IsAdmin())
async def cb_admin_close(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.delete()
    await callback.answer("Панель закрыта.")


# =========================================================
# ОТВЕТ НА ОБРАЩЕНИЕ В ПОДДЕРЖКУ ПРЯМО ИЗ БОТА
# =========================================================

@router.callback_query(F.data.startswith("reply_support:"), IsAdmin())
async def cb_reply_support(callback: CallbackQuery, state: FSMContext) -> None:
    user_id_str = callback.data.replace("reply_support:", "")
    try:
        target_user_id = int(user_id_str)
    except ValueError:
        await callback.answer("Ошибка ID пользователя.")
        return

    await state.update_data(target_user_id=target_user_id)
    await state.set_state(AdminStates.waiting_support_reply)

    await callback.message.answer(
        f"✍️ <b>Ответ пользователю (ID: <code>{target_user_id}</code>)</b>\n\n"
        "Напишите текст ответа. Он будет доставлен пользователю напрямую в чат бота.\n"
        "Для отмены отправьте /cancel.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminStates.waiting_support_reply, IsAdmin())
async def process_support_reply(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите текстовый ответ.")
        return

    data = await state.get_data()
    target_user_id = data.get("target_user_id")

    if not target_user_id:
        await state.clear()
        await message.answer("⚠️ Пользователь для ответа не найден.")
        return

    user_message = (
        "📨 <b>Ответ от службы поддержки:</b>\n\n"
        f"{message.text}\n\n"
        "<i>Если у вас остались вопросы, вы можете снова нажать «💬 Поддержка».</i>"
    )

    try:
        await bot.send_message(chat_id=target_user_id, text=user_message, parse_mode="HTML")
        await message.answer(f"✅ Ответ успешно доставлен пользователю (ID: <code>{target_user_id}</code>)!", parse_mode="HTML")
    except Exception as e:
        logger.error("Не удалось отправить ответ поддержки пользователю %s: %s", target_user_id, e)
        await message.answer(f"❌ Не удалось доставить сообщение пользователю: <code>{e}</code>", parse_mode="HTML")

    await state.clear()


# =========================================================
# СТАТИСТИКА
# =========================================================

@router.callback_query(F.data == "admin_stats", IsAdmin())
async def cb_admin_stats(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.message:
        return

    users_count = 0
    paid_users_count = 0

    if os.path.exists(USERS_DATA_FILE):
        try:
            with open(USERS_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                users_count = len(data)
                paid_users_count = sum(
                    1
                    for u in data.values()
                    if isinstance(u, dict) and (u.get("paid") or u.get("has_access") or u.get("is_paid"))
                )
            elif isinstance(data, list):
                users_count = len(data)
        except Exception as e:
            logger.error("Ошибка чтения USERS_DATA_FILE: %s", e)

    real_balance = None
    try:
        stars_data = await bot.get_my_star_balance()
        real_balance = getattr(stars_data, "amount", stars_data)
    except Exception as e:
        logger.warning("Не удалось получить баланс Stars через API в статистике: %s", e)

    payments_count = 0
    logged_stars = 0

    if os.path.exists(PAYMENTS_LOG_FILE):
        try:
            with open(PAYMENTS_LOG_FILE, "r", encoding="utf-8") as f:
                payments = json.load(f)

            if isinstance(payments, list):
                payments_count = len(payments)
                logged_stars = sum(
                    p.get("amount_stars", p.get("total_amount", p.get("amount", 0)))
                    for p in payments
                    if isinstance(p, dict)
                )
        except Exception as e:
            logger.error("Ошибка чтения PAYMENTS_LOG_FILE: %s", e)

    display_stars = real_balance if real_balance is not None else logged_stars

    stats_text = (
        "📊 <b>Общая статистика бота</b>\n\n"
        f"👥 Всего пользователей: <code>{users_count}</code>\n"
        f"💎 Пользователей с доступом: <code>{paid_users_count}</code>\n"
        f"💳 Всего успешных платежей: <code>{payments_count}</code>\n"
        f"⭐️ <b>Текущий баланс Stars:</b> <code>{display_stars} XTR</code>\n"
        f"<i>(По логам платежей зафиксировано: {logged_stars} XTR)</i>"
    )

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="admin_menu",
                )
            ]
        ]
    )

    await callback.message.edit_text(
        stats_text,
        reply_markup=back_kb,
        parse_mode="HTML",
    )
    await callback.answer()


# =========================================================
# БАЛАНС TELEGRAM STARS
# =========================================================

@router.message(Command("stars"), IsAdmin())
async def cmd_stars(message: Message, bot: Bot) -> None:
    await send_stars_info(message.answer, bot)


@router.callback_query(F.data == "admin_stars", IsAdmin())
async def cb_admin_stars(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.message:
        return

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="admin_menu",
                )
            ]
        ]
    )

    await send_stars_info(callback.message.edit_text, bot, reply_markup=back_kb)
    await callback.answer()


async def send_stars_info(sender_func, bot: Bot, **kwargs) -> None:
    try:
        stars_data = await bot.get_my_star_balance()
        balance = getattr(stars_data, "amount", stars_data)

        text = (
            "⭐️ <b>Баланс Telegram Stars (напрямую из Telegram API):</b>\n\n"
            f"Текущий баланс: <code>{balance} XTR</code>"
        )
    except Exception as e:
        logger.warning("Не удалось получить баланс Stars через API: %s", e)

        total_earned = 0
        if os.path.exists(PAYMENTS_LOG_FILE):
            try:
                with open(PAYMENTS_LOG_FILE, "r", encoding="utf-8") as f:
                    payments = json.load(f)

                if isinstance(payments, list):
                    total_earned = sum(
                        p.get("amount_stars", p.get("total_amount", p.get("amount", 0)))
                        for p in payments
                        if isinstance(p, dict)
                    )
            except Exception as log_error:
                logger.error("Ошибка чтения PAYMENTS_LOG_FILE: %s", log_error)

        text = (
            "⭐️ <b>Баланс Telegram Stars</b>\n\n"
            f"• По локальным логам: <code>{total_earned} XTR</code>\n\n"
            f"<i>Прямой запрос баланса через Telegram API временно недоступен:</i>\n"
            f"<code>{e}</code>"
        )

    await sender_func(text=text, parse_mode="HTML", **kwargs)


# =========================================================
# ВЫГРУЗКА БАЗ (JSON)
# =========================================================

@router.callback_query(F.data == "admin_export_db", IsAdmin())
async def cb_export_db(callback: CallbackQuery) -> None:
    if not callback.message:
        return

    sent_any = False
    for filepath in [USERS_DATA_FILE, PAYMENTS_LOG_FILE]:
        if os.path.exists(filepath):
            await callback.message.answer_document(FSInputFile(filepath))
            sent_any = True

    if not sent_any:
        await callback.message.answer("⚠️ Файлы данных пока не созданы.")

    await callback.answer("Выгрузка завершена")


# =========================================================
# РАССЫЛКА
# =========================================================

@router.callback_query(F.data == "admin_broadcast", IsAdmin())
async def cb_start_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        return

    await state.set_state(AdminStates.waiting_for_broadcast_msg)
    await callback.message.answer(
        "📝 Отправьте сообщение для рассылки всем пользователям.\n\n"
        "Поддерживается текст и медиа.\n"
        "Для отмены отправьте /cancel."
    )
    await callback.answer()


@router.message(Command("cancel"), IsAdmin())
async def cmd_cancel_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Действие отменено.")


@router.message(AdminStates.waiting_for_broadcast_msg, IsAdmin())
async def process_broadcast(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    user_ids = []

    if os.path.exists(USERS_DATA_FILE):
        try:
            with open(USERS_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                user_ids = [int(uid) for uid in data.keys() if str(uid).isdigit()]
            elif isinstance(data, list):
                user_ids = [
                    int(u.get("user_id", u.get("id")))
                    for u in data
                    if isinstance(u, dict) and (u.get("user_id") or u.get("id"))
                ]
        except Exception as e:
            logger.error("Ошибка получения списка пользователей: %s", e)

    if not user_ids:
        await message.answer("⚠️ Список пользователей пуст. Некому рассылать.")
        return

    status_msg = await message.answer(f"⏳ Запуск рассылки на {len(user_ids)} пользователей...")

    success = 0
    blocked = 0

    for uid in user_ids:
        try:
            await message.copy_to(chat_id=uid)
            success += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            blocked += 1
            logger.debug("Не удалось отправить сообщение пользователю %s: %s", uid, e)

    await status_msg.edit_text(
        "✅ <b>Рассылка завершена!</b>\n\n"
        f"• Доставлено: <code>{success}</code>\n"
        f"• Ошибок / заблокировали бота: <code>{blocked}</code>",
        parse_mode="HTML",
    )