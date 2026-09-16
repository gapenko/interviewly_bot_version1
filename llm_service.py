"""
llm_service.py — обращение к LLM API (через SpeShu.AI / Anthropic) для анализа ответов
пользователя, формирования итогового отчёта и работы с резюме.
"""
import logging
import re
import anthropic
from anthropic import AsyncAnthropic

from config import LLM_API_KEY, LLM_MODEL, LLM_BASE_URL

logger = logging.getLogger(__name__)

# Инициализируем клиент с автоматическим retry при 500/503 ошибках и таймаутом
client = AsyncAnthropic(
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL,
    max_retries=3,
    timeout=35.0,
)

# Премиальный промпт для промежуточной рецензии ответа
ANSWER_ANALYSIS_SYSTEM_PROMPT = """\
Ты — Senior IT-ментор и технический интервьюер ведущей технологической компании.
Тебе передают вопрос собеседования и ответ кандидата на русском языке.

Твоя задача — дать краткую, конструктивную, премиальную обратную связь.

ВАЖНО ПО РАЗМЕТКЕ:
НЕ используй Markdown (никаких звёздочек ** или решёток ###).
Для выделения важного используй ТОЛЬКО HTML-теги: <b>жирный текст</b> и <i>курсив</i>.

Ответ формируй строго по следующим блокам:

🎯 <b>Ключевые акценты ответа:</b>
(1-2 предложения: что подмечено точно, какие сильные стороны и термины использованы)

💡 <b>Как усилить позицию на собеседовании:</b>
(1-2 конкретных совета: чего не хватило для ответа уровня Middle/Senior, какие риски или паттерны стоило упомянуть)

📊 <b>Скоринг:</b> [Оценка от 1 до 10]/10

Не задавай встречных вопросов. Пиши лаконично, емко и профессионально.
"""

# Промпт для итогового отчёта по всему собеседованию
FINAL_REPORT_SYSTEM_PROMPT = """\
Ты — технический директор и ведущий IT-интервьюер.
Тебе передана расшифровка ответов кандидата по 15 вопросам собеседования.

ВАЖНО ПО РАЗМЕТКЕ:
НЕ используй Markdown (никаких звёздочек ** или решёток ###).
Для выделения используй только HTML-теги: <b>жирный текст</b> и <i>курсив</i>.

Сформируй развернутое заключение строго по следующему формату:

🛠 <b>HARD SKILLS (ТЕХНИЧЕСКИЕ НАВЫКИ):</b>
• <b>Оценка:</b> X/10
• <b>Сильные стороны:</b> (какие технические концепции кандидат понимает глубоко)
• <b>Пробелы и точки роста:</b> (какие темы и технологии стоит подтянуть)

🤝 <b>SOFT SKILLS (ГИБКИЕ НАВЫКИ И КОММУНИКАЦИЯ):</b>
• <b>Оценка:</b> X/10
• <b>Структура и подача:</b> (умение ясно формулировать мысль, логика изложения)
• <b>Поведение в сложных ситуациях:</b> (реакция на сжатые сроки, критику и форс-мажоры)

🎯 <b>ИТОГОВЫЙ ГРЕЙД И РЕКОМЕНДАЦИИ:</b>
• <b>Ориентировочный уровень:</b> (Junior+ / Middle / Senior-ready)
• <b>Главный фокус для подготовки:</b> (3 конкретных шага перед выходом на реальный рынок)
"""

RESUME_DRAFT_SYSTEM_PROMPT = """\
Ты — IT-HR эксперт и составитель профессиональных резюме.
На основе ответов кандидата с технического собеседования составь сильный черновик резюме для размещения на HeadHunter / LinkedIn.

ВАЖНО ПО РАЗМЕТКЕ:
НЕ используй Markdown (никаких звёздочек ** или решёток ###).
Для заголовков и жирного шрифта используй только HTML: <b>текст</b>.

Формат вывода:
🎯 <b>ЖЕЛАЕМАЯ ПОЗИЦИЯ:</b> (Специальность на основе стека)
📌 <b>ОБО МНЕ:</b> (Сильный профессиональный summary в 3-4 предложения)
🛠 <b>КЛЮЧЕВЫЕ НАВЫКИ:</b> (Список технологий, подходов и инструментов)
💼 <b>ОПИСАНИЕ ОПЫТА И ДОСТИЖЕНИЙ:</b>
(Сформулируй 3-5 ключевых пунктов опыта на основе его ответов по методике STAR/XYZ: действие + технология + результат).
"""

