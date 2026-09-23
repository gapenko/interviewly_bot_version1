"""
config.py — глобальная конфигурация приложения.
Загружает переменные окружения из файла .env.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Загрузка переменных окружения из .env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# --- Telegram Bot ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# --- Администрирование ---
# Список ID администраторов Telegram через запятую (например: "12345678,87654321")
raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in raw_admin_ids.split(",") if x.strip().isdigit()
]
# Пароль для команды /auth. Если переменная не задана — вход по паролю отключён
# (раньше по умолчанию подставлялся пароль "admin123").
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "").strip()
# Защита от подбора пароля: после N неудачных попыток /auth блокируется на M минут.
AUTH_MAX_ATTEMPTS = int(os.getenv("AUTH_MAX_ATTEMPTS", "5"))
AUTH_LOCK_MINUTES = int(os.getenv("AUTH_LOCK_MINUTES", "30"))

# --- LLM Service (SpeShu.AI / Anthropic) ---
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-3-5-sonnet-20241022")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.speshu.ai/v1")

# --- Настройки собеседования ---
FREE_QUESTIONS_COUNT = int(os.getenv("FREE_QUESTIONS_COUNT", "4"))

# Показывать ли оценку X/10 сразу после каждого ответа. По умолчанию выключено:
# на реальном собеседовании баллы не озвучивают после каждого ответа. Оценки всё равно
# сохраняются и попадают в итоговый отчёт и .docx.
SHOW_ANSWER_SCORE = os.getenv("SHOW_ANSWER_SCORE", "0") == "1"

# --- Настройки платежей ---
# ACCESS_PRICE_STARS — актуальное имя переменной для цены в Telegram Stars.
# STARS_PRICE читается как алиас для обратной совместимости со старым .env.
ACCESS_PRICE_STARS = int(os.getenv("ACCESS_PRICE_STARS", os.getenv("STARS_PRICE", "50")))
STARS_PRICE = ACCESS_PRICE_STARS  # алиас, чтобы не ломать код, который ссылается на старое имя

# Стоимость в рублях (СБП / ЮKassa)
SBP_PRICE_RUB = int(os.getenv("SBP_PRICE_RUB", "100"))

# Сколько бонусов списывается за 1 звезду скидки при оплате в Telegram Stars
# (при оплате в рублях 1 бонус = 1 рубль скидки).
BONUSES_PER_STAR = max(1, int(os.getenv("BONUSES_PER_STAR", "4")))

# --- Реферальная программа ---
REFERRAL_BONUS_PER_INVITE = int(os.getenv("REFERRAL_BONUS_PER_INVITE", "150"))
REFERRAL_FULL_ACCESS_THRESHOLD = int(os.getenv("REFERRAL_FULL_ACCESS_THRESHOLD", "600"))

# ЮKassa API
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")

# --- Пути к файлам данных (JSON) ---
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

USERS_DATA_FILE = str(DATA_DIR / "users.json")
PAYMENTS_LOG_FILE = str(DATA_DIR / "payments_log.json")
