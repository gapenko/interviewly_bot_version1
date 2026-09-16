"""
handlers/payment.py — приём платежей (СБП/карты через ЮKassa и Telegram Stars).
"""
import logging
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

import config
import storage
from states import InterviewStates
import yookassa_service

logger = logging.getLogger(__name__)

router = Router(name="payment")

RUB_PRICE = getattr(config, "SBP_PRICE_RUB", 100)
STARS_PRICE = getattr(config, "STARS_PRICE", getattr(config, "ACCESS_PRICE_STARS", 50))
SBP_BANNER_URL = "https://raw.githubusercontent.com/tandpfun/skill-icons/main/icons/FastAPI.svg"  # Надежный плейсхолдер или прямая ссылка на логотип СБП


def get_paywall_inline_keyboard() -> InlineKeyboardMarkup:
    """Лаконичная клавиатура выбора метода оплаты."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⚡️ Оплатить через СБП / Картой — {RUB_PRICE} ₽",
                    callback_data="pay_yookassa",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⭐️ Telegram Stars — {STARS_PRICE} XTR",
                    callback_data="pay_stars_invoice",
                )
            ],
        ]
    )


async def send_paywall(message_or_call, user_id: int) -> None:
    """Универсальная отправка чистого экрана оплаты с баннером."""
    caption = (
        "🔒 <b>Полный доступ к ассессменту</b>\n\n"
        "▸ Разблокировка вопросов 3–15 по вашей специальности\n"
        "▸ Анализ Hard & Soft Skills от Senior-интервьюера\n"
        "▸ Готовое резюме и отчет в формате <code>.docx</code>\n\n"
        f"<b>К оплате:</b> <code>{RUB_PRICE} ₽</code> или <code>{STARS_PRICE} ⭐️</code>"
    )
    
    kb = get_paywall_inline_keyboard()
    msg = message_or_call.message if isinstance(message_or_call, CallbackQuery) else message_or_call

    # Отправляем фото с подписью СБП
    try:
        await msg.answer_photo(
            photo="https://cdn-icons-png.flaticon.com/512/893/893081.png",
            caption=caption,
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
        await msg.answer(caption, reply_markup=kb, parse_mode="HTML")


@router.message(Command("pay"))
async def cmd_pay(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return

    user = await storage.get_user(message.from_user.id, message.from_user.username)
    if user.get("paid"):
        await message.answer("✅ У вас уже оплачен полный доступ ко всем вопросам.")
        return

    await state.set_state(InterviewStates.waiting_payment)
    await send_paywall(message, message.from_user.id)


@router.callback_query(F.data == "pay_yookassa")
async def cb_pay_yookassa(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    username = callback.from_user.username

    wait_msg = await callback.message.answer("⏳ <i>Формирую ссылку на оплату через СБП...</i>", parse_mode="HTML")
    url, payment_id = await yookassa_service.create_yookassa_payment(user_id, username)

    try:
        await wait_msg.delete()
    except Exception:
        pass

    if not url or not payment_id:
        await callback.message.answer("⚠️ Не удалось сформировать платеж. Попробуйте еще раз позже.")
        await callback.answer()
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡️ Оплатить (СБП / Карта)", url=url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_yk:{payment_id}")],
        ]
    )

    await callback.message.answer(
        f"🧾 <b>Счет сформирован</b>\n\n"
        f"Сумма: <b>{RUB_PRICE} ₽</b>\n\n"
        f"1. Нажмите кнопку <b>«Оплатить»</b> и подтвердите перевод через СБП.\n"
        f"2. После возвращения нажмите <b>«Проверить оплату»</b>.",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("check_yk:"))
async def cb_check_yk(callback: CallbackQuery, state: FSMContext) -> None:
    payment_id = callback.data.replace("check_yk:", "")
    is_paid = await yookassa_service.check_yookassa_payment(payment_id)

    if not is_paid:
        await callback.answer("⏳ Оплата еще не поступила. Завершите перевод и нажмите снова.", show_alert=True)
        return

    user_id = callback.from_user.id
    username = callback.from_user.username

    await storage.mark_paid(user_id)
    await storage.log_payment(
        telegram_id=user_id,
        username=username,
        amount_stars=RUB_PRICE,
        telegram_payment_charge_id=payment_id,
        currency="RUB",
        invoice_payload=f"yookassa_{payment_id}",
    )

    await callback.answer("Оплата подтверждена!", show_alert=True)
    await callback.message.answer(
        "🎉 <b>Оплата прошла успешно!</b>\n\n"
        "Полный доступ разблокирован. Переходим к следующему вопросу!",
        parse_mode="HTML",
    )

    await state.set_state(InterviewStates.waiting_answer)
    from handlers.interview import send_next_question
    await send_next_question(callback.message, state, user_id)


@router.callback_query(F.data == "pay_stars_invoice")
async def cb_pay_stars(callback: CallbackQuery) -> None:
    prices = [LabeledPrice(label="Полный доступ к ассессменту", amount=STARS_PRICE)]
    await callback.message.answer_invoice(
        title="Полный доступ к IT-собеседованию",
        description="Доступ ко всем вопросам, разборам ментора и резюме (.docx)",
        payload="full_access_stars",
        currency="XTR",
        prices=prices,
    )
    await callback.answer()


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout: PreCheckoutQuery) -> None:
    await pre_checkout.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.successful_payment:
        return

    payment = message.successful_payment
    user_id = message.from_user.id
    username = message.from_user.username

    await storage.mark_paid(user_id)
    await storage.log_payment(
        telegram_id=user_id,
        username=username,
        amount_stars=payment.total_amount,
        telegram_payment_charge_id=payment.telegram_payment_charge_id,
        currency=payment.currency,
        invoice_payload=payment.invoice_payload,
    )

    await message.answer("🎉 <b>Оплата подтверждена!</b> Продолжаем собеседование.", parse_mode="HTML")
    await state.set_state(InterviewStates.waiting_answer)

    from handlers.interview import send_next_question
    await send_next_question(message, state, user_id)