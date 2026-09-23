"""
handlers/payment.py — оплата доступа к направлению, промокоды и бонусы.

- Оплата открывает доступ ТОЛЬКО к направлению, за которое заплатили (оно зашивается в платёж).
- Промокод и списание бонусов хранятся в записи пользователя, а не в FSM: они не теряются
  при переходах по меню и перезапуске бота. Активация промокода и бонусы списываются только
  после успешной оплаты.
- Каждый платёж засчитывается один раз (защита от повторного нажатия «Проверить оплату»
  и от повторного использования старого счёта для другого направления).
"""
import logging
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

import config
import storage
import yookassa_service
from keyboards import back_to_paywall_kb, btn, continue_kb, ikb, menu_btn, paywall_kb, stars_invoice_kb, yookassa_kb
from menus import get_bot_username, interview_in_progress, track_title, tracks_view
from screen import delete_user_message, esc, not_command, replace_screen, show_screen
from states import PaymentStates

logger = logging.getLogger(__name__)
router = Router(name="payment")

BASE_RUB_PRICE = config.SBP_PRICE_RUB
BASE_STARS_PRICE = config.ACCESS_PRICE_STARS


def _chat_id(callback: CallbackQuery) -> int:
    return callback.message.chat.id if callback.message else callback.from_user.id


# =========================================================
# РАСЧЁТ ЦЕНЫ
# =========================================================

