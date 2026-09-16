"""
keyboards.py — все инлайн-клавиатуры приложения.
Нижние Reply-кнопки полностью удалены для чистого ввода текста.
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
import config
from questions import TRACKS

ACCESS_PRICE_STARS = getattr(
    config, "ACCESS_PRICE_STARS", getattr(config, "PRICE_STARS", getattr(config, "STARS_PRICE", 50))
)
SBP_PRICE_RUB = getattr(
    config, "SBP_PRICE_RUB", getattr(config, "PRICE_RUB", getattr(config, "PAYMENT_AMOUNT", 100))
)

# Очистка нижних кнопок при входе
remove_reply_kb = ReplyKeyboardRemove()


# --- ИНТЕРВЬЮ: ТУЛБАР ПОД КАРТОЧКОЙ ВОПРОСА ---

def get_interview_toolbar() -> InlineKeyboardMarkup:
    """
    Инлайн-кнопки управления под карточкой вопроса.
    Названия полные, без сокращений, в 2 ряда.
    Кнопка 'Продолжить' убрана.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💡 Подсказка ментора", callback_data="cmd_hint"),
                InlineKeyboardButton(text="⏭ Пропустить вопрос", callback_data="cmd_skip_question"),
            ],
            [
                InlineKeyboardButton(text="🔄 Начать заново", callback_data="cmd_reset_prompt"),
                InlineKeyboardButton(text="🛑 Завершить интервью", callback_data="cmd_finish_early"),
            ],
        ]
    )


def get_reset_confirm_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение сброса прогресса."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚠️ Да, начать заново", callback_data="cmd_reset_confirm"),
                InlineKeyboardButton(text="↩️ Продолжить ответ", callback_data="cmd_resume"),
            ]
        ]
    )


# --- ВЫБОР НАПРАВЛЕНИЯ ---

def get_tracks_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    items = list(TRACKS.items())
    for i in range(0, len(items), 2):
        row = [InlineKeyboardButton(text=items[i][1], callback_data=f"track_{items[i][0]}")]
        if i + 1 < len(items):
            row.append(InlineKeyboardButton(text=items[i + 1][1], callback_data=f"track_{items[i + 1][0]}"))
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_welcome_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Начать собеседование", callback_data="start_choose_track")],
            [
                InlineKeyboardButton(text="🎁 Реферальная программа", callback_data="btn_ref_program"),
                InlineKeyboardButton(text="⭐️ Отзывы участников", callback_data="btn_reviews_show"),
            ],
        ]
    )


def get_ready_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Поехали к вопросам ➡️", callback_data="start_first_question")],
            [InlineKeyboardButton(text="◀️ Выбрать другое направление", callback_data="start_choose_track")],
        ]
    )


# --- ПЛАТЕЖИ И ФИНАЛ ---

def get_paywall_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Банковская карта / СБП — {SBP_PRICE_RUB} ₽", callback_data="pay_yookassa")],
            [InlineKeyboardButton(text=f"⭐️ Telegram Stars — {ACCESS_PRICE_STARS} XTR", callback_data="pay_stars_invoice")],
        ]
    )


def get_finished_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Оставить отзыв ментору", callback_data="review_start_fsm")],
            [InlineKeyboardButton(text="🔄 Пройти другое направление", callback_data="cmd_reset_confirm")],
        ]
    )


# --- ОТЗЫВЫ ---

def get_stars_rating_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ 1", callback_data="rate_star_1"),
                InlineKeyboardButton(text="⭐⭐ 2", callback_data="rate_star_2"),
                InlineKeyboardButton(text="⭐⭐⭐ 3", callback_data="rate_star_3"),
            ],
            [
                InlineKeyboardButton(text="⭐⭐⭐⭐ 4", callback_data="rate_star_4"),
                InlineKeyboardButton(text="⭐⭐⭐⭐⭐ 5", callback_data="rate_star_5"),
            ],
        ]
    )


def get_admin_review_kb(review_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"adm_rev_ok:{review_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_rev_no:{review_id}"),
            ]
        ]
    )


# --- АДМИН-ПАНЕЛЬ ---

def get_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats"),
                InlineKeyboardButton(text="🔗 UTM-ссылки каналов", callback_data="admin_campaigns"),
            ],
            [
                InlineKeyboardButton(text="⭐️ Баланс Stars", callback_data="admin_stars"),
                InlineKeyboardButton(text="📢 Создать рассылку", callback_data="admin_broadcast"),
            ],
            [
                InlineKeyboardButton(text="➕ Создать рекламную ссылку", callback_data="admin_create_camp"),
                InlineKeyboardButton(text="💾 Выгрузить базы (JSON)", callback_data="admin_export_db"),
            ],
            [
                InlineKeyboardButton(text="❌ Закрыть панель", callback_data="admin_close"),
            ],
        ]
    )