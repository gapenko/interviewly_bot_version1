"""
yookassa_service.py — прямое взаимодействие с API ЮKassa (СБП и банковские карты).
Поддерживает базовую цену из config.py и динамическую цену с учётом скидок/бонусов.
"""
import logging
import socket
import uuid
import aiohttp

from config import SBP_PRICE_RUB, YOOKASSA_SECRET_KEY, YOOKASSA_SHOP_ID

logger = logging.getLogger(__name__)

API_URL = "https://api.yookassa.ru/v3/payments"


def _get_connector() -> aiohttp.TCPConnector:
    """
    Создаёт TCP-коннектор с форсированным IPv4 (обход проблем с резолвом/IPv6 у некоторых хостингов).
    Проверка TLS-сертификата НЕ отключается — соединение с API оплаты обязано быть проверенным,
    иначе запросы (включая секретный ключ магазина) становятся уязвимы к перехвату (MITM).
    """
    return aiohttp.TCPConnector(family=socket.AF_INET)


async def create_yookassa_payment(
    user_id: int,
    username: str | None = None,
    amount: int | None = None,
) -> tuple[str | None, str | None]:
    """
    Генерирует ссылку на оплату через ЮKassa.
    Если передан amount — выставляет счёт на эту сумму (с учётом промокодов/бонусов).
    Если amount не передан — берёт базовую цену SBP_PRICE_RUB.
    """
    shop_id = str(YOOKASSA_SHOP_ID).strip()
    secret_key = str(YOOKASSA_SECRET_KEY).strip()
    idempotence_key = str(uuid.uuid4())

    final_price = amount if amount is not None else SBP_PRICE_RUB

    logger.info(
        "➡️ [ЮKassa REST] Запрос платежа: shop_id='%s', user_id=%s, сумма=%s ₽",
        shop_id,
        user_id,
        final_price,
    )

    payload = {
        "amount": {
            "value": f"{final_price}.00",
            "currency": "RUB",
        },
        "confirmation": {
            "type": "redirect",
            "return_url": "https://t.me",
        },
        "capture": True,
        "description": f"Доступ к IT-тренажеру (ID: {user_id})",
        "metadata": {
            "user_id": str(user_id),
            "username": username or "",
            "final_amount": str(final_price),
        },
    }

    headers = {
        "Idempotence-Key": idempotence_key,
        "Content-Type": "application/json",
    }

    auth = aiohttp.BasicAuth(login=shop_id, password=secret_key)
    timeout = aiohttp.ClientTimeout(total=20, connect=10)

    try:
        async with aiohttp.ClientSession(auth=auth, timeout=timeout, connector=_get_connector()) as session:
            async with session.post(API_URL, json=payload, headers=headers) as resp:
                status = resp.status
                data = await resp.json()

                logger.info("⬅️ [ЮKassa REST] HTTP %d, payment_id=%s", status, data.get("id"))

                if status in (200, 201):
                    confirmation_url = data.get("confirmation", {}).get("confirmation_url")
                    payment_id = data.get("id")
                    return confirmation_url, payment_id
                else:
                    logger.error("❌ Ошибка ответа ЮKassa: HTTP %d", status)
                    return None, None

    except Exception as e:
        logger.exception("❌ Исключение при запросе к ЮKassa: %s", e)
        return None, None


async def check_yookassa_payment(payment_id: str) -> bool:
    """Проверяет фактический статус платежа в ЮKassa."""
    shop_id = str(YOOKASSA_SHOP_ID).strip()
    secret_key = str(YOOKASSA_SECRET_KEY).strip()
    auth = aiohttp.BasicAuth(login=shop_id, password=secret_key)
    timeout = aiohttp.ClientTimeout(total=20, connect=10)

    try:
        async with aiohttp.ClientSession(auth=auth, timeout=timeout, connector=_get_connector()) as session:
            async with session.get(f"{API_URL}/{payment_id}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("status") == "succeeded"
                return False
    except Exception as e:
        logger.exception("❌ Ошибка проверки платежа %s: %s", payment_id, e)
        return False
