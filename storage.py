"""
storage.py — атомарное управление данными в JSON-файлах.

Главные принципы:
- Любое изменение делается по схеме «прочитать → изменить → записать» под общим asyncio.Lock,
  а запись атомарная (tmp-файл + os.replace), поэтому файл не портится при падении процесса.
- Для точечных изменений используйте update_user / append_answer / grant_track_access и т.п.,
  а НЕ get_user → правка → save_user: между чтением и записью (например, пока идёт запрос к LLM)
  другой обработчик мог изменить запись, и полная перезапись её бы затёрла.
- Все пути — абсолютные (от папки проекта), а не относительные к текущей директории запуска.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from config import (
    ADMIN_IDS,
    DATA_DIR,
    PAYMENTS_LOG_FILE,
    REFERRAL_BONUS_PER_INVITE,
    REFERRAL_FULL_ACCESS_THRESHOLD,
    USERS_DATA_FILE,
)

ADMINS_DATA_FILE = str(DATA_DIR / "admins.json")
REVIEWS_DATA_FILE = str(DATA_DIR / "reviews.json")
CAMPAIGNS_DATA_FILE = str(DATA_DIR / "campaigns.json")
PROMOCODES_DATA_FILE = str(DATA_DIR / "promocodes.json")
SCREENS_DATA_FILE = str(DATA_DIR / "screens.json")
# ID всех когда-либо засчитанных платежей. НЕ очищается при сбросе статистики — иначе после сброса
# старую кнопку «Проверить оплату» можно было бы нажать повторно и получить доступ ещё раз.
PROCESSED_PAYMENTS_FILE = str(DATA_DIR / "processed_payments.json")
ARCHIVE_DIR = DATA_DIR / "archive"

_file_lock = asyncio.Lock()
_screen_lock = asyncio.Lock()

REGISTERED_AT_FORMAT = "%d.%m.%Y %H:%M"


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def _archive_file(path: str, prefix: str) -> Optional[str]:
    """Копирует файл в data/archive/<prefix>_<дата>.json перед очисткой. Возвращает путь архива."""
    if not os.path.exists(path):
        return None
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y%m%d_%H%M%S")
    target = ARCHIVE_DIR / f"{prefix}_{stamp}.json"
    with open(path, "r", encoding="utf-8") as src, open(target, "w", encoding="utf-8") as dst:
        dst.write(src.read())
    return str(target)


# =========================================================
# АДМИНИСТРАТОРЫ (с кэшем в памяти — фильтр IsAdmin вызывается очень часто)
# =========================================================

_admins_cache: Optional[list[int]] = None


async def get_saved_admins() -> list[int]:
    """ID администраторов, добавленных через бота (без учёта ADMIN_IDS из .env)."""
    global _admins_cache
    if _admins_cache is None:
        async with _file_lock:
            admins = _read_json(ADMINS_DATA_FILE, [])
            _admins_cache = [int(x) for x in admins if str(x).lstrip("-").isdigit()]
    return list(_admins_cache)


async def get_active_admin_ids() -> set[int]:
    return set(ADMIN_IDS).union(await get_saved_admins())


async def is_admin(telegram_id: int) -> bool:
    return int(telegram_id) in await get_active_admin_ids()


async def add_admin_permanently(telegram_id: int) -> bool:
    global _admins_cache
    async with _file_lock:
        admins = [int(x) for x in _read_json(ADMINS_DATA_FILE, [])]
        tid = int(telegram_id)
        if tid in admins:
            _admins_cache = admins
            return False
        admins.append(tid)
        _write_json(ADMINS_DATA_FILE, admins)
        _admins_cache = admins
        return True


async def remove_admin_permanently(telegram_id: int) -> bool:
    global _admins_cache
    async with _file_lock:
        admins = [int(x) for x in _read_json(ADMINS_DATA_FILE, [])]
        tid = int(telegram_id)
        if tid not in admins:
            _admins_cache = admins
            return False
        admins.remove(tid)
        _write_json(ADMINS_DATA_FILE, admins)
        _admins_cache = admins
        return True


# =========================================================
# ПОЛЬЗОВАТЕЛИ
# =========================================================

def _default_user_record(
    username: str | None,
    full_name: str | None = None,
    referrer_id: int | None = None,
    campaign: str | None = None,
) -> dict:
    return {
        "username": username or "",
        "full_name": full_name or "",
        "paid": False,            # полный доступ ко всем направлениям (админ / реферальная программа)
        "paid_tracks": [],        # направления, оплаченные по отдельности
        "track": None,
        "current_question_index": 0,
        "answers": [],
        "finished": False,
        "last_result": None,      # последний завершённый разбор: отчёт, резюме, ответы
        "bonus_balance": 0,
        "referrals_count": 0,
        "invited_by": referrer_id,
        "campaign": campaign,
        "pending_promo": None,    # промокод, применённый к текущей оплате
        "apply_bonuses": False,   # списывать ли бонусы в текущей оплате
        "registered_at": _now().strftime(REGISTERED_AT_FORMAT),
        "is_blocked": False,      # заблокирован администратором
        "bot_blocked": False,     # пользователь сам заблокировал бота (выясняется при рассылке)
    }


def parse_registered_at(user: dict) -> Optional[datetime]:
    raw = user.get("registered_at")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, REGISTERED_AT_FORMAT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


async def get_user(telegram_id: int, username: str | None = None) -> dict:
    """Возвращает копию записи пользователя (создаёт запись, если её нет)."""
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        changed = False
        if key not in users:
            users[key] = _default_user_record(username)
            changed = True
        elif username and users[key].get("username") != username:
            users[key]["username"] = username
            changed = True
        if changed:
            _write_json(USERS_DATA_FILE, users)
        return dict(users[key])


async def peek_user(telegram_id: int) -> Optional[dict]:
    """Запись пользователя без создания новой."""
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        record = users.get(str(telegram_id))
        return dict(record) if record else None


async def get_all_users() -> dict[str, dict]:
    async with _file_lock:
        return _read_json(USERS_DATA_FILE, {})


async def find_user_by_query(query: str) -> Optional[tuple[int, dict]]:
    """Ищет пользователя по ID или @username."""
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        cleaned_query = query.strip().lstrip("@").lower()
        if not cleaned_query:
            return None
        if cleaned_query.isdigit() and cleaned_query in users:
            return int(cleaned_query), users[cleaned_query]
        for uid_str, data in users.items():
            if str(data.get("username", "")).lower() == cleaned_query:
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
            if referrer_id and str(referrer_id) in users and str(referrer_id) != key:
                ref_user = users[str(referrer_id)]
                ref_user["bonus_balance"] = ref_user.get("bonus_balance", 0) + REFERRAL_BONUS_PER_INVITE
                ref_user["referrals_count"] = ref_user.get("referrals_count", 0) + 1
                if ref_user["bonus_balance"] >= REFERRAL_FULL_ACCESS_THRESHOLD:
                    ref_user["paid"] = True

            if campaign_tag:
                campaigns = _read_json(CAMPAIGNS_DATA_FILE, {})
                if campaign_tag in campaigns:
                    campaigns[campaign_tag]["joins"] = campaigns[campaign_tag].get("joins", 0) + 1
                    _write_json(CAMPAIGNS_DATA_FILE, campaigns)
        else:
            # Обновляем имя/username — они могли смениться в Telegram
            if username:
                users[key]["username"] = username
            if full_name:
                users[key]["full_name"] = full_name

        _write_json(USERS_DATA_FILE, users)
        return dict(users[key]), is_new


async def mutate_user(telegram_id: int, fn: Callable[[dict], Any]) -> dict:
    """Атомарно изменяет запись пользователя функцией fn(record). Создаёт запись при необходимости."""
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        if key not in users:
            users[key] = _default_user_record(None)
        fn(users[key])
        _write_json(USERS_DATA_FILE, users)
        return dict(users[key])


async def update_user(telegram_id: int, **fields) -> dict:
    """Атомарно обновляет отдельные поля пользователя."""
    return await mutate_user(telegram_id, lambda u: u.update(fields))


async def save_user(telegram_id: int, user_data: dict) -> None:
    """Полная перезапись записи. Используйте только когда запись только что прочитана."""
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        users[str(telegram_id)] = user_data
        _write_json(USERS_DATA_FILE, users)


async def reset_user(telegram_id: int) -> dict:
    """Сбрасывает прогресс текущего собеседования. Оплаты, бонусы и последний результат сохраняются."""
    def _reset(u: dict) -> None:
        u.update(track=None, current_question_index=0, answers=[], finished=False)
    return await mutate_user(telegram_id, _reset)


async def start_track(telegram_id: int, track: str) -> dict:
    """Начинает новое собеседование по направлению с первого вопроса."""
    def _start(u: dict) -> None:
        u.update(track=track, current_question_index=0, answers=[], finished=False)
    return await mutate_user(telegram_id, _start)


async def append_answer(telegram_id: int, expected_index: int, record: dict) -> bool:
    """
    Добавляет ответ и сдвигает индекс вопроса — только если индекс не изменился с момента,
    когда ответ начали обрабатывать. Защищает от «двойных» ответов при быстрых сообщениях
    (раньше два сообщения подряд могли записать ответ на один вопрос дважды или потерять один из них).
    """
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        u = users.get(str(telegram_id))
        if not u or u.get("finished") or u.get("current_question_index", 0) != expected_index:
            return False
        u.setdefault("answers", []).append(record)
        u["current_question_index"] = expected_index + 1
        _write_json(USERS_DATA_FILE, users)
        return True


async def save_result(telegram_id: int, result: dict) -> None:
    """Сохраняет последний завершённый результат (отчёт, черновик резюме, ответы)."""
    await update_user(telegram_id, last_result=result)


# --- Доступ ---

def user_has_track_access(user_data: dict, track: str | None) -> bool:
    """
    "paid" = True — полный доступ ко ВСЕМ направлениям (админ / реферальная программа).
    "paid_tracks" — направления, оплаченные по отдельности. За каждое новое направление
    нужно платить заново, если нет полного доступа.
    """
    if user_data.get("paid"):
        return True
    return bool(track) and track in user_data.get("paid_tracks", [])


def user_has_any_paid_access(user_data: dict) -> bool:
    return bool(user_data.get("paid") or user_data.get("paid_tracks"))


async def grant_track_access(telegram_id: int, track: str, count_payment: bool = True) -> None:
    """Открывает доступ к направлению. count_payment — засчитать оплату в UTM-статистику."""
    async with _file_lock:
        users = _read_json(USERS_DATA_FILE, {})
        key = str(telegram_id)
        if key not in users:
            return
        paid_tracks = users[key].get("paid_tracks", [])
        if track not in paid_tracks:
            paid_tracks.append(track)
        users[key]["paid_tracks"] = paid_tracks

        campaign_tag = users[key].get("campaign")
        if count_payment and campaign_tag:
            campaigns = _read_json(CAMPAIGNS_DATA_FILE, {})
            if campaign_tag in campaigns:
                campaigns[campaign_tag]["payments"] = campaigns[campaign_tag].get("payments", 0) + 1
                _write_json(CAMPAIGNS_DATA_FILE, campaigns)
        _write_json(USERS_DATA_FILE, users)


async def revoke_track_access(telegram_id: int, track: str) -> None:
    def _revoke(u: dict) -> None:
        u["paid_tracks"] = [t for t in u.get("paid_tracks", []) if t != track]
    await mutate_user(telegram_id, _revoke)


async def set_user_paid_status(telegram_id: int, status: bool) -> bool:
    """Выдаёт/забирает ПОЛНЫЙ доступ ко всем направлениям (ручное решение администратора)."""
    user = await peek_user(telegram_id)
    if not user:
        return False
    await update_user(telegram_id, paid=status)
    return True


async def mark_paid(telegram_id: int) -> None:
    await set_user_paid_status(telegram_id, True)


async def revoke_paid(telegram_id: int) -> None:
    await set_user_paid_status(telegram_id, False)


async def adjust_user_bonuses(telegram_id: int, amount: int) -> int:
    result = {}

    def _adjust(u: dict) -> None:
        u["bonus_balance"] = max(0, u.get("bonus_balance", 0) + amount)
        if amount > 0 and u["bonus_balance"] >= REFERRAL_FULL_ACCESS_THRESHOLD:
            u["paid"] = True
        result["balance"] = u["bonus_balance"]

    user = await peek_user(telegram_id)
    if not user:
        return 0
    await mutate_user(telegram_id, _adjust)
    return result["balance"]


# --- Блокировки (кэш в памяти: проверяется на каждое сообщение) ---

_blocked_cache: Optional[set[int]] = None


async def is_user_blocked(telegram_id: int) -> bool:
    global _blocked_cache
    if _blocked_cache is None:
        async with _file_lock:
            users = _read_json(USERS_DATA_FILE, {})
            _blocked_cache = {int(k) for k, v in users.items() if isinstance(v, dict) and v.get("is_blocked")}
    return int(telegram_id) in _blocked_cache


async def set_user_blocked(telegram_id: int, blocked: bool) -> None:
    global _blocked_cache
    await update_user(telegram_id, is_blocked=blocked)
    if _blocked_cache is None:
        await is_user_blocked(telegram_id)
    if blocked:
        _blocked_cache.add(int(telegram_id))
    else:
        _blocked_cache.discard(int(telegram_id))


async def mark_bot_blocked(telegram_id: int, value: bool = True) -> None:
    user = await peek_user(telegram_id)
    if user is not None and user.get("bot_blocked") != value:
        await update_user(telegram_id, bot_blocked=value)


# =========================================================
# ПЛАТЕЖИ
# =========================================================

def payment_key(p: dict) -> str:
    return str(p.get("id") or p.get("telegram_payment_charge_id") or "")


def payment_amount(p: dict) -> int:
    """Совместимость со старым форматом: раньше сумма лежала в поле amount_stars даже для рублей."""
    return int(p.get("amount", p.get("amount_stars", 0)) or 0)


def _load_processed_ids(payments: list[dict]) -> set[str]:
    if os.path.exists(PROCESSED_PAYMENTS_FILE):
        return set(_read_json(PROCESSED_PAYMENTS_FILE, []))
    # Первый запуск после обновления: берём ID из текущего журнала
    return {payment_key(p) for p in payments if payment_key(p)}


async def register_payment_once(record: dict) -> bool:
    """
    Записывает платёж, если платёж с таким id ещё не засчитывался. Возвращает False для повторов.
    Раньше повторное нажатие «Проверить оплату» засчитывало платёж снова (повторно списывало
    бонусы и открывало доступ к текущему направлению по старому платежу).
    """
    record = dict(record)
    record.setdefault("timestamp", _now().isoformat())
    key = str(record["id"])
    async with _file_lock:
        payments = _read_json(PAYMENTS_LOG_FILE, [])
        processed = _load_processed_ids(payments)
        if key in processed or any(payment_key(p) == key for p in payments):
            return False
        payments.append(record)
        processed.add(key)
        _write_json(PAYMENTS_LOG_FILE, payments)
        _write_json(PROCESSED_PAYMENTS_FILE, sorted(processed))
        return True


async def log_payment(
    telegram_id: int,
    username: str | None,
    amount_stars: int,
    telegram_payment_charge_id: str,
    provider_payment_charge_id: str | None = None,
    currency: str = "XTR",
    invoice_payload: str | None = None,
) -> None:
    """Старый интерфейс — оставлен для совместимости."""
    await register_payment_once({
        "id": telegram_payment_charge_id,
        "telegram_id": telegram_id,
        "username": username,
        "amount": amount_stars,
        "currency": currency,
        "provider_payment_charge_id": provider_payment_charge_id,
        "invoice_payload": invoice_payload,
    })


async def get_payments() -> list[dict]:
    async with _file_lock:
        return _read_json(PAYMENTS_LOG_FILE, [])


async def reset_payments_log() -> tuple[int, Optional[str]]:
    """Архивирует журнал платежей в data/archive/ и очищает его. Доступ пользователей не меняется."""
    async with _file_lock:
        payments = _read_json(PAYMENTS_LOG_FILE, [])
        # Сохраняем ID платежей до очистки журнала — защита от повторного зачёта
        processed = _load_processed_ids(payments) | {payment_key(p) for p in payments if payment_key(p)}
        _write_json(PROCESSED_PAYMENTS_FILE, sorted(processed))
        archive = _archive_file(PAYMENTS_LOG_FILE, "payments_log") if payments else None
        _write_json(PAYMENTS_LOG_FILE, [])
        return len(payments), archive


# =========================================================
# ОТЗЫВЫ
# =========================================================

REVIEW_STATUSES = ("pending", "approved", "rejected")


def review_status(r: dict) -> str:
    """Совместимость со старым форматом, где было только поле approved: True/False."""
    status = r.get("status")
    if status in REVIEW_STATUSES:
        return status
    return "approved" if r.get("approved") else "pending"


async def add_review(user_id: int, username: str | None, full_name: str, rating: int, text: str) -> int:
    async with _file_lock:
        reviews = _read_json(REVIEWS_DATA_FILE, [])
        # Раньше id = len+1: после удаления отзыва новый получал id уже существующего
        rev_id = max((int(r.get("id", 0)) for r in reviews), default=0) + 1
        reviews.append({
            "id": rev_id,
            "user_id": user_id,
            "username": username or "",
            "full_name": full_name or "Кандидат",
            "rating": rating,
            "text": text,
            "created_at": _now().strftime(REGISTERED_AT_FORMAT),
            "status": "pending",
            "approved": False,
        })
        _write_json(REVIEWS_DATA_FILE, reviews)
        return rev_id


async def get_review(review_id: int) -> Optional[dict]:
    async with _file_lock:
        for r in _read_json(REVIEWS_DATA_FILE, []):
            if r.get("id") == review_id:
                return r
    return None


async def set_review_status(review_id: int, status) -> bool:
    """status: "approved" / "rejected" / "pending" (или bool для совместимости)."""
    if isinstance(status, bool):
        status = "approved" if status else "rejected"
    async with _file_lock:
        reviews = _read_json(REVIEWS_DATA_FILE, [])
        for r in reviews:
            if r.get("id") == review_id:
                r["status"] = status
                r["approved"] = status == "approved"
                _write_json(REVIEWS_DATA_FILE, reviews)
                return True
        return False


async def delete_review(review_id: int) -> bool:
    async with _file_lock:
        reviews = _read_json(REVIEWS_DATA_FILE, [])
        new_reviews = [r for r in reviews if r.get("id") != review_id]
        if len(new_reviews) < len(reviews):
            _write_json(REVIEWS_DATA_FILE, new_reviews)
            return True
        return False


async def get_reviews(status: Optional[str] = None) -> list[dict]:
    """Отзывы, новые первыми. status=None — все."""
    async with _file_lock:
        reviews = _read_json(REVIEWS_DATA_FILE, [])
    if status:
        reviews = [r for r in reviews if review_status(r) == status]
    return sorted(reviews, key=lambda r: int(r.get("id", 0)), reverse=True)


async def get_approved_reviews() -> list[dict]:
    return await get_reviews("approved")


async def get_pending_reviews() -> list[dict]:
    return await get_reviews("pending")


async def get_all_reviews() -> list[dict]:
    return await get_reviews()


async def user_has_pending_review(user_id: int) -> bool:
    return any(r.get("user_id") == user_id for r in await get_reviews("pending"))


# =========================================================
# UTM / КАНАЛЫ
# =========================================================

async def create_campaign(tag: str, description: str = "") -> None:
    async with _file_lock:
        campaigns = _read_json(CAMPAIGNS_DATA_FILE, {})
        campaigns[tag] = {
            "tag": tag,
            "description": description,
            "created_at": _now().strftime("%d.%m.%Y"),
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


async def reset_campaign_counters() -> tuple[int, Optional[str]]:
    """Обнуляет счётчики переходов и оплат у всех UTM-кампаний (сами ссылки остаются)."""
    async with _file_lock:
        campaigns = _read_json(CAMPAIGNS_DATA_FILE, {})
        archive = _archive_file(CAMPAIGNS_DATA_FILE, "campaigns") if campaigns else None
        for c in campaigns.values():
            c["joins"] = 0
            c["payments"] = 0
        _write_json(CAMPAIGNS_DATA_FILE, campaigns)
        return len(campaigns), archive


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
            "used_by": [],
            "created_at": _now().strftime("%d.%m.%Y"),
        }
        _write_json(PROMOCODES_DATA_FILE, promos)


async def get_all_promocodes() -> dict:
    async with _file_lock:
        return _read_json(PROMOCODES_DATA_FILE, {})


async def get_promocode(code: str | None) -> Optional[dict]:
    if not code:
        return None
    return (await get_all_promocodes()).get(code.strip().upper())


async def check_promocode(code: str, user_id: int) -> tuple[Optional[dict], Optional[str]]:
    """
    Проверяет промокод БЕЗ списания активации. Возвращает (промокод, None) или (None, причина).
    Раньше активация списывалась уже при вводе кода — даже если пользователь так и не оплатил.
    """
    promo = await get_promocode(code)
    if not promo:
        return None, "Промокод не найден."
    if promo.get("used_count", 0) >= promo.get("max_uses", 0):
        return None, "Лимит активаций этого промокода исчерпан."
    if user_id in promo.get("used_by", []):
        return None, "Вы уже использовали этот промокод."
    return promo, None


async def consume_promocode(code: str | None, user_id: int) -> None:
    """Списывает активацию промокода — вызывается только после успешной оплаты/активации."""
    if not code:
        return
    async with _file_lock:
        promos = _read_json(PROMOCODES_DATA_FILE, {})
        p = promos.get(code.strip().upper())
        if not p:
            return
        used_by = p.setdefault("used_by", [])
        if user_id not in used_by:
            used_by.append(user_id)
            p["used_count"] = p.get("used_count", 0) + 1
            _write_json(PROMOCODES_DATA_FILE, promos)


async def delete_promocode(code: str) -> bool:
    async with _file_lock:
        promos = _read_json(PROMOCODES_DATA_FILE, {})
        if code.upper() in promos:
            del promos[code.upper()]
            _write_json(PROMOCODES_DATA_FILE, promos)
            return True
        return False


async def apply_promocode(code: str) -> Optional[dict]:
    """Старый интерфейс (без привязки к пользователю) — оставлен для совместимости."""
    promo = await get_promocode(code)
    if promo and promo.get("used_count", 0) < promo.get("max_uses", 0):
        return promo
    return None


# =========================================================
# «ЭКРАН» — ID последнего сообщения бота в чате (для чистого интерфейса)
# Хранится отдельно от пользователей: обновляется очень часто и не должен конфликтовать
# с изменением записей пользователей.
# =========================================================

_screens_cache: Optional[dict[str, int]] = None


async def _load_screens() -> dict[str, int]:
    global _screens_cache
    if _screens_cache is None:
        _screens_cache = {k: int(v) for k, v in _read_json(SCREENS_DATA_FILE, {}).items()}
    return _screens_cache


async def get_screen_id(chat_id: int) -> Optional[int]:
    async with _screen_lock:
        return (await _load_screens()).get(str(chat_id))


async def set_screen_id(chat_id: int, message_id: Optional[int]) -> None:
    async with _screen_lock:
        screens = await _load_screens()
        key = str(chat_id)
        if message_id is None:
            if key not in screens:
                return
            screens.pop(key, None)
        else:
            if screens.get(key) == message_id:
                return
            screens[key] = int(message_id)
        _write_json(SCREENS_DATA_FILE, screens)
