"""
yookassa_service.py — прямое взаимодействие с API ЮKassa (СБП и банковские карты).

В metadata платежа сохраняется всё, что нужно для его зачёта: пользователь, направление,
списываемые бонусы и промокод. Благодаря этому доступ открывается ровно к тому направлению,
за которое заплатили, даже если пользователь успел переключиться на другое.
"""
import logging
import socket
import uuid
from typing import Optional

import aiohttp

from config import SBP_PRICE_RUB, YOOKASSA_SECRET_KEY, YOOKASSA_SHOP_ID

logger = logging.getLogger(__name__)

API_URL = "https://api.yookassa.ru/v3/payments"


def _get_connector() -> aiohttp.TCPConnector:
    """
    TCP-коннектор с форсированным IPv4 (обход проблем с резолвом/IPv6 у некоторых хостингов).
    Проверка TLS-сертификата не отключается.
    """
    return aiohttp.TCPConnector(family=socket.AF_INET)


def _auth() -> aiohttp.BasicAuth:
    return aiohttp.BasicAuth(login=str(YOOKASSA_SHOP_ID).strip(), password=str(YOOKASSA_SECRET_KEY).strip())


async def create_yookassa_payment(
    user_id: int,
    username: str | None = None,
    amount: int | None = None,
    *,
    track: str | None = None,
    bonus_cost: int = 0,
    promo: str | None = None,
    description: str | None = None,
    return_url: str = "https://t.me",
) -> tuple[str | None, str | None]:
    """Создаёт платёж и возвращает (ссылка на оплату, id платежа) или (None, None) при ошибке."""
    final_price = amount if amount is not None else SBP_PRICE_RUB

    logger.info("➡️ [ЮKassa] Запрос платежа: user_id=%s, трек=%s, сумма=%s ₽", user_id, track, final_price)

    payload = {
        "amount": {"value": f"{final_price}.00", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": (description or f"Доступ к тренажёру собеседований (ID: {user_id})")[:128],
        "metadata": {
            "user_id": str(user_id),
            "username": username or "",
            "track": track or "",
            "bonus_cost": str(int(bonus_cost or 0)),
            "promo": promo or "",
            "final_amount": str(final_price),
        },
    }
    headers = {"Idempotence-Key": str(uuid.uuid4()), "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=20, connect=10)

    try:
        async with aiohttp.ClientSession(auth=_auth(), timeout=timeout, connector=_get_connector()) as session:
            async with session.post(API_URL, json=payload, headers=headers) as resp:
                status = resp.status
                data = await resp.json()
                logger.info("⬅️ [ЮKassa] HTTP %d, payment_id=%s", status, data.get("id"))
                if status in (200, 201):
                    return data.get("confirmation", {}).get("confirmation_url"), data.get("id")
                logger.error("❌ Ошибка ответа ЮKassa: HTTP %d, %s", status, data.get("description"))
                return None, None
    except Exception as e:
        logger.exception("❌ Исключение при запросе к ЮKassa: %s", e)
        return None, None


async def get_yookassa_payment(payment_id: str) -> Optional[dict]:
    """Полные данные платежа из ЮKassa (статус, сумма, metadata, признак test) или None."""
    timeout = aiohttp.ClientTimeout(total=20, connect=10)
    try:
        async with aiohttp.ClientSession(auth=_auth(), timeout=timeout, connector=_get_connector()) as session:
            async with session.get(f"{API_URL}/{payment_id}") as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.warning("ЮKassa: платёж %s не получен, HTTP %d", payment_id, resp.status)
                return None
    except Exception as e:
        logger.exception("❌ Ошибка проверки платежа %s: %s", payment_id, e)
        return None


async def check_yookassa_payment(payment_id: str) -> bool:
    """Старый интерфейс — оставлен для совместимости."""
    data = await get_yookassa_payment(payment_id)
    return bool(data) and data.get("status") == "succeeded"