RESUME_AUDIT_SYSTEM_PROMPT = """\
Ты — ведущий IT-рекрутер. Тебе прислали текст резюме кандидата, а также есть контекст его ответов на собеседовании.

ВАЖНО ПО РАЗМЕТКЕ:
НЕ используй Markdown (никаких звёздочек ** или решёток ###).
Используй HTML: <b>текст</b> для выделения.

Проведи конструктивный аудит резюме:
1. 🔍 <b>Первое впечатление:</b> (что рекрутер считывает в первые 5 секунд).
2. ⚠️ <b>Слабые места:</b> (где описаны размытые обязанности вместо измеримых результатов).
3. ✍️ <b>Примеры перефразирования:</b> (как переписать 2-3 строчки опыта в формат «Достиг X при помощи Y»).
4. 🚀 <b>Совет по улучшению отклика:</b>
"""


def _clean_html(text: str) -> str:
    """Превращает Markdown в чистый Telegram HTML."""
    if not text:
        return ""

    # Убираем решётки заголовков в начале строк
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)

    # Заменяем **жирный** на <b>жирный</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    # Заменяем оставшиеся одиночные *курсив* на <i>курсив</i>
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)

    return text.strip()


def _extract_text(response) -> str:
    """Извлекает текст из ответа Anthropic API и форматирует под HTML Telegram."""
    parts = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    raw_text = "\n".join(parts).strip()
    return _clean_html(raw_text) or "Ответ недоступен."


async def analyze_answer(question_text: str, answer_text: str) -> str:
    """Отправляет вопрос и ответ пользователя в LLM и возвращает рецензию."""
    user_prompt = f"Вопрос собеседования:\n{question_text}\n\nОтвет кандидата:\n{answer_text}"
    try:
        response = await client.messages.create(
            model=LLM_MODEL,
            max_tokens=600,
            system=ANSWER_ANALYSIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return _extract_text(response)
    except anthropic.APIStatusError as e:
        logger.error("Ошибка статуса API LLM (код %s): %s", e.status_code, e.message)
        return (
            "⚠️ <b>Сервис рецензий временно перегружен.</b>\n\n"
            "Ваш ответ зафиксирован. Двигаемся к следующему вопросу, "
            "а полный анализ навыков будет сформирован в итоговом отчёте."
        )
    except Exception as e:
        logger.exception("Непредвиденная ошибка при обращении к LLM: %s", e)
        return "⚠️ Не удалось получить разбор ответа из-за временной ошибки связи. Двигаемся дальше."


# Алиас для совместимости вызовов
process_answer = analyze_answer


async def generate_final_report(qa_pairs: list[dict], track_title: str) -> str:
    """Формирует итоговый отчёт по Hard & Soft Skills."""
    transcript_lines = [f"Направление: {track_title}"]
    for i, pair in enumerate(qa_pairs, start=1):
        transcript_lines.append(f"{i}. Вопрос: {pair['question']}\n   Ответ: {pair['answer']}")
    transcript = "\n\n".join(transcript_lines)

    try:
        response = await client.messages.create(
            model=LLM_MODEL,
            max_tokens=1200,
            system=FINAL_REPORT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": transcript}],
        )
        return _extract_text(response)
    except Exception as e:
        logger.exception("Ошибка при формировании отчета: %s", e)
        return "⚠️ Сервер нейросети временно недоступен. Попробуйте запросить отчёт позже командой /report."


async def generate_resume_draft(qa_pairs: list[dict], track_title: str) -> str:
    """Генерирует текстовый черновик резюме на основе ответов пользователя."""
    transcript_lines = [f"Специализация: {track_title}"]
    for i, pair in enumerate(qa_pairs, start=1):
        transcript_lines.append(f"В: {pair['question']}\nО: {pair['answer']}")
    transcript = "\n\n".join(transcript_lines)

    try:
        response = await client.messages.create(
            model=LLM_MODEL,
            max_tokens=1200,
            system=RESUME_DRAFT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": transcript}],
        )
        return _extract_text(response)
    except Exception as e:
        logger.exception("Ошибка генерации черновика резюме: %s", e)
        return "⚠️ Не удалось сгенерировать черновик резюме из-за нагрузки на сеть. Попробуйте чуть позже."


async def audit_user_resume(resume_text: str, qa_pairs: list[dict]) -> str:
    """Аудит присланного резюме кандидата."""
    context = ""
    if qa_pairs:
        context = "Контекст реальных ответов кандидата на собеседовании:\n" + "\n".join(
            [f"- {p['question'][:60]}... -> {p['answer'][:100]}..." for p in qa_pairs[:5]]
        )

    user_prompt = f"Текст резюме кандидата:\n\n{resume_text}\n\n{context}"

    try:
        response = await client.messages.create(
            model=LLM_MODEL,
            max_tokens=1200,
            system=RESUME_AUDIT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return _extract_text(response)
    except Exception as e:
        logger.exception("Ошибка аудита резюме: %s", e)
        return "⚠️ Не удалось провести аудит резюме. Повторите попытку через минуту."