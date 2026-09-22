"""
keyboards.py — клавиатуры бота с кнопками возврата и адаптацией под мобильные экраны.
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
import config
from questions import TRACKS

# Раньше здесь была цепочка из трёх getattr() на случай расхождения имён переменных
# в config.py / .env. Теперь config.py гарантирует эти два имени, поэтому просто берём их напрямую.
ACCESS_PRICE_STARS = config.ACCESS_PRICE_STARS
SBP_PRICE_RUB = config.SBP_PRICE_RUB

remove_reply_kb = ReplyKeyboardRemove()


# =========================================================
# ИНТЕРВЬЮ: КАРТОЧКА ВОПРОСА
# =========================================================

def get_interview_toolbar() -> InlineKeyboardMarkup:
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
    buttons = [[InlineKeyboardButton(text=name, callback_data=f"track_{key}")] for key, name in TRACKS.items()]
    buttons.append([InlineKeyboardButton(text="◀️ Назад в меню", callback_data="nav_back_to_welcome")])
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
# ДИНАМИЧЕСКИЙ ЭКРАН ОПЛАТЫ
# =========================================================

def get_dynamic_paywall_keyboard(
    rub_price: int,
    stars_price: int,
    has_bonuses: bool = False,
    bonuses_applied: bool = False,
    has_promo: bool = False,
) -> InlineKeyboardMarkup:
    buttons = []

    if rub_price > 0:
        buttons.append([InlineKeyboardButton(text=f"💳 СБП / Карты — {rub_price} ₽", callback_data="pay_yookassa")])
        buttons.append([InlineKeyboardButton(text=f"⭐️ Оплатить {stars_price} Stars", callback_data="pay_stars_invoice")])
    else:
        buttons.append([InlineKeyboardButton(text="🎉 Открыть доступ бесплатно", callback_data="pay_free_unlock")])

    if has_bonuses and not bonuses_applied:
        buttons.append([InlineKeyboardButton(text="🎁 Списать бонусы со счёта", callback_data="pay_apply_bonuses")])

    if not has_promo:
        buttons.append([InlineKeyboardButton(text="🎟 Ввести промокод", callback_data="pay_enter_promocode")])

    buttons.append([InlineKeyboardButton(text="◀️ Назад в меню", callback_data="nav_back_to_welcome")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_paywall_keyboard() -> InlineKeyboardMarkup:
    return get_dynamic_paywall_keyboard(SBP_PRICE_RUB, ACCESS_PRICE_STARS)


def get_cancel_promo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена / Назад к оплате", callback_data="cancel_promocode_input")]
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
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="nav_back_to_welcome")],
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
            ],
            [InlineKeyboardButton(text="◀️ Назад к отзывам", callback_data="btn_reviews_show")],
        ]
    )


def get_cancel_review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена / Назад к отзывам", callback_data="btn_reviews_show")]
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


def get_support_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена / Назад", callback_data="nav_back_to_welcome")]
        ]
    )


# =========================================================
# АДМИН-ПАНЕЛЬ
# =========================================================

def get_admin_keyboard() -> InlineKeyboardMarkup:
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
        InlineKeyboardButton(text="🔒 Забрать полный доступ", callback_data=f"adm_u_revoke:{target_id}")
        if has_paid
        else InlineKeyboardButton(text="👑 Выдать полный доступ (все направления)", callback_data=f"adm_u_grant:{target_id}")
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
