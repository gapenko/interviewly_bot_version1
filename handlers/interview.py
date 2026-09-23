"""
handlers/interview.py — проведение мок-интервью:
- Подсказки ментора, пропуск вопроса, безопасный перезапуск и досрочный финал.
- Валидация развернутых ответов кандидата.
- Живая реакция ментора на каждый ответ с учётом контекста разговора (llm_service.mentor_reply).
- Генерация итогового отчета и компиляция Word-файла (.docx).

Как устроено «живое» общение:
- реплика ментора приходит отдельным сообщением без заголовков и шаблонных блоков;
- следующий вопрос приходит вторым сообщением после короткой паузы с индикатором «печатает…»;
- оценка 1–10 по каждому ответу сохраняется, но по умолчанию не показывается
  (включается переменной SHOW_ANSWER_SCORE=1 в .env).

ВАЖНО: выбор направления (callback_data="track_*") и подтверждение сброса прогресса
(callback_data="cmd_reset_confirm") обрабатываются в handlers/start.py и handlers/resume.py.
"""
import asyncio
import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from config import FREE_QUESTIONS_COUNT, HUMAN_TYPING_DELAY, SHOW_ANSWER_SCORE
from docx_service import create_candidate_docx
from keyboards import (
    get_finished_keyboard,
    get_interview_toolbar,
    get_paywall_keyboard,
    get_reset_confirm_keyboard,
)
from llm_service import generate_final_report, generate_hint, generate_resume_draft, mentor_reply
from questions import TOTAL_QUESTIONS, TRACKS, get_question
from states import InterviewStates
from storage import get_user, save_user, user_has_track_access
from ui_utils import (
    build_question_message,
    early_finish_reply,
    finished_reply,
    first_name,
    non_text_reply,
    short_answer_reply,
    skip_reply,
    typing_delay_for,
)

logger = logging.getLogger(__name__)
router = Router(name="interview")

TELEGRAM_TEXT_LIMIT = 4000


def get_pay_inline_keyboard() -> InlineKeyboardMarkup:
    return get_paywall_keyboard()


# =========================================================
# ВСПОМОГАТЕЛЬНОЕ: безопасная отправка и «человеческий» ритм
# =========================================================

