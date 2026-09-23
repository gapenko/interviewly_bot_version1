"""
handlers/start.py — главное меню, выбор направления, бонусы, помощь, поддержка,
а также «страховочные» обработчики в самом конце цепочки:
- любой текст вне сценариев (например, ответ на вопрос после перезапуска бота, когда FSM сброшен);
- любые прочие сообщения (стикеры, фото…) — удаляются, чтобы не засорять чат;
- устаревшие кнопки (от старых версий меню) — открывают главное меню вместо «вечной загрузки».

Этот роутер подключается последним (см. handlers/__init__.py).
"""
import logging
from urllib.parse import quote

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import storage
from config import REFERRAL_BONUS_PER_INVITE, REFERRAL_FULL_ACCESS_THRESHOLD
from handlers.interview import process_answer_text, render_question
from keyboards import back_menu_kb, btn, ikb, menu_btn, reset_confirm_kb
from menus import get_bot_username, interview_in_progress, main_menu_view, plural, results_view, rules_view, tracks_view
from questions import TRACKS
from screen import DISMISS_CALLBACK, delete_user_message, esc, not_command, notify, show_screen
from states import InterviewStates, SupportStates

logger = logging.getLogger(__name__)
router = Router(name="start")


def _chat_id(callback: CallbackQuery) -> int:
    return callback.message.chat.id if callback.message else callback.from_user.id


async def show_main_menu(bot: Bot, chat_id: int, uid: int, state: FSMContext, *, source=None,
                         force_new: bool = False, notice: str | None = None, intro: bool = False) -> None:
    await state.clear()
    text, kb = await main_menu_view(uid, notice=notice, intro=intro)
    await show_screen(bot, chat_id, text, kb, source=source, force_new=force_new)


# =========================================================
# СТАРТ И ГЛАВНОЕ МЕНЮ
# =========================================================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    parts = (message.text or "").split(maxsplit=1)
    args = parts[1].strip() if len(parts) > 1 else ""
    referrer_id = None
    campaign_tag = None
    if args.startswith("ref_"):
        try:
            referrer_id = int(args[4:])
        except ValueError:
            pass
    elif args.startswith("c_"):
        campaign_tag = args[2:]

    _, is_new = await storage.register_user_with_ref(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        referrer_id=referrer_id,
        campaign_tag=campaign_tag,
    )
    notice = "Вы перешли по приглашению — добро пожаловать!" if is_new and referrer_id else None
    await show_main_menu(bot, message.chat.id, message.from_user.id, state,
                         force_new=True, notice=notice, intro=is_new)


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    await show_main_menu(bot, message.chat.id, message.from_user.id, state, force_new=True)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, bot: Bot) -> None:
    """Выход из любого режима ввода (раньше /cancel работал только для администраторов)."""
    await delete_user_message(message)
    await show_main_menu(bot, message.chat.id, message.from_user.id, state, force_new=True, notice="Действие отменено.")


