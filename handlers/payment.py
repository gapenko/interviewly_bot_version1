"""
handlers/payment.py — приём платежей, промокоды и списание бонусов с кнопками отмены.
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
from keyboards import get_cancel_promo_keyboard, get_dynamic_paywall_keyboard
from states import InterviewStates, PaymentStates
import yookassa_service

logger = logging.getLogger(__name__)
router = Router(name="payment")

BASE_RUB_PRICE = getattr(config, "SBP_PRICE_RUB", 100)
BASE_STARS_PRICE = getattr(config, "ACCESS_PRICE_STARS", getattr(config, "STARS_PRICE", 50))


async def calculate_prices(user_id: int, state: FSMContext) -> dict:
    data = await state.get_data()
    user = await storage.get_user(user_id)

    promo_discount = data.get("promo_discount", 0)
    apply_bonuses = data.get("apply_bonuses", False)
    user_bonuses = user.get("bonus_balance", 0)

    rub_after_promo = int(BASE_RUB_PRICE * (100 - promo_discount) / 100)
    stars_after_promo = int(BASE_STARS_PRICE * (100 - promo_discount) / 100)

    used_rub_bonuses = 0
    used_stars_bonuses = 0
    final_rub = rub_after_promo
    final_stars = stars_after_promo

    if apply_bonuses and user_bonuses > 0:
        used_rub_bonuses = min(user_bonuses, final_rub)
        final_rub = max(0, final_rub - used_rub_bonuses)

        max_star_discount = user_bonuses // 4
        used_stars_bonuses = min(max_star_discount, final_stars)
        final_stars = max(0, final_stars - used_stars_bonuses)

    return {
        "base_rub": BASE_RUB_PRICE,
        "base_stars": BASE_STARS_PRICE,
        "final_rub": final_rub,
        "final_stars": final_stars,
        "promo_discount": promo_discount,
        "promo_code": data.get("promo_code"),
        "user_bonuses": user_bonuses,
        "apply_bonuses": apply_bonuses,
        "used_rub_bonuses": used_rub_bonuses,
    }


async def render_paywall(target: Message | CallbackQuery, state: FSMContext, user_id: int, edit: bool = False):
    calc = await calculate_prices(user_id, state)

    rub_price = calc["final_rub"]
    stars_price = calc["final_stars"]

    discount_lines = []
    if calc["promo_discount"] > 0:
        discount_lines.append(f"🎟 Промокод <code>{calc['promo_code']}</code>: <b>-{calc['promo_discount']}%</b>")

    if calc["apply_bonuses"] and calc["used_rub_bonuses"] > 0:
        discount_lines.append(f"🎁 Списано бонусов: <b>-{calc['used_rub_bonuses']} ₽</b>")

    discount_text = "\n".join(discount_lines) + "\n" if discount_lines else ""

    text = (
        "💎 <b>Оформление полного доступа к собеседованию</b>\n\n"
        "Вам откроются все 15 глубоких вопросов, рецензии по каждому ответу, "
        "итоговая оценка компетенций и готовое резюме в формате Word (.docx).\n\n"
        f"{discount_text}"
        f"💰 <b>К оплате:</b> <b>{rub_price} ₽</b> или <b>{stars_price} ⭐️</b>\n"
        f"💼 Доступно бонусов на счёте: <b>{calc['user_bonuses']}</b>\n\n"
        "<i>Используйте кнопки ниже для оплаты, ввода промокода или списания бонусов:</i>"
    )

    kb = get_dynamic_paywall_keyboard(
        rub_price=rub_price,
        stars_price=stars_price,
        has_bonuses=(calc["user_bonuses"] > 0),
        bonuses_applied=calc["apply_bonuses"],
        has_promo=bool(calc["promo_code"]),
    )

    if edit and isinstance(target, CallbackQuery) and target.message:
        await target.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    elif isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("pay"))
async def cmd_pay(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    user = await storage.get_user(message.from_user.id)
    if user.get("paid"):
        await message.answer("✅ У вас уже оформлен полный доступ ко всем вопросам!")
        return

    await state.set_state(InterviewStates.waiting_payment)
    await render_paywall(message, state, message.from_user.id)


# --- ВВОД ПРОМОКОДА И КНОПКА ОТМЕНЫ ---

@router.callback_query(F.data == "pay_enter_promocode")
async def cb_enter_promo_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(PaymentStates.waiting_promocode)
    await callback.message.edit_text(
        "🎟 <b>Активация промокода</b>\n\n"
        "Отправьте кодовое слово промокода ответным сообщением в чат.\n\n"
        "<i>Если хотите вернуться назад, нажмите кнопку ниже:</i>",
        reply_markup=get_cancel_promo_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "cancel_promocode_input")
async def cb_cancel_promo(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Ввод промокода отменён")
    await state.set_state(InterviewStates.waiting_payment)
    await render_paywall(callback, state, callback.from_user.id, edit=True)


@router.message(PaymentStates.waiting_promocode, F.text)
async def process_promocode_input(message: Message, state: FSMContext):
    code_text = message.text.strip().upper()

    promo = await storage.apply_promocode(code_text)
    if not promo:
        await message.answer(
            "❌ <b>Промокод не найден или срок его действия истёк.</b>\n"
            "Попробуйте ввести другой или вернитесь к оплате:",
            reply_markup=get_cancel_promo_keyboard(),
            parse_mode="HTML",
        )
        return

    discount = promo["discount_percent"]
    await state.update_data(promo_code=code_text, promo_discount=discount)
    await state.set_state(InterviewStates.waiting_payment)

    await message.answer(
        f"✅ <b>Промокод {code_text} успешно активирован!</b>\n"
        f"Предоставлена скидка: <b>{discount}%</b>",
        parse_mode="HTML",
    )

    await render_paywall(message, state, message.from_user.id)


# --- СПИСАНИЕ БОНУСОВ ---

@router.callback_query(F.data == "pay_apply_bonuses")
async def cb_apply_bonuses(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Бонусы применены!")
    await state.update_data(apply_bonuses=True)
    await render_paywall(callback, state, callback.from_user.id, edit=True)


# --- БЕСПЛАТНЫЙ ДОСТУП ---

@router.callback_query(F.data == "pay_free_unlock")
async def cb_free_unlock(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = callback.from_user.id
    calc = await calculate_prices(user_id, state)

    if calc["apply_bonuses"] and calc["used_rub_bonuses"] > 0:
        await storage.adjust_user_bonuses(user_id, -calc["used_rub_bonuses"])

    await storage.mark_paid(user_id)
    await state.set_state(InterviewStates.waiting_answer)

    await callback.message.answer(
        "🎉 <b>Поздравляем! Полный доступ успешно разблокирован!</b>\n\n"
        "Скидка 100% применена. Продолжаем собеседование!",
        parse_mode="HTML",
    )

    from handlers.start import ask_current_question
    if callback.message:
        await ask_current_question(callback.message, state, user_id)


# --- ЮKASSA ---

@router.callback_query(F.data == "pay_yookassa")
async def cb_pay_yookassa(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    username = callback.from_user.username
    calc = await calculate_prices(user_id, state)
    rub_price = calc["final_rub"]

    wait_msg = None
    if callback.message:
        wait_msg = await callback.message.answer("⏳ <i>Формирую ссылку на оплату...</i>", parse_mode="HTML")

    url, payment_id = await yookassa_service.create_yookassa_payment(user_id, username, amount=rub_price)

    if wait_msg:
        try:
            await wait_msg.delete()
        except Exception:
            pass

    if not url or not payment_id:
        if callback.message:
            await callback.message.answer(
                "⚠️ <b>Не удалось сформировать счёт.</b> Попробуйте позже или напишите в поддержку.",
                parse_mode="HTML",
            )
        await callback.answer()
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате (СБП / Карта)", url=url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_yk:{payment_id}")],
            [InlineKeyboardButton(text="◀️ Назад к выбору оплаты", callback_data="nav_back_to_paywall")],
        ]
    )

    if callback.message:
        await callback.message.answer(
            f"🧾 <b>Счёт на оплату:</b>\n\n"
            f"• <b>К оплате:</b> {rub_price} ₽\n"
            f"• <b>Способ:</b> СБП (QR-код) или банковская карта\n\n"
            "После оплаты нажмите <b>«Проверить оплату»</b>.",
            reply_markup=kb,
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "nav_back_to_paywall")
async def cb_back_to_paywall(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await render_paywall(callback, state, callback.from_user.id, edit=True)


@router.callback_query(F.data.startswith("check_yk:"))
async def cb_check_yk(callback: CallbackQuery, state: FSMContext) -> None:
    payment_id = callback.data.replace("check_yk:", "")
    is_paid = await yookassa_service.check_yookassa_payment(payment_id)

    if not is_paid:
        await callback.answer("⏳ Оплата ещё не поступила. Завершите платёж в банке и нажмите снова.", show_alert=True)
        return

    user_id = callback.from_user.id
    username = callback.from_user.username
    calc = await calculate_prices(user_id, state)

    if calc["apply_bonuses"] and calc["used_rub_bonuses"] > 0:
        await storage.adjust_user_bonuses(user_id, -calc["used_rub_bonuses"])

    await storage.mark_paid(user_id)
    await storage.log_payment(
        telegram_id=user_id,
        username=username,
        amount_stars=calc["final_rub"],
        telegram_payment_charge_id=payment_id,
        provider_payment_charge_id=payment_id,
        currency="RUB",
        invoice_payload=f"yookassa_{payment_id}",
    )

    await callback.answer("Оплата подтверждена!")
    if callback.message:
        await callback.message.answer(
            "🎉 <b>Оплата прошла успешно!</b>\n\n"
            "Вам открыт полный доступ ко всем 15 вопросам собеседования и модулю резюме.",
            parse_mode="HTML",
        )

    await state.set_state(InterviewStates.waiting_answer)

    from handlers.start import ask_current_question
    if callback.message:
        await ask_current_question(callback.message, state, user_id)


# --- TELEGRAM STARS ---

@router.callback_query(F.data == "pay_stars_invoice")
async def cb_pay_stars(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    calc = await calculate_prices(user_id, state)
    stars_price = calc["final_stars"]

    if callback.message:
        prices = [LabeledPrice(label="Полный доступ к IT-собеседованию", amount=stars_price)]
        await callback.message.answer_invoice(
            title="Полный доступ к собеседованию",
            description=f"Доступ ко всем 15 вопросам и резюме со скидкой. К оплате: {stars_price} Stars.",
            payload="full_interview_access_stars",
            currency="XTR",
            prices=prices,
        )
    await callback.answer()


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.successful_payment:
        return

    payment = message.successful_payment
    user_id = message.from_user.id
    username = message.from_user.username
    calc = await calculate_prices(user_id, state)

    if calc["apply_bonuses"] and calc["used_rub_bonuses"] > 0:
        await storage.adjust_user_bonuses(user_id, -calc["used_rub_bonuses"])

    await storage.mark_paid(user_id)
    await storage.log_payment(
        telegram_id=user_id,
        username=username,
        amount_stars=payment.total_amount,
        telegram_payment_charge_id=payment.telegram_payment_charge_id,
        provider_payment_charge_id=payment.provider_payment_charge_id,
        currency=payment.currency,
        invoice_payload=payment.invoice_payload,
    )

    await message.answer(
        "🎉 <b>Оплата через Stars успешно завершена!</b>\n\n"
        "Полный доступ открыт. Продолжаем подготовку!",
        parse_mode="HTML",
    )

    await state.set_state(InterviewStates.waiting_answer)

    from handlers.start import ask_current_question
    await ask_current_question(message, state, user_id)