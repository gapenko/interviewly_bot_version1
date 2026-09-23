"""
handlers/admin.py — панель администратора.

Все разделы работают в режиме «одного экрана» и имеют кнопку возврата:
- 📊 Статистика: пользователи, конверсия, выручка без тестовых платежей, популярные направления;
- 💰 Платежи: последние операции;
- 👥 Пользователи: поиск, последние регистрации, заблокированные; карточка пользователя
  с управлением доступом (полный / по направлениям), бонусами, прогрессом, блокировкой и сообщением;
- ⭐️ Отзывы: модерация по статусам (на модерации / опубликованные / отклонённые) с листанием;
- 🎟 Промокоды и 🔗 UTM-ссылки: создание, список, удаление;
- 📢 Рассылка: с предпросмотром и подтверждением, прогрессом и учётом заблокировавших бота;
- 👮 Администраторы: добавление и снятие;
- 💾 Выгрузка данных;
- 🧹 Сброс статистики: очистка журнала платежей и счётчиков UTM (с архивом в data/archive/).
"""
import asyncio
import hmac
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

import storage
from config import ADMIN_IDS, ADMIN_SECRET_KEY, AUTH_LOCK_MINUTES, AUTH_MAX_ATTEMPTS, PAYMENTS_LOG_FILE, USERS_DATA_FILE
from keyboards import admin_back_btn, back_menu_kb, btn, dismiss_kb, ikb
from menus import get_bot_username, track_title
from questions import TOTAL_QUESTIONS, TRACKS
from screen import delete_message, delete_user_message, esc, not_command, notify, show_screen, truncate_plain
from states import AdminStates

logger = logging.getLogger(__name__)
router = Router(name="admin")

STATUS_LABELS = {"pending": "⏳ на модерации", "approved": "✅ опубликован", "rejected": "🚫 отклонён"}
PROMO_RE = re.compile(r"^[A-Z0-9_-]{3,20}$")
UTM_RE = re.compile(r"^[a-z0-9_-]{2,32}$")


class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user) and await storage.is_admin(user.id)


async def get_active_admin_ids() -> set[int]:
    """Совместимость: раньше функция жила здесь."""
    return await storage.get_active_admin_ids()


def _cid(callback: CallbackQuery) -> int:
    return callback.message.chat.id if callback.message else callback.from_user.id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user_label(uid, u: dict) -> str:
    name = (u.get("full_name") or "").strip()
    uname = u.get("username")
    if name and uname:
        return f"{name} (@{uname})"
    return name or (f"@{uname}" if uname else str(uid))


def _is_real_payment(p: dict) -> bool:
    return not p.get("test") and p.get("method") != "free" and storage.payment_amount(p) > 0


def _fmt_ts(ts: Optional[str]) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%d.%m %H:%M")
    except (TypeError, ValueError):
        return "—"


# =========================================================
# ВХОД ПО ПАРОЛЮ (/auth) — с защитой от подбора
# =========================================================

_auth_failures: dict[int, list[float]] = {}


