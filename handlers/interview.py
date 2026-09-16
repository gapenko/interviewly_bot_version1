"""
handlers/interview.py — выбор направления, проведение интервью, пейвол, финал и пропуск вопросов.
"""
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.chat_action import ChatActionSender

from states import InterviewStates
from storage import get_user, save_user
from config import FREE_QUESTIONS_COUNT
from questions import get_question, TOTAL_QUESTIONS, TRACKS
from ui_utils import format_question_card
from keyboards import (
    get_interview_toolbar,
    get_reset_confirm_keyboard,
    get_finished_keyboard,
)
from llm_service import process_answer, generate_final_report, generate_resume_draft
from docx_service import create_candidate_docx

router = Router(name="interview")


def get_pay_inline_keyboard():
    """Совместимость для handlers/start.py."""
    from handlers.payment import get_paywall_inline_keyboard
    return get_paywall_inline_keyboard()


async def send_next_question(message: Message, state: FSMContext, user_id: int):
    """Отправляет текущий активный вопрос пользователю."""
    user_data = await get_user(user_id)
    current_index = user_data.get("current_question_index", 0)
    track = user_data.get("track", "backend")

    if current_index >= TOTAL_QUESTIONS:
        await send_final_report(message, state, user_id)
        return

    question = get_question(track, current_index)
    card = format_question_card(question, current_index)
    await message.bot.send_message(
        chat_id=user_id,
        text=card,
        reply_markup=get_interview_toolbar(),
        parse_mode="HTML"
    )


async def send_final_report(message: Message, state: FSMContext, user_id: int):
    """Формирует итоговые отчеты и выдает .docx."""
    await state.set_state(InterviewStates.finished)
    user_data = await get_user(user_id)
    user_data["finished"] = True
    await save_user(user_id, user_data)

    status_msg = await message.bot.send_message(
        chat_id=user_id,
        text="🎉 <b>Интервью успешно завершено!</b>\n\n⏳ <i>Ментор формирует итоговый карьерный профиль и компилирует файл резюме (.docx)...</i>",
        parse_mode="HTML"
    )

    track = user_data.get("track", "backend")
    track_title = TRACKS.get(track, "Backend-разработка")
    qa_pairs = [{"question": a["question_text"], "answer": a["answer"]} for a in user_data["answers"]]

    async with ChatActionSender.upload_document(bot=message.bot, chat_id=user_id):
        final_report = await generate_final_report(qa_pairs, track_title)
        resume_draft = await generate_resume_draft(qa_pairs, track_title)
        username = message.from_user.username if message.from_user else "Candidate"
        docx_file = create_candidate_docx(username, final_report, resume_draft, user_data["answers"])

    await message.bot.send_message(chat_id=user_id, text=final_report, parse_mode="HTML")

    input_file = BufferedInputFile(docx_file.getvalue(), filename=f"Resume_{username}.docx")
    await message.bot.send_document(
        chat_id=user_id,
        document=input_file,
        caption="📄 <b>Ваш персональный карьерный отчет и черновик резюме готовы!</b>",
        reply_markup=get_finished_keyboard(),
        parse_mode="HTML",
    )
    try:
        await status_msg.delete()
    except Exception:
        pass


# 1. Выбор специальности
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
        f"🎯 Выбрано направление: <b>{track_title}</b>\nНачинаем ассессмент!",
        parse_mode="HTML"
    )
    await send_next_question(callback.message, state, callback.from_user.id)


@router.callback_query(F.data == "cmd_help")
async def process_help_callback(callback: CallbackQuery):
    await callback.answer()
    help_text = (
        "⚙️ <b>Справка по прохождению</b>\n\n"
        "• Отвечайте развернутым текстовым сообщением.\n"
        "• Приводите технические аргументы и стек.\n\n"
        "<i>Интервью ожидает вашего ответа на текущий вопрос.</i>"
    )
    await callback.message.answer(help_text, parse_mode="HTML")


@router.callback_query(F.data == "cmd_reset_prompt")
async def process_reset_prompt(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "⚠️ Сбросить текущий прогресс собеседования?",
        reply_markup=get_reset_confirm_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "cmd_resume")
async def process_resume(callback: CallbackQuery):
    await callback.answer("Продолжаем")
    await callback.message.delete()


# 2. Пропуск вопроса
@router.message(InterviewStates.waiting_answer, Command("skipvopros"))
async def handle_skip_question(message: Message, state: FSMContext):
    user_data = await get_user(message.from_user.id)
    current_index = user_data.get("current_question_index", 0)
    track = user_data.get("track", "backend")
    question = get_question(track, current_index)

    user_data["answers"].append({
        "q_id": question["id"],
        "question_text": question["text"],
        "answer": "[Вопрос пропущен пользователем]",
        "feedback": "Вопрос пропущен без оценки.",
    })
    user_data["current_question_index"] = current_index + 1
    await save_user(message.from_user.id, user_data)

    next_index = user_data["current_question_index"]
    await message.answer(f"⏩ <i>Вопрос {current_index + 1} пропущен.</i>", parse_mode="HTML")

    if next_index == FREE_QUESTIONS_COUNT and not user_data.get("paid"):
        await state.set_state(InterviewStates.waiting_payment)
        from handlers.payment import send_paywall
        await send_paywall(message, message.from_user.id)
        return

    if next_index >= TOTAL_QUESTIONS:
        await send_final_report(message, state, message.from_user.id)
        return

    await send_next_question(message, state, message.from_user.id)


@router.message(InterviewStates.waiting_answer, ~F.text)
async def handle_non_text(message: Message):
    await message.reply("⚠️ Пожалуйста, отправьте ваш ответ развернутым текстом.", parse_mode="HTML")


# 3. Обработка ответа
@router.message(InterviewStates.waiting_answer, F.text)
async def handle_text_answer(message: Message, state: FSMContext, bot: Bot):
    text = message.text.strip()
    if len(text) < 15:
        await message.reply("⚠️ Ответ слишком короткий. Раскройте мысль подробнее.", parse_mode="HTML")
        return

    user_data = await get_user(message.from_user.id)
    current_index = user_data.get("current_question_index", 0)
    track = user_data.get("track", "backend")
    question = get_question(track, current_index)

    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        ai_feedback = await process_answer(question["text"], text)

    user_data["answers"].append({
        "q_id": question["id"],
        "question_text": question["text"],
        "answer": text,
        "feedback": ai_feedback,
    })
    user_data["current_question_index"] = current_index + 1
    await save_user(message.from_user.id, user_data)

    await message.answer(f"<b>Разбор ответа:</b>\n\n{ai_feedback}", parse_mode="HTML")

    next_index = user_data["current_question_index"]

    if next_index == FREE_QUESTIONS_COUNT and not user_data.get("paid"):
        await state.set_state(InterviewStates.waiting_payment)
        from handlers.payment import send_paywall
        await send_paywall(message, message.from_user.id)
        return

    if next_index >= TOTAL_QUESTIONS:
        await send_final_report(message, state, message.from_user.id)
        return

    await send_next_question(message, state, message.from_user.id)