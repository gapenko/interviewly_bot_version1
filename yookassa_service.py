"""
yookassa_service.py — прямое взаимодействие с REST API ЮKassa.
"""
import aiohttp
import uuid
import logging
import config

logger = logging.getLogger(__name__)

API_URL = "https://api.yookassa.ru/v3/payments"


async def create_yookassa_payment(user_id: int, username: str | None = None) -> tuple[str | None, str | None]:
    shop_id = str(config.YOOKASSA_SHOP_ID).strip()
    secret_key = str(config.YOOKASSA_SECRET_KEY).strip()
    idempotence_key = str(uuid.uuid4())

    bot_username = getattr(config, "BOT_USERNAME", "").replace("@", "")
    return_url = f"https://t.me/{bot_username}" if bot_username else "https://t.me"

    payload = {
        "amount": {
            "value": f"{config.SBP_PRICE_RUB}.00",
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": return_url
        },
        "capture": True,
        "description": f"Доступ к IT-ассессменту (ID: {user_id})",
        "metadata": {
            "user_id": str(user_id),
            "username": username or ""
        }
    }

    headers = {
        "Idempotence-Key": idempotence_key,
        "Content-Type": "application/json"
    }

    auth = aiohttp.BasicAuth(login=shop_id, password=secret_key)
    timeout = aiohttp.ClientTimeout(total=20, connect=10)

    try:
        async with aiohttp.ClientSession(auth=auth, timeout=timeout) as session:
            async with session.post(API_URL, json=payload, headers=headers) as resp:
                status = resp.status
                data = await resp.json()

                logger.info("⬅️ [ЮKassa REST] HTTP %d, тело: %s", status, data)

                if status in (200, 201):
                    return data.get("confirmation", {}).get("confirmation_url"), data.get("id")
                else:
                    logger.error("❌ Ошибка создания платежа ЮKassa: %s", data)
                    return None, None

    except Exception as e:
        logger.exception("❌ Исключение при запросе к ЮKassa: %s", e)
        return None, None


async def check_yookassa_payment(payment_id: str) -> bool:
    shop_id = str(config.YOOKASSA_SHOP_ID).strip()
    secret_key = str(config.YOOKASSA_SECRET_KEY).strip()
    auth = aiohttp.BasicAuth(login=shop_id, password=secret_key)
    timeout = aiohttp.ClientTimeout(total=20, connect=10)

    try:
        async with aiohttp.ClientSession(auth=auth, timeout=timeout) as session:
            async with session.get(f"{API_URL}/{payment_id}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("status") == "succeeded"
                return False
    except Exception as e:
        logger.exception("Ошибка проверки платежа %s: %s", payment_id, e)
        return False