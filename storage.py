"""
storage.py — атомарное управление данными JSON:
- Пользователи, платежи, отзывы, промокоды, UTM-кампании.
- Постоянный список администраторов с сохранением на диск.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from config import PAYMENTS_LOG_FILE, USERS_DATA_FILE

ADMINS_DATA_FILE = "data/admins.json"
REVIEWS_DATA_FILE = "data/reviews.json"
CAMPAIGNS_DATA_FILE = "data/campaigns.json"
PROMOCODES_DATA_FILE = "data/promocodes.json"

_file_lock = asyncio.Lock()


def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


# =========================================================
# ПОСТОЯННОЕ ХРАНИЛИЩЕ АДМИНИСТРАТОРОВ
# =========================================================

async def get_saved_admins() -> list[int]:
    """Возвращает список ID всех подтверждённых администраторов из JSON-файла."""
    async with _file_lock:
        admins = _read_json(ADMINS_DATA_FILE, [])
        return [int(x) for x in admins if str(x).isdigit()]


async def add_admin_permanently(telegram_id: int) -> bool:
    """Навсегда закрепляет права администратора за указанным ID."""
    async with _file_lock:
        admins = _read_json(ADMINS_DATA_FILE, [])
        tid = int(telegram_id)
        if tid not in admins:
            admins.append(tid)
            _write_json(ADMINS_DATA_FILE, admins)
            return True
        return False


async def remove_admin_permanently(telegram_id: int) -> bool:
    """Удаляет права администратора."""
    async with _file_lock:
        admins = _read_json(ADMINS_DATA_FILE, [])
        tid = int(telegram_id)
        if tid in admins:
            admins.remove(tid)
            _write_json(ADMINS_DATA_FILE, admins)
            return True
        return False


# =========================================================
# ПОЛЬЗОВАТЕЛИ И РЕФЕРАЛЫ
# =========================================================

def _default_user_record(
    username: str | None,
    full_name: str | None = None,
    paid: bool = False,
    track: str | None = None,
    referrer_id: int | None = None,
    campaign: str | None = None,
) -> dict:
    return {
        "username": username or "",
        "full_name": full_name or "",
        "paid": paid,
        "track": track,
        "current_question_index": 0,
        "answers": [],
        "finished": False,
        "bonus_balance": 0,
        "referrals_count": 0,
        "invited_by": referrer_id,
        "campaign": campaign,
        "registered_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M"),
        "is_blocked": False,
    }


async def get_user(telegram_id: int, username: str | None = None) -> dict:
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        if key not in users:
            users[key] = _default_user_record(username)
            _write_json(USERS_DATA_FILE, users)
        elif username and users[key].get("username") != username:
            users[key]["username"] = username
            _write_json(USERS_DATA_FILE, users)
        return users[key]


async def find_user_by_query(query: str) -> Optional[tuple[int, dict]]:
    """Ищет пользователя по ID или @username."""
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        cleaned_query = query.strip().lstrip("@").lower()

        # Поиск по точному Telegram ID
        if cleaned_query.isdigit() and cleaned_query in users:
            return int(cleaned_query), users[cleaned_query]

        # Поиск по username
        for uid_str, data in users.items():
            u_name = str(data.get("username", "")).lower()
            if u_name == cleaned_query:
                return int(uid_str), data
    return None


async def register_user_with_ref(
    telegram_id: int,
    username: str | None = None,
    full_name: str | None = None,
    referrer_id: int | None = None,
    campaign_tag: str | None = None,
) -> tuple[dict, bool]:
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        is_new = key not in users

        if is_new:
            users[key] = _default_user_record(
                username=username,
                full_name=full_name,
                referrer_id=referrer_id,
                campaign=campaign_tag,
            )
            # Начисление 150 бонусов рефереру
            if referrer_id and str(referrer_id) in users and str(referrer_id) != key:
                ref_user = users[str(referrer_id)]
                ref_user["bonus_balance"] = ref_user.get("bonus_balance", 0) + 150
                ref_user["referrals_count"] = ref_user.get("referrals_count", 0) + 1
                if ref_user["bonus_balance"] >= 600:
                    ref_user["paid"] = True

            if campaign_tag:
                campaigns = _read_json(CAMPAIGNS_DATA_FILE, {})
                if campaign_tag in campaigns:
                    campaigns[campaign_tag]["joins"] = campaigns[campaign_tag].get("joins", 0) + 1
                    _write_json(CAMPAIGNS_DATA_FILE, campaigns)

            _write_json(USERS_DATA_FILE, users)

        return users[key], is_new


async def save_user(telegram_id: int, user_data: dict) -> None:
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        users[str(telegram_id)] = user_data
        _write_json(USERS_DATA_FILE, users)


async def reset_user(telegram_id: int) -> None:
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        existing = users.get(key, {})
        users[key] = {
            "username": existing.get("username", ""),
            "full_name": existing.get("full_name", ""),
            "paid": existing.get("paid", False),
            "track": None,
            "current_question_index": 0,
            "answers": [],
            "finished": False,
            "bonus_balance": existing.get("bonus_balance", 0),
            "referrals_count": existing.get("referrals_count", 0),
            "invited_by": existing.get("invited_by"),
            "campaign": existing.get("campaign"),
            "registered_at": existing.get("registered_at"),
            "is_blocked": existing.get("is_blocked", False),
        }
        _write_json(USERS_DATA_FILE, users)


async def set_user_paid_status(telegram_id: int, status: bool) -> bool:
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        if key in users:
            users[key]["paid"] = status
            if status:
                campaign_tag = users[key].get("campaign")
                if campaign_tag:
                    campaigns = _read_json(CAMPAIGNS_DATA_FILE, {})
                    if campaign_tag in campaigns:
                        campaigns[campaign_tag]["payments"] = campaigns[campaign_tag].get("payments", 0) + 1
                        _write_json(CAMPAIGNS_DATA_FILE, campaigns)
            _write_json(USERS_DATA_FILE, users)
            return True
        return False


async def mark_paid(telegram_id: int) -> None:
    await set_user_paid_status(telegram_id, True)


async def revoke_paid(telegram_id: int) -> None:
    await set_user_paid_status(telegram_id, False)


async def adjust_user_bonuses(telegram_id: int, amount: int) -> int:
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        if key in users:
            users[key]["bonus_balance"] = max(0, users[key].get("bonus_balance", 0) + amount)
            if users[key]["bonus_balance"] >= 600:
                users[key]["paid"] = True
            _write_json(USERS_DATA_FILE, users)
            return users[key]["bonus_balance"]
        return 0


async def log_payment(
    telegram_id: int,
    username: str | None,
    amount_stars: int,
    telegram_payment_charge_id: str,
    provider_payment_charge_id: str | None = None,
    currency: str = "XTR",
    invoice_payload: str | None = None,
) -> None:
    async with _file_lock:
        payments = _read_json(PAYMENTS_LOG_FILE, [])
        record = {
            "telegram_id": telegram_id,
            "username": username,
            "amount_stars": amount_stars,
            "currency": currency,
            "telegram_payment_charge_id": telegram_payment_charge_id,
            "provider_payment_charge_id": provider_payment_charge_id,
            "invoice_payload": invoice_payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        payments.append(record)
        _write_json(PAYMENTS_LOG_FILE, payments)


# =========================================================
# ОТЗЫВЫ
# =========================================================

async def add_review(user_id: int, username: str | None, full_name: str, rating: int, text: str) -> int:
    async with _file_lock:
        reviews = _read_json(REVIEWS_DATA_FILE, [])
        rev_id = len(reviews) + 1
        review = {
            "id": rev_id,
            "user_id": user_id,
            "username": username or "",
            "full_name": full_name or "Кандидат",
            "rating": rating,
            "text": text,
            "created_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M"),
            "approved": False,
        }
        reviews.append(review)
        _write_json(REVIEWS_DATA_FILE, reviews)
        return rev_id


async def set_review_status(review_id: int, approved: bool) -> bool:
    async with _file_lock:
        reviews = _read_json(REVIEWS_DATA_FILE, [])
        for r in reviews:
            if r.get("id") == review_id:
                r["approved"] = approved
                _write_json(REVIEWS_DATA_FILE, reviews)
                return True
        return False


async def delete_review(review_id: int) -> bool:
    async with _file_lock:
        reviews = _read_json(REVIEWS_DATA_FILE, [])
        initial_len = len(reviews)
        reviews = [r for r in reviews if r.get("id") != review_id]
        if len(reviews) < initial_len:
            _write_json(REVIEWS_DATA_FILE, reviews)
            return True
        return False


async def get_approved_reviews() -> list[dict]:
    async with _file_lock:
        reviews = _read_json(REVIEWS_DATA_FILE, [])
        return [r for r in reviews if r.get("approved")]


async def get_pending_reviews() -> list[dict]:
    async with _file_lock:
        reviews = _read_json(REVIEWS_DATA_FILE, [])
        return [r for r in reviews if not r.get("approved", False)]


async def get_all_reviews() -> list[dict]:
    async with _file_lock:
        return _read_json(REVIEWS_DATA_FILE, [])


# =========================================================
# UTM / КАНАЛЫ
# =========================================================

async def create_campaign(tag: str, description: str = "") -> None:
    async with _file_lock:
        campaigns = _read_json(CAMPAIGNS_DATA_FILE, {})
        campaigns[tag] = {
            "tag": tag,
            "description": description,
            "created_at": datetime.now(timezone.utc).strftime("%d.%m.%Y"),
            "joins": campaigns.get(tag, {}).get("joins", 0),
            "payments": campaigns.get(tag, {}).get("payments", 0),
        }
        _write_json(CAMPAIGNS_DATA_FILE, campaigns)


async def get_all_campaigns() -> dict:
    async with _file_lock:
        return _read_json(CAMPAIGNS_DATA_FILE, {})


async def delete_campaign(tag: str) -> bool:
    async with _file_lock:
        campaigns = _read_json(CAMPAIGNS_DATA_FILE, {})
        if tag in campaigns:
            del campaigns[tag]
            _write_json(CAMPAIGNS_DATA_FILE, campaigns)
            return True
        return False


# =========================================================
# ПРОМОКОДЫ
# =========================================================

async def create_promocode(code: str, discount_percent: int = 100, max_uses: int = 10) -> None:
    async with _file_lock:
        promos = _read_json(PROMOCODES_DATA_FILE, {})
        promos[code.upper()] = {
            "code": code.upper(),
            "discount_percent": discount_percent,
            "max_uses": max_uses,
            "used_count": 0,
            "created_at": datetime.now(timezone.utc).strftime("%d.%m.%Y"),
        }
        _write_json(PROMOCODES_DATA_FILE, promos)


async def get_all_promocodes() -> dict:
    async with _file_lock:
        return _read_json(PROMOCODES_DATA_FILE, {})


async def apply_promocode(code: str) -> Optional[dict]:
    async with _file_lock:
        promos = _read_json(PROMOCODES_DATA_FILE, {})
        code_key = code.strip().upper()
        if code_key in promos:
            p = promos[code_key]
            if p["used_count"] < p["max_uses"]:
                p["used_count"] += 1
                _write_json(PROMOCODES_DATA_FILE, promos)
                return p
    return None