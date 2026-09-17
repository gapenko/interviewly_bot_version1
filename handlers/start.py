"""
handlers/start.py — приветствие, выбор направления, рефералы, навигация назад и поддержка.
"""
import logging
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import storage
from config import ADMIN_IDS, FREE_QUESTIONS_COUNT
from keyboards import (
    get_interview_toolbar,
    get_ready_keyboard,
    get_support_cancel_keyboard,
    get_tracks_keyboard,
    get_welcome_inline_keyboard,
    remove_reply_kb,
)
from questions import TOTAL_QUESTIONS, TRACKS, get_question
from states import InterviewStates, SupportStates
from ui_utils import format_question_card

logger = logging.getLogger(__name__)
router = Router(name="start")


# -------------------------------------------------------------
# СТАРТ, РЕФЕРАЛЫ И ВОССТАНОВЛЕНИЕ ПРОГРЕССА
# -------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return

    args = message.text.split()[1] if len(message.text.split()) > 1 else None
    referrer_id = None
    campaign_tag = None

    if args:
        if args.startswith("ref_"):
            try:
                referrer_id = int(args.replace("ref_", ""))
            except ValueError:
                pass
        elif args.startswith("c_"):
            campaign_tag = args.replace("c_", "")

    user, is_new = await storage.register_user_with_ref(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        referrer_id=referrer_id,
        campaign_tag=campaign_tag,
    )

    if user.get("track") and not user.get("finished", False) and user.get("current_question_index", 0) > 0:
        await state.set_state(InterviewStates.waiting_answer)
        await message.answer(
            "Продолжаем собеседование с того места, где вы остановились 👇",
            reply_markup=remove_reply_kb,
        )
        await ask_current_question(message, state, message.from_user.id)
        return

    await state.clear()
    await state.set_state(InterviewStates.welcome)

    ref_note = ""
    if is_new and referrer_id:
        ref_note = "\n🎁 <i>Вы активировали приглашение от друга!</i>\n"

    welcome_text = (
        f"👋 <b>Привет, {message.from_user.full_name}! Я AI-тренажёр для подготовки к IT-собеседованиям.</b>{ref_note}\n\n"
        "Я помогу проверить уровень знаний, научиться отвечать структурированно и без воды, "
        "а также подготовлю к сложным техническим и поведенческим кейсам.\n\n"
        "Выберите действие ниже или откройте меню команд слева внизу:"
    )

    await message.answer(
        welcome_text,
        reply_markup=get_welcome_inline_keyboard(),
        parse_mode="HTML",
    )


# -------------------------------------------------------------
# НАВИГАЦИЯ: УНИВЕРСАЛЬНЫЙ ВОЗВРАТ В ГЛАВНОЕ МЕНЮ
# -------------------------------------------------------------

@router.callback_query(F.data == "nav_back_to_welcome")
async def cb_back_to_welcome(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(InterviewStates.welcome)
    await callback.answer()

    welcome_text = (
        f"👋 <b>Главное меню AI-тренажёра</b>\n\n"
        "Выберите действие ниже или используйте список команд в меню:"
    )

    if callback.message:
        await callback.message.edit_text(
            welcome_text,
            reply_markup=get_welcome_inline_keyboard(),
            parse_mode="HTML",
        )


# -------------------------------------------------------------
# РЕФЕРАЛЬНАЯ ПРОГРАММА
# -------------------------------------------------------------

@router.message(Command("ref"))
@router.message(Command("bonus"))
@router.callback_query(F.data == "btn_ref_program")
async def cmd_referral(event: Message | CallbackQuery):
    is_cb = isinstance(event, CallbackQuery)
    target = event.message if is_cb else event
    if is_cb:
        await event.answer()

    uid = event.from_user.id
    user = await storage.get_user(uid)
    balance = user.get("bonus_balance", 0)
    refs = user.get("referrals_count", 0)
    has_paid = user.get("paid", False)

    bot_me = await event.bot.get_me()
    ref_link = f"https://t.me/{bot_me.username}?start=ref_{uid}"

    progress = min(balance, 600)
    filled_blocks = int((progress / 600) * 10)
    bar = "🟩" * filled_blocks + "⬜️" * (10 - filled_blocks)

    status_str = (
        "👑 <b>У вас уже разблокирован полный доступ!</b>"
        if has_paid
        else f"🎯 До вечного доступа ко всем вопросам: <b>{max(0, 600 - balance)} бонусов</b>"
    )

    text = (
        "🎁 <b>Реферальная программа тренажёра</b>\n\n"
        "Приглашайте друзей и готовьтесь к собеседованиям бесплатно!\n\n"
        "• За каждого приглашённого друга: <b>+150 бонусов</b>\n"
        "• При накоплении <b>600 бонусов</b> (всего 4 друга) автоматически открывается <b>вечный доступ</b>!\n"
        "• Бонусы можно тратить на скидку при оплате (1 бонус = 1 рубль скидки).\n\n"
        f"📊 <b>Ваша статистика:</b>\n"
        f"├ Баланс: <b>{balance} бонусов</b>\n"
        f"├ Приглашено: <b>{refs} чел.</b>\n"
        f"└ {bar} ({balance}/600)\n\n"
        f"{status_str}\n\n"
        f"🔗 <b>Ваша реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>"
    )

    share_url = f"https://t.me/share/url?url={ref_link}&text=Привет!%20Пройди%20тренировочное%20IT-собеседование%20с%20AI-ментором%20бесплатно:"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📲 Поделиться ссылкой", url=share_url)],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="nav_back_to_welcome")],
        ]
    )

    if is_cb and target:
        await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


