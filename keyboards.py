"""
keyboards.py — клавиатуры бота.
Все кнопки адаптированы под мобильные экраны Telegram:
- Никаких обрезок текста многоточием (...)
- Оптимальная компоновка (1 в ряд для ключевых действий, компактные пары для коротких).
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

remove_reply_kb = ReplyKeyboardRemove()


# =========================================================
# ИНТЕРВЬЮ: КАРТОЧКА ВОПРОСА
# =========================================================

def get_interview_toolbar() -> InlineKeyboardMarkup:
    """
    Кнопки под вопросом.
    Каждое действие в отдельную строку или компактными парами без усечения.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💡 Подсказка", callback_data="cmd_hint"),
                InlineKeyboardButton(text="⏭ Пропустить", callback_data="cmd_skip_question"),
            ],
            [
                InlineKeyboardButton(text="🔄 Начать заново", callback_data="cmd_reset_prompt"),
                InlineKeyboardButton(text="🛑 Завершить", callback_data="cmd_finish_early"),
            ],
        ]
    )


def get_reset_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚠️ Да, начать заново", callback_data="cmd_reset_confirm")],
            [InlineKeyboardButton(text="↩️ Продолжить ответ", callback_data="cmd_resume")],
        ]
    )


# =========================================================
# ВЫБОР НАПРАВЛЕНИЯ И СТАРТ
# =========================================================

def get_tracks_keyboard() -> InlineKeyboardMarkup:
    """
    Направления по 1 в ряд, чтобы названия специализаций
    читались целиком без сокращений.
    """
    buttons = [[InlineKeyboardButton(text=name, callback_data=f"track_{key}")] for key, name in TRACKS.items()]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_welcome_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Начать собеседование", callback_data="start_choose_track")],
            [InlineKeyboardButton(text="🎁 Рефералы и бонусы", callback_data="btn_ref_program")],
            [InlineKeyboardButton(text="⭐️ Отзывы участников", callback_data="btn_reviews_show")],
        ]
    )


def get_ready_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Поехали к вопросам ➡️", callback_data="start_first_question")],
            [InlineKeyboardButton(text="◀️ Другое направление", callback_data="start_choose_track")],
        ]
    )


# =========================================================
# ПЛАТЕЖИ И ФИНАЛ
# =========================================================

def get_paywall_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 СБП / Карты РФ — {SBP_PRICE_RUB} ₽", callback_data="pay_yookassa")],
            [InlineKeyboardButton(text=f"⭐️ Telegram Stars — {ACCESS_PRICE_STARS} XTR", callback_data="pay_stars_invoice")],
        ]
    )


def get_finished_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Оставить отзыв ментору", callback_data="review_start_fsm")],
            [InlineKeyboardButton(text="🔄 Выбрать другое направление", callback_data="cmd_reset_confirm")],
        ]
    )


def get_resume_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Черновик резюме", callback_data="resume_draft")],
            [InlineKeyboardButton(text="🔍 Аудит резюме", callback_data="resume_audit")],
            [InlineKeyboardButton(text="📥 Скачать .docx", callback_data="resume_download")],
        ]
    )


# =========================================================
# ОТЗЫВЫ
# =========================================================

def get_stars_rating_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 ⭐", callback_data="rate_star_1"),
                InlineKeyboardButton(text="2 ⭐", callback_data="rate_star_2"),
                InlineKeyboardButton(text="3 ⭐", callback_data="rate_star_3"),
                InlineKeyboardButton(text="4 ⭐", callback_data="rate_star_4"),
                InlineKeyboardButton(text="5 ⭐", callback_data="rate_star_5"),
            ]
        ]
    )


def get_admin_review_kb(review_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"adm_rev_ok:{review_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_rev_no:{review_id}"),
            ],
            [
                InlineKeyboardButton(text="🗑 Удалить из базы", callback_data=f"adm_rev_del:{review_id}"),
            ]
        ]
    )


# =========================================================
# АДМИН-ПАНЕЛЬ (100% ЧИТАЕМОСТЬ НА ТЕЛЕФОНЕ)
# =========================================================

def get_admin_keyboard() -> InlineKeyboardMarkup:
    """
    Лаконичные надписи и правильная сетка:
    текст умещается на любом мобильном экране без троеточий (...).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Аналитика", callback_data="admin_stats"),
                InlineKeyboardButton(text="⭐️ Отзывы", callback_data="admin_reviews_hub"),
            ],
            [
                InlineKeyboardButton(text="🔍 Поиск юзера", callback_data="admin_find_user"),
                InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast"),
            ],
            [
                InlineKeyboardButton(text="🔗 UTM-каналы", callback_data="admin_campaigns"),
                InlineKeyboardButton(text="🎟 Промокоды", callback_data="admin_promos_hub"),
            ],
            [
                InlineKeyboardButton(text="👥 Администраторы", callback_data="admin_team_hub"),
                InlineKeyboardButton(text="💾 Выгрузка JSON", callback_data="admin_export_db"),
            ],
            [
                InlineKeyboardButton(text="❌ Закрыть панель", callback_data="admin_close"),
            ],
        ]
    )


def get_user_manage_kb(target_id: int, has_paid: bool) -> InlineKeyboardMarkup:
    paid_btn = (
        InlineKeyboardButton(text="🔒 Забрать доступ", callback_data=f"adm_u_revoke:{target_id}")
        if has_paid
        else InlineKeyboardButton(text="👑 Выдать доступ", callback_data=f"adm_u_grant:{target_id}")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [paid_btn],
            [
                InlineKeyboardButton(text="➕ 150 бонусов", callback_data=f"adm_u_addb:{target_id}:150"),
                InlineKeyboardButton(text="➖ 150 бонусов", callback_data=f"adm_u_addb:{target_id}:-150"),
            ],
            [
                InlineKeyboardButton(text="🔄 Сброс прогресса", callback_data=f"adm_u_reset:{target_id}"),
                InlineKeyboardButton(text="✉️ Написать", callback_data=f"reply_support:{target_id}"),
            ],
            [InlineKeyboardButton(text="◀️ Назад в админку", callback_data="admin_menu")],
        ]
    )