def _split_message(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Режет длинный текст на части по абзацам, чтобы не упереться в лимит Telegram (4096 символов)."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


async def _send_html(message: Message, text: str, **kwargs) -> Message | None:
    """
    Отправляет текст с HTML-разметкой. Если Telegram не смог разобрать разметку
    (например, модель вернула кривой тег), отправляет тот же текст без разметки,
    вместо того чтобы молча «съесть» ответ ментора.
    Клавиатура (reply_markup) прикрепляется к последней части сообщения.
    """
    chunks = _split_message(text)
    sent = None
    for i, chunk in enumerate(chunks):
        extra = kwargs if i == len(chunks) - 1 else {}
        try:
            sent = await message.answer(chunk, parse_mode="HTML", **extra)
        except TelegramBadRequest as e:
            logger.warning("Не удалось разобрать HTML (%s), отправляю без разметки", e)
            plain = html.unescape(re.sub(r"<[^>]+>", "", chunk))
            sent = await message.answer(plain, parse_mode=None, **extra)
    return sent


async def _typing_pause(bot: Bot, chat_id: int, upcoming_text: str) -> None:
    """Пауза с индикатором «печатает…» перед следующим сообщением — как в живой переписке."""
    if not HUMAN_TYPING_DELAY:
        return
    async with ChatActionSender.typing(bot=bot, chat_id=chat_id):
        await asyncio.sleep(typing_delay_for(upcoming_text))


def _candidate_name(user_data: dict) -> str | None:
    return first_name(user_data.get("full_name"))


def _track_title(track: str | None) -> str:
    return TRACKS.get(track or "", "IT")


def needs_payment(user_data: dict, question_index: int) -> bool:
    """Нужна ли оплата, чтобы получить/принять вопрос с этим индексом."""
    track = user_data.get("track")
    return question_index >= FREE_QUESTIONS_COUNT and not user_has_track_access(user_data, track)


async def show_paywall(message: Message, state: FSMContext, track: str | None) -> None:
    """Экран оплаты для конкретного направления."""
    await state.set_state(InterviewStates.waiting_payment)
    track_title = TRACKS.get(track or "", "выбранному направлению")
    paywall_text = (
        "⭐️ <b>На этом бесплатная часть собеседования заканчивается.</b>\n\n"
        f"Чтобы продолжить разговор и пройти углублённые вопросы по направлению «<b>{track_title}</b>», "
        "получить итоговый разбор компетенций и персональный Word-отчёт (.docx) с черновиком резюме, "
        "откройте полный доступ к этому направлению."
    )
    await message.answer(paywall_text, reply_markup=get_paywall_keyboard(), parse_mode="HTML")


# =========================================================
# ДЕЙСТВИЯ ПОД ВОПРОСОМ (INLINE)
# =========================================================

@router.callback_query(F.data == "cmd_hint")
async def process_hint_callback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подсказка ментора к конкретному вопросу без перехода к следующему."""
    await callback.answer()
    user_data = await get_user(callback.from_user.id)
    track = user_data.get("track", "backend")
    idx = user_data.get("current_question_index", 0)
    q = get_question(track, idx)

    async with ChatActionSender.typing(bot=bot, chat_id=callback.message.chat.id):
        hint = await generate_hint(q["text"], _track_title(track))

    if not hint:
        # Запасной вариант, если LLM недоступен
        hint = (
            "Попробуйте зайти так: сначала коротко объясните суть, потом покажите на примере из своей практики, "
            "а в конце скажите, какие были компромиссы и что бы вы сделали иначе. "
            "Интервьюеру важнее ход мысли, чем идеальная формулировка."
        )

    await _send_html(callback.message, hint)


@router.callback_query(F.data == "cmd_skip_question")
async def process_skip_callback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Пропуск текущего вопроса через инлайн-кнопку."""
    await callback.answer()
    user_data = await get_user(callback.from_user.id)
    current_index = user_data.get("current_question_index", 0)
    track = user_data.get("track", "backend")

    # Кнопка «Пропустить» под старым сообщением не должна проводить мимо пейволла
    if needs_payment(user_data, current_index):
        await show_paywall(callback.message, state, track)
        return

    question = get_question(track, current_index)

    user_data["answers"].append({
        "q_id": question["id"],
        "question_text": question["text"],
        "answer": "[Вопрос пропущен кандидатом]",
        "feedback": "Кандидат решил пропустить данный вопрос.",
        "score": None,
    })
    user_data["current_question_index"] = current_index + 1
    await save_user(callback.from_user.id, user_data)

    await callback.message.answer(skip_reply())
    await proceed_after_answer(callback.message, state, bot, callback.from_user.id)


@router.callback_query(F.data == "cmd_reset_prompt")
async def process_reset_prompt(callback: CallbackQuery):
    """Запрос подтверждения перезапуска (сама кнопка подтверждения обрабатывается в handlers/resume.py)."""
    await callback.answer()
    await callback.message.answer(
        "Точно начинаем заново? Все ответы этой сессии сотрутся, и собеседование пойдёт с первого вопроса.",
        reply_markup=get_reset_confirm_keyboard(),
    )


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
        await callback.message.answer(
            "Мы ещё не успели обсудить ни одного вопроса — подводить итоги пока не по чему. "
            "Возвращайтесь, когда будете готовы: /start"
        )
        await state.clear()
        return

    await finalize_interview(callback.message, state, bot, callback.from_user.id, early=True)


# =========================================================
# ОБРАБОТКА ТЕКСТОВОГО ОТВЕТА КАНДИДАТА
# =========================================================

@router.message(InterviewStates.waiting_answer, ~F.text)
async def handle_non_text(message: Message):
    await message.reply(non_text_reply())


@router.message(InterviewStates.waiting_answer, F.text)
async def handle_text_answer(message: Message, state: FSMContext, bot: Bot):
    text = message.text.strip()
    if len(text) < 15:
        await message.reply(short_answer_reply())
        return

    user_data = await get_user(message.from_user.id)
    current_index = user_data.get("current_question_index", 0)
    track = user_data.get("track", "backend")

    # Защита пейволла: ответ на платный вопрос без доступа не принимаем, из какой бы точки
    # пользователь сюда ни попал (/start, catch-all, старое сообщение и т.п.)
    if needs_payment(user_data, current_index):
        await show_paywall(message, state, track)
        return

    question = get_question(track, current_index)

    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        reply = await mentor_reply(
            question["text"],
            text,
            track_title=_track_title(track),
            candidate_name=_candidate_name(user_data) or first_name(message.from_user.full_name),
            question_number=current_index + 1,
            total_questions=TOTAL_QUESTIONS,
            history=user_data.get("answers", []),
        )

    user_data["answers"].append({
        "q_id": question["id"],
        "question_text": question["text"],
        "answer": text,
        "feedback": reply.text,
        "score": reply.score,
    })
    user_data["current_question_index"] = current_index + 1
    await save_user(message.from_user.id, user_data)

    reply_text = reply.text
    if SHOW_ANSWER_SCORE and reply.score:
        reply_text += f"\n\n<i>Оценка ответа: {reply.score}/10</i>"

    await _send_html(message, reply_text)
    await proceed_after_answer(message, state, bot, message.from_user.id)


async def proceed_after_answer(message: Message, state: FSMContext, bot: Bot, user_id: int):
    """Проверяет переход к следующему вопросу, пейволлу или финалу."""
    user_data = await get_user(user_id)
    next_index = user_data["current_question_index"]
    track = user_data.get("track", "backend")

    # Пейволл после бесплатных вопросов.
    # Доступ проверяется ПО КОНКРЕТНОМУ НАПРАВЛЕНИЮ: оплата другого направления
    # не открывает текущее, если у пользователя нет полного безлимитного доступа.
    # Условие ">=" (а не "=="), чтобы пейволл нельзя было «перепрыгнуть».
    if next_index < TOTAL_QUESTIONS and needs_payment(user_data, next_index):
        await show_paywall(message, state, track)
        return

    # Финал: все вопросы отвечены
    if next_index >= TOTAL_QUESTIONS:
        await finalize_interview(message, state, bot, user_id)
        return

    # Подача следующего вопроса — отдельным сообщением, после короткой «печатающей» паузы
    next_question = get_question(track, next_index)
    prev_question = get_question(track, next_index - 1) if next_index > 0 else None
    text = build_question_message(
        next_question,
        next_index,
        mode="next",
        prev_question=prev_question,
    )
    await _typing_pause(bot, message.chat.id, text)
    await _send_html(message, text, reply_markup=get_interview_toolbar())


async def finalize_interview(message: Message, state: FSMContext, bot: Bot, user_id: int, early: bool = False):
    """Генерация итогового отчета и компиляция .docx файла."""
    await state.set_state(InterviewStates.finished)
    user_data = await get_user(user_id)
    user_data["finished"] = True
    await save_user(user_id, user_data)

    name = _candidate_name(user_data)
    await message.answer(early_finish_reply() if early else finished_reply(name))

    track = user_data.get("track", "backend")
    track_title = TRACKS.get(track, "Backend-разработка")
    answers = user_data.get("answers", [])
    qa_pairs = [
        {"question": a["question_text"], "answer": a["answer"], "score": a.get("score")}
        for a in answers
    ]

    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        final_report, resume_draft = await asyncio.gather(
            generate_final_report(qa_pairs, track_title),
            generate_resume_draft(qa_pairs, track_title),
        )
        # Имя берём из профиля пользователя, а не из message.from_user:
        # в колбэках (кнопка «Завершить») message.from_user — это сам бот.
        doc_name = user_data.get("username") or name or "Candidate"
        docx_file = create_candidate_docx(doc_name, final_report, resume_draft, answers)

    await _send_html(message, final_report)

    input_file = BufferedInputFile(docx_file.getvalue(), filename=f"Resume_{doc_name}.docx")
    await message.answer_document(
        document=input_file,
        caption="📄 Здесь полный разбор, стенограмма нашего разговора и черновик резюме — удобно сохранить себе.",
        reply_markup=get_finished_keyboard(),
    )


# Экспорт для восстановления после рестарта
async def send_final_report(message: Message, state: FSMContext, user_id: int):
    await finalize_interview(message, state, message.bot, user_id)
