"""
handlers/reviews.py — система отзывов с кнопками возврата и модерацией.
"""
import logging
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from keyboards import (
    get_admin_review_kb,
    get_cancel_review_keyboard,
    get_stars_rating_kb,
)
from states import ReviewStates
from storage import (
    add_review,
    delete_review,
    get_approved_reviews,
    set_review_status,
)

logger = logging.getLogger(__name__)
router = Router(name="reviews")


@router.message(Command("reviews"))
@router.callback_query(F.data == "btn_reviews_show")
async def cmd_reviews(event: Message | CallbackQuery, state: FSMContext = None):
    if state:
        await state.clear()

    is_cb = isinstance(event, CallbackQuery)
    msg = event.message if is_cb else event
    if is_cb:
        await event.answer()

    approved = await get_approved_reviews()
    if not approved:
        text = "⭐️ <b>Пока отзывов нет. Вы можете оставить первый отзыв после собеседования!</b>"
    else:
        text = "⭐️ <b>Отзывы участников о тренажёре:</b>\n\n"
        for r in approved[-10:]:
            stars = "⭐️" * int(r.get("rating", 5))
            text += f"👤 <b>{r['full_name']}</b> ({stars})\n<i>«{r['text']}»</i>\n📅 <code>{r.get('created_at', '')}</code>\n\n"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Оставить свой отзыв", callback_data="review_start_fsm")],
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="nav_back_to_welcome")],
        ]
    )

    if is_cb and msg:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "review_start_fsm")
async def cb_start_review(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "⭐️ <b>Оцените качество и пользу AI-собеседования:</b>",
        reply_markup=get_stars_rating_kb(),
        parse_mode="HTML",
    )
    await state.set_state(ReviewStates.waiting_rating)


@router.callback_query(ReviewStates.waiting_rating, F.data.startswith("rate_star_"))
async def cb_select_stars(callback: CallbackQuery, state: FSMContext):
    rating = int(callback.data.replace("rate_star_", ""))
    await state.update_data(rating=rating)
    await callback.answer()
    await callback.message.edit_text(
        f"Вы выбрали {rating} ⭐.\n\n"
        f"✍️ Напишите ваш отзыв текстом в чат (что понравилось, помогло ли на собеседовании, что улучшить):",
        reply_markup=get_cancel_review_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(ReviewStates.waiting_text)


@router.message(ReviewStates.waiting_text, F.text)
async def handle_review_text(message: Message, state: FSMContext):
    data = await state.get_data()
    rating = data.get("rating", 5)
    text = message.text.strip()

    rev_id = await add_review(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        rating=rating,
        text=text,
    )
    await state.clear()

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐️ Смотреть отзывы", callback_data="btn_reviews_show")],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav_back_to_welcome")],
        ]
    )

    await message.answer(
        "✅ <b>Спасибо за обратную связь!</b>\nВаш отзыв отправлен на модерацию и скоро появится в общем списке.",
        reply_markup=back_kb,
        parse_mode="HTML",
    )

    stars_str = "⭐️" * rating
    adm_alert = (
        f"📬 <b>НОВЫЙ ОТЗЫВ #{rev_id} НА МОДЕРАЦИЮ</b>\n\n"
        f"👤 <b>От:</b> {message.from_user.full_name} (@{message.from_user.username or 'отсутствует'})\n"
        f"🆔 <code>{message.from_user.id}</code>\n"
        f"⭐️ <b>Оценка:</b> {stars_str}\n\n"
        f"💬 <b>Текст:</b>\n{text}"
    )

    from handlers.admin import get_active_admin_ids
    target_admins = await get_active_admin_ids()

    for admin_id in target_admins:
        try:
            await message.bot.send_message(
                admin_id,
                adm_alert,
                reply_markup=get_admin_review_kb(rev_id),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Не удалось доставить отзыв админу %s: %s", admin_id, e)


@router.callback_query(F.data.startswith("adm_rev_ok:"))
async def cb_approve_rev(callback: CallbackQuery):
    rev_id = int(callback.data.replace("adm_rev_ok:", ""))
    await set_review_status(rev_id, True)
    await callback.answer("Одобрено!")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(f"✅ Отзыв #{rev_id} опубликован в общем списке!")


@router.callback_query(F.data.startswith("adm_rev_no:"))
async def cb_reject_rev(callback: CallbackQuery):
    rev_id = int(callback.data.replace("adm_rev_no:", ""))
    await set_review_status(rev_id, False)
    await callback.answer("Отклонено")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(f"❌ Отзыв #{rev_id} скрыт.")


@router.callback_query(F.data.startswith("adm_rev_del:"))
async def cb_rev_del(callback: CallbackQuery):
    rev_id = int(callback.data.replace("adm_rev_del:", ""))
    await delete_review(rev_id)
    await callback.answer("Удалено из базы")
    await callback.message.delete()