"""
ui_utils.py — визуальные утилиты: шкала прогресса и карточки вопросов.
"""
import math
from questions import TOTAL_QUESTIONS


def generate_progress_bar(current: int, total: int = TOTAL_QUESTIONS) -> str:
    """Генерирует моноширинный прогресс-бар из 10 сегментов с процентами."""
    total = max(1, total)
    current = min(total, max(0, current))
    
    filled_blocks = math.floor((current / total) * 10)
    empty_blocks = 10 - filled_blocks
    percent = int((current / total) * 100)
    
    bar = "■" * filled_blocks + "□" * empty_blocks
    return f"<code>Прогресс: [{bar}] {percent}% • Вопрос {current}/{total}</code>"


def format_question_card(question_data: dict, current_index: int) -> str:
    """Форматирует вопрос: открытый текст с подсказкой о развернутом ответе."""
    category = str(question_data.get("category", "")).upper()
    text = question_data.get("text", "")
    
    cat_emoji = "🏷"
    if "ОПЫТ" in category:
        cat_emoji = "💼"
    elif "ТЕХНИЧЕСК" in category:
        cat_emoji = "⚙️"
    elif "ПОВЕДЕНЧЕСК" in category:
        cat_emoji = "🤝"
        
    progress = generate_progress_bar(current_index + 1)
    
    return (
        f"{progress}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{cat_emoji} <b>СЕКЦИЯ: {category}</b>\n\n"
        f"<b>{text}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>Совет ментора: отвечайте развернуто, приводите примеры из практики, "
        f"описывайте логику выбора инструментов и возможные риски.</i>\n\n"
        f"✍️ <i>Отправьте ваш ответ текстом ниже:</i>"
    )