def _auth_locked_for(uid: int) -> int:
    """Сколько минут осталось до разблокировки /auth (0 — не заблокирован)."""
    window = AUTH_LOCK_MINUTES * 60
    attempts = [t for t in _auth_failures.get(uid, []) if time.time() - t < window]
    _auth_failures[uid] = attempts
    if len(attempts) >= AUTH_MAX_ATTEMPTS:
        return max(1, int((window - (time.time() - attempts[0])) // 60) + 1)
    return 0


async def _try_password(message: Message, state: FSMContext, bot: Bot, password: str) -> None:
    uid = message.from_user.id
    if hmac.compare_digest(password.strip().encode(), ADMIN_SECRET_KEY.encode()):
        _auth_failures.pop(uid, None)
        await storage.add_admin_permanently(uid)
        await state.clear()
        await show_admin_menu(bot, message.chat.id, state, notice="Доступ администратора выдан.")
        return
    _auth_failures.setdefault(uid, []).append(time.time())
    locked = _auth_locked_for(uid)
    if locked:
        await state.clear()
        await show_screen(bot, message.chat.id,
                          f"🔑 Слишком много неверных попыток. Попробуйте через {locked} мин.", back_menu_kb())
        return
    left = AUTH_MAX_ATTEMPTS - len(_auth_failures.get(uid, []))
    await state.set_state(AdminStates.waiting_for_auth_key)
    await show_screen(bot, message.chat.id,
                      f"🔑 <b>Вход в админ-панель</b>\n\n<i>Неверный пароль. Осталось попыток: {left}.</i>",
                      ikb([btn("✖️ Отмена", "nav_menu")]))


@router.message(Command("auth"))
async def cmd_auth(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)  # в сообщении может быть пароль
    uid = message.from_user.id
    if await storage.is_admin(uid):
        await show_admin_menu(bot, message.chat.id, state, force_new=True)
        return
    if not ADMIN_SECRET_KEY:
        await show_screen(bot, message.chat.id, "🔑 Вход по паролю отключён.", back_menu_kb(), force_new=True)
        return
    locked = _auth_locked_for(uid)
    if locked:
        await show_screen(bot, message.chat.id,
                          f"🔑 Слишком много неверных попыток. Попробуйте через {locked} мин.", back_menu_kb(), force_new=True)
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1:
        await _try_password(message, state, bot, parts[1])
        return
    await state.set_state(AdminStates.waiting_for_auth_key)
    await show_screen(bot, message.chat.id, "🔑 <b>Вход в админ-панель</b>\n\nВведите пароль администратора.",
                      ikb([btn("✖️ Отмена", "nav_menu")]), force_new=True)


@router.message(AdminStates.waiting_for_auth_key, F.text, not_command)
async def process_auth_key(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    if not ADMIN_SECRET_KEY or _auth_locked_for(message.from_user.id):
        await state.clear()
        await show_screen(bot, message.chat.id, "🔑 Вход сейчас недоступен. Попробуйте позже.", back_menu_kb())
        return
    await _try_password(message, state, bot, message.text)


# =========================================================
# ГЛАВНОЕ МЕНЮ АДМИНКИ
# =========================================================

async def admin_menu_view(notice: Optional[str] = None):
    users = await storage.get_all_users()
    payments = await storage.get_payments()
    pending = len(await storage.get_reviews("pending"))
    today = _now().date()
    new_today = sum(1 for u in users.values() if (d := storage.parse_registered_at(u)) and d.date() == today)
    pays_today = 0
    for p in payments:
        try:
            if _is_real_payment(p) and datetime.fromisoformat(p.get("timestamp", "")).date() == today:
                pays_today += 1
        except ValueError:
            pass

    parts = ["🛠 <b>Панель администратора</b>"]
    if notice:
        parts.append(f"<i>{esc(notice)}</i>")
    parts.append(
        f"👥 Пользователей: <b>{len(users)}</b> (сегодня +{new_today})\n"
        f"💳 Оплат сегодня: <b>{pays_today}</b>\n"
        f"⭐️ Отзывов на модерации: <b>{pending}</b>"
    )
    parts.append("Выберите раздел:")
    kb = ikb(
        [btn("📊 Статистика", "adm_stats"), btn("💰 Платежи", "adm_pay")],
        [btn("👥 Пользователи", "adm_users"), btn(f"⭐️ Отзывы ({pending})" if pending else "⭐️ Отзывы", "adm_revs")],
        [btn("🎟 Промокоды", "adm_promos"), btn("🔗 UTM-ссылки", "adm_utm")],
        [btn("📢 Рассылка", "adm_bc"), btn("👮 Администраторы", "adm_admins")],
        [btn("💾 Выгрузка данных", "adm_export"), btn("🧹 Сброс статистики", "adm_reset")],
        [btn("🏠 Выйти в главное меню", "nav_menu")],
    )
    return "\n\n".join(parts), kb


async def show_admin_menu(bot: Bot, chat_id: int, state: FSMContext, *, source=None,
                          force_new: bool = False, notice: Optional[str] = None) -> None:
    await state.clear()
    text, kb = await admin_menu_view(notice)
    await show_screen(bot, chat_id, text, kb, source=source, force_new=force_new)


@router.message(Command("admin"), IsAdmin())
async def cmd_admin(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    await show_admin_menu(bot, message.chat.id, state, force_new=True)


@router.callback_query(F.data.in_({"adm_menu", "admin_menu"}), IsAdmin())
async def cb_admin_menu(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await show_admin_menu(bot, _cid(callback), state, source=callback.message)


# =========================================================
# СТАТИСТИКА И ПЛАТЕЖИ
# =========================================================

@router.callback_query(F.data == "adm_stats", IsAdmin())
async def cb_stats(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer("Обновлено" if "Статистика" in (getattr(callback.message, "text", "") or "") else None)
    users = await storage.get_all_users()
    payments = await storage.get_payments()
    now = _now()

    total = len(users)
    reg_dates = [storage.parse_registered_at(u) for u in users.values()]
    new_today = sum(1 for d in reg_dates if d and d.date() == now.date())
    new_week = sum(1 for d in reg_dates if d and d >= now - timedelta(days=7))
    in_progress = sum(1 for u in users.values() if u.get("track") and not u.get("finished"))
    finished = sum(1 for u in users.values() if u.get("last_result") or u.get("finished"))
    bot_blocked = sum(1 for u in users.values() if u.get("bot_blocked"))
    admin_blocked = sum(1 for u in users.values() if u.get("is_blocked"))
    paying = sum(1 for u in users.values() if u.get("paid_tracks"))
    full_access = sum(1 for u in users.values() if u.get("paid"))
    conversion = paying / total * 100 if total else 0

    real = [p for p in payments if _is_real_payment(p)]
    rub = [p for p in real if p.get("currency") == "RUB"]
    stars = [p for p in real if p.get("currency") == "XTR"]
    free = [p for p in payments if p.get("method") == "free"]
    tests = [p for p in payments if p.get("test")]

    star_balance = None
    try:
        balance = await bot.get_my_star_balance()
        star_balance = getattr(balance, "amount", None)
    except Exception:
        pass

    tracks = Counter()
    for u in users.values():
        started = {u.get("track"), (u.get("last_result") or {}).get("track")} - {None}
        tracks.update(started)
    top = "\n".join(
        f"{i}. {esc(track_title(t))} — {n}" for i, (t, n) in enumerate(tracks.most_common(5), start=1)
    ) or "—"

    text = (
        "📊 <b>Статистика</b>\n\n"
        "<b>Пользователи</b>\n"
        f"• Всего: {total} · новых сегодня: {new_today} · за 7 дней: {new_week}\n"
        f"• Сейчас проходят собеседование: {in_progress}\n"
        f"• Завершили хотя бы одно: {finished}\n"
        f"• Заблокировали бота: {bot_blocked} · заблокированы админом: {admin_blocked}\n\n"
        "<b>Доступ</b>\n"
        f"• Оплатили хотя бы одно направление: {paying} (конверсия {conversion:.1f}%)\n"
        f"• Полный доступ (бонусы / выдан админом): {full_access}\n\n"
        "<b>Выручка</b> <i>(без тестовых платежей)</i>\n"
        f"• ЮKassa: {sum(storage.payment_amount(p) for p in rub)} ₽ ({len(rub)} опл.)\n"
        f"• Telegram Stars: {sum(storage.payment_amount(p) for p in stars)} ⭐️ ({len(stars)} опл.)\n"
        + (f"• Баланс Stars в Telegram: {star_balance} ⭐️\n" if star_balance is not None else "")
        + f"• Бесплатные активации (промокод/бонусы): {len(free)}\n"
        + (f"• Тестовых платежей в журнале: {len(tests)}\n" if tests else "")
        + "\n<b>Популярные направления</b>\n"
        + top
    )
    await show_screen(bot, _cid(callback), text,
                      ikb([btn("🔄 Обновить", "adm_stats")], [admin_back_btn()]), source=callback.message)


@router.callback_query(F.data == "adm_pay", IsAdmin())
async def cb_payments(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    payments = await storage.get_payments()
    method_names = {"yookassa": "ЮKassa", "stars": "Stars", "free": "бесплатно"}
    lines = ["💰 <b>Платежи</b>"]
    if not payments:
        lines.append("Журнал платежей пуст.")
    else:
        recent = sorted(payments, key=lambda p: p.get("timestamp", ""), reverse=True)[:15]
        rows = []
        for p in recent:
            currency = "₽" if p.get("currency") == "RUB" else "⭐️"
            method = method_names.get(p.get("method"), "ЮKassa" if str(p.get("invoice_payload", "")).startswith("yookassa") else "—")
            who = f"@{p['username']}" if p.get("username") else str(p.get("telegram_id", "—"))
            track = f" · {track_title(p['track'])}" if p.get("track") else ""
            test = " · <i>тест</i>" if p.get("test") else ""
            rows.append(f"{_fmt_ts(p.get('timestamp'))} · <b>{storage.payment_amount(p)} {currency}</b> · {method} · {esc(who)}{esc(track)}{test}")
        lines.append("\n".join(rows))
        lines.append(f"<i>Показаны последние {len(recent)} из {len(payments)}.</i>")
    kb = ikb([btn("🧹 Очистить журнал платежей", "adm_reset_ask:payments")], [admin_back_btn()])
    await show_screen(bot, _cid(callback), "\n\n".join(lines), kb, source=callback.message)


# =========================================================
# ПОЛЬЗОВАТЕЛИ
# =========================================================

@router.callback_query(F.data == "adm_users", IsAdmin())
async def cb_users_hub(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.clear()
    users = await storage.get_all_users()
    blocked = sum(1 for u in users.values() if u.get("is_blocked"))
    kb = ikb(
        [btn("🔍 Найти пользователя", "adm_find")],
        [btn("🆕 Последние регистрации", "adm_recent")],
        [btn(f"🚫 Заблокированные ({blocked})", "adm_blocked")],
        [admin_back_btn()],
    )
    await show_screen(bot, _cid(callback),
                      f"👥 <b>Пользователи</b>\n\nВсего: {len(users)}. Найдите пользователя по ID или @username "
                      "либо откройте список.", kb, source=callback.message)


@router.callback_query(F.data.in_({"adm_find", "admin_find_user"}), IsAdmin())
async def cb_find_user(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.set_state(AdminStates.waiting_user_search)
    await show_screen(bot, _cid(callback),
                      "🔍 <b>Поиск пользователя</b>\n\nОтправьте Telegram ID (например, <code>123456789</code>) "
                      "или username (например, <code>@nickname</code>).",
                      ikb([btn("◀️ Назад", "adm_users")]), source=callback.message)


@router.message(AdminStates.waiting_user_search, IsAdmin(), F.text, not_command)
async def process_user_search(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    query = message.text.strip()
    result = await storage.find_user_by_query(query)
    if not result:
        await show_screen(bot, message.chat.id,
                          f"🔍 <b>Поиск пользователя</b>\n\n<i>Пользователь «{esc(query)}» не найден.</i> "
                          "Попробуйте другой ID или username.",
                          ikb([btn("◀️ Назад", "adm_users")]))
        return
    await state.clear()
    uid, _ = result
    await _show_user_card(bot, message.chat.id, uid)


async def _user_list_screen(callback: CallbackQuery, bot: Bot, title: str, items: list[tuple[str, dict]]) -> None:
    rows = [[btn(truncate_plain(_user_label(uid, u), 40), f"adm_u:{uid}")] for uid, u in items[:15]]
    rows.append([btn("◀️ Назад", "adm_users")])
    text = f"{title}\n\n" + ("Выберите пользователя:" if items else "Список пуст.")
    await show_screen(bot, _cid(callback), text, ikb(*rows), source=callback.message)


@router.callback_query(F.data == "adm_recent", IsAdmin())
async def cb_recent_users(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    users = await storage.get_all_users()
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    items = sorted(users.items(), key=lambda kv: storage.parse_registered_at(kv[1]) or epoch, reverse=True)
    await _user_list_screen(callback, bot, "🆕 <b>Последние регистрации</b>", items)


@router.callback_query(F.data == "adm_blocked", IsAdmin())
async def cb_blocked_users(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    users = await storage.get_all_users()
    items = [(uid, u) for uid, u in users.items() if u.get("is_blocked")]
    await _user_list_screen(callback, bot, "🚫 <b>Заблокированные пользователи</b>", items)


async def _user_card_view(uid: int):
    u = await storage.peek_user(uid)
    if not u:
        return (f"Пользователь <code>{uid}</code> не найден.", ikb([btn("◀️ К пользователям", "adm_users")]))

    if u.get("paid"):
        access = "👑 полный доступ ко всем направлениям"
    elif u.get("paid_tracks"):
        access = ", ".join(track_title(t) for t in u["paid_tracks"])
    else:
        access = "нет"
    if u.get("track") and not u.get("finished"):
        idx = min(u.get("current_question_index", 0) + 1, TOTAL_QUESTIONS)
        current = f"{track_title(u['track'])} — вопрос {idx}/{TOTAL_QUESTIONS} (ответов: {len(u.get('answers', []))})"
    else:
        current = "нет"
    last = u.get("last_result") or {}
    last_str = f"{last.get('track_title') or track_title(last.get('track'))}, {last.get('finished_at', '')}" if last else "нет"
    payments = [p for p in await storage.get_payments() if str(p.get("telegram_id")) == str(uid)]
    status = "🚫 заблокирован" if u.get("is_blocked") else "✅ активен"
    if u.get("bot_blocked"):
        status += " · заблокировал бота"
    if await storage.is_admin(uid):
        status += " · 👮 администратор"

    text = (
        "👤 <b>Карточка пользователя</b>\n\n"
        f"Имя: {esc(u.get('full_name') or '—')}\n"
        f"Username: {esc('@' + u['username']) if u.get('username') else '—'}\n"
        f"ID: <code>{uid}</code>\n"
        f"Регистрация: {esc(u.get('registered_at') or '—')} · канал: {esc(u.get('campaign') or 'органика')}\n\n"
        f"Доступ: {esc(access)}\n"
        f"Текущее собеседование: {esc(current)}\n"
        f"Последний результат: {esc(last_str)}\n"
        f"Бонусы: {u.get('bonus_balance', 0)} · приглашено: {u.get('referrals_count', 0)}\n"
        f"Платежей: {len(payments)}\n"
        f"Статус: {status}"
    )
    kb = ikb(
        [btn("🔒 Забрать полный доступ", f"adm_uf:{uid}:0") if u.get("paid")
         else btn("👑 Выдать полный доступ", f"adm_uf:{uid}:1")],
        [btn("🎯 Доступ к направлениям", f"adm_ut:{uid}")],
        [btn("➕ 150 бонусов", f"adm_ub:{uid}:150"), btn("➖ 150 бонусов", f"adm_ub:{uid}:-150")],
        [btn("🔄 Сбросить прогресс", f"adm_ur:{uid}"), btn("✉️ Написать", f"adm_um:{uid}")],
        [btn("✅ Разблокировать", f"adm_ubl:{uid}:0") if u.get("is_blocked")
         else btn("🚫 Заблокировать", f"adm_ubl:{uid}:1")],
        [btn("◀️ К пользователям", "adm_users")],
    )
    return text, kb


async def _show_user_card(bot: Bot, chat_id: int, uid: int, source=None) -> None:
    text, kb = await _user_card_view(uid)
    await show_screen(bot, chat_id, text, kb, source=source)


def _parse_ids(data: str) -> list[str]:
    return data.split(":")[1:]


@router.callback_query(F.data.startswith("adm_u:"), IsAdmin())
async def cb_user_card(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.clear()
    await _show_user_card(bot, _cid(callback), int(_parse_ids(callback.data)[0]), source=callback.message)


@router.callback_query(F.data.startswith("adm_uf:"), IsAdmin())
async def cb_user_full_access(callback: CallbackQuery, bot: Bot) -> None:
    uid, value = _parse_ids(callback.data)
    await storage.set_user_paid_status(int(uid), value == "1")
    await callback.answer("Полный доступ выдан" if value == "1" else "Полный доступ отозван")
    await _show_user_card(bot, _cid(callback), int(uid), source=callback.message)


@router.callback_query(F.data.startswith("adm_ut:"), IsAdmin())
async def cb_user_tracks(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    uid = int(_parse_ids(callback.data)[0])
    await _show_user_tracks(bot, _cid(callback), uid, source=callback.message)


async def _show_user_tracks(bot: Bot, chat_id: int, uid: int, source=None) -> None:
    u = await storage.peek_user(uid) or {}
    paid = set(u.get("paid_tracks", []))
    rows = [[btn(f"{'✅' if key in paid else '▫️'} {name}", f"adm_utt:{uid}:{key}")] for key, name in TRACKS.items()]
    rows.append([btn("◀️ К карточке", f"adm_u:{uid}")])
    note = "\n\n<i>У пользователя полный доступ — он открывает все направления независимо от списка.</i>" if u.get("paid") else ""
    await show_screen(
        bot, chat_id,
        f"🎯 <b>Доступ к направлениям</b> · {esc(_user_label(uid, u))}\n\n"
        f"Нажмите на направление, чтобы открыть или закрыть доступ.{note}",
        ikb(*rows), source=source,
    )


@router.callback_query(F.data.startswith("adm_utt:"), IsAdmin())
async def cb_user_toggle_track(callback: CallbackQuery, bot: Bot) -> None:
    uid_str, track = _parse_ids(callback.data)
    uid = int(uid_str)
    u = await storage.peek_user(uid) or {}
    if track in u.get("paid_tracks", []):
        await storage.revoke_track_access(uid, track)
        await callback.answer(f"Доступ к «{track_title(track)}» закрыт")
    else:
        await storage.grant_track_access(uid, track, count_payment=False)
        await callback.answer(f"Доступ к «{track_title(track)}» открыт")
    await _show_user_tracks(bot, _cid(callback), uid, source=callback.message)


@router.callback_query(F.data.startswith("adm_ub:"), IsAdmin())
async def cb_user_bonuses(callback: CallbackQuery, bot: Bot) -> None:
    uid, amount = _parse_ids(callback.data)
    new_balance = await storage.adjust_user_bonuses(int(uid), int(amount))
    await callback.answer(f"Баланс: {new_balance}")
    await _show_user_card(bot, _cid(callback), int(uid), source=callback.message)


@router.callback_query(F.data.startswith("adm_ur:"), IsAdmin())
async def cb_user_reset(callback: CallbackQuery, bot: Bot) -> None:
    uid = int(_parse_ids(callback.data)[0])
    await storage.reset_user(uid)
    await callback.answer("Прогресс собеседования сброшен")
    await _show_user_card(bot, _cid(callback), uid, source=callback.message)


@router.callback_query(F.data.startswith("adm_ubl:"), IsAdmin())
async def cb_user_block(callback: CallbackQuery, bot: Bot) -> None:
    uid, value = _parse_ids(callback.data)
    uid = int(uid)
    if value == "1" and await storage.is_admin(uid):
        await callback.answer("Нельзя заблокировать администратора", show_alert=True)
        return
    await storage.set_user_blocked(uid, value == "1")
    await callback.answer("Пользователь заблокирован" if value == "1" else "Пользователь разблокирован")
    await _show_user_card(bot, _cid(callback), uid, source=callback.message)


# --- Сообщение пользователю (из карточки или ответ на обращение в поддержку) ---

@router.callback_query(F.data.startswith("adm_um:") | F.data.startswith("reply_support:"), IsAdmin())
async def cb_user_message(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    uid = int(callback.data.split(":", 1)[1])
    u = await storage.peek_user(uid) or {}
    await state.set_state(AdminStates.waiting_user_message)
    await state.update_data(target_user_id=uid)
    await show_screen(
        bot, _cid(callback),
        f"✉️ <b>Сообщение пользователю</b> {esc(_user_label(uid, u))}\n\n"
        "Напишите текст — пользователь получит его как сообщение от поддержки.",
        ikb([btn("✖️ Отмена", f"adm_u:{uid}")]), source=callback.message,
    )


@router.message(AdminStates.waiting_user_message, IsAdmin(), F.text, not_command)
async def process_user_message(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    target = (await state.get_data()).get("target_user_id")
    await state.clear()
    if not target:
        await show_admin_menu(bot, message.chat.id, state)
        return
    delivered = await notify(
        bot, target,
        f"📨 <b>Сообщение от поддержки</b>\n\n{esc(message.text)}",
        [[btn("💬 Ответить", "support")]],
    )
    if not delivered:
        await storage.mark_bot_blocked(target)
    text = ("✅ Сообщение доставлено." if delivered
            else "❌ Не удалось доставить сообщение: возможно, пользователь заблокировал бота.")
    await show_screen(bot, message.chat.id, text,
                      ikb([btn("◀️ К карточке пользователя", f"adm_u:{target}")], [admin_back_btn()]))


# =========================================================
# ОТЗЫВЫ
# =========================================================

@router.callback_query(F.data.in_({"adm_revs", "admin_reviews_hub"}), IsAdmin())
async def cb_reviews_hub(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    counts = {s: len(await storage.get_reviews(s)) for s in storage.REVIEW_STATUSES}
    kb = ikb(
        [btn(f"⏳ На модерации ({counts['pending']})", "adm_revl:pending:0")],
        [btn(f"✅ Опубликованные ({counts['approved']})", "adm_revl:approved:0")],
        [btn(f"🚫 Отклонённые ({counts['rejected']})", "adm_revl:rejected:0")],
        [admin_back_btn()],
    )
    await show_screen(bot, _cid(callback),
                      "⭐️ <b>Отзывы</b>\n\nВыберите раздел. Опубликованные отзывы видят все пользователи.",
                      kb, source=callback.message)


async def _review_list_view(status: str, idx: int):
    reviews = await storage.get_reviews(status)
    label = STATUS_LABELS.get(status, status)
    if not reviews:
        return (f"⭐️ <b>Отзывы — {label}</b>\n\nОтзывов нет.",
                ikb([btn("◀️ К разделу отзывов", "adm_revs")], [admin_back_btn()]))
    idx = max(0, min(idx, len(reviews) - 1))
    r = reviews[idx]
    rid = r.get("id")
    stars = "⭐️" * max(1, min(5, int(r.get("rating", 5) or 5)))
    who = esc(r.get("full_name") or "Кандидат")
    if r.get("username"):
        who += f" (@{esc(r['username'])})"
    text = (
        f"⭐️ <b>Отзыв #{rid}</b> · {label}\n\n"
        f"👤 {who}, ID <code>{r.get('user_id')}</code>\n"
        f"Оценка: {stars} · {esc(r.get('created_at', ''))}\n\n"
        f"<i>«{truncate_plain(esc(r.get('text', '')), 3000)}»</i>"
    )
    tail = f"{status}:{idx}"
    actions = []
    if status != "approved":
        actions.append(btn("✅ Опубликовать", f"adm_rva:ok:{rid}:{tail}"))
    if status != "rejected":
        actions.append(btn("🚫 Отклонить" if status == "pending" else "🚫 Снять с публикации", f"adm_rva:no:{rid}:{tail}"))
    nav = []
    if idx > 0:
        nav.append(btn("◀️", f"adm_revl:{status}:{idx - 1}"))
    nav.append(btn(f"{idx + 1} / {len(reviews)}", f"adm_revl:{status}:{idx}"))
    if idx < len(reviews) - 1:
        nav.append(btn("▶️", f"adm_revl:{status}:{idx + 1}"))
    kb = ikb(
        actions,
        [btn("🗑 Удалить", f"adm_rva:del:{rid}:{tail}")],
        nav,
        [btn("◀️ К разделу отзывов", "adm_revs")],
    )
    return text, kb


@router.callback_query(F.data.startswith("adm_revl:"), IsAdmin())
async def cb_review_list(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    status, idx = _parse_ids(callback.data)
    text, kb = await _review_list_view(status, int(idx))
    await show_screen(bot, _cid(callback), text, kb, source=callback.message)


async def _apply_review_action(action: str, rid: int) -> str:
    if action == "ok":
        await storage.set_review_status(rid, "approved")
        return f"Отзыв #{rid} опубликован"
    if action == "no":
        await storage.set_review_status(rid, "rejected")
        return f"Отзыв #{rid} отклонён"
    await storage.delete_review(rid)
    return f"Отзыв #{rid} удалён"


@router.callback_query(F.data.startswith("adm_rva:"), IsAdmin())
async def cb_review_action(callback: CallbackQuery, bot: Bot) -> None:
    action, rid, status, idx = _parse_ids(callback.data)
    await callback.answer(await _apply_review_action(action, int(rid)))
    text, kb = await _review_list_view(status, int(idx))
    await show_screen(bot, _cid(callback), text, kb, source=callback.message)


@router.callback_query(F.data.startswith("adm_rvn:"), IsAdmin())
async def cb_review_notification_action(callback: CallbackQuery) -> None:
    """Кнопки модерации прямо в уведомлении о новом отзыве: действие + уведомление убирается."""
    action, rid = _parse_ids(callback.data)
    if not await storage.get_review(int(rid)):
        await callback.answer("Отзыв уже удалён")
    else:
        await callback.answer(await _apply_review_action(action, int(rid)))
    try:
        await callback.message.delete()
    except Exception:
        pass


# =========================================================
# ПРОМОКОДЫ
# =========================================================

@router.callback_query(F.data.in_({"adm_promos", "admin_promos_hub"}), IsAdmin())
async def cb_promos(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.clear()
    await _show_promos(bot, _cid(callback), source=callback.message)


async def _show_promos(bot: Bot, chat_id: int, source=None, notice: Optional[str] = None) -> None:
    promos = await storage.get_all_promocodes()
    lines = ["🎟 <b>Промокоды</b>"]
    if notice:
        lines.append(f"<i>{esc(notice)}</i>")
    if not promos:
        lines.append("Промокодов пока нет.")
    else:
        lines.append("\n".join(
            f"• <code>{esc(code)}</code> — −{p.get('discount_percent')}%, активаций {p.get('used_count', 0)}/{p.get('max_uses')}"
            for code, p in promos.items()
        ))
        lines.append("<i>Активация засчитывается только после оплаты. Один пользователь — одна активация.</i>")
    rows = [[btn("➕ Создать промокод", "adm_prn")]]
    rows += [[btn(f"🗑 Удалить {code}", f"adm_prd:{code}")] for code in list(promos)[:20]]
    rows.append([admin_back_btn()])
    await show_screen(bot, chat_id, "\n\n".join(lines), ikb(*rows), source=source)


@router.callback_query(F.data.startswith("adm_prd:"), IsAdmin())
async def cb_promo_delete(callback: CallbackQuery, bot: Bot) -> None:
    code = callback.data.split(":", 1)[1]
    await storage.delete_promocode(code)
    await callback.answer(f"Промокод {code} удалён")
    await _show_promos(bot, _cid(callback), source=callback.message)


@router.callback_query(F.data.in_({"adm_prn", "admin_create_promo"}), IsAdmin())
async def cb_promo_new(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.set_state(AdminStates.waiting_promo_code)
    await show_screen(bot, _cid(callback),
                      "🎟 <b>Новый промокод · шаг 1 из 3</b>\n\nОтправьте кодовое слово: латинские буквы, цифры, "
                      "«_» или «-», от 3 до 20 символов (например, <code>START2026</code>).",
                      ikb([btn("✖️ Отмена", "adm_promos")]), source=callback.message)


@router.message(AdminStates.waiting_promo_code, IsAdmin(), F.text, not_command)
async def process_promo_code(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    code = message.text.strip().upper()
    error = None
    if not PROMO_RE.match(code):
        error = "Код может содержать только латинские буквы, цифры, «_» и «-» (3–20 символов)."
    elif await storage.get_promocode(code):
        error = "Такой промокод уже существует."
    if error:
        await show_screen(bot, message.chat.id,
                          f"🎟 <b>Новый промокод · шаг 1 из 3</b>\n\n<i>{esc(error)}</i>\n\nОтправьте другое кодовое слово.",
                          ikb([btn("✖️ Отмена", "adm_promos")]))
        return
    await state.update_data(code=code)
    await state.set_state(AdminStates.waiting_promo_discount)
    await show_screen(bot, message.chat.id,
                      f"🎟 <b>Новый промокод {esc(code)} · шаг 2 из 3</b>\n\nОтправьте размер скидки в процентах "
                      "(от 1 до 100; 100 — бесплатный доступ).",
                      ikb([btn("✖️ Отмена", "adm_promos")]))


@router.message(AdminStates.waiting_promo_discount, IsAdmin(), F.text, not_command)
async def process_promo_discount(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    code = (await state.get_data()).get("code", "")
    try:
        discount = int(message.text.strip().rstrip("%"))
        if not 1 <= discount <= 100:
            raise ValueError
    except ValueError:
        await show_screen(bot, message.chat.id,
                          f"🎟 <b>Новый промокод {esc(code)} · шаг 2 из 3</b>\n\n<i>Нужно целое число от 1 до 100.</i>",
                          ikb([btn("✖️ Отмена", "adm_promos")]))
        return
    await state.update_data(discount=discount)
    await state.set_state(AdminStates.waiting_promo_uses)
    await show_screen(bot, message.chat.id,
                      f"🎟 <b>Новый промокод {esc(code)} (−{discount}%) · шаг 3 из 3</b>\n\n"
                      "Сколько раз его можно активировать? Отправьте число от 1 до 10000.",
                      ikb([btn("✖️ Отмена", "adm_promos")]))


@router.message(AdminStates.waiting_promo_uses, IsAdmin(), F.text, not_command)
async def process_promo_uses(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    data = await state.get_data()
    try:
        uses = int(message.text.strip())
        if not 1 <= uses <= 10000:
            raise ValueError
    except ValueError:
        await show_screen(bot, message.chat.id,
                          f"🎟 <b>Новый промокод {esc(data.get('code', ''))} · шаг 3 из 3</b>\n\n"
                          "<i>Нужно целое число от 1 до 10000.</i>",
                          ikb([btn("✖️ Отмена", "adm_promos")]))
        return
    await storage.create_promocode(data["code"], discount_percent=data["discount"], max_uses=uses)
    await state.clear()
    await _show_promos(bot, message.chat.id,
                       notice=f"Промокод {data['code']} создан: −{data['discount']}%, до {uses} активаций.")


# =========================================================
# UTM-ССЫЛКИ
# =========================================================

@router.callback_query(F.data.in_({"adm_utm", "admin_campaigns"}), IsAdmin())
async def cb_campaigns(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.clear()
    await _show_campaigns(bot, _cid(callback), source=callback.message)


async def _show_campaigns(bot: Bot, chat_id: int, source=None, notice: Optional[str] = None) -> None:
    camps = await storage.get_all_campaigns()
    username = await get_bot_username(bot)
    lines = ["🔗 <b>UTM-ссылки</b>"]
    if notice:
        lines.append(f"<i>{esc(notice)}</i>")
    if not camps:
        lines.append("Рекламных ссылок пока нет.")
    for tag, c in camps.items():
        joins, pays = c.get("joins", 0), c.get("payments", 0)
        conv = pays / joins * 100 if joins else 0
        lines.append(
            f"📌 <b>{esc(c.get('description') or tag)}</b>\n"
            f"<code>https://t.me/{esc(username)}?start=c_{esc(tag)}</code>\n"
            f"Переходов: {joins} · оплат: {pays} · CR {conv:.1f}%"
        )
    rows = [[btn("➕ Создать ссылку", "adm_utn")]]
    rows += [[btn(f"🗑 Удалить {tag}", f"adm_utd:{tag}")] for tag in list(camps)[:20]]
    rows.append([admin_back_btn()])
    await show_screen(bot, chat_id, "\n\n".join(lines), ikb(*rows), source=source)


@router.callback_query(F.data.startswith("adm_utd:"), IsAdmin())
async def cb_campaign_delete(callback: CallbackQuery, bot: Bot) -> None:
    tag = callback.data.split(":", 1)[1]
    await storage.delete_campaign(tag)
    await callback.answer(f"Ссылка {tag} удалена")
    await _show_campaigns(bot, _cid(callback), source=callback.message)


@router.callback_query(F.data.in_({"adm_utn", "admin_create_camp"}), IsAdmin())
async def cb_campaign_new(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.set_state(AdminStates.waiting_camp_tag)
    await show_screen(bot, _cid(callback),
                      "🔗 <b>Новая ссылка · шаг 1 из 2</b>\n\nОтправьте идентификатор: строчные латинские буквы, "
                      "цифры, «_» или «-», 2–32 символа (например, <code>habr_qa</code>).",
                      ikb([btn("✖️ Отмена", "adm_utm")]), source=callback.message)


@router.message(AdminStates.waiting_camp_tag, IsAdmin(), F.text, not_command)
async def process_camp_tag(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    tag = message.text.strip().lower().replace(" ", "_")
    error = None
    if not UTM_RE.match(tag):
        # Telegram допускает в ссылке ?start= только латиницу, цифры, «_» и «-»
        error = "Идентификатор может содержать только строчные латинские буквы, цифры, «_» и «-» (2–32 символа)."
    elif tag in await storage.get_all_campaigns():
        error = "Ссылка с таким идентификатором уже существует."
    if error:
        await show_screen(bot, message.chat.id,
                          f"🔗 <b>Новая ссылка · шаг 1 из 2</b>\n\n<i>{esc(error)}</i>",
                          ikb([btn("✖️ Отмена", "adm_utm")]))
        return
    await state.update_data(tag=tag)
    await state.set_state(AdminStates.waiting_camp_desc)
    await show_screen(bot, message.chat.id,
                      f"🔗 <b>Новая ссылка {esc(tag)} · шаг 2 из 2</b>\n\n"
                      "Отправьте описание (например, «Пост в канале @proger»).",
                      ikb([btn("✖️ Отмена", "adm_utm")]))


@router.message(AdminStates.waiting_camp_desc, IsAdmin(), F.text, not_command)
async def process_camp_desc(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    tag = (await state.get_data()).get("tag")
    await state.clear()
    if not tag:
        await show_admin_menu(bot, message.chat.id, state)
        return
    await storage.create_campaign(tag, message.text.strip()[:100])
    await _show_campaigns(bot, message.chat.id, notice=f"Ссылка {tag} создана.")


# =========================================================
# РАССЫЛКА
# =========================================================

async def _broadcast_recipients() -> list[int]:
    users = await storage.get_all_users()
    return [int(uid) for uid, u in users.items()
            if str(uid).isdigit() and not u.get("is_blocked") and not u.get("bot_blocked")]


@router.callback_query(F.data.in_({"adm_bc", "admin_broadcast"}), IsAdmin())
async def cb_broadcast(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.set_state(AdminStates.waiting_broadcast)
    count = len(await _broadcast_recipients())
    await show_screen(bot, _cid(callback),
                      "📢 <b>Рассылка</b>\n\nОтправьте сообщение для рассылки — текст, фото, видео или документ. "
                      f"Перед отправкой будет предпросмотр. Получателей: {count} "
                      "(без заблокированных и тех, кто заблокировал бота).",
                      ikb([btn("✖️ Отмена", "adm_menu")]), source=callback.message)


@router.message(AdminStates.waiting_broadcast, IsAdmin(), not_command)
async def process_broadcast_message(message: Message, state: FSMContext, bot: Bot) -> None:
    # Сообщение администратора не удаляем — это предпросмотр, его и будем копировать
    await state.update_data(bc_chat=message.chat.id, bc_msg=message.message_id)
    count = len(await _broadcast_recipients())
    await show_screen(bot, message.chat.id,
                      f"📢 <b>Рассылка</b>\n\n👆 Предпросмотр — сообщение выше.\nОтправить его {count} пользователям?",
                      ikb([btn("✅ Отправить", "adm_bc_go")], [btn("✖️ Отмена", "adm_bc_cancel")]),
                      force_new=True)


@router.callback_query(F.data == "adm_bc_cancel", IsAdmin())
async def cb_broadcast_cancel(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await delete_message(bot, data.get("bc_chat", _cid(callback)), data.get("bc_msg"))
    await callback.answer("Рассылка отменена")
    await show_admin_menu(bot, _cid(callback), state, source=callback.message)


@router.callback_query(F.data == "adm_bc_go", IsAdmin())
async def cb_broadcast_go(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    if not data.get("bc_msg"):
        await callback.answer("Сообщение для рассылки не найдено", show_alert=True)
        await show_admin_menu(bot, _cid(callback), state, source=callback.message)
        return
    await callback.answer("Рассылка запущена")
    recipients = await _broadcast_recipients()
    progress_id = await show_screen(bot, _cid(callback), f"📢 <b>Рассылка</b>\n\n⏳ Отправлено 0 из {len(recipients)}…",
                                    None, source=callback.message)
    asyncio.create_task(_run_broadcast(bot, _cid(callback), progress_id, data["bc_chat"], data["bc_msg"], recipients))


async def _run_broadcast(bot: Bot, admin_chat: int, progress_id: int, from_chat: int, msg_id: int,
                         recipients: list[int]) -> None:
    sent = blocked = failed = 0
    for i, uid in enumerate(recipients, start=1):
        for _attempt in range(2):
            try:
                await bot.copy_message(chat_id=uid, from_chat_id=from_chat, message_id=msg_id, reply_markup=dismiss_kb())
                sent += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                blocked += 1
                await storage.mark_bot_blocked(uid)
                break
            except Exception as e:
                logger.warning("Рассылка: не доставлено %s: %s", uid, e)
                failed += 1
                break
        await asyncio.sleep(0.05)
        if i % 25 == 0:
            try:
                await bot.edit_message_text(
                    text=f"📢 <b>Рассылка</b>\n\n⏳ Отправлено {i} из {len(recipients)}…",
                    chat_id=admin_chat, message_id=progress_id, parse_mode="HTML",
                )
            except Exception:
                pass

    await delete_message(bot, from_chat, msg_id)
    result = (
        "📢 <b>Рассылка завершена</b>\n\n"
        f"✅ Доставлено: {sent}\n🚫 Заблокировали бота: {blocked}\n⚠️ Другие ошибки: {failed}"
    )
    if await storage.get_screen_id(admin_chat) == progress_id:
        await show_screen(bot, admin_chat, result, ikb([admin_back_btn()]))
    else:
        await notify(bot, admin_chat, result)


# =========================================================
# АДМИНИСТРАТОРЫ
# =========================================================

@router.callback_query(F.data.in_({"adm_admins", "admin_team_hub"}), IsAdmin())
async def cb_admins(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.clear()
    await _show_admins(bot, _cid(callback), callback.from_user.id, source=callback.message)


async def _show_admins(bot: Bot, chat_id: int, me: int, source=None, notice: Optional[str] = None) -> None:
    saved = await storage.get_saved_admins()
    users = await storage.get_all_users()
    lines = ["👮 <b>Администраторы</b>"]
    if notice:
        lines.append(f"<i>{esc(notice)}</i>")
    entries = []
    for aid in sorted(set(ADMIN_IDS) | set(saved)):
        label = esc(_user_label(aid, users.get(str(aid), {})))
        source_note = " · из .env" if aid in ADMIN_IDS else ""
        you = " · это вы" if aid == me else ""
        entries.append(f"• {label} (<code>{aid}</code>){source_note}{you}")
    lines.append("\n".join(entries) or "Список пуст.")
    rows = [[btn("➕ Назначить администратора", "adm_ad_new")]]
    rows += [[btn(f"🗑 Снять {aid}", f"adm_ad_del:{aid}")] for aid in saved if aid != me]
    rows.append([admin_back_btn()])
    await show_screen(bot, chat_id, "\n\n".join(lines), ikb(*rows), source=source)


@router.callback_query(F.data.startswith("adm_ad_del:"), IsAdmin())
async def cb_admin_remove(callback: CallbackQuery, bot: Bot) -> None:
    aid = int(callback.data.split(":", 1)[1])
    if aid == callback.from_user.id:
        await callback.answer("Нельзя снять права с самого себя", show_alert=True)
        return
    await storage.remove_admin_permanently(aid)
    await callback.answer("Права администратора сняты")
    await _show_admins(bot, _cid(callback), callback.from_user.id, source=callback.message)


@router.callback_query(F.data.in_({"adm_ad_new", "admin_add_team_member"}), IsAdmin())
async def cb_admin_add(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.set_state(AdminStates.waiting_add_admin_id)
    await show_screen(bot, _cid(callback),
                      "👮 <b>Новый администратор</b>\n\nОтправьте Telegram ID пользователя (число).",
                      ikb([btn("✖️ Отмена", "adm_admins")]), source=callback.message)


@router.message(AdminStates.waiting_add_admin_id, IsAdmin(), F.text, not_command)
async def process_add_admin(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    try:
        aid = int(message.text.strip())
    except ValueError:
        await show_screen(bot, message.chat.id,
                          "👮 <b>Новый администратор</b>\n\n<i>Нужен числовой Telegram ID.</i>",
                          ikb([btn("✖️ Отмена", "adm_admins")]))
        return
    await state.clear()
    await storage.add_admin_permanently(aid)
    await _show_admins(bot, message.chat.id, message.from_user.id, notice=f"Пользователь {aid} назначен администратором.")


# =========================================================
# ВЫГРУЗКА ДАННЫХ
# =========================================================

@router.callback_query(F.data.in_({"adm_export", "admin_export_db"}), IsAdmin())
async def cb_export(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    files = [USERS_DATA_FILE, PAYMENTS_LOG_FILE, storage.REVIEWS_DATA_FILE, storage.CAMPAIGNS_DATA_FILE,
             storage.PROMOCODES_DATA_FILE, storage.ADMINS_DATA_FILE]
    existing = [f for f in files if os.path.exists(f)]
    if not existing:
        await callback.answer("Файлы данных пока не созданы", show_alert=True)
        return
    await callback.answer("Отправляем файлы…")
    for path in existing:
        try:
            await bot.send_document(chat_id=_cid(callback), document=FSInputFile(path), reply_markup=dismiss_kb())
        except Exception as e:
            logger.warning("Не удалось отправить %s: %s", path, e)
    # Меню переносим под документы
    await show_admin_menu(bot, _cid(callback), state, force_new=True,
                          notice=f"Выгружено файлов: {len(existing)}. Их можно скрыть кнопкой под каждым файлом.")


# =========================================================
# СБРОС СТАТИСТИКИ
# =========================================================

RESET_KINDS = {
    "payments": "журнал платежей (выручка и список платежей)",
    "utm": "счётчики переходов и оплат по UTM-ссылкам",
    "all": "журнал платежей и счётчики UTM-ссылок",
}


@router.callback_query(F.data == "adm_reset", IsAdmin())
async def cb_reset_hub(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    payments = await storage.get_payments()
    rub = sum(storage.payment_amount(p) for p in payments if p.get("currency") == "RUB")
    stars = sum(storage.payment_amount(p) for p in payments if p.get("currency") == "XTR")
    camps = await storage.get_all_campaigns()
    joins = sum(c.get("joins", 0) for c in camps.values())
    pays = sum(c.get("payments", 0) for c in camps.values())
    text = (
        "🧹 <b>Сброс статистики</b>\n\n"
        f"• Журнал платежей: {len(payments)} записей ({rub} ₽, {stars} ⭐️)\n"
        f"• UTM-ссылок: {len(camps)} (переходов {joins}, оплат {pays})\n\n"
        "Сброс не затрагивает пользователей: их оплаченный доступ, бонусы и результаты сохраняются. "
        "Перед очисткой данные копируются в архив <code>data/archive/</code>."
    )
    kb = ikb(
        [btn("🧾 Очистить журнал платежей", "adm_reset_ask:payments")],
        [btn("🔗 Обнулить счётчики UTM", "adm_reset_ask:utm")],
        [btn("🧹 Сбросить всё", "adm_reset_ask:all")],
        [admin_back_btn()],
    )
    await show_screen(bot, _cid(callback), text, kb, source=callback.message)


@router.callback_query(F.data.startswith("adm_reset_ask:"), IsAdmin())
async def cb_reset_ask(callback: CallbackQuery, bot: Bot) -> None:
    kind = callback.data.split(":", 1)[1]
    if kind not in RESET_KINDS:
        await callback.answer()
        return
    await callback.answer()
    await show_screen(
        bot, _cid(callback),
        f"⚠️ <b>Подтвердите сброс</b>\n\nБудет очищено: {RESET_KINDS[kind]}.\n"
        "Копия текущих данных сохранится в <code>data/archive/</code>.",
        ikb([btn("✅ Да, сбросить", f"adm_reset_do:{kind}")], [btn("✖️ Отмена", "adm_reset")]),
        source=callback.message,
    )


@router.callback_query(F.data.startswith("adm_reset_do:"), IsAdmin())
async def cb_reset_do(callback: CallbackQuery, bot: Bot) -> None:
    kind = callback.data.split(":", 1)[1]
    if kind not in RESET_KINDS:
        await callback.answer()
        return
    lines = []
    if kind in ("payments", "all"):
        count, archive = await storage.reset_payments_log()
        lines.append(f"• Журнал платежей очищен (записей: {count})" + (f", архив: <code>{esc(os.path.basename(archive))}</code>" if archive else ""))
    if kind in ("utm", "all"):
        count, archive = await storage.reset_campaign_counters()
        lines.append(f"• Счётчики UTM обнулены (ссылок: {count})" + (f", архив: <code>{esc(os.path.basename(archive))}</code>" if archive else ""))
    await callback.answer("Статистика сброшена")
    await show_screen(bot, _cid(callback), "✅ <b>Готово</b>\n\n" + "\n".join(lines),
                      ikb([btn("◀️ К сбросу статистики", "adm_reset")], [admin_back_btn()]), source=callback.message)