@router.callback_query(F.data.in_({"nav_menu", "nav_back_to_welcome"}))
async def cb_menu(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await show_main_menu(bot, _chat_id(callback), callback.from_user.id, state, source=callback.message)


@router.callback_query(F.data == DISMISS_CALLBACK)
async def cb_dismiss(callback: CallbackQuery):
    """Кнопка «✖️ Скрыть» у уведомлений."""
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass


# =========================================================
# ВЫБОР НАПРАВЛЕНИЯ
# =========================================================

@router.callback_query(F.data.in_({"menu_start", "start_choose_track"}))
async def cb_show_tracks(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.clear()
    user = await storage.get_user(callback.from_user.id)
    text, kb = tracks_view(user)
    await show_screen(bot, _chat_id(callback), text, kb, source=callback.message)


@router.callback_query(F.data.startswith("track:") | F.data.startswith("track_"))
async def cb_track_selected(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Выбор направления только показывает правила — прогресс сбрасывается лишь по кнопке «Начать»."""
    track = callback.data.split(":", 1)[1] if callback.data.startswith("track:") else callback.data[6:]
    user = await storage.get_user(callback.from_user.id)
    if track not in TRACKS:
        await callback.answer("Направление не найдено")
        text, kb = tracks_view(user)
    else:
        await callback.answer()
        text, kb = rules_view(user, track)
    await show_screen(bot, _chat_id(callback), text, kb, source=callback.message)


@router.callback_query(F.data.startswith("begin:"))
async def cb_begin(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    track = callback.data.split(":", 1)[1]
    uid = callback.from_user.id
    if track not in TRACKS:
        await callback.answer("Направление не найдено")
        user = await storage.get_user(uid)
        text, kb = tracks_view(user)
        await show_screen(bot, _chat_id(callback), text, kb, source=callback.message)
        return
    await callback.answer()
    await storage.start_track(uid, track)
    await state.clear()
    await render_question(bot, _chat_id(callback), uid, state, source=callback.message, ctx={"mode": "first"})


# =========================================================
# ПРОДОЛЖЕНИЕ / СБРОС
# =========================================================

@router.message(Command("continue"))
async def cmd_continue(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    uid = message.from_user.id
    user = await storage.get_user(uid, message.from_user.username)
    if interview_in_progress(user):
        await render_question(bot, message.chat.id, uid, state, force_new=True, ctx={"mode": "resume"})
    elif user.get("last_result"):
        await state.clear()
        text, kb = results_view(user)
        await show_screen(bot, message.chat.id, text, kb, force_new=True)
    else:
        await state.clear()
        text, kb = tracks_view(user, notice="Начатого собеседования нет. Выберите направление:")
        await show_screen(bot, message.chat.id, text, kb, force_new=True)


@router.message(Command("reset"))
async def cmd_reset(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    await state.clear()
    user = await storage.get_user(message.from_user.id)
    if not interview_in_progress(user):
        text, kb = tracks_view(user)
        await show_screen(bot, message.chat.id, text, kb, force_new=True)
        return
    await show_screen(
        bot, message.chat.id,
        "🔄 <b>Начать собеседование заново?</b>\n\n"
        "Все ответы текущего собеседования будут удалены, и вы сможете выбрать направление заново. "
        "Оплаченный доступ и бонусы сохранятся.",
        reset_confirm_kb(), force_new=True,
    )


# =========================================================
# БОНУСЫ И ПРИГЛАШЕНИЯ
# =========================================================

async def referral_view(bot: Bot, uid: int):
    user = await storage.get_user(uid)
    balance = user.get("bonus_balance", 0)
    refs = user.get("referrals_count", 0)
    ref_link = f"https://t.me/{await get_bot_username(bot)}?start=ref_{uid}"

    threshold = REFERRAL_FULL_ACCESS_THRESHOLD
    filled = int(min(balance, threshold) / threshold * 10) if threshold else 0
    bar = "🟩" * filled + "⬜️" * (10 - filled)
    friends_needed = threshold // REFERRAL_BONUS_PER_INVITE if REFERRAL_BONUS_PER_INVITE else 0
    status = (
        "👑 <b>У вас открыт полный доступ ко всем направлениям.</b>"
        if user.get("paid")
        else f"🎯 До полного доступа ко всем направлениям: <b>{max(0, threshold - balance)} бонусов</b>"
    )
    text = (
        "🎁 <b>Бонусы и приглашения</b>\n\n"
        f"• За каждого приглашённого друга: <b>+{REFERRAL_BONUS_PER_INVITE} бонусов</b>.\n"
        f"• При накоплении <b>{threshold} бонусов</b> (это {friends_needed} {plural(friends_needed, 'друг', 'друга', 'друзей')}) "
        "автоматически открывается "
        "<b>полный доступ ко всем направлениям</b>.\n"
        "• Бонусы можно списать при оплате: 1 бонус = 1 ₽ скидки.\n\n"
        f"📊 Баланс: <b>{balance}</b> · приглашено: <b>{refs}</b>\n"
        f"{bar} {min(balance, threshold)}/{threshold}\n\n"
        f"{status}\n\n"
        f"🔗 Ваша ссылка для приглашения:\n<code>{esc(ref_link)}</code>"
    )
    share_text = quote("Тренажёр реального технического собеседования в IT — первые вопросы бесплатно:")
    share_url = f"https://t.me/share/url?url={quote(ref_link, safe='')}&text={share_text}"
    kb = ikb([btn("📲 Поделиться ссылкой", url=share_url)], [menu_btn()])
    return text, kb


@router.message(Command("ref", "bonus"))
async def cmd_referral(message: Message, state: FSMContext, bot: Bot):
    await delete_user_message(message)
    await state.clear()
    text, kb = await referral_view(bot, message.from_user.id)
    await show_screen(bot, message.chat.id, text, kb, force_new=True)


@router.callback_query(F.data.in_({"ref", "btn_ref_program"}))
async def cb_referral(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await state.clear()
    text, kb = await referral_view(bot, callback.from_user.id)
    await show_screen(bot, _chat_id(callback), text, kb, source=callback.message)


# =========================================================
# ПОМОЩЬ
# =========================================================

HELP_TEXT = (
    "ℹ️ <b>Как пользоваться тренажёром</b>\n\n"
    "1. Выберите направление и начните собеседование.\n"
    "2. Отвечайте на вопросы текстом — развёрнуто, как на реальном интервью. После каждого ответа "
    "интервьюер даёт краткую обратную связь.\n"
    "3. Под вопросом есть кнопки: подсказка, пропуск вопроса, начать заново, завершить досрочно.\n"
    "4. В конце вы получите итоговый разбор, черновик резюме и Word-отчёт — они сохраняются "
    "в разделе «Мои результаты».\n\n"
    "Доступ к полному собеседованию оплачивается отдельно для каждого направления. "
    "Промокоды и бонусы применяются на экране оплаты.\n\n"
    "<b>Команды</b>\n"
    "/start — главное меню\n"
    "/continue — продолжить собеседование\n"
    "/results — мои результаты и резюме\n"
    "/pay — оплата доступа\n"
    "/ref — бонусы и приглашения\n"
    "/reviews — отзывы\n"
    "/reset — начать заново\n"
    "/support — поддержка\n"
    "/cancel — отменить текущее действие"
)


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    await state.clear()
    await show_screen(bot, message.chat.id, HELP_TEXT, back_menu_kb(), force_new=True)


@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.clear()
    await show_screen(bot, _chat_id(callback), HELP_TEXT, back_menu_kb(), source=callback.message)


# =========================================================
# ПОДДЕРЖКА
# =========================================================

SUPPORT_PROMPT = (
    "💬 <b>Поддержка</b>\n\n"
    "Опишите вопрос или проблему одним сообщением — мы передадим его администраторам, "
    "ответ придёт в этот чат."
)


@router.message(Command("support"))
async def cmd_support(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    await state.set_state(SupportStates.waiting_support_message)
    await show_screen(bot, message.chat.id, SUPPORT_PROMPT, ikb([btn("✖️ Отмена", "nav_menu")]), force_new=True)


@router.callback_query(F.data == "support")
async def cb_support(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.set_state(SupportStates.waiting_support_message)
    await show_screen(bot, _chat_id(callback), SUPPORT_PROMPT, ikb([btn("✖️ Отмена", "nav_menu")]), source=callback.message)


@router.message(SupportStates.waiting_support_message, F.text, not_command)
async def process_support_message(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    await state.clear()
    user = message.from_user
    alert = (
        "📩 <b>Новое обращение в поддержку</b>\n\n"
        f"👤 {esc(user.full_name)} (@{esc(user.username or '—')}), ID <code>{user.id}</code>\n\n"
        f"💬 {esc(message.text[:3000])}"
    )
    rows = [[btn("✏️ Ответить", f"reply_support:{user.id}"), btn("👤 Карточка", f"adm_u:{user.id}")]]
    if user.username:
        rows.append([btn("💬 Написать в личку", url=f"https://t.me/{user.username}")])

    delivered = False
    for admin_id in await storage.get_active_admin_ids():
        delivered = await notify(bot, admin_id, alert, rows) or delivered

    text = (
        "✅ <b>Сообщение отправлено</b>\n\nОтвет администратора придёт в этот чат."
        if delivered
        else "⚠️ Не удалось доставить сообщение администраторам. Пожалуйста, попробуйте позже."
    )
    await show_screen(bot, message.chat.id, text, back_menu_kb())


# =========================================================
# СТРАХОВОЧНЫЕ ОБРАБОТЧИКИ (должны быть последними)
# =========================================================

@router.message(F.text)
async def process_unhandled_text(message: Message, state: FSMContext, bot: Bot) -> None:
    uid = message.from_user.id
    user = await storage.get_user(uid)

    if interview_in_progress(user) and not message.text.startswith("/"):
        # FSM мог сброситься после перезапуска бота — считаем текст ответом на текущий вопрос
        await state.set_state(InterviewStates.waiting_answer)
        await process_answer_text(message, state, bot)
        return

    await delete_user_message(message)
    if interview_in_progress(user):
        await render_question(bot, message.chat.id, uid, state,
                              notice="Неизвестная команда. Ответьте на вопрос сообщением или воспользуйтесь кнопками.")
        return
    await show_main_menu(bot, message.chat.id, uid, state, notice="Команда не распознана. Воспользуйтесь кнопками меню.")


@router.message()
async def process_other_messages(message: Message, state: FSMContext, bot: Bot) -> None:
    """Стикеры, фото, голосовые и т.п. вне сценариев — удаляем, чтобы не засорять чат."""
    await delete_user_message(message)
    user = await storage.get_user(message.from_user.id)
    if interview_in_progress(user):
        await render_question(bot, message.chat.id, message.from_user.id, state,
                              notice="Ответы принимаются только в текстовом виде.")


@router.callback_query()
async def fallback_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Кнопка, которую никто не обработал (например, от старой версии меню)."""
    await callback.answer("Это меню устарело — открываю главное меню.")
    await show_main_menu(bot, _chat_id(callback), callback.from_user.id, state, source=callback.message)
