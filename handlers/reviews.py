"""
handlers/reviews.py — отзывы пользователей: просмотр опубликованных, отправка своего отзыва.

Модерация (опубликовать / отклонить / удалить) — в handlers/admin.py, только для администраторов.

Исправлено:
- кнопка «Оставить отзыв» после собеседования не работала: она висела под Word-документом,
  а бот пытался отредактировать документ как текстовое сообщение (Telegram возвращает ошибку);
- текст отзыва вставлялся в HTML без экранирования — один отзыв с символом «<» ломал весь список;
- длинные отзывы могли превысить лимит сообщения Telegram — теперь список постраничный.
"""
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import storage
from keyboards import btn, ikb, menu_btn, review_notification_rows, review_text_kb, stars_rating_kb
from screen import delete_user_message, esc, not_command, notify, show_screen, truncate_plain
from states import ReviewStates

logger = logging.getLogger(__name__)
router = Router(name="reviews")

PAGE_SIZE = 5
REVIEW_MIN_LENGTH = 10
REVIEW_MAX_LENGTH = 1000


def _chat_id(callback: CallbackQuery) -> int:
    return callback.message.chat.id if callback.message else callback.from_user.id


def _stars(rating) -> str:
    try:
        n = max(1, min(5, int(rating)))
    except (TypeError, ValueError):
        n = 5
    return "⭐️" * n


async def reviews_view(page: int):
    approved = await storage.get_approved_reviews()
    total_pages = max(1, (len(approved) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    if not approved:
        text = "⭐️ <b>Отзывы участников</b>\n\nОтзывов пока нет — вы можете оставить первый."
    else:
        avg = sum(int(r.get("rating", 5)) for r in approved) / len(approved)
        lines = [f"⭐️ <b>Отзывы участников</b> · средняя оценка {avg:.1f} из 5 ({len(approved)})"]
        for r in approved[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
            lines.append(
                f"{_stars(r.get('rating'))}  <b>{esc(r.get('full_name') or 'Кандидат')}</b> · {esc(r.get('created_at', ''))}\n"
                f"<i>«{truncate_plain(esc(r.get('text', '')), 400)}»</i>"
            )
        text = "\n\n".join(lines)

    nav = []
    if total_pages > 1:
        if page > 0:
            nav.append(btn("◀️", f"reviews:{page - 1}"))
        nav.append(btn(f"{page + 1} / {total_pages}", f"reviews:{page}"))
        if page < total_pages - 1:
            nav.append(btn("▶️", f"reviews:{page + 1}"))
    kb = ikb(nav, [btn("✍️ Оставить отзыв", "rv_new")], [menu_btn()])
    return text, kb


@router.message(Command("reviews"))
async def cmd_reviews(message: Message, state: FSMContext, bot: Bot):
    await delete_user_message(message)
    await state.clear()
    text, kb = await reviews_view(0)
    await show_screen(bot, message.chat.id, text, kb, force_new=True)


@router.callback_query(F.data.startswith("reviews:") | (F.data == "btn_reviews_show"))
async def cb_reviews(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await state.clear()
    try:
        page = int(callback.data.split(":", 1)[1]) if ":" in callback.data else 0
    except ValueError:
        page = 0
    text, kb = await reviews_view(page)
    await show_screen(bot, _chat_id(callback), text, kb, source=callback.message)


@router.callback_query(F.data.in_({"rv_new", "review_start_fsm"}))
async def cb_start_review(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await state.clear()
    if await storage.user_has_pending_review(callback.from_user.id):
        await show_screen(
            bot, _chat_id(callback),
            "✍️ <b>Отзыв</b>\n\nВаш предыдущий отзыв ещё на модерации. Новый можно будет оставить после его проверки.",
            ikb([btn("◀️ К отзывам", "reviews:0")], [menu_btn()]), source=callback.message,
        )
        return
    await show_screen(
        bot, _chat_id(callback),
        "✍️ <b>Отзыв о тренажёре</b>\n\nОцените качество и пользу собеседования:",
        stars_rating_kb(), source=callback.message,
    )


@router.callback_query(F.data.startswith("rv_rate:") | F.data.startswith("rate_star_"))
async def cb_select_stars(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    try:
        rating = int(callback.data.replace("rv_rate:", "").replace("rate_star_", ""))
    except ValueError:
        rating = 5
    rating = max(1, min(5, rating))
    await state.set_state(ReviewStates.waiting_text)
    await state.update_data(rating=rating)
    await show_screen(
        bot, _chat_id(callback),
        f"✍️ <b>Отзыв о тренажёре</b>\n\nВаша оценка: {_stars(rating)}\n\n"
        "Напишите отзыв сообщением: что было полезно, помогло ли в подготовке, что стоит улучшить.",
        review_text_kb(), source=callback.message,
    )


@router.message(ReviewStates.waiting_text, F.text, not_command)
async def handle_review_text(message: Message, state: FSMContext, bot: Bot):
    await delete_user_message(message)
    text = message.text.strip()
    data = await state.get_data()
    rating = data.get("rating", 5)

    if len(text) < REVIEW_MIN_LENGTH:
        await show_screen(
            bot, message.chat.id,
            f"✍️ <b>Отзыв о тренажёре</b>\n\nВаша оценка: {_stars(rating)}\n\n"
            "<i>Отзыв слишком короткий.</i> Пожалуйста, напишите пару предложений.",
            review_text_kb(),
        )
        return

    text = text[:REVIEW_MAX_LENGTH]
    rev_id = await storage.add_review(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        rating=rating,
        text=text,
    )
    await state.clear()

    await show_screen(
        bot, message.chat.id,
        "✅ <b>Спасибо за отзыв!</b>\n\nОн появится в общем списке после модерации.",
        ikb([btn("⭐️ К отзывам", "reviews:0")], [menu_btn()]),
    )

    alert = (
        f"📬 <b>Новый отзыв #{rev_id} на модерации</b>\n\n"
        f"👤 {esc(message.from_user.full_name)} (@{esc(message.from_user.username or '—')}), "
        f"ID <code>{message.from_user.id}</code>\n"
        f"Оценка: {_stars(rating)}\n\n"
        f"<i>«{esc(text)}»</i>"
    )
    for admin_id in await storage.get_active_admin_ids():
        await notify(bot, admin_id, alert, review_notification_rows(rev_id))
