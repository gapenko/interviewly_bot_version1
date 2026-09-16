"""
handlers/interview.py — проведение мок-интервью:
- Подсказки ментора, пропуск вопроса, безопасный перезапуск и досрочный финал.
- Валидация развернутых ответов кандидата.
- Обработка в реальном времени через Claude 3.5 Sonnet (llm_service.py).
- Генерация итогового отчета и компиляция Word-файла (.docx).
"""
import logging
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from config import FREE_QUESTIONS_COUNT
from docx_service import create_candidate_docx
from keyboards import (
    get_finished_keyboard,
    get_interview_toolbar,
    get_paywall_keyboard,
    get_reset_confirm_keyboard,
    remove_reply_kb,
)
from llm_service import generate_final_report, generate_resume_draft, process_answer
from questions import TOTAL_QUESTIONS, TRACKS, get_question
from states import InterviewStates
from storage import get_user, save_user
from ui_utils import format_question_card

logger = logging.getLogger(__name__)
router = Router(name="interview")


def get_pay_inline_keyboard() -> InlineKeyboardMarkup:
    return get_paywall_keyboard()


# 1. Выбор направления
@router.callback_query(F.data.startswith("track_"))
async def process_track_selection(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    track_key = callback.data.replace("track_", "")
    track_title = TRACKS.get(track_key, "Backend-разработка")

    user_data = await get_user(callback.from_user.id)
    user_data["track"] = track_key
    user_data["current_question_index"] = 0
    user_data["answers"] = []
    user_data["finished"] = False
    await save_user(callback.from_user.id, user_data)

    await state.set_state(InterviewStates.waiting_answer)
    await callback.message.answer(
        f"🎯 Выбрано направление: <b>{track_title}</b>\n"
        f"Интервью состоит из {TOTAL_QUESTIONS} вопросов. Начинаем!",
        reply_markup=remove_reply_kb,
        parse_mode="HTML",
    )

    first_question = get_question(track_key, 0)
    card = format_question_card(first_question, 0)
    await callback.message.answer(card, reply_markup=get_interview_toolbar(), parse_mode="HTML")


# --- ДЕЙСТВИЯ ПОД ВОПРОСОМ (INLINE) ---

@router.callback_query(F.data == "cmd_hint")
async def process_hint_callback(callback: CallbackQuery, state: FSMContext):
    """Подсказка ментора без перехода к следующему вопросу."""
    await callback.answer()
    user_data = await get_user(callback.from_user.id)
    track = user_data.get("track", "backend")
    idx = user_data.get("current_question_index", 0)
    q = get_question(track, idx)

    hint_text = (
        f"💡 <b>Подсказка ментора к вопросу {idx + 1}:</b>\n\n"
        f"• Категория: <code>{q.get('category', '').upper()}</code>\n"
        f"• Раскройте фундаментальный принцип и архитектурную логику.\n"
        f"• Приведите 1 конкретный пример из вашего реального продакшн-опыта.\n"
        f"• Обязательно укажите, какие были риски, компромиссы (trade-offs) и как вы их решили.\n\n"
        f"<i>Теперь напишите ваш развёрнутый ответ в чат ниже:</i>"
    )
    await callback.message.answer(hint_text, parse_mode="HTML")


@router.callback_query(F.data == "cmd_skip_question")
async def process_skip_callback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Пропуск текущего вопроса через инлайн-кнопку."""
    await callback.answer("Вопрос пропущен")
    user_data = await get_user(callback.from_user.id)
    current_index = user_data.get("current_question_index", 0)
    track = user_data.get("track", "backend")
    question = get_question(track, current_index)

    user_data["answers"].append({
        "q_id": question["id"],
        "question_text": question["text"],
        "answer": "[Вопрос пропущен кандидатом]",
        "feedback": "Кандидат решил пропустить данный вопрос.",
    })
    user_data["current_question_index"] = current_index + 1
    await save_user(callback.from_user.id, user_data)

    await callback.message.answer(f"⏩ <i>Вопрос {current_index + 1} пропущен.</i>", parse_mode="HTML")
    await proceed_after_answer(callback.message, state, bot, callback.from_user.id)


@router.callback_query(F.data == "cmd_reset_prompt")
async def process_reset_prompt(callback: CallbackQuery):
    """Запрос подтверждения перезапуска."""
    await callback.answer()
    await callback.message.answer(
        "⚠️ <b>Внимание:</b> Вы собираетесь начать интервью заново. Все ответы текущей сессии будут сброшены.\n\nВы уверены?",
        reply_markup=get_reset_confirm_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "cmd_reset_confirm")
async def process_reset_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждённый сброс прогресса и перезапуск."""
    await callback.answer("Прогресс сброшен")
    user_data = await get_user(callback.from_user.id)
    user_data["current_question_index"] = 0
    user_data["answers"] = []
    user_data["finished"] = False
    await save_user(callback.from_user.id, user_data)

    await state.set_state(InterviewStates.waiting_answer)
    track = user_data.get("track", "backend")
    await callback.message.answer("🔄 <b>Прогресс сброшен. Начинаем с 1-го вопроса!</b>", parse_mode="HTML")

    first_question = get_question(track, 0)
    card = format_question_card(first_question, 0)
    await callback.message.answer(card, reply_markup=get_interview_toolbar(), parse_mode="HTML")


@router.callback_query(F.data == "cmd_resume")
async def process_resume(callback: CallbackQuery):
    await callback.answer("Продолжаем")
    await callback.message.delete()


@router.callback_query(F.data == "cmd_finish_early")
async def process_finish_early(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Досрочное завершение интервью по кнопке кандидата."""
    await callback.answer()
    user_data = await get_user(callback.from_user.id)

    if not user_data.get("answers"):
        await callback.message.answer("Вы ещё не ответили ни на один вопрос. Собеседование отменено.")
        await state.clear()
        return

    await callback.message.answer(
        "🛑 <b>Собеседование завершено досрочно по вашему запросу.</b>\n"
        "Формирую отчёт на основе тех вопросов, на которые вы успели ответить...",
        parse_mode="HTML",
    )
    await finalize_interview(callback.message, state, bot, callback.from_user.id)


# --- ОБРАБОТКА ТЕКСТОВОГО ОТВЕТА КАНДИДАТА ---

@router.message(InterviewStates.waiting_answer, ~F.text)
async def handle_non_text(message: Message):
    await message.reply(
        "⚠️ <b>Формат не поддерживается.</b>\n"
        "Пожалуйста, отправьте ваш ответ развёрнутым текстом.",
        parse_mode="HTML",
    )


@router.message(InterviewStates.waiting_answer, F.text)
async def handle_text_answer(message: Message, state: FSMContext, bot: Bot):
    text = message.text.strip()
    if len(text) < 15:
        await message.reply(
            "⚠️ <b>Слишком короткий ответ.</b>\n"
            "Пожалуйста, раскройте вашу мысль подробнее, укажите технологии и аргументируйте технический подход.",
            parse_mode="HTML",
        )
        return

    user_data = await get_user(message.from_user.id)
    current_index = user_data.get("current_question_index", 0)
    track = user_data.get("track", "backend")
    question = get_question(track, current_index)

    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        try:
            ai_feedback = await process_answer(question["text"], text)
        except Exception as e:
            logger.error("Ошибка process_answer: %s", e)
            ai_feedback = "<i>Разбор зафиксирован. Детальная оценка будет сформирована в итоговом резюме.</i>"

    user_data["answers"].append({
        "q_id": question["id"],
        "question_text": question["text"],
        "answer": text,
        "feedback": ai_feedback,
    })
    user_data["current_question_index"] = current_index + 1
    await save_user(message.from_user.id, user_data)

    await message.answer(f"<b>Анализ ответа ментором:</b>\n\n{ai_feedback}", parse_mode="HTML")
    await proceed_after_answer(message, state, bot, message.from_user.id)


async def proceed_after_answer(message: Message, state: FSMContext, bot: Bot, user_id: int):
    """Проверяет переход к следующему вопросу, пейволлу или финалу."""
    user_data = await get_user(user_id)
    next_index = user_data["current_question_index"]
    track = user_data.get("track", "backend")

    # Пейволл после бесплатных вопросов
    if next_index == FREE_QUESTIONS_COUNT and not user_data.get("paid"):
        await state.set_state(InterviewStates.waiting_payment)
        paywall_text = (
            "⭐️ <b>Базовый блок успешно завершён!</b>\n\n"
            "Вы отлично справляетесь! Чтобы открыть доступ к углубленным архитектурным вопросам, "
            "получить аудит компетенций по 15 критериям и сгенерировать персональный Word-отчёт (.docx), "
            "разблокируйте полный доступ."
        )
        await message.answer(paywall_text, reply_markup=get_paywall_keyboard(), parse_mode="HTML")
        return

    # Финал: все 15 вопросов отвечены
    if next_index >= TOTAL_QUESTIONS:
        await finalize_interview(message, state, bot, user_id)
        return

    # Подача следующего вопроса
    next_question = get_question(track, next_index)
    card = format_question_card(next_question, next_index)
    await message.answer(card, reply_markup=get_interview_toolbar(), parse_mode="HTML")


async def finalize_interview(message: Message, state: FSMContext, bot: Bot, user_id: int):
    """Генерация итогового отчета Claude и компиляция .docx файла."""
    await state.set_state(InterviewStates.finished)
    user_data = await get_user(user_id)
    user_data["finished"] = True
    await save_user(user_id, user_data)

    status_msg = await message.answer(
        "🎉 <b>Интервью завершено!</b>\n\n"
        "⏳ <i>Ментор формирует итоговую карту компетенций и компилирует документ резюме (.docx)... Это займёт около 15 секунд.</i>",
        parse_mode="HTML",
    )

    track = user_data.get("track", "backend")
    track_title = TRACKS.get(track, "Backend-разработка")
    qa_pairs = [{"question": a["question_text"], "answer": a["answer"]} for a in user_data.get("answers", [])]

    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        final_report = await generate_final_report(qa_pairs, track_title)
        resume_draft = await generate_resume_draft(qa_pairs, track_title)
        username = message.from_user.username or message.from_user.first_name if message.from_user else "Candidate"
        docx_file = create_candidate_docx(username, final_report, resume_draft, user_data.get("answers", []))

    await message.answer(final_report, parse_mode="HTML")

    input_file = BufferedInputFile(docx_file.getvalue(), filename=f"Resume_{username}.docx")
    await message.answer_document(
        document=input_file,
        caption="📄 <b>Ваш персональный карьерный отчёт и черновик резюме готовы!</b>",
        reply_markup=get_finished_keyboard(),
        parse_mode="HTML",
    )

    try:
        await status_msg.delete()
    except Exception:
        pass


# Экспорт для восстановления после рестарта
async def send_final_report(message: Message, state: FSMContext, user_id: int):
    await finalize_interview(message, state, message.bot, user_id)