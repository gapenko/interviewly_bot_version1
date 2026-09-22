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
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "admin123")

# --- LLM Service (SpeShu.AI / Anthropic) ---
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-3-5-sonnet-20241022")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.speshu.ai/v1")

# --- Настройки собеседования ---
FREE_QUESTIONS_COUNT = int(os.getenv("FREE_QUESTIONS_COUNT", "4"))

# --- Настройки платежей ---
# ACCESS_PRICE_STARS — актуальное имя переменной для цены в Telegram Stars.
# STARS_PRICE читается как алиас для обратной совместимости со старым .env.
ACCESS_PRICE_STARS = int(os.getenv("ACCESS_PRICE_STARS", os.getenv("STARS_PRICE", "50")))
STARS_PRICE = ACCESS_PRICE_STARS  # алиас, чтобы не ломать код, который ссылается на старое имя

# Стоимость в рублях (СБП / ЮKassa)
SBP_PRICE_RUB = int(os.getenv("SBP_PRICE_RUB", "100"))

# --- Реферальная программа ---
# Вынесено из кода в конфиг — раньше числа 150 и 600 были "зашиты" в нескольких местах storage.py и handlers/start.py.
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