async def calculate_prices(uid: int, user: dict) -> dict:
    promo_code = user.get("pending_promo")
    discount = 0
    if promo_code:
        promo, _ = await storage.check_promocode(promo_code, uid)
        if promo:
            discount = max(0, min(100, int(promo.get("discount_percent", 0))))
        else:
            promo_code = None  # промокод удалили, исчерпали или он уже использован

    rub_after_promo = BASE_RUB_PRICE * (100 - discount) // 100
    stars_after_promo = BASE_STARS_PRICE * (100 - discount) // 100

    bonuses = int(user.get("bonus_balance", 0) or 0)
    apply_bonuses = bool(user.get("apply_bonuses")) and bonuses > 0

    rub_bonus = min(bonuses, rub_after_promo) if apply_bonuses else 0
    stars_bonus = min(bonuses // config.BONUSES_PER_STAR, stars_after_promo) if apply_bonuses else 0

    return {
        "discount": discount,
        "promo_code": promo_code,
        "rub_before_bonus": rub_after_promo,
        "stars_before_bonus": stars_after_promo,
        "final_rub": rub_after_promo - rub_bonus,
        "final_stars": stars_after_promo - stars_bonus,
        "rub_bonus_cost": rub_bonus,                                   # бонусов спишется при оплате в рублях
        "stars_bonus_cost": stars_bonus * config.BONUSES_PER_STAR,     # бонусов спишется при оплате в Stars
        "stars_bonus_discount": stars_bonus,
        "bonuses": bonuses,
        "apply_bonuses": apply_bonuses,
    }


# =========================================================
# ЭКРАНЫ
# =========================================================

async def paywall_view(uid: int, *, prefix: str = "", notice: str | None = None):
    """Экран оплаты для текущего направления пользователя. prefix — например, комментарий к последнему ответу."""
    user = await storage.get_user(uid)
    track = user.get("track")
    if not track:
        return tracks_view(user, notice="Сначала выберите направление — оплата открывает доступ к конкретному направлению.")

    calc = await calculate_prices(uid, user)
    parts = []
    if prefix:
        parts.append(prefix)
    parts.append(f"💎 <b>Доступ к направлению «{esc(track_title(track))}»</b>")
    if notice:
        parts.append(f"<i>{esc(notice)}</i>")
    intro = (
        "Бесплатная часть собеседования пройдена. "
        if user.get("current_question_index", 0) >= config.FREE_QUESTIONS_COUNT else ""
    )
    parts.append(
        f"{intro}Полный доступ к направлению открывает:\n"
        "• все 15 вопросов с обратной связью после каждого ответа;\n"
        "• итоговый разбор компетенций;\n"
        "• Word-отчёт (.docx) с черновиком резюме."
    )

    has_discount = bool(calc["promo_code"]) or (
        calc["apply_bonuses"] and (calc["rub_bonus_cost"] or calc["stars_bonus_discount"])
    )
    price_lines = [f"Стоимость без скидок: {BASE_RUB_PRICE} ₽ или {BASE_STARS_PRICE} ⭐️"] if has_discount else []
    if calc["promo_code"]:
        price_lines.append(f"🎟 Промокод <code>{esc(calc['promo_code'])}</code>: −{calc['discount']}%")
    if calc["apply_bonuses"] and (calc["rub_bonus_cost"] or calc["stars_bonus_discount"]):
        price_lines.append(f"🎁 Бонусы: −{calc['rub_bonus_cost']} ₽ / −{calc['stars_bonus_discount']} ⭐️")
    if calc["final_rub"] > 0:
        stars_part = f" или {calc['final_stars']} ⭐️" if calc["final_stars"] > 0 else ""
        price_lines.append(f"💰 <b>К оплате: {calc['final_rub']} ₽{stars_part}</b>")
    else:
        price_lines.append("💰 <b>К оплате: 0 ₽</b> — доступ можно открыть бесплатно")
    if calc["bonuses"]:
        price_lines.append(f"💼 Бонусов на счёте: {calc['bonuses']}")
    parts.append("\n".join(price_lines))
    parts.append("<i>Оплата открывает доступ только к этому направлению.</i>")

    kb = paywall_kb(
        rub_price=calc["final_rub"],
        stars_price=calc["final_stars"],
        bonuses_available=calc["bonuses"],
        bonuses_applied=calc["apply_bonuses"],
        promo_applied=bool(calc["promo_code"]),
    )
    return "\n\n".join(parts), kb


async def _show_paywall(bot: Bot, chat_id: int, uid: int, state: FSMContext, *, source=None,
                        force_new: bool = False, notice: str | None = None) -> None:
    await state.clear()
    user = await storage.get_user(uid)
    if user.get("track") and storage.user_has_track_access(user, user.get("track")):
        text = f"✅ У вас уже открыт полный доступ к направлению «{esc(track_title(user.get('track')))}»."
        kb = continue_kb() if interview_in_progress(user) else ikb([menu_btn()])
        await show_screen(bot, chat_id, text, kb, source=source, force_new=force_new)
        return
    text, kb = await paywall_view(uid, notice=notice)
    await show_screen(bot, chat_id, text, kb, source=source, force_new=force_new)


async def _show_success(bot: Bot, chat_id: int, uid: int, track: str, *, source=None, force_new=False,
                        already: bool = False) -> None:
    user = await storage.get_user(uid)
    title = esc(track_title(track))
    if already:
        text = f"✅ Этот платёж уже засчитан. Доступ к направлению «{title}» открыт."
    else:
        text = (
            "🎉 <b>Оплата прошла успешно!</b>\n\n"
            f"Открыт полный доступ к направлению «{title}»: все вопросы, обратная связь, "
            "итоговый разбор и Word-отчёт с черновиком резюме."
        )
    if user.get("track") == track and interview_in_progress(user):
        kb = continue_kb()
    else:
        kb = ikb([btn("🚀 Начать собеседование", f"begin:{track}")], [menu_btn()])
    await show_screen(bot, chat_id, text, kb, source=source, force_new=force_new)


async def _apply_paid_extras(uid: int, track: str, bonus_cost: int, promo: str | None) -> None:
    """После успешной оплаты: списать бонусы, активацию промокода и открыть доступ."""
    if bonus_cost > 0:
        await storage.adjust_user_bonuses(uid, -bonus_cost)
    if promo:
        await storage.consume_promocode(promo, uid)
    await storage.grant_track_access(uid, track)
    await storage.update_user(uid, pending_promo=None, apply_bonuses=False)


# =========================================================
# ОТКРЫТИЕ ЭКРАНА ОПЛАТЫ
# =========================================================

@router.message(Command("pay"))
async def cmd_pay(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    await _show_paywall(bot, message.chat.id, message.from_user.id, state, force_new=True)


@router.callback_query(F.data.in_({"pay_open", "nav_back_to_paywall"}))
async def cb_pay_open(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await _show_paywall(bot, _chat_id(callback), callback.from_user.id, state, source=callback.message)


# =========================================================
# БОНУСЫ И ПРОМОКОДЫ
# =========================================================

@router.callback_query(F.data.in_({"pay_bonus_on", "pay_bonus_off"}))
async def cb_toggle_bonuses(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    enable = callback.data == "pay_bonus_on"
    await storage.update_user(callback.from_user.id, apply_bonuses=enable)
    await callback.answer("Бонусы будут списаны при оплате" if enable else "Бонусы не будут списаны")
    await _show_paywall(bot, _chat_id(callback), callback.from_user.id, state, source=callback.message)


@router.callback_query(F.data == "pay_promo")
async def cb_enter_promo(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await state.set_state(PaymentStates.waiting_promocode)
    await show_screen(
        bot, _chat_id(callback),
        "🎟 <b>Промокод</b>\n\nОтправьте промокод сообщением.",
        back_to_paywall_kb(), source=callback.message,
    )


@router.message(PaymentStates.waiting_promocode, F.text, not_command)
async def process_promocode_input(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_message(message)
    uid = message.from_user.id
    code = message.text.strip().upper()[:32]
    promo, error = await storage.check_promocode(code, uid)
    if not promo:
        await show_screen(
            bot, message.chat.id,
            f"🎟 <b>Промокод</b>\n\n<i>❌ {esc(error)}</i>\n\nПопробуйте другой промокод или вернитесь к оплате.",
            back_to_paywall_kb(),
        )
        return
    await storage.update_user(uid, pending_promo=code)
    await _show_paywall(
        bot, message.chat.id, uid, state,
        notice=f"✅ Промокод {code} применён: скидка {promo['discount_percent']}%.",
    )


@router.callback_query(F.data == "pay_promo_off")
async def cb_remove_promo(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await storage.update_user(callback.from_user.id, pending_promo=None)
    await callback.answer("Промокод убран")
    await _show_paywall(bot, _chat_id(callback), callback.from_user.id, state, source=callback.message)


# =========================================================
# БЕСПЛАТНОЕ ОТКРЫТИЕ (100% скидка промокодом/бонусами)
# =========================================================

@router.callback_query(F.data.in_({"pay_free", "pay_free_unlock"}))
async def cb_free_unlock(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    uid = callback.from_user.id
    user = await storage.get_user(uid)
    track = user.get("track")
    if not track:
        await callback.answer()
        await _show_paywall(bot, _chat_id(callback), uid, state, source=callback.message)
        return

    calc = await calculate_prices(uid, user)
    # Раньше доступ выдавался без проверки, что итоговая цена действительно нулевая
    if calc["final_rub"] > 0:
        await callback.answer("Стоимость доступа не нулевая — выберите способ оплаты.", show_alert=True)
        await _show_paywall(bot, _chat_id(callback), uid, state, source=callback.message)
        return

    await callback.answer()
    await storage.register_payment_once({
        "id": f"free_{uid}_{track}_{int(time.time())}",
        "telegram_id": uid,
        "username": callback.from_user.username,
        "amount": 0,
        "currency": "RUB",
        "method": "free",
        "track": track,
        "promo": calc["promo_code"],
        "bonus_used": calc["rub_bonus_cost"],
    })
    await _apply_paid_extras(uid, track, calc["rub_bonus_cost"], calc["promo_code"])
    await state.clear()
    await _show_success(bot, _chat_id(callback), uid, track, source=callback.message)


# =========================================================
# ЮKASSA
# =========================================================

@router.callback_query(F.data.in_({"pay_yk", "pay_yookassa"}))
async def cb_pay_yookassa(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    uid = callback.from_user.id
    chat_id = _chat_id(callback)
    user = await storage.get_user(uid)
    track = user.get("track")
    if not track or storage.user_has_track_access(user, track):
        await callback.answer()
        await _show_paywall(bot, chat_id, uid, state, source=callback.message)
        return

    calc = await calculate_prices(uid, user)
    if calc["final_rub"] <= 0:
        await callback.answer()
        await _show_paywall(bot, chat_id, uid, state, source=callback.message)
        return

    await callback.answer("Формируем счёт…")
    await show_screen(bot, chat_id, "⏳ <i>Формируем ссылку на оплату…</i>", None, source=callback.message)

    url, payment_id = await yookassa_service.create_yookassa_payment(
        uid,
        callback.from_user.username,
        amount=calc["final_rub"],
        track=track,
        bonus_cost=calc["rub_bonus_cost"],
        promo=calc["promo_code"],
        description=f"Доступ к направлению «{track_title(track)}» (ID: {uid})",
        return_url=f"https://t.me/{await get_bot_username(bot)}",
    )
    if not url or not payment_id:
        text, kb = await paywall_view(uid, notice="Не удалось сформировать счёт. Попробуйте позже или напишите в поддержку.")
        await show_screen(bot, chat_id, text, kb)
        return

    await show_screen(
        bot, chat_id,
        "🧾 <b>Счёт на оплату</b>\n\n"
        f"• Направление: {esc(track_title(track))}\n"
        f"• Сумма: <b>{calc['final_rub']} ₽</b>\n"
        "• Способ: банковская карта или СБП\n\n"
        "После оплаты вернитесь в бот и нажмите «Я оплатил — проверить».",
        yookassa_kb(url, payment_id),
    )


@router.callback_query(F.data.startswith("check_yk:"))
async def cb_check_yk(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    uid = callback.from_user.id
    chat_id = _chat_id(callback)
    payment_id = callback.data.split(":", 1)[1]

    data = await yookassa_service.get_yookassa_payment(payment_id)
    if not data:
        await callback.answer("Не удалось проверить платёж. Попробуйте через минуту.", show_alert=True)
        return

    status = data.get("status")
    if status == "canceled":
        await callback.answer("Платёж отменён. Сформируйте новый счёт.", show_alert=True)
        await _show_paywall(bot, chat_id, uid, state, source=callback.message)
        return
    if status != "succeeded":
        await callback.answer("⏳ Оплата ещё не поступила. Завершите платёж и нажмите снова.", show_alert=True)
        return

    meta = data.get("metadata") or {}
    if str(meta.get("user_id")) != str(uid):
        await callback.answer("Этот платёж оформлен с другого аккаунта.", show_alert=True)
        return

    user = await storage.get_user(uid)
    track = meta.get("track") or user.get("track")
    try:
        amount = int(float((data.get("amount") or {}).get("value", 0)))
    except (TypeError, ValueError):
        amount = 0
    try:
        bonus_cost = int(meta.get("bonus_cost") or 0)
    except ValueError:
        bonus_cost = 0
    promo = meta.get("promo") or None

    is_new = await storage.register_payment_once({
        "id": payment_id,
        "telegram_id": uid,
        "username": callback.from_user.username,
        "amount": amount,
        "currency": (data.get("amount") or {}).get("currency", "RUB"),
        "method": "yookassa",
        "track": track,
        "test": bool(data.get("test", False)),
        "promo": promo,
        "bonus_used": bonus_cost,
        "provider_payment_charge_id": payment_id,
        "invoice_payload": f"yookassa_{payment_id}",
    })
    await callback.answer("Оплата подтверждена!" if is_new else "Платёж уже засчитан")
    if is_new and track:
        await _apply_paid_extras(uid, track, bonus_cost, promo)
    await state.clear()
    await _show_success(bot, chat_id, uid, track, source=callback.message, already=not is_new)


# =========================================================
# TELEGRAM STARS
# =========================================================

def _stars_payload(uid: int, track: str, bonus_cost: int, promo: str | None) -> str:
    return f"st:{uid}:{track}:{bonus_cost}:{promo or ''}"[:128]


def _parse_stars_payload(payload: str) -> dict | None:
    parts = (payload or "").split(":")
    if len(parts) != 5 or parts[0] != "st":
        return None
    try:
        return {"uid": int(parts[1]), "track": parts[2], "bonus_cost": int(parts[3]), "promo": parts[4] or None}
    except ValueError:
        return None


@router.callback_query(F.data.in_({"pay_stars", "pay_stars_invoice"}))
async def cb_pay_stars(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    uid = callback.from_user.id
    chat_id = _chat_id(callback)
    user = await storage.get_user(uid)
    track = user.get("track")
    if not track or storage.user_has_track_access(user, track):
        await callback.answer()
        await _show_paywall(bot, chat_id, uid, state, source=callback.message)
        return

    calc = await calculate_prices(uid, user)
    stars_price = calc["final_stars"]
    if stars_price < 1:
        await callback.answer()
        await _show_paywall(bot, chat_id, uid, state, source=callback.message)
        return

    await callback.answer()
    invoice = await bot.send_invoice(
        chat_id=chat_id,
        title="Доступ к тренажёру собеседований",
        description=f"Полный доступ к направлению «{track_title(track)}»: все вопросы, разбор и Word-отчёт.",
        payload=_stars_payload(uid, track, calc["stars_bonus_cost"], calc["promo_code"]),
        currency="XTR",
        prices=[LabeledPrice(label=f"Направление «{track_title(track)}»", amount=stars_price)],
        reply_markup=stars_invoice_kb(stars_price),
    )
    await replace_screen(bot, chat_id, invoice.message_id)


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    payload = pre_checkout_query.invoice_payload
    parsed = _parse_stars_payload(payload)
    if parsed and parsed["uid"] != pre_checkout_query.from_user.id:
        await pre_checkout_query.answer(ok=False, error_message="Счёт выставлен для другого аккаунта.")
        return
    if not parsed and payload != "full_interview_access_stars":  # старые счета до обновления
        await pre_checkout_query.answer(ok=False, error_message="Счёт устарел. Сформируйте новый в боте.")
        return
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user or not message.successful_payment:
        return
    payment = message.successful_payment
    uid = message.from_user.id
    parsed = _parse_stars_payload(payment.invoice_payload) or {}
    user = await storage.get_user(uid)
    track = parsed.get("track") or user.get("track")
    bonus_cost = parsed.get("bonus_cost", 0)
    promo = parsed.get("promo")

    is_new = await storage.register_payment_once({
        "id": payment.telegram_payment_charge_id,
        "telegram_id": uid,
        "username": message.from_user.username,
        "amount": payment.total_amount,
        "currency": payment.currency,
        "method": "stars",
        "track": track,
        "promo": promo,
        "bonus_used": bonus_cost,
        "provider_payment_charge_id": payment.provider_payment_charge_id,
        "invoice_payload": payment.invoice_payload,
    })
    if is_new and track:
        await _apply_paid_extras(uid, track, bonus_cost, promo)

    await delete_user_message(message)  # служебное сообщение «Вы оплатили…»
    await state.clear()
    if track:
        await _show_success(bot, message.chat.id, uid, track, force_new=True, already=not is_new)
