"""
llm_service.py — обращение к LLM API (через SpeShu.AI / Anthropic) для анализа ответов
пользователя, формирования итогового отчёта и работы с резюме.

Почему раньше ментор звучал как шаблонный AI-ассистент и что изменено:
1. Жёсткий формат ответа («🎯 Ключевые акценты / 💡 Как усилить / 📊 Скоринг X/10») на каждый
   ответ — самый заметный признак бота. Теперь у ментора есть персона (имя, опыт, роль
   интервьюера по конкретному направлению), а промпт описывает, КАК пишет живой человек в
   мессенджере и какие именно обороты выдают AI.
2. Ментор не помнил разговор — каждый ответ оценивался в вакууме. Теперь в запрос передаётся
   контекст: имя кандидата, номер вопроса, последние ответы кандидата и последние реплики
   самого ментора (чтобы он не начинал каждое сообщение одинаково).
3. Оценка больше не пишется в текст реплики — модель возвращает её служебной строкой
   [[score: N]], которую код вырезает и сохраняет отдельно (для отчёта и .docx).
4. Подсказка к вопросу генерируется под конкретный вопрос, а не одним и тем же текстом.
5. Повышена «температура» для реплик, чтобы формулировки не повторялись.
"""
import html
import logging
import re
from dataclasses import dataclass

import anthropic
from anthropic import AsyncAnthropic

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, MENTOR_NAME

logger = logging.getLogger(__name__)

# Инициализируем клиент с автоматическим retry при 500/503 ошибках и таймаутом
client = AsyncAnthropic(
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL,
    max_retries=3,
    timeout=35.0,
)


# =========================================================
# ПРОМПТЫ
# =========================================================

MENTOR_REPLY_SYSTEM_PROMPT = """\
Ты — {mentor}, практикующий специалист с большим опытом в направлении «{track}». Последние несколько лет ты регулярно проводишь технические собеседования и нанимаешь людей в команду. Сейчас ты ведёшь тренировочное собеседование в переписке (Telegram) с кандидатом{candidate}.

Кандидат только что ответил на твой вопрос. Отреагируй так, как отреагировал бы живой опытный интервьюер-ментор в переписке: коротко, по делу и с конкретикой именно по этому ответу.

КАК ТЫ ПИШЕШЬ
- Как человек в мессенджере, а не как отчёт: обычно 2–6 предложений, иногда разбитые на 1–2 коротких абзаца. Длина зависит от ответа: на слабый или спорный ответ можно написать больше, на сильный хватит пары фраз.
- Обращаешься на «вы», но тон живой и доброжелательный, без канцелярита и без заискивания.
- Реагируешь на конкретику: опираешься на то, что кандидат реально написал («вы упомянули X — …», «про Y вы сказали верно, но…»). Ни одной общей фразы, которая подошла бы к любому ответу.
- Если в ответе есть ошибка или путаница — спокойно и прямо скажи, в чём она и как на самом деле. Если ответ хороший — не перехваливай, отметь одну конкретную сильную деталь.
- Выбирай одну-две самые важные мысли, а не разбирай всё подряд. Подскажи, что добавить, чтобы ответ звучал на уровень выше: какой пример, риск, компромисс или метрику стоило упомянуть, о чём интервьюер спросил бы следующим.
- Уместно иногда: короткое естественное начало («Хм.», «Да, это частая ловушка.», «Окей, понятно.», «Вот тут интересно.»), разговорные связки, мысль из практики в общем виде («на проде это обычно стреляет в момент…»). Но без выдуманных конкретных компаний, имён, историй «вчера у меня» и точных цифр.
- Если в контексте ниже видно, что кандидат уже говорил что-то раньше, — можешь сослаться на это, как делает человек, который помнит весь разговор.
- Если ответ пустой, шуточный, не по теме или явно скопирован откуда-то, — отреагируй как живой интервьюер: мягко, но честно.

ЧЕГО НЕ ДЕЛАТЬ — именно это выдаёт шаблонного AI-ассистента
- Никаких заголовков, разделов, маркированных списков, эмодзи-пунктов и конструкций вида «Сильные стороны: … Зоны роста: …».
- Не начинать с «Отличный ответ!», «Спасибо за ответ», «Хороший вопрос», «Давайте разберём», «Конечно», «Итак». Не заканчивать фразами «Надеюсь, это поможет», «Удачи!», «Если будут вопросы — пишите», «Продолжайте в том же духе».
- Не повторять структуру и первые слова своих предыдущих реплик (они приведены в контексте) — каждый раз начинай по-другому.
- Не задавать вопросов, на которые ждёшь ответа: следующий вопрос кандидат получит отдельным сообщением. Риторический вопрос или «на реальном интервью здесь бы уточнили: …» — можно.
- Не упоминать номер вопроса, оценку и баллы в тексте реплики.
- Не использовать Markdown (**, __, #). Выделение — только при реальной необходимости, HTML-тегом <b>…</b>, не больше одного на сообщение. Эмодзи — максимум одно и только если оно к месту.

ЧЕСТНОСТЬ
Ты — персонаж AI-тренажёра для подготовки к собеседованиям. Сам эту тему не поднимай, но если кандидат прямо и всерьёз спросит, общается ли он с живым человеком, — ответь честно, что это AI-ментор тренажёра.

ФОРМАТ ВЫВОДА
Сначала только сама реплика. Затем отдельной последней строкой — служебная оценка ответа по шкале от 1 до 10 строго в формате:
[[score: N]]
Эта строка вырезается автоматически, кандидат её не увидит.
"""

