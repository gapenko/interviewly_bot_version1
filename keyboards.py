"""
keyboards.py — клавиатуры бота.

Правило навигации: на КАЖДОМ экране есть путь назад — к предыдущему меню
и/или в главное меню (для админки — в админ-панель).
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from questions import TRACKS
from screen import dismiss_row


# =========================================================
# КОНСТРУКТОР
# =========================================================

def btn(text: str, data: str | None = None, *, url: str | None = None, pay: bool = False) -> InlineKeyboardButton:
    if pay:
        return InlineKeyboardButton(text=text, pay=True)
    if url:
        return InlineKeyboardButton(text=text, url=url)
    return InlineKeyboardButton(text=text, callback_data=data)


def ikb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def menu_btn() -> InlineKeyboardButton:
    return btn("🏠 Главное меню", "nav_menu")


def admin_back_btn() -> InlineKeyboardButton:
    return btn("◀️ В админ-панель", "adm_menu")


def back_menu_kb() -> InlineKeyboardMarkup:
    return ikb([menu_btn()])


# =========================================================
# ГЛАВНОЕ МЕНЮ И ВЫБОР НАПРАВЛЕНИЯ
# =========================================================

def main_menu_kb(*, in_progress: bool, has_result: bool, is_admin: bool) -> InlineKeyboardMarkup:
    rows = []
    if in_progress:
        rows.append([btn("▶️ Продолжить собеседование", "interview_continue")])
        rows.append([btn("🔄 Сменить направление", "menu_start")])
    else:
        rows.append([btn("🚀 Начать собеседование", "menu_start")])
    if has_result:
        rows.append([btn("📊 Мои результаты и резюме", "results")])
    rows.append([btn("🎁 Бонусы и друзья", "ref"), btn("⭐️ Отзывы", "reviews:0")])
    rows.append([btn("💬 Поддержка", "support"), btn("ℹ️ Помощь", "help")])
    if is_admin:
        rows.append([btn("🛠 Админ-панель", "adm_menu")])
    return ikb(*rows)


def tracks_kb() -> InlineKeyboardMarkup:
    rows = [[btn(name, f"track:{key}")] for key, name in TRACKS.items()]
    rows.append([menu_btn()])
    return ikb(*rows)


def rules_kb(track: str) -> InlineKeyboardMarkup:
    return ikb(
        [btn("🚀 Начать собеседование", f"begin:{track}")],
        [btn("◀️ Другое направление", "menu_start")],
        [menu_btn()],
    )


# =========================================================
# СОБЕСЕДОВАНИЕ
# =========================================================

def interview_kb(*, hint_shown: bool = False) -> InlineKeyboardMarkup:
    first_row = [btn("⏭ Пропустить", "iv_skip")]
    if not hint_shown:
        first_row.insert(0, btn("💡 Подсказка", "iv_hint"))
    return ikb(
        first_row,
        [btn("🔄 Начать заново", "iv_reset"), btn("🏁 Завершить", "iv_finish")],
        [btn("🏠 Главное меню (прогресс сохранится)", "nav_menu")],
    )


def reset_confirm_kb() -> InlineKeyboardMarkup:
    return ikb(
        [btn("⚠️ Да, начать заново", "iv_reset_ok")],
        [btn("↩️ Вернуться к вопросу", "iv_back")],
    )


def finish_confirm_kb() -> InlineKeyboardMarkup:
    return ikb(
        [btn("🏁 Да, завершить и получить разбор", "iv_finish_ok")],
        [btn("↩️ Вернуться к вопросу", "iv_back")],
    )


def back_to_question_kb() -> InlineKeyboardMarkup:
    return ikb([btn("↩️ Вернуться к вопросу", "iv_back")], [menu_btn()])


def finished_kb() -> InlineKeyboardMarkup:
    return ikb(
        [btn("📄 Черновик резюме", "res_resume"), btn("✍️ Оставить отзыв", "rv_new")],
        [btn("🔄 Пройти другое направление", "menu_start")],
        [menu_btn()],
    )


def continue_kb() -> InlineKeyboardMarkup:
    return ikb([btn("▶️ Продолжить собеседование", "interview_continue")], [menu_btn()])


# =========================================================
# ОПЛАТА
# =========================================================

def paywall_kb(
    *,
    rub_price: int,
    stars_price: int,
    bonuses_available: int,
    bonuses_applied: bool,
    promo_applied: bool,
) -> InlineKeyboardMarkup:
    rows = []
    if rub_price > 0:
        rows.append([btn(f"💳 Карта / СБП — {rub_price} ₽", "pay_yk")])
        if stars_price > 0:
            rows.append([btn(f"⭐️ Telegram Stars — {stars_price}", "pay_stars")])
    else:
        rows.append([btn("🎉 Открыть доступ бесплатно", "pay_free")])

    if bonuses_applied:
        rows.append([btn("↩️ Не списывать бонусы", "pay_bonus_off")])
    elif bonuses_available > 0:
        rows.append([btn(f"🎁 Списать бонусы ({bonuses_available})", "pay_bonus_on")])

    if promo_applied:
        rows.append([btn("✖️ Убрать промокод", "pay_promo_off")])
    else:
        rows.append([btn("🎟 Ввести промокод", "pay_promo")])

    rows.append([btn("🏁 Завершить собеседование", "iv_finish")])
    rows.append([menu_btn()])
    return ikb(*rows)


def back_to_paywall_kb() -> InlineKeyboardMarkup:
    return ikb([btn("◀️ Назад к оплате", "pay_open")], [menu_btn()])


def yookassa_kb(url: str, payment_id: str) -> InlineKeyboardMarkup:
    return ikb(
        [btn("💳 Перейти к оплате", url=url)],
        [btn("🔄 Я оплатил — проверить", f"check_yk:{payment_id}")],
        [btn("◀️ Назад к способам оплаты", "pay_open")],
    )


def stars_invoice_kb(stars_price: int) -> InlineKeyboardMarkup:
    # По правилам Telegram первая кнопка в счёте обязана быть кнопкой оплаты
    return ikb(
        [btn(f"Оплатить {stars_price} ⭐️", pay=True)],
        [btn("◀️ Назад к способам оплаты", "pay_open")],
    )


# =========================================================
# ОТЗЫВЫ
# =========================================================

def stars_rating_kb() -> InlineKeyboardMarkup:
    return ikb(
        [btn(f"{i} ⭐", f"rv_rate:{i}") for i in range(1, 6)],
        [btn("◀️ Назад к отзывам", "reviews:0")],
    )


def review_text_kb() -> InlineKeyboardMarkup:
    return ikb([btn("◀️ Изменить оценку", "rv_new")], [btn("✖️ Отмена", "reviews:0")])


def review_notification_rows(review_id: int) -> list[list[InlineKeyboardButton]]:
    """Кнопки модерации в уведомлении админу о новом отзыве."""
    return [
        [btn("✅ Опубликовать", f"adm_rvn:ok:{review_id}"), btn("🚫 Отклонить", f"adm_rvn:no:{review_id}")],
        [btn("🗑 Удалить", f"adm_rvn:del:{review_id}")],
    ]


# =========================================================
# УВЕДОМЛЕНИЯ
# =========================================================

def dismiss_kb() -> InlineKeyboardMarkup:
    return ikb(dismiss_row())
