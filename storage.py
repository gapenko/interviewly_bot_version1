"""
storage.py — работа с JSON-файлами пользователей и платежей.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

from config import PAYMENTS_LOG_FILE, USERS_DATA_FILE

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
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _default_user_record(
    username: str | None, paid: bool = False, track: str | None = None
) -> dict:
    return {
        "username": username,
        "paid": paid,
        "track": track,
        "current_question_index": 0,
        "answers": [],
        "finished": False,
    }


async def get_user(telegram_id: int, username: str | None = None) -> dict:
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)

        if key not in users:
            users[key] = _default_user_record(username)
            _write_json(USERS_DATA_FILE, users)

        return users[key]


async def save_user(telegram_id: int, user_data: dict) -> None:
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        users[str(telegram_id)] = user_data
        _write_json(USERS_DATA_FILE, users)


async def reset_user(telegram_id: int) -> None:
    """Сбрасывает прогресс, но сохраняет факт оплаты и username."""
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        existing = users.get(key, {})
        users[key] = _default_user_record(
            username=existing.get("username"),
            paid=existing.get("paid", False),
            track=None,
        )
        _write_json(USERS_DATA_FILE, users)


async def mark_paid(telegram_id: int) -> None:
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        if key not in users:
            users[key] = _default_user_record(None)
        users[key]["paid"] = True
        _write_json(USERS_DATA_FILE, users)


async def revoke_paid(telegram_id: int) -> None:
    """Сбрасывает статус оплаты пользователю для тестирования платежного шлюза."""
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        if key in users:
            users[key]["paid"] = False
            _write_json(USERS_DATA_FILE, users)


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