# -------------------------------------------------------------
# КОМАНДЫ ВОССТАНОВЛЕНИЯ И СБРОСА
# -------------------------------------------------------------

@router.message(Command("continue"))
async def cmd_continue(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return

    user = await storage.get_user(message.from_user.id, message.from_user.username)
    track = user.get("track")
    index = user.get("current_question_index", 0)
    finished = user.get("finished", False)

    if not track:
        await state.set_state(InterviewStates.selecting_track)
        await message.answer(
            "У вас нет начатого собеседования.\nВыберите направление для старта:",
            reply_markup=get_tracks_keyboard(),
            parse_mode="HTML",
        )
        return

    if finished or index >= TOTAL_QUESTIONS:
        from handlers.interview import send_final_report
        await send_final_report(message, state, message.from_user.id)
        return

    if index >= FREE_QUESTIONS_COUNT and not user.get("paid"):
        from handlers.interview import get_pay_inline_keyboard
        await state.set_state(InterviewStates.waiting_payment)
        await message.answer(
            "🔒 Вы остановились на этапе оплаты доступа.",
            reply_markup=get_pay_inline_keyboard(),
            parse_mode="HTML",
        )
        return

    await state.set_state(InterviewStates.waiting_answer)
    await message.answer("🔄 <b>Собеседование возобновлено!</b>", parse_mode="HTML")
    await ask_current_question(message, state, message.from_user.id)


@router.message(Command("reset"))
async def cmd_reset(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return

    await storage.reset_user(message.from_user.id)
    await state.clear()
    await state.set_state(InterviewStates.selecting_track)

    await message.answer(
        "🔄 <b>Прогресс сброшен.</b>\n\n"
        "Выберите направление, чтобы начать новую тренировку:",
        reply_markup=get_tracks_keyboard(),
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    help_text = (
        "ℹ️ <b>Памятка по работе с ботом:</b>\n\n"
        "• /start — главное меню тренажёра.\n"
        "• /continue — продолжить начатое собеседование.\n"
        "• /pay — оплатить доступ, ввести промокод или списать бонусы.\n"
        "• /ref — реферальная программа (+150 бонусов за друга).\n"
        "• /reviews — отзывы участников.\n"
        "• /reset — сбросить ответы и выбрать другое направление.\n"
        "• /support — техподдержка.\n\n"
        "💡 <i>В левом нижнем углу экрана всегда доступна кнопка Menu со всеми командами!</i>"
    )
    await message.answer(help_text, parse_mode="HTML")


# -------------------------------------------------------------
# ТЕХНИЧЕСКАЯ ПОДДЕРЖКА
# -------------------------------------------------------------

@router.message(Command("support"))
async def cmd_support(message: Message, state: FSMContext) -> None:
    await state.set_state(SupportStates.waiting_support_message)
    await message.answer(
        "✍️ <b>Служба поддержки</b>\n\n"
        "Опишите ваш вопрос или проблему одним сообщением.\n"
        "Мы сразу передадим его администраторам бота.",
        reply_markup=get_support_cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(SupportStates.waiting_support_message, F.text)
async def process_support_message(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user or not message.text:
        await message.answer("Пожалуйста, отправьте текстовое сообщение с описанием проблемы.")
        return

    user_info = (
        f"👤 <b>От:</b> {message.from_user.full_name}\n"
        f"🆔 <b>ID:</b> <code>{message.from_user.id}</code>\n"
        f"🔗 <b>Username:</b> @{message.from_user.username if message.from_user.username else 'отсутствует'}"
    )

    admin_alert = (
        f"📩 <b>НОВОЕ ОБРАЩЕНИЕ В ПОДДЕРЖКУ!</b>\n\n"
        f"{user_info}\n\n"
        f"💬 <b>Текст обращения:</b>\n{message.text}"
    )

    inline_buttons = [
        [
            InlineKeyboardButton(
                text="✏️ Ответить в боте",
                callback_data=f"reply_support:{message.from_user.id}",
            )
        ]
    ]
    if message.from_user.username:
        inline_buttons.append(
            [InlineKeyboardButton(text="💬 Написать в личку", url=f"https://t.me/{message.from_user.username}")]
        )

    reply_kb = InlineKeyboardMarkup(inline_keyboard=inline_buttons)

    from handlers.admin import get_active_admin_ids
    target_admins = await get_active_admin_ids()

    for admin_id in target_admins:
        try:
            await bot.send_message(chat_id=admin_id, text=admin_alert, reply_markup=reply_kb, parse_mode="HTML")
        except Exception as e:
            logger.error("Не удалось доставить обращение админу %s: %s", admin_id, e)

    await state.clear()
    await message.answer(
        "✅ <b>Ваше сообщение передано администраторам!</b>\n"
        "Мы ответим вам прямо в этом чате в ближайшее время.",
        parse_mode="HTML",
    )


# -------------------------------------------------------------
# ВЫБОР СПЕЦИАЛЬНОСТИ И ПРАВИЛА
# -------------------------------------------------------------

@router.callback_query(F.data == "start_choose_track")
async def cb_show_tracks(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(InterviewStates.selecting_track)
    choose_text = (
        "🎯 <b>Выберите направление для прохождения собеседования:</b>\n\n"
        "<i>Вопросы будут подобраны строго под ваш стек.</i>"
    )
    if callback.message:
        await callback.message.edit_text(choose_text, reply_markup=get_tracks_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("track_"), InterviewStates.selecting_track)
async def cb_track_selected(callback: CallbackQuery, state: FSMContext) -> None:
    track_key = callback.data.replace("track_", "")
    if track_key not in TRACKS:
        await callback.answer("Некорректный выбор.")
        return

    user = await storage.get_user(callback.from_user.id)
    user["track"] = track_key
    user["current_question_index"] = 0
    user["answers"] = []
    user["finished"] = False
    await storage.save_user(callback.from_user.id, user)

    track_name = TRACKS[track_key]
    await state.set_state(InterviewStates.ready_to_start)

    rules_text = (
        f"✅ <b>Выбранное направление: {track_name}</b>\n\n"
        "📋 <b>Как будет проходить интервью:</b>\n"
        f"• Вас ждёт <b>{TOTAL_QUESTIONS} вопросов</b>: реальный опыт, глубокий Hard Skills и Soft Skills.\n"
        f"• Первые <b>{FREE_QUESTIONS_COUNT} вопроса доступны бесплатно</b>.\n"
        "• На каждый ваш ответ ментор даёт <b>разбор</b> сильных и слабых сторон.\n"
        "• В финале формируется <b>комплексный Word-отчёт (.docx)</b>.\n\n"
        "💡 <b>Главное правило:</b>\n"
        "Отвечайте <b>максимально развёрнуто</b>. Односложные ответы не позволят оценить ваш грейд!"
    )
    if callback.message:
        await callback.message.edit_text(rules_text, reply_markup=get_ready_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "start_first_question", InterviewStates.ready_to_start)
async def cb_first_question(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass
    await callback.answer()
    await ask_current_question(callback.message, state, callback.from_user.id)


# -------------------------------------------------------------
# ОТПРАВКА ТЕКУЩЕГО ВОПРОСА
# -------------------------------------------------------------

async def ask_current_question(message: Message, state: FSMContext, user_id: int | None = None) -> None:
    uid = user_id or (message.from_user.id if message.from_user else None)
    if not uid:
        return

    user = await storage.get_user(uid)
    index = user["current_question_index"]
    track = user.get("track")

    if not track:
        await state.set_state(InterviewStates.selecting_track)
        await message.answer("Пожалуйста, выберите направление:", reply_markup=get_tracks_keyboard(), parse_mode="HTML")
        return

    if index >= TOTAL_QUESTIONS:
        from handlers.interview import send_final_report
        await send_final_report(message, state, uid)
        return

    question = get_question(track, index)
    await state.set_state(InterviewStates.waiting_answer)
    await state.update_data(current_question_id=question["id"])

    card = format_question_card(question, index)
    await message.bot.send_message(
        chat_id=uid,
        text=card,
        reply_markup=get_interview_toolbar(),
        parse_mode="HTML",
    )


# -------------------------------------------------------------
# CATCH-ALL
# -------------------------------------------------------------

@router.message(F.text)
async def process_unhandled_text(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return

    user = await storage.get_user(message.from_user.id)
    track = user.get("track")
    finished = user.get("finished", False)
    index = user.get("current_question_index", 0)

    if track and not finished and index < TOTAL_QUESTIONS:
        if index >= FREE_QUESTIONS_COUNT and not user.get("paid"):
            from handlers.interview import get_pay_inline_keyboard
            await state.set_state(InterviewStates.waiting_payment)
            await message.answer(
                "🔒 Вы остановились на этапе оплаты доступа.",
                reply_markup=get_pay_inline_keyboard(),
                parse_mode="HTML",
            )
            return

        if message.text.startswith("/"):
            await message.answer(
                "⚠️ Вы находитесь на этапе собеседования. Ответьте на вопрос текстом или используйте кнопки под ним.",
                parse_mode="HTML",
            )
            return

        await state.set_state(InterviewStates.waiting_answer)
        from handlers.interview import handle_text_answer
        await handle_text_answer(message, state, message.bot)
        return

    await message.answer(
        "❓ <b>Команда не распознана.</b>\nВоспользуйтесь кнопкой <b>Menu</b> в левом нижнем углу или кнопками ниже:",
        reply_markup=get_welcome_inline_keyboard(),
        parse_mode="HTML",
    )