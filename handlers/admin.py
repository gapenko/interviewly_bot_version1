"""
handlers/admin.py — расширенная панель управления администратора.
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
from keyboards import (
    get_admin_keyboard,
    get_admin_review_kb,
    get_user_manage_kb,
)
from questions import TRACKS
from states import AdminStates
from storage import (
    add_admin_permanently,
    adjust_user_bonuses,
    create_campaign,
    create_promocode,
    delete_review,
    find_user_by_query,
    get_all_campaigns,
    get_all_promocodes,
    get_all_reviews,
    get_pending_reviews,
    get_saved_admins,
    get_user,
    remove_admin_permanently,
    reset_user,
    set_review_status,
    set_user_paid_status,
)

REVIEWS_DATA_FILE = "data/reviews.json"
CAMPAIGNS_DATA_FILE = "data/campaigns.json"
ADMINS_DATA_FILE = "data/admins.json"
PROMOCODES_DATA_FILE = "data/promocodes.json"

logger = logging.getLogger(__name__)
router = Router(name="admin")


# =========================================================
# ФИЛЬТР АДМИНИСТРАТОРА (Файл + Config)
# =========================================================

async def get_active_admin_ids() -> set[int]:
    saved = await get_saved_admins()
    return set(ADMIN_IDS).union(saved)


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        if not user:
            return False
        active_admins = await get_active_admin_ids()
        return user.id in active_admins


# =========================================================
# АВТОРИЗАЦИЯ ПО ПАРОЛЮ С СОХРАНЕНИЕМ НАВСЕГДА
# =========================================================

@router.message(Command("auth"))
async def cmd_auth(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1:
        if parts[1].strip() == ADMIN_SECRET_KEY:
            await add_admin_permanently(message.from_user.id)
            await state.clear()
            await message.answer(
                "✅ <b>Доступ администратора навсегда закреплён за вашим аккаунтом!</b>\n\n"
                "Откройте панель управления: /admin",
                parse_mode="HTML",
            )
            return
        await message.answer("❌ Неверный пароль администратора.")
        return

    await state.set_state(AdminStates.waiting_for_auth_key)
    await message.answer("🔑 Введите секретный пароль администратора:")


@router.message(AdminStates.waiting_for_auth_key)
async def process_auth_key(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    if (message.text or "").strip() == ADMIN_SECRET_KEY:
        await add_admin_permanently(message.from_user.id)
        await state.clear()
        await message.answer(
            "✅ <b>Доступ администратора навсегда закреплён за вашим аккаунтом!</b>\n\n"
            "Откройте панель управления: /admin",
            parse_mode="HTML",
        )
    else:
        await message.answer("❌ Неверный пароль. Для отмены: /cancel")


# =========================================================
# ГЛАВНОЕ МЕНЮ АДМИНКИ
# =========================================================

@router.message(Command("admin"), IsAdmin())
async def cmd_admin(message: Message) -> None:
    await message.answer(
        "🛠 <b>Центр управления IT-тренажёром</b>\n\n"
        "Выберите раздел для работы:",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin_menu", IsAdmin())
async def cb_admin_menu(callback: CallbackQuery) -> None:
    if not callback.message:
        return
    await callback.message.edit_text(
        "🛠 <b>Центр управления IT-тренажёром</b>\n\n"
        "Выберите раздел для работы:",
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
# 1. ЦЕНТР МОДЕРАЦИИ ОТЗЫВОВ
# =========================================================

@router.callback_query(F.data == "admin_reviews_hub", IsAdmin())
async def cb_reviews_hub(callback: CallbackQuery):
    if not callback.message:
        return
    await callback.answer()

    pending = await get_pending_reviews()
    all_revs = await get_all_reviews()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⏳ Ожидают проверки ({len(pending)})", callback_data="adm_rev_view_pending")],
            [InlineKeyboardButton(text=f"📋 Все отзывы ({len(all_revs)})", callback_data="adm_rev_view_all")],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="admin_menu")],
        ]
    )

    await callback.message.edit_text(
        f"⭐️ <b>Управление отзывами кандидатов</b>\n\n"
        f"• Ожидают модерации: <b>{len(pending)}</b>\n"
        f"• Всего отзывов в базе: <b>{len(all_revs)}</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "adm_rev_view_pending", IsAdmin())
async def cb_view_pending_reviews(callback: CallbackQuery):
    if not callback.message:
        return
    await callback.answer()

    pending = await get_pending_reviews()
    if not pending:
        await callback.message.answer("✅ <b>Все отзывы проверены!</b> Новых заявок нет.", parse_mode="HTML")
        return

    await callback.message.answer(f"📋 <b>Список отзывов на модерации ({len(pending)}):</b>", parse_mode="HTML")
    for r in pending:
        stars_str = "⭐️" * int(r.get("rating", 5))
        card = (
            f"📬 <b>Отзыв #{r['id']}</b>\n"
            f"👤 {r.get('full_name')} (@{r.get('username') or 'нет'})\n"
            f"🆔 <code>{r.get('user_id')}</code> | {stars_str}\n"
            f"📅 <code>{r.get('created_at')}</code>\n\n"
            f"💬 <i>«{r.get('text')}»</i>"
        )
        await callback.message.answer(card, reply_markup=get_admin_review_kb(r["id"]), parse_mode="HTML")


@router.callback_query(F.data.startswith("adm_rev_ok:"), IsAdmin())
async def cb_rev_ok(callback: CallbackQuery):
    rev_id = int(callback.data.replace("adm_rev_ok:", ""))
    await set_review_status(rev_id, True)
    await callback.answer("Одобрено!")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(f"✅ Отзыв #{rev_id} опубликован в общем списке!")


@router.callback_query(F.data.startswith("adm_rev_no:"), IsAdmin())
async def cb_rev_no(callback: CallbackQuery):
    rev_id = int(callback.data.replace("adm_rev_no:", ""))
    await set_review_status(rev_id, False)
    await callback.answer("Отклонено")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(f"❌ Отзыв #{rev_id} скрыт.")


@router.callback_query(F.data.startswith("adm_rev_del:"), IsAdmin())
async def cb_rev_del(callback: CallbackQuery):
    rev_id = int(callback.data.replace("adm_rev_del:", ""))
    await delete_review(rev_id)
    await callback.answer("Удалено из базы")
    await callback.message.delete()


# =========================================================
# 2. ПОИСК И CRM КАНДИДАТОВ
# =========================================================

@router.callback_query(F.data == "admin_find_user", IsAdmin())
async def cb_find_user_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminStates.waiting_user_search)
    await callback.message.answer(
        "🔍 <b>Поиск кандидата</b>\n\n"
        "Введите <b>Telegram ID</b> (например: <code>123456789</code>) или <b>Username</b> (например: <code>@nickname</code>):",
        parse_mode="HTML",
    )


@router.message(AdminStates.waiting_user_search, IsAdmin())
async def process_user_search(message: Message, state: FSMContext):
    await state.clear()
    query = (message.text or "").strip()
    result = await find_user_by_query(query)

    if not result:
        await message.answer(f"❌ Пользователь по запросу <code>{query}</code> не найден в базе данных.", parse_mode="HTML")
        return

    uid, data = result
    access_status = "👑 Полный доступ ко всем направлениям" if data.get("paid") else "🔒 Стандартный доступ"
    answers_count = len(data.get("answers", []))

    paid_tracks = data.get("paid_tracks", [])
    if data.get("paid"):
        paid_tracks_str = "все (полный доступ)"
    elif paid_tracks:
        paid_tracks_str = ", ".join(TRACKS.get(t, t) for t in paid_tracks)
    else:
        paid_tracks_str = "нет оплаченных направлений"

    info = (
        f"👤 <b>Карточка кандидата</b>\n\n"
        f"├ <b>Имя:</b> {data.get('full_name') or 'Не указано'}\n"
        f"├ <b>Username:</b> @{data.get('username') or 'нет'}\n"
        f"├ <b>ID:</b> <code>{uid}</code>\n"
        f"├ <b>Статус:</b> <b>{access_status}</b>\n"
        f"├ <b>Оплаченные направления:</b> {paid_tracks_str}\n"
        f"├ <b>Текущее направление:</b> <code>{data.get('track') or 'Не выбрано'}</code>\n"
        f"├ <b>Вопрос:</b> {data.get('current_question_index', 0)} / 15 (ответов: {answers_count})\n"
        f"├ <b>Бонусы:</b> <b>{data.get('bonus_balance', 0)}</b> (рефералов: {data.get('referrals_count', 0)})\n"
        f"├ <b>Канал входа:</b> <code>{data.get('campaign') or 'Органический'}</code>\n"
        f"└ <b>Регистрация:</b> <code>{data.get('registered_at', 'Неизвестно')}</code>"
    )

    await message.answer(info, reply_markup=get_user_manage_kb(uid, data.get("paid", False)), parse_mode="HTML")


@router.callback_query(F.data.startswith("adm_u_grant:"), IsAdmin())
async def cb_user_grant(callback: CallbackQuery):
    uid = int(callback.data.split(":")[1])
    await set_user_paid_status(uid, True)
    await callback.answer("Доступ выдан!")
    await callback.message.reply(f"👑 Пользователю <code>{uid}</code> выдан полный доступ ко всем направлениям!", parse_mode="HTML")


@router.callback_query(F.data.startswith("adm_u_revoke:"), IsAdmin())
async def cb_user_revoke(callback: CallbackQuery):
    uid = int(callback.data.split(":")[1])
    await set_user_paid_status(uid, False)
    await callback.answer("Доступ отозван")
    await callback.message.reply(f"🔒 Полный доступ у пользователя <code>{uid}</code> отозван (ранее оплаченные направления сохранены).", parse_mode="HTML")


@router.callback_query(F.data.startswith("adm_u_addb:"), IsAdmin())
async def cb_user_add_bonuses(callback: CallbackQuery):
    parts = callback.data.split(":")
    uid = int(parts[1])
    amount = int(parts[2])
    new_bal = await adjust_user_bonuses(uid, amount)
    await callback.answer(f"Баланс: {new_bal}")
    await callback.message.reply(f"🎁 Баланс пользователя <code>{uid}</code> изменён на {amount}. Новый баланс: <b>{new_bal}</b>", parse_mode="HTML")


@router.callback_query(F.data.startswith("adm_u_reset:"), IsAdmin())
async def cb_user_reset(callback: CallbackQuery):
    uid = int(callback.data.split(":")[1])
    await reset_user(uid)
    await callback.answer("Прогресс сброшен")
    await callback.message.reply(f"🔄 Прогресс интервью пользователя <code>{uid}</code> сброшен (оплаченные направления сохранены).", parse_mode="HTML")


# =========================================================
# 3. АНАЛИТИКА И ФИНАНСЫ
# =========================================================

@router.callback_query(F.data == "admin_stats", IsAdmin())
async def cb_admin_stats(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.message:
        return
    await callback.answer()

    users_count = 0
    paid_users_count = 0
    if os.path.exists(USERS_DATA_FILE):
        try:
            with open(USERS_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    users_count = len(data)
                    paid_users_count = sum(
                        1 for u in data.values()
                        if isinstance(u, dict) and (u.get("paid") or u.get("paid_tracks"))
                    )
        except Exception as e:
            logger.error("Ошибка чтения USERS: %s", e)

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
            logger.error("Ошибка чтения PAYMENTS: %s", e)

    real_balance = None
    try:
        stars_data = await bot.get_my_star_balance()
        real_balance = getattr(stars_data, "amount", stars_data)
    except Exception:
        pass

    display_stars = real_balance if real_balance is not None else logged_stars
    conversion = (paid_users_count / users_count * 100) if users_count > 0 else 0

    text = (
        "📊 <b>Финансовая и продуктовая аналитика</b>\n\n"
        f"👥 Всего кандидатов: <b>{users_count}</b>\n"
        f"👑 Оплативших хотя бы одно направление: <b>{paid_users_count}</b>\n"
        f"📈 Конверсия в оплату: <b>{conversion:.1f}%</b>\n\n"
        f"💳 Всего успешных транзакций: <b>{payments_count}</b>\n"
        f"💰 Выручка ЮKassa (СБП): <b>{logged_rub} ₽</b>\n"
        f"⭐️ Баланс Telegram Stars: <b>{display_stars} XTR</b>"
    )

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад в меню", callback_data="admin_menu")]]
    )
    await callback.message.edit_text(text, reply_markup=back_kb, parse_mode="HTML")


# =========================================================
# 4. РЕКЛАМНЫЕ ССЫЛКИ ДЛЯ КАНАЛОВ (UTM)
# =========================================================

@router.callback_query(F.data == "admin_campaigns", IsAdmin())
async def cb_campaigns(callback: CallbackQuery, bot: Bot):
    if not callback.message:
        return
    await callback.answer()

    camps = await get_all_campaigns()
    bot_me = await bot.get_me()

    text = "🔗 <b>Аналитика каналов и UTM-меток:</b>\n\n"
    if not camps:
        text += "<i>Пока нет созданных рекламных кампаний. Создайте первую!</i>"
    else:
        for tag, c in camps.items():
            link = f"https://t.me/{bot_me.username}?start=c_{tag}"
            joins = c.get("joins", 0)
            pays = c.get("payments", 0)
            conv = (pays / joins * 100) if joins > 0 else 0
            text += (
                f"📌 <b>{c.get('description') or tag}</b> (<code>{tag}</code>)\n"
                f"├ Ссылка: <code>{link}</code>\n"
                f"├ Переходов: <b>{joins}</b> чел.\n"
                f"├ Оплат: <b>{pays}</b>\n"
                f"└ CR: <b>{conv:.1f}%</b>\n\n"
            )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать ссылку для канала", callback_data="admin_create_camp")],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="admin_menu")],
        ]
    )
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


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
    await message.answer("Теперь введите описание (например: <i>Пост в канале @proger</i>):", parse_mode="HTML")


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
        f"✅ <b>Рекламная ссылка готова:</b>\n\n"
        f"• Описание: {desc}\n"
        f"• Ссылка: <code>{link}</code>",
        parse_mode="HTML",
    )


# =========================================================
# 5. ПРОМОКОДЫ И СКИДКИ
# =========================================================

@router.callback_query(F.data == "admin_promos_hub", IsAdmin())
async def cb_promos_hub(callback: CallbackQuery):
    if not callback.message:
        return
    await callback.answer()

    promos = await get_all_promocodes()
    text = "🎟 <b>Активные промокоды:</b>\n\n"
    if not promos:
        text += "<i>Промокодов пока нет. Создайте первый!</i>"
    else:
        for code, p in promos.items():
            text += f"• <code>{code}</code>: скидка {p['discount_percent']}%, активаций: {p['used_count']}/{p['max_uses']}\n"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin_create_promo")],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="admin_menu")],
        ]
    )
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "admin_create_promo", IsAdmin())
async def cb_create_promo_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminStates.waiting_promo_code)
    await callback.message.answer("Введите кодовое слово промокода (например: <code>START2026</code>):", parse_mode="HTML")


@router.message(AdminStates.waiting_promo_code, IsAdmin())
async def process_promo_code(message: Message, state: FSMContext):
    code = (message.text or "").strip().upper()
    await state.update_data(code=code)
    await state.set_state(AdminStates.waiting_promo_discount)
    await message.answer("Введите скидку в % (от 1 до 100, где 100 — бесплатный доступ):", parse_mode="HTML")


@router.message(AdminStates.waiting_promo_discount, IsAdmin())
async def process_promo_discount(message: Message, state: FSMContext):
    try:
        disc = int(message.text.strip())
    except ValueError:
        await message.answer("Введите целое число процентов.")
        return

    data = await state.get_data()
    code = data.get("code")
    await create_promocode(code, discount_percent=disc, max_uses=50)
    await state.clear()
    await message.answer(f"✅ Промокод <code>{code}</code> со скидкой {disc}% успешно создан!", parse_mode="HTML")


# =========================================================
# 6. УПРАВЛЕНИЕ АДМИНИСТРАТОРАМИ
# =========================================================

@router.callback_query(F.data == "admin_team_hub", IsAdmin())
async def cb_admin_team_hub(callback: CallbackQuery):
    if not callback.message:
        return
    await callback.answer()

    admins = await get_active_admin_ids()
    saved_admins = set(await get_saved_admins())
    text = "👥 <b>Список постоянных администраторов бота:</b>\n\n"
    if not admins:
        text += "<i>Список пуст.</i>\n"
    for aid in admins:
        source = "" if aid in saved_admins else " (задан в .env, снять можно только там)"
        text += f"• ID: <code>{aid}</code>{source}\n"

    rows = [
        [InlineKeyboardButton(text=f"🗑 Снять {aid}", callback_data=f"adm_team_remove:{aid}")]
        for aid in saved_admins
    ]
    rows.append([InlineKeyboardButton(text="➕ Назначить админа", callback_data="admin_add_team_member")])
    rows.append([InlineKeyboardButton(text="◀️ Назад в меню", callback_data="admin_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "admin_add_team_member", IsAdmin())
async def cb_add_admin_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminStates.waiting_add_admin_id)
    await callback.message.answer("Введите Telegram ID пользователя, которому нужно выдать вечный доступ к /admin:")


@router.message(AdminStates.waiting_add_admin_id, IsAdmin())
async def process_add_admin_id(message: Message, state: FSMContext):
    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("Введите корректный числовой Telegram ID.")
        return

    await add_admin_permanently(target_id)
    await state.clear()
    await message.answer(f"✅ Пользователь <code>{target_id}</code> навсегда добавлен в администраторы бота!", parse_mode="HTML")


# =========================================================
# ВЫГРУЗКА БАЗ (JSON)
# =========================================================

@router.callback_query(F.data == "admin_export_db", IsAdmin())
async def cb_export_db(callback: CallbackQuery) -> None:
    if not callback.message:
        return
    sent_any = False
    files = [USERS_DATA_FILE, PAYMENTS_LOG_FILE, REVIEWS_DATA_FILE, CAMPAIGNS_DATA_FILE, ADMINS_DATA_FILE, PROMOCODES_DATA_FILE]
    for filepath in files:
        if os.path.exists(filepath):
            await callback.message.answer_document(FSInputFile(filepath))
            sent_any = True
    if not sent_any:
        await callback.message.answer("⚠️ Файлы данных пока не созданы.")
    await callback.answer("Выгрузка завершена")


# =========================================================
# РАССЫЛКА И ТЕХПОДДЕРЖКА
# =========================================================

@router.callback_query(F.data == "admin_broadcast", IsAdmin())
async def cb_start_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        return
    await state.set_state(AdminStates.waiting_for_broadcast_msg)
    await callback.message.answer("📝 Отправьте сообщение для рассылки всем кандидатам. Для отмены: /cancel")
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
            logger.error("Ошибка пользователей: %s", e)

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
        f"✅ <b>Рассылка завершена!</b>\n\n• Доставлено: <code>{success}</code>\n• Заблокировали: <code>{blocked}</code>",
        parse_mode="HTML",
    )


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
        f"✍️ <b>Ответ кандидату (ID: <code>{target_user_id}</code>)</b>\n\nНапишите текст ответа. Для отмены: /cancel",
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


# =========================================================
# 7. УДАЛЕНИЕ АДМИНИСТРАТОРА
#
# Раньше storage.remove_admin_permanently импортировалась, но нигде не вызывалась —
# убрать администратора можно было только вручную правкой data/admins.json.
# =========================================================

@router.callback_query(F.data.startswith("adm_team_remove:"), IsAdmin())
async def cb_remove_admin_confirmed(callback: CallbackQuery) -> None:
    target_id = int(callback.data.replace("adm_team_remove:", ""))
    removed = await remove_admin_permanently(target_id)
    if removed:
        await callback.answer("Администратор удалён")
        await callback.message.reply(f"🗑 Пользователь <code>{target_id}</code> больше не администратор.", parse_mode="HTML")
    else:
        await callback.answer("Этот ID не найден в списке администраторов (возможно, он задан через ADMIN_IDS в .env).", show_alert=True)
