"""
llm_check.py — быстрая проверка связи с нейросетью с теми же настройками, что у бота.

Запуск из папки проекта:
    python llm_check.py

Печатает либо ответ модели, либо точную причину ошибки (код и текст от API).
Бота не трогает и данные не меняет.
"""
import asyncio

import anthropic

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from llm_service import client


async def main() -> None:
    print(f"Модель:   {LLM_MODEL}")
    print(f"Адрес API: {LLM_BASE_URL}")
    print(f"Ключ:     {'задан' if LLM_API_KEY else 'НЕ ЗАДАН (проверьте LLM_API_KEY в .env)'}")
    print("Отправляю тестовый запрос…")
    try:
        response = await client.messages.create(
            model=LLM_MODEL,
            max_tokens=20,
            system="Отвечай очень коротко.",
            messages=[{"role": "user", "content": "Ответь одним словом: работает?"}],
        )
    except anthropic.APIStatusError as e:
        print(f"\n❌ API вернул ошибку {e.status_code}: {e.message}")
        return
    except Exception as e:
        print(f"\n❌ Не удалось связаться с API: {type(e).__name__}: {e}")
        return
    text = " ".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text")
    print(f"\n✅ Нейросеть отвечает: {text.strip()!r}")


if __name__ == "__main__":
    asyncio.run(main())
