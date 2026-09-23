"""
menus.py — тексты и клавиатуры общих экранов (главное меню, выбор направления, правила,
результаты). Вынесены из хэндлеров, чтобы любой модуль мог показать их без циклических импортов.
"""
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

import storage
from config import FREE_QUESTIONS_COUNT
from keyboards import btn, ikb, main_menu_kb, menu_btn, rules_kb, tracks_kb
from questions import TOTAL_QUESTIONS, TRACKS
from screen import esc

View = tuple[str, Optional[InlineKeyboardMarkup]]

_bot_username: Optional[str] = None


async def get_bot_username(bot: Bot) -> str:
    global _bot_username
    if not _bot_username:
        _bot_username = (await bot.get_me()).username
    return _bot_username


def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(4, "вопрос", "вопроса", "вопросов") -> "вопроса"."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def track_title(track: Optional[str]) -> str:
    return TRACKS.get(track or "", "IT")


def interview_in_progress(user: dict) -> bool:
    return bool(user.get("track")) and not user.get("finished", False)


def fit_long_html(text: str, limit: int, tail: str = "") -> str:
    """Обрезает длинный HTML по границе абзаца (чтобы не разорвать теги) и добавляет хвост."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = cut.rfind("\n\n")
    if boundary < limit * 0.5:
        boundary = cut.rfind("\n")
    if boundary > 0:
        cut = cut[:boundary]
    return cut.rstrip() + (f"\n\n{tail}" if tail else "\n…")


# =========================================================
# ГЛАВНОЕ МЕНЮ
# =========================================================

INTRO_TEXT = (
    "Здесь можно пройти техническое собеседование в формате диалога с интервьюером: вопросы об опыте, "
    "технические и поведенческие вопросы по выбранному направлению. После каждого ответа — краткая "
    "обратная связь, в конце — итоговый разбор и черновик резюме."
)


async def main_menu_view(uid: int, *, notice: Optional[str] = None, intro: bool = False) -> View:
    user = await storage.get_user(uid)
    in_progress = interview_in_progress(user)
    last = user.get("last_result") or None

    parts = ["🎯 <b>Тренажёр технического собеседования</b>"]
    if intro:
        parts.append(INTRO_TEXT)
    if notice:
        parts.append(f"<i>{esc(notice)}</i>")

    status = []
    if in_progress:
        idx = min(user.get("current_question_index", 0) + 1, TOTAL_QUESTIONS)
        status.append(f"📍 Текущее собеседование: <b>{esc(track_title(user.get('track')))}</b> — вопрос {idx} из {TOTAL_QUESTIONS}")
    if last:
        status.append(f"📊 Последний результат: {esc(last.get('track_title') or track_title(last.get('track')))}, {esc(last.get('finished_at', ''))}")
    if user.get("bonus_balance"):
        status.append(f"🎁 Бонусов на счёте: {user.get('bonus_balance')}")
    if status:
        parts.append("\n".join(status))

    parts.append("Выберите действие:")
    kb = main_menu_kb(in_progress=in_progress, has_result=bool(last), is_admin=await storage.is_admin(uid))
    return "\n\n".join(parts), kb


# =========================================================
# ВЫБОР НАПРАВЛЕНИЯ И ПРАВИЛА
# =========================================================

def _progress_warning(user: dict, new_track: Optional[str] = None) -> Optional[str]:
    if not interview_in_progress(user) or not user.get("answers"):
        return None
    if new_track and new_track == user.get("track"):
        return (
            f"⚠️ У вас уже есть начатое собеседование по этому направлению (ответов: {len(user['answers'])}). "
            "Если начать заново, прогресс будет сброшен — чтобы продолжить, вернитесь в главное меню."
        )
    return (
        f"⚠️ У вас есть начатое собеседование по направлению «{esc(track_title(user.get('track')))}» "
        f"(ответов: {len(user['answers'])}). Если начать новое, этот прогресс будет сброшен."
    )


def tracks_view(user: dict, notice: Optional[str] = None) -> View:
    parts = ["🎯 <b>Выберите направление собеседования</b>"]
    if notice:
        parts.append(f"<i>{esc(notice)}</i>")
    parts.append(
        "Вопросы подбираются под выбранный стек. Доступ к полному собеседованию "
        "оплачивается отдельно для каждого направления."
    )
    warning = _progress_warning(user)
    if warning:
        parts.append(warning)
    return "\n\n".join(parts), tracks_kb()


def rules_view(user: dict, track: str) -> View:
    if storage.user_has_track_access(user, track):
        payment_note = "• Это направление у вас оплачено — все вопросы доступны."
    else:
        payment_note = (
            f"• Первые <b>{FREE_QUESTIONS_COUNT}</b> {plural(FREE_QUESTIONS_COUNT, 'вопрос', 'вопроса', 'вопросов')} — "
            "бесплатно, далее — по оплате доступа к этому направлению."
        )
    parts = [
        f"✅ <b>Направление: {esc(track_title(track))}</b>",
        "📋 <b>Как проходит собеседование</b>\n"
        f"• {TOTAL_QUESTIONS} вопросов: опыт, технические и поведенческие.\n"
        f"{payment_note}\n"
        "• После каждого ответа — краткая обратная связь интервьюера.\n"
        "• В конце — итоговый разбор и Word-отчёт (.docx) с черновиком резюме.\n"
        "• Можно взять подсказку, пропустить вопрос или завершить досрочно.",
        "💡 Отвечайте развёрнуто, как на реальном собеседовании: односложные ответы не позволят оценить уровень.",
    ]
    warning = _progress_warning(user, track)
    if warning:
        parts.append(warning)
    return "\n\n".join(parts), rules_kb(track)


# =========================================================
# РЕЗУЛЬТАТЫ
# =========================================================

REPORT_TAIL = "<i>Полная версия разбора — в Word-документе.</i>"


def report_text(result: dict) -> str:
    title = esc(result.get("track_title") or track_title(result.get("track")))
    header = f"📊 <b>Итоговый разбор · {title}</b>"
    if result.get("finished_at"):
        header += f"\n<i>{esc(result['finished_at'])}</i>"
    return fit_long_html(f"{header}\n\n{result.get('report', '')}", 3700, REPORT_TAIL)


def results_view(user: dict) -> View:
    result = user.get("last_result")
    if not result:
        return (
            "📊 <b>Мои результаты</b>\n\nЗавершённых собеседований пока нет. "
            "Пройдите собеседование — здесь появятся итоговый разбор, черновик резюме и Word-отчёт.",
            ikb([btn("🚀 Начать собеседование", "menu_start")], [menu_btn()]),
        )
    kb = ikb(
        [btn("📄 Черновик резюме", "res_resume"), btn("📥 Скачать .docx", "res_docx")],
        [btn("🔍 Аудит моего резюме", "res_audit")],
        [menu_btn()],
    )
    return report_text(result), kb
