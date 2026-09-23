"""
handlers/interview.py — проведение собеседования.

Интерфейс «одного экрана»: во время собеседования в чате одно сообщение бота. После ответа
сообщение пользователя удаляется, а экран обновляется: сначала «ответ принят, интервьюер изучает…»,
затем комментарий к ответу (с самим ответом в свёрнутой цитате) и следующий вопрос.

Защита от гонок: ответы одного пользователя обрабатываются строго по одному (asyncio.Lock),
а запись ответа проверяет, что индекс вопроса не изменился (storage.append_answer).
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender

import storage
from config import FREE_QUESTIONS_COUNT, SHOW_ANSWER_SCORE
from docx_service import create_candidate_docx
from handlers.payment import paywall_view
from keyboards import back_to_question_kb, btn, finish_confirm_kb, finished_kb, ikb, interview_kb, reset_confirm_kb
from llm_service import generate_final_report, generate_hint, generate_resume_draft, interviewer_reply
from menus import main_menu_view, report_text, track_title, tracks_view
from questions import TOTAL_QUESTIONS, get_question
from screen import delete_user_message, esc, not_command, show_screen, truncate_plain
from states import InterviewStates
from ui_utils import (
    SEPARATOR,
    build_question_message,
    early_finish_reply,
    finished_reply,
    non_text_reply,
    short_answer_reply,
    skip_reply,
)

logger = logging.getLogger(__name__)
router = Router(name="interview")

MIN_ANSWER_LENGTH = 15
ANSWER_QUOTE_LIMIT = 600
SCREEN_SOFT_LIMIT = 3900

_locks: dict[int, asyncio.Lock] = {}


def _lock(uid: int) -> asyncio.Lock:
    return _locks.setdefault(uid, asyncio.Lock())


def _chat_id(callback: CallbackQuery) -> int:
    return callback.message.chat.id if callback.message else callback.from_user.id


def needs_payment(user_data: dict, question_index: int) -> bool:
    """Нужна ли оплата, чтобы получить/принять вопрос с этим индексом."""
    track = user_data.get("track")
    return question_index >= FREE_QUESTIONS_COUNT and not storage.user_has_track_access(user_data, track)


# =========================================================
# СБОРКА ЭКРАНА
# =========================================================

def feedback_block(ctx: dict, *, with_quote: bool = True, feedback_limit: int | None = None) -> str:
    """Комментарий интервьюера к последнему ответу (с самим ответом в свёрнутой цитате)."""
    feedback = ctx.get("fb_text") or ""
    if feedback_limit is not None:
        plain = re.sub(r"</?(b|i|code)>", "", feedback)
        feedback = truncate_plain(plain, feedback_limit)
    lines = [f"💬 <b>Комментарий к ответу на вопрос {ctx.get('fb_num')}</b>"]
    if with_quote and ctx.get("fb_answer"):
        quote = truncate_plain(esc(ctx["fb_answer"]), ANSWER_QUOTE_LIMIT)
        lines.append(f"<blockquote expandable>{quote}</blockquote>")
    lines.append(feedback)
    if SHOW_ANSWER_SCORE and ctx.get("fb_score"):
        lines.append(f"<i>Оценка ответа: {ctx['fb_score']}/10</i>")
    return "\n".join(lines)


def _compose(user: dict, ctx: dict, notice: str | None) -> str:
    track = user.get("track")
    idx = user.get("current_question_index", 0)
    question = get_question(track, idx)
    prev_question = get_question(track, idx - 1) if idx > 0 else None
    question_part = build_question_message(
        question, idx, mode=ctx.get("mode", "resume"), prev_question=prev_question, track_title=track_title(track)
    )

    def build(with_quote: bool = True, feedback_limit: int | None = None) -> str:
        parts = []
        if notice or ctx.get("notice"):
            parts.append(f"<i>{esc(notice or ctx.get('notice'))}</i>")
        if ctx.get("fb_text"):
            parts.append(feedback_block(ctx, with_quote=with_quote, feedback_limit=feedback_limit))
            parts.append(SEPARATOR)
        parts.append(question_part)
        if ctx.get("hint"):
            parts.append(f"💡 <b>Подсказка</b>\n{ctx['hint']}")
        return "\n\n".join(parts)

    text = build()
    if len(text) > SCREEN_SOFT_LIMIT:
        text = build(with_quote=False)
    if len(text) > SCREEN_SOFT_LIMIT:
        text = build(with_quote=False, feedback_limit=1200)
    return text


async def render_question(
    bot: Bot,
    chat_id: int,
    uid: int,
    state: FSMContext,
    *,
    source: Message | None = None,
    force_new: bool = False,
    ctx: dict | None = None,
    notice: str | None = None,
) -> None:
    """
    Показывает экран текущего вопроса. ctx — контекст экрана: режим подачи вопроса, комментарий
    к прошлому ответу, подсказка. Если ctx не передан, берётся сохранённый в FSM (если он для
    этого же вопроса), иначе вопрос подаётся в режиме «продолжаем».
    """
    user = await storage.get_user(uid)
    track = user.get("track")
    idx = user.get("current_question_index", 0)

    if not track or user.get("finished"):
        text, kb = await main_menu_view(uid, notice="Активного собеседования нет. Выберите действие в меню.")
        await state.clear()
        await show_screen(bot, chat_id, text, kb, source=source, force_new=force_new)
        return
    if idx >= TOTAL_QUESTIONS:
        await finalize_interview(bot, chat_id, uid, state)
        return
    if needs_payment(user, idx):
        await state.clear()
        text, kb = await paywall_view(uid)
        await show_screen(bot, chat_id, text, kb, source=source, force_new=force_new)
        return

    if ctx is None:
        saved = (await state.get_data()).get("iv_ctx") or {}
        ctx = saved if saved.get("q_index") == idx else {"mode": "resume"}
    ctx = dict(ctx, q_index=idx)

    await state.set_state(InterviewStates.waiting_answer)
    await state.update_data(iv_ctx={k: v for k, v in ctx.items() if k != "notice"})
    await show_screen(
        bot, chat_id, _compose(user, ctx, notice), interview_kb(hint_shown=bool(ctx.get("hint"))),
        source=source, force_new=force_new,
    )


async def proceed_after_answer(bot: Bot, chat_id: int, uid: int, state: FSMContext, ctx: dict) -> None:
    """После ответа/пропуска: следующий вопрос, экран оплаты или финал."""
    user = await storage.get_user(uid)
    next_index = user.get("current_question_index", 0)

    if next_index >= TOTAL_QUESTIONS:
        await finalize_interview(bot, chat_id, uid, state, ctx=ctx)
        return

    if needs_payment(user, next_index):
        # Комментарий к последнему бесплатному ответу показываем над экраном оплаты, чтобы он не потерялся
        prefix = ""
        if ctx.get("fb_text"):
            prefix = feedback_block(ctx, with_quote=False) + f"\n\n{SEPARATOR}"
        elif ctx.get("notice"):
            prefix = f"<i>{esc(ctx['notice'])}</i>"
        await state.clear()
        text, kb = await paywall_view(uid, prefix=prefix)
        await show_screen(bot, chat_id, text, kb)
        return

    await render_question(bot, chat_id, uid, state, ctx=dict(ctx, mode="next"))


# =========================================================
# ОТВЕТ КАНДИДАТА
# =========================================================

async def process_answer_text(message: Message, state: FSMContext, bot: Bot) -> None:
    """Обработка текстового ответа. Вызывается и из catch-all (если FSM сбросился после перезапуска)."""
    uid = message.from_user.id
    chat_id = message.chat.id
    text = (message.text or "").strip()
    await delete_user_message(message)

    lock = _lock(uid)
    if lock.locked():
        return  # предыдущий ответ ещё обрабатывается — лишнее сообщение просто убираем
    async with lock:
        user = await storage.get_user(uid)
        idx = user.get("current_question_index", 0)
        if not user.get("track") or user.get("finished") or idx >= TOTAL_QUESTIONS or needs_payment(user, idx):
            await render_question(bot, chat_id, uid, state)
            return

        if len(text) < MIN_ANSWER_LENGTH:
            await render_question(bot, chat_id, uid, state, notice=short_answer_reply())
            return

        question = get_question(user["track"], idx)
        quote = truncate_plain(esc(text), ANSWER_QUOTE_LIMIT)
        await show_screen(
            bot, chat_id,
            f"✍️ <b>Ответ на вопрос {idx + 1} принят</b>\n"
            f"<blockquote expandable>{quote}</blockquote>\n\n"
            "⏳ <i>Интервьюер изучает ответ…</i>",
            None,
        )

        async with ChatActionSender.typing(bot=bot, chat_id=chat_id):
            reply = await interviewer_reply(
                question["text"],
                text,
                track_title=track_title(user["track"]),
                question_number=idx + 1,
                total_questions=TOTAL_QUESTIONS,
                history=user.get("answers", []),
            )

        saved = await storage.append_answer(uid, idx, {
            "q_id": question["id"],
            "question_text": question["text"],
            "answer": text,
            "feedback": reply.text,
            "score": reply.score,
        })
        if not saved:
            await render_question(bot, chat_id, uid, state)
            return

        ctx = {"fb_num": idx + 1, "fb_answer": text, "fb_text": reply.text, "fb_score": reply.score}
        await proceed_after_answer(bot, chat_id, uid, state, ctx)


@router.message(InterviewStates.waiting_answer, F.text, not_command)
async def handle_text_answer(message: Message, state: FSMContext, bot: Bot):
    await process_answer_text(message, state, bot)


@router.message(InterviewStates.waiting_answer, not_command)
async def handle_non_text(message: Message, state: FSMContext, bot: Bot):
    await delete_user_message(message)
    await render_question(bot, message.chat.id, message.from_user.id, state, notice=non_text_reply())


# =========================================================
# КНОПКИ ПОД ВОПРОСОМ
# =========================================================

@router.callback_query(F.data.in_({"interview_continue", "cmd_resume"}))
async def cb_continue(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await render_question(bot, _chat_id(callback), callback.from_user.id, state,
                          source=callback.message, ctx={"mode": "resume"})


@router.callback_query(F.data == "iv_back")
async def cb_back_to_question(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await render_question(bot, _chat_id(callback), callback.from_user.id, state, source=callback.message)


@router.callback_query(F.data.in_({"iv_hint", "cmd_hint"}))
async def cb_hint(callback: CallbackQuery, state: FSMContext, bot: Bot):
    uid = callback.from_user.id
    user = await storage.get_user(uid)
    idx = user.get("current_question_index", 0)
    if not user.get("track") or user.get("finished") or idx >= TOTAL_QUESTIONS or needs_payment(user, idx):
        await callback.answer()
        await render_question(bot, _chat_id(callback), uid, state, source=callback.message)
        return

    await callback.answer("Готовим подсказку…")
    async with ChatActionSender.typing(bot=bot, chat_id=_chat_id(callback)):
        hint = await generate_hint(get_question(user["track"], idx)["text"], track_title(user["track"]))
    if not hint:
        hint = (
            "Рекомендуем построить ответ так: кратко изложите суть, затем приведите пример из практики "
            "и в завершение укажите, какие были компромиссы и что вы сделали бы иначе."
        )

    saved = (await state.get_data()).get("iv_ctx") or {}
    ctx = saved if saved.get("q_index") == idx else {"mode": "resume"}
    await render_question(bot, _chat_id(callback), uid, state, source=callback.message, ctx=dict(ctx, hint=hint))


@router.callback_query(F.data.in_({"iv_skip", "cmd_skip_question"}))
async def cb_skip(callback: CallbackQuery, state: FSMContext, bot: Bot):
    uid = callback.from_user.id
    chat_id = _chat_id(callback)
    lock = _lock(uid)
    if lock.locked():
        await callback.answer("Подождите, идёт обработка ответа…")
        return
    await callback.answer()
    async with lock:
        user = await storage.get_user(uid)
        idx = user.get("current_question_index", 0)
        if not user.get("track") or user.get("finished") or idx >= TOTAL_QUESTIONS or needs_payment(user, idx):
            await render_question(bot, chat_id, uid, state, source=callback.message)
            return
        question = get_question(user["track"], idx)
        await storage.append_answer(uid, idx, {
            "q_id": question["id"],
            "question_text": question["text"],
            "answer": "[Вопрос пропущен кандидатом]",
            "feedback": "Кандидат решил пропустить данный вопрос.",
            "score": None,
        })
        await proceed_after_answer(bot, chat_id, uid, state, {"notice": skip_reply()})


@router.callback_query(F.data.in_({"iv_reset", "cmd_reset_prompt"}))
async def cb_reset_prompt(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    await show_screen(
        bot, _chat_id(callback),
        "🔄 <b>Начать собеседование заново?</b>\n\n"
        "Все ответы текущего собеседования будут удалены, и вы сможете выбрать направление заново. "
        "Оплаченный доступ и бонусы сохранятся.",
        reset_confirm_kb(), source=callback.message,
    )


@router.callback_query(F.data.in_({"iv_reset_ok", "cmd_reset_confirm"}))
async def cb_reset_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer("Прогресс сброшен")
    user = await storage.reset_user(callback.from_user.id)
    await state.clear()
    text, kb = tracks_view(user, notice="Прогресс сброшен. Выберите направление для нового собеседования.")
    await show_screen(bot, _chat_id(callback), text, kb, source=callback.message)


@router.callback_query(F.data.in_({"iv_finish", "cmd_finish_early"}))
async def cb_finish_prompt(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    user = await storage.get_user(callback.from_user.id)

    # До оплаты «Завершить» просто завершает собеседование — разбор и Word-отчёт входят в платный доступ
    if not storage.user_has_track_access(user, user.get("track")):
        await show_screen(
            bot, _chat_id(callback),
            "🏁 <b>Завершить собеседование?</b>\n\n"
            "Итоговый разбор и Word-отчёт формируются только при полном доступе к направлению. "
            "Если завершить сейчас, ответы этого собеседования будут удалены.",
            ikb(
                [btn("🏁 Да, завершить", "iv_finish_ok")],
                [btn("💎 Открыть полный доступ", "pay_open")],
                [btn("↩️ Вернуться к вопросу", "iv_back")],
            ),
            source=callback.message,
        )
        return

    answered = [a for a in user.get("answers", []) if not str(a.get("answer", "")).startswith("[Вопрос пропущен")]
    if not answered:
        await show_screen(
            bot, _chat_id(callback),
            "🏁 <b>Завершить собеседование</b>\n\n"
            "Вы ещё не ответили ни на один вопрос, поэтому итоговый разбор сформировать нельзя.",
            back_to_question_kb(), source=callback.message,
        )
        return
    await show_screen(
        bot, _chat_id(callback),
        "🏁 <b>Завершить собеседование досрочно?</b>\n\n"
        f"Итоговый разбор будет подготовлен по {len(answered)} ответам из {TOTAL_QUESTIONS}. "
        "Продолжить это собеседование после завершения будет нельзя.",
        finish_confirm_kb(), source=callback.message,
    )


@router.callback_query(F.data == "iv_finish_ok")
async def cb_finish_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    uid = callback.from_user.id
    lock = _lock(uid)
    if lock.locked():
        await callback.answer("Подождите, идёт обработка…")
        return
    await callback.answer()
    async with lock:
        user = await storage.get_user(uid)
        if not user.get("track") or user.get("finished"):
            await render_question(bot, _chat_id(callback), uid, state, source=callback.message)
            return

        if not storage.user_has_track_access(user, user.get("track")):
            # Бесплатная часть: собеседование просто завершается, без разбора и отчёта
            await storage.reset_user(uid)
            await state.clear()
            text, kb = await main_menu_view(
                uid,
                notice="Собеседование завершено. Итоговый разбор и Word-отчёт доступны при полном доступе к направлению.",
            )
            await show_screen(bot, _chat_id(callback), text, kb, source=callback.message)
            return

        if not user.get("answers"):
            await render_question(bot, _chat_id(callback), uid, state, source=callback.message)
            return
        await finalize_interview(bot, _chat_id(callback), uid, state, early=True)


# =========================================================
# ФИНАЛ
# =========================================================

async def finalize_interview(
    bot: Bot, chat_id: int, uid: int, state: FSMContext, *, early: bool = False, ctx: dict | None = None
) -> None:
    """Итоговый разбор + черновик резюме + Word-документ. Результат сохраняется в «Мои результаты»."""
    user = await storage.update_user(uid, finished=True)
    await state.clear()
    track = user.get("track")
    title = track_title(track)
    answers = user.get("answers", [])

    prefix = ""
    if ctx and ctx.get("fb_text"):
        prefix = feedback_block(ctx, with_quote=False) + f"\n\n{SEPARATOR}\n\n"
    head = early_finish_reply() if early else finished_reply()
    await show_screen(
        bot, chat_id,
        f"{prefix}🏁 <b>{esc(head)}</b>\n\n⏳ <i>Готовим итоговый разбор и черновик резюме. Обычно это занимает до минуты…</i>",
        None,
    )

    qa_pairs = [{"question": a["question_text"], "answer": a["answer"], "score": a.get("score")} for a in answers]
    async with ChatActionSender.typing(bot=bot, chat_id=chat_id):
        report, resume_draft = await asyncio.gather(
            generate_final_report(qa_pairs, title),
            generate_resume_draft(qa_pairs, title),
        )

    result = {
        "track": track,
        "track_title": title,
        "report": report,
        "resume": resume_draft,
        "answers": answers,
        "finished_at": datetime.now(timezone.utc).strftime("%d.%m.%Y"),
    }
    await storage.save_result(uid, result)

    # Word-документ — постоянное сообщение (его не удаляем: это результат, который сохраняют себе)
    await send_result_docx(bot, chat_id, user, result)
    await show_screen(bot, chat_id, report_text(result), finished_kb(), force_new=True)


async def send_result_docx(bot: Bot, chat_id: int, user: dict, result: dict) -> None:
    try:
        doc_name = user.get("username") or "Candidate"
        docx_file = create_candidate_docx(
            doc_name, result.get("report", ""), result.get("resume", ""), result.get("answers", []),
            track_title=result.get("track_title"),
        )
        await bot.send_document(
            chat_id=chat_id,
            document=BufferedInputFile(docx_file.getvalue(), filename=f"Interview_{result.get('track') or 'result'}.docx"),
            caption=f"📄 Итоговый разбор, черновик резюме и стенограмма собеседования · {esc(result.get('track_title', ''))}",
        )
    except Exception as e:
        logger.exception("Не удалось отправить .docx: %s", e)


# Совместимость со старыми вызовами
async def send_final_report(message: Message, state: FSMContext, user_id: int):
    await finalize_interview(message.bot, message.chat.id, user_id, state)