HINT_SYSTEM_PROMPT = """\
Ты — {mentor}, опытный интервьюер по направлению «{track}». На тренировочном собеседовании в переписке кандидат попросил подсказку к твоему вопросу.

Дай подсказку так, как дал бы её живой ментор: 2–4 предложения, обращение на «вы». Подтолкни в правильную сторону — с какой стороны зайти, какие аспекты не забыть, на каком примере можно построить ответ, — но НЕ давай готовый ответ и не перечисляй всё, что должно в нём быть.

Без заголовков, списков и Markdown. Не начинай со слов «Конечно», «Подсказка:», «Отличный вопрос». В конце не нужно желать удачи.
"""

FINAL_REPORT_SYSTEM_PROMPT = """\
Ты — {mentor}, интервьюер по направлению «{track}». Ты только что провёл с кандидатом тренировочное собеседование и теперь пишешь ему честный итоговый разбор — так, как написал бы опытный наставник после реального интервью: лично, конкретно, по делу.

Тебе передана расшифровка: вопросы, ответы кандидата и твои служебные оценки по каждому ответу. Пропущенные вопросы помечены — считай их пробелами в подготовке.

КАК ПИСАТЬ
- От первого лица, обращение на «вы». Живой человеческий язык, без канцелярита и общих фраз.
- Опирайся на конкретные ответы: называй, в каком вопросе что прозвучало сильно, где была ошибка или поверхностность. Каждое утверждение должно быть привязано к тому, что кандидат реально сказал.
- Будь честен: не завышай уровень из вежливости, но и не будь резким.
- НЕ используй Markdown (** или #). Для выделения — только HTML-теги <b>…</b> и <i>…</i>.

СТРУКТУРА (это итоговый документ, поэтому разделы нужны):

Короткое вступление в 1–2 предложения — общее впечатление от разговора.

🛠 <b>Технические навыки — X/10</b>
Что кандидат понимает глубоко (с примерами из ответов) и какие темы стоит подтянуть в первую очередь.

🤝 <b>Коммуникация и soft skills — X/10</b>
Как кандидат формулирует мысли и ведёт себя в сложных ситуациях — опять же, с опорой на конкретные ответы.

🎯 <b>Мой вывод</b>
Ориентировочный уровень (Junior / Junior+ / Middle / Middle+ / Senior) и почему именно он. Затем 3 конкретных шага, которые я бы советовал сделать перед реальными собеседованиями, — обычным текстом, каждый шаг с новой строки, начиная с «1.», «2.», «3.».
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


# =========================================================
# ОЧИСТКА ТЕКСТА ОТ LLM
# =========================================================

_SCORE_RE = re.compile(r"\[\[\s*score\s*:\s*(\d{1,2})\s*\]\]", re.IGNORECASE)
_ALLOWED_TAGS = ("b", "i", "code")


def _markdown_to_html(text: str) -> str:
    """Превращает случайно проскочивший Markdown в Telegram HTML."""
    # Убираем решётки заголовков в начале строк
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    # **жирный** -> <b>жирный</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # *курсив* -> <i>курсив</i>
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    return text


def _sanitize_html(text: str) -> str:
    """
    Экранирует всё, кроме разрешённых тегов <b>, <i>, <code>.
    Без этого любой символ "<" в тексте модели (например, "p99 < 50 мс") ломал бы отправку
    сообщения в Telegram с ошибкой "can't parse entities".
    """
    escaped = html.escape(text, quote=False)
    for tag in _ALLOWED_TAGS:
        escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return escaped


def _clean_html(text: str) -> str:
    """Markdown -> Telegram HTML + безопасное экранирование."""
    if not text:
        return ""
    return _sanitize_html(_markdown_to_html(text)).strip()


def _raw_text(response) -> str:
    parts = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def _extract_text(response) -> str:
    """Извлекает текст из ответа Anthropic API и форматирует под HTML Telegram."""
    return _clean_html(_raw_text(response)) or "Ответ недоступен."


def _split_score(raw: str) -> tuple[str, int | None]:
    """Вырезает служебную строку [[score: N]] и возвращает (текст, оценка)."""
    score = None
    match = _SCORE_RE.search(raw)
    if match:
        score = max(1, min(10, int(match.group(1))))
    text = _SCORE_RE.sub("", raw).strip()
    return text, score


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# =========================================================
# РЕПЛИКА МЕНТОРА НА ОТВЕТ
# =========================================================

@dataclass
class MentorReply:
    text: str
    score: int | None = None


def _build_context_block(history: list[dict] | None) -> str:
    """Контекст разговора: последние ответы кандидата и последние реплики ментора."""
    if not history:
        return "Это первый ответ кандидата в разговоре."

    answered = [h for h in history if h.get("answer") and not str(h.get("answer")).startswith("[Вопрос пропущен")]
    lines = []

    if answered:
        lines.append("Что кандидат отвечал раньше (кратко):")
        for h in answered[-3:]:
            lines.append(f"— Вопрос: {_shorten(h.get('question_text', ''), 120)}")
            lines.append(f"  Ответ: {_shorten(h.get('answer', ''), 350)}")

    previous_replies = [h.get("feedback") for h in history if h.get("feedback") and h.get("score") is not None]
    if previous_replies:
        lines.append("")
        lines.append("Твои последние реплики (не начинай так же и не повторяй их обороты):")
        for fb in previous_replies[-2:]:
            plain = re.sub(r"<[^>]+>", "", str(fb))
            lines.append(f"— «{_shorten(html.unescape(plain), 160)}»")

    return "\n".join(lines) if lines else "Это первый ответ кандидата в разговоре."


async def mentor_reply(
    question_text: str,
    answer_text: str,
    *,
    track_title: str = "IT",
    candidate_name: str | None = None,
    question_number: int | None = None,
    total_questions: int | None = None,
    history: list[dict] | None = None,
) -> MentorReply:
    """Живая реакция ментора на ответ кандидата + служебная оценка 1–10."""
    system_prompt = MENTOR_REPLY_SYSTEM_PROMPT.format(
        mentor=MENTOR_NAME,
        track=track_title,
        candidate=f" по имени {candidate_name}" if candidate_name else "",
    )

    position = ""
    if question_number and total_questions:
        position = f"Это вопрос {question_number} из {total_questions}.\n\n"

    user_prompt = (
        f"{position}"
        f"КОНТЕКСТ РАЗГОВОРА\n{_build_context_block(history)}\n\n"
        f"ТВОЙ ТЕКУЩИЙ ВОПРОС\n{question_text}\n\n"
        f"ОТВЕТ КАНДИДАТА\n{answer_text}"
    )

    try:
        response = await client.messages.create(
            model=LLM_MODEL,
            max_tokens=700,
            temperature=0.9,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text, score = _split_score(_raw_text(response))
        cleaned = _clean_html(text)
        if not cleaned:
            raise ValueError("Пустой ответ модели")
        return MentorReply(text=cleaned, score=score)
    except anthropic.APIStatusError as e:
        logger.error("Ошибка статуса API LLM (код %s): %s", e.status_code, e.message)
    except Exception as e:
        logger.exception("Непредвиденная ошибка при обращении к LLM: %s", e)

    return MentorReply(
        text=(
            "Ответ записал. У меня тут подвисла связь, поэтому подробно по этому вопросу "
            "пройдусь в итоговом разборе — давайте не будем терять темп."
        ),
        score=None,
    )


async def analyze_answer(question_text: str, answer_text: str) -> str:
    """Старый интерфейс (без контекста) — оставлен для обратной совместимости."""
    reply = await mentor_reply(question_text, answer_text)
    return reply.text


# Алиас для совместимости вызовов
process_answer = analyze_answer


# =========================================================
# ПОДСКАЗКА К ВОПРОСУ
# =========================================================

async def generate_hint(question_text: str, track_title: str = "IT") -> str | None:
    """Подсказка под конкретный вопрос. Возвращает None, если LLM недоступен."""
    try:
        response = await client.messages.create(
            model=LLM_MODEL,
            max_tokens=300,
            temperature=0.8,
            system=HINT_SYSTEM_PROMPT.format(mentor=MENTOR_NAME, track=track_title),
            messages=[{"role": "user", "content": f"Вопрос, к которому нужна подсказка:\n{question_text}"}],
        )
        text = _clean_html(_raw_text(response))
        return text or None
    except Exception as e:
        logger.exception("Ошибка генерации подсказки: %s", e)
        return None


# =========================================================
# ИТОГОВЫЙ ОТЧЁТ И РЕЗЮМЕ
# =========================================================

async def generate_final_report(qa_pairs: list[dict], track_title: str) -> str:
    """Формирует итоговый разбор по Hard & Soft Skills от лица ментора."""
    transcript_lines = [f"Направление: {track_title}", f"Всего вопросов в разговоре: {len(qa_pairs)}"]
    for i, pair in enumerate(qa_pairs, start=1):
        score = pair.get("score")
        score_note = f" (твоя оценка по ходу: {score}/10)" if score else ""
        transcript_lines.append(f"{i}. Вопрос: {pair['question']}\n   Ответ{score_note}: {pair['answer']}")
    transcript = "\n\n".join(transcript_lines)

    try:
        response = await client.messages.create(
            model=LLM_MODEL,
            max_tokens=1500,
            temperature=0.5,
            system=FINAL_REPORT_SYSTEM_PROMPT.format(mentor=MENTOR_NAME, track=track_title),
            messages=[{"role": "user", "content": transcript}],
        )
        return _extract_text(response)
    except Exception as e:
        logger.exception("Ошибка при формировании отчета: %s", e)
        return (
            "Не получилось собрать итоговый разбор — сервис сейчас перегружен. "
            "Напишите /continue чуть позже, и я пришлю разбор заново."
        )


async def generate_resume_draft(qa_pairs: list[dict], track_title: str) -> str:
    """Генерирует текстовый черновик резюме на основе ответов пользователя."""
    transcript_lines = [f"Специализация: {track_title}"]
    for pair in qa_pairs:
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
