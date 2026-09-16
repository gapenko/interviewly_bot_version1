"""
handlers/admin.py — расширенная админ-панель:
- Общая статистика, баланс Telegram Stars.
- UTM-генератор ссылок для Telegram-каналов с аналитикой переходов и конверсий.
- Рассылка, ответы техподдержки прямо из бота и выгрузка баз (JSON).
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
from keyboards import get_admin_keyboard
from states import AdminStates
from storage import create_campaign, get_all_campaigns

REVIEWS_DATA_FILE = "data/reviews.json"
CAMPAIGNS_DATA_FILE = "data/campaigns.json"

logger = logging.getLogger(__name__)
router = Router(name="admin")

AUTHENTICATED_ADMINS: set[int] = set(ADMIN_IDS)


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        if not user:
            return False
        return user.id in AUTHENTICATED_ADMINS


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
            await message.answer("✅ <b>Доступ администратора подтверждён!</b> Откройте панель: /admin", parse_mode="HTML")
            return
        await message.answer("❌ Неверный пароль администратора.")
        return

    await state.set_state(AdminStates.waiting_for_auth_key)
    await message.answer("🔑 Введите пароль администратора:")


@router.message(AdminStates.waiting_for_auth_key)
async def process_auth_key(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    if (message.text or "").strip() == ADMIN_SECRET_KEY:
        AUTHENTICATED_ADMINS.add(message.from_user.id)
        await state.clear()
        await message.answer("✅ <b>Доступ администратора подтверждён!</b> Откройте панель: /admin", parse_mode="HTML")
    else:
        await message.answer("❌ Неверный пароль. Попробуйте снова или отмените ввод: /cancel")


@router.message(Command("admin"), IsAdmin())
async def cmd_admin(message: Message) -> None:
    await message.answer(
        "🛠 <b>Панель управления администратора</b>\n\nВыберите действие:",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_menu", IsAdmin())
async def cb_admin_menu(callback: CallbackQuery) -> None:
    if not callback.message:
        return
    await callback.message.edit_text(
        "🛠 <b>Панель управления администратора</b>\n\nВыберите действие:",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_close", IsAdmin())
async def cb_admin_close(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.delete()
    await callback.answer("Панель закрыта.")


# --- СТАТИСТИКА И БАЛАНС STARS ---

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
                    paid_users_count = sum(1 for u in data.values() if isinstance(u, dict) and u.get("paid"))
        except Exception as e:
            logger.error("Ошибка чтения USERS_DATA_FILE: %s", e)

    payments_count = 0
    logged_rub = 0
    logged_stars = 0
    if os.path.exists(PAYMENTS_LOG_FILE):
        try:
            with open(PAYMENTS_LOG_FILE, "r", encoding="utf-8") as f:
                payments = json.load(f)
                if isinstance(payments, list):
                    payments_count = len(payments)
                    for p in payments:
                        if p.get("currency") == "RUB":
                            logged_rub += p.get("amount_stars", 0)
                        else:
                            logged_stars += p.get("amount_stars", 0)
        except Exception as e:
            logger.error("Ошибка чтения PAYMENTS_LOG_FILE: %s", e)

    real_balance = None
    try:
        stars_data = await bot.get_my_star_balance()
        real_balance = getattr(stars_data, "amount", stars_data)
    except Exception:
        pass

    display_stars = real_balance if real_balance is not None else logged_stars

    stats_text = (
        "📊 <b>Сводная статистика проекта</b>\n\n"
        f"👥 Всего кандидатов: <code>{users_count}</code>\n"
        f"💎 С полным доступом: <code>{paid_users_count}</code>\n"
        f"💳 Успешных оплат: <code>{payments_count}</code>\n"
        f"💰 Выручка ЮKassa (СБП): <code>{logged_rub} ₽</code>\n"
        f"⭐️ Баланс Stars: <code>{display_stars} XTR</code>"
    )

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад в меню", callback_data="admin_menu")]]
    )
    await callback.message.edit_text(stats_text, reply_markup=back_kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_stars", IsAdmin())
async def cb_admin_stars(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.message:
        return
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад в меню", callback_data="admin_menu")]]
    )
    try:
        stars_data = await bot.get_my_star_balance()
        bal = getattr(stars_data, "amount", stars_data)
        text = f"⭐️ <b>Баланс Telegram Stars:</b> <code>{bal} XTR</code>"
    except Exception as e:
        text = f"⭐️ <b>Ошибка запроса Stars API:</b> <code>{e}</code>"

    await callback.message.edit_text(text, reply_markup=back_kb, parse_mode="HTML")
    await callback.answer()


# --- РЕКЛАМНЫЕ ССЫЛКИ ДЛЯ КАНАЛОВ (UTM) ---

@router.callback_query(F.data == "admin_campaigns", IsAdmin())
async def cb_campaigns(callback: CallbackQuery, bot: Bot):
    if not callback.message:
        return
    camps = await get_all_campaigns()
    if not camps:
        await callback.message.answer("Пока нет созданных рекламных ссылок. Создайте первую кнопкой ниже.")
        await callback.answer()
        return

    bot_me = await bot.get_me()
    text = "🔗 <b>Статистика рекламных каналов:</b>\n\n"
    for tag, c in camps.items():
        link = f"https://t.me/{bot_me.username}?start=c_{tag}"
        text += (
            f"📌 <b>{c.get('description') or tag}</b> (<code>{tag}</code>)\n"
            f"├ Ссылка: <code>{link}</code>\n"
            f"├ Переходов: <b>{c.get('joins', 0)}</b> чел.\n"
            f"└ Оплат: <b>{c.get('payments', 0)}</b>\n\n"
        )

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад в меню", callback_data="admin_menu")]]
    )
    await callback.message.edit_text(text, reply_markup=back_kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_create_camp", IsAdmin())
async def cb_create_camp_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminStates.waiting_camp_tag)
    await callback.message.answer(
        "Введите идентификатор канала (латиницей, без пробелов, например: <code>habr_qa</code> или <code>proger_tg</code>):",
        parse_mode="HTML",
    )


@router.message(AdminStates.waiting_camp_tag, IsAdmin())
async def process_camp_tag(message: Message, state: FSMContext):
    tag = (message.text or "").strip().lower().replace(" ", "_")
    await state.update_data(tag=tag)
    await state.set_state(AdminStates.waiting_camp_desc)
    await message.answer("Теперь введите описание (например: <i>Рекламный пост в канале @proger</i>):", parse_mode="HTML")


@router.message(AdminStates.waiting_camp_desc, IsAdmin())
async def process_camp_desc(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    tag = data.get("tag", "campaign")
    desc = message.text.strip()
    await create_campaign(tag, desc)
    await state.clear()

    bot_me = await bot.get_me()
    link = f"https://t.me/{bot_me.username}?start=c_{tag}"
    await message.answer(
        f"✅ <b>Рекламная ссылка успешно создана!</b>\n\n"
        f"• <b>Канал/Описание:</b> {desc}\n"
        f"• <b>Тег:</b> <code>{tag}</code>\n"
        f"• <b>Индивидуальная ссылка:</b>\n<code>{link}</code>",
        parse_mode="HTML",
    )


# --- ОТВЕТ В ТЕХПОДДЕРЖКУ ИЗ БОТА ---

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
        f"✍️ <b>Ответ пользователю (ID: <code>{target_user_id}</code>)</b>\n\nНапишите текст ответа. Для отмены: /cancel",
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
        return

    user_message = f"📨 <b>Ответ службы поддержки:</b>\n\n{message.text}"
    try:
        await bot.send_message(chat_id=target_user_id, text=user_message, parse_mode="HTML")
        await message.answer(f"✅ Ответ успешно доставлен (ID: <code>{target_user_id}</code>)!", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Не удалось доставить сообщение: <code>{e}</code>", parse_mode="HTML")
    await state.clear()


# --- ВЫГРУЗКА БАЗ (JSON) ---

@router.callback_query(F.data == "admin_export_db", IsAdmin())
async def cb_export_db(callback: CallbackQuery) -> None:
    if not callback.message:
        return
    sent_any = False
    for filepath in [USERS_DATA_FILE, PAYMENTS_LOG_FILE, REVIEWS_DATA_FILE, CAMPAIGNS_DATA_FILE]:
        if os.path.exists(filepath):
            await callback.message.answer_document(FSInputFile(filepath))
            sent_any = True
    if not sent_any:
        await callback.message.answer("⚠️ Файлы данных пока не созданы.")
    await callback.answer("Выгрузка завершена")


# --- МАССОВАЯ РАССЫЛКА ---

@router.callback_query(F.data == "admin_broadcast", IsAdmin())
async def cb_start_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        return
    await state.set_state(AdminStates.waiting_for_broadcast_msg)
    await callback.message.answer(
        "📝 Отправьте сообщение для рассылки всем пользователям (текст, фото или документ).\nДля отмены отправьте /cancel."
    )
    await callback.answer()


@router.message(Command("cancel"), IsAdmin())
async def cmd_cancel_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Действие отменено.")


@router.message(AdminStates.waiting_for_broadcast_msg, IsAdmin())
async def process_broadcast(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_ids = []
    if os.path.exists(USERS_DATA_FILE):
        try:
            with open(USERS_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    user_ids = [int(uid) for uid in data.keys() if str(uid).isdigit()]
        except Exception as e:
            logger.error("Ошибка получения пользователей: %s", e)

    if not user_ids:
        await message.answer("⚠️ Список пользователей пуст.")
        return

    status_msg = await message.answer(f"⏳ Запуск рассылки на {len(user_ids)} пользователей...")
    success = 0
    blocked = 0

    for uid in user_ids:
        try:
            await message.copy_to(chat_id=uid)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            blocked += 1

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n• Доставлено: <code>{success}</code>\n• Заблокировали бота: <code>{blocked}</code>",
        parse_mode="HTML",
    )