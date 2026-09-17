"""
storage.py — работа с JSON-файлами пользователей, платежей, отзывов и UTM-кампаний.
Все файлы строго хранятся в директории data/.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from config import PAYMENTS_LOG_FILE, USERS_DATA_FILE

# Строгая привязка путей к директории data/
REVIEWS_DATA_FILE = "data/reviews.json"
CAMPAIGNS_DATA_FILE = "data/campaigns.json"

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


def _default_user_record(
    username: str | None,
    paid: bool = False,
    track: str | None = None,
    referrer_id: int | None = None,
    campaign: str | None = None,
) -> dict:
    return {
        "username": username,
        "paid": paid,
        "track": track,
        "current_question_index": 0,
        "answers": [],
        "finished": False,
        "bonus_balance": 0,
        "referrals_count": 0,
        "invited_by": referrer_id,
        "campaign": campaign,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_user(telegram_id: int, username: str | None = None) -> dict:
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        if key not in users:
            users[key] = _default_user_record(username)
            _write_json(USERS_DATA_FILE, users)
        return users[key]


async def register_user_with_ref(
    telegram_id: int,
    username: str | None = None,
    referrer_id: int | None = None,
    campaign_tag: str | None = None,
) -> tuple[dict, bool]:
    """Регистрирует нового пользователя. Если есть реферер — начисляет +150 бонусов."""
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        is_new = key not in users

        if is_new:
            users[key] = _default_user_record(
                username=username,
                referrer_id=referrer_id,
                campaign=campaign_tag,
            )
            # Начисление бонусов рефереру
            if referrer_id and str(referrer_id) in users and str(referrer_id) != key:
                ref_user = users[str(referrer_id)]
                ref_user["bonus_balance"] = ref_user.get("bonus_balance", 0) + 150
                ref_user["referrals_count"] = ref_user.get("referrals_count", 0) + 1
                if ref_user["bonus_balance"] >= 600:
                    ref_user["paid"] = True

            # Учет захода по рекламной ссылке
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
    """Сбрасывает прогресс ответов, сохраняя оплату, бонусы и username."""
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        existing = users.get(key, {})
        users[key] = {
            "username": existing.get("username"),
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
        }
        _write_json(USERS_DATA_FILE, users)


async def mark_paid(telegram_id: int) -> None:
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        if key not in users:
            users[key] = _default_user_record(None)
        users[key]["paid"] = True

        campaign_tag = users[key].get("campaign")
        if campaign_tag:
            campaigns = _read_json(CAMPAIGNS_DATA_FILE, {})
            if campaign_tag in campaigns:
                campaigns[campaign_tag]["payments"] = campaigns[campaign_tag].get("payments", 0) + 1
                _write_json(CAMPAIGNS_DATA_FILE, campaigns)

        _write_json(USERS_DATA_FILE, users)


async def revoke_paid(telegram_id: int) -> None:
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        if key in users:
            users[key]["paid"] = False
            _write_json(USERS_DATA_FILE, users)


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


# --- СИСТЕМА ОТЗЫВОВ ---

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


async def get_approved_reviews() -> list[dict]:
    async with _file_lock:
        reviews = _read_json(REVIEWS_DATA_FILE, [])
        return [r for r in reviews if r.get("approved")]


async def get_pending_reviews() -> list[dict]:
    """Возвращает список отзывов, ожидающих модерации."""
    async with _file_lock:
        reviews = _read_json(REVIEWS_DATA_FILE, [])
        return [r for r in reviews if not r.get("approved", False)]


# --- РЕКЛАМНЫЕ ССЫЛКИ ДЛЯ КАНАЛОВ (UTM) ---

async def create_campaign(tag: str, description: str = "") -> None:
    async with _file_lock:
        campaigns = _read_json(CAMPAIGNS_DATA_FILE, {})
        if tag not in campaigns:
            campaigns[tag] = {
                "tag": tag,
                "description": description,
                "created_at": datetime.now(timezone.utc).strftime("%d.%m.%Y"),
                "joins": 0,
                "payments": 0,
            }
            _write_json(CAMPAIGNS_DATA_FILE, campaigns)


async def get_all_campaigns() -> dict:
    async with _file_lock:
        return _read_json(CAMPAIGNS_DATA_FILE, {})