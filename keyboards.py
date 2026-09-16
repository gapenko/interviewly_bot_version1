"""
keyboards.py — инлайн-тулбары, сетка выбора треков и меню.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import config
from questions import TRACKS

# Безопасное чтение цен из config.py
ACCESS_PRICE_STARS = getattr(
    config, "ACCESS_PRICE_STARS", getattr(config, "PRICE_STARS", getattr(config, "STARS_PRICE", 50))
)
SBP_PRICE_RUB = getattr(
    config, "SBP_PRICE_RUB", getattr(config, "PRICE_RUB", getattr(config, "PAYMENT_AMOUNT", 100))
)


def get_tracks_keyboard() -> InlineKeyboardMarkup:
    """Генерирует аккуратную сетку выбора специальности по 2 в ряд."""
    buttons = []
    row = []
    for track_key, track_name in TRACKS.items():
        row.append(InlineKeyboardButton(text=track_name, callback_data=f"track_{track_key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_interview_toolbar() -> InlineKeyboardMarkup:
    """Тулбар управления под каждым вопросом."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏸ Пауза / Справка", callback_data="cmd_help"),
                InlineKeyboardButton(text="🔄 Сброс", callback_data="cmd_reset_prompt"),
            ]
        ]
    )


def get_reset_confirm_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение сброса прогресса."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚠️ Да, сбросить прогресс", callback_data="cmd_reset_confirm")],
            [InlineKeyboardButton(text="↩️ Вернуться к вопросу", callback_data="cmd_resume")],
        ]
    )


def get_paywall_keyboard() -> InlineKeyboardMarkup:
    """Кнопки оплаты тарифа."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Банковская карта / СБП — {SBP_PRICE_RUB} ₽", callback_data="pay_yookassa")],
            [InlineKeyboardButton(text=f"⭐️ Telegram Stars — {ACCESS_PRICE_STARS} XTR", callback_data="pay_stars")],
        ]
    )


def get_finished_keyboard() -> InlineKeyboardMarkup:
    """Кнопки итогового экрана."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Пройти другое направление", callback_data="cmd_reset_confirm")]
        ]
    )


def get_resume_menu_keyboard() -> InlineKeyboardMarkup:
    """Меню резюме (требуется для совместимости)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Черновик резюме", callback_data="resume_draft")],
            [InlineKeyboardButton(text="🔍 Аудит текущего резюме", callback_data="resume_audit")],
            [InlineKeyboardButton(text="📥 Скачать .docx", callback_data="resume_download")],
        ]
    )