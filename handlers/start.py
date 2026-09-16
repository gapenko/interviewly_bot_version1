"""
handlers/start.py — пошаговое приветствие, выбор направления, запуск вопросов,
служба поддержки, команды тестирования (/test, /testpay), вызов премиума (/prem)
и восстановление прогресса (/continue).
"""
import logging
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

import storage
from config import ADMIN_IDS, FREE_QUESTIONS_COUNT
from questions import TOTAL_QUESTIONS, TRACKS, get_question
from states import InterviewStates, SupportStates

logger = logging.getLogger(__name__)

router = Router(name="start")


# -------------------------------------------------------------
# КЛАВИАТУРЫ
# -------------------------------------------------------------

def get_idle_reply_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура начального экрана с кнопкой быстрого продолжения."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="▶️ Продолжить")],
            [KeyboardButton(text="🔄 Начать заново"), KeyboardButton(text="💬 Поддержка")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def get_interview_reply_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура, активная во время диалога/собеседования."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔄 Начать заново"), KeyboardButton(text="❓ Помощь")],
            [KeyboardButton(text="💬 Поддержка")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def get_welcome_inline_keyboard() -> InlineKeyboardMarkup:
    """Кнопка перехода к выбору направления."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Начать собеседование",
                    callback_data="start_choose_track",
                )
            ]
        ]
    )


def get_tracks_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-кнопки выбора направления (в 2 колонки)."""
    buttons = []
    items = list(TRACKS.items())
    for i in range(0, len(items), 2):
        row = [
            InlineKeyboardButton(text=items[i][1], callback_data=f"track_{items[i][0]}")
        ]
        if i + 1 < len(items):
            row.append(
                InlineKeyboardButton(text=items[i + 1][1], callback_data=f"track_{items[i + 1][0]}")
            )
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_ready_keyboard() -> InlineKeyboardMarkup:
    """Кнопка готовности к первому вопросу."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Поехали к вопросам ➡️",
                    callback_data="start_first_question",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Выбрать другое направление",
                    callback_data="start_choose_track",
                )
            ],
        ]
    )


# -------------------------------------------------------------
# СТАРТ И ВОССТАНОВЛЕНИЕ ПРОГРЕССА
# -------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return

    user = await storage.get_user(message.from_user.id, message.from_user.username)

    if user.get("track") and not user.get("finished", False) and user.get("current_question_index", 0) > 0:
        await state.set_state(InterviewStates.waiting_answer)
        await message.answer(
            "Продолжаем собеседование с того места, где вы остановились 👇",
            reply_markup=get_interview_reply_keyboard(),
        )
        await ask_current_question(message, state, message.from_user.id)
        return

    await state.clear()
    await state.set_state(InterviewStates.welcome)

    welcome_text = (
        "👋 <b>Привет! Я AI-тренажёр для подготовки к IT-собеседованиям.</b>\n\n"
        "Я помогу вам проверить уровень знаний, научиться отвечать уверенно и без воды, "
        "а также подготовлю к сложным техническим и поведенческим вопросам.\n\n"
        "Готовы проверить себя?"
    )

    await message.answer(
        welcome_text,
        reply_markup=get_idle_reply_keyboard(),
        parse_mode="HTML",
    )
    await message.answer(
        "Нажмите кнопку ниже, чтобы выбрать направление:",
        reply_markup=get_welcome_inline_keyboard(),
    )


@router.message(Command("continue"))
@router.message(F.text == "▶️ Продолжить")
async def cmd_continue(message: Message, state: FSMContext) -> None:
    """Восстанавливает активное собеседование из файла users.json после перезапуска бота."""
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
    await message.answer(
        "🔄 <b>Собеседование возобновлено!</b>\n"
        "Вы продолжаете ровно с того места, где остановились.",
        reply_markup=get_interview_reply_keyboard(),
        parse_mode="HTML",
    )
    await ask_current_question(message, state, message.from_user.id)


@router.message(Command("reset"))
@router.message(F.text.in_(["🔄 Начать заново", "🔄 Заново"]))
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


@router.message(Command("prem"))
async def cmd_prem(message: Message, state: FSMContext) -> None:
    """Предлагает сразу оформить полный доступ к собеседованию."""
    if not message.from_user:
        return

    user = await storage.get_user(message.from_user.id, message.from_user.username)
    if user.get("paid"):
        await message.answer("✅ У вас уже оформлен полный доступ ко всем вопросам!")
        return

    from handlers.interview import get_pay_inline_keyboard

    await state.set_state(InterviewStates.waiting_payment)
    await message.answer(
        "💎 <b>Полный доступ к IT-собеседованию</b>\n\n"
        "Открывает все 15 вопросов, детальную рецензию каждого ответа, "
        "итоговую оценку Hard & Soft Skills и модуль генерации резюме.\n\n"
        "Выберите удобный способ оплаты:",
        reply_markup=get_pay_inline_keyboard(),
        parse_mode="HTML",
    )


@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message) -> None:
    help_text = (
        "ℹ️ <b>Памятка по работе с ботом:</b>\n\n"
        "• <b>▶️ Продолжить</b> (/continue) — восстановить собеседование, если бот перезагружался.\n"
        "• <b>💎 Полный доступ</b> (/prem) — оплатить полный доступ ко всем 15 вопросам.\n"
        "• <b>🔄 Начать заново</b> (/reset) — сбросить текущие ответы и выбрать другое направление.\n"
        "• <b>💬 Поддержка</b> (/support) — задать вопрос разработчикам.\n"
        "• <b>/report</b> — запросить итоговый отчёт по собеседованию.\n\n"
        "💡 <i>Совет: отвечайте подробно, приводите примеры архитектурных решений и стека.</i>"
    )
    await message.answer(help_text, parse_mode="HTML")


# -------------------------------------------------------------
# ТЕХНИЧЕСКАЯ ПОДДЕРЖКА
# -------------------------------------------------------------

@router.message(F.text == "💬 Поддержка")
@router.message(Command("support"))
async def cmd_support(message: Message, state: FSMContext) -> None:
    await state.set_state(SupportStates.waiting_support_message)
    await message.answer(
        "✍️ <b>Служба поддержки</b>\n\n"
        "Опишите ваш вопрос или проблему одним сообщением.\n"
        "Мы сразу передадим его администраторам бота.",
        parse_mode="HTML",
    )


@router.message(SupportStates.waiting_support_message)
async def process_support_message(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user or not message.text:
        await message.answer("Пожалуйста, отправьте текстовое сообщение с описанием проблемы.")
        return

    if message.text in ["🔄 Начать заново", "🔄 Заново", "❓ Помощь", "💬 Поддержка", "▶️ Продолжить"]:
        await state.clear()
        if message.text in ["🔄 Начать заново", "🔄 Заново"]:
            await cmd_reset(message, state)
        elif message.text == "❓ Помощь":
            await cmd_help(message)
        elif message.text == "▶️ Продолжить":
            await cmd_continue(message, state)
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
            [
                InlineKeyboardButton(
                    text="💬 Написать в личку",
                    url=f"https://t.me/{message.from_user.username}",
                )
            ]
        )

    reply_kb = InlineKeyboardMarkup(inline_keyboard=inline_buttons)

    from handlers.admin import AUTHENTICATED_ADMINS
    target_admins = set(ADMIN_IDS).union(AUTHENTICATED_ADMINS)

    sent_count = 0
    for admin_id in target_admins:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=admin_alert,
                reply_markup=reply_kb,
                parse_mode="HTML",
            )
            sent_count += 1
        except Exception as e:
            logger.error("Не удалось доставить сообщение поддержки админу %s: %s", admin_id, e)

    logger.info("Обращение поддержки от %s доставлено %d администраторам", message.from_user.id, sent_count)

    user = await storage.get_user(message.from_user.id)
    kb = get_interview_reply_keyboard() if (user.get("track") and not user.get("finished")) else get_idle_reply_keyboard()

    await state.clear()
    await message.answer(
        "✅ <b>Ваше сообщение передано администраторам!</b>\n\n"
        "Мы свяжемся с вами в ближайшее время.",
        reply_markup=kb,
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
        "<i>Вопросы и технические кейсы будут подобраны строго под ваш стек.</i>"
    )

    if callback.message:
        await callback.message.edit_text(
            choose_text,
            reply_markup=get_tracks_keyboard(),
            parse_mode="HTML",
        )
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
        f"• Первые <b>{FREE_QUESTIONS_COUNT} вопроса доступны бесплатно</b>, чтобы оценить формат.\n"
        "• На каждый ваш ответ AI-интервьюер даёт <b>разбор</b>: сильные стороны, точки роста и рекомендации.\n"
        "• В финале формируется <b>комплексный отчёт</b> компетенций + помощь с резюме.\n\n"
        "💡 <b>Главное правило успеха:</b>\n"
        "Отвечайте <b>максимально развёрнуто</b>. Используйте профессиональные термины, "
        "объясняйте логику решений и приводите примеры. Односложные ответы ('да', 'знаю', 'делал') "
        "не позволят раскрыть ваш реальный грейд!"
    )

    if callback.message:
        await callback.message.edit_text(
            rules_text,
            reply_markup=get_ready_keyboard(),
            parse_mode="HTML",
        )
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
# ТЕСТОВЫЕ РЕЖИМЫ (/test, /testpay)
# -------------------------------------------------------------

@router.message(Command("test"))
async def cmd_test_mode(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return

    await storage.mark_paid(message.from_user.id)
    user = await storage.get_user(message.from_user.id)
    current_state = await state.get_state()

    await message.answer(
        "🧪 <b>Тестовый режим включён!</b>\n\n"
        "Ограничения по оплате сняты (<code>paid = True</code>).",
        parse_mode="HTML",
    )

    if current_state == InterviewStates.waiting_payment:
        await state.set_state(InterviewStates.waiting_answer)
        await ask_current_question(message, state, message.from_user.id)
    elif not user.get("track"):
        await state.set_state(InterviewStates.selecting_track)
        await message.answer("Выберите направление:", reply_markup=get_tracks_keyboard(), parse_mode="HTML")


@router.message(Command("testpay"))
async def cmd_testpay(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return

    await storage.revoke_paid(message.from_user.id)

    user = await storage.get_user(message.from_user.id)
    user["paid"] = False
    user["current_question_index"] = FREE_QUESTIONS_COUNT
    await storage.save_user(message.from_user.id, user)

    from handlers.interview import get_pay_inline_keyboard

    await state.set_state(InterviewStates.waiting_payment)
    await message.answer(
        "🧪 <b>Тест оплаты активирован!</b>\n\n"
        "Статус оплаты сброшен (<code>paid = False</code>), а прогресс выставлен на окончание бесплатного лимита.\n\n"
        "Проверьте выставление счёта и оплату по кнопке ниже 👇",
        reply_markup=get_pay_inline_keyboard(),
        parse_mode="HTML",
    )


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

    text = (
        f"❓ <b>Вопрос {index + 1} из {TOTAL_QUESTIONS}</b> "
        f"<i>[{question['category'].upper()}]</i>\n\n"
        f"{question['text']}\n\n"
        f"✍️ <i>Совет: отвечайте развёрнуто, делитесь деталями и примерами из практики.</i>"
    )

    await message.bot.send_message(
        chat_id=uid,
        text=text,
        reply_markup=get_interview_reply_keyboard(),
        parse_mode="HTML",
    )


# -------------------------------------------------------------
# АВТОПОДХВАТ СЕССИИ ПОСЛЕ РЕСТАРТА (CATCH-ALL)
# -------------------------------------------------------------

@router.message(F.text)
async def process_unhandled_text(message: Message, state: FSMContext) -> None:
    """Если бот перезагрузился и потерял FSM, восстанавливаем диалог по сохранённым данным."""
    if not message.from_user:
        return

    user = await storage.get_user(message.from_user.id)
    track = user.get("track")
    finished = user.get("finished", False)
    index = user.get("current_question_index", 0)

    # Если собеседование начато, но стейт сбросился из-за перезапуска бота
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
                "⚠️ Вы находитесь на этапе собеседования.\n"
                "Ответьте на вопрос текстом или нажмите <b>🔄 Начать заново</b>.",
                parse_mode="HTML",
            )
            return

        await state.set_state(InterviewStates.waiting_answer)
        from handlers.interview import handle_answer
        await handle_answer(message, state)
        return

    kb = get_idle_reply_keyboard()
    await message.answer(
        "❓ <b>Команда не распознана.</b>\n\n"
        "Используйте кнопки меню или команды:\n"
        "• /continue — продолжить собеседование\n"
        "• /prem — оформить полный доступ\n"
        "• /start — главное меню\n"
        "• /reset — начать заново\n"
        "• /support — техподдержка",
        reply_markup=kb,
        parse_mode="HTML",
    )