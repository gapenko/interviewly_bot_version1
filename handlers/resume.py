"""
handlers/resume.py — раздел «Мои результаты»: последний итоговый разбор, черновик резюме,
повторная выгрузка Word-отчёта и аудит резюме пользователя.

Раньше команда /resume показывала меню с кнопками, у которых не было обработчиков.
"""
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender

import storage
from handlers.interview import send_result_docx
from keyboards import btn, ikb, menu_btn
from llm_service import audit_user_resume
from menus import fit_long_html, results_view
from screen import delete_user_message, esc, not_command, show_screen
from states import ResumeStates

logger = logging.getLogger(__name__)
router = Router(name="resume")

RESUME_MIN_LENGTH = 200


def _chat_id(callback: CallbackQuery) -> int:
    return callback.message.chat.id if callback.message else callback.from_user.id


def _back_to_results_kb():
    return ikb([btn("◀️ К результатам", "results")], [menu_btn()])


# =========================================================
# РЕЗУЛЬТАТЫ
# =========================================================

@router.message(Command("results", "resume"))
async def cmd_results(message: Message, state: FSMContext, bot: Bot):
    await delete_user_message(message)
    await state.clear()
    user = await storage.get_user(message.from_user.id)
    text, kb = results_view(user)
    await show_screen(bot, message.chat.id, text, kb, force_new=True)


@router.callback_query(F.data == "results")
async def cb_results(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await state.clear()
    user = await storage.get_user(callback.from_user.id)
    text, kb = results_view(user)
    await show_screen(bot, _chat_id(callback), text, kb, source=callback.message)


@router.callback_query(F.data.in_({"res_resume", "resume_draft"}))
async def cb_resume_draft(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    user = await storage.get_user(callback.from_user.id)
    result = user.get("last_result")
    if not result:
        text, kb = results_view(user)
        await show_screen(bot, _chat_id(callback), text, kb, source=callback.message)
        return
    text = fit_long_html(
        f"📄 <b>Черновик резюме · {esc(result.get('track_title', ''))}</b>\n\n{result.get('resume', '')}",
        3800, "<i>Полная версия — в Word-документе.</i>",
    )
    await show_screen(bot, _chat_id(callback), text, _back_to_results_kb(), source=callback.message)


@router.callback_query(F.data.in_({"res_docx", "resume_download"}))
async def cb_resume_docx(callback: CallbackQuery, bot: Bot):
    user = await storage.get_user(callback.from_user.id)
    result = user.get("last_result")
    chat_id = _chat_id(callback)
    if not result:
        await callback.answer()
        text, kb = results_view(user)
        await show_screen(bot, chat_id, text, kb, source=callback.message)
        return
    await callback.answer("Отправляем документ…")
    await send_result_docx(bot, chat_id, user, result)
    # Экран переносим под документ, чтобы меню оставалось последним сообщением
    text, kb = results_view(user)
    await show_screen(bot, chat_id, text, kb, force_new=True)


# =========================================================
# АУДИТ РЕЗЮМЕ
# =========================================================

@router.callback_query(F.data.in_({"res_audit", "resume_audit"}))
async def cb_resume_audit(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    user = await storage.get_user(callback.from_user.id)
    if not storage.user_has_any_paid_access(user):
        await show_screen(
            bot, _chat_id(callback),
            "🔍 <b>Аудит резюме</b>\n\nАудит резюме доступен после оплаты доступа к любому направлению собеседования.",
            _back_to_results_kb(), source=callback.message,
        )
        return
    await state.set_state(ResumeStates.waiting_user_resume)
    await show_screen(
        bot, _chat_id(callback),
        "🔍 <b>Аудит резюме</b>\n\n"
        "Отправьте текст вашего резюме одним сообщением. Мы оценим его глазами рекрутера "
        "и предложим, что переформулировать, чтобы повысить отклик.",
        _back_to_results_kb(), source=callback.message,
    )


@router.message(ResumeStates.waiting_user_resume, F.text, not_command)
async def process_resume_text(message: Message, state: FSMContext, bot: Bot):
    await delete_user_message(message)
    text = message.text.strip()
    if len(text) < RESUME_MIN_LENGTH:
        await show_screen(
            bot, message.chat.id,
            "🔍 <b>Аудит резюме</b>\n\n<i>Текст слишком короткий для аудита.</i> "
            "Пожалуйста, отправьте полный текст резюме одним сообщением.",
            _back_to_results_kb(),
        )
        return

    await state.clear()
    await show_screen(bot, message.chat.id, "🔍 <b>Аудит резюме</b>\n\n⏳ <i>Анализируем резюме…</i>", None)
    user = await storage.get_user(message.from_user.id)
    qa_pairs = [
        {"question": a.get("question_text", ""), "answer": a.get("answer", "")}
        for a in ((user.get("last_result") or {}).get("answers") or user.get("answers", []))
    ]
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        audit = await audit_user_resume(text, qa_pairs)

    await show_screen(
        bot, message.chat.id,
        fit_long_html(f"🔍 <b>Аудит резюме</b>\n\n{audit}", 3800),
        ikb([btn("🔁 Проверить другой вариант", "res_audit")], [btn("◀️ К результатам", "results")], [menu_btn()]),
    )
