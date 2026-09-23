"""
handlers/__init__.py — объединение роутеров.

Порядок важен: aiogram отдаёт событие первому подходящему обработчику.
- admin — первым: его обработчики защищены фильтром IsAdmin и не мешают остальным;
- payment, reviews, resume, interview — обработчики конкретных сценариев и режимов ввода;
- start — последним: главное меню и «страховочные» обработчики (любой текст/сообщение/кнопка).
"""
from aiogram import Router

from .admin import router as admin_router
from .interview import router as interview_router
from .payment import router as payment_router
from .resume import router as resume_router
from .reviews import router as reviews_router
from .start import router as start_router

main_router = Router(name="main")

main_router.include_router(admin_router)
main_router.include_router(payment_router)
main_router.include_router(reviews_router)
main_router.include_router(resume_router)
main_router.include_router(interview_router)
main_router.include_router(start_